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

    def test_each_child_is_asked_for_three_times_the_limit_with_the_whole_budget(self):
        a, b = ScriptedChild(), ScriptedChild()
        hybrid_of(a, b).find("q", 4, timeout=2.0)
        self.assertEqual(a.asked[0][1], 12)
        self.assertEqual(b.asked[0][1], 12)
        self.assertLessEqual(a.asked[0][2], 2.0)
        self.assertLessEqual(b.asked[0][2], 2.0)
        self.assertGreater(a.asked[0][2], 1.9)
        self.assertGreater(b.asked[0][2], 1.9)

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


class NeighbourChild(ScriptedChild):
    """A child with the optional `neighbours` capability: it answers what the test scripted."""

    def __init__(self, answers=None, fail=False):
        super().__init__()
        self.answers = dict(answers or {})
        self.fail = fail
        self.calls = []

    def neighbours(self, ref, limit, timeout=None, ctl=None):
        self.calls.append((ref, limit, timeout, ctl))
        if self.fail:
            raise RuntimeError("boom")
        return list(self.answers.get(ref, []))[:limit]


class HybridNeighboursTest(unittest.TestCase):
    def test_answers_of_capable_children_are_merged_by_ref_keeping_the_higher_score(self):
        a = NeighbourChild({"m": [Hit("x", 0.6, "text x", "2025-01-01"), Hit("y", 0.7, "", None)]})
        b = NeighbourChild({"m": [Hit("x", 0.8, "", None), Hit("z", 0.5, "text z")]})
        hits = hybrid_of(a, b).neighbours("m", 10)
        self.assertEqual([(h.ref, h.score) for h in hits], [("x", 0.8), ("y", 0.7), ("z", 0.5)])
        by = {h.ref: h for h in hits}
        self.assertEqual((by["x"].text, by["x"].date), ("text x", "2025-01-01"))  # filled from the other child

    def test_limit_applies_after_the_merge_and_the_memory_itself_never_comes_back(self):
        a = NeighbourChild({"m": [Hit("m", 1.0), Hit("x", 0.6)]})
        b = NeighbourChild({"m": [Hit("y", 0.9), Hit("z", 0.8)]})
        be = hybrid_of(a, b)
        self.assertEqual([h.ref for h in be.neighbours("m", 2)], ["y", "z"])
        self.assertEqual([h.ref for h in be.neighbours("m", 10)], ["y", "z", "x"])
        self.assertEqual(be.neighbours("m", 0), [])

    def test_a_child_without_the_method_is_not_asked_and_one_that_fails_is_skipped(self):
        plain = ScriptedChild()
        broken = NeighbourChild(fail=True)
        good = NeighbourChild({"m": [Hit("x", 0.7)]})
        self.assertEqual([h.ref for h in hybrid_of(plain, broken, good).neighbours("m", 5)], ["x"])
        self.assertEqual(len(broken.calls), 1)

    def test_no_capable_child_gives_nothing(self):
        be = hybrid_of(ScriptedChild(), ScriptedChild())
        self.assertEqual(be.neighbours("m", 5), [])
        self.assertEqual(hybrid_of(NeighbourChild(fail=True)).neighbours("m", 5), [])

    def test_an_unknown_ref_gives_nothing_and_scores_stay_in_range(self):
        child = NeighbourChild({"m": [Hit("x", 1.7), Hit("y", -0.2)]})
        be = hybrid_of(child)
        self.assertEqual(be.neighbours("other", 5), [])
        self.assertEqual([(h.ref, h.score) for h in be.neighbours("m", 5)], [("x", 1.0), ("y", 0.0)])

    def test_timeout_and_ctl_reach_the_children_that_take_them(self):
        child = NeighbourChild({"m": [Hit("x", 0.7)]})
        ctl = FakeCtl()
        hybrid_of(child).neighbours("m", 3, timeout=2.0, ctl=ctl)
        ref, limit, timeout, got = child.calls[0]
        self.assertEqual((ref, limit), ("m", 3))
        self.assertLessEqual(timeout, 2.0)
        self.assertIs(got, ctl)


