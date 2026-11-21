"""The archive: the full text of forgotten memories, kept for a while so they can be restored.

It holds memory content: protect the file like the memories themselves.
Standard library only.
"""
import sqlite3
import threading
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive (
    store        TEXT NOT NULL,
    ref          TEXT NOT NULL,
    text         TEXT NOT NULL,
    date         TEXT,
    uses         INTEGER NOT NULL DEFAULT 0,
    forgotten_at REAL NOT NULL,
    expires_at   REAL NOT NULL,
    PRIMARY KEY (store, ref)
)
"""


@dataclass
class Record:
    store: str
    ref: str
    text: str | None
    date: str | None
    uses: int
    forgotten_at: float
    expires_at: float


class Archive:
    def __init__(self, path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        with self._lock:
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(_SCHEMA)

    def add(self, store, ref, text, date, uses, forgotten_at, expires_at):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO archive (store, ref, text, date, uses, forgotten_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", (store, ref, text, date, uses, forgotten_at, expires_at))

    def get(self, store, ref):
        with self._lock:
            row = self._db.execute(
                "SELECT store, ref, text, date, uses, forgotten_at, expires_at FROM archive "
                "WHERE store = ? AND ref = ?", (store, ref)).fetchone()
        return Record(*row) if row else None

    def list(self, store=None):
        """Records without their text, oldest forgotten first."""
        sql = "SELECT store, ref, NULL, date, uses, forgotten_at, expires_at FROM archive"
        args: tuple[str, ...] = ()
        if store is not None:
            sql += " WHERE store = ?"
            args = (store,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY forgotten_at, store, ref", args).fetchall()
        return [Record(*r) for r in rows]

    def remove(self, store, ref):
        with self._lock:
            self._db.execute("DELETE FROM archive WHERE store = ? AND ref = ?", (store, ref))

    def purge(self, now):
        """Delete what has expired; returns how many."""
        with self._lock:
            return self._db.execute("DELETE FROM archive WHERE expires_at <= ?", (now,)).rowcount

    def count(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM archive").fetchone()[0]

    def close(self):
        with self._lock:
            self._db.close()
