import threading
import unittest

from feiyu_memgate import registry
from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.core import stages
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import DEFER, DROP, KEEP, RecallContext, RecallStage, StageError, piece_name, recall
from feiyu_memgate.errors import BackendError
from feiyu_memgate.model import Candidate, Hit


class FakeStore(MemoryBackend):
    """A backend that answers from a list and counts its reads."""

    def __init__(self, hits=(), contents=None, error=False, gate=None):
        self.hits = list(hits)
        self.contents = dict(contents or {})
        self.error = error
        self.gate = gate  # an Event the find waits for (a slow store)
        self.reads = []
        self.queries = []

    def find(self, query, limit, timeout=None, ctl=None):
        self.queries.append((query, limit))
        if self.gate is not None:
            self.gate.wait(5)
        if self.error:
            raise BackendError("down")
        return list(self.hits)

    def read(self, ref, timeout=None, ctl=None):
        self.reads.append(ref)
        if ref not in self.contents:
            raise BackendError("gone")
        return self.contents[ref]


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class StageTestCase(unittest.TestCase):
    def setUp(self):
        self.pool = Pool()
        self.addCleanup(self.pool.close)
        self.clock = Clock()

    def ctx(self, query="q", limit=5, **kw):
        kw.setdefault("clock", self.clock)
        return RecallContext(query, limit, pool=self.pool, **kw)

    def cand(self, text="some text", score=0.9, source="a", store=None, ref="r", **kw):
        return Candidate(score=score, source=source, store=store, ref=ref, text=text, **kw)


class KeywordStore:
    """Stands for a backend that declares its own threshold, as the keyword backends do."""
    default_min_score = 0.3


class MinScoreTests(StageTestCase):
    def test_drops_under_the_threshold_and_keeps_the_threshold_itself(self):
        out = stages.MinScore().prepare([self.cand(score=0.49), self.cand(score=0.5), self.cand(score=0.9)], self.ctx())
        self.assertEqual([c.score for c in out], [0.5, 0.9])

    def test_without_a_value_each_store_uses_its_own_default(self):
        keyword = KeywordStore()
        cands = [self.cand(score=0.35, source="kw", store=keyword), self.cand(score=0.35, source="vec"),
                 self.cand(score=0.29, source="kw", store=keyword)]
        out = stages.MinScore().prepare(cands, self.ctx())
        self.assertEqual([(c.source, c.score) for c in out], [("kw", 0.35)])
        self.assertIsNone(stages.MinScore().value)

    def test_value_and_per_store_win_over_the_store_default(self):
        keyword = KeywordStore()
        cand = self.cand(score=0.35, source="kw", store=keyword)
        self.assertEqual(stages.MinScore(value=0.5).prepare([cand], self.ctx()), [])
        self.assertEqual(stages.MinScore(value=0.5, per_store={"kw": 0.2}).prepare([cand], self.ctx()), [cand])
        self.assertEqual(stages.MinScore(per_store={"kw": 0.4}).prepare([cand], self.ctx()), [])

    def test_value_option(self):
        out = stages.MinScore(value=0.8).prepare([self.cand(score=0.7), self.cand(score=0.8)], self.ctx())
        self.assertEqual([c.score for c in out], [0.8])

    def test_per_store_threshold_overrides_the_value_for_that_store_only(self):
        stage = stages.MinScore(value=0.5, per_store={"kw": 0.1, "strict": 0.95})
        cands = [self.cand(score=0.2, source="kw"), self.cand(score=0.2, source="a"),
                 self.cand(score=0.9, source="strict"), self.cand(score=0.96, source="strict")]
        out = stage.prepare(cands, self.ctx())
        self.assertEqual([(c.source, c.score) for c in out], [("kw", 0.2), ("strict", 0.96)])

    def test_admit_keeps(self):
        self.assertEqual(stages.MinScore().admit(self.cand(score=0.0), self.ctx()), KEEP)


