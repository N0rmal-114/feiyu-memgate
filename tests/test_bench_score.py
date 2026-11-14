"""The benchmark scorer, checked on a tiny hand-made dataset whose numbers were worked out by hand."""
import importlib.util
import json
import os
import tempfile
import unittest

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench", "score.py")
_spec = importlib.util.spec_from_file_location("bench_score", _PATH)
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)

MEMORIES = {"m00%d" % i: {"text": "memory %d" % i, "date": "2025-01-0%d" % i, "source": "private", "kind": "event"}
            for i in range(1, 6)}
QUERIES = {
    "q1": {"query": "a", "expect": ["m001"], "type": "lexical"},
    "q2": {"query": "b", "expect": ["m002", "m003"], "type": "paraphrase"},
    "q3": {"query": "c", "expect": ["m004"], "type": "updated", "stale": ["m005"]},
    "q4": {"query": "d", "expect": [], "type": "quiet"},
    "q5": {"query": "e", "expect": ["m001"], "type": "updated", "stale": ["m002"]},
}


def items(*ids):
    return [{"id": i, "score": round(1.0 - 0.1 * n, 2)} for n, i in enumerate(ids)]


def make_result(**changes):
    result = {
        "system": "toy", "version": "1", "mode": "test", "how": "hand made",
        "ingest_seconds": 2.5, "llm_tokens": 100, "llm_cost_cny": 0.5, "notes": ["a note"],
        "answers": {
            # q1: right memory second -> hit1 0, hit5 1, rr 0.5; deployed one wrong item
            "q1": {"ranked": items("m002", "m001"), "deployed": items("m002"), "ms": 10},
            # q2: two expected, both in the top 3 -> everything 1; deployed 2 items, one wrong
            "q2": {"ranked": items("m003", "m001", "m002"), "deployed": items("m003", "m001"), "ms": 20},
            # q3: newer first -> fresh-first win; deployed exactly the newer one
            "q3": {"ranked": items("m004", "m005"), "deployed": items("m004"), "ms": 30},
            # q4: quiet and nothing returned
            "q4": {"ranked": items("m001"), "deployed": [], "ms": 40},
            # q5: stale first, newer at rank 5 -> rr 0.2, fresh-first loss; nothing deployed
            "q5": {"ranked": items("m002", "m003", "m004", "m005", "m001"), "deployed": [], "ms": 50},
        },
    }
    result.update(changes)
    return result


class RetrievalMetricsTest(unittest.TestCase):
    def test_rank_of(self):
        self.assertEqual(score.rank_of(["a", "b", "c"], {"c", "z"}), 3)
        self.assertIsNone(score.rank_of(["a"], {"z"}))

    def test_single_expected(self):
        m = score.retrieval_metrics(["x", "y", "m001"], ["m001"])
        self.assertEqual((m["hit1"], m["hit5"], m["rr"], m["recall5"]), (0.0, 1.0, 1 / 3, 1.0))

    def test_two_expected_partial_recall(self):
        # one of two expected is at rank 1, the other beyond the top 5
        ids = ["m001", "a", "b", "c", "d", "m002"]
        m = score.retrieval_metrics(ids, ["m001", "m002"])
        self.assertEqual((m["hit1"], m["hit5"], m["rr"], m["recall5"]), (1.0, 1.0, 1.0, 0.5))

    def test_beyond_ten_is_a_miss(self):
        ids = ["a%d" % i for i in range(10)] + ["m001"]
        m = score.retrieval_metrics(ids, ["m001"])
        self.assertEqual((m["hit5"], m["rr"], m["recall5"]), (0.0, 0.0, 0.0))

    def test_fresh_first(self):
        self.assertTrue(score.fresh_first(["n", "o"], "n", ["o"]))
        self.assertFalse(score.fresh_first(["o", "n"], "n", ["o"]))
        self.assertTrue(score.fresh_first(["n"], "n", ["o"]))   # stale missing, newer present: win
        self.assertFalse(score.fresh_first(["o"], "n", ["o"]))  # newer missing: loss
        self.assertFalse(score.fresh_first([], "n", ["o"]))     # both missing: loss

    def test_delivered_fresh_first(self):
        f = score.delivered_fresh_first
        self.assertTrue(f(["n", "o"], "n", ["o"]))
        self.assertFalse(f(["o", "n"], "n", ["o"]))
        self.assertTrue(f(["n"], "n", ["o"]))      # stale not delivered: win
        self.assertTrue(f(["x", "n", "y"], "n", ["o"]))
        self.assertIsNone(f(["o"], "n", ["o"]))    # the newer one was not delivered: no verdict
        self.assertIsNone(f([], "n", ["o"]))
        self.assertTrue(f(["n", "o1", "o2"], "n", ["o1", "o2"]))
        self.assertFalse(f(["o2", "n", "o1"], "n", ["o1", "o2"]))  # any stale one before the newer loses
        self.assertTrue(f([None, "n"], "n", ["o"]))  # an id-less item is neither

    def test_percentile_is_nearest_rank(self):
        values = [50, 10, 40, 20, 30]
        self.assertEqual(score.percentile(values, 50), 30)
        self.assertEqual(score.percentile(values, 95), 50)
        self.assertEqual(score.percentile(values, 20), 10)
        self.assertIsNone(score.percentile([], 50))


