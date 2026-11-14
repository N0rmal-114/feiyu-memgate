import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from feiyu_memgate import config, registry
from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.backends.hybrid import HybridBackend
from feiyu_memgate.backends.sqlite import SQLiteBackend
from feiyu_memgate.errors import BackendError, ConfigError
from feiyu_memgate.model import Hit

from tests.test_backend_embedding import FakeCtl, FakeEmbeddingServer, closed_port_url

TOKEN = "test-only-owner-token-0000000000000000"


class ScriptedChild(MemoryBackend):
    """A child whose answers the test decides."""
    writable = True

    def __init__(self, hits=(), fail_find=False, fail_put=False):
        self.hits = list(hits)
        self.fail_find, self.fail_put = fail_find, fail_put
        self.store = {}
        self.deleted = []
        self.asked = []
        self.closed = False

    def find(self, query, limit, timeout=None, ctl=None):
        self.asked.append((query, limit, timeout))
        if self.fail_find:
            raise RuntimeError("boom")
        return list(self.hits)

    def put(self, text, ref=None, date=None):
        if self.fail_put:
            raise RuntimeError("boom")
        self.store[ref] = text
        return ref

    def delete(self, ref):
        self.deleted.append(ref)
        return self.store.pop(ref, None) is not None

    def read(self, ref, timeout=None, ctl=None):
        if ref not in self.store:
            raise KeyError(ref)
        return self.store[ref]

    def close(self):
        self.closed = True


def hybrid_of(*children, **options):
    """A HybridBackend around ready-made children (bypassing the registry)."""
    with mock.patch("feiyu_memgate.backends.hybrid.registry.create", side_effect=lambda kind, spec: spec):
        be = HybridBackend(list(children), **options)
    return be


class HybridCase(unittest.TestCase):
    def setUp(self):
        self.server = FakeEmbeddingServer()
        self.addCleanup(self.server.stop)

    def specs(self, **embedding):
        return [
            {"type": "sqlite", "path": ":memory:"},
            dict({"type": "embedding", "path": ":memory:", "url": self.server.url, "model": "fake-model"}, **embedding),
        ]

    def make(self, **options):
        be = registry.create("backend", {"type": "hybrid", "children": self.specs(), **options})
        self.addCleanup(be.close)
        return be


class HybridFusionTest(unittest.TestCase):
    def test_both_children_outrank_one(self):
        a = ScriptedChild([Hit("both", 0.5), Hit("only-a", 0.6)])
        b = ScriptedChild([Hit("both", 0.5), Hit("only-b", 0.6)])
        hits = hybrid_of(a, b).find("q", 5)
        self.assertEqual([h.ref for h in hits][0], "both")
        self.assertAlmostEqual(hits[0].score, 1 - 0.5 * 0.5)
        self.assertEqual({h.ref for h in hits}, {"both", "only-a", "only-b"})

    def test_a_strong_single_signal_still_passes(self):
        a = ScriptedChild([Hit("x", 0.9, "text x", "2025-01-01")])
        b = ScriptedChild([])
        hits = hybrid_of(a, b).find("q", 5)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].score, 0.9)
        self.assertEqual((hits[0].text, hits[0].date), ("text x", "2025-01-01"))

    def test_weights_are_clamped_and_applied(self):
        a = ScriptedChild([Hit("x", 0.8), Hit("y", 0.5)])
        b = ScriptedChild([Hit("y", 0.4)])
        be = hybrid_of(a, b, weights=[2.0, 0.5])  # 2.0 clamps to 1.0
        self.assertEqual(be.weights, [1.0, 0.5])
        by = {h.ref: h.score for h in be.find("q", 5)}
        self.assertAlmostEqual(by["x"], 0.8)
        self.assertAlmostEqual(by["y"], 1 - (1 - 0.5) * (1 - 0.5 * 0.4))
        self.assertTrue(all(0.0 <= s <= 1.0 for s in by.values()))
        with self.assertRaises(ValueError):
            hybrid_of(a, b, weights=[1.0])

    def test_text_comes_from_whichever_child_has_it_and_limit_applies(self):
        a = ScriptedChild([Hit("x", 0.5, ""), Hit("y", 0.4, "")])
        b = ScriptedChild([Hit("x", 0.5, "the text")])
        hits = hybrid_of(a, b).find("q", 1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].text, "the text")

    def test_each_child_is_asked_for_three_times_the_limit_with_a_split_budget(self):
        a, b = ScriptedChild(), ScriptedChild()
        hybrid_of(a, b).find("q", 4, timeout=2.0)
        self.assertEqual(a.asked[0][1], 12)
        self.assertEqual(b.asked[0][1], 12)
        self.assertLessEqual(a.asked[0][2], 1.0)
        self.assertLessEqual(b.asked[0][2], 2.0)

    def test_meta_carries_child_scores(self):
        if "meta" not in getattr(Hit, "__dataclass_fields__", {}):
            self.skipTest("Hit has no meta field in this version")
        a = ScriptedChild([Hit("x", 0.5)])
        b = ScriptedChild([Hit("x", 0.25, meta={"cosine": 0.6}), Hit("y", 0.3)])
        by = {h.ref: h for h in hybrid_of(a, b).find("q", 5)}
        self.assertEqual(by["x"].meta["child_scores"], [0.5, 0.25])
        self.assertEqual(by["x"].meta["cosine"], 0.6)
        self.assertEqual(by["y"].meta["child_scores"], [None, 0.3])

    def test_a_failing_child_is_skipped_unless_all_fail(self):
        good = ScriptedChild([Hit("x", 0.7)])
        bad = ScriptedChild(fail_find=True)
        self.assertEqual([h.ref for h in hybrid_of(bad, good).find("q", 5)], ["x"])
        with self.assertRaises(BackendError):
            hybrid_of(ScriptedChild(fail_find=True), ScriptedChild(fail_find=True)).find("q", 5)

    def test_no_child_hits_is_an_empty_answer_not_an_error(self):
        self.assertEqual(hybrid_of(ScriptedChild(), ScriptedChild()).find("q", 5), [])


