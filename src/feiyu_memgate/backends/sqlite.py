"""SQLite backend: a model-free store in one file, searched by shared wording.

What it is: every memory is kept verbatim in a `memories` table, and an inverted index
of character grams (taken from the normalised text, so Chinese needs no word segmentation:
every character bigram, plus every single CJK ideograph) finds the memories that share wording
with a query. Candidates are scored by IDF-weighted containment: the weight of the query's grams
the memory holds divided by the weight of all the query's grams, 0..1, where a gram's weight is
its BM25 inverse document frequency in this store (`tidy.idf_weight`; the index itself says how
many memories hold each gram). A pair that most memories contain counts for little, a rare one
for a lot. Nothing but the standard library: no vector model, no service.

What it is not: it does not understand meaning. A paraphrase that shares no wording
with the query is not found, and scores are keyword overlap, not semantic similarity,
so the backend declares its own `default_min_score` (0.30, chosen on the development set in
bench/dev with bench/dev_eval.py). It is a good fit for small and medium personal stores (thousands
of memories); at most `max_candidates` memories are scored per query.

Concurrency: one guarded writer connection (writes are serialised by a lock) and a bounded
pool of reader connections (`max_readers`, default 16), so searches run in parallel and do not
queue behind each other or behind a write (file databases use WAL). A ":memory:" database cannot
be shared between connections, so it falls back to the one connection under the lock. close()
closes every connection (Windows cannot delete a database file that is still open).

Deadlines: find() stops its SQL when the time is up or the call is cancelled (a progress handler
checks both), it does not just notice afterwards. What comes back after running out of time:
- out of time while collecting candidates: nothing was scored, so BackendError is raised;
- out of time while scoring: the best hits scored so far are returned (candidates are scored
  best-first, so this is the best of what was reachable);
- cancelled (ctl.cancelled, or ctl.on_cancel fired): BackendError, the caller is gone anyway.

Work per find is bounded whatever the query length: the query is normalised once, candidates are
collected from the rarest of its grams first (document frequency comes from the index itself),
at most `max_postings` index rows are read and at most `max_candidates` memories are scored. The
document frequency of a gram is counted exactly up to DF_PROBE memories and a more common gram
counts as DF_PROBE (its weight is then a little too high, which only matters for stores far
above DF_PROBE memories); the store size N in the weights is the exact row count.

Index versions: stores written before CJK single characters were indexed hold bigrams only. A
writable open rebuilds the index once (a pass over the stored texts); a read-only open of such a
file keeps working on bigrams alone.
"""
import contextlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

from ..core.tidy import PreparedQuery, gram_weights, index_grams, normalize
from ..errors import BackendError
from ..model import Hit
from ..registry import register
from .base import MemoryBackend

