"""bench/chat/noise.py: the hub penalty, the z scores, top and each in a lookup, lead and cap in a session, the
parsing rules and the small statistics of --diagnose, all worked by hand on two-dimensional unit vectors given as angles.
"""
import importlib.util
import math
import os
import sys
import unittest

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_CHAT = os.path.join(_ROOT, "bench", "chat")


def _load(name):
    key = "bench_chat_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(_CHAT, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


noise = _load("noise")
model_lab = _load("model_lab")
lab = model_lab.lab

SCALE = (0.0, 1.0, 0.3, 0.6)


def angle(degrees):
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)]


def cos(x, y):
    return math.cos(math.radians(x - y))


class SpyEngine(model_lab.LabEngine):
    """A LabEngine that remembers the cosines its last lookup was given."""

    seen = None

    def lookup(self, cosines):
        self.seen = list(cosines)
        return super().lookup(cosines)


def engine_of(angles, query, scale=SCALE, cls=SpyEngine):
    memories = {mid: {"text": "memory %s" % mid, "date": "2026-01-01", "cluster": None, "role": "filler"} for mid in angles}
    vectors = {"memory %s" % mid: angle(deg) for mid, deg in angles.items()}
    vectors["query"] = angle(query)
    world = {"world": "w", "memories": memories, "cases": []}
    return cls(world, lambda text: vectors[text], lab.Scale(*scale), {})


def cosines_of(engine):
    return engine.cosines_for("query", None)


NEAR = {"a": 0, "b": 15, "c": 70, "d": 105, "e": 160, "f": 230}  # a query at 5 degrees reaches a and b only
HUBS = {"a": 0, "b": 10, "c": 20, "d": 90}


class ParseAndLabelTest(unittest.TestCase):
    def values(self, **given):
        return dict(noise.DEFAULTS, **given)

    def test_nothing_is_on_by_default(self):
        self.assertFalse(noise.active(noise.DEFAULTS))
        self.assertFalse(noise.active(None))
        self.assertTrue(noise.active(self.values(cap=2)))
        self.assertTrue(noise.active(self.values(hub=0.0)))  # a lambda of 0 is "on" and changes nothing
        self.assertEqual(noise.DEFAULTS, {"hub": None, "k": 10, "top": None, "each": None, "lead": None, "cap": None})

    def test_check_accepts_good_values(self):
        noise.check(self.values(hub=0.0, k=1, top=-1.0, each=0.0, lead=1.01, cap=1), sessions=True)
        noise.check(self.values(hub=2.0, k=20, top=3.5, each=2.0), sessions=False)

    def test_check_refuses_bad_values(self):
        for given in ({"hub": -0.1}, {"hub": float("nan")}, {"k": 0}, {"top": float("inf")}, {"each": float("nan")},
                      {"lead": 0.0}, {"lead": -1.0}, {"cap": 0}):
            with self.assertRaises(SystemExit, msg=str(given)):
                noise.check(self.values(**given), sessions=True)

    def test_lead_and_cap_are_for_sessions_only(self):
        for given in ({"lead": 0.9}, {"cap": 2}):
            with self.assertRaises(SystemExit) as context:
                noise.check(self.values(**given), sessions=False)
            self.assertIn("session_lab", str(context.exception))

    def test_the_label_shows_k_only_with_hub(self):
        self.assertEqual(noise.label(self.values(hub=1.0, k=5)), "hub=1.0,k=5")
        self.assertEqual(noise.label(self.values(top=2.0, cap=2)), "top=2.0,cap=2")
        self.assertEqual(noise.label(self.values(k=7, each=1.5)), "each=1.5")


