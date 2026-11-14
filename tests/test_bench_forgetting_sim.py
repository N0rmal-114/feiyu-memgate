"""The forgetting simulator's bookkeeping, on a tiny synthetic scenario (10 memories, 30 days)."""
import csv
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench", "forgetting_sim.py")
_spec = importlib.util.spec_from_file_location("bench_forgetting_sim", _PATH)
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)

# Ten memories with texts that share no wording: a question that is a memory's own text finds exactly it.
TEXTS = ["zyx quartz violin", "mnb walnut harbor", "lkj copper meadow", "hgf velvet orchid", "dsa timber lantern",
         "poi marble thunder", "uyt saffron glacier", "rew indigo canyon", "qaz pewter harvest", "xsw amber voyage"]
MEMORIES = {}
for i, text in enumerate(TEXTS, start=1):
    # m01-m05 are very old, m06-m10 are from the last days before the simulation starts
    date = "2025-01-%02d" % i if i <= 5 else "2026-10-%02d" % (10 + i)
    MEMORIES["m%02d" % i] = {"text": text, "date": date, "source": "private", "kind": "event"}

QUERIES = {"q%d" % i: {"query": TEXTS[i - 1], "expect": ["m%02d" % i], "type": "lexical"} for i in range(1, 7)}
QUERIES.update({
    "q7": {"query": TEXTS[6], "expect": ["m07"], "type": "updated", "stale": ["m01"]},
    "q8": {"query": TEXTS[7], "expect": ["m08"], "type": "updated", "stale": ["m02"]},
    "q9": {"query": TEXTS[8], "expect": ["m09"], "type": "updated", "stale": ["m03"]},
    "q10": {"query": "nothing like it", "expect": [], "type": "quiet"},
})
FAST = {"curve": {"half_life_days": 20}}                       # lifetime 86 days: nothing is deleted in 30 days
OPEN = {"curve": {"half_life_days": 2}, "section": {"max_forget_fraction": 1.0}}   # lifetime 8.6 days, no limit
LIMITED = {"curve": {"half_life_days": 2}, "section": {"max_forget_min": 2}}      # the same, 2 forgotten per sweep
ARMS = {"baseline": None, "fast": FAST, "open": OPEN, "limited": LIMITED}
DAYS = 30


class ScenarioTests(unittest.TestCase):
    def test_groups_are_disjoint_thirds_and_cold_is_never_asked(self):
        scenario = sim.make_scenario(QUERIES, seed=1, days=DAYS)
        groups = scenario["groups"]
        self.assertEqual([len(groups[g]) for g in sim.GROUPS], [3, 3, 3])
        self.assertEqual(len(set(groups["hot"]) | set(groups["warm"]) | set(groups["cold"])), 9)
        self.assertNotIn("q10", sum(groups.values(), []))  # a quiet question is not answerable
        schedule = scenario["schedule"]
        self.assertEqual(len(schedule), DAYS)
        self.assertTrue(all(len(day) <= sim.MAX_PER_DAY for day in schedule))
        asked = {qid for day in schedule for qid in day}
        self.assertFalse(asked & set(groups["cold"]))
        self.assertTrue(asked <= set(groups["hot"]) | set(groups["warm"]))

    def test_same_seed_same_draws_and_hot_gets_most_of_the_traffic(self):
        a = sim.make_scenario(QUERIES, seed=7, days=400)
        b = sim.make_scenario(QUERIES, seed=7, days=400)
        self.assertEqual(a, b)
        self.assertNotEqual(a["schedule"], sim.make_scenario(QUERIES, seed=8, days=400)["schedule"])
        asked = [qid for day in a["schedule"] for qid in day]
        hot_share = sum(1 for q in asked if q in set(a["groups"]["hot"])) / len(asked)
        self.assertGreater(hot_share, 0.7)
        self.assertLess(hot_share, 0.9)

    def test_the_scenario_constants_are_the_fixed_ones(self):
        self.assertEqual((sim.SEED, sim.DAYS, sim.CHECK_EVERY, sim.MAX_PER_DAY, sim.HOT_SHARE, sim.LIMIT),
                         (20261114, 540, 30, 6, 0.8, 5))
        self.assertEqual(list(sim.ARMS), ["baseline", "default", "default_pinned", "aggressive"])
        self.assertEqual(sim.ARMS["default"], {})  # the default arm uses the defaults as they are
        self.assertEqual(sim.ARMS["default_pinned"], {"pin_kind": "identity"})  # the defaults, identity pinned
        self.assertEqual(sim.ARMS["aggressive"]["curve"]["half_life_days"], 30)  # the default 90, divided by 3
        self.assertFalse(hasattr(sim, "MIN_SCORE"))  # no hand-set thresholds: the backends' defaults apply

    def test_start_day_and_checkpoints(self):
        self.assertEqual(sim.start_epoch(MEMORIES), sim.date_to_epoch("2026-10-20") + sim.DAY)
        self.assertEqual(sim.checkpoints(30, 10), [0, 1, 10, 20, 30])
        self.assertEqual(sim.checkpoints(35, 10), [0, 1, 10, 20, 30, 35])


