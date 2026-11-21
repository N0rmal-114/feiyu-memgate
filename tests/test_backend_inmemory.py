import threading
import unittest
from unittest import mock

from feiyu_memgate import registry
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.core.tidy import index_grams
from feiyu_memgate.errors import BackendError


class InMemoryBackendTest(unittest.TestCase):
    def test_put_find_read(self):
        be = InMemoryBackend()
        ref = be.put("I drink oolong tea", date="2025-01-01")
        be.put("The train leaves at nine")
        hits = be.find("oolong tea", 5)
        self.assertEqual(hits[0].ref, ref)
        self.assertEqual(hits[0].date, "2025-01-01")
        self.assertTrue(0 < hits[0].score <= 1)
        self.assertEqual(be.read(ref), "I drink oolong tea")

    def test_initial_memories_limit_and_order(self):
        be = InMemoryBackend(memories=["alpha beta", "alpha beta gamma", "alpha", "zzz"])
        hits = be.find("alpha beta gamma", 2)
        self.assertEqual(len(hits), 2)
        self.assertGreaterEqual(hits[0].score, hits[1].score)
        self.assertEqual(be.find("qqqq", 5), [])

    def test_put_replaces_ref(self):
        be = InMemoryBackend()
        be.put("first version", ref="r")
        be.put("second version", ref="r")
        self.assertEqual(be.read("r"), "second version")

    def test_chinese(self):
        be = InMemoryBackend(memories=["我喜欢乌龙茶"])
        self.assertEqual(len(be.find("乌龙茶", 3)), 1)

    def test_remember_keeps_user_messages_only(self):
        be = InMemoryBackend()
        receipt = be.remember([{"role": "user", "content": "banana smoothie"}, {"role": "assistant", "content": "Quartz widget"}])
        self.assertIsInstance(receipt, str)
        self.assertEqual(len(be.find("banana smoothie", 5)), 1)
        self.assertEqual(be.find("Quartz widget", 5), [])

    def test_read_only(self):
        be = InMemoryBackend(writable=False)
        with self.assertRaises(PermissionError):
            be.remember([{"role": "user", "content": "x"}])

    def test_registry_and_threads(self):
        be = registry.create("backend", {"type": "inmemory", "memories": ["seed text"]})

        def work():
            for i in range(50):
                be.put("note %d" % i)
                be.find("note", 3)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(be.find("seed text", 1)), 1)


    def test_refs_delete_receipt_refs(self):
        be = InMemoryBackend()
        a = be.put("alpha note", ref="a", date="2025-03-04")
        be.put("beta note", ref="b")
        self.assertEqual(sorted(be.refs()), [("a", "2025-03-04"), ("b", None)])
        self.assertTrue(be.delete(a))
        self.assertFalse(be.delete(a))
        self.assertEqual(be.refs(), [("b", None)])
        receipt = be.remember([{"role": "user", "content": "one"}, {"role": "assistant", "content": "x"},
                               {"role": "user", "content": "two"}])
        self.assertEqual(sorted(be.receipt_refs(receipt)), [receipt + "-0", receipt + "-2"])
        self.assertEqual(be.receipt_refs("w-unknown"), [])
        self.assertIsNone(be.receipt_refs(""))

    def test_refs_is_a_snapshot_safe_to_delete_while_walking(self):
        be = InMemoryBackend(memories=["one one", "two two", "three three"])
        for ref, _date in be.refs():
            self.assertTrue(be.delete(ref))
        self.assertEqual(be.refs(), [])

    def test_read_only_refuses_delete_and_put_but_seeds(self):
        be = InMemoryBackend(writable=False, memories=["seed"])
        self.assertEqual(len(be.refs()), 1)
        with self.assertRaises(PermissionError):
            be.delete(be.refs()[0][0])
        with self.assertRaises(PermissionError):
            be.put("x")


    def test_remember_without_a_user_message_returns_none(self):
        be = InMemoryBackend()
        self.assertIsNone(be.remember([{"role": "assistant", "content": "x"}]))
        self.assertIsNone(be.remember([]))
        self.assertEqual(be.refs(), [])

    def test_defaults(self):
        self.assertEqual(InMemoryBackend.default_min_score, 0.30)
        self.assertEqual(InMemoryBackend.default_relative_ratio, 0.75)

    def test_scores_are_the_idf_weighted_containment(self):
        from feiyu_memgate.core.tidy import PreparedQuery, gram_weights, index_grams, normalize
        texts = ["Green Tea, with honey!", "我最喜欢乌龙茶", "tea", "乌龙茶很好喝"]
        be = InMemoryBackend(memories=texts)
        counts = {}
        for text in texts:
            for gram in index_grams(normalize(text)):
                counts[gram] = counts.get(gram, 0) + 1
        for query in ("green tea", "乌龙茶", "tea", "喝茶"):
            weights = gram_weights(normalize(query), counts.get, len(texts))
            prepared = PreparedQuery(query, weights)
            for hit in be.find(query, 5):
                self.assertEqual(hit.score, prepared.score(hit.text), query)
                self.assertTrue(0 < hit.score <= 1)

    def test_a_rare_shared_gram_outweighs_a_common_one(self):
        be = InMemoryBackend(memories=["猫的饭", "狗的饭", "鱼的饭", "鸟的饭", "龙的饭", "熊猫的故事"])
        hits = be.find("猫的饭", 10)
        by_text = {h.text: h.score for h in hits}
        self.assertEqual(hits[0].text, "猫的饭")
        self.assertAlmostEqual(by_text["猫的饭"], 1.0)
        # "狗的饭" shares the common "的饭" and nothing else; "熊猫的故事" shares the rarer "猫" with it.
        self.assertGreater(by_text["熊猫的故事"], 0.0)
        self.assertLess(by_text["狗的饭"], by_text["猫的饭"])

    def test_counts_follow_put_replace_and_delete(self):
        be = InMemoryBackend()
        be.put("乌龙茶", ref="a")
        be.put("乌龙茶", ref="a")  # replaced, not counted twice
        be.put("红茶", ref="b")
        self.assertEqual(be._frequency["茶"], 2)
        self.assertEqual(be._frequency["乌龙"], 1)
        be.put("绿茶", ref="a")  # the old text leaves the counts
        self.assertNotIn("乌龙", be._frequency)
        self.assertTrue(be.delete("b"))
        self.assertFalse(be.delete("b"))
        self.assertEqual(be._frequency["茶"], 1)
        self.assertTrue(be.delete("a"))
        self.assertEqual(dict(be._frequency), {})