class ScoreSystemTest(unittest.TestCase):
    def setUp(self):
        self.scored = score.score_system(make_result(), QUERIES)
        self.overall = self.scored["overall"]

    def test_overall_ranking_metrics(self):
        o = self.overall
        self.assertAlmostEqual(o["hit1"], 2 / 4)            # q2 and q3
        self.assertAlmostEqual(o["hit5"], 1.0)
        self.assertAlmostEqual(o["mrr10"], (0.5 + 1 + 1 + 0.2) / 4)
        self.assertAlmostEqual(o["recall5"], 1.0)

    def test_overall_deployed_metrics(self):
        o = self.overall
        self.assertAlmostEqual(o["deployed_hit"], 2 / 4)    # q2 and q3
        self.assertAlmostEqual(o["noise"], (1.0 + 0.5 + 0.0) / 3)  # q5 returned nothing, so it is skipped
        self.assertAlmostEqual(o["avg_items"], (1 + 2 + 1 + 0 + 0) / 5)
        self.assertAlmostEqual(o["quiet_rate"], 1.0)
        self.assertAlmostEqual(o["fresh_first"], 0.5)

    def test_delivered_fresh_first_counts_only_questions_with_the_newer_memory_delivered(self):
        # q3 delivered exactly the newer memory (win); q5 delivered nothing (not counted); the ranked
        # fresh_first of q3/q5 (1 win of 2) is unchanged by this metric.
        u = self.scored["by_type"]["updated"]
        self.assertEqual((u["delivered_fresh_first_questions"], u["delivered_fresh_first_wins"]), (1, 1))
        self.assertAlmostEqual(u["delivered_fresh_first"], 1.0)
        self.assertAlmostEqual(u["fresh_first"], 0.5)
        self.assertEqual(self.overall["delivered_fresh_first_questions"], 1)
        result = make_result()
        result["answers"]["q3"]["deployed"] = items("m005", "m004")                          # stale before newer
        result["answers"]["q5"]["deployed"] = items("m001")                                  # newer alone
        scored = score.score_system(result, QUERIES)["by_type"]["updated"]
        self.assertEqual((scored["delivered_fresh_first_questions"], scored["delivered_fresh_first_wins"]), (2, 1))
        self.assertAlmostEqual(scored["delivered_fresh_first"], 0.5)
        result["answers"]["q3"]["deployed"] = []
        result["answers"]["q5"]["deployed"] = []
        none = score.score_system(result, QUERIES)["by_type"]["updated"]
        self.assertEqual(none["delivered_fresh_first_questions"], 0)
        self.assertIsNone(none["delivered_fresh_first"])

    def test_latency(self):
        self.assertEqual(self.overall["latency_p50_ms"], 30)
        self.assertEqual(self.overall["latency_p95_ms"], 50)

    def test_per_type(self):
        t = self.scored["by_type"]
        self.assertEqual(t["lexical"]["hit1"], 0.0)
        self.assertAlmostEqual(t["lexical"]["mrr10"], 0.5)
        self.assertAlmostEqual(t["lexical"]["noise"], 1.0)
        self.assertEqual(t["paraphrase"]["recall5"], 1.0)
        self.assertAlmostEqual(t["updated"]["hit1"], 0.5)
        self.assertAlmostEqual(t["updated"]["mrr10"], (1 + 0.2) / 2)
        self.assertAlmostEqual(t["updated"]["fresh_first"], 0.5)
        self.assertAlmostEqual(t["updated"]["noise"], 0.0)  # only q3 returned something, and it was right
        self.assertEqual(t["quiet"]["quiet_rate"], 1.0)
        self.assertEqual(t["quiet"]["avg_items"], 0.0)
        self.assertNotIn("hit1", t["quiet"])

    def test_meta_is_carried(self):
        self.assertEqual(self.scored["ingest_seconds"], 2.5)
        self.assertEqual(self.scored["llm_tokens"], 100)
        self.assertEqual(self.scored["notes"], ["a note"])

    def test_quiet_leak_lowers_quiet_rate(self):
        result = make_result()
        result["answers"]["q4"]["deployed"] = items("m001")
        self.assertEqual(score.score_system(result, QUERIES)["overall"]["quiet_rate"], 0.0)

    def test_noise_is_none_when_nothing_was_ever_returned(self):
        result = make_result()
        for answer in result["answers"].values():
            answer["deployed"] = []
        o = score.score_system(result, QUERIES)["overall"]
        self.assertIsNone(o["noise"])
        self.assertEqual(o["avg_items"], 0.0)

    def test_null_id_counts_as_noise(self):
        result = make_result()
        result["answers"]["q3"]["deployed"] = [{"id": "m004", "score": 0.9}, {"id": None, "score": 0.5}]
        t = score.score_system(result, QUERIES)["by_type"]["updated"]
        self.assertAlmostEqual(t["noise"], 0.5)


