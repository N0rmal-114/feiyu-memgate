"""The archive: the full text of forgotten memories, kept for a while so they can be restored.

It holds memory content: protect the file like the memories themselves. A memory's record fields (who said
it, the channel and so on, see model.FIELDS) are archived with it as one JSON object in the `fields` column,
NULL for a memory without any and for rows written before the column existed.
Standard library only.
"""
import json
import sqlite3
import threading
from dataclasses import dataclass, field

_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive (
    store        TEXT NOT NULL,
    ref          TEXT NOT NULL,
    text         TEXT NOT NULL,
    date         TEXT,
    uses         INTEGER NOT NULL DEFAULT 0,
    forgotten_at REAL NOT NULL,
    expires_at   REAL NOT NULL,
    fields       TEXT,
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
    fields: dict = field(default_factory=dict)


def _record(row):
    """A Record from a row of the columns store .. expires_at plus the `fields` JSON (or NULL)."""
    fields = json.loads(row[7]) if row[7] else {}
    return Record(row[0], row[1], row[2], row[3], row[4], row[5], row[6],
                  fields=fields if isinstance(fields, dict) else {})


class Archive:
    def __init__(self, path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        with self._lock:
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(_SCHEMA)
            self._migrate()

    def _migrate(self):
        """An archive file from before the record fields gets the column (its rows have none)."""
        columns = [r[1] for r in self._db.execute("PRAGMA table_info(archive)").fetchall()]
        if "fields" in columns:
            return
        try:
            self._db.execute("ALTER TABLE archive ADD COLUMN fields TEXT")
        except sqlite3.OperationalError:
            # Another process (a command next to a running gateway) migrated first.
            if "fields" not in [r[1] for r in self._db.execute("PRAGMA table_info(archive)").fetchall()]:
                raise

    def add(self, store, ref, text, date, uses, forgotten_at, expires_at, fields=None):
        """Archive one memory; `fields` is its record fields (a dict, none when empty)."""
        encoded = json.dumps(fields, sort_keys=True, ensure_ascii=False) if fields else None
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO archive (store, ref, text, date, uses, forgotten_at, expires_at, fields) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (store, ref, text, date, uses, forgotten_at, expires_at, encoded))

    def get(self, store, ref):
        with self._lock:
            row = self._db.execute(
                "SELECT store, ref, text, date, uses, forgotten_at, expires_at, fields FROM archive "
                "WHERE store = ? AND ref = ?", (store, ref)).fetchone()
        return _record(row) if row else None

    def list(self, store=None):
        """Records without their text, oldest forgotten first."""
        sql = "SELECT store, ref, NULL, date, uses, forgotten_at, expires_at, NULL FROM archive"
        args: tuple[str, ...] = ()
        if store is not None:
            sql += " WHERE store = ?"
            args = (store,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY forgotten_at, store, ref", args).fetchall()
        return [_record(r) for r in rows]

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