class HybridWriteTest(unittest.TestCase):
    def test_write_goes_to_every_child_under_one_ref(self):
        a, b = ScriptedChild(), ScriptedChild()
        be = hybrid_of(a, b)
        ref = be.put("hello", date="2025-01-02")
        self.assertEqual(a.store, {ref: "hello"})
        self.assertEqual(b.store, {ref: "hello"})
        self.assertEqual(be.put("again", ref="fixed"), "fixed")
        self.assertEqual(be.put_many(["one", ("two", "r2")])[1], "r2")

    def test_a_failing_child_rolls_the_others_back(self):
        a, b, c = ScriptedChild(), ScriptedChild(fail_put=True), ScriptedChild()
        be = hybrid_of(a, b, c)
        with self.assertRaises(BackendError):
            be.put("hello", ref="r1")
        self.assertEqual(a.store, {})
        self.assertEqual(c.store, {})
        self.assertIn("r1", a.deleted)
        with self.assertRaises(BackendError):
            be.put_many([("one", "m1"), ("two", "m2")])
        self.assertEqual(a.store, {})
        self.assertEqual(a.deleted.count("m1"), 1)

    def test_rollback_failure_does_not_hide_the_write_error(self):
        a, b = ScriptedChild(), ScriptedChild(fail_put=True)
        a.delete = mock.Mock(side_effect=RuntimeError("cannot delete"))
        with self.assertRaises(BackendError):
            hybrid_of(a, b).put("x", ref="r")

    def test_read_only_refuses(self):
        be = hybrid_of(ScriptedChild(), writable=False)
        self.assertFalse(be.writable)
        for call in (lambda: be.put("x"), lambda: be.put_many(["x"]), lambda: be.delete("r"),
                     lambda: be.remember([{"role": "user", "content": "x"}])):
            with self.assertRaises(PermissionError):
                call()

    def test_children_that_cannot_store_are_refused_when_writable(self):
        class ReadOnlyChild(MemoryBackend):
            def find(self, query, limit, timeout=None, ctl=None):
                return []

        with self.assertRaises(ValueError):
            hybrid_of(ReadOnlyChild())
        hybrid_of(ReadOnlyChild(), writable=False)

    def test_construction_errors(self):
        with self.assertRaises(ValueError):
            HybridBackend([])
        with self.assertRaises(ValueError):
            HybridBackend("sqlite")
        with self.assertRaises(ConfigError):
            HybridBackend([{"type": "no-such-backend"}])

    def test_a_child_built_before_a_failure_is_closed(self):
        built = []

        def create(kind, spec):
            if spec == "bad":
                raise ConfigError("nope")
            child = ScriptedChild()
            built.append(child)
            return child

        with mock.patch("feiyu_memgate.backends.hybrid.registry.create", side_effect=create):
            with self.assertRaises(ConfigError):
                HybridBackend(["ok", "bad"])
        self.assertTrue(built[0].closed)

    def test_delete_reports_any_and_fails_when_a_child_fails(self):
        a, b = ScriptedChild(), ScriptedChild()
        be = hybrid_of(a, b)
        ref = be.put("x")
        self.assertTrue(be.delete(ref))
        self.assertFalse(be.delete(ref))
        b.delete = mock.Mock(side_effect=RuntimeError("boom"))
        with self.assertRaises(BackendError):
            be.delete("whatever")