LONG_MEMORIES = {mid: dict(m, text="x" * length) for (mid, m), length in zip(
    MEMORIES.items(), (10, 20, 30, 40, 50))}  # mean length 30


class DeliveredMetricsTest(unittest.TestCase):
    """precision, delivered F1, delivered characters and the Hit@k curve, worked out by hand."""

    def setUp(self):
        self.scored = score.score_system(make_result(), QUERIES, LONG_MEMORIES)
        self.overall = self.scored["overall"]

    def test_precision_is_one_minus_noise_over_questions_that_returned_something(self):
        # answerable q1 (0 of 1 right), q2 (1 of 2 right), q3 (1 of 1 right); q5 returned nothing
        self.assertAlmostEqual(self.overall["precision"], (0.0 + 0.5 + 1.0) / 3)
        self.assertEqual(self.overall["precision_questions"], 3)
        self.assertAlmostEqual(self.overall["precision"], 1.0 - self.overall["noise"])

    def test_delivered_f1_is_the_harmonic_mean_of_hit_and_precision(self):
        # deployed hit 2/4 = 0.5, precision 0.5 -> 0.5
        self.assertAlmostEqual(self.overall["delivered_f1"], 0.5)
        self.assertAlmostEqual(score.harmonic_mean(1.0, 0.25), 2 * 0.25 / 1.25)
        self.assertEqual(score.harmonic_mean(0.0, 0.0), 0.0)
        self.assertIsNone(score.harmonic_mean(0.5, None))

    def test_precision_and_f1_are_none_when_nothing_was_returned(self):
        result = make_result()
        for answer in result["answers"].values():
            answer["deployed"] = []
        o = score.score_system(result, QUERIES, LONG_MEMORIES)["overall"]
        self.assertIsNone(o["precision"])
        self.assertIsNone(o["delivered_f1"])
        self.assertEqual(o["avg_chars"], 0.0)

    def test_avg_chars_sums_the_delivered_texts_over_all_questions(self):
        # q1 m002 (20), q2 m003 + m001 (30 + 10), q3 m004 (40), q4 and q5 nothing: 100 / 5 questions
        self.assertAlmostEqual(self.overall["avg_chars"], 20.0)
        self.assertEqual(self.overall["delivered_null_items"], 0)

    def test_id_less_item_counts_with_the_mean_memory_length(self):
        result = make_result()
        result["answers"]["q3"]["deployed"] = [{"id": "m004", "score": 0.9}, {"id": None, "score": 0.5}]
        o = score.score_system(result, QUERIES, LONG_MEMORIES)["overall"]
        self.assertAlmostEqual(o["avg_chars"], (100 + 30) / 5)  # the null item stands in for 30 chars
        self.assertEqual(o["delivered_null_items"], 1)

    def test_avg_chars_is_absent_without_the_memory_set(self):
        self.assertNotIn("avg_chars", score.score_system(make_result(), QUERIES)["overall"])

    def test_hit_at_k_curve(self):
        # first expected memory is at rank 2 (q1), 1 (q2), 1 (q3), 5 (q5)
        self.assertEqual(self.overall["hit_at_k"], [0.5, 0.75, 0.75, 0.75] + [1.0] * 6)
        self.assertEqual(len(self.overall["hit_at_k"]), 10)
        self.assertAlmostEqual(self.overall["hit_at_k"][4], self.overall["hit5"])
        self.assertAlmostEqual(self.overall["hit_at_k"][0], self.overall["hit1"])

    def test_hit_curve_helper(self):
        self.assertEqual(score.hit_curve([1, None, 3], max_k=3), [1 / 3, 1 / 3, 2 / 3])
        self.assertIsNone(score.hit_curve([]))

    def test_family_and_kind_come_from_the_mapping(self):
        systems = {"feiyu-memgate-x": {"kind": "hybrid", "llm": False, "experiment": False},
                   "toy": {"kind": "keyword", "llm": True, "experiment": True,
                           "label": {"zh": "玩具", "en": "Toy"}}}
        mine = score.score_system(make_result(system="feiyu-memgate-x"), QUERIES, LONG_MEMORIES, systems)
        other = score.score_system(make_result(), QUERIES, LONG_MEMORIES, systems)
        self.assertEqual((mine["family"], mine["kind"], mine["experiment"]), ("feiyu-memgate", "hybrid", False))
        self.assertEqual((other["family"], other["kind"], other["experiment"]), ("other", "keyword", True))
        self.assertEqual(other["label"]["en"], "Toy")
        unknown = score.score_system(make_result(system="nobody"), QUERIES, LONG_MEMORIES, systems)
        self.assertEqual((unknown["family"], unknown["kind"]), ("other", None))

    def test_markdown_appends_the_new_columns_and_the_footnote(self):
        result = make_result()
        result["answers"]["q3"]["deployed"] = [{"id": "m004", "score": 0.9}, {"id": None, "score": 0.5}]
        summary = {"dataset": {"version": "v1", "memories": 5, "queries": 5,
                               "types": {"lexical": 1, "paraphrase": 1, "updated": 2, "quiet": 1}},
                   "systems": {"toy": score.score_system(result, QUERIES, LONG_MEMORIES)}, "errors": {}}
        text = score.build_markdown(summary)
        header = [l for l in text.splitlines() if l.startswith("| 系统 | Hit@1 | Hit@5 | MRR@10 | Recall@5 | 交付命中")][0]
        cells = [c.strip() for c in header.strip("|").split("|")]
        self.assertEqual(cells[-5:], ["精确率", "交付 F1", "平均字数", "家族", "检索类型"])
        self.assertEqual(cells[:15][-1], "费用 CNY")
        self.assertIn("26†", text)
        self.assertIn("† 平均字数", text)
        self.assertIn("Hit@k 曲线", text)
        updated = text.split("## 更新题（updated）")[1].split("##")[0]
        self.assertIn("| 新旧优先 | 交付中新旧优先 | 题数 |", updated)
        self.assertIn("100.0% | 1/1 |", updated)  # q3 delivered the newer memory first; q5 delivered nothing
        self.assertIn("交付中新旧优先", score.HOW_TO_READ)

    def test_variant_flag_is_carried_and_noted(self):
        systems = {"toy": {"variant": True, "kind": "keyword"}}
        scored = score.score_system(make_result(), QUERIES, None, systems)
        self.assertTrue(scored["variant"])
        self.assertFalse(score.score_system(make_result(system="other"), QUERIES, None, systems)["variant"])
        summary = {"dataset": {"version": "v1", "memories": 5, "queries": 5, "types": {}},
                   "systems": {"toy": scored}, "errors": {}}
        self.assertIn("另一档配置", score.build_markdown(summary))


