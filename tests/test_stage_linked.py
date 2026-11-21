"""The linked recall stage: after the score lines, the memories linked to the first delivered ones come along."""
import os
import tempfile
import time
import unittest

from feiyu_memgate import config, registry
from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.client import LocalClient
from feiyu_memgate.core import stages
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.forgetting.stage import Freshness
from feiyu_memgate.model import Candidate, Hit

from .test_backend_embedding import FakeEmbeddingServer
from .test_forgetting_engine import T0

FAKE_TOKEN = "linked-stage-test-fake-token-00000000"


class LinkStore(MemoryBackend):
    """A store whose answers the test decides: `found` is what find() returns, `links` what neighbours() returns
    for each ref (a list of Hit), `texts` what read() gives."""

    writable = False

    def __init__(self, found=(), links=None, texts=None, fail=False, delay=0.0):
        self.found = list(found)
        self.links = dict(links or {})
        self.texts = dict(texts or {})
        self.fail, self.delay = fail, delay
        self.calls = []

    def find(self, query, limit, timeout=None, ctl=None):
        return list(self.found)

    def neighbours(self, ref, limit, timeout=None, ctl=None):
        self.calls.append((ref, limit))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return list(self.links.get(ref, []))[:limit]

    def read(self, ref, timeout=None, ctl=None):
        return self.texts[ref]


def hit(ref, score, text=None):
    return Hit(ref=ref, score=score, text="text of %s" % ref if text is None else text)


class Case(unittest.TestCase):
    def setUp(self):
        self.pool = Pool()
        self.addCleanup(self.pool.close)

    def run_recall(self, stores, stage_list, limit=5, seconds=3.0, **ctx_kw):
        notes = []
        ctx = RecallContext("q", limit, pool=self.pool, notes=notes,
                            deadline=time.monotonic() + seconds, **ctx_kw)
        out = recall(stores, stage_list, ctx)
        return out, ctx, notes

    @staticmethod
    def ids(out):
        return [r["id"] for r in out["results"]]