class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = sim.simulate(MEMORIES, QUERIES, seed=3, days=DAYS, every=10, arms=ARMS)
        cls.rows = {}
        for row in cls.result["timeline"]:
            cls.rows.setdefault(row["arm"], {})[row["day"]] = row
        cls.groups = cls.result["scenario"]["groups"]

    def test_one_row_per_arm_and_checkpoint(self):
        for arm in ARMS:
            self.assertEqual(sorted(self.rows[arm]), [0, 1, 10, 20, 30])

    def test_baseline_never_loses_a_memory_and_finds_every_question(self):
        for row in self.rows["baseline"].values():
            self.assertEqual((row["in_store"], row["archived"], row["purged"]), (10, 0, 0))
            self.assertEqual((row["demoted"], row["due"], row["held"]), (0, 0, 0))
            self.assertEqual((row["hit5_hot"], row["hit5_warm"], row["hit5_cold"]), (1.0, 1.0, 1.0))
            self.assertEqual(row["avg_items"], 1.0)  # one memory per question
            self.assertEqual(row["noise_rate"], 0.0)
            self.assertEqual((row["deleted_hot"], row["deleted_warm"], row["deleted_cold"]), (0, 0, 0))

    def test_start_state_is_the_same_for_every_arm(self):
        def state(row):  # freshness may reorder the updated pairs even on day 0; states are the arm's own
            skip = ("arm", "updated_new_first", "updated_detail", "demoted", "due", "held", "held_sweeps", "pinned")
            return {k: v for k, v in row.items() if k not in skip}

        for arm in ("fast", "open", "limited"):
            self.assertEqual(state(self.rows[arm][0]), state(self.rows["baseline"][0]))

    def test_enabling_forgetting_deletes_nothing_on_the_first_day_but_the_old_ones_rank_low(self):
        # m01-m05 are dated far back: they are demoted at once, none is deleted, every question still finds its memory
        asked_day_one = {qid for qid in self.result["scenario"]["schedule"][0]}
        saved = {QUERIES[q]["expect"][0] for q in asked_day_one} & {"m01", "m02", "m03", "m04", "m05"}
        for arm in ("fast", "open", "limited"):
            day1 = self.rows[arm][1]
            self.assertEqual((day1["in_store"], day1["archived"], day1["due"]), (10, 0, 0), arm)
            if arm == "fast":  # half-life 20: the five old ones are demoted, the recent five are not
                self.assertEqual(day1["demoted"], 5 - len(saved), arm)
            self.assertEqual((day1["hit5_hot"], day1["hit5_warm"], day1["hit5_cold"]), (1.0, 1.0, 1.0), arm)

    def test_a_slow_curve_deletes_nothing_inside_its_lifetime(self):
        for row in self.rows["fast"].values():
            self.assertEqual((row["in_store"], row["archived"], row["purged"]), (10, 0, 0))

    def test_memories_are_conserved_between_store_archive_and_purge(self):
        for arm in ("open", "limited"):
            for row in self.rows[arm].values():
                self.assertEqual(row["in_store"] + row["archived"] + row["purged"], 10)
        self.assertEqual(self.rows["open"][30]["purged"], 0)  # the archive keeps 90 days, only 30 passed

    def test_the_open_arm_forgets_what_nobody_asked_for(self):
        last = self.rows["open"][30]
        self.assertLess(last["in_store"], 10)
        self.assertEqual(last["held"], 0)
        self.assertEqual(last["hit5_cold"], 0.0)

    def test_deleted_counts_match_the_hit_rates(self):
        for arm in ("open", "limited"):
            last = self.rows[arm][30]
            for group in sim.GROUPS:
                # every question's expected memory is its own: a question is a hit exactly when its memory is still there
                hits = round(last["hit5_" + group] * len(self.groups[group]))
                self.assertEqual(hits + last["deleted_" + group], len(self.groups[group]))
                self.assertLessEqual(last["unrecoverable_" + group], last["deleted_" + group])
            self.assertEqual(sum(last["unrecoverable_" + g] for g in sim.GROUPS), 0)  # all still in the archive

    def test_the_cold_group_loses_at_least_as_much_as_the_asked_ones(self):
        last = self.rows["open"][30]
        self.assertLessEqual(last["hit5_cold"], last["hit5_hot"])
        self.assertLessEqual(last["hit5_cold"], last["hit5_warm"])

    def test_the_rate_limit_spreads_a_mass_expiry_over_sweeps(self):
        self.assertGreater(self.rows["limited"][30]["held_sweeps"], 0)
        self.assertGreaterEqual(self.rows["limited"][30]["in_store"], self.rows["open"][30]["in_store"])
        scenario = self.result["scenario"]
        arm = sim.Arm("t", MEMORIES, QUERIES, scenario, LIMITED, sim.start_epoch(MEMORIES))
        self.addCleanup(arm.close)
        service = arm.gateway.forgetting
        saw_held = False
        forgetting_days = 0
        for day in range(1, DAYS + 1):
            before = len(list(arm.store.refs()))
            arm.step(day)
            gone = before - len(list(arm.store.refs()))
            self.assertLessEqual(gone, service.forget_limit(before))  # never more than the limit in one sweep
            forgetting_days += gone > 0
            saw_held = saw_held or arm.held_now > 0
        self.assertTrue(saw_held)
        self.assertGreater(forgetting_days, 1)  # several sweeps, not one
        self.assertLess(len(list(arm.store.refs())), 10)  # and in the end what nobody asked for went

    def test_restore_check_brings_a_forgotten_memory_back(self):
        check = self.result["restore"]["open"]
        self.assertNotIn("baseline", self.result["restore"])
        self.assertIsNotNone(check["question"])
        self.assertFalse(check["found_before_restore"])
        self.assertTrue(check["restored"])
        self.assertTrue(check["found_after_restore"])
        self.assertTrue(check["text_identical"])
        self.assertIsNone(self.result["restore"]["fast"]["question"])  # nothing archived: no check, said so

    def test_the_run_is_deterministic(self):
        again = sim.simulate(MEMORIES, QUERIES, seed=3, days=DAYS, every=10, arms=ARMS)
        self.assertEqual(again["timeline"], self.result["timeline"])


