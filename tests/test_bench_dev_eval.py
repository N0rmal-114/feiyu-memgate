"""bench/dev_eval.py: the development-set evaluation, on a tiny hand-made case and on examples/eval (keyword only)."""
import importlib.util
import os
import sys
import unittest

_BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench")
sys.path.insert(0, _BENCH)  # dev_eval imports bench/score.py by its plain name
_spec = importlib.util.spec_from_file_location("bench_dev_eval", os.path.join(_BENCH, "dev_eval.py"))
dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dev)

QUERIES = {
    "a": {"query": "q", "expect": ["m1", "m2"]},
    "b": {"query": "q", "expect": ["m3"]},
    "n": {"query": "q", "expect": []},
    "o": {"query": "q", "expect": []},
}


def item(*ids):
    return [{"id": i, "score": 1.0 - 0.1 * n} for n, i in enumerate(ids)]


class MetricsTest(unittest.TestCase):
    def test_numbers_worked_out_by_hand(self):
        answers = {
            "a": {"ranked": item("x", "m1"), "deployed": item("m1", "x"), "ms": 0},          # hit@5 not @1; precision 1/2
            "b": {"ranked": item("m3"), "deployed": item("m3"), "ms": 0},                    # perfect
            "n": {"ranked": item("x"), "deployed": [], "ms": 0},                             # quiet
            "o": {"ranked": item("x"), "deployed": item("x"), "ms": 0},                      # not quiet
        }
        m = dev.metrics(answers, QUERIES)
        self.assertEqual((m["hit1"], m["hit5"], m["deployed_hit"]), (0.5, 1.0, 1.0))
        self.assertAlmostEqual(m["precision"], (0.5 + 1.0) / 2)
        self.assertAlmostEqual(m["f1"], 2 * 1.0 * 0.75 / 1.75)
        self.assertEqual(m["quiet"], 0.5)
        self.assertAlmostEqual(m["items"], (2 + 1 + 0 + 1) / 4)
        # balanced = (2 answerable x F1 + 2 quiet x quiet rate) / 4
        self.assertAlmostEqual(m["balanced"], (2 * m["f1"] + 2 * 0.5) / 4)

    def test_nothing_returned_has_no_precision_and_scores_zero(self):
        answers = {q: {"ranked": [], "deployed": [], "ms": 0} for q in QUERIES}
        m = dev.metrics(answers, QUERIES)
        self.assertEqual((m["hit5"], m["deployed_hit"], m["quiet"], m["f1"]), (0.0, 0.0, 1.0, 0.0))
        self.assertIsNone(m["precision"])
        self.assertAlmostEqual(m["balanced"], 0.5)  # quiet half of the questions, nothing answered


class SelectionTest(unittest.TestCase):
    def test_ties_go_to_higher_hit_then_lower_min_score_then_lower_ratio(self):
        def m(balanced, hit):
            return {"balanced": balanced, "deployed_hit": hit}
        keys = [
            ((0.3, 0.8), m(0.6, 0.5)),
            ((0.2, 0.8), m(0.6, 0.5)),      # lower min_score beats the line above
            ((0.2, 0.7), m(0.6, 0.5)),      # lower ratio beats the line above
            ((0.2, None), m(0.6, 0.5)),     # stage off counts as the lowest ratio
            ((0.9, 0.9), m(0.6, 0.6)),      # higher deployed hit beats all of them
            ((0.1, 0.5), m(0.5, 0.9)),      # lower balanced loses whatever the rest
        ]
        best = max(keys, key=lambda pm: dev.selection_key(*pm))
        self.assertEqual(best[0], (0.9, 0.9))
        rest = [k for k in keys if k[0] != (0.9, 0.9) and k[1]["balanced"] == 0.6]
        self.assertEqual(max(rest, key=lambda pm: dev.selection_key(*pm))[0], (0.2, None))

    def test_the_grid_is_the_fixed_one(self):
        self.assertEqual(dev.GRID_SCORES[0], 0.05)
        self.assertEqual(dev.GRID_SCORES[-1], 0.8)
        self.assertEqual(len(dev.GRID_SCORES), 16)
        self.assertEqual(dev.GRID_RATIOS[:2], (0.5, 0.55))
        self.assertEqual(dev.GRID_RATIOS[-2:], (0.95, None))
        self.assertEqual(len(dev.grid()), 176)


