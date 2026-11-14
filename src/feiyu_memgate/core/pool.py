"""A small fixed-size worker pool with cancellable tasks. Standard library only.

Backend calls run on the pool. Every call gets a `ctl`, its cancellation handle:

- ctl.attach(sock) / ctl.detach(): a backend that opens a socket hands it over, and a
  caller that runs out of budget shuts it down (Task.cancel()), so the worker is freed
  whatever the upstream is doing (dribbled headers or body included);
- ctl.cancelled / ctl.on_cancel(fn): a backend without a raw socket (SQLite, a vendor
  SDK) looks at the flag between steps, or registers a function that stops its work.

A call made outside the pool gets NULL_CTL, which is never cancelled: `ctl` is never None.
"""
import queue
import socket
import threading
import time

from ..errors import BackendError

POOL_SIZE = 8


def _kill(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _call_quietly(fn):
    # A cancel hook runs on the caller's thread: its failure must not stop the caller.
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


class NullCtl:
    """The ctl of a call that runs outside the pool: nothing can cancel it."""

    @property
    def cancelled(self):
        return False

    def attach(self, sock):
        pass

    def detach(self):
        pass

    def on_cancel(self, fn):
        pass  # never cancelled, so fn is never called


NULL_CTL = NullCtl()
# The old private name: backends written before NULL_CTL existed import it and call it.
_NoCtl = NullCtl


class Task:
    """One unit of pool work; also the cancellation handle (ctl) of the call it runs."""

    def __init__(self, fn):
        self._fn = fn
        self._lock = threading.Lock()
        self._sock = None
        self._cancelled = False
        self._finished = False
        self._hooks = []
        self.done = threading.Event()
        self.result = None
        self.error = None

    @property
    def cancelled(self):
        """True once the caller gave up on this call."""
        with self._lock:
            return self._cancelled

    def attach(self, sock):
        with self._lock:
            if self._cancelled:
                _kill(sock)
                raise BackendError("cancelled")
            self._sock = sock

    def detach(self):
        with self._lock:
            self._sock = None

    def on_cancel(self, fn):
        """Call fn() once when this call is cancelled, at once if it already was.

        fn runs on the cancelling thread, possibly while the call is still working: keep it
        to setting a flag or interrupting the call's own handle. Once the call has returned
        it is never called, so it cannot disturb a later call that reuses the handle."""
        with self._lock:
            if self._finished:
                return
            if not self._cancelled:
                self._hooks.append(fn)
                return
        _call_quietly(fn)

    def cancel(self):
        with self._lock:
            self._cancelled = True
            sock = self._sock
            hooks, self._hooks = self._hooks, []
        if sock is not None:
            _kill(sock)
        for fn in hooks:
            _call_quietly(fn)

    def wait(self, timeout):
        return self.done.wait(max(timeout, 0))

    def ok(self):
        return self.done.is_set() and self.error is None

    def _run(self):
        try:
            self.result = self._fn(self)
        except BaseException as err:  # noqa: BLE001 - the caller decides what to do
            self.error = err
        finally:
            with self._lock:
                self._finished = True
                self._hooks = []
            self.done.set()


class Pool:
    """Fixed upper bound on upstream worker threads; never queues beyond `size`.

    submit() may wait a moment for a worker (`wait`), and may be asked to leave some
    workers free for others (`keep`): tenant requests use that so the shared stores'
    clients always keep part of the pool.
    """

    def __init__(self, size=POOL_SIZE):
        self.size = size
        self._queue = queue.SimpleQueue()
        self._workers = []
        self._busy = 0
        self._closed = False
        self._lock = threading.Condition()

    @property
    def in_flight(self):
        with self._lock:
            return self._busy

    @property
    def workers(self):
        with self._lock:
            return len(self._workers)

    def submit(self, fn, wait=0.0, keep=0):
        """Run fn(task) on a worker. Returns the task, or None when no worker became free
        within `wait` seconds. With `keep`, a worker is only taken while more than `keep`
        are free."""
        end = time.monotonic() + max(wait, 0.0)
        with self._lock:
            while not self._closed and self.size - self._busy <= keep:
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._lock.wait(left)
            if self._closed:
                return None
            task = Task(fn)
            self._busy += 1
            if self._busy > len(self._workers) and len(self._workers) < self.size:
                worker = threading.Thread(target=self._work, name="memory-pool", daemon=True)
                self._workers.append(worker)
                try:
                    worker.start()
                except Exception:
                    # Roll everything back so a failed start cannot leak capacity.
                    self._workers.remove(worker)
                    self._busy -= 1
                    self._lock.notify_all()
                    return None
        self._queue.put(task)
        return task

    def _work(self):
        while True:
            task = self._queue.get()
            if task is None:
                return
            try:
                task._run()
            finally:
                with self._lock:
                    self._busy -= 1
                    # Waiters ask for different amounts of room (keep): wake them all.
                    self._lock.notify_all()

    def close(self):
        with self._lock:
            self._closed = True
            workers = [w for w in self._workers if w.is_alive()]
            self._lock.notify_all()
        for _ in workers:
            self._queue.put(None)
        for worker in workers:
            worker.join(1)