class SourceWeightTests(StageTestCase):
    def test_multiplies_the_score_and_keeps_the_similarity(self):
        stage = stages.SourceWeight(weights={"a": 0.5, "b": 2.0})
        out = stage.prepare([self.cand(score=0.8, source="a"), self.cand(score=0.4, source="b"),
                             self.cand(score=0.6, source="c")], self.ctx())
        self.assertEqual([round(c.score, 6) for c in out], [0.4, 0.8, 0.6])
        self.assertEqual([c.meta["similarity"] for c in out], [0.8, 0.4, 0.6])

    def test_default_weight_for_unnamed_stores(self):
        out = stages.SourceWeight(weights={"a": 1.0}, default=0.5).prepare([self.cand(score=0.8, source="z")], self.ctx())
        self.assertEqual(out[0].score, 0.4)

    def test_similarity_is_kept_once_when_applied_twice(self):
        c = self.cand(score=0.8, source="a")
        stage = stages.SourceWeight(weights={"a": 0.5})
        stage.prepare([c], self.ctx())
        stage.prepare([c], self.ctx())
        self.assertEqual(c.meta["similarity"], 0.8)

    def test_weights_change_the_ranking_across_stores(self):
        high = FakeStore([Hit("h", 0.9, "from the weak store")])
        low = FakeStore([Hit("l", 0.8, "from the favoured store")])
        out = recall({"weak": high, "fav": low},
                     [stages.SourceWeight(weights={"weak": 0.5, "fav": 1.0})], self.ctx())
        self.assertEqual([r["source"] for r in out["results"]], ["fav", "weak"])

    def test_min_score_before_weight_looks_at_similarity_only(self):
        store = FakeStore([Hit("x", 0.6, "text")])
        out = recall({"s": store}, [stages.MinScore(0.5), stages.SourceWeight(weights={"s": 0.1})], self.ctx())
        self.assertEqual(len(out["results"]), 1)
        out = recall({"s": store}, [stages.SourceWeight(weights={"s": 0.1}), stages.MinScore(0.5)], self.ctx())
        self.assertEqual(out["results"], [])


class DropMarkerTests(StageTestCase):
    def test_marker_constant(self):
        self.assertEqual(stages.PROBE_MARKER, "FEIYU-PROBE-")

    def test_prepare_drops_marked_candidates(self):
        out = stages.DropMarker().prepare([self.cand("fine"), self.cand("a FEIYU-PROBE-123 b")], self.ctx())
        self.assertEqual([c.text for c in out], ["fine"])

    def test_admit_catches_a_marker_that_arrived_with_a_read(self):
        stage = stages.DropMarker()
        self.assertEqual(stage.admit(self.cand("FEIYU-PROBE-9"), self.ctx()), DROP)
        self.assertEqual(stage.admit(self.cand("fine"), self.ctx()), KEEP)

    def test_markers_option_replaces_the_default(self):
        stage = stages.DropMarker(markers=["TEST-ONLY-"])
        self.assertEqual(stage.admit(self.cand("x TEST-ONLY-1"), self.ctx()), DROP)
        self.assertEqual(stage.admit(self.cand("FEIYU-PROBE-1"), self.ctx()), KEEP)


class FillTextTests(StageTestCase):
    def test_text_already_there_is_kept_without_a_read(self):
        store = FakeStore()
        self.assertEqual(stages.FillText().admit(self.cand("hello", store=store), self.ctx()), KEEP)
        self.assertEqual(store.reads, [])

    def test_empty_text_is_read_from_the_store(self):
        store = FakeStore(contents={"r1": "full text"})
        c = self.cand("  ", store=store, ref="r1")
        self.assertEqual(stages.FillText().admit(c, self.ctx()), KEEP)
        self.assertEqual(c.text, "full text")

    def test_a_failed_read_drops_the_candidate(self):
        c = self.cand("", store=FakeStore(), ref="missing")
        self.assertEqual(stages.FillText().admit(c, self.ctx()), DROP)

    def test_a_non_text_answer_drops_the_candidate(self):
        store = FakeStore(contents={"r": None})
        self.assertEqual(stages.FillText().admit(self.cand("", store=store, ref="r"), self.ctx()), DROP)

    def test_at_most_max_reads_per_request(self):
        store = FakeStore(contents={"r%d" % i: "text %d" % i for i in range(5)})
        stage = stages.FillText(max_reads=2)
        ctx = self.ctx()
        verdicts = [stage.admit(self.cand("", store=store, ref="r%d" % i), ctx) for i in range(5)]
        self.assertEqual(verdicts, [KEEP, KEEP, DROP, DROP, DROP])
        self.assertEqual(len(store.reads), 2)

    def test_default_budget_is_three_reads(self):
        self.assertEqual(stages.FillText().max_reads, 3)

    def test_no_read_after_the_deadline(self):
        store = FakeStore(contents={"r": "text"})
        ctx = self.ctx(deadline=self.clock() + 1)
        self.clock.t += 2
        self.assertEqual(stages.FillText().admit(self.cand("", store=store, ref="r"), ctx), DROP)
        self.assertEqual(store.reads, [])

    def test_the_read_counter_lives_in_the_request_context(self):
        store = FakeStore(contents={"r": "text"})
        stage = stages.FillText(max_reads=1)
        first, second = self.ctx(), self.ctx()
        self.assertEqual(stage.admit(self.cand("", store=store, ref="r"), first), KEEP)
        self.assertEqual(stage.admit(self.cand("", store=store, ref="r"), first), DROP)
        # the same stage object, another request: a fresh budget
        self.assertEqual(stage.admit(self.cand("", store=store, ref="r"), second), KEEP)
        self.assertEqual(vars(stage), {"max_reads": 1})
        self.assertEqual(first.state[stage], 1)
        self.assertEqual(second.state[stage], 1)

    def test_two_instances_do_not_share_state(self):
        a, b = stages.FillText(max_reads=1), stages.FillText(max_reads=1)
        store = FakeStore(contents={"r": "text"})
        ctx = self.ctx()
        self.assertEqual(a.admit(self.cand("", store=store, ref="r"), ctx), KEEP)
        self.assertEqual(b.admit(self.cand("", store=store, ref="r"), ctx), KEEP)


