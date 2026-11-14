import unittest

from feiyu_memgate.backends.openviking import extraction_state
from feiyu_memgate.core import watch


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeStore:
    """A backend as the watch sees it: only write_state(receipt, timeout)."""

    def __init__(self, state=None, error=False):
        self.state = state
        self.error = error
        self.asked = []

    def write_state(self, receipt, timeout=None):
        self.asked.append(receipt)
        if self.error:
            raise RuntimeError("boom")
        return self.state


class WatchTests(unittest.TestCase):
    def test_extraction_state(self):
        self.assertEqual(extraction_state({"commit_count": 1, "memories_extracted": {"total": 2}}), "saved")
        self.assertEqual(extraction_state({"commit_count": 1, "llm_token_usage": {"total_tokens": 900},
                                           "memories_extracted": {"total": 0}}), "empty")
        self.assertIsNone(extraction_state({"commit_count": 0}))
        self.assertIsNone(extraction_state({"commit_count": True, "memories_extracted": {"total": True}}))
        self.assertIsNone(extraction_state("nope"))

    def test_watch_marks_missing_after_wait(self):
        clock = Clock()
        w = watch.ExtractionWatch(clock, wait=300, poll=30)
        w.track("gw-1", FakeStore(state=None))
        clock.t += 301
        w.tick()
        self.assertEqual(w.counts()["missing"], 1)
        self.assertEqual(w.counts()["pending"], 0)

    def test_watch_keeps_pending_before_wait_and_not_polled_early(self):
        clock = Clock()
        w = watch.ExtractionWatch(clock, wait=300, poll=30)
        c = FakeStore(state=None)
        w.track("gw-1", c)
        clock.t += 10
        w.tick()
        self.assertEqual(c.asked, [])
        clock.t += 25
        w.tick()
        self.assertEqual(c.asked, ["gw-1"])
        self.assertEqual(w.counts(), {"saved": 0, "empty": 0, "missing": 0, "untracked": 0, "pending": 1})

    def test_watch_counts_saved_and_forgets_after_a_day(self):
        clock = Clock()
        w = watch.ExtractionWatch(clock, wait=300, poll=30, window=86400)
        w.track("gw-1", FakeStore(state="saved"))
        w.track("gw-2", FakeStore(state="empty"))
        clock.t += 31
        w.tick()
        self.assertEqual(w.counts(), {"saved": 1, "empty": 1, "missing": 0, "untracked": 0, "pending": 0})
        clock.t += 86401
        self.assertEqual(w.counts(), {"saved": 0, "empty": 0, "missing": 0, "untracked": 0, "pending": 0})

    def test_watch_status_error_counts_as_not_done(self):
        clock = Clock()
        w = watch.ExtractionWatch(clock, wait=300, poll=30)
        w.track("gw-1", FakeStore(error=True))
        clock.t += 31
        w.tick()
        self.assertEqual(w.counts()["pending"], 1)
        clock.t += 300
        w.tick()
        self.assertEqual(w.counts()["missing"], 1)

    def test_watch_caps_pending_and_an_evicted_write_is_untracked_not_missing(self):
        clock = Clock()
        w = watch.ExtractionWatch(clock, max_pending=200)
        for i in range(201):
            w.track("gw-%d" % i, FakeStore(state=None))
        c = w.counts()
        self.assertEqual((c["pending"], c["missing"], c["untracked"]), (200, 0, 1))

    def test_recall_failures_window(self):
        clock = Clock()
        f = watch.RecallFailures(clock)
        f.record()
        f.record()
        self.assertEqual(f.count(), 2)
        clock.t += 3601
        self.assertEqual(f.count(), 0)

    def test_done_store_is_trimmed_without_counts(self):
        clock = Clock()
        w = watch.ExtractionWatch(clock, wait=300, poll=30, window=1000)
        for i in range(50):
            w.track("gw-%d" % i, FakeStore(state="saved"))
            clock.t += 31
            w.tick()
            clock.t += 100
        self.assertLessEqual(len(w._done), 1000 // 131 + 1)
        self.assertGreater(len(w._done), 0)

    def test_start_and_stop(self):
        w = watch.ExtractionWatch(Clock(), poll=0.01)
        w.start()
        w.stop()


if __name__ == "__main__":
    unittest.main()