class AtomicRememberTest(unittest.TestCase):
    """A write of several messages is all or nothing."""

    MESSAGES = [{"role": "user", "content": "memory number %d about tea" % i} for i in range(5)]

    def setUp(self):
        self.be = InMemoryBackend(memories=["an older memory about tea"])

    def snapshot(self):
        return (sorted(self.be.refs()), dict(self.be._items), dict(self.be._frequency),
                [h.ref for h in self.be.find("tea", 20)])

    def test_a_failure_in_the_middle_stores_nothing(self):
        before = self.snapshot()
        real = index_grams
        calls = []

        def build(*args, **kw):
            calls.append(1)
            if len(calls) == 3:
                raise ValueError("cannot index the third message")
            return real(*args, **kw)

        with mock.patch("feiyu_memgate.backends.inmemory.index_grams", build):
            with self.assertRaises(ValueError):
                self.be.remember(self.MESSAGES)
        self.assertEqual(self.snapshot(), before)

    def test_a_message_the_store_rejects_stores_nothing(self):
        before = self.snapshot()
        with self.assertRaises(Exception):
            self.be.remember(self.MESSAGES[:2] + [{"role": "user", "content": 3}] + self.MESSAGES[3:])
        self.assertEqual(self.snapshot(), before)

    def test_a_cancelled_call_stores_nothing(self):
        class Cancelled:
            cancelled = True

        before = self.snapshot()
        with self.assertRaises(BackendError):
            self.be.remember(self.MESSAGES, ctl=Cancelled())
        self.assertEqual(self.snapshot(), before)

    def test_the_cancel_flag_is_read_once_right_before_the_commit(self):
        reads = []

        class Ctl:
            @property
            def cancelled(self):
                reads.append(1)
                return False

        self.assertIsNotNone(self.be.remember(self.MESSAGES, ctl=Ctl()))
        self.assertEqual(len(reads), 1)

    def test_a_normal_call_stores_every_message(self):
        messages = self.MESSAGES[:2] + [{"role": "assistant", "content": "not kept"}] + self.MESSAGES[2:]
        receipt = self.be.remember(messages)
        refs = sorted(self.be.receipt_refs(receipt))
        self.assertEqual(refs, sorted("%s-%d" % (receipt, i) for i in (0, 1, 3, 4, 5)))
        self.assertEqual(self.be.read(receipt + "-3"), "memory number 2 about tea")
        self.assertEqual(len(self.be.refs()), 6)

    def test_nothing_kept_gives_no_receipt(self):
        self.assertIsNone(self.be.remember([{"role": "assistant", "content": "x"}]))
        self.assertEqual(len(self.be.refs()), 1)


if __name__ == "__main__":
    unittest.main()