class HybridRealChildrenTest(HybridCase):
    def test_built_through_the_registry(self):
        be = self.make()
        self.assertIsInstance(be, HybridBackend)
        self.assertEqual(HybridBackend.default_min_score, 0.70)
        self.assertEqual(HybridBackend.default_relative_ratio, 0.85)
        self.assertEqual(len(be.children), 2)

    def test_shared_refs_and_all_capabilities(self):
        be = self.make()
        ref = be.put("我喜欢喝燕麦拿铁咖啡", date="2025-01-02")
        self.assertEqual([c.count() for c in be.children], [1, 1])
        self.assertEqual([list(c.refs()) for c in be.children], [[(ref, "2025-01-02")]] * 2)
        self.assertEqual(be.read(ref), "我喜欢喝燕麦拿铁咖啡")
        self.assertEqual(be.count(), 1)
        self.assertEqual(list(be.refs()), [(ref, "2025-01-02")])
        self.assertEqual(list(be.items()), [(ref, "我喜欢喝燕麦拿铁咖啡", "2025-01-02")])
        with self.assertRaises(KeyError):
            be.read("missing")
        self.assertTrue(be.health())
        self.assertTrue(be.delete(ref))
        self.assertEqual([c.count() for c in be.children], [0, 0])
        self.assertFalse(be.delete(ref))

    def test_keyword_and_vector_both_find_it_and_the_memory_ranks_first(self):
        be = self.make()
        be.put_many([("我喜欢喝燕麦拿铁咖啡", "coffee"), ("周末去游泳馆游泳", "swim"), ("养了一只橘猫", "cat")])
        hits = be.find("喜欢喝燕麦拿铁咖啡", 5)
        self.assertEqual(hits[0].ref, "coffee")
        self.assertGreater(hits[0].score, 0.9)
        self.assertEqual(hits[0].text, "我喜欢喝燕麦拿铁咖啡")
        self.assertTrue(all(0.0 <= h.score <= 1.0 for h in hits))

    def test_remember_roles_receipt_and_refs(self):
        be = self.make()
        receipt = be.remember([
            {"role": "user", "content": "我养了一只叫团子的猫"},
            {"role": "assistant", "content": "好可爱"},
            {"role": "user", "content": "团子爱吃金枪鱼"},
        ])
        refs = be.receipt_refs(receipt)
        self.assertEqual(len(refs), 2)
        self.assertEqual([c.count() for c in be.children], [2, 2])
        self.assertIsNone(be.remember([{"role": "assistant", "content": "nothing kept"}]))
        self.assertEqual(be.count(), 2)

    def test_one_failing_endpoint_leaves_keyword_search_working(self):
        be = registry.create("backend", {"type": "hybrid", "children": [
            {"type": "sqlite", "path": ":memory:"},
            {"type": "embedding", "path": ":memory:", "url": closed_port_url(), "model": "fake-model", "timeout": 1.0},
        ]})
        self.addCleanup(be.close)
        sqlite_child = be.children[0]
        sqlite_child.put("我喜欢喝燕麦拿铁咖啡", ref="coffee")  # the embedding child cannot be written now
        start = time.monotonic()
        hits = be.find("燕麦拿铁咖啡", 3, timeout=4.0)
        self.assertEqual([h.ref for h in hits], ["coffee"])
        self.assertLess(time.monotonic() - start, 8.0)
        self.assertIsInstance(be.health(timeout=0.5), bool)  # health never raises

    def test_write_with_a_dead_endpoint_fails_and_rolls_back(self):
        be = registry.create("backend", {"type": "hybrid", "children": [
            {"type": "sqlite", "path": ":memory:"},
            {"type": "embedding", "path": ":memory:", "url": closed_port_url(), "model": "fake-model", "timeout": 1.0},
        ]})
        self.addCleanup(be.close)
        with self.assertRaises(BackendError):
            be.put("something to keep", ref="r")
        self.assertEqual(be.children[0].count(), 0)  # the sqlite write was undone

    def test_embedding_child_batches_a_bulk_write(self):
        be = self.make()
        be.put_many(["memory %d about subject %d" % (i, i % 5) for i in range(40)])
        sizes = [len(r["body"]["input"]) for r in self.server.requests]
        self.assertEqual(sizes, [32, 8])

    def test_ctl_reaches_the_embedding_child(self):
        be = self.make()
        ctl = FakeCtl()
        be.put_many(["one memory", "two memory"], ctl=ctl)
        be.find("one memory", 3, ctl=ctl)
        self.assertGreaterEqual(ctl.attached, 2)
        self.assertEqual(ctl.attached, ctl.detached)

    def test_child_env_options_are_resolved_from_the_environment(self):
        spec = {"type": "hybrid", "children": [
            {"type": "embedding", "path": ":memory:", "url": self.server.url, "model": "fake-model",
             "key_env": "TEST_HYBRID_KEY"}]}
        with mock.patch.dict(os.environ, {"TEST_HYBRID_KEY": "test-only-fake-key-0002"}):
            be = registry.create("backend", spec)
        self.addCleanup(be.close)
        be.put("x text", ref="a")
        self.assertEqual(self.server.requests[-1]["auth"], "Bearer test-only-fake-key-0002")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_HYBRID_KEY", None)
            with self.assertRaises(ConfigError) as cm:
                registry.create("backend", spec)
        self.assertIn("TEST_HYBRID_KEY", str(cm.exception))

    def test_persistence_across_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            children = [{"type": "sqlite", "path": os.path.join(tmp, "kw.db")},
                        {"type": "embedding", "path": os.path.join(tmp, "vec.db"), "url": self.server.url,
                         "model": "fake-model"}]
            be = registry.create("backend", {"type": "hybrid", "children": children})
            be.put("我最喜欢的饮料是乌龙茶", ref="tea")
            be.close()
            again = registry.create("backend", {"type": "hybrid", "children": children})
            try:
                self.assertEqual(again.find("乌龙茶", 3)[0].ref, "tea")
            finally:
                again.close()

    def test_thread_safety_smoke(self):
        be = self.make()
        errors = []

        def worker(n):
            try:
                for i in range(8):
                    be.put("thread %d memory %d" % (n, i), ref="t%d-%d" % (n, i))
                    be.find("thread %d memory %d" % (n, i), 3)
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual([c.count() for c in be.children], [32, 32])


