import json
import unittest
from pathlib import Path

from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.core.pool import NULL_CTL
from feiyu_memgate.tools import evaluate

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "eval"


def load(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class ScoreTests(unittest.TestCase):
    def test_score_rows(self):
        found = {"q1": [("m1", 0.7), ("m9", 0.5)], "q2": [("m3", 0.4)], "none1": [("m2", 0.45)]}
        expect = {"q1": ["m1"], "q2": ["m3"], "none1": []}
        rows = {(r.threshold, r.limit): r for r in evaluate.score_rows(found, expect, (0.4, 0.5), (3,))}
        self.assertEqual(rows[(0.4, 3)].hit, 1.0)
        self.assertEqual(rows[(0.5, 3)].hit, 0.5)
        self.assertEqual(rows[(0.5, 3)].quiet, 1.0)
        self.assertEqual(rows[(0.4, 3)].quiet, 0.0)
        self.assertAlmostEqual(rows[(0.4, 3)].noise, 2 / 4)

    def test_limit_cuts_the_list_before_the_threshold(self):
        found = {"q1": [("x", 0.9), ("m1", 0.8)]}
        rows = evaluate.score_rows(found, {"q1": ["m1"]}, (0.5,), (1, 2))
        by_limit = {r.limit: r for r in rows}
        self.assertEqual(by_limit[1].hit, 0.0)
        self.assertEqual(by_limit[2].hit, 1.0)

    def test_best_row_prefers_low_noise(self):
        R = evaluate.Row
        rows = [R(0.4, 3, 1.0, 0.5, 1.0), R(0.5, 3, 0.8, 0.2, 0.8), R(0.6, 3, 0.7, 0.1, 1.0), R(0.7, 3, 0.8, 0.1, 0.9)]
        best = evaluate.best_row(rows)
        self.assertEqual((best.threshold, best.limit), (0.7, 3))  # tie on hit: higher threshold

    def test_best_row_never_recommends_zero_hit(self):
        self.assertIsNone(evaluate.best_row([evaluate.Row(0.9, 3, 0.0, 0.0, 1.0)]))

    def test_summary_files_never_count(self):
        found = {"q1": [("a/.overview.md", 0.95), ("a/.abstract.md", 0.9), ("m1", 0.6), ("m9", 0.5)]}
        rows = evaluate.score_rows(found, {"q1": ["m1"]}, (0.4,), (1, 2))
        by_limit = {r.limit: r for r in rows}
        self.assertEqual(by_limit[1].hit, 1.0)  # summaries took no limit slot
        self.assertEqual(by_limit[1].noise, 0.0)
        self.assertEqual(by_limit[2].noise, 0.5)  # only m9, not the summaries

    def test_unsorted_input_scores_like_sorted(self):
        sorted_found = {"q1": [("m1", 0.9), ("x", 0.6), ("y", 0.5)], "n": [("z", 0.7)]}
        shuffled = {"q1": [("y", 0.5), ("m1", 0.9), ("x", 0.6)], "n": [("z", 0.7)]}
        expect = {"q1": ["m1"], "n": []}
        self.assertEqual(evaluate.score_rows(sorted_found, expect), evaluate.score_rows(shuffled, expect))

    def test_best_row_none(self):
        self.assertIsNone(evaluate.best_row([evaluate.Row(0.4, 3, 1.0, 0.5, 0.5)]))

    def test_grid_reaches_high_score_lines(self):
        self.assertEqual(evaluate.THRESHOLDS[-5:], (0.70, 0.75, 0.80, 0.85, 0.90))
        self.assertEqual(list(evaluate.THRESHOLDS), sorted(evaluate.THRESHOLDS))

    def test_score_spread(self):
        found = {"q1": [("m1", 0.8), ("x", 0.7)], "q2": [("y", 0.75), ("m3", 0.6)], "q3": [("z", 0.5)],
                 "none1": [("w", 0.72), ("a/.overview.md", 0.99)]}
        expect = {"q1": ["m1"], "q2": ["m3"], "q3": ["m4"], "none1": []}
        spread = evaluate.score_spread(found, expect)
        self.assertEqual((spread.wanted_low, spread.wanted_mid), (0.6, 0.7))  # q3 found nothing it wanted
        self.assertEqual((spread.other_mid, spread.other_high), (0.71, 0.75))  # the summary file never counts
        line = evaluate._spread_line(spread)
        self.assertIn("最低 0.60", line)
        self.assertIn("最高 0.75", line)

    def test_score_spread_with_nothing_found(self):
        spread = evaluate.score_spread({}, {"q1": ["m1"]})
        self.assertEqual(spread, evaluate.Spread(None, None, None, None))
        self.assertIn("没有", evaluate._spread_line(spread))


class FixtureTests(unittest.TestCase):
    def test_fixtures_are_consistent(self):
        memories, queries = load("memories.json"), load("queries.json")
        self.assertGreaterEqual(len(memories), 40)
        self.assertGreaterEqual(sum(1 for q in queries.values() if not q["expect"]), 8)
        for q in queries.values():
            for ref in q["expect"]:
                self.assertIn(ref, memories)


class RunEvalTests(unittest.TestCase):
    def test_runs_against_inmemory_and_prints_a_table(self):
        out = []
        result = evaluate.run_eval(InMemoryBackend(), load("memories.json"), load("queries.json"), out=out.append)
        self.assertEqual(len(result["rows"]), len(evaluate.THRESHOLDS) * len(evaluate.LIMITS))
        self.assertTrue(any("分数线" in line for line in out))
        self.assertTrue(any("分数分布" in line for line in out))
        # Some line must find something: the questions share words with their memories.
        self.assertGreater(max(r.hit for r in result["rows"]), 0)

    def test_seed_false_uses_what_is_already_there(self):
        memories = load("memories.json")
        backend = InMemoryBackend()
        for ref, text in memories.items():
            backend.put(text, ref=ref)
        out = []
        result = evaluate.run_eval(backend, memories, load("queries.json"), seed=False, out=out.append)
        self.assertGreater(max(r.hit for r in result["rows"]), 0)
        self.assertFalse(any("已写入" in line for line in out))

    def test_backend_without_put_is_a_clear_error(self):
        class ReadOnly:
            def find(self, query, limit, timeout=None, ctl=None):
                return []
        with self.assertRaises(ValueError) as caught:
            evaluate.run_eval(ReadOnly(), {"a": "x"}, {}, out=lambda s: None)
        self.assertIn("seed=False", str(caught.exception))
        # and it works when told not to seed
        result = evaluate.run_eval(ReadOnly(), {"a": "x"}, {"q": {"query": "x", "expect": ["a"]}},
                                   seed=False, out=lambda s: None)
        self.assertIsNone(result["best"])

    def test_the_backend_gets_a_ctl_that_is_never_none(self):
        seen = []

        class Watching:
            def find(self, query, limit, timeout=None, ctl=None):
                seen.append(ctl)
                return []

        evaluate.run_eval(Watching(), {}, {"q": {"query": "x", "expect": []}}, seed=False, out=lambda s: None)
        self.assertEqual(seen, [NULL_CTL])


if __name__ == "__main__":
    unittest.main()