class BookkeepingTests(unittest.TestCase):
    def arm(self, spec=None):
        scenario = sim.make_scenario(QUERIES, seed=3, days=DAYS)
        arm = sim.Arm("t", MEMORIES, QUERIES, scenario, spec, sim.start_epoch(MEMORIES))
        self.addCleanup(arm.close)
        return arm

    def test_a_probe_does_not_count_as_a_use(self):
        arm = self.arm(FAST)
        self.assertEqual(arm.probe("q6"), ["m06"])
        self.assertEqual(arm.gateway.forgetting.ledger.entries(sim.STORE), [])
        arm.gateway.recall("owner", {"query": QUERIES["q6"]["query"]})  # a real recall does
        entry = arm.gateway.forgetting.ledger.entries(sim.STORE)[0]
        self.assertEqual((entry.ref, entry.uses), ("m06", 1))

    def test_the_arm_settings_reach_the_forgetting_service(self):
        service = self.arm({}).gateway.forgetting
        self.assertEqual((service.curve.half_life_days, service.max_forget_fraction, service.max_forget_min),
                         (90, 0.02, 5))
        service = self.arm(OPEN).gateway.forgetting
        self.assertEqual((service.curve.half_life_days, service.max_forget_fraction), (2, 1.0))
        self.assertIsNone(self.arm(None).gateway.forgetting)

    def test_the_pinned_arm_pins_the_identity_memories_and_they_are_never_forgotten(self):
        scenario = sim.make_scenario(QUERIES, seed=3, days=DAYS)
        never_asked = sorted(QUERIES[q]["expect"][0] for q in scenario["groups"]["cold"])
        memories = {k: dict(v, kind="identity" if k in never_asked else "event") for k, v in MEMORIES.items()}
        left = {}
        for pin in (False, True):
            spec = {"curve": {"half_life_days": 2}, "section": {"max_forget_fraction": 1.0}}
            if pin:
                spec["pin_kind"] = "identity"
            arm = sim.Arm("t", memories, QUERIES, scenario, spec, sim.start_epoch(memories))
            self.addCleanup(arm.close)
            for day in range(1, DAYS + 1):
                arm.step(day)
            left[pin] = {ref for ref, _ in arm.store.refs()}
            self.assertEqual(arm.pinned, len(never_asked) if pin else 0)
        self.assertFalse(set(never_asked) & left[False])  # nobody asked: forgotten without the pin ...
        self.assertTrue(set(never_asked) <= left[True])   # ... kept with it
        row = arm.snapshot(DAYS)
        self.assertEqual((row["pinned"], row["in_store"] + row["archived"]), (len(never_asked), 10))

    def test_the_ledger_follows_the_fake_clock(self):
        arm = self.arm(FAST)
        arm.step(1)
        entries = arm.gateway.forgetting.ledger.entries(sim.STORE)
        self.assertEqual(len(entries), 10)
        for entry in entries:  # met at the day-1 sweep (or used at noon before it), in simulated time
            self.assertEqual(entry.managed_since, arm.start + (sim.DAY / 2 if entry.uses else sim.DAY))

    def test_updated_share_counts_newer_above_stale(self):
        arm = self.arm()
        answers = {
            "q7": ["m07", "m01"],   # newer above stale: win
            "q8": ["m02", "m08"],   # stale above newer: no
            "q9": ["m09"],          # newer only: win
        }
        share, detail = arm._updated(answers)
        self.assertEqual(share, round(2 / 3, 4))
        self.assertEqual(detail, {"questions": 3, "both_returned": 2, "new_only": 1, "stale_only": 0, "neither": 0})
        share, detail = arm._updated({"q7": ["m01"], "q8": [], "q9": []})
        self.assertEqual(share, 0.0)
        self.assertEqual(detail, {"questions": 3, "both_returned": 0, "new_only": 0, "stale_only": 1, "neither": 2})

    def test_ratio_of_nothing_is_none(self):
        self.assertIsNone(sim._ratio(0, 0))
        self.assertEqual(sim._ratio(1, 3), 0.3333)


