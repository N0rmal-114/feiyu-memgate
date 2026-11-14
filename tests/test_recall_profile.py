"""The "broad" recall profile: stage option, lookup on the stores, pins of the declared numbers,
and the selection rule of bench/dev_eval.py that produced them."""
import os
import sys
import unittest

from feiyu_memgate import registry
from feiyu_memgate.backends import embedding as embedding_module
from feiyu_memgate.backends.base import MemoryBackend
from feiyu_memgate.backends.embedding import EmbeddingBackend
from feiyu_memgate.backends.hybrid import HybridBackend
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.backends.openviking import OpenVikingBackend
from feiyu_memgate.backends.sqlite import SQLiteBackend
from feiyu_memgate.core import stages
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.model import Candidate

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "bench"))
import dev_eval  # noqa: E402


class Store(MemoryBackend):
    def __init__(self, **declared):
        for key, value in declared.items():
            setattr(self, key, value)

    def find(self, query, limit, timeout=None, ctl=None):
        return []


class StageProfileTest(unittest.TestCase):
    def setUp(self):
        self.pool = Pool()
        self.addCleanup(self.pool.close)

    def cands(self, scores, store, source="s"):
        return [Candidate(score=s, source=source, store=store, ref="r%d" % i, text="t%d" % i)
                for i, s in enumerate(scores)]

    def kept(self, stage, scores, store, source="s"):
        ctx = RecallContext("q", 5, pool=self.pool)
        return [c.score for c in stage.prepare(self.cands(scores, store, source), ctx)]

    def test_min_score_uses_the_broad_line_only_when_asked(self):
        store = Store(default_min_score=0.5, broad_min_score=0.3)
        scores = [0.9, 0.4, 0.2]
        self.assertEqual(self.kept(stages.MinScore(), scores, store), [0.9])
        self.assertEqual(self.kept(stages.MinScore(profile="precise"), scores, store), [0.9])
        self.assertEqual(self.kept(stages.MinScore(profile="broad"), scores, store), [0.9, 0.4])

    def test_relative_score_uses_the_broad_ratio_only_when_asked(self):
        store = Store(default_relative_ratio=0.9, broad_relative_ratio=0.5)
        scores = [1.0, 0.7, 0.4]
        self.assertEqual(self.kept(stages.RelativeScore(), scores, store), [1.0])
        self.assertEqual(self.kept(stages.RelativeScore(profile="broad"), scores, store), [1.0, 0.7])

    def test_a_store_without_broad_values_falls_back_to_its_default(self):
        store = Store(default_min_score=0.45, default_relative_ratio=0.8)  # no broad_* at all
        self.assertEqual(self.kept(stages.MinScore(profile="broad"), [0.5, 0.4], store), [0.5])
        self.assertEqual(self.kept(stages.RelativeScore(profile="broad"), [1.0, 0.85, 0.7], store), [1.0, 0.85])
        none = Store(default_min_score=0.45, broad_min_score=None, default_relative_ratio=0.8,
                     broad_relative_ratio=None)  # declared as None: the same
        self.assertEqual(self.kept(stages.MinScore(profile="broad"), [0.5, 0.4], none), [0.5])
        self.assertEqual(self.kept(stages.RelativeScore(profile="broad"), [1.0, 0.85, 0.7], none), [1.0, 0.85])

    def test_a_store_with_nothing_declared_uses_the_stage_constants(self):
        bare = object()
        self.assertEqual(self.kept(stages.MinScore(profile="broad"), [0.6, 0.4], bare), [0.6])
        self.assertEqual(self.kept(stages.RelativeScore(profile="broad"), [1.0, 0.7, 0.5], bare), [1.0, 0.7])

    def test_a_malformed_broad_value_is_ignored(self):
        store = Store(default_min_score=0.5, broad_min_score="low", default_relative_ratio=0.9,
                      broad_relative_ratio=7)
        self.assertEqual(self.kept(stages.MinScore(profile="broad"), [0.6, 0.4], store), [0.6])
        self.assertEqual(self.kept(stages.RelativeScore(profile="broad"), [1.0, 0.8], store), [1.0])

    def test_explicit_values_win_over_the_profile(self):
        store = Store(default_min_score=0.5, broad_min_score=0.3, default_relative_ratio=0.9,
                      broad_relative_ratio=0.5)
        self.assertEqual(self.kept(stages.MinScore(value=0.8, profile="broad"), [0.9, 0.7], store), [0.9])
        self.assertEqual(self.kept(stages.MinScore(per_store={"s": 0.65}, profile="broad"), [0.9, 0.7, 0.6], store),
                         [0.9, 0.7])
        self.assertEqual(self.kept(stages.RelativeScore(ratio=0.95, profile="broad"), [1.0, 0.9], store), [1.0])

    def test_an_unknown_profile_is_a_configuration_error(self):
        for name in ("min_score", "relative_score"):
            for bad in ("wide", "", None, 1, "Broad"):
                with self.assertRaises(ConfigError, msg=repr(bad)) as ctx:
                    registry.create("recall_stage", {"type": name, "profile": bad})
                self.assertIn("profile", str(ctx.exception))
                self.assertIn(name, str(ctx.exception))

    def test_the_stages_are_built_with_the_option_from_a_spec(self):
        stage = registry.create("recall_stage", {"type": "min_score", "profile": "broad"})
        self.assertEqual(stage.profile, "broad")
        self.assertEqual(registry.create("recall_stage", "relative_score").profile, "precise")

    def test_default_stages_do_not_change(self):
        self.assertEqual(stages.DEFAULT_STAGES[:3], ("min_score", "relative_score", "newer_first"))