class DropOversizeTests(StageTestCase):
    def test_drops_a_long_memory_whole_and_notes_the_reason(self):
        ctx = self.ctx()
        stage = stages.DropOversize(max_chars=10)
        self.assertEqual(stage.admit(self.cand("x" * 11), ctx), DROP)
        self.assertEqual(ctx.notes, ["oversize-dropped"])
        self.assertEqual(stage.admit(self.cand("x" * 10), ctx), KEEP)

    def test_default_limit(self):
        self.assertEqual(stages.DropOversize().max_chars, 64 * 1024)


class RedactStageTests(StageTestCase):
    def test_cleans_the_text_with_the_context_redactor(self):
        ctx = self.ctx(redact=lambda text: text.replace("secret", "[X]"))
        c = self.cand("a secret b")
        self.assertEqual(stages.Redact().admit(c, ctx), KEEP)
        self.assertEqual(c.text, "a [X] b")

    def test_drops_what_ends_up_empty(self):
        ctx = self.ctx(redact=lambda text: "  ")
        self.assertEqual(stages.Redact().admit(self.cand("anything"), ctx), DROP)

    def test_without_a_redactor_the_text_is_only_stripped(self):
        c = self.cand("  hi  ")
        self.assertEqual(stages.Redact().admit(c, self.ctx()), KEEP)
        self.assertEqual(c.text, "hi")


class DedupeTests(StageTestCase):
    def test_drops_a_near_copy_of_an_accepted_memory(self):
        ctx = self.ctx()
        ctx.accepted.append(self.cand("用户喜欢喝乌龙茶"))
        self.assertEqual(stages.Dedupe().admit(self.cand("用户喜欢喝乌龙茶。"), ctx), DROP)
        self.assertEqual(stages.Dedupe().admit(self.cand("用户不喝咖啡"), ctx), KEEP)

    def test_nothing_accepted_yet_keeps(self):
        self.assertEqual(stages.Dedupe().admit(self.cand("x memory"), self.ctx()), KEEP)

    def test_overlap_option(self):
        ctx = self.ctx()
        ctx.accepted.append(self.cand("用户喜欢喝很烫的乌龙茶啊"))
        self.assertEqual(stages.Dedupe(overlap=0.8).admit(self.cand("用户喜欢喝很烫的乌龙茶呀"), ctx), DROP)
        self.assertEqual(stages.Dedupe(overlap=0.99).admit(self.cand("用户喜欢喝很烫的乌龙茶呀"), ctx), KEEP)


class BudgetTests(StageTestCase):
    def test_defers_what_does_not_fit_and_counts_what_is_used(self):
        ctx = self.ctx()
        stage = stages.Budget(max_chars=10)
        ctx.accepted.append(self.cand("x" * 6))
        self.assertEqual(stage.admit(self.cand("y" * 5), ctx), DEFER)
        self.assertEqual(stage.admit(self.cand("y" * 4), ctx), KEEP)

    def test_default_cap(self):
        self.assertEqual(stages.Budget().max_chars, 1500)


