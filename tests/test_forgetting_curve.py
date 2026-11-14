import unittest

from feiyu_memgate import registry
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.forgetting.curve import Ebbinghaus, ForgettingCurve
from feiyu_memgate.forgetting.engine import Forgetting
from feiyu_memgate.forgetting.stage import Freshness


class CurveTests(unittest.TestCase):
    def test_half_life_halves_the_retention(self):
        curve = Ebbinghaus()
        self.assertEqual(curve.retention(0, 0, "x"), 1.0)
        self.assertAlmostEqual(curve.retention(90, 0, "x"), 0.5)
        self.assertAlmostEqual(curve.retention(180, 0, "x"), 0.25)

    def test_reinforcement_lengthens_the_half_life(self):
        curve = Ebbinghaus()
        self.assertAlmostEqual(curve.retention(144, 1, "x"), 0.5)
        self.assertAlmostEqual(curve.retention(90 * 1.6 ** 3, 3, "x"), 0.5)
        self.assertGreater(curve.retention(100, 2, "x"), curve.retention(100, 1, "x"))

    def test_default_lifetime_is_about_389_days_without_use(self):
        # R < 0.05 after half_life * log2(20) days: 90 days -> 388.97
        curve = Ebbinghaus()
        self.assertGreaterEqual(curve.retention(388, 0, "x"), 0.05)
        self.assertLess(curve.retention(389, 0, "x"), 0.05)

    def test_reinforcement_stops_at_max_uses(self):
        curve = Ebbinghaus(max_uses=4)
        self.assertEqual(curve.retention(500, 4, "x"), curve.retention(500, 99, "x"))

    def test_negative_age_counts_as_zero(self):
        self.assertEqual(Ebbinghaus().retention(-10, 0, "x"), 1.0)

    def test_per_store_half_lives(self):
        curve = Ebbinghaus(half_life_days=60, per_store={"private": 180, "group": 30})
        self.assertAlmostEqual(curve.retention(180, 0, "private"), 0.5)
        self.assertAlmostEqual(curve.retention(30, 0, "group"), 0.5)
        self.assertAlmostEqual(curve.retention(60, 0, "other"), 0.5)

    def test_a_tenant_key_uses_the_part_before_the_colon(self):
        curve = Ebbinghaus(per_store={"group": 30})
        self.assertAlmostEqual(curve.retention(30, 0, "group:10001"), 0.5)

    def test_the_engine_passes_the_source_of_a_tenant_key(self):
        engine = Forgetting(None, None, Ebbinghaus(per_store={"group": 30}), wall=lambda: 30 * 86400.0)
        self.assertEqual(Forgetting.key("group", "10001"), "group:10001")
        self.assertEqual(Forgetting.key("private"), "private")

        class Entry:
            first_seen, last_used, uses, pinned = 0.0, None, 0, False

        self.assertAlmostEqual(engine.retention("group:10001", Entry()), 0.5)

    def test_bad_options_are_config_errors(self):
        for options in ({"half_life_days": 0}, {"per_store": {"a": -1}}, {"reinforce": 0.5}, {"max_uses": -1}):
            with self.assertRaises(ConfigError):
                Ebbinghaus(**options)

    def test_registered_as_a_piece(self):
        self.assertIn("forgetting_curve", registry.KINDS)
        curve = registry.create("forgetting_curve", {"type": "ebbinghaus", "half_life_days": 10})
        self.assertIsInstance(curve, Ebbinghaus)
        self.assertAlmostEqual(curve.retention(10, 0, "x"), 0.5)
        self.assertIs(registry.resolve("recall_stage", "freshness"), Freshness)

    def test_a_custom_curve_by_dotted_path(self):
        curve = registry.create("forgetting_curve", {"type": "tests.test_forgetting_curve:Flat"})
        self.assertEqual(curve.retention(1000, 0, "x"), 0.7)

    def test_unknown_option_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            registry.create("forgetting_curve", {"type": "ebbinghaus", "nonsense": 1})


class Flat(ForgettingCurve):
    def retention(self, age_days, uses, source):
        return 0.7
