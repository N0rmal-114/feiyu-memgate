"""The pool's note for the access log, and the way a store's note reaches the request's log line."""
import threading
import unittest

from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.core.pool import NOTE_MAX, NOTE_WORD, NULL_CTL, Pool, Task
from feiyu_memgate.gateway.app import Gateway
from feiyu_memgate.model import ClientPolicy, Hit

TOKEN = "test-only-owner-token-0000000000000000"


class TaskNoteTest(unittest.TestCase):
    def test_notes_are_distinct_words_in_order(self):
        task = Task(lambda t: None)
        self.assertEqual(task.notes, [])
        for word in ("a-word", "other", "a-word"):
            task.note(word)
        self.assertEqual(task.notes, ["a-word", "other"])

    def test_the_list_returned_is_a_copy(self):
        task = Task(lambda t: None)
        task.note("one")
        task.notes.append("changed")
        self.assertEqual(task.notes, ["one"])

    def test_only_a_few_short_words_are_kept(self):
        task = Task(lambda t: None)
        task.note("")
        task.note(None)
        task.note(12)
        self.assertEqual(task.notes, [])
        task.note("x" * 500)
        self.assertEqual(task.notes, ["x" * NOTE_WORD])
        for i in range(50):
            task.note("w%d" % i)
        self.assertEqual(len(task.notes), NOTE_MAX)

    def test_notes_from_several_threads_are_all_kept_once(self):
        task = Task(lambda t: None)
        start = threading.Barrier(4)

        def work():
            start.wait(5)
            for i in range(NOTE_MAX):
                task.note("n%d" % i)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertEqual(sorted(task.notes), sorted("n%d" % i for i in range(NOTE_MAX)))

    def test_a_call_outside_the_pool_can_note_and_nobody_keeps_it(self):
        NULL_CTL.note("anything")
        self.assertEqual(list(NULL_CTL.notes), [])

    def test_a_note_made_on_a_pool_worker_is_on_the_task(self):
        pool = Pool(2)
        self.addCleanup(pool.close)
        task = pool.submit(lambda t: t.note("from-worker"))
        self.assertTrue(task.wait(5))
        self.assertEqual(task.notes, ["from-worker"])


class NotingStore(MemoryBackend):
    def __init__(self, words, fail=False):
        self.words, self.fail = words, fail

    def find(self, query, limit, timeout=None, ctl=None):
        for word in self.words:
            ctl.note(word)
        if self.fail:
            raise RuntimeError("boom")
        return [Hit("x", 0.95, "text of x", "2025-01-01")]


class RecallCopiesNotesTest(unittest.TestCase):
    def reply(self, *stores):
        names = ["s%d" % i for i in range(len(stores))]
        gw = Gateway(dict(zip(names, stores)), {"owner": ClientPolicy("owner", recall=tuple(names))},
                     {"owner": TOKEN}, log=lambda line: None)
        self.addCleanup(gw.close)
        return gw.recall("owner", {"query": "the secret query"})

    def test_a_stores_note_is_in_the_replys_access_log_note(self):
        reply = self.reply(NotingStore(["child-failed"]))
        self.assertEqual(reply.status, 200, reply.body)
        self.assertIn("child-failed", reply.note.split(","))
        self.assertNotIn("secret", reply.note)

    def test_the_notes_of_a_store_that_then_failed_are_kept_too(self):
        reply = self.reply(NotingStore(["child-failed"], fail=True), NotingStore([]))
        self.assertEqual(reply.status, 200, reply.body)
        self.assertIn("child-failed", reply.note.split(","))

    def test_the_same_word_from_two_stores_is_one_word(self):
        reply = self.reply(NotingStore(["child-failed"]), NotingStore(["child-failed"]))
        self.assertEqual(reply.note.split(",").count("child-failed"), 1)

    def test_no_note_without_a_word(self):
        self.assertNotIn("child-failed", self.reply(NotingStore([])).note)


if __name__ == "__main__":
    unittest.main()