class EngineSemanticsTests(StageTestCase):
    class Rule(RecallStage):
        """admit by a function of the text; records what it saw."""

        def __init__(self, fn):
            self.fn = fn
            self.seen = []

        def admit(self, candidate, ctx):
            self.seen.append(candidate.text)
            return self.fn(candidate.text)

    def store(self, *texts):
        return FakeStore([Hit("ref-%d" % i, 0.9 - i / 100, t) for i, t in enumerate(texts)])

    def test_keep_adds_to_the_results_in_score_order(self):
        out = recall({"s": self.store("one", "two", "three")}, [], self.ctx())
        self.assertEqual([r["text"] for r in out["results"]], ["one", "two", "three"])
        self.assertEqual((out["more"], out["failed"]), (0, 0))

    def test_results_carry_id_source_text_score_and_date(self):
        store = FakeStore([Hit("r", 0.7, "dated", date="2026-11-03"), Hit("p", 0.6, "plain")])
        out = recall({"s": store}, [], self.ctx())
        self.assertEqual(out["results"], [{"id": "r", "source": "s", "text": "dated", "score": 0.7, "date": "2026-11-03"},
                                          {"id": "p", "source": "s", "text": "plain", "score": 0.6}])

    def test_hit_meta_is_copied_into_the_candidate_and_never_returned(self):
        hit = Hit("r", 0.9, "text", meta={"origin": "chat-7"})
        seen = []

        class Look(RecallStage):
            def admit(self, candidate, ctx):
                seen.append(dict(candidate.meta))
                candidate.meta["touched"] = True
                return KEEP

        out = recall({"s": FakeStore([hit])}, [Look()], self.ctx())
        self.assertEqual(seen, [{"origin": "chat-7"}])
        self.assertEqual(hit.meta, {"origin": "chat-7"})  # a copy: the backend's hit is untouched
        self.assertNotIn("origin", out["results"][0])
        self.assertEqual(Hit("x", 0.5).meta, {})

    def test_drop_is_not_counted_in_more(self):
        rule = self.Rule(lambda text: DROP if text == "two" else KEEP)
        out = recall({"s": self.store("one", "two", "three")}, [rule], self.ctx())
        self.assertEqual([r["text"] for r in out["results"]], ["one", "three"])
        self.assertEqual(out["more"], 0)

    def test_defer_is_counted_in_more_and_later_candidates_still_get_a_turn(self):
        rule = self.Rule(lambda text: DEFER if text == "two" else KEEP)
        out = recall({"s": self.store("one", "two", "three")}, [rule], self.ctx())
        self.assertEqual([r["text"] for r in out["results"]], ["one", "three"])
        self.assertEqual(out["more"], 1)

    def test_more_counts_the_candidates_left_when_the_limit_is_reached(self):
        out = recall({"s": self.store(*"abcdefgh")}, [], self.ctx(limit=5))
        self.assertEqual(len(out["results"]), 5)
        self.assertEqual(out["more"], 3)

    def test_more_at_the_limit_counts_only_candidates_that_survived_prepare(self):
        class Gate(RecallStage):
            def prepare(self, candidates, ctx):
                return [c for c in candidates if c.text != "c"]

        out = recall({"s": self.store("a", "b", "c", "d")}, [Gate()], self.ctx(limit=1))
        self.assertEqual((len(out["results"]), out["more"]), (1, 2))

    def test_exactly_the_limit_leaves_nothing_more(self):
        out = recall({"s": self.store("a", "b")}, [], self.ctx(limit=2))
        self.assertEqual((len(out["results"]), out["more"]), (2, 0))

    def test_defer_and_the_limit_add_up(self):
        rule = self.Rule(lambda text: DEFER if text in ("b", "e") else KEEP)
        out = recall({"s": self.store(*"abcdef")}, [rule], self.ctx(limit=2))
        self.assertEqual([r["text"] for r in out["results"]], ["a", "c"])
        # b deferred (1), then the limit is reached with d, e, f still waiting (3)
        self.assertEqual(out["more"], 4)

    def test_a_dropping_stage_stops_the_later_ones_for_that_candidate(self):
        first = self.Rule(lambda text: DROP if text == "two" else KEEP)
        second = self.Rule(lambda text: KEEP)
        recall({"s": self.store("one", "two", "three")}, [first, second], self.ctx())
        self.assertEqual(first.seen, ["one", "two", "three"])
        self.assertEqual(second.seen, ["one", "three"])

    def test_stages_run_in_the_configured_order(self):
        log = []

        class Tag(RecallStage):
            def __init__(self, name):
                self.name = name

            def prepare(self, candidates, ctx):
                log.append(("prepare", self.name))
                return candidates

            def admit(self, candidate, ctx):
                log.append(("admit", self.name))
                return KEEP

        recall({"s": self.store("only")}, [Tag("x"), Tag("y"), Tag("z")], self.ctx())
        self.assertEqual(log, [("prepare", "x"), ("prepare", "y"), ("prepare", "z"),
                               ("admit", "x"), ("admit", "y"), ("admit", "z")])

    def test_order_changes_the_outcome(self):
        # fill_text before drop_marker sees a marker that only the full text holds.
        uri = "r"
        store = FakeStore([Hit(uri, 0.9, "")], contents={uri: "FEIYU-PROBE-1 body"})
        marker_first = recall({"s": store}, [stages.DropMarker(), stages.FillText()], self.ctx())
        self.assertEqual(len(marker_first["results"]), 1)
        fill_first = recall({"s": store}, [stages.FillText(), stages.DropMarker()], self.ctx())
        self.assertEqual(fill_first["results"], [])

    def test_drop_marker_after_fill_text_catches_a_marker_found_only_after_a_read(self):
        store = FakeStore([Hit("r", 0.9, "")], contents={"r": "FEIYU-PROBE-1 body"})
        names = ("min_score", "fill_text", "drop_marker", "redact", "dedupe", "budget")
        chain = [registry.create("recall_stage", name) for name in names]
        self.assertEqual(recall({"s": store}, chain, self.ctx())["results"], [])

    def test_drop_marker_is_not_a_default_stage(self):
        self.assertNotIn("drop_marker", stages.DEFAULT_STAGES)
        self.assertEqual(stages.DEFAULT_STAGES, ("min_score", "relative_score", "newer_first", "linked", "fill_text",
                                                 "drop_oversize", "redact", "dedupe", "budget"))

    def test_accepted_is_visible_to_later_candidates(self):
        seen = []

        class Look(RecallStage):
            def admit(self, candidate, ctx):
                seen.append([c.text for c in ctx.accepted])
                return KEEP

        recall({"s": self.store("a", "b", "c")}, [Look()], self.ctx())
        self.assertEqual(seen, [[], ["a"], ["a", "b"]])

    def test_per_request_state_in_ctx_state(self):
        class Count(RecallStage):
            def admit(self, candidate, ctx):
                ctx.state["n"] = ctx.state.get("n", 0) + 1
                return KEEP if ctx.state["n"] <= 2 else DROP

        stage = Count()
        store = self.store("a", "b", "c", "d")
        for _ in range(2):  # the same stage object serves two requests
            ctx = self.ctx(limit=10)
            out = recall({"s": store}, [stage], ctx)
            self.assertEqual([r["text"] for r in out["results"]], ["a", "b"])
            self.assertEqual(ctx.state, {"n": 4})

    def test_fetch_size_is_three_times_the_limit_capped_at_thirty(self):
        store = self.store("a")
        recall({"s": store}, [], self.ctx(limit=4))
        recall({"s": store}, [], self.ctx(limit=10))
        recall({"s": store}, [], self.ctx(limit=50))
        self.assertEqual([limit for _, limit in store.queries], [12, 30, 30])

    def test_notes_reach_the_caller(self):
        notes = []
        recall({"s": FakeStore([Hit("r", 0.9, "x" * 20)])}, [stages.DropOversize(max_chars=5)],
               self.ctx(notes=notes))
        self.assertEqual(notes, ["oversize-dropped"])

    def test_the_context_tells_stages_who_asks(self):
        seen = []

        class Look(RecallStage):
            def admit(self, candidate, ctx):
                seen.append((ctx.client, ctx.tenant_id, ctx.public, ctx.query, ctx.limit))
                return KEEP

        recall({"s": self.store("a")}, [Look()], self.ctx("hello", 3, client="owner", tenant_id="12345", public=True))
        self.assertEqual(seen, [("owner", "12345", True, "hello", 3)])


