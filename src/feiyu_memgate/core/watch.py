"""Counters for the memory gateway: failed and busy recalls, and what became of each write.

Standard library only. Keeps write receipts and times, never any content.

A backend with tracks_writes = True is asked through write_state(receipt) until it
answers "saved" or "empty"; no answer within `wait` seconds counts as "missing". A write
pushed out of the pending list (more than max_pending at once) is "untracked": nobody
looked, so it is not known to be missing.
"""
import collections
import threading
import time

EXTRACT_WAIT = 300.0
EXTRACT_POLL = 30.0
DAY = 86400.0
HOUR = 3600.0
MAX_PENDING = 200
STATUS_TIMEOUT = 2.0


class RecallFailures:
    """How many times something happened within the last `window` seconds (failed or busy recalls)."""

    def __init__(self, clock=time.monotonic, window=HOUR):
        self._clock = clock
        self._window = window
        self._times = collections.deque()
        self._lock = threading.Lock()

    def _trim(self, now):
        while self._times and now - self._times[0] > self._window:
            self._times.popleft()

    def record(self):
        with self._lock:
            now = self._clock()
            self._times.append(now)
            self._trim(now)

    def count(self):
        with self._lock:
            self._trim(self._clock())
            return len(self._times)


class ExtractionWatch:
    def __init__(self, clock=time.monotonic, wait=EXTRACT_WAIT, poll=EXTRACT_POLL, window=DAY,
                 max_pending=MAX_PENDING):
        self._clock = clock
        self._wait = wait
        self._poll = poll
        self._window = window
        self._max = max_pending
        self._pending = collections.OrderedDict()  # id -> [client, tracked_at, next_check]
        self._done = collections.deque()  # (time, state)
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._thread = None

    def _finish(self, now, state):
        """Record an outcome (lock held) and drop outcomes older than the window."""
        self._done.append((now, state))
        while self._done and now - self._done[0][0] > self._window:
            self._done.popleft()

    def track(self, session_id, client, on_saved=None):
        """Follow one write. `on_saved(receipt, store)` is called once when it is reported
        "saved" (its exceptions are swallowed)."""
        with self._lock:
            now = self._clock()
            self._pending[session_id] = [client, now, now + self._poll, on_saved]
            while len(self._pending) > self._max:
                self._pending.popitem(last=False)
                self._finish(now, "untracked")

    def tick(self):
        now = self._clock()
        with self._lock:
            due = [(sid, item[0]) for sid, item in self._pending.items() if item[2] <= now]
        for sid, client in due:
            try:
                state = client.write_state(sid, STATUS_TIMEOUT)
            except Exception:  # noqa: BLE001 - a failed look is "not finished yet"
                state = None
            now = self._clock()
            with self._lock:
                item = self._pending.get(sid)
                if item is None:
                    continue
                callback = None
                if state in ("saved", "empty"):
                    del self._pending[sid]
                    self._finish(now, state)
                    callback = item[3] if state == "saved" else None
                elif now - item[1] >= self._wait:
                    del self._pending[sid]
                    self._finish(now, "missing")
                else:
                    item[2] = now + self._poll
            if callback is not None:
                try:
                    callback(sid, client)
                except Exception:  # noqa: BLE001 - a failed follow-up must not stop the watch
                    pass

    def counts(self):
        with self._lock:
            now = self._clock()
            while self._done and now - self._done[0][0] > self._window:
                self._done.popleft()
            out = {"saved": 0, "empty": 0, "missing": 0, "untracked": 0}
            for _, state in self._done:
                out[state] += 1
            out["pending"] = len(self._pending)
            return out

    def _loop(self):
        while not self._halt.wait(self._poll):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the watcher must not die
                pass

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="extraction-watch", daemon=True)
            self._thread.start()

    def stop(self):
        self._halt.set()
        thread = self._thread
        if thread is not None:
            thread.join(2)