class LinkedStageTest(Case):
    def store(self, **kw):
        found = [hit("a", 0.90), hit("b", 0.80)]
        links = {"a": [hit("c", 0.80), hit("d", 0.70), hit("e", 0.65)], "b": [hit("f", 0.95), hit("c", 0.85)]}
        return LinkStore(found=kw.pop("found", found), links=kw.pop("links", links), **kw)

    def go(self, store=None, limit=5, **options):
        store = store or self.store()
        out, ctx, notes = self.run_recall({"s": store}, [stages.Linked(**options)], limit=limit)
        return out, store, ctx, notes

    # -- what is added
    def test_adds_at_most_two_after_the_first_hop_best_link_first(self):
        out, store, _, notes = self.go()
        # from a: c 0.80, d 0.70, e 0.65; from b: f 0.95, c 0.85 (c counts once, with its higher score)
        self.assertEqual(self.ids(out), ["a", "b", "f", "c"])
        self.assertEqual(notes, [])

    def test_only_while_the_total_stays_within_the_limit(self):
        self.assertEqual(self.ids(self.go(limit=3)[0]), ["a", "b", "f"])
        self.assertEqual(self.ids(self.go(limit=4)[0]), ["a", "b", "f", "c"])
        out, store, _, _ = self.go(limit=2)
        self.assertEqual(self.ids(out), ["a", "b"])
        self.assertEqual(store.calls, [])  # no room: nothing is even asked
        self.assertEqual(self.ids(self.go(limit=1)[0]), ["a"])

    def test_limit_counts_the_candidates_of_every_store(self):
        one, two = self.store(), LinkStore(found=[hit("x", 0.7), hit("y", 0.6)])
        out, _, _ = self.run_recall({"one": one, "two": two}, [stages.Linked()], limit=5)
        self.assertEqual(len(out["results"]), 5)  # 4 first-hop + 1 more: room is 1
        self.assertEqual(self.ids(out)[:4], ["a", "b", "x", "y"])

    def test_top_decides_how_many_are_followed(self):
        out, store, _, _ = self.go(top=1)
        self.assertEqual(self.ids(out), ["a", "b", "c", "d"])
        self.assertEqual([call[0] for call in store.calls], ["a"])
        out, store, _, _ = self.go(top=2)
        self.assertEqual([call[0] for call in store.calls], ["a", "b"])
        out, store, _, _ = self.go(top=5)  # only two candidates exist
        self.assertEqual([call[0] for call in store.calls], ["a", "b"])

    def test_the_first_candidates_are_the_best_scored_not_the_first_listed(self):
        store = self.store(found=[hit("b", 0.80), hit("a", 0.90)])  # listed the other way round
        out, store, _, _ = self.go(store, top=1)
        self.assertEqual([call[0] for call in store.calls], ["a"])

    def test_max_added(self):
        self.assertEqual(self.ids(self.go(max_added=1)[0]), ["a", "b", "f"])
        self.assertEqual(self.ids(self.go(max_added=3)[0]), ["a", "b", "f", "c", "d"])
        out, store, _, _ = self.go(max_added=0)  # switched off: nothing is asked
        self.assertEqual(self.ids(out), ["a", "b"])
        self.assertEqual(store.calls, [])

    def test_minimum_is_a_line_that_a_score_equal_to_it_passes(self):
        self.assertEqual(self.ids(self.go(minimum=0.85)[0]), ["a", "b", "f", "c"])
        self.assertEqual(self.ids(self.go(minimum=0.86)[0]), ["a", "b", "f"])
        self.assertEqual(self.ids(self.go(minimum=0.951)[0]), ["a", "b"])
        self.assertEqual(self.ids(self.go(minimum=0.95)[0]), ["a", "b", "f"])
        self.assertEqual(self.ids(self.go(max_added=5, minimum=0.0)[0]), ["a", "b", "f", "c", "d"])
        self.assertEqual(stages.Linked().minimum, 0.60)
        self.assertEqual(self.ids(self.go(max_added=5, minimum=0.6)[0]), ["a", "b", "f", "c", "d"])  # limit 5
        self.assertEqual(self.ids(self.go(max_added=5, minimum=0.6, limit=9)[0]), ["a", "b", "f", "c", "d", "e"])

    def test_a_memory_that_is_a_candidate_already_is_never_added(self):
        store = self.store(links={"a": [hit("b", 0.99), hit("a", 1.0), hit("c", 0.7)], "b": [hit("a", 0.99)]})
        out, _, _, _ = self.go(store)
        self.assertEqual(self.ids(out), ["a", "b", "c"])
        self.assertEqual(len(set(self.ids(out))), len(out["results"]))

    def test_a_candidate_beyond_top_is_not_added_either(self):
        store = LinkStore(found=[hit("a", 0.9), hit("b", 0.8), hit("c", 0.7)],
                          links={"a": [hit("c", 0.99), hit("d", 0.7)]})
        out, _, _ = self.run_recall({"s": store}, [stages.Linked(top=1)])
        self.assertEqual(self.ids(out), ["a", "b", "c", "d"])  # c was a first-hop candidate: once, in its place

    def test_the_same_ref_in_another_store_is_another_memory(self):
        one = LinkStore(found=[hit("a", 0.9)], links={"a": [hit("same", 0.8)]})
        two = LinkStore(found=[hit("same", 0.5)])
        out, _, _ = self.run_recall({"one": one, "two": two}, [stages.Linked()])
        self.assertEqual([(r["source"], r["id"]) for r in out["results"]],
                         [("one", "a"), ("two", "same"), ("one", "same")])

    # -- what the additions look like
    def test_first_hop_order_is_kept_and_additions_come_last(self):
        out, _, ctx, _ = self.go(max_added=2)
        scores = [r["score"] for r in out["results"]]
        self.assertEqual(self.ids(out)[:2], ["a", "b"])
        self.assertEqual(scores[:2], [0.90, 0.80])
        self.assertLess(max(scores[2:]), min(scores[:2]))
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_an_addition_scores_parent_times_similarity_and_never_above_the_first_hop(self):
        store = LinkStore(found=[hit("a", 0.9), hit("b", 0.8)], links={"a": [hit("c", 0.5), hit("d", 0.9)]})
        out, _, _ = self.run_recall({"s": store}, [stages.Linked(minimum=0.0)])
        by = {r["id"]: r["score"] for r in out["results"]}
        self.assertEqual(self.ids(out), ["a", "b", "d", "c"])
        self.assertLess(by["d"], 0.8)  # 0.9 x 0.9 = 0.81 is capped below the lowest first-hop score
        self.assertGreater(by["d"], 0.79999)
        self.assertAlmostEqual(by["c"], 0.45)  # 0.9 x 0.5, under the cap
        store = LinkStore(found=[hit("a", 0.9), hit("b", 0.8)], links={"a": [hit("c", 0.5)], "b": [hit("d", 0.5)]})
        out, _, _ = self.run_recall({"s": store}, [stages.Linked(minimum=0.0)])
        self.assertEqual(self.ids(out), ["a", "b", "c", "d"])  # 0.45 then 0.40: the chosen order holds

    def test_the_order_chosen_holds_when_a_later_one_would_score_higher(self):
        store = LinkStore(found=[hit("a", 0.95), hit("b", 0.60)],
                          links={"b": [hit("c", 0.90)], "a": [hit("d", 0.85)]})
        out, _, _ = self.run_recall({"s": store}, [stages.Linked(minimum=0.0, max_added=2)])
        self.assertEqual(self.ids(out), ["a", "b", "c", "d"])  # c first (0.90 > 0.85) although 0.60 x 0.90 < 0.95 x 0.85

    def test_an_addition_keeps_source_store_meta_text_and_date(self):
        store = LinkStore(found=[hit("a", 0.9)],
                          links={"a": [Hit("c", 0.8, "text c", "2025-02-03", {"cosine": 0.77})]})
        out, ctx, _ = self.run_recall({"mine": store}, [stages.Linked()])
        added = ctx.accepted[1]
        self.assertEqual((added.source, added.store, added.ref), ("mine", store, "c"))
        self.assertEqual((added.text, added.date), ("text c", "2025-02-03"))
        self.assertEqual(added.meta["linked_from"], "a")
        self.assertEqual(added.meta["cosine"], 0.77)
        self.assertNotIn("linked_from", ctx.accepted[0].meta)
        self.assertEqual(added.meta["similarity"], added.score)

    def test_linked_from_is_not_in_the_answer(self):
        out, _, _, _ = self.go()
        for item in out["results"]:
            self.assertEqual(set(item), {"id", "source", "text", "score"})
        self.assertNotIn("linked", str(out))

    def test_the_memory_it_came_from_is_the_one_that_gave_the_higher_score(self):
        _, _, ctx, _ = self.go(max_added=5, minimum=0.0)
        by = {c.ref: c for c in ctx.accepted}
        self.assertEqual(by["c"].meta["linked_from"], "b")  # 0.85 from b beats 0.80 from a
        self.assertEqual(by["d"].meta["linked_from"], "a")
        self.assertEqual(by["f"].meta["linked_from"], "b")

    # -- quiet, stores without the method, failures
    def test_nothing_added_when_the_first_hop_is_empty(self):
        store = self.store(found=[])
        out, store, _, notes = self.go(store)
        self.assertEqual(out["results"], [])
        self.assertEqual(store.calls, [])
        self.assertEqual(notes, [])

    def test_a_store_without_the_method_is_left_alone_without_a_note(self):
        store = InMemoryBackend(memories=["i like green tea", "i like jam"])
        out, _, notes = self.run_recall({"m": store}, [stages.Linked()])
        plain, _, _ = self.run_recall({"m": store}, [])
        self.assertEqual(self.ids(out), self.ids(plain))
        self.assertEqual(notes, [])

    def test_only_stores_that_have_it_are_asked(self):
        plain = InMemoryBackend(memories=["q"])
        plain.find = lambda query, limit, timeout=None, ctl=None: [hit("p", 0.9)]
        capable = LinkStore(found=[hit("a", 0.8)], links={"a": [hit("c", 0.9)]})
        out, _, notes = self.run_recall({"plain": plain, "capable": capable}, [stages.Linked()])
        self.assertEqual([(r["source"], r["id"]) for r in out["results"]],
                         [("plain", "p"), ("capable", "a"), ("capable", "c")])
        self.assertEqual(notes, [])

    def test_an_error_in_neighbours_adds_nothing_and_leaves_one_note(self):
        out, store, _, notes = self.go(self.store(fail=True))
        self.assertEqual(self.ids(out), ["a", "b"])
        self.assertEqual(notes, ["linked-failed"])
        self.assertEqual(len(store.calls), 2)  # the second memory was still tried

    def test_one_failing_store_does_not_stop_the_others(self):
        broken = LinkStore(found=[hit("x", 0.9)], fail=True)
        good = LinkStore(found=[hit("a", 0.8)], links={"a": [hit("c", 0.9)]})
        out, _, notes = self.run_recall({"broken": broken, "good": good}, [stages.Linked()])
        self.assertEqual(self.ids(out), ["x", "a", "c"])
        self.assertEqual(notes, ["linked-failed"])

    def test_a_bad_answer_is_nothing_added(self):
        for answer in (None, "text", [("a", 0.5)], [Hit("c", float("nan"))]):
            store = LinkStore(found=[hit("a", 0.9)])
            store.neighbours = lambda ref, limit, timeout=None, ctl=None, answer=answer: answer
            out, _, notes = self.run_recall({"s": store}, [stages.Linked()])
            self.assertEqual(self.ids(out), ["a"], answer)
            self.assertEqual(notes, ["linked-failed"], answer)

    def test_a_lookup_that_hangs_costs_bounded_time_and_keeps_the_first_hop(self):
        store = self.store(delay=5.0)
        start = time.monotonic()
        out, _, notes = self.run_recall({"s": store}, [stages.Linked(), stages.FillText()], seconds=1.0)
        took = time.monotonic() - start
        self.assertEqual(self.ids(out), ["a", "b"])
        self.assertIn("linked-failed", notes)
        self.assertLess(took, 1.0)  # the stage waited for at most half of what was left, then went on

    def test_a_prepare_that_would_raise_never_fails_the_recall(self):
        class Odd(LinkStore):
            @property
            def neighbours(self):
                raise RuntimeError("no")

        store = Odd(found=[hit("a", 0.9)])
        out, _, notes = self.run_recall({"s": store}, [stages.Linked()])
        self.assertEqual(self.ids(out), ["a"])
        self.assertEqual(notes, ["linked-failed"])

    def test_no_time_left_adds_nothing(self):
        store = self.store()
        ctx = RecallContext("q", 5, pool=self.pool, deadline=time.monotonic() - 1.0)
        candidates = [Candidate(score=0.9, source="s", store=store, ref="a", text="t")]
        self.assertEqual(stages.Linked().prepare(candidates, ctx), candidates)
        self.assertEqual(store.calls, [])

    # -- later stages
    def test_additions_are_read_when_their_text_is_missing(self):
        store = LinkStore(found=[hit("a", 0.9)], links={"a": [Hit("c", 0.8, "")]}, texts={"c": "read text of c"})
        out, _, _ = self.run_recall({"s": store}, [stages.Linked(), stages.FillText()])
        self.assertEqual([r["text"] for r in out["results"]], ["text of a", "read text of c"])

    def test_redaction_applies_to_additions(self):
        store = LinkStore(found=[hit("a", 0.9, "plain a")],
                          links={"a": [hit("c", 0.8, "mail me at SECRET-ADDR now"), hit("d", 0.7, "SECRET-ADDR")]})
        def redact(text):
            return text.replace("SECRET-ADDR", "[hidden]")

        out, _, _ = self.run_recall({"s": store}, [stages.Linked(), stages.Redact()], redact=redact, public=True)
        self.assertEqual([r["text"] for r in out["results"]], ["plain a", "mail me at [hidden] now", "[hidden]"])
        # what redaction empties is dropped, the same as for any candidate
        out, _, _ = self.run_recall({"s": store}, [stages.Linked(), stages.Redact()], redact=lambda t: "")
        self.assertEqual(out["results"], [])

    def test_the_budget_applies_to_additions(self):
        store = LinkStore(found=[hit("a", 0.9, "x" * 10)], links={"a": [hit("c", 0.8, "y" * 10), hit("d", 0.7, "z" * 3)]})
        out, _, _ = self.run_recall({"s": store}, [stages.Linked(), stages.Budget(max_chars=14)])
        self.assertEqual([r["id"] for r in out["results"]], ["a", "d"])  # c does not fit, the shorter d still does
        self.assertEqual(out["more"], 1)

    def test_dedupe_and_oversize_apply_to_additions(self):
        store = LinkStore(found=[hit("a", 0.9, "i like green tea a lot")],
                          links={"a": [hit("c", 0.8, "i like green tea a lot"), hit("d", 0.7, "b" * 50)]})
        out, _, notes = self.run_recall({"s": store}, [stages.Linked(), stages.DropOversize(max_chars=30),
                                                       stages.Dedupe()])
        self.assertEqual(self.ids(out), ["a"])

    # -- the usage ledger of the forgetting curve
    def test_an_addition_counts_as_delivered_for_the_ledger_under_the_rule_of_credit(self):
        store = LinkStore(found=[hit("a", 0.9)], links={"a": [hit("c", 0.8)]})
        self.assertEqual(self.credit(store, "all"), {"a", "c"})
        self.assertEqual(self.credit(store, "none"), set())
        # "top": the linked memory scores 0.9 x 0.8 = 0.72, under 0.9 x the best similarity 0.9: not credited
        self.assertEqual(self.credit(store, "top"), {"a"})
        near = LinkStore(found=[hit("a", 0.9)], links={"a": [hit("c", 1.0)]})
        self.assertEqual(self.credit(near, "top"), {"a", "c"})  # 0.9 is within 0.9 x 0.9 of the best one

    def credit(self, store, rule):
        from feiyu_memgate.forgetting.engine import Forgetting
        from feiyu_memgate.forgetting.archive import Archive
        from feiyu_memgate.forgetting.curve import Ebbinghaus
        from feiyu_memgate.forgetting.ledger import Ledger
        engine = Forgetting(Ledger(":memory:"), Archive(":memory:"), Ebbinghaus(half_life_days=60),
                            stores=("private",), max_forget_fraction=1.0, wall=lambda: T0)
        self.addCleanup(engine.close)
        stage_list = [stages.MinScore(0.5), Freshness(engine, credit=rule), stages.Linked(minimum=0.0)]
        self.run_recall({"private": store}, stage_list)
        got = engine.ledger.get_many("private", ["a", "c"])
        return {ref for ref, entry in got.items() if entry.uses}