class HybridRealNeighboursTest(HybridCase):
    def test_the_embedding_child_answers_and_the_keyword_child_is_left_out(self):
        be = self.make()
        embedding = be.children[1]
        embedding.floor, embedding.ceil = -1.0, 1.0
        be.put("我喜欢喝绿茶和红茶", ref="tea")
        be.put("我喜欢喝绿茶还有乌龙茶", ref="tea2")
        be.put("周末去游泳馆游泳", ref="swim")
        hits = be.neighbours("tea", 5)
        self.assertEqual(hits[0].ref, "tea2")
        self.assertNotIn("tea", [h.ref for h in hits])
        self.assertEqual(be.neighbours("nothing", 5), [])
        self.assertFalse(hasattr(be.children[0], "neighbours"))


class GateChild(ScriptedChild):
    """A child that can wait for the test: `entered` is set when its find starts, `release` lets it go.
    A cancelled call is released too (through its own control object), as a real child would stop."""

    def __init__(self, hits=(), wait_for=None, set_on_done=None, hold=False):
        super().__init__(hits)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.wait_for, self.set_on_done, self.hold = wait_for, set_on_done, hold
        self.ctls = []
        self.cancel_seen = False

    def find(self, query, limit, timeout=None, ctl=None):
        self.asked.append((query, limit, timeout))
        self.ctls.append(ctl)
        on_cancel = getattr(ctl, "on_cancel", None)
        if callable(on_cancel):
            on_cancel(self.release.set)
        self.entered.set()
        if self.wait_for is not None:
            if not self.wait_for.wait(10):
                raise RuntimeError("the other child never started")
        if self.hold:
            self.release.wait(10)
            self.cancel_seen = bool(getattr(ctl, "cancelled", False))
        if self.set_on_done is not None:
            self.set_on_done.set()
        return list(self.hits)


def child_threads():
    return [t for t in threading.enumerate() if t.name.startswith("hybrid-")]


