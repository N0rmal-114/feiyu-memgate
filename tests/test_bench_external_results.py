"""The scorer, the report and the packer of bench/external, on small hand-made stores and result files."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

_EXT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench", "external")


def _load(name):
    spec = importlib.util.spec_from_file_location("ext_%s_for_tests" % name, os.path.join(_EXT, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ext_score = _load("score")
report = _load("report")
pack = _load("pack")

MEMORIES = {"m1": {"text": "a" * 10}, "m2": {"text": "b" * 30}, "m3": {"text": "c" * 20}, "m4": {"text": "d" * 40}}
QUERIES = {"q1": {"query": "one", "expect": ["m1"], "type": "4-single-hop"},
           "q2": {"query": "two", "expect": ["m2", "m3"], "type": "1-multi-hop"},
           "q3": {"query": "three", "expect": [], "type": "quiet"}}
SIDE = {"questions": {"q1": {"type": "4-single-hop", "no_answer": False},
                      "q2": {"type": "1-multi-hop", "no_answer": False},
                      "q3": {"type": "5-adversarial", "no_answer": True}}}


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f)


def write_store(root, dataset, store):
    folder = os.path.join(root, dataset, store)
    dump(os.path.join(folder, "memories.json"), MEMORIES)
    dump(os.path.join(folder, "queries.json"), QUERIES)
    dump(os.path.join(folder, "external.json"), SIDE)


def item(mid, score=1.0, **more):
    return dict({"id": mid, "score": score}, **more)


def result(system, dataset, stores, good=True, llm=None):
    """A results file: on every store q1 is found first (or missed), q2 found second, q3 delivers one item."""
    answers, worlds = {}, {}
    for s in stores:
        first = "m1" if good else "m4"
        answers[s + "/q1"] = {"ranked": [item(first), item("m4")], "deployed": [item(first)], "ms": 2.0}
        answers[s + "/q2"] = {"ranked": [item("m4"), item("m2")], "deployed": [item("m4"), item("m2")], "ms": 4.0}
        answers[s + "/q3"] = {"ranked": [item("m4")], "deployed": [] if good else [item("m4")], "ms": 6.0}
        usage = {"llm_calls": 4 if llm else 0, "llm_input_tokens": 400 if llm else 0, "llm_output_tokens": 40 if llm else 0,
                 "llm_cached_input_tokens": 100 if llm else 0, "llm_cost_usd": 0.02 if llm else 0, "ingest_seconds": 8.0,
                 "question_seconds": 1.0}
        if llm:
            usage["llm_model"] = llm
        worlds[s] = {"memories": 4, "recall_not_200": 0, "ranked_failed": 0, "unmapped_items": 0, "recall_notes": {},
                     "usage": usage}
    total = ext_score.usage_of(worlds.values(), {"llm_model": llm} if llm else {})
    return {"system": system, "dataset": dataset, "profile": "broad", "llm_model": llm,
            "llm_thinking": "disabled" if llm else None, "embedding_model": None, "stopped": None, "subset": None,
            "failed_worlds": {}, "usage": total, "worlds": worlds, "answers": answers}


class ScorerTest(unittest.TestCase):
    def test_definitions(self):
        memories = {"m1": {"text": "x" * 10}, "m2": {"text": "y" * 30}, "m3": {"text": "z" * 2000},
                    "m4": {"text": "w" * 20}}
        multi = {"expect": ["m1", "m2", "m3"], "type": "t"}
        ans = {"ranked": [item("m4", .9), item("m2", .8), item("m1", .7)],
               "deployed": [item("m4", .9), item("m2", .8)], "ms": 5.0}
        r = ext_score.question_figures(ans, multi, memories, {}, None)
        self.assertEqual((r["hit1"], r["hit5"], r["hit10"]), (0.0, 1.0, 1.0))
        self.assertAlmostEqual(r["evidence10"], 2 / 3)  # a multi-evidence question: 2 of 3 found in the top 10
        self.assertEqual(r["delivered_hit"], 1.0)
        self.assertAlmostEqual(r["precision"], 0.5)
        self.assertEqual((r["items"], r["chars"]), (2, 50))
        self.assertEqual((r["any_over_budget"], r["all_over_budget"]), (1.0, 0.0))  # m3 is over 1500 characters
        single = {"expect": ["m1"], "type": "t"}
        r2 = ext_score.question_figures({"ranked": [], "deployed": [], "ms": 1.0}, single, memories, {}, None)
        self.assertEqual((r2["hit10"], r2["delivered_hit"], r2["items"]), (0.0, 0.0, 0))
        self.assertNotIn("precision", r2)  # nothing delivered: not part of the precision average
        self.assertNotIn("evidence10", r2)  # a single-evidence question is not part of that figure
        summary = ext_score.summarize([r, r2])
        self.assertEqual((summary["questions"], summary["evidence10_n"], summary["precision_n"]), (2, 1, 1))
        self.assertAlmostEqual(summary["hit5"], 0.5)
        self.assertAlmostEqual(summary["precision"], 0.5)

    def test_shortened_items_count_with_their_recorded_length(self):
        memories = {"m1": {"text": "x" * 900}, "m2": {"text": "y" * 30}}
        ans = {"ranked": [item("m1")],
               "deployed": [item("m1", 2, chars=500, shortened=True), item("m2"), item(None, 0)], "ms": 1.0}
        r = ext_score.question_figures(ans, {"expect": ["m1"], "type": "t"}, memories, {}, None)
        self.assertEqual(r["delivered_hit"], 1.0)  # a memory delivered shortened is that memory
        self.assertEqual(r["chars"], 500 + 30 + (900 + 30) / 2)  # recorded length, full length, the mean for no id

    def test_usage_of_a_selection_of_stores(self):
        worlds = [{"usage": {"llm_calls": 2, "llm_cost_usd": 0.25, "llm_model": "m", "note": None}},
                  {"usage": {"llm_calls": 3, "llm_cost_usd": 0.5, "llm_model": "m", "flag": True}}]
        usage = ext_score.usage_of(worlds, {"llm_calls": 99, "llm_cost_usd": 9.0, "llm_model": "m"})
        self.assertEqual((usage["llm_calls"], usage["llm_cost_usd"], usage["llm_model"]), (5, 0.75, "m"))
        self.assertNotIn("flag", usage)

    def test_score_file_and_same_stores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, folder = os.path.join(tmp, "worlds"), os.path.join(tmp, "results", "locomo")
            for store in ("w1", "w2"):
                write_store(root, "locomo", store)
            path = os.path.join(folder, "some-system.json")
            dump(path, result("some-system", "locomo", ["w1", "w2"], llm="some-model"))
            scored = ext_score.score_file(path, root)
            m = scored["metrics"]
            self.assertEqual((m["ALL answerable"]["questions"], m["no-answer"]["questions"]), (4, 2))
            self.assertEqual((m["4-single-hop"]["hit1"], m["1-multi-hop"]["hit1"], m["1-multi-hop"]["hit5"]), (1.0, 0.0, 1.0))
            self.assertEqual(m["no-answer"]["quiet_rate"], 1.0)
            self.assertAlmostEqual(m["ALL answerable"]["precision"], 0.75)
            self.assertEqual((scored["memories"], scored["world_ids"], scored["failed_worlds"]), (8, ["w1", "w2"], []))
            self.assertEqual((scored["usage"]["llm_calls"], scored["llm_thinking"]), (8, "disabled"))
            # restricted to one store: its questions, its memories and its spending only
            only, missing = ext_score.only_for_stores(path, ["w1"])
            self.assertEqual((len(only), missing), (3, []))
            one = ext_score.score_file(path, root, only, "t")
            self.assertEqual((one["world_ids"], one["memories"], one["usage"]["llm_calls"]), (["w1"], 4, 4))
            # a store the file lacks: the file is listed as not covering it, not scored
            text = ext_score.compare_stores(folder, root, ["w1", "w3"], "t")
            self.assertIn("Not covering", text)
            self.assertNotIn("| some-system |", text)
            self.assertIn("| some-system | 1 | 3 |", ext_score.compare_stores(folder, root, ["w1"], "t"))


class ReportTest(unittest.TestCase):
    def test_built_from_the_metrics_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, results = os.path.join(tmp, "worlds"), os.path.join(tmp, "results")
            folder = os.path.join(results, "locomo")
            for store in ("w1", "w2"):
                write_store(root, "locomo", store)
            files = {"feiyu-memgate-sqlite": result("feiyu-memgate-sqlite", "locomo", ["w1", "w2"], good=False),
                     "feiyu-memgate-hybrid": result("feiyu-memgate-hybrid", "locomo", ["w1", "w2"]),
                     "sampled": result("sampled", "locomo", ["w1"], llm="some-model")}
            for name, body in files.items():
                dump(os.path.join(folder, name + ".json"), body)
                dump(os.path.join(folder, name + ".metrics.json"),
                     ext_score.score_file(os.path.join(folder, name + ".json"), root))
            ext_score.compare_stores(folder, root, ["w1"], "first1")
            dump(os.path.join(folder, "latency-summary.json"),
                 {"sampled": {"kind": "measured under load", "world": None, "memories": None, "questions": 3,
                              "p50_ms": 4.0, "p95_ms": 6.0}})
            for name in files:  # the report must not need the per-question files or the stores
                os.remove(os.path.join(folder, name + ".json"))
            with mock.patch.object(report, "DATASETS", ("locomo",)), \
                    mock.patch.object(report, "SAMPLES", {"locomo": [(1, "first1", ["sampled"])]}):
                data = report.build(results)
                registry = {"feiyu-memgate-sqlite": {"label": {"zh": "本项目 sqlite", "en": "own sqlite"}, "kind": "keyword"},
                            "feiyu-memgate-hybrid": {"label": {"zh": "本项目 hybrid", "en": "own hybrid"}, "kind": "hybrid"},
                            "sampled": {"label": {"zh": "抽样的", "en": "Sampled"}, "kind": "extraction"}}
                blocks = {key: table for key, _, table in report.blocks(data, "en", registry)}
            d = data["locomo"]
            self.assertEqual(d["stores"], ["w1", "w2"])
            self.assertEqual(sorted(r["system"] for r in d["full"]), ["feiyu-memgate-hybrid", "feiyu-memgate-sqlite"])
            hybrid = next(r for r in d["full"] if r["system"] == "feiyu-memgate-hybrid")
            self.assertEqual((hybrid["answerable"], hybrid["hit5"], hybrid["quiet_rate"]), (4, 1.0, 1.0))
            self.assertEqual(hybrid["by_type"]["1-multi-hop"]["hit1"], 0.0)
            sample = d["samples"][0]
            self.assertEqual((sample["n"], sample["stores"]), (1, ["w1"]))
            self.assertEqual([r["system"] for r in sample["rows"]], ["sampled", "feiyu-memgate-sqlite", "feiyu-memgate-hybrid"])
            self.assertEqual([r["answerable"] for r in sample["rows"]], [2, 2, 2])
            cost = d["cost"][0]
            self.assertEqual((cost["system"], cost["model"], cost["memories"], cost["calls"]), ("sampled", "some-model", 4, 4))
            self.assertAlmostEqual(cost["usd_per_1000_memories"], 5.0)
            self.assertEqual(set(blocks), {"locomo-full", "locomo-types", "locomo-first1", "cost", "latency"})
            self.assertIn("| **own hybrid** | hybrid | 100.0% | 100.0% |", blocks["locomo-types"])
            self.assertIn("| Sampled | LoCoMo | 1 | 4 | 4 |", blocks["cost"])
            self.assertIn("measured during the parallel runs", blocks["latency"])

    def _results_with_an_audit(self, tmp, audit):
        """Two stores; this project's two reference rows, `other` on both stores, `sampled` (a model) on the first."""
        root, results = os.path.join(tmp, "worlds"), os.path.join(tmp, "results")
        folder = os.path.join(results, "locomo")
        for store in ("w1", "w2"):
            write_store(root, "locomo", store)
        files = {"feiyu-memgate-sqlite": result("feiyu-memgate-sqlite", "locomo", ["w1", "w2"], good=False),
                 "feiyu-memgate-hybrid": result("feiyu-memgate-hybrid", "locomo", ["w1", "w2"]),
                 "other": result("other", "locomo", ["w1", "w2"]),
                 "sampled": result("sampled", "locomo", ["w1"], llm="some-model")}
        for name, body in files.items():
            dump(os.path.join(folder, name + ".json"), body)
            dump(os.path.join(folder, name + ".metrics.json"), ext_score.score_file(os.path.join(folder, name + ".json"), root))
        ext_score.compare_stores(folder, root, ["w1"], "first1")
        dump(os.path.join(folder, "latency-summary.json"),
             {name: {"kind": "measured under load", "world": None, "memories": None, "questions": 3, "p50_ms": 4.0,
                     "p95_ms": 6.0} for name in ("other", "sampled", "feiyu-memgate-hybrid")})
        if audit is not None:
            dump(os.path.join(results, "write-audit.json"), {"threshold": 0.05, "locomo": audit})
        return results

    REGISTRY = {"feiyu-memgate-sqlite": {"label": {"zh": "本项目 sqlite", "en": "own sqlite"}, "kind": "keyword"},
                "feiyu-memgate-hybrid": {"label": {"zh": "本项目 hybrid", "en": "own hybrid"}, "kind": "hybrid"},
                "other": {"label": {"zh": "别的", "en": "Other"}, "kind": "keyword"},
                "sampled": {"label": {"zh": "抽样的", "en": "Sampled"}, "kind": "extraction"}}

    def _build(self, results):
        samples = {"locomo": [(1, "first1", ["sampled"])]}
        with mock.patch.object(report, "DATASETS", ("locomo",)), mock.patch.object(report, "SAMPLES", samples):
            data = report.build(results)
            return data, {key: table for key, _, table in report.blocks(data, "en", self.REGISTRY)}

    def test_a_configuration_that_stored_too_little_is_left_out_of_that_dataset(self):
        audit = {"other": {"stores": 2, "sent": 8, "stored": 3, "counts": "stored", "basis": "record",
                           "reason": "daily-quota"},
                 "sampled": {"stores": 1, "sent": 4, "stored": 2, "counts": "processed", "basis": "record",
                             "reason": "model-output"},
                 "feiyu-memgate-hybrid": {"stores": 2, "sent": 8, "stored": 8, "counts": "stored", "basis": "count"}}
        with tempfile.TemporaryDirectory() as tmp:
            data, blocks = self._build(self._results_with_an_audit(tmp, audit))
        d = data["locomo"]
        self.assertEqual(sorted(r["system"] for r in d["full"]), ["feiyu-memgate-hybrid", "feiyu-memgate-sqlite"])
        self.assertEqual(d["samples"], [])  # a group with nothing left but the reference rows is not shown
        self.assertEqual(d["cost"], [])
        self.assertEqual(sorted(d["latency"]), ["feiyu-memgate-hybrid"])
        self.assertEqual(d["excluded"], ["other", "sampled"])
        other = next(w for w in d["writes"] if w["system"] == "other")
        self.assertEqual((other["stores"], other["sent"], other["stored"], other["reason"], other["excluded"]),
                         (2, 8, 3, "daily-quota", True))
        self.assertNotIn("locomo-first1", blocks)
        self.assertNotIn("Other", blocks["locomo-full"])
        self.assertIn("- Other: 3 of 8 memories stored (37.5%).", blocks["locomo-excluded"])
        self.assertIn("at most 8 memories a day", blocks["locomo-excluded"])
        self.assertIn("| Other | 2 | 3 / 8 |", blocks["writes"])
        self.assertIn("| **own hybrid** | 2 | 8 / 8 |", blocks["writes"])

    def test_a_shortfall_within_the_threshold_stays_in_and_is_listed(self):
        audit = {"other": {"stores": 2, "sent": 100, "stored": 96, "counts": "stored", "basis": "record",
                           "reason": "merged"},
                 "sampled": {"stores": 1, "sent": 4, "stored": None, "counts": "processed", "basis": "record", "failed": 0}}
        with tempfile.TemporaryDirectory() as tmp:
            data, blocks = self._build(self._results_with_an_audit(tmp, audit))
        d = data["locomo"]
        self.assertIn("other", [r["system"] for r in d["full"]])
        self.assertEqual(d["excluded"], [])
        self.assertEqual([r["system"] for r in d["samples"][0]["rows"]][0], "sampled")
        self.assertNotIn("locomo-excluded", blocks)
        self.assertIn("| Other | 2 | 96 / 100 |", blocks["writes"])
        self.assertIn("merged into a similar memory", blocks["writes"])
        self.assertIn("| Sampled | 1 | – / 4 (0 failed) |", blocks["writes"])

    def test_without_an_audit_file_nothing_is_left_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, blocks = self._build(self._results_with_an_audit(tmp, None))
        d = data["locomo"]
        self.assertEqual((d["excluded"], d["writes"]), ([], []))
        self.assertIn("other", [r["system"] for r in d["full"]])
        self.assertNotIn("writes", blocks)

    def test_the_published_audit_covers_every_published_configuration(self):
        """Every configuration with a metrics file has a row in write-audit.json for the same number of stores."""
        audit = report.load(os.path.join(_EXT, "results", "write-audit.json"))
        for dataset in report.DATASETS:
            for name, scored in report.metrics_files(dataset).items():
                row = audit[dataset][report.audited_name(name)]
                self.assertEqual(row["stores"], len(scored["world_ids"]), (dataset, name))
                self.assertEqual(row["sent"], scored["memories"], (dataset, name))

    def test_rows_order_and_tables(self):
        registry = {"feiyu-memgate-sqlite": {"label": {"zh": "本项目 sqlite", "en": "own sqlite"}, "kind": "keyword"},
                    "feiyu-memgate-sqlite-precise": {"label": {"zh": "本项目 sqlite（精确档）", "en": "own sqlite (precise)"},
                                                     "kind": "keyword"},
                    "other": {"label": {"zh": "别的", "en": "Other"}, "kind": "vector"}}

        def row(name, hit5, quiet=None):
            return {"system": name, "stores": 2, "hit1": 0.1, "hit5": hit5, "hit10": 0.9, "items": 2.5,
                    "delivered_hit": 0.5, "precision": 0.25, "delivered_f1": report.harmonic(0.5, 0.25), "chars": 1234.4,
                    "quiet_rate": quiet}

        rows = report.order([row("other", 0.9), row("feiyu-memgate-sqlite-precise", 0.2), row("mem0-llm-deepseek-flash", 0.95),
                             row("feiyu-memgate-sqlite", 0.2)])
        self.assertEqual([r["system"] for r in rows], ["feiyu-memgate-sqlite", "feiyu-memgate-sqlite-precise",
                                                       "mem0-llm-deepseek-flash", "other"])
        table = report.quality_table(rows, "en", registry, quiet=False).split("\n")
        self.assertEqual(table[2], "| **own sqlite** | keyword | 10.0% | 20.0% | 90.0% | 2.50 | 50.0% | 25.0% | 33.3% | 1,234 |")
        self.assertTrue(table[4].startswith("| mem0 (LLM extraction, deepseek-flash) | extraction |"))
        zh = report.quality_table([row("other", 0.9, quiet=0.125)], "zh", registry, quiet=True, with_stores=True)
        self.assertIn("| 别的 | 向量 | 2 | 10.0% | 90.0% |", zh)
        self.assertTrue(zh.endswith("| 12.5% |"))
        self.assertIsNone(report.harmonic(None, 0.5))
        self.assertIsNone(report.harmonic(0.0, 0.0))

    def test_the_published_summary_matches_the_published_metrics(self):
        """results/summary.json is what report.build gives from the metrics files that are in the repository."""
        with open(os.path.join(_EXT, "results", "summary.json"), encoding="utf-8") as f:
            published = json.load(f)
        self.assertRegex(published.pop("generated"), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(json.loads(json.dumps(report.build())), published)


class PackTest(unittest.TestCase):
    def test_round_trip_and_what_is_packed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "locomo")
            os.makedirs(folder)
            body = b'{\n "answers": {"w/q1": {"ranked": [], "deployed": [], "ms": 1.0}}\n}\n'
            for name in ("some-system.json", "some-system.metrics.json", "latency-summary.json", "same-stores-x.json"):
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(body)
            self.assertEqual(pack.pack(tmp), 1)
            self.assertEqual(sorted(n for n in os.listdir(folder) if n.endswith(".gz")), ["some-system.json.gz"])
            with open(os.path.join(folder, "some-system.json.gz"), "rb") as f:
                packed = f.read()
            self.assertEqual(pack.pack(tmp), 0)  # unchanged content: nothing is rewritten
            self.assertEqual(pack.compress(body), packed)  # deterministic
            os.remove(os.path.join(folder, "some-system.json"))
            self.assertEqual(pack.unpack(tmp), 1)
            with open(os.path.join(folder, "some-system.json"), "rb") as f:
                self.assertEqual(f.read(), body)
            self.assertEqual(pack.unpack(tmp), 0)


if __name__ == "__main__":
    unittest.main()
