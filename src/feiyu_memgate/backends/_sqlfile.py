"""What the two SQLite-file stores (sqlite and embedding) share. Private to the backends package.

Both keep their memories in a `memories` table and a key-value `info` table of one database file. The
functions here take what they need to read with: `read` is a callable that returns a context manager
yielding a connection (each backend brings its own: the sqlite backend a pooled reader, the embedding
backend its one connection under its lock), so the locking stays with the backend. Standard library only.
"""
import os
import sqlite3

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