class HybridConcurrentTest(unittest.TestCase):
    def test_both_children_are_in_flight_at_the_same_time(self):
        a, b = GateChild([Hit("x", 0.5)]), GateChild([Hit("y", 0.5)])
        a.wait_for, b.wait_for = b.entered, a.entered  # each only answers once the other has started
        start = time.monotonic()
        hits = hybrid_of(a, b).find("q", 5, timeout=5.0)
        self.assertLess(time.monotonic() - start, 4.0)
        self.assertEqual(sorted(h.ref for h in hits), ["x", "y"])

    def test_a_slow_child_does_not_delay_the_answer_beyond_the_budget(self):
        slow, quick = GateChild([Hit("x", 0.9)], hold=True), ScriptedChild([Hit("y", 0.5)])
        be = hybrid_of(slow, quick)
        start = time.monotonic()
        hits = be.find("q", 5, timeout=0.3)
        self.assertLess(time.monotonic() - start, 3.0)
        self.assertEqual([h.ref for h in hits], ["y"])
        self.assertEqual(be.child_failures, [1, 0])
        self.assertTrue(slow.release.wait(5))  # the slow child was cancelled, so it winds down
        be.close()
        self.assertEqual(child_threads(), [])

    def test_the_result_does_not_depend_on_who_answers_first(self):
        hits_a = [Hit("x", 0.6, "tx", "2025-01-01"), Hit("y", 0.4, "ty")]
        hits_b = [Hit("x", 0.5, "", None), Hit("z", 0.7, "tz")]
        expected = [(h.ref, h.score, h.text, h.date, getattr(h, "meta", None))
                    for h in hybrid_of(ScriptedChild(hits_a), ScriptedChild(hits_b)).find("q", 5)]
        for a_first in (True, False):
            with self.subTest(a_first=a_first):
                a, b = GateChild(hits_a), GateChild(hits_b)
                late, early = (b, a) if a_first else (a, b)
                late.wait_for = threading.Event()  # the early child opens it once it is done
                early.set_on_done = late.wait_for
                got = hybrid_of(a, b).find("q", 5, timeout=5.0)
                self.assertEqual([(h.ref, h.score, h.text, h.date, getattr(h, "meta", None)) for h in got],
                                 expected)

    def test_neighbours_ask_together_and_merge_the_same_way(self):
        class Gated(NeighbourChild):
            def __init__(self, answers, gate, mine):
                super().__init__(answers)
                self.gate, self.mine = gate, mine

            def neighbours(self, ref, limit, timeout=None, ctl=None):
                self.mine.set()
                if not self.gate.wait(5):
                    raise RuntimeError("the other child never started")
                return super().neighbours(ref, limit, timeout=timeout, ctl=ctl)

        ea, eb = threading.Event(), threading.Event()
        a = Gated({"m": [Hit("x", 0.6)]}, gate=eb, mine=ea)
        b = Gated({"m": [Hit("x", 0.8), Hit("z", 0.5)]}, gate=ea, mine=eb)
        be = hybrid_of(a, b)
        self.assertEqual([(h.ref, h.score) for h in be.neighbours("m", 5, timeout=5.0)], [("x", 0.8), ("z", 0.5)])
        self.assertEqual(be.child_failures, [0, 0])
        self.assertIsNot(a.calls[0][3], b.calls[0][3])  # each child has its own control object

    def test_a_failing_child_raises_its_counter_and_the_note(self):
        from feiyu_memgate.core.pool import Task
        be = hybrid_of(ScriptedChild(fail_find=True), ScriptedChild([Hit("x", 0.7)]))
        task = Task(lambda t: None)
        self.assertEqual([h.ref for h in be.find("q", 5, ctl=task)], ["x"])
        self.assertEqual(be.child_failures, [1, 0])
        self.assertEqual(task.notes, ["child-failed"])
        be.find("q", 5, ctl=task)
        self.assertEqual(be.child_failures, [2, 0])
        self.assertEqual(task.notes, ["child-failed"])  # one word, once
        be.find("q", 5)  # a caller without ctl, or with an object that cannot note, is fine
        be.find("q", 5, ctl=FakeCtl())
        self.assertEqual(be.child_failures, [4, 0])

    def test_every_child_failing_still_raises_and_counts(self):
        be = hybrid_of(ScriptedChild(fail_find=True), ScriptedChild(fail_find=True))
        with self.assertRaises(BackendError):
            be.find("q", 5)
        self.assertEqual(be.child_failures, [1, 1])

    def test_a_failure_of_a_neighbours_child_is_counted_too(self):
        be = hybrid_of(NeighbourChild(fail=True), NeighbourChild({"m": [Hit("x", 0.7)]}))
        self.assertEqual([h.ref for h in be.neighbours("m", 5)], ["x"])
        self.assertEqual(be.child_failures, [1, 0])

    def test_cancelling_the_parent_cancels_both_children(self):
        from feiyu_memgate.core.pool import Task
        a, b = GateChild(hold=True), GateChild(hold=True)
        be = hybrid_of(a, b)
        task = Task(lambda t: None)
        caller = threading.Thread(target=lambda: be.find("q", 5, timeout=10.0, ctl=task))
        caller.start()
        self.assertTrue(a.entered.wait(5) and b.entered.wait(5))
        self.assertIsNot(a.ctls[0], b.ctls[0])
        self.assertFalse(a.ctls[0].cancelled or b.ctls[0].cancelled)
        task.cancel()
        caller.join(5)
        self.assertFalse(caller.is_alive())
        self.assertTrue(a.cancel_seen and b.cancel_seen)
        self.assertEqual(be.child_failures, [0, 0])  # the caller gave up: no child is blamed

    def test_a_cancelled_parent_cancels_children_started_after_it(self):
        from feiyu_memgate.core.pool import Task
        seen = []

        class Watching(ScriptedChild):
            def find(self, query, limit, timeout=None, ctl=None):
                seen.append(ctl.cancelled)
                return []

        task = Task(lambda t: None)
        task.cancel()
        hybrid_of(Watching(), Watching()).find("q", 5, ctl=task)
        self.assertEqual(seen, [True, True])

    def test_each_child_has_a_socket_slot_of_its_own(self):
        from feiyu_memgate.core.pool import Task

        class Sock:
            def __init__(self):
                self.shut = False

            def shutdown(self, how):
                self.shut = True

        socks = [Sock(), Sock()]
        ready = threading.Barrier(3)
        go = threading.Event()

        class Holding(ScriptedChild):
            def __init__(self, sock):
                super().__init__()
                self.sock = sock

            def find(self, query, limit, timeout=None, ctl=None):
                ctl.attach(self.sock)
                ready.wait(5)
                go.wait(5)
                ctl.detach()
                return []

        be = hybrid_of(Holding(socks[0]), Holding(socks[1]))
        task = Task(lambda t: None)
        caller = threading.Thread(target=lambda: be.find("q", 5, timeout=10.0, ctl=task))
        caller.start()
        ready.wait(5)  # both children hold a socket now
        task.cancel()
        self.assertEqual([s.shut for s in socks], [True, True])  # one shared slot would have shut only one
        go.set()
        caller.join(5)

    def test_no_thread_is_left_after_a_find_or_after_close(self):
        before = set(threading.enumerate())
        a, b = GateChild([Hit("x", 0.5)]), GateChild(hold=True)
        be = hybrid_of(a, b)
        be.find("q", 3, timeout=0.2)  # b is cancelled by the budget and winds down alone
        self.assertTrue(b.release.wait(5))
        be.close()
        self.assertEqual(child_threads(), [])
        self.assertEqual(set(threading.enumerate()) - before, set())
        be.close()  # twice is fine

    def test_a_find_after_close_fails_without_starting_threads(self):
        be = hybrid_of(ScriptedChild([Hit("x", 0.5)]), ScriptedChild([Hit("y", 0.5)]))
        be.close()
        with self.assertRaises(BackendError):
            be.find("q", 3)
        self.assertEqual(child_threads(), [])