class HubTest(unittest.TestCase):
    def vectors(self):
        return [angle(deg) for deg in HUBS.values()]

    def test_penalty_by_hand_with_k_two(self):
        # most similar others: a: b .985, c .940; b: a, c .985 both; c: b .985, a .940; d: c .342, b .174
        # h = .9623, .9848, .9623, .2578; the median is .9623, so only b is above it, by (cos10 - cos20) / 2
        pen = noise.hub_penalties(self.vectors(), lab.Scale(*SCALE), 2)
        self.assertEqual(len(pen), 4)
        self.assertAlmostEqual(pen[1], (cos(0, 10) - cos(0, 20)) / 2, places=6)
        for i in (0, 2, 3):
            self.assertAlmostEqual(pen[i], 0.0, places=6)

    def test_the_penalty_is_in_cosine_units_whatever_the_scale(self):
        want = (cos(0, 10) - cos(0, 20)) / 2
        for scale in ((0.0, 2.0, 0.3, 0.6), (0.2, 1.0, 0.3, 0.6)):
            pen = noise.hub_penalties(self.vectors(), lab.Scale(*scale), 2)
            self.assertAlmostEqual(pen[1], want, places=6, msg=str(scale))

    def test_k_larger_than_the_world_uses_every_other_memory(self):
        h = [(cos(0, 10) + cos(0, 20) + cos(0, 90)) / 3, (cos(0, 10) + cos(10, 20) + cos(10, 90)) / 3,
             (cos(0, 20) + cos(10, 20) + cos(20, 90)) / 3, (cos(0, 90) + cos(10, 90) + cos(20, 90)) / 3]
        ref = sorted(h)[1] / 2 + sorted(h)[2] / 2
        pen = noise.hub_penalties(self.vectors(), lab.Scale(*SCALE), 10)
        for got, mine in zip(pen, h):
            self.assertAlmostEqual(got, max(0.0, mine - ref), places=6)
        self.assertEqual(pen, noise.hub_penalties(self.vectors(), lab.Scale(*SCALE), 3))  # 3 others: the same

    def test_a_penalty_is_never_negative_and_small_worlds_are_fine(self):
        self.assertTrue(all(p >= 0.0 for p in noise.hub_penalties(self.vectors(), lab.Scale(*SCALE), 2)))
        self.assertEqual(noise.hub_penalties([angle(0)], lab.Scale(*SCALE), 5), [0.0])
        self.assertEqual(noise.hub_penalties([], lab.Scale(*SCALE), 5), [])

    def test_the_lookup_receives_lowered_cosines_never_raised(self):
        engine = engine_of(HUBS, 30)
        raw = cosines_of(engine)
        pen = noise.hub_penalties(engine.base.vectors, engine.scale, 2)
        noise.lookup(engine, raw, dict(noise.DEFAULTS, hub=1.0, k=2))
        for was, now in zip(raw, engine.seen):
            self.assertLessEqual(now, was)
        self.assertAlmostEqual(engine.seen[1], raw[1] - pen[1], places=9)
        for i in (0, 2, 3):  # float32 rounding may leave a hair of penalty on the tie at the median
            self.assertAlmostEqual(engine.seen[i], raw[i], places=7)
        noise.lookup(engine, raw, dict(noise.DEFAULTS, hub=3.0, k=2))
        self.assertAlmostEqual(engine.seen[1], raw[1] - 3 * pen[1], places=9)
        noise.lookup(engine, raw, dict(noise.DEFAULTS, hub=0.0))
        self.assertEqual(engine.seen, raw)

    def test_the_penalty_can_push_a_memory_out_of_the_lookup(self):
        engine = engine_of(HUBS, 10)  # b is the best, a and c close behind
        raw = cosines_of(engine)
        self.assertEqual([m for m, _ in noise.lookup(engine, raw, None)["delivered"]][0], "b")
        lowered = noise.lookup(engine, raw, dict(noise.DEFAULTS, hub=50.0, k=2))
        self.assertNotIn("b", [m for m, _ in lowered["delivered"]])


class ZTest(unittest.TestCase):
    def test_z_by_hand(self):
        z = noise.zscores([1.0, 0.0, 0.0, 0.0])
        sd = math.sqrt(0.1875)  # the population standard deviation
        self.assertAlmostEqual(z[0], 0.75 / sd)
        self.assertAlmostEqual(z[1], -0.25 / sd)
        self.assertAlmostEqual(sum(z), 0.0)

    def test_a_standard_deviation_of_zero_gives_zero_for_all(self):
        self.assertEqual(noise.zscores([0.5, 0.5, 0.5]), [0.0, 0.0, 0.0])
        self.assertEqual(noise.zscores([0.3]), [0.0])
        self.assertEqual(noise.zscores([]), [])


