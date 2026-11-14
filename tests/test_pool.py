"""The worker pool and the ctl every backend call gets."""
import threading
import time
import unittest

from feiyu_memgate.core import limits
from feiyu_memgate.core.pool import NULL_CTL, NullCtl, Pool, _NoCtl
from feiyu_memgate.core.recall import BUSY, FAILED, OK, call, submit
from feiyu_memgate.errors import BackendError


class CtlTests(unittest.TestCase):
    def setUp(self):
        self.pool = Pool(4)
        self.addCleanup(self.pool.close)

    def test_null_ctl_is_never_cancelled_and_never_calls_back(self):
        calls = []
        NULL_CTL.on_cancel(lambda: calls.append(1))
        NULL_CTL.attach(object())
        NULL_CTL.detach()
        self.assertFalse(NULL_CTL.cancelled)
        self.assertEqual(calls, [])
        self.assertIsInstance(_NoCtl(), NullCtl)  # the old name still works for backends that call it

    def test_a_cancelled_call_sees_the_flag_and_its_hook_runs_once(self):
        started, release = threading.Event(), threading.Event()
        hooks = []

        def work(ctl):
            ctl.on_cancel(lambda: hooks.append("stop"))
            started.set()
            release.wait(5)
            return ctl.cancelled

        task = self.pool.submit(work)
        self.assertTrue(started.wait(2))
        self.assertFalse(task.cancelled)
        task.cancel()
        task.cancel()
        release.set()
        self.assertTrue(task.wait(2))
        self.assertTrue(task.result)
        self.assertEqual(hooks, ["stop"])

    def test_a_hook_registered_after_the_cancel_runs_at_once(self):
        def work(ctl):
            while not ctl.cancelled:
                time.sleep(0.005)
            seen = []
            ctl.on_cancel(lambda: seen.append("late"))
            return seen

        task = self.pool.submit(work)
        time.sleep(0.02)
        task.cancel()
        self.assertTrue(task.wait(2))
        self.assertEqual(task.result, ["late"])

    def test_no_hook_runs_after_the_call_returned(self):
        hooks = []
        task = self.pool.submit(lambda ctl: ctl.on_cancel(lambda: hooks.append("x")))
        self.assertTrue(task.wait(2))
        task.cancel()  # a late cancel must not disturb a handle a later call may reuse
        self.assertEqual(hooks, [])

    def test_a_failing_hook_does_not_stop_the_canceller(self):
        gate = threading.Event()

        def work(ctl):
            ctl.on_cancel(lambda: 1 / 0)
            gate.wait(5)

        task = self.pool.submit(work)
        time.sleep(0.02)
        task.cancel()
        gate.set()
        self.assertTrue(task.wait(2))

    def test_attach_after_cancel_refuses(self):
        def work(ctl):
            while not ctl.cancelled:
                time.sleep(0.005)
            ctl.attach(DummySock())

        task = self.pool.submit(work)
        time.sleep(0.02)
        task.cancel()
        self.assertTrue(task.wait(2))
        self.assertIsInstance(task.error, BackendError)


class DummySock:
    def shutdown(self, how):
        pass


class SubmitTests(unittest.TestCase):
    def test_submit_waits_briefly_for_a_free_worker(self):
        pool = Pool(1)
        self.addCleanup(pool.close)
        first = pool.submit(lambda ctl: time.sleep(0.1))
        self.assertIsNotNone(first)
        self.assertIsNone(pool.submit(lambda ctl: 1))  # no wait: refused at once
        second = pool.submit(lambda ctl: 2, wait=1.0)
        self.assertIsNotNone(second)
        self.assertTrue(second.wait(2))
        self.assertEqual(second.result, 2)

    def test_keep_leaves_workers_free_for_others(self):
        pool = Pool(3)
        self.addCleanup(pool.close)
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.assertIsNotNone(pool.submit(lambda ctl: gate.wait(5), keep=1))
        self.assertIsNotNone(pool.submit(lambda ctl: gate.wait(5), keep=1))
        self.assertIsNone(pool.submit(lambda ctl: 1, keep=1))  # one free worker, kept for others
        self.assertIsNotNone(pool.submit(lambda ctl: gate.wait(5)))

    def test_store_slots_cap_calls_per_store(self):
        pool = Pool(4)
        self.addCleanup(pool.close)
        slots = limits.StoreSlots(1)
        gate = threading.Event()
        self.addCleanup(gate.set)
        hung, other = object(), object()
        self.assertIsNotNone(submit(pool, lambda ctl: gate.wait(5), slots=slots, store=hung))
        self.assertIsNone(submit(pool, lambda ctl: 1, slots=slots, store=hung, wait=0.05))
        task = submit(pool, lambda ctl: "other", slots=slots, store=other)
        self.assertTrue(task.wait(2))
        self.assertEqual(slots.in_flight(other), 0)  # released when the call ended
        self.assertEqual(slots.in_flight(hung), 1)

    def test_call_tells_busy_from_failed(self):
        pool = Pool(1)
        self.addCleanup(pool.close)
        gate = threading.Event()
        self.addCleanup(gate.set)
        clock = time.monotonic
        self.assertEqual(call(pool, lambda ctl: 5, clock, clock() + 1), (OK, 5))
        outcome, error = call(pool, lambda ctl: 1 / 0, clock, clock() + 1)
        self.assertEqual(outcome, FAILED)
        self.assertIsInstance(error, ZeroDivisionError)
        pool.submit(lambda ctl: gate.wait(5))
        self.assertEqual(call(pool, lambda ctl: 5, clock, clock() + 1), (BUSY, None))


class RateLimiterMemoryTests(unittest.TestCase):
    def test_keys_with_an_empty_window_are_forgotten(self):
        now = [0.0]
        limiter = limits.RateLimiter(lambda: now[0])
        for i in range(1000):
            self.assertTrue(limiter.allow("client:%d" % i, 2))
        self.assertEqual(len(limiter), 1000)
        now[0] += 61
        self.assertTrue(limiter.allow("client:new", 2))
        self.assertEqual(len(limiter), 1)

    def test_a_zero_limit_refuses_without_keeping_a_key(self):
        limiter = limits.RateLimiter(lambda: 0.0)
        self.assertFalse(limiter.allow("k", 0))
        self.assertEqual(len(limiter), 0)


class DailyCapsTests(unittest.TestCase):
    def test_reserve_checks_and_counts_at_once_and_release_gives_back(self):
        caps = limits.DailyCaps(lambda: 0.0)
        self.assertTrue(caps.reserve("k", 1))
        self.assertFalse(caps.reserve("k", 1))
        caps.release("k")
        self.assertTrue(caps.reserve("k", 1))

    def test_a_reservation_from_yesterday_is_not_taken_from_today(self):
        now = [0.0]
        caps = limits.DailyCaps(lambda: now[0])
        day = caps.reserve_day("k", 2)
        now[0] += 86400
        self.assertTrue(caps.reserve("k", 2))
        caps.release("k", day)
        self.assertTrue(caps.reserve("k", 2))
        self.assertFalse(caps.reserve("k", 2))

    def test_concurrent_reservations_never_pass_the_limit(self):
        caps = limits.DailyCaps(lambda: 0.0)
        results = []
        barrier = threading.Barrier(16)

        def go():
            barrier.wait()
            results.append(caps.reserve("k", 3))

        threads = [threading.Thread(target=go) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count(True), 3)


if __name__ == "__main__":
    unittest.main()