class OptionsTest(unittest.TestCase):
    def test_parse_options(self):
        self.assertEqual(dev.parse_options(["floor=0.4", "weights=[1, 0.5]", "model=bge"]),
                         {"floor": 0.4, "weights": [1, 0.5], "model": "bge"})
        self.assertEqual(dev.parse_options(None), {})
        with self.assertRaises(SystemExit):
            dev.parse_options(["nonsense"])

    def test_store_specs(self):
        self.assertEqual(dev.store_spec("sqlite")["type"], "sqlite")
        self.assertEqual(dev.store_spec("inmemory")["type"], "inmemory")
        emb = dev.store_spec("embedding", "http://x/v1", "m", {"floor": 0.5})
        self.assertEqual((emb["type"], emb["url"], emb["model"], emb["floor"]), ("embedding", "http://x/v1", "m", 0.5))
        hyb = dev.store_spec("hybrid", "http://x/v1", "m", {"floor": 0.5}, {"weights": [1, 0.5]})
        self.assertEqual([c["type"] for c in hyb["children"]], ["sqlite", "embedding"])
        self.assertEqual(hyb["children"][1]["floor"], 0.5)
        self.assertEqual(hyb["weights"], [1, 0.5])
        with self.assertRaises(SystemExit):
            dev.store_spec("nope")

    def test_stage_list_defaults_and_overrides(self):
        default = dev.stage_list(None)
        self.assertEqual([s["type"] for s in default], list(dev.stages_module.DEFAULT_STAGES))
        tuned = dev.stage_list(None, min_score=0.3, ratio=0.7, gap=0.1)
        by_type = {s["type"]: s for s in tuned}
        self.assertEqual(by_type["min_score"]["value"], 0.3)
        self.assertEqual((by_type["relative_score"]["ratio"], by_type["relative_score"]["gap"]), (0.7, 0.1))
        plain = dev.stage_list("min_score,redact")
        self.assertEqual([s["type"] for s in plain], ["min_score", "redact"])
        added = dev.stage_list("min_score,redact", ratio=0.5)  # relative_score goes right after min_score
        self.assertEqual([s["type"] for s in added], ["min_score", "relative_score", "redact"])
        as_json = dev.stage_list('["min_score", {"type": "relative_score", "ratio": 0.9}]')
        self.assertEqual(as_json[1]["ratio"], 0.9)
        self.assertEqual([s["type"] for s in dev.without_relative(default)],
                         [n for n in dev.stages_module.DEFAULT_STAGES if n != "relative_score"])


class DevSetRunTest(unittest.TestCase):
    """The real examples/eval with the keyword backends: no network. The code, not the numbers."""

    EXAMPLES = os.path.join(_BENCH, "..", "examples", "eval")

    def candidates(self, backend):
        memories, queries = dev.load("memories.json", self.EXAMPLES), dev.load("queries.json", self.EXAMPLES)
        return dev.collect(dev.store_spec(backend), memories, queries), queries

    def test_the_relative_stage_lowers_noise_without_losing_the_answer(self):
        cand, queries = self.candidates("inmemory")
        without = dev.evaluate(cand, [dev.without_relative(dev.stage_list(None, 0.2))], queries)[0][1]
        with_it = dev.evaluate(cand, [dev.stage_list(None, 0.2, 0.7)], queries)[0][1]
        self.assertGreater(with_it["precision"], without["precision"])
        self.assertGreaterEqual(with_it["deployed_hit"], without["deployed_hit"] - 0.1)
        self.assertLessEqual(with_it["items"], without["items"])

    def test_candidates_file_round_trip_gives_the_same_numbers(self):
        import tempfile
        cand, queries = self.candidates("sqlite")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.json")
            dev.save_candidates(path, cand)
            again = dev.load_candidates(path)
        specs = [dev.stage_list(None, 0.3, 0.8)]
        self.assertEqual(dev.evaluate(cand, specs, queries), dev.evaluate(again, specs, queries))

    def test_main_prints_one_row(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = dev.main(["--backend", "inmemory", "--data", self.EXAMPLES, "--min-score", "0.2", "--ratio", "0.6"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("Hit@5", text)
        self.assertIn("min_score=0.2 relative=0.6", text)

    def test_search_writes_the_report_and_names_a_chosen_point(self):
        import contextlib
        import io
        import tempfile
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(out):
            code = dev.main(["--backend", "inmemory", "--data", self.EXAMPLES, "--search", "--out-dir", tmp])
            self.assertEqual(sorted(os.listdir(tmp)), ["inmemory-precise.md", "inmemory.md"])
            with open(os.path.join(tmp, "inmemory-precise.md"), encoding="utf-8") as f:
                written = f.read()
            with open(os.path.join(tmp, "inmemory.md"), encoding="utf-8") as f:
                broad_report = f.read()
        self.assertIn("broad", broad_report)
        self.assertEqual(code, 0)
        self.assertIn("\u9009\u5b9a", written)
        self.assertIn("before:", written)
        self.assertIn("176", written)

    def test_the_held_out_set_is_refused(self):
        with self.assertRaises(SystemExit):
            dev.main(["--backend", "inmemory", "--data", os.path.join(_BENCH, "data")])


if __name__ == "__main__":
    unittest.main()