class OptionsTest(unittest.TestCase):
    def test_defaults_and_registration(self):
        stage = registry.create("recall_stage", "linked")
        self.assertIsInstance(stage, stages.Linked)
        self.assertEqual((stage.name, stage.top, stage.max_added, stage.minimum), ("linked", 2, 2, 0.60))
        self.assertFalse(stage.cheap)

    def test_the_ranges(self):
        for options in ({"top": 1}, {"top": 5}, {"max_added": 0}, {"max_added": 5}, {"minimum": 0},
                        {"minimum": 1}, {"minimum": 0.6}):
            stages.Linked(**options)
        for options in ({"top": 0}, {"top": 6}, {"top": 1.5}, {"top": True}, {"top": "2"}, {"max_added": -1},
                        {"max_added": 6}, {"max_added": 2.0}, {"max_added": False}, {"minimum": -0.1},
                        {"minimum": 1.1}, {"minimum": "0.6"}, {"minimum": True}, {"minimum": float("nan")}):
            with self.assertRaises(ConfigError, msg=options):
                stages.Linked(**options)

    def test_a_configuration_with_bad_options_is_refused(self):
        with self.assertRaises(ConfigError):
            self.build(["min_score", {"type": "linked", "top": 9}])

    def build(self, stage_list):
        cfg = {"stores": {"private": {"type": "inmemory"}},
               "clients": {"owner": {"token_env": "GATEWAY_TOKEN_OWNER", "recall": ["private"], "write": "private"}}}
        if stage_list is not None:
            cfg["recall"] = {"stages": stage_list}
        gateway = config.build_gateway(cfg, env={"GATEWAY_TOKEN_OWNER": FAKE_TOKEN}, log=lambda line: None)
        self.addCleanup(gateway.close)
        return gateway

    def test_the_default_stages_have_it_between_newer_first_and_fill_text(self):
        names = list(stages.DEFAULT_STAGES)
        self.assertEqual(names, ["min_score", "relative_score", "newer_first", "linked", "fill_text", "drop_oversize",
                                 "redact", "dedupe", "budget"])
        gateway = self.build(None)
        self.assertEqual([s.name for s in gateway.stages], names)
        self.assertEqual(stages.misplaced(gateway.stages), [])

    def test_placement_is_checked(self):
        def order(*names):
            return [registry.create("recall_stage", name) for name in names]

        self.assertEqual(stages.misplaced(order("min_score", "relative_score", "newer_first", "linked")), [])
        self.assertEqual(stages.misplaced(order("linked", "min_score")), ["linked"])
        self.assertEqual(stages.misplaced(order("min_score", "linked", "relative_score")), ["linked"])
        self.assertEqual(stages.misplaced(order("min_score", "relative_score", "linked", "newer_first")), ["linked"])
        self.assertEqual(stages.misplaced(order("linked", "fill_text")), [])  # nothing to follow: fine
        self.assertEqual(stages.misplaced(order("newer_first", "linked", "min_score")), ["newer_first", "linked"])
        self.assertEqual(stages.Linked.after, ("min_score", "relative_score", "newer_first"))

    def test_a_custom_list_without_it_is_what_it_was(self):
        store = LinkStore(found=[hit("a", 0.9)], links={"a": [hit("c", 0.9)]})
        for names in (["min_score", "relative_score", "fill_text"], ["fill_text"]):
            gateway = self.build(names)
            self.assertNotIn("linked", [s.name for s in gateway.stages])
        pool = Pool()
        self.addCleanup(pool.close)
        out = recall({"s": store}, [registry.create("recall_stage", name) for name in ("min_score", "fill_text")],
                     RecallContext("q", 5, pool=pool))
        self.assertEqual([r["id"] for r in out["results"]], ["a"])
        self.assertEqual(store.calls, [])