class FakeEmbedder(BaseHTTPRequestHandler):
    """A local stand-in for an embeddings endpoint: a vector of letter counts per text."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = []
        for index, text in enumerate(body["input"]):
            vec = [float(text.lower().count(c)) for c in "abcdefghijklmnopqrstuvwxyz"]
            data.append({"index": index, "embedding": vec})
        raw = json.dumps({"data": data}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class HybridBackendTests(unittest.TestCase):
    def test_the_store_specs(self):
        self.assertEqual(sim.store_spec("sqlite"), {"type": "sqlite", "path": ":memory:"})
        spec = sim.store_spec("hybrid", "http://embedder.example:11434/v1")
        self.assertEqual([c["type"] for c in spec["children"]], ["sqlite", "embedding"])
        self.assertEqual(spec["children"][1]["model"], "bge-m3")
        with self.assertRaises(ValueError):
            sim.store_spec("hybrid")
        with self.assertRaises(ValueError):
            sim.store_spec("nope")

    def test_a_hybrid_arm_runs_against_a_local_embedder(self):
        server = HTTPServer(("127.0.0.1", 0), FakeEmbedder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:%d/v1" % server.server_address[1]
        scenario = sim.make_scenario(QUERIES, seed=3, days=5)
        rows = sim.run_arm("open", OPEN, MEMORIES, QUERIES, scenario, days=5, every=5, backend="hybrid",
                           embed_url=url)
        self.assertEqual([r["day"] for r in rows], [0, 1, 5])
        self.assertEqual(rows[0]["in_store"], 10)
        self.assertEqual(rows[0]["hit5_hot"], 1.0)  # a memory's own text is its best match in both children


class OutputTests(unittest.TestCase):
    def test_csv_json_and_the_combined_markdown(self):
        result = sim.simulate(MEMORIES, QUERIES, seed=3, days=DAYS, every=10, arms=ARMS)
        with tempfile.TemporaryDirectory() as folder:
            summary = sim.write_run(folder, "sqlite", result, MEMORIES, 1.5, days=DAYS)
            path = os.path.join(folder, "sqlite", "timeline.csv")
            with open(path, encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), len(result["timeline"]))
            self.assertEqual(list(rows[0]), sim.CSV_COLUMNS)
            with open(os.path.join(folder, "sqlite", "summary.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["backend"], "sqlite")
            self.assertEqual(summary["seconds"], 1.5)
            self.assertEqual(list(summary["arms"]), list(ARMS))
            self.assertIsNone(sim.write_combined(os.path.join(folder, "empty")))
            md = sim.write_combined(folder, reading="## 解读\n\n正文")
            with open(md, encoding="utf-8") as f:
                text = f.read()
        self.assertIn("## 方法", text)
        self.assertIn("## 最后一天", text)
        self.assertIn("无遗忘（基线）", text)
        self.assertIn("## 解读", text)
        self.assertIn("## 时间线：关键词后端", text)
        self.assertNotIn("## 时间线：混合后端", text)  # only the backends that were run
        rows_of_day_30 = [line for line in text.splitlines() if line.startswith("| 30 |")]
        self.assertEqual(len(rows_of_day_30), len(ARMS))  # day 30 of each arm, in the timeline tables


if __name__ == "__main__":
    unittest.main()