class ValidateTest(unittest.TestCase):
    def check(self, result):
        return score.validate_result(result, MEMORIES, QUERIES)

    def test_good_result(self):
        self.assertEqual(self.check(make_result()), [])

    def test_missing_field(self):
        result = make_result()
        del result["how"]
        self.assertTrue(any("how" in e for e in self.check(result)))

    def test_missing_answer(self):
        result = make_result()
        del result["answers"]["q3"]
        self.assertTrue(any("no answer" in e for e in self.check(result)))

    def test_unknown_memory_and_duplicates(self):
        result = make_result()
        result["answers"]["q1"]["ranked"] = items("m001", "m001")
        result["answers"]["q2"]["deployed"] = items("m999")
        errors = "\n".join(self.check(result))
        self.assertIn("listed twice", errors)
        self.assertIn("unknown memory id", errors)

    def test_too_many_ranked(self):
        result = make_result()
        result["answers"]["q1"]["ranked"] = [{"id": None, "score": 0.1}] * 11
        self.assertTrue(any("at most 10" in e for e in self.check(result)))

    def test_null_ids_may_repeat(self):
        result = make_result()
        result["answers"]["q1"]["ranked"] = [{"id": None, "score": 0.2}, {"id": None, "score": 0.1}]
        self.assertEqual(self.check(result), [])

    def test_bad_score_and_ms(self):
        result = make_result()
        result["answers"]["q1"]["ranked"] = [{"id": "m001", "score": "high"}]
        result["answers"]["q2"]["ms"] = -1
        errors = "\n".join(self.check(result))
        self.assertIn("q1.ranked[0]", errors)
        self.assertIn("q2.ms", errors)