class FailedCountTests(StageTestCase):
    def test_a_store_that_raises_counts_as_failed_and_the_others_still_answer(self):
        good = FakeStore([Hit("r", 0.9, "fact")])
        bad = FakeStore(error=True)
        out = recall({"good": good, "bad": bad}, [], self.ctx())
        self.assertEqual(out["failed"], 1)
        self.assertEqual([r["text"] for r in out["results"]], ["fact"])

    def test_an_empty_answer_is_not_a_failure(self):
        out = recall({"s": FakeStore()}, [], self.ctx())
        self.assertEqual((out["results"], out["more"], out["failed"]), ([], 0, 0))

    def test_every_store_failing_counts_every_store(self):
        out = recall({"a": FakeStore(error=True), "b": FakeStore(error=True)}, [], self.ctx())
        self.assertEqual(out["failed"], 2)

    def test_a_busy_pool_counts_as_busy_not_failed(self):
        pool = Pool(size=0)
        self.addCleanup(pool.close)
        out = recall({"a": FakeStore([Hit("r", 0.9, "x")])}, [], RecallContext("q", 5, pool=pool))
        self.assertEqual((out["failed"], out["busy"], out["results"]), (0, 1, []))

    def test_a_store_that_answers_with_something_else_than_hits_fails_alone(self):
        class Odd(FakeStore):
            def __init__(self, answer):
                super().__init__()
                self.answer = answer

            def find(self, query, limit, timeout=None, ctl=None):
                return self.answer

        lines = []
        good = FakeStore([Hit("r", 0.9, "fact")])
        for answer in (None, 5, "text", [{"ref": "x"}], [Hit("r", float("nan"), "t")], [Hit(3, 0.5, "t")],
                       [Hit("r", True, "t")], (h for h in [Hit("r", 0.9, "t")])):
            with self.subTest(answer=repr(answer)[:30]):
                out = recall({"odd": Odd(answer), "good": good}, [], self.ctx(log=lines.append, client="c"))
                self.assertEqual(out["failed"], 1)
                self.assertEqual([r["text"] for r in out["results"]], ["fact"])
        self.assertTrue(all(line.startswith("piece-error client=c store=odd where=find error=bad-result")
                            for line in lines))

    def test_a_raising_store_is_logged_by_type_only(self):
        lines = []
        recall({"bad": FakeStore(error=True)}, [], self.ctx(log=lines.append, client="c"))
        self.assertEqual(lines, ["piece-error client=c store=bad where=find error=BackendError"])

    def test_a_store_that_misses_the_deadline_counts_as_failed(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        slow = FakeStore([Hit("r", 0.9, "late")], gate=gate)
        fast = FakeStore([Hit("f", 0.8, "on time")])
        ctx = RecallContext("q", 5, pool=self.pool, deadline=__import__("time").monotonic() + 0.1)
        out = recall({"slow": slow, "fast": fast}, [], ctx)
        self.assertEqual(out["failed"], 1)
        self.assertEqual([r["text"] for r in out["results"]], ["on time"])

    def test_no_stores_is_an_empty_answer_with_nothing_failed(self):
        out = recall({}, [stages.MinScore()], self.ctx())
        self.assertEqual(out, {"results": [], "more": 0, "failed": 0, "busy": 0})


class PieceFailureTests(StageTestCase):
    """One broken stage never takes the request down silently, and never leaks what it should have stopped."""

    def store(self, *texts):
        return FakeStore([Hit("ref-%d" % i, 0.9 - i / 100, t) for i, t in enumerate(texts)])

    def test_an_exception_in_admit_drops_that_candidate_and_is_noted_once(self):
        class Picky(RecallStage):
            def admit(self, candidate, ctx):
                if candidate.text != "fine":
                    raise ValueError("boom " + candidate.text)
                return KEEP

        lines = []
        stage = registry.create("recall_stage", {"type": __name__ + ":ShoutStage"})  # a dotted path: class name
        picky = Picky()
        picky.name = "picky"
        notes = []
        out = recall({"s": self.store("secret one", "fine", "secret two")}, [picky, stage],
                     self.ctx(notes=notes, log=lines.append, client="owner"))
        self.assertEqual([r["text"] for r in out["results"]], ["FINE!"])
        self.assertEqual(notes, ["stage-error:picky"])
        self.assertEqual(lines, ["piece-error client=owner stage-error:picky where=admit error=ValueError"])
        self.assertNotIn("secret", " ".join(lines))

    def test_a_broken_redact_never_returns_the_raw_text(self):
        def explode(text):
            raise RuntimeError(text)

        notes = []
        out = recall({"s": self.store("token=abcdef123456")}, [registry.create("recall_stage", "redact")],
                     self.ctx(notes=notes, redact=explode))
        self.assertEqual(out["results"], [])
        self.assertEqual(notes, ["stage-error:redact"])  # the registered name

    def test_a_verdict_that_is_not_keep_drop_or_defer_is_a_drop(self):
        class Forgetful(RecallStage):
            def admit(self, candidate, ctx):
                candidate.text.upper()  # forgot the return

        notes, lines = [], []
        stage = Forgetful()
        stage.name = "forgetful"
        out = recall({"s": self.store("a", "b")}, [stage], self.ctx(notes=notes, log=lines.append))
        self.assertEqual((out["results"], out["more"]), ([], 0))
        self.assertEqual(notes, ["bad-verdict:Forgetful"])
        self.assertEqual(lines, ["piece-error client=- bad-verdict:Forgetful stage=forgetful"])

    def test_an_exception_in_prepare_fails_the_recall(self):
        class Broken(RecallStage):
            def prepare(self, candidates, ctx):
                raise KeyError("x")

        notes, lines = [], []
        with self.assertRaises(StageError) as caught:
            recall({"s": self.store("a")}, [Broken()], self.ctx(notes=notes, log=lines.append))
        self.assertEqual(caught.exception.name, "Broken")
        self.assertEqual(notes, ["stage-error:Broken"])
        self.assertEqual(lines, ["piece-error client=- stage-error:Broken where=prepare error=KeyError"])

    def test_prepare_returning_nothing_or_garbage_fails_the_recall(self):
        class NoReturn(RecallStage):
            def prepare(self, candidates, ctx):
                candidates.sort(key=lambda c: c.score)

        class Garbage(RecallStage):
            def prepare(self, candidates, ctx):
                return ["not a candidate"]

        for stage in (NoReturn(), Garbage()):
            with self.subTest(stage=type(stage).__name__), self.assertRaises(StageError):
                recall({"s": self.store("a")}, [stage], self.ctx())

    def test_a_duck_typed_stage_without_admit_or_prepare_is_skipped_for_that_moment(self):
        class OnlyPrepare:
            def prepare(self, candidates, ctx):
                return candidates

        out = recall({"s": self.store("a")}, [OnlyPrepare()], self.ctx())
        self.assertEqual([r["text"] for r in out["results"]], ["a"])

    def test_the_name_is_the_registered_name_else_the_class_name(self):
        self.assertEqual(piece_name(registry.create("recall_stage", "dedupe")), "dedupe")
        self.assertEqual(piece_name(stages.Dedupe()), "Dedupe")
        self.assertIsNone(RecallStage.name)


class DeadlineTests(StageTestCase):
    """The deadline covers the stages: once it passes, nothing more is admitted."""

    def test_slow_stages_stop_at_the_deadline_and_the_rest_count_as_more(self):
        clock = self.clock

        class Slow(RecallStage):
            def admit(self, candidate, ctx):
                clock.t += 0.4  # each candidate takes 0.4 s
                return KEEP

        notes = []
        store = FakeStore([Hit("r%d" % i, 0.9 - i / 100, "memory %d" % i) for i in range(10)])
        ctx = self.ctx(limit=10, deadline=self.clock() + 1.0, notes=notes)
        out = recall({"s": store}, [Slow()], ctx)
        self.assertEqual(len(out["results"]), 3)  # 0.4, 0.8, 1.2: the third one was already started
        self.assertEqual(out["more"], 7)
        self.assertEqual(notes, ["deadline"])

    def test_sixty_kilobyte_memories_answer_near_the_deadline(self):
        import time as real_time
        big = ["记忆%d " % i + "字" * 60000 for i in range(30)]
        store = FakeStore([Hit("r%d" % i, 0.9, text) for i, text in enumerate(big)])
        chain = [registry.create("recall_stage", name) for name in stages.DEFAULT_STAGES]

        def slow_redact(text):
            real_time.sleep(0.15)  # stands for a redactor working through 60 KB
            return text

        started = real_time.monotonic()
        ctx = RecallContext("记忆", 10, pool=self.pool, deadline=started + 1.0, redact=slow_redact)
        recall({"s": store}, chain, ctx)
        self.assertLess(real_time.monotonic() - started, 1.5)

    def test_finds_leave_time_for_the_stages(self):
        ctx = self.ctx(deadline=self.clock() + 3.0)
        self.assertAlmostEqual(ctx.deadline - ctx.find_deadline, 0.5)
        short = self.ctx(deadline=self.clock() + 0.5)
        self.assertAlmostEqual(short.deadline - short.find_deadline, 0.1)


class MoreTests(StageTestCase):
    """"more" is at most how many good memories were left out: obvious drops are not counted."""

    def test_six_identical_memories_with_limit_one_leave_nothing_more(self):
        store = FakeStore([Hit("r%d" % i, 0.9, "用户喜欢喝乌龙茶") for i in range(6)])
        chain = [registry.create("recall_stage", name) for name in stages.DEFAULT_STAGES]
        out = recall({"s": store}, chain, self.ctx("乌龙茶", 1))
        self.assertEqual((len(out["results"]), out["more"]), (1, 0))

    def test_cheap_stages_also_judge_the_candidates_past_the_limit(self):
        texts = ["first memory", "a FEIYU-PROBE-1 memory", "x" * 100, "another one"]
        store = FakeStore([Hit("r%d" % i, 0.9 - i / 100, t) for i, t in enumerate(texts)])
        chain = [stages.DropMarker(), stages.DropOversize(max_chars=50), stages.Dedupe()]
        out = recall({"s": store}, chain, self.ctx(limit=1))
        self.assertEqual(out["more"], 1)  # only "another one": the marked and the oversize ones are drops

    def test_expensive_stages_are_not_run_past_the_limit(self):
        store = FakeStore([Hit("r%d" % i, 0.9 - i / 100, "") for i in range(5)],
                          contents={"r%d" % i: "text %d" % i for i in range(5)})
        out = recall({"s": store}, [stages.FillText()], self.ctx(limit=1))
        self.assertEqual((len(out["results"]), out["more"]), (1, 4))
        self.assertEqual(store.reads, ["r0"])

    def test_which_built_in_stages_are_cheap(self):
        cheap = {name for name, factory in registry.available("recall_stage").items()
                 if getattr(factory, "cheap", False)}
        self.assertTrue({"drop_marker", "drop_oversize", "dedupe"} <= cheap)
        self.assertFalse(cheap & {"fill_text", "redact", "budget", "min_score"})


class ShoutStage(RecallStage):
    """A custom stage, loaded by its dotted path below."""

    def __init__(self, suffix="!"):
        self.suffix = suffix

    def admit(self, candidate, ctx):
        candidate.text = candidate.text.upper() + self.suffix
        return KEEP


class CustomStageTests(StageTestCase):
    def test_a_stage_loaded_by_dotted_path_takes_part_in_the_recall(self):
        stage = registry.create("recall_stage", {"type": __name__ + ":ShoutStage", "suffix": "?"})
        self.assertIsInstance(stage, ShoutStage)
        out = recall({"s": FakeStore([Hit("r", 0.9, "hello")])}, [stage], self.ctx())
        self.assertEqual(out["results"][0]["text"], "HELLO?")

    def test_a_registered_stage_is_found_by_name(self):
        registry.register("recall_stage", "test-shout")(ShoutStage)
        self.addCleanup(lambda: registry._pieces["recall_stage"].pop("test-shout", None))
        self.assertIsInstance(registry.create("recall_stage", "test-shout"), ShoutStage)

    def test_the_base_class_is_a_no_op(self):
        base = RecallStage()
        c = self.cand("x")
        self.assertEqual(base.prepare([c], self.ctx()), [c])
        self.assertEqual(base.admit(c, self.ctx()), KEEP)


class BuiltInSetTests(unittest.TestCase):
    def test_default_stages_are_all_registered(self):
        for name in stages.DEFAULT_STAGES:
            self.assertIsInstance(registry.create("recall_stage", name), RecallStage)


if __name__ == "__main__":
    unittest.main()
