"""The relative_score recall stage: drop what scores far below the best candidate of the request."""
import unittest

from feiyu_memgate import registry
from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.core import stages
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.model import Candidate, Hit


class Store(MemoryBackend):
    def __init__(self, hits, **declared):
        self.hits = list(hits)
        for key, value in declared.items():
            setattr(self, key, value)

    def find(self, query, limit, timeout=None, ctl=None):
        return list(self.hits)


class RelativeScoreTest(unittest.TestCase):
    def setUp(self):
        self.pool = Pool()
        self.addCleanup(self.pool.close)

    def cands(self, scores, store=None, source="s"):
        return [Candidate(score=s, source=source, store=store, ref="r%d" % i, text="text %d" % i)
                for i, s in enumerate(scores)]

    def ctx(self, limit=5):
        return RecallContext("q", limit, pool=self.pool)

    def kept(self, stage, scores, **kw):
        return [c.score for c in stage.prepare(self.cands(scores, **kw), self.ctx())]

    def test_registered_cheap_and_documented(self):
        stage = registry.create("recall_stage", "relative_score")
        self.assertIsInstance(stage, stages.RelativeScore)
        self.assertTrue(stage.cheap)
        self.assertEqual(stage.name, "relative_score")
        self.assertTrue(stages.RelativeScore.__doc__.strip())
        self.assertTrue(registry.available("recall_stage")["relative_score"].cheap)

    def test_default_stages_hold_it_right_after_min_score(self):
        names = stages.DEFAULT_STAGES
        self.assertEqual(names[names.index("min_score") + 1], "relative_score")
        chain = [registry.create("recall_stage", n) for n in names]  # every default builds without options
        self.assertEqual(len(chain), len(names))

    def test_drops_below_ratio_times_best(self):
        stage = stages.RelativeScore(ratio=0.7)
        self.assertEqual(self.kept(stage, [0.5, 0.9, 0.62, 0.64, 0.2]), [0.9, 0.64])  # line 0.63, order kept
        self.assertEqual(self.kept(stage, [0.9, 0.63]), [0.9, 0.63])  # the line itself stays (>=)

    def test_ratio_zero_keeps_everything_and_one_keeps_only_the_best(self):
        scores = [0.9, 0.5, 0.1, 0.9]
        self.assertEqual(self.kept(stages.RelativeScore(ratio=0), scores), scores)
        self.assertEqual(self.kept(stages.RelativeScore(ratio=1), scores), [0.9, 0.9])

    def test_gap_adds_a_second_line(self):
        stage = stages.RelativeScore(ratio=0.5, gap=0.2)
        self.assertEqual(self.kept(stage, [0.9, 0.75, 0.6, 0.45]), [0.9, 0.75])  # best - gap = 0.7 is the stricter line
        loose = stages.RelativeScore(ratio=0.9, gap=0.5)
        self.assertEqual(self.kept(loose, [1.0, 0.95, 0.85]), [1.0, 0.95])  # ratio is the stricter line
        only_gap = stages.RelativeScore(ratio=0, gap=0.1)
        self.assertEqual(self.kept(only_gap, [0.5, 0.45, 0.3]), [0.5, 0.45])

    def test_empty_and_single_candidates(self):
        stage = stages.RelativeScore(ratio=0.9)
        self.assertEqual(stage.prepare([], self.ctx()), [])
        self.assertEqual(self.kept(stage, [0.05]), [0.05])  # the best one always stays, however poor

    def test_scores_are_not_changed_and_input_not_mutated(self):
        candidates = self.cands([0.9, 0.2])
        out = stages.RelativeScore(ratio=0.5).prepare(candidates, self.ctx())
        self.assertEqual([c.score for c in candidates], [0.9, 0.2])
        self.assertIs(out[0], candidates[0])

    def test_monotone_in_ratio(self):
        """A higher ratio never keeps a candidate a lower one dropped."""
        scores = [0.95, 0.9, 0.81, 0.7, 0.66, 0.5, 0.31, 0.1]
        previous = None
        for ratio in (0, 0.2, 0.4, 0.6, 0.7, 0.8, 0.9, 1):
            now = set(self.kept(stages.RelativeScore(ratio=ratio), scores))
            if previous is not None:
                self.assertTrue(now <= previous, ratio)
            previous = now
        self.assertIn(0.95, previous)

    def test_scale_invariant(self):
        scores = [0.9, 0.7, 0.5, 0.3]
        stage = stages.RelativeScore(ratio=0.7)
        base = len(self.kept(stage, scores))
        self.assertEqual(len(self.kept(stage, [s / 3 for s in scores])), base)

    def test_store_default_ratio_and_explicit_override(self):
        scores = [1.0, 0.7]
        strict = Store([], default_relative_ratio=0.8)
        loose = Store([], default_relative_ratio=0.5)
        bare = Store([])
        auto = stages.RelativeScore()
        self.assertEqual(self.kept(auto, scores, store=strict), [1.0])
        self.assertEqual(self.kept(auto, scores, store=loose), [1.0, 0.7])
        self.assertEqual(self.kept(auto, scores, store=bare), [1.0, 0.7])  # generic 0.6
        self.assertEqual(self.kept(auto, [1.0, 0.5], store=bare), [1.0])
        self.assertEqual(self.kept(stages.RelativeScore(ratio=0.6), scores, store=strict), [1.0, 0.7])

    def test_a_bad_declared_ratio_falls_back_to_the_generic_one(self):
        for bad in ("x", None, True, -1, 2, float("nan")):
            store = Store([], default_relative_ratio=bad)
            self.assertEqual(self.kept(stages.RelativeScore(), [1.0, 0.7, 0.5], store=store), [1.0, 0.7], bad)

    def test_the_best_candidate_is_the_best_of_all_sources(self):
        mixed = [Candidate(score=0.9, source="a", store=None, ref="a1", text="a1"),
                 Candidate(score=0.5, source="b", store=None, ref="b1", text="b1"),
                 Candidate(score=0.6, source="b", store=None, ref="b2", text="b2")]
        out = stages.RelativeScore(ratio=0.6).prepare(mixed, self.ctx())
        self.assertEqual([c.ref for c in out], ["a1", "b2"])

    def test_options_are_checked(self):
        for bad in (-0.1, 1.1, "0.5", True, float("nan")):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                stages.RelativeScore(ratio=bad)
        for bad in (-1, "x", True, float("inf"), float("nan")):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                stages.RelativeScore(gap=bad)
        stages.RelativeScore(ratio=0, gap=0)
        stages.RelativeScore(ratio=1, gap=None)

    def test_after_min_score_the_best_is_the_best_that_passed(self):
        store = Store([Hit("a", 0.9, "text a"), Hit("b", 0.6, "text b"), Hit("c", 0.2, "text c")],
                      default_min_score=0.5, default_relative_ratio=0.8)
        chain = [stages.MinScore(), stages.RelativeScore()]
        out = recall({"s": store}, chain, self.ctx())
        self.assertEqual([r["id"] for r in out["results"]], ["a"])  # c fails the threshold, b the ratio
        out = recall({"s": Store([Hit("b", 0.6, "text b"), Hit("c", 0.2, "text c")], default_min_score=0.5,
                                 default_relative_ratio=0.8)}, chain, self.ctx())
        self.assertEqual([r["id"] for r in out["results"]], ["b"])  # a poor best still stays: min_score judged it

    def test_a_stage_that_changes_scores_goes_between_the_two(self):
        store = Store([Hit("old", 0.9, "old text"), Hit("new", 0.8, "new text")], default_relative_ratio=0.9)
        halve_old = stages.SourceWeight(weights={"s": 1.0})

        class AgeOld(stages.RecallStage):
            def prepare(self, candidates, ctx):
                for c in candidates:
                    if c.ref == "old":
                        c.score *= 0.5
                return candidates

        out = recall({"s": store}, [halve_old, AgeOld(), stages.RelativeScore()], self.ctx())
        self.assertEqual([r["id"] for r in out["results"]], ["new"])  # compared on the aged scores

    def test_does_not_hide_the_noise_a_whole_default_chain_removes(self):
        """End to end with the default chain: one clear answer and a tail of weaker memories."""
        hits = [Hit("right", 0.9, "the right memory"), Hit("n1", 0.5, "noise one"), Hit("n2", 0.45, "noise two")]
        store = Store(hits, default_min_score=0.4, default_relative_ratio=0.8)
        chain = [registry.create("recall_stage", n) for n in stages.DEFAULT_STAGES]
        out = recall({"s": store}, chain, self.ctx())
        self.assertEqual([r["id"] for r in out["results"]], ["right"])
        self.assertEqual(out["more"], 0)  # the cheap stage also judged the ones past the limit


if __name__ == "__main__":
    unittest.main()
