"""Limits: requests per minute, writes per day, tenant requests in flight, calls per store.

Standard library only, in memory only: a restart clears every count.
"""
import collections
import threading
import time

RATE_LIMIT = 60
RATE_WINDOW = 60.0


class RateLimiter:
    """Sliding window: at most `limit` hits per `window` seconds for each key.

    A key whose window is empty is forgotten (checked once per window), so the memory
    held is bounded by recent activity, however many keys (tenant ids) callers invent."""

    def __init__(self, clock=time.monotonic, limit=RATE_LIMIT, window=RATE_WINDOW):
        self._clock = clock
        self._limit = limit
        self._window = window
        self._hits = {}
        self._swept = None
        self._lock = threading.Lock()

    def allow(self, key, limit=None):
        limit = self._limit if limit is None else limit
        now = self._clock()
        with self._lock:
            self._sweep(now)
            hits = self._hits.get(key)
            if hits is None:
                if limit < 1:
                    return False
                hits = self._hits[key] = collections.deque()
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def _sweep(self, now):
        """Drop every key without a hit inside the window (lock held)."""
        if self._swept is not None and now - self._swept < self._window:
            return
        self._swept = now
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self._window]:
            del self._hits[key]

    def __len__(self):
        with self._lock:
            return len(self._hits)


class DailyCaps:
    """Counts per key and calendar day. The day starts at midnight in UTC+`utc_offset_hours`.

    When the date changes every count is cleared, so the store holds at most one day of keys.
    reserve() checks and counts in one step, so concurrent writes cannot all pass the check
    before any of them is counted; release() gives a reservation back (a write that did
    not happen)."""

    def __init__(self, wall=time.time, utc_offset_hours=0):
        self._wall = wall
        self._offset = utc_offset_hours * 3600
        self._day = None
        self._counts = {}
        self._lock = threading.Lock()

    def _roll(self):
        day = time.gmtime(self._wall() + self._offset)[:3]
        if day != self._day:
            self._day = day
            self._counts.clear()
        return day

    def reserve(self, key, limit):
        """Count one write for `key` today if it is still under `limit`. True when counted."""
        return self.reserve_day(key, limit) is not None

    def reserve_day(self, key, limit):
        """Like reserve(), but returns the day the write was counted on (None when refused),
        for release()."""
        with self._lock:
            day = self._roll()
            count = self._counts.get(key, 0)
            if count >= limit:
                return None
            self._counts[key] = count + 1
            return day

    def release(self, key, day=None):
        """Give back one reservation of `key`. A reservation from an earlier day is already
        gone with that day's counts and is not taken from today's."""
        with self._lock:
            today = self._roll()
            if day is not None and day != today:
                return
            count = self._counts.get(key, 0)
            if count <= 1:
                self._counts.pop(key, None)
            else:
                self._counts[key] = count - 1

    def allow(self, key, limit):
        """True while `key` is under `limit` today. Looks only, never records."""
        with self._lock:
            self._roll()
            return self._counts.get(key, 0) < limit

    def count(self, key):
        """Record one write for `key` today, without a limit."""
        with self._lock:
            self._roll()
            self._counts[key] = self._counts.get(key, 0) + 1


class TenantBudget:
    """Caps how many tenant requests are in flight: in total and per tenant.

    Taken once per request (not per store call), so a request is either admitted whole or
    refused up front, and one tenant cannot hold every tenant slot.
    """

    def __init__(self, total=3, per_tenant=2):
        self.total = total
        self.per_tenant = per_tenant
        self._held = collections.Counter()
        self._lock = threading.Lock()

    def acquire(self, tenant_key):
        with self._lock:
            if sum(self._held.values()) >= self.total or self._held[tenant_key] >= self.per_tenant:
                return False
            self._held[tenant_key] += 1
            return True

    def release(self, tenant_key):
        with self._lock:
            self._held[tenant_key] -= 1
            if self._held[tenant_key] <= 0:
                del self._held[tenant_key]


class StoreSlots:
    """At most `limit` calls in flight per store, so one hung store cannot hold every worker.

    Keyed by the store object (its id while a call holds a slot: the call keeps the store
    alive, so the id cannot be reused meanwhile)."""

    def __init__(self, limit):
        self.limit = limit
        self._held = {}
        self._cond = threading.Condition()

    def acquire(self, store, wait=0.0):
        """Take a slot of `store`, waiting up to `wait` seconds for one. True when taken."""
        key = id(store)
        end = time.monotonic() + max(wait, 0.0)
        with self._cond:
            while self._held.get(key, 0) >= self.limit:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            self._held[key] = self._held.get(key, 0) + 1
            return True

    def release(self, store):
        key = id(store)
        with self._cond:
            count = self._held.get(key, 0) - 1
            if count <= 0:
                self._held.pop(key, None)
            else:
                self._held[key] = count
            self._cond.notify_all()

    def in_flight(self, store):
        with self._cond:
            return self._held.get(id(store), 0)