class HybridGatewayTest(HybridCase):
    def build(self):
        cfg = {
            "stores": {"private": {"type": "hybrid", "children": self.specs()}},
            "clients": {"owner": {"token_env": "T", "recall": ["private"], "write": "private"}},
        }
        gw = config.build_gateway(cfg, env={"T": TOKEN}, log=lambda line: None)
        self.addCleanup(gw.close)
        return gw

    def test_remember_then_recall_end_to_end(self):
        gw = self.build()
        reply = gw.remember("owner", {"messages": [
            {"role": "user", "content": "我喜欢喝燕麦拿铁咖啡，不加糖"},
            {"role": "user", "content": "周末通常去游泳馆游泳"},
        ]})
        self.assertEqual(reply.status, 200, reply.body)
        reply = gw.recall("owner", {"query": "喜欢喝燕麦拿铁咖啡"})
        self.assertEqual(reply.status, 200, reply.body)
        texts = [r["text"] for r in reply.body["results"]]
        self.assertTrue(texts and "燕麦拿铁" in texts[0], texts)
        self.assertEqual(sum("燕麦拿铁" in t for t in texts), 1)  # one memory, not one per child

    def test_a_dead_embedding_endpoint_does_not_stop_recall_of_keyword_hits(self):
        cfg = {
            "stores": {"private": {"type": "hybrid", "children": [
                {"type": "sqlite", "path": ":memory:"},
                {"type": "embedding", "path": ":memory:", "url": closed_port_url(), "model": "fake-model",
                 "timeout": 0.5}]}},
            "clients": {"owner": {"token_env": "T", "recall": ["private"], "write": "private"}},
        }
        gw = config.build_gateway(cfg, env={"T": TOKEN}, log=lambda line: None)
        self.addCleanup(gw.close)
        gw.stores["private"].children[0].put("我喜欢喝燕麦拿铁咖啡", ref="coffee")
        reply = gw.recall("owner", {"query": "燕麦拿铁咖啡"})
        self.assertEqual(reply.status, 200, reply.body)
        self.assertEqual(len(reply.body["results"]), 1)


if __name__ == "__main__":
    unittest.main()
