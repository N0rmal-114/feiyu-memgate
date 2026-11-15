"""The ledger: when each memory was first seen, last used, how often, and whether it is pinned.

Two clocks per memory. `first_seen` is the memory's own date (or the time the ledger first met
it when it has none); it drives RANKING. `managed_since` is the time the ledger first learned of
the memory; together with `last_used` it drives DELETION, so turning forgetting on never
deletes an old memory before it had a chance to be used under management.

One SQLite table. It holds addresses and numbers, never memory content; but an address
may carry a topic name and a tenant id, so protect the file like the memories themselves.

Standard library only.
"""
import sqlite3
import threading
import time
from dataclasses import dataclass

CHUNK = 400  # stays under SQLite's variable limit

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    store      TEXT NOT NULL,
    ref        TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_used  REAL,
    uses       INTEGER NOT NULL DEFAULT 0,
    pinned     INTEGER NOT NULL DEFAULT 0,
    managed_since REAL,
    PRIMARY KEY (store, ref)
)
"""

_COLUMNS = "store, ref, first_seen, last_used, uses, pinned, managed_since"


@dataclass
class Entry:
    store: str
    ref: str
    first_seen: float
    last_used: float | None = None
    uses: int = 0
    pinned: bool = False
    managed_since: float | None = None  # None: unknown, which deletion treats as "just now"


def _chunks(items):
    items = list(items)
    for start in range(0, len(items), CHUNK):
        yield items[start:start + CHUNK]


def _entry(row):
    return Entry(row[0], row[1], row[2], row[3], row[4], bool(row[5]), row[6])


class Ledger:
    def __init__(self, path, wall=time.time):
        self._wall = wall
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        with self._lock:
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(_SCHEMA)
            self._migrate()

    def _migrate(self):
        """Ledger files from before the deletion clock get the column; their rows start being
        managed now (the moment of migration), not at their memory's date."""
        columns = [r[1] for r in self._db.execute("PRAGMA table_info(ledger)").fetchall()]
        if "managed_since" in columns:
            return
        try:
            self._db.execute("ALTER TABLE ledger ADD COLUMN managed_since REAL")
        except sqlite3.OperationalError:
            # Another process (a command next to a running gateway) migrated first.
            if "managed_since" not in [r[1] for r in self._db.execute("PRAGMA table_info(ledger)").fetchall()]:
                raise
        self._db.execute("UPDATE ledger SET managed_since = ? WHERE managed_since IS NULL", (self._wall(),))

    def _write(self, sql, rows):
        """Run one statement for every row, in one transaction."""
        rows = list(rows)
        if not rows:
            return
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.executemany(sql, rows)
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def _select(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def get_many(self, store, refs):
        out = {}
        for part in _chunks(set(refs)):
            marks = ",".join("?" * len(part))
            rows = self._select(
                "SELECT %s FROM ledger WHERE store = ? AND ref IN (%s)" % (_COLUMNS, marks), [store] + part)
            for row in rows:
                out[row[1]] = _entry(row)
        return out

    def entries(self, store):
        rows = self._select("SELECT %s FROM ledger WHERE store = ? ORDER BY ref" % _COLUMNS, (store,))
        return [_entry(r) for r in rows]

    def stores(self):
        return [r[0] for r in self._select("SELECT DISTINCT store FROM ledger ORDER BY store")]

    def touch(self, store, items, now):
        """Add rows for refs not yet known (first_seen from the item, else `now`; managed_since `now`).
        Never overwrites."""
        self._write("INSERT OR IGNORE INTO ledger (store, ref, first_seen, managed_since) VALUES (?, ?, ?, ?)",
                    [(store, ref, now if seen is None else seen, now) for ref, seen in items])

    def use(self, store, items, now):
        """Record one use of each ref: last_used = now, uses += 1 (the row is created when missing,
        with managed_since = now; an existing row keeps its managed_since)."""
        self._write(
            "INSERT INTO ledger (store, ref, first_seen, last_used, uses, managed_since) VALUES (?, ?, ?, ?, 1, ?) "
            "ON CONFLICT (store, ref) DO UPDATE SET last_used = excluded.last_used, uses = uses + 1",
            [(store, ref, now if seen is None else seen, now, now) for ref, seen in items])

    def pin(self, store, refs):
        now = self._wall()
        self._write(
            "INSERT INTO ledger (store, ref, first_seen, pinned, managed_since) VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT (store, ref) DO UPDATE SET pinned = 1",
            [(store, ref, now, now) for ref in refs])

    def unpin(self, store, refs):
        self._write("UPDATE ledger SET pinned = 0 WHERE store = ? AND ref = ?", [(store, r) for r in refs])

    def remove(self, store, refs):
        self._write("DELETE FROM ledger WHERE store = ? AND ref = ?", [(store, r) for r in refs])

    def revive(self, store, ref, now):
        """Insert or update with last_used = managed_since = now, so a restored memory is not
        forgotten again at once (neither clock is left in the past)."""
        self._write(
            "INSERT INTO ledger (store, ref, first_seen, last_used, managed_since) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (store, ref) DO UPDATE SET last_used = excluded.last_used, "
            "managed_since = excluded.managed_since",
            [(store, ref, now, now, now)])

    def counts(self, store):
        total, pinned, used = self._select(
            "SELECT COUNT(*), COALESCE(SUM(pinned), 0), COALESCE(SUM(uses > 0), 0) FROM ledger WHERE store = ?",
            (store,))[0]
        return {"total": total, "pinned": pinned, "used": used}

    def close(self):
        with self._lock:
            self._db.close()