class RunTest(unittest.TestCase):
    def test_run_writes_summaries_and_reports_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, raw, out = (os.path.join(tmp, n) for n in ("data", "raw", "out"))
            os.makedirs(data)
            os.makedirs(raw)
            with open(os.path.join(data, "memories.json"), "w", encoding="utf-8") as f:
                json.dump(MEMORIES, f)
            with open(os.path.join(data, "queries.json"), "w", encoding="utf-8") as f:
                json.dump(QUERIES, f)
            with open(os.path.join(raw, "toy.json"), "w", encoding="utf-8") as f:
                json.dump(make_result(), f)
            broken = make_result(system="broken")
            del broken["answers"]["q1"]
            with open(os.path.join(raw, "broken.json"), "w", encoding="utf-8") as f:
                json.dump(broken, f)
            summary, problems = score.run(data, raw, out)
            self.assertEqual(list(summary["systems"]), ["toy"])
            self.assertEqual(len(problems), 1)
            self.assertIn("broken.json", problems[0])
            with open(os.path.join(out, "summary.json"), encoding="utf-8") as f:
                self.assertAlmostEqual(json.load(f)["systems"]["toy"]["overall"]["hit5"], 1.0)
            with open(os.path.join(out, "summary.md"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("| toy |", text)
            self.assertIn("怎么读", text)
            self.assertIn("broken.json", text)

    def test_markdown_sorts_by_hit5(self):
        weak = make_result(system="weak")
        weak["answers"]["q1"]["ranked"] = items("m003")  # misses q1 completely
        summary = {"dataset": {"version": "v1", "memories": 5, "queries": 5, "types": {}},
                   "systems": {"toy": score.score_system(make_result(), QUERIES),
                               "weak": score.score_system(weak, QUERIES)},
                   "errors": {}}
        text = score.build_markdown(summary)
        table = text.split("## 总表")[1]
        self.assertLess(table.index("| toy |"), table.index("| weak |"))


if __name__ == "__main__":
    unittest.main()