class DeclaredNumbersTest(unittest.TestCase):
    """Pins: the numbers the rule of bench/dev_eval.py chose on bench/dev (results/dev/*-broad.md) and the
    unchanged defaults next to them."""

    def test_class_declarations(self):
        rows = (
            (SQLiteBackend, 0.30, 0.75, 0.25, 0.70),
            (InMemoryBackend, 0.30, 0.75, 0.25, 0.70),
            (HybridBackend, 0.70, 0.85, 0.40, 0.65),
            (OpenVikingBackend, 0.55, 0.95, 0.55, 0.90),
            (EmbeddingBackend, 0.50, 0.85, 0.30, 0.60),
        )
        for cls, dmin, dratio, bmin, bratio in rows:
            numbers = (cls.default_min_score, cls.default_relative_ratio, cls.broad_min_score,
                       cls.broad_relative_ratio)
            self.assertEqual(numbers, (dmin, dratio, bmin, bratio), cls.__name__)

    def test_a_broad_profile_is_never_stricter_than_the_default(self):
        classes = (SQLiteBackend, InMemoryBackend, HybridBackend, OpenVikingBackend, EmbeddingBackend)
        for cls in classes:
            self.assertLessEqual(cls.broad_min_score, cls.default_min_score, cls.__name__)
            self.assertLessEqual(cls.broad_relative_ratio, cls.default_relative_ratio, cls.__name__)
        for pattern, preset in embedding_module.PRESETS:
            self.assertLessEqual(preset["broad_min_score"], preset["min_score"], pattern)
            self.assertLessEqual(preset["broad_ratio"], preset["ratio"], pattern)

    def test_the_base_class_declares_no_broad_profile(self):
        self.assertIsNone(MemoryBackend.broad_min_score)
        self.assertIsNone(MemoryBackend.broad_relative_ratio)

    def test_embedding_presets_per_model(self):
        expected = {
            "bge-m3": (0.50, 0.85, 0.30, 0.60),
            "hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16": (0.30, 0.90, 0.10, 0.65),
            "hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0": (0.25, 0.75, 0.05, 0.50),
            "embeddinggemma": (0.55, 0.85, 0.30, 0.60),
            "qwen3-embedding:0.6b": (0.20, 0.95, 0.15, 0.65),
        }
        for model, numbers in expected.items():
            preset, known = embedding_module.preset_for(model)
            self.assertTrue(known, model)
            self.assertEqual((preset["min_score"], preset["ratio"], preset["broad_min_score"],
                              preset["broad_ratio"]), numbers, model)

    def test_an_embedding_instance_carries_its_models_broad_numbers(self):
        for model, numbers in (("bge-base-zh-v1.5", (0.05, 0.50)), ("embeddinggemma", (0.30, 0.60))):
            be = EmbeddingBackend(":memory:", "http://127.0.0.1:1/v1", model)
            try:
                self.assertEqual((be.broad_min_score, be.broad_relative_ratio), numbers, model)
            finally:
                be.close()

    def test_the_instance_values_drive_the_stage(self):
        be = EmbeddingBackend(":memory:", "http://127.0.0.1:1/v1", "hf.co/x/bge-base-zh-v1.5-gguf:Q8_0")
        self.addCleanup(be.close)
        cand = Candidate(score=0.1, source="s", store=be, ref="r", text="t")
        ctx = RecallContext("q", 5, pool=Pool())
        self.addCleanup(ctx.pool.close)
        self.assertEqual(stages.MinScore().prepare([cand], ctx), [])  # default 0.25
        self.assertEqual(stages.MinScore(profile="broad").prepare([cand], ctx), [cand])  # broad 0.05