class TopAndEachTest(unittest.TestCase):
    """A query at 5 degrees: z of a is 1.1818 and of b 1.1668 (the others are below 0.5)."""

    def setUp(self):
        self.engine = engine_of(NEAR, 5)
        self.raw = cosines_of(self.engine)
        self.base = self.engine.lookup(self.raw)

    def settings(self, **given):
        return dict(noise.DEFAULTS, **given)

    def test_z_of_the_world_for_the_query(self):
        z = noise.zscores(self.raw)
        self.assertAlmostEqual(z[0], 1.1818, places=3)
        self.assertAlmostEqual(z[1], 1.1668, places=3)
        self.assertLess(max(z[2:]), 0.5)

    def test_without_settings_the_lookup_is_the_products(self):
        for given in (None, {}, self.settings()):
            self.assertEqual(noise.lookup(self.engine, self.raw, given), self.base)

    def test_top_silences_the_lookup_when_nothing_stands_out(self):
        silent = noise.lookup(self.engine, self.raw, self.settings(top=1.5))
        self.assertEqual((silent["delivered"], silent["added"], silent["items"]), ([], [], []))
        self.assertEqual(noise.lookup(self.engine, self.raw, self.settings(top=1.18)), self.base)  # 1.1818 passes
        self.assertEqual(noise.lookup(self.engine, self.raw, self.settings(top=1.19))["items"], [])

    def test_top_never_adds_anything_and_a_flat_query_is_silent(self):
        flat = noise.lookup(self.engine, [0.5] * 6, self.settings(top=0.1))  # sd 0: z is 0 for all, 0 < 0.1
        self.assertEqual(flat["items"], [])
        self.assertEqual(noise.lookup(self.engine, [0.5] * 6, self.settings(top=-1.0))["items"],
                         self.engine.lookup([0.5] * 6)["items"])

    def test_each_keeps_only_the_delivered_that_stand_out(self):
        self.assertEqual([m for m, _ in self.base["items"]], ["a", "b"])
        self.assertEqual([m for m, _ in noise.lookup(self.engine, self.raw, self.settings(each=1.0))["items"]], ["a", "b"])
        self.assertEqual([m for m, _ in noise.lookup(self.engine, self.raw, self.settings(each=1.17))["items"]], ["a"])
        self.assertEqual(noise.lookup(self.engine, self.raw, self.settings(each=1.5))["items"], [])

    def test_each_cuts_linked_additions_too(self):
        engine = engine_of(dict(NEAR, g=62), 5)  # g is no hit itself but a neighbour of b: linked in
        raw = cosines_of(engine)
        product = engine.lookup(raw)
        self.assertEqual([m for m, _ in product["items"]], ["a", "b", "g"])
        self.assertEqual([a["id"] for a in product["added"]], ["g"])
        z = noise.zscores(raw)
        kept = noise.lookup(engine, raw, self.settings(each=z[1] - 0.01))
        self.assertEqual([m for m, _ in kept["items"]], ["a", "b"])
        self.assertEqual(kept["added"], [])
        self.assertEqual(kept["order"], product["order"])  # the ranking itself is not touched

    def test_both_together_top_first(self):
        both = noise.lookup(self.engine, self.raw, self.settings(top=1.5, each=0.0))
        self.assertEqual(both["items"], [])
        both = noise.lookup(self.engine, self.raw, self.settings(top=1.0, each=1.17))
        self.assertEqual([m for m, _ in both["items"]], ["a"])

    def test_z_is_taken_from_the_cosines_before_the_penalty(self):
        engine = engine_of(HUBS, 10)
        raw = cosines_of(engine)
        pen = noise.hub_penalties(engine.base.vectors, engine.scale, 2)
        settings = self.settings(hub=50.0, k=2, each=0.0)
        got = noise.lookup(engine, raw, settings)
        shifted = [c - 50.0 * p for c, p in zip(raw, pen)]
        z = noise.zscores(raw)  # not noise.zscores(shifted)
        position = {mid: i for i, mid in enumerate(engine.ids)}
        want = noise.restrict(engine.lookup(shifted), {m for m, _ in engine.lookup(shifted)["items"]
                                                       if z[position[m]] >= 0.0})
        self.assertEqual(got, want)
        self.assertNotEqual(noise.zscores(shifted), z)