class HybridFailureReachesTheAccessLogTest(unittest.TestCase):
    def gateway(self, be):
        from feiyu_memgate.gateway.app import Gateway
        from feiyu_memgate.model import ClientPolicy
        gw = Gateway({"private": be}, {"owner": ClientPolicy("owner", recall=("private",))}, {"owner": TOKEN},
                     log=lambda line: None)
        self.addCleanup(gw.close)
        return gw

    def test_the_note_reaches_the_reply_and_recall_still_answers(self):
        be = hybrid_of(ScriptedChild(fail_find=True), ScriptedChild([Hit("x", 0.95, "the only text", "2025-01-01")]),
                       writable=False)
        reply = self.gateway(be).recall("owner", {"query": "anything at all"})
        self.assertEqual(reply.status, 200, reply.body)
        self.assertEqual([r["text"] for r in reply.body["results"]], ["the only text"])
        self.assertIn("child-failed", reply.note.split(","))
        self.assertEqual(be.child_failures, [1, 0])
        self.assertNotIn("anything", reply.note)

    def test_a_healthy_search_has_no_note(self):
        be = hybrid_of(ScriptedChild([Hit("x", 0.95, "t")]), ScriptedChild(), writable=False)
        reply = self.gateway(be).recall("owner", {"query": "q"})
        self.assertEqual(reply.status, 200)
        self.assertNotIn("child-failed", reply.note)


if __name__ == "__main__":
    unittest.main()