class BroadRuleTest(unittest.TestCase):
    """bench/dev_eval.py: the selection rule of the broad profile."""

    @staticmethod
    def m(hit, precision, quiet, balanced=0.5, items=1.0):
        return {"deployed_hit": hit, "precision": precision, "quiet": quiet, "balanced": balanced, "items": items}

    def test_admission_and_choice(self):
        results = [
            ((0.5, 0.85), self.m(0.6, 0.9, 0.8)),   # the precise point
            ((0.3, 0.7), self.m(0.9, 0.55, 0.5)),   # best hit, admissible (quiet 0.5 >= 0.4, precision >= 0.5)
            ((0.2, 0.6), self.m(0.95, 0.45, 0.6)),  # precision under 50 %
            ((0.1, 0.6), self.m(0.99, 0.6, 0.3)),   # quiet under half of 0.8
            ((0.4, 0.8), self.m(0.7, None, 1.0)),   # nothing delivered: no precision
        ]
        point, m, precise, count = dev_eval.broad_choice(results, (0.5, 0.85))
        self.assertEqual(point, (0.3, 0.7))
        self.assertEqual(m["deployed_hit"], 0.9)
        self.assertEqual(precise["deployed_hit"], 0.6)
        self.assertEqual(count, 2)

    def test_ties_go_to_balanced_then_higher_min_score_then_higher_ratio(self):
        precise = ((0.5, 0.85), self.m(0.5, 0.9, 0.8))
        hit = 0.8
        tied = [precise, ((0.3, 0.6), self.m(hit, 0.6, 0.5, balanced=0.50)),
                ((0.2, 0.6), self.m(hit, 0.6, 0.5, balanced=0.55))]
        self.assertEqual(dev_eval.broad_choice(tied, (0.5, 0.85))[0], (0.2, 0.6))
        tied = [precise, ((0.2, 0.6), self.m(hit, 0.6, 0.5)), ((0.3, 0.6), self.m(hit, 0.6, 0.5))]
        self.assertEqual(dev_eval.broad_choice(tied, (0.5, 0.85))[0], (0.3, 0.6))
        tied = [precise, ((0.3, 0.6), self.m(hit, 0.6, 0.5)), ((0.3, 0.7), self.m(hit, 0.6, 0.5)),
                ((0.3, None), self.m(hit, 0.6, 0.5))]
        self.assertEqual(dev_eval.broad_choice(tied, (0.5, 0.85))[0], (0.3, 0.7))  # "off" ranks lowest

    def test_the_precise_point_can_win_when_nothing_beats_it(self):
        precise = ((0.5, 0.85), self.m(0.8, 0.9, 0.8))
        worse = ((0.3, 0.7), self.m(0.7, 0.9, 0.8))
        self.assertEqual(dev_eval.broad_choice([precise, worse], (0.5, 0.85))[0], (0.5, 0.85))

    def test_declared_point_reads_the_backends(self):
        self.assertEqual(dev_eval.declared_point("sqlite"), (0.30, 0.75))
        self.assertEqual(dev_eval.declared_point("hybrid"), (0.70, 0.85))
        self.assertEqual(dev_eval.declared_point("openviking"), (0.55, 0.95))
        self.assertEqual(dev_eval.declared_point("embedding", "embeddinggemma"), (0.55, 0.85))

    def test_search_still_refuses_the_held_out_set(self):
        with self.assertRaises(SystemExit):
            dev_eval.main(["--backend", "sqlite", "--search", "--profile", "broad",
                           "--data", os.path.join(ROOT, "bench", "data")])


if __name__ == "__main__":
    unittest.main()