class SessionPartTest(unittest.TestCase):
    FOUND = [("a", 0.8), ("b", 0.6), ("c", 0.5)]

    def keep(self, shown, position=2, **given):
        return noise.keep_session_part(self.FOUND, set(shown), dict(noise.DEFAULTS, **given), position)

    def test_without_filters_only_the_shown_are_skipped(self):
        self.assertEqual(self.keep({"a"}), [("b", 0.6), ("c", 0.5)])
        self.assertEqual(noise.keep_session_part(self.FOUND, {"a"}, None, 2), [("b", 0.6), ("c", 0.5)])
        self.assertEqual(self.keep(set()), self.FOUND)

    def test_lead_when_the_best_memory_is_already_shown(self):
        self.assertEqual(self.keep({"a"}, lead=0.7), [("b", 0.6)])  # .56: b stays, c (.5) goes
        self.assertEqual(self.keep({"a"}, lead=0.5), [("b", 0.6), ("c", 0.5)])
        self.assertEqual(self.keep({"a"}, lead=0.8), [])  # .64
        self.assertEqual(self.keep({"a"}, lead=1.01), [])  # above 1 keeps nothing new

    def test_lead_does_nothing_when_the_best_memory_is_new(self):
        self.assertEqual(self.keep({"c"}, lead=1.01), [("a", 0.8), ("b", 0.6)])
        self.assertEqual(self.keep({"b"}, lead=0.99), [("a", 0.8), ("c", 0.5)])
        self.assertEqual(noise.keep_session_part([], {"a"}, dict(noise.DEFAULTS, lead=0.9), 2), [])

    def test_cap_counts_new_memories_from_the_second_turn_on(self):
        self.assertEqual(self.keep(set(), position=1, cap=1), self.FOUND)  # the first turn is not capped
        self.assertEqual(self.keep(set(), position=2, cap=1), [("a", 0.8)])
        self.assertEqual(self.keep({"a"}, position=3, cap=1), [("b", 0.6)])  # a is skipped first, then the cap
        self.assertEqual(self.keep({"a"}, position=3, cap=5), [("b", 0.6), ("c", 0.5)])

    def test_the_order_is_skip_then_lead_then_cap(self):
        found = [("a", 0.8), ("b", 0.7), ("c", 0.65), ("d", 0.3)]
        got = noise.keep_session_part(found, {"a"}, dict(noise.DEFAULTS, lead=0.75, cap=2), 2)
        self.assertEqual(got, [("b", 0.7), ("c", 0.65)])  # d is under .6 (lead), then the cap of 2 leaves b and c
        got = noise.keep_session_part(found, {"a"}, dict(noise.DEFAULTS, lead=0.9, cap=2), 2)
        self.assertEqual(got, [])


class StatisticsTest(unittest.TestCase):
    def test_quantiles(self):
        self.assertEqual(noise.quantile([4, 1, 3, 2], 0.5), 2.5)
        self.assertEqual(noise.quantile([1, 2, 3, 4], 0.25), 1.75)
        self.assertEqual(noise.quantile([1, 2, 3, 4], 0.75), 3.25)
        self.assertEqual(noise.quantile([7], 0.25), 7)
        self.assertEqual(noise.quantile([1, 3], 0.0), 1)
        self.assertEqual(noise.quantile([1, 3], 1.0), 3)
        self.assertIsNone(noise.quantile([], 0.5))

    def test_auc(self):
        self.assertEqual(noise.auc([3, 4], [1, 2]), 1.0)
        self.assertEqual(noise.auc([1, 2], [3, 4]), 0.0)
        self.assertEqual(noise.auc([1], [1]), 0.5)
        self.assertEqual(noise.auc([2, 1], [1, 2]), 0.5)  # one win, one loss, two ties: 2 / 4
        self.assertEqual(noise.auc([3, 1], [2]), 0.5)
        self.assertIsNone(noise.auc([], [1]))
        self.assertIsNone(noise.auc([1], []))


if __name__ == "__main__":
    unittest.main()
