"""What the two SQLite-file stores (sqlite and embedding) share. Private to the backends package.

Both keep their memories in a `memories` table and a key-value `info` table of one database file. The
functions here take what they need to read with: `read` is a callable that returns a context manager
yielding a connection (each backend brings its own: a pooled reader, or for a ":memory:" database the one
connection under the writer's lock), so the locking stays with the backend. `Readers` is the bounded pool of
read-only connections both backends use. Standard library only.
"""
import os
import sqlite3
import threading
import time

from ..errors import BackendError

SCAN_BATCH = 500  # rows read per step when walking the whole store

MEMORIES_TABLE = """
CREATE TABLE IF NOT EXISTS memories (
    ref TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    date TEXT%s
);
"""
INFO_TABLE = """CREATE TABLE IF NOT EXISTS info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def memories_table(extra_columns=""):
    """The `memories` table definition, with `extra_columns` (for example ",\n    vector BLOB NOT NULL")
    after the shared ones."""
    return MEMORIES_TABLE % extra_columns


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
