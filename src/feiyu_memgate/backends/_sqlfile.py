"""What the two SQLite-file stores (sqlite and embedding) share. Private to the backends package.

Both keep their memories in a `memories` table and a key-value `info` table of one database file. The
functions here take what they need to read with: `read` is a callable that returns a context manager
yielding a connection (each backend brings its own: a pooled reader, or for a ":memory:" database the one
connection under the writer's lock), so the locking stays with the backend. `Readers` is the bounded pool of
read-only connections both backends use. Standard library only.

Record fields: the `memories` table has a nullable TEXT column for each of model.FIELDS (who said it, the
channel and so on). A file made before they existed is upgraded by `ensure_fields` when it is opened
writable and read without them when it is not; the SELECT lists below are built for either case.
"""
import os
import sqlite3
import threading
import time

from ..errors import BackendError
from ..model import FIELDS

SCAN_BATCH = 500  # rows read per step when walking the whole store

MEMORIES_TABLE = """
CREATE TABLE IF NOT EXISTS memories (
    ref TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    date TEXT,
    speaker TEXT,
    about TEXT,
    channel TEXT,
    audience TEXT,
    origin TEXT,
    event_date TEXT,
    kind TEXT%s
);
"""
INFO_TABLE = """CREATE TABLE IF NOT EXISTS info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


RECORD_VERSION = "2"  # info record_version: the memories table has the FIELDS columns (older stores have none)
FIELD_LIST = ", ".join(FIELDS)  # the field columns, in the order of model.FIELDS
FIELD_MARKS = ", ".join("?" * len(FIELDS))


def memories_table(extra_columns=""):
    """The `memories` table definition, with `extra_columns` (for example ",\n    vector BLOB NOT NULL")
    after the shared ones. It holds a nullable TEXT column for each of model.FIELDS."""
    return MEMORIES_TABLE % extra_columns


def ensure_fields(db, writable):
    """True when the `memories` table of `db` has the field columns afterwards.

    A store made before the fields existed gets them with ALTER TABLE when `writable` (and `info`
    record_version "2"); a read-only open changes nothing and gets False: its memories are read and
    returned without fields. A store that has the columns already is only marked, when writable and not
    marked yet."""
    def missing():
        have = {row[1] for row in db.execute("PRAGMA table_info(memories)").fetchall()}
        return [name for name in FIELDS if name not in have]

    todo = missing()
    if todo and not writable:
        return False
    if not todo:
        if writable and db.execute("SELECT 1 FROM info WHERE key = 'record_version'").fetchone() is None:
            with db:
                db.execute("INSERT OR IGNORE INTO info (key, value) VALUES ('record_version', ?)", (RECORD_VERSION,))
        return True
    with db:
        for name in todo:
            try:
                db.execute("ALTER TABLE memories ADD COLUMN %s TEXT" % name)
            except sqlite3.OperationalError:
                if name in missing():  # not a second process that added it first
                    raise
        db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('record_version', ?)", (RECORD_VERSION,))
    return True


def field_columns(stored):
    """", speaker, about, ..." for a SELECT list when the table has the field columns, else ""."""
    return ", " + FIELD_LIST if stored else ""


def fields_of(values):
    """The fields dict of a row's field columns (in the order of model.FIELDS); None entries are left out."""
    return {name: value for name, value in zip(FIELDS, values) if value is not None}


def field_values(fields):
    """The values for the field columns, in the order of model.FIELDS (None where not set)."""
    return tuple(fields.get(name) for name in FIELDS)


def record_rows(read, stored, ref=None):
    """Rows of {"ref", "text", "date", "fields"}: one memory when `ref` is given (empty list when it is not
    stored), else everything, in the order of items()."""
    sql = "SELECT ref, text, date%s FROM memories" % field_columns(stored)
    args: tuple = ()
    if ref is not None:
        sql += " WHERE ref = ?"
        args = (ref,)
    else:
        sql += " ORDER BY created_at, ref"
    with read() as db:
        rows = db.execute(sql, args).fetchall()
    return [{"ref": r[0], "text": r[1], "date": r[2], "fields": fields_of(r[3:])} for r in rows]


def open_store(path, schema):
    """A connection to the database file `path` (parent directories are created; ":memory:" is kept in
    memory) with the `schema` script applied; file databases use WAL. The connection is closed again when
    this fails."""
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
    try:
        if path != ":memory:":
            db.execute("PRAGMA journal_mode=WAL")
        db.executescript(schema)
        db.commit()
    except Exception:
        db.close()
        raise
    return db


def open_reader(path):
    """A read-only connection to the database file `path` (not for ":memory:", which cannot be shared)."""
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
    try:
        conn.execute("PRAGMA query_only=ON")
    except Exception:
        conn.close()
        raise
    return conn


class Readers:
    """A bounded pool of read-only connections. Many searches run at once; none waits for another
    one's SQL (WAL lets readers run beside the writer). Closed with the backend."""

    def __init__(self, connect, maximum):
        self._connect = connect
        self._max = max(1, int(maximum))
        self._cond = threading.Condition()
        self._free = []
        self._made = 0
        self._closed = False

    def acquire(self, deadline):
        with self._cond:
            while True:
                if self._closed:
                    raise BackendError("the store is closed")
                if self._free:
                    return self._free.pop()
                if self._made < self._max:
                    self._made += 1
                    break
                left = deadline - time.monotonic()
                if left <= 0:
                    raise BackendError("no free read connection in time")
                self._cond.wait(left)
        try:
            return self._connect()
        except Exception:
            with self._cond:
                self._made -= 1
                self._cond.notify()
            raise

    def release(self, conn):
        with self._cond:
            if not self._closed:
                self._free.append(conn)
                self._cond.notify()
                return
            self._made -= 1
        conn.close()  # the store was closed while this one was in use

    def close(self):
        with self._cond:
            self._closed = True
            free, self._free = self._free, []
            self._made -= len(free)
            self._cond.notify_all()
        for conn in free:
            conn.close()


def count(read):
    with read() as db:
        return db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]


def items(read):
    """Iterate (ref, text, date) over everything stored, oldest first."""
    with read() as db:
        rows = db.execute("SELECT ref, text, date FROM memories ORDER BY created_at, ref").fetchall()
    return iter(rows)


def iter_refs(read, batch):
    """Iterate (ref, date or None) over every memory, ordered by ref, without texts, `batch` rows per step.

    Read in batches by key, the connection is released between them, so a big store is never held in
    memory and deleting while iterating is safe."""
    last = None
    while True:
        with read() as db:
            if last is None:
                rows = db.execute("SELECT ref, date FROM memories ORDER BY ref LIMIT ?", (batch,)).fetchall()
            else:
                rows = db.execute(
                    "SELECT ref, date FROM memories WHERE ref > ? ORDER BY ref LIMIT ?", (last, batch)
                ).fetchall()
        for row in rows:
            yield row[0], row[1]
        if len(rows) < batch:
            return
        last = rows[-1][0]


def receipt_refs(read, receipt):
    """The refs remember() stored for `receipt` (it names them "<receipt>-<index>"); a memory deleted
    since is absent. None when `receipt` is not a non-empty string."""
    if not isinstance(receipt, str) or not receipt:
        return None
    prefix = receipt + "-"
    with read() as db:
        rows = db.execute(
            "SELECT ref FROM memories WHERE substr(ref, 1, ?) = ? ORDER BY ref", (len(prefix), prefix)
        ).fetchall()
    return [r[0] for r in rows]