class EndToEndTest(unittest.TestCase):
    """The gateway with the embedding backend and the fake embeddings server: a remark close to one memory brings
    the memory that is close to that one. Floor 0 and ceil 1 make the score the cosine of the fake vectors."""

    NEAR = "主人喜欢吃冰淇淋，尤其是巧克力味的冰淇淋"
    LINKED = "巧克力味的冰淇淋掉在地上了"  # cosine 0.47 to NEAR, 0.17 to the remark below
    OTHERS = ["周末去游泳馆游泳", "写代码调试程序", "主人养了一只黄色的狗"]
    REMARK = "我喜欢吃冰淇淋"

    def setUp(self):
        self.server = FakeEmbeddingServer()
        self.addCleanup(self.server.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def cfg(self, stage_list=None, backend="embedding"):
        embedding = {"type": "embedding", "path": os.path.join(self.tmp.name, "v.db"), "url": self.server.url,
                     "model": "fake-model", "floor": 0.0, "ceil": 1.0}
        store = embedding if backend == "embedding" else {"type": "hybrid", "children": [
            {"type": "sqlite", "path": ":memory:"}, embedding]}
        cfg = {"stores": {"private": store},
               "clients": {"owner": {"token_env": "GATEWAY_TOKEN_OWNER", "recall": ["private"], "write": "private",
                                     "rate_limit": 100000}}}
        if stage_list is not None:
            cfg["recall"] = {"stages": stage_list}
        return cfg

    def gateway(self, stage_list=None, backend="embedding"):
        gateway = config.build_gateway(self.cfg(stage_list, backend), env={"GATEWAY_TOKEN_OWNER": FAKE_TOKEN},
                                       log=lambda line: None)
        self.addCleanup(gateway.close)
        store = gateway.stores["private"]
        for ref, text in [("near", self.NEAR), ("linked", self.LINKED)] + [("o%d" % i, t) for i, t in
                                                                           enumerate(self.OTHERS)]:
            store.put(text, ref=ref, date="2026-01-01")
        return gateway

    def ask(self, gateway):
        reply = gateway.recall("owner", {"query": self.REMARK})
        self.assertEqual(reply.status, 200, reply.body)
        return reply.body["results"]

    LINES = [{"type": "min_score", "value": 0.4}, "relative_score", "newer_first"]

    def test_a_close_neighbour_comes_with_the_memory_a_remark_is_close_to(self):
        without = self.ask(self.gateway(self.LINES + ["fill_text", "redact", "dedupe", "budget"]))
        self.assertEqual([r["id"] for r in without], ["near"])
        linked = self.ask(self.gateway(self.LINES + [{"type": "linked", "minimum": 0.4}, "fill_text", "redact",
                                                     "dedupe", "budget"]))
        self.assertEqual([r["id"] for r in linked], ["near", "linked"])
        self.assertEqual(linked[1]["text"], self.LINKED)
        self.assertLess(linked[1]["score"], linked[0]["score"])
        self.assertEqual(set(linked[1]), {"id", "source", "text", "score", "date"})  # no new field in the answer

    def test_hybrid_store_too(self):
        stage_list = self.LINES + [{"type": "linked", "minimum": 0.4}, "fill_text", "redact"]
        gateway = self.gateway(stage_list, backend="hybrid")
        # the keyword child shares the wording of the remark with both memories: use the vector line only
        results = self.ask(gateway)
        self.assertEqual(results[0]["id"], "near")
        self.assertIn("linked", [r["id"] for r in results])

    def test_the_default_minimum_is_the_measured_one(self):
        # the default stages: cosine 0.47 is under the default line 0.60, so nothing is added
        gateway = self.gateway()
        self.assertEqual(stages.DEFAULT_STAGES.index("linked"), 3)
        self.assertIn("linked", [s.name for s in gateway.stages])
        self.assertNotIn("linked", [r["id"] for r in self.ask(gateway)])
        # and max_added = 0 or a list without the stage is the old behaviour at any minimum
        off = self.ask(self.gateway(self.LINES + [{"type": "linked", "minimum": 0.0, "max_added": 0}, "fill_text"]))
        self.assertEqual([r["id"] for r in off], ["near"])

    def test_no_embeddings_request_for_the_link_lookup(self):
        gateway = self.gateway(self.LINES + [{"type": "linked", "minimum": 0.4}, "fill_text"])
        before = len(self.server.requests)
        self.ask(gateway)
        self.assertEqual(len(self.server.requests) - before, 1)  # the remark only

    def test_a_quiet_turn_stays_quiet(self):
        gateway = self.gateway(self.LINES + [{"type": "linked", "minimum": 0.0}, "fill_text"])
        reply = gateway.recall("owner", {"query": "xyz qqq 12345"})
        self.assertEqual(reply.body["results"], [])

    def test_through_the_local_client(self):
        cfg = self.cfg(self.LINES + [{"type": "linked", "minimum": 0.4}, "fill_text"])
        client = LocalClient.from_config(cfg, client="owner", env={}, log=lambda line: None)
        self.addCleanup(client.close)
        store = client.gateway.stores["private"]
        for ref, text in [("near", self.NEAR), ("linked", self.LINKED), ("o", self.OTHERS[0])]:
            store.put(text, ref=ref, date="2026-01-01")
        detail = client.recall_detail(self.REMARK)
        self.assertEqual([r["id"] for r in detail["results"]], ["near", "linked"])


if __name__ == "__main__":
    unittest.main()