DEFAULT_BUDGET = 10.0  # seconds for one find() when the caller gives none
MAX_QUERY_GRAMS = 600  # distinct query grams looked at (their document frequency is probed)
DF_PROBE = 500  # document frequency is counted up to this many postings per gram
SCAN_BATCH = 500
SCORE_BATCH = 200  # memories fetched per step while scoring (the deadline is checked between steps)
PROGRESS_OPS = 20000  # SQLite virtual-machine steps between two looks at the deadline

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    ref TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    date TEXT
);
CREATE TABLE IF NOT EXISTS grams (
    gram TEXT NOT NULL,
    ref TEXT NOT NULL,
    PRIMARY KEY (gram, ref)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS grams_by_ref ON grams (ref);
CREATE INDEX IF NOT EXISTS memories_by_created ON memories (created_at);
CREATE TABLE IF NOT EXISTS info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
# What the grams table holds: "1" bigrams only (stores of older versions), "2" bigrams and CJK unigrams.
GRAM_SCHEME = "2"


# Why the rows come back as one JSON value: the sqlite3 module lets go of the GIL at every row it steps,
# and with several threads searching at once each of those hand-overs can wait a whole GIL switch
# interval. One row per statement keeps parallel searches parallel.


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class _Readers:
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


class _Run:
    """The deadline and cancellation of one find(), as the SQL progress handler and the scoring
    loop see them."""

    def __init__(self, deadline, ctl):
        self.deadline = deadline
        self._ctl = ctl
        self._stop = threading.Event()
        on_cancel = getattr(ctl, "on_cancel", None)
        if callable(on_cancel):
            on_cancel(self._stop.set)

    @property
    def cancelled(self):
        return self._stop.is_set() or bool(getattr(self._ctl, "cancelled", False))

    def time_up(self):
        return time.monotonic() > self.deadline

    def should_stop(self):
        return self.cancelled or self.time_up()

    def progress(self):
        return 1 if self.should_stop() else 0  # non-zero makes SQLite abort the statement


@register("backend", "sqlite")
class SQLiteBackend(MemoryBackend):
    """Options: path (the database file; parent directories are created; ":memory:" for
    tests), writable (default True), roles (message roles remember() keeps, default
    ("user",)), max_candidates (memories scored per query, default 2000), max_postings
    (index rows read per query, default 20000), max_readers (read connections, default 16)."""

    # A write is final as soon as remember() returns.
    tracks_writes = False
    # Scores are IDF-weighted keyword overlap: the gateway's min_score stage starts from this. Both numbers
    # were chosen on bench/dev (160 memories, 110 questions) by the fixed balanced rule of bench/dev_eval.py.
    default_min_score = 0.30
    # The `relative_score` stage keeps candidates within this share of the best score. On the development
    # set every ratio from 0.75 to 0.95 gave the same deployed hit and precision at min_score 0.30
    # (the lowest tied ratio is taken).
    default_relative_ratio = 0.75
    # The "broad" profile (recall over precision), chosen on bench/dev by the rule in bench/dev_eval.py
    # (--search --profile broad): precision >= 50 % and half of the default's quiet rate, best deployed hit.
    broad_min_score = 0.25
    broad_relative_ratio = 0.70

    def __init__(self, path, writable=True, roles=("user",), max_candidates=2000, max_postings=20000,
                 max_readers=16):
        if isinstance(roles, str):
            roles = (roles,)
        self.writable = bool(writable)
        self.roles = tuple(roles)
        self.max_candidates = max(1, int(max_candidates))
        self.max_postings = max(1, int(max_postings))
        self._path = str(path)
        self._shared = self._path == ":memory:"  # one connection for everything
        self._lock = threading.Lock()  # the writer (and, for ":memory:", every reader)
        self._closed = False
        if not self._shared:
            parent = os.path.dirname(os.path.abspath(self._path))
            os.makedirs(parent, exist_ok=True)
        self._db = sqlite3.connect(self._path, check_same_thread=False, timeout=10.0)
        try:
            if not self._shared:
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
            self._unigrams = self._open_scheme()
        except Exception:
            self._db.close()
            raise
        self._readers = None if self._shared else _Readers(self._open_reader, max_readers)

    def _open_scheme(self):
        """True when the index holds CJK unigrams. A new store is marked with the current scheme; an
        older one is re-indexed when it is writable and searched on bigrams alone when it is not."""
        row = self._db.execute("SELECT value FROM info WHERE key = 'gram_scheme'").fetchone()
        if row is not None:
            return row[0] == GRAM_SCHEME
        empty = self._db.execute("SELECT 1 FROM memories LIMIT 1").fetchone() is None
        if not self.writable:
            return empty
        with self._db:
            if not empty:
                self._reindex()
            self._db.execute("INSERT OR REPLACE INTO info (key, value) VALUES ('gram_scheme', ?)", (GRAM_SCHEME,))
        return True

    def _reindex(self):
        """Rebuild the gram index of every memory (inside the caller's transaction)."""
        self._db.execute("DELETE FROM grams")
        rows = self._db.execute("SELECT ref, text FROM memories").fetchall()
        for start in range(0, len(rows), SCAN_BATCH):
            self._db.executemany(
                "INSERT OR IGNORE INTO grams (gram, ref) VALUES (?, ?)",
                [(g, ref) for ref, text in rows[start:start + SCAN_BATCH] for g in index_grams(normalize(text))])

    def _open_reader(self):
        conn = sqlite3.connect(self._path, check_same_thread=False, timeout=10.0)
        try:
            conn.execute("PRAGMA query_only=ON")
        except Exception:
            conn.close()
            raise
        return conn

    @contextlib.contextmanager
    def _read(self, deadline=None):
        """A connection to read with, used by this thread alone until the block ends."""
        if deadline is None:
            deadline = time.monotonic() + DEFAULT_BUDGET
        if self._closed:
            raise BackendError("the store is closed")
        if self._shared:
            if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
                raise BackendError("the store is busy")
            try:
                yield self._db
            finally:
                self._lock.release()
            return
        conn = self._readers.acquire(deadline)
        try:
            yield conn
        finally:
            self._readers.release(conn)

    # ------------------------------------------------------------ writing

    def put(self, text, ref=None, date=None):
        """Store one memory text as it is and return its ref. An existing ref is replaced
        together with its index rows."""
        if not self.writable:
            raise PermissionError("this backend is read-only")
        ref = ref or "mem-" + uuid.uuid4().hex
        grams = [(g, ref) for g in index_grams(normalize(text), self._unigrams)]
        with self._lock:
            with self._db:  # one transaction: a memory and its index rows go together
                self._db.execute("DELETE FROM grams WHERE ref = ?", (ref,))
                self._db.execute(
                    "INSERT OR REPLACE INTO memories (ref, text, created_at, date) VALUES (?, ?, ?, ?)",
                    (ref, text, int(time.time()), date),
                )
                self._db.executemany("INSERT OR IGNORE INTO grams (gram, ref) VALUES (?, ?)", grams)
        return ref

    def delete(self, ref):
        """Remove one memory and its index rows. True when it existed."""
        if not self.writable:
            raise PermissionError("this backend is read-only")
        with self._lock:
            with self._db:
                self._db.execute("DELETE FROM grams WHERE ref = ?", (ref,))
                cur = self._db.execute("DELETE FROM memories WHERE ref = ?", (ref,))
                return cur.rowcount > 0

    def remember(self, messages, timeout=None, ctl=None):
        """Keep the messages whose role is in `roles`; returns the receipt id, or None when
        no message qualified (nothing was kept, so there is no receipt for it)."""
        if not self.writable:
            raise PermissionError("this backend is read-only")
        receipt = "w-" + uuid.uuid4().hex
        today = _today()
        kept = 0
        for index, message in enumerate(messages):
            if message["role"] in self.roles:
                self.put(message["content"], ref="%s-%d" % (receipt, index), date=today)
                kept += 1
        return receipt if kept else None

    # ------------------------------------------------------------ reading

    def count(self):
        with self._read() as db:
            return db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def items(self):
        """Iterate (ref, text, date) over everything stored, oldest first."""
        with self._read() as db:
            rows = db.execute("SELECT ref, text, date FROM memories ORDER BY created_at, ref").fetchall()
        return iter(rows)

    def refs(self):
        """Iterate (ref, date or None) over every memory, ordered by ref, without texts.

        Read in batches by key, the connection is released between them, so a big store is never
        held in memory and deleting while iterating is safe."""
        last = None
        while True:
            with self._read() as db:
                if last is None:
                    rows = db.execute("SELECT ref, date FROM memories ORDER BY ref LIMIT ?", (SCAN_BATCH,)).fetchall()
                else:
                    rows = db.execute(
                        "SELECT ref, date FROM memories WHERE ref > ? ORDER BY ref LIMIT ?", (last, SCAN_BATCH)
                    ).fetchall()
            for row in rows:
                yield row[0], row[1]
            if len(rows) < SCAN_BATCH:
                return
            last = rows[-1][0]

    def receipt_refs(self, receipt):
        """The refs remember() stored for `receipt` (it names them "<receipt>-<index>").
        Only the messages it kept have one; a memory deleted since is absent."""
        if not isinstance(receipt, str) or not receipt:
            return None
        prefix = receipt + "-"
        with self._read() as db:
            rows = db.execute(
                "SELECT ref FROM memories WHERE substr(ref, 1, ?) = ? ORDER BY ref", (len(prefix), prefix)
            ).fetchall()
        return [r[0] for r in rows]

    def read(self, ref, timeout=None, ctl=None):
        with self._read() as db:
            row = db.execute("SELECT text FROM memories WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            raise KeyError(ref)
        return row[0]

    def find(self, query, limit, timeout=None, ctl=None):
        """Up to `limit` hits, best first. Out of time: see the module docstring (BackendError
        when nothing was scored yet, the best hits so far otherwise)."""
        budget = DEFAULT_BUDGET if timeout is None else max(0.0, float(timeout))
        run = _Run(time.monotonic() + budget, ctl)
        if run.cancelled:
            raise BackendError("cancelled")
        prepared = PreparedQuery(query)  # normalised once for every candidate
        with self._read(run.deadline) as db:
            db.set_progress_handler(run.progress, PROGRESS_OPS)
            try:
                try:
                    if len(prepared.norm) < 2:
                        refs = self._newest(db, run)
                    else:
                        frequency, size = self._frequencies(db, prepared.norm, run)
                        weights = gram_weights(prepared.norm, frequency.get, size, self._unigrams, MAX_QUERY_GRAMS)
                        prepared = PreparedQuery(query, weights)
                        refs = self._candidates(db, prepared, run)
                except sqlite3.OperationalError:
                    self._interrupted(run)  # our own stop becomes a BackendError
                    raise
                hits = self._score(db, refs, prepared, run)
            finally:
                db.set_progress_handler(None, 0)
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def _frequencies(self, db, norm, run):
        """({gram: how many memories hold it, up to DF_PROBE} for the query's grams that any memory
        holds, number of memories)."""
        grams = sorted(index_grams(norm, self._unigrams))[:MAX_QUERY_GRAMS]
        frequency = {}
        for gram in grams:
            n = db.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM grams WHERE gram = ? LIMIT ?)", (gram, DF_PROBE)
            ).fetchone()[0]
            if n:
                frequency[gram] = n
        self._check(run)
        return frequency, db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def _candidates(self, db, prepared, run):
        """Refs of the memories sharing a gram with the query, the likeliest first, at most
        max_candidates. Reads the postings of the rarest grams first and at most max_postings rows
        in all, so a long query over a big store costs no more than a short one. "Likeliest" is the
        weight of the shared grams, the same thing the score is made of. The rarest gram is the one
        with the greatest weight."""
        weights = prepared.weights
        shared = {}
        budget = self.max_postings
        for gram in sorted(weights, key=lambda g: (-weights[g], g)):
            if budget <= 0 or run.should_stop():
                break
            posting = json.loads(db.execute(
                "SELECT json_group_array(ref) FROM (SELECT ref FROM grams WHERE gram = ? LIMIT ?)", (gram, budget)
            ).fetchone()[0])
            budget -= len(posting)
            weight = weights[gram]
            for ref in posting:
                shared[ref] = shared.get(ref, 0.0) + weight
        self._check(run)
        best = sorted(shared, key=lambda r: (-shared[r], r))
        return best[:self.max_candidates]

    def _newest(self, db, run):
        """The newest memories, for a query too short to have a bigram. Bounded by max_candidates."""
        rows = db.execute(
            "SELECT ref FROM memories ORDER BY created_at DESC, rowid DESC LIMIT ?", (self.max_candidates,)
        ).fetchall()
        self._check(run)
        return [r[0] for r in rows]

    def _interrupted(self, run):
        """An SQL error while a find runs: if it was our own stop, say so (no SQL text, no memory text)."""
        if run.cancelled:
            raise BackendError("cancelled") from None
        if run.time_up():
            raise BackendError("timed out before any memory was scored") from None

    def _check(self, run):
        if run.cancelled:
            raise BackendError("cancelled")
        if run.time_up():
            raise BackendError("timed out before any memory was scored")

    def _score(self, db, refs, prepared, run):
        """Score the candidates in the order given; stop at the deadline with what is scored."""
        hits = []
        for start in range(0, len(refs), SCORE_BATCH):
            if run.should_stop():
                break
            chunk = refs[start:start + SCORE_BATCH]
            try:
                rows = json.loads(db.execute(
                    "SELECT json_group_array(json_array(ref, text, date)) FROM memories WHERE ref IN (%s)"
                    % ",".join("?" * len(chunk)), chunk
                ).fetchone()[0])
            except sqlite3.OperationalError:
                if run.cancelled:
                    raise BackendError("cancelled") from None
                if run.time_up():
                    break
                raise
            for ref, text, date in rows:
                score = prepared.score(text)
                if score > 0:
                    hits.append(Hit(ref=ref, score=score, text=text, date=date))
                if run.should_stop():
                    break
        if run.cancelled:
            raise BackendError("cancelled")
        return hits

    # ------------------------------------------------------------ lifecycle

    def health(self, timeout=2.0):
        try:
            with self._read(time.monotonic() + max(0.0, float(timeout))) as db:
                return not self._closed and db.execute("SELECT 1").fetchone()[0] == 1
        except Exception:  # noqa: BLE001 - health never raises
            return False

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._db.close()
        if self._readers is not None:
            self._readers.close()
