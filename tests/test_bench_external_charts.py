"""The charts of the public-dataset benchmark: every chart, in both languages and both themes, from the real input
files and from a small hand-made summary."""
import copy
import importlib.util
import json
import math
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_spec = importlib.util.spec_from_file_location("bench_external_charts",
                                               os.path.join(_ROOT, "bench", "external", "charts.py"))
ext_charts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ext_charts)

SVG = "{http://www.w3.org/2000/svg}"
BANNED = re.compile(r"\bnan\b|\bNone\b|\binf\b")


def _read(*parts):
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as f:
        return json.load(f)


def _row(name, hit5, **extra):
    by_type = {k: {"questions": 10, "hit1": hit5 / 2, "hit5": hit5, "hit10": min(hit5 + 0.1, 1.0),
                   "delivered_hit": hit5, "precision": 0.5, "items": 2.0}
               for k in ("profile", "social_relationship", "events", "dialogues")}
    return {"system": name, "stores": 2, "answerable": 40, "hit1": hit5 / 2, "hit5": hit5,
            "hit10": min(hit5 + 0.1, 1.0), "delivered_hit": hit5 - 0.1, "precision": 0.5, "delivered_f1": 0.4,
            "items": extra.pop("items", 2.5), "chars": 300.0, "no_answer": extra.pop("no_answer", None),
            "quiet_rate": extra.pop("quiet_rate", None), "llm_model": extra.pop("llm_model", None), "by_type": by_type}


def small_inputs():
    """Two systems per dataset (this project's sqlite, which also has its precise twin, and one other), one sample,
    one cost pair and one latency entry of each kind."""
    ext = {}
    for dataset, types, quiet in (("perltqa", ("profile", "social_relationship", "events", "dialogues"), None),
                                  ("locomo", ("4-single-hop", "1-multi-hop", "2-temporal", "3-open-domain"), 0.2)):
        full = [_row("feiyu-memgate-sqlite", 0.7, quiet_rate=quiet, no_answer=5 if quiet is not None else None),
                _row("feiyu-memgate-sqlite-precise", 0.7, items=1.1, quiet_rate=quiet,
                     no_answer=5 if quiet is not None else None),
                _row("memsearch", 0.8, items=5.0, quiet_rate=0.0 if quiet is not None else None,
                     no_answer=5 if quiet is not None else None)]
        for row in full:
            row["by_type"] = {k: dict(v) for k, v in zip(types, row["by_type"].values())}
        sample_rows = [_row("graphiti-deepseek-flash", 0.5), _row("feiyu-memgate-sqlite", 0.7),
                       _row("feiyu-memgate-hybrid", 0.8)]
        cost = [{"system": "graphiti-deepseek-flash", "model": "deepseek-flash", "stores": 3, "memories": 100,
                 "calls": 250, "usd": 0.3, "usd_per_1000_memories": 3.0, "ingest_seconds_per_memory": 1.0},
                {"system": "graphiti-claude-haiku-5-5", "model": "claude-haiku-5-5", "stores": 2, "memories": 100,
                 "calls": 240, "usd": 0.2, "usd_per_1000_memories": 2.0, "ingest_seconds_per_memory": 1.0}]
        latency = {
            "feiyu-memgate-sqlite": {"kind": "sequential pass on an idle machine, one world", "world": "w1",
                                     "memories": 50, "questions": 9, "p50_ms": 5.4, "p95_ms": 7.5},
            "memsearch": {"kind": "sequential pass on an idle machine, one world", "world": "w1", "memories": 50,
                          "questions": 9, "p50_ms": 120.0, "p95_ms": 160.0},
            "graphiti-deepseek-flash": {"kind": "measured under load (parallel run, the worlds it ran on)",
                                        "world": None, "memories": None, "questions": None, "p50_ms": 51.2,
                                        "p95_ms": 90.0}}
        ext[dataset] = {"stores": ["a", "b"], "full": full,
                        "samples": [{"n": 1, "stores": ["a"], "rows": sample_rows}], "cost": cost, "latency": latency}
    own = {"dataset": {"memories": 20, "queries": 15, "run_date": "2026-01-02",
                       "types": {"lexical": 5, "paraphrase": 5, "updated": 2, "quiet": 3}},
           "systems": {}, "errors": {}}
    for name, hit5, items in (("feiyu-memgate-sqlite", 0.8, 0.5), ("feiyu-memgate-sqlite-precise", 0.8, 0.4),
                              ("memsearch", 0.95, 5.0)):
        own["systems"][name] = {
            "system": name, "family": "feiyu-memgate" if name.startswith("feiyu") else "other", "llm": False,
            "experiment": False, "variant": name.endswith("-precise"),
            "overall": {"hit1": 0.5, "hit5": hit5, "deployed_hit": 0.5, "avg_items": items, "precision": 0.8},
            "by_type": {"quiet": {"quiet_rate": 0.7 if name.startswith("feiyu") else 0.0}}}
    return ext, own, {"memsearch": {"label": {"zh": "memsearch", "en": "memsearch"}}}


class ExternalChartsOnRealData(unittest.TestCase):
    FULL = ["feiyu-memgate sqlite", "feiyu-memgate embedding", "feiyu-memgate hybrid"]
    # the labels of this project's configurations each chart carries (the cost chart has none of its own; the
    # sampled groups hold sqlite and hybrid only; the scatter and the dumbbells print the short names)
    LABELS = {"overview": FULL, "ranking": FULL, "by-type": FULL, "latency": FULL, "samples": FULL[::2],
              "delivered": ["sqlite", "embedding", "hybrid"], "thresholds": ["sqlite", "embedding", "hybrid"],
              "cost": []}

    @classmethod
    def setUpClass(cls):
        cls.ext = _read("bench", "external", "results", "summary.json")
        cls.own = _read("bench", "results", "summary.json")
        cls.systems = _read("bench", "systems.json")

    def test_every_chart_is_well_formed_and_complete(self):
        for name in ext_charts.CHARTS:
            for lang in ext_charts.LANGS:
                for theme in ext_charts.THEMES:
                    with self.subTest(chart=name, lang=lang, theme=theme):
                        svg = ext_charts.render_chart(name, self.ext, self.own, self.systems, theme, lang)
                        root = ET.fromstring(svg)
                        self.assertEqual(root.get("width"), "1000")
                        self.assertEqual(root.get("viewBox").split()[2], "1000")
                        for label in self.LABELS[name]:
                            self.assertIn(label, svg)
                        self.assertIsNone(BANNED.search(svg), BANNED.search(svg))

    def test_overview_rows_are_the_configurations_without_a_model_in_all_three_sources(self):
        rows = ext_charts.overview_rows(self.ext, self.own)
        names = [r[0] for r in rows]
        self.assertEqual(names[:3], ["feiyu-memgate-sqlite", "feiyu-memgate-embedding", "feiyu-memgate-hybrid"])
        for name, base, perltqa, locomo in rows:
            self.assertFalse(perltqa["llm_model"] or locomo["llm_model"] or base.get("llm"))
        means = [(r[1]["overall"]["hit5"] + r[2]["hit5"] + r[3]["hit5"]) / 3.0 for r in rows[3:]]
        self.assertEqual(means, sorted(means, reverse=True))

    def test_rows_of_the_ranking_charts_are_full_store_rows_without_precise_twins(self):
        names = ext_charts.ranked_names(self.ext)
        self.assertFalse([n for n in names if n.endswith("-precise")])
        self.assertEqual(names[:3], ["feiyu-memgate-sqlite", "feiyu-memgate-embedding", "feiyu-memgate-hybrid"])
        self.assertEqual(len(names), len(set(names)))

    def test_delivered_labels_sit_next_to_their_dots(self):
        for lang in ext_charts.LANGS:
            layout = ext_charts.delivered_layout(self.ext, self.systems, lang)
            for dataset, panel in layout.items():
                with self.subTest(lang=lang, dataset=dataset):
                    points, placement = panel["points"], panel["placement"]
                    self.assertEqual(len(points), len(placement))
                    segments = []
                    for (x, y, _), (_, _, _, box, leader) in zip(points, placement):
                        if leader:
                            self.assertLessEqual(math.hypot(leader[0] - x, leader[1] - y), 40.0)
                            segments.append((x, y) + tuple(leader))
                    boxes = [p[3] for p in placement]
                    for i in range(len(boxes)):
                        for j in range(i + 1, len(boxes)):
                            a, b = boxes[i], boxes[j]
                            self.assertFalse(min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]),
                                             (points[i][2], points[j][2]))
                    for i in range(len(segments)):
                        for j in range(i + 1, len(segments)):
                            self.assertFalse(ext_charts._cross(segments[i], segments[j]))
                    for seg in segments:
                        for arrow in panel["arrows"]:
                            self.assertFalse(ext_charts._cross(seg, arrow))

    def test_files_are_written_with_the_expected_names(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = ext_charts.render_all(self.ext, self.own, self.systems, folder, charts=("cost", "ranking"),
                                          langs=("en",), themes=("light", "dark"))
            self.assertEqual(sorted(os.path.basename(p) for p in paths),
                             ["ext-cost.en.dark.svg", "ext-cost.en.light.svg",
                              "ext-ranking.en.dark.svg", "ext-ranking.en.light.svg"])
            with open(paths[0], "rb") as f:
                self.assertNotIn(b"\r", f.read())


class HeatmapColours(unittest.TestCase):
    def test_every_cell_text_reaches_4_5_to_1_on_both_themes(self):
        for theme in ext_charts.THEMES:
            for k in range(0, 1001):
                fill, ink = ext_charts.cell_colors(theme, k / 1000.0)
                self.assertGreaterEqual(ext_charts.contrast(fill, ink), 4.5, (theme, k))

    def test_the_ramp_starts_at_the_surface_and_has_its_own_end_per_theme(self):
        for theme in ext_charts.THEMES:
            self.assertEqual(ext_charts.ramp_color(theme, 0.0), ext_charts.PALETTE[theme]["surface"])
        self.assertNotEqual(ext_charts.ramp_color("light", 1.0), ext_charts.ramp_color("dark", 1.0))


class ExternalChartsOnASmallSummary(unittest.TestCase):
    def test_small_summary_builds_every_chart(self):
        ext, own, systems = small_inputs()
        for name in ext_charts.CHARTS:
            for lang in ext_charts.LANGS:
                for theme in ext_charts.THEMES:
                    with self.subTest(chart=name, lang=lang, theme=theme):
                        svg = ext_charts.render_chart(name, ext, own, systems, theme, lang)
                        root = ET.fromstring(svg)
                        self.assertEqual(root.get("width"), "1000")
                        self.assertIsNone(BANNED.search(svg), BANNED.search(svg))

    def test_footer_date_is_the_generated_key_or_absent(self):
        ext, own, systems = small_inputs()
        without = ext_charts.source_text(ext, "en")
        self.assertNotIn("2026", without)
        self.assertFalse(without.rstrip().endswith("·"))
        ext["generated"] = "2026-11-17"
        self.assertTrue(ext_charts.source_text(ext, "en").endswith(" · 2026-11-17"))
        self.assertTrue(ext_charts.source_text(ext, "zh").endswith(" · 2026-11-17"))
        for name in ext_charts.CHARTS:        # the key is not a dataset: no chart may trip over it
            ET.fromstring(ext_charts.render_chart(name, ext, own, systems, "dark", "zh"))
            ET.fromstring(ext_charts.render_chart(name, small_inputs()[0], own, systems, "light", "en"))

    def test_the_footer_names_the_configurations_left_out_of_a_dataset(self):
        ext, own, systems = small_inputs()
        systems["dsh-fish-memory"] = {"label": {"zh": "dsh-fish-memory", "en": "dsh-fish-memory"}, "project": "dsh-fish-memory"}
        systems["dsh-fish-memory-expand-deepseek-flash"] = {
            "label": {"zh": "dsh-fish-memory + x 扩词", "en": "dsh-fish-memory + x expansion"}, "project": "dsh-fish-memory",
            "llm": True}
        self.assertNotIn("Left out", ext_charts.render_chart("ranking", ext, own, systems, "light", "en"))
        ext["perltqa"]["excluded"] = ["dsh-fish-memory", "dsh-fish-memory-expand-deepseek-flash"]
        ext["locomo"]["excluded"] = ["dsh-fish-memory", "dsh-fish-memory-expand-deepseek-flash", "memsearch"]
        ext["locomo"]["full"] = [r for r in ext["locomo"]["full"] if r["system"] != "memsearch"]
        self.assertEqual(ext_charts.left_out_note(ext, "en", systems),
                         "Left out: dsh-fish-memory (PerLTQA, LoCoMo); memsearch (LoCoMo). On that dataset they stored over "
                         "5% fewer memories than they were sent; see the write audit in the README.")
        self.assertTrue(ext_charts.left_out_note(ext, "zh", systems).startswith(
            "未列入：dsh-fish-memory（PerLTQA、LoCoMo）；memsearch（LoCoMo）。"))
        for name in ext_charts.CHARTS:
            svg = ext_charts.render_chart(name, ext, own, systems, "light", "en")
            ET.fromstring(svg)
            self.assertEqual("Left out" in svg, name != "thresholds", name)  # that chart has this project's rows only
            # memsearch calls no model and ran on every store: the cost and samples charts never showed it
            self.assertEqual("memsearch (LoCoMo)" in svg, name not in ("thresholds", "cost", "samples"), name)

    def test_a_single_model_of_a_cost_pair_still_draws(self):
        ext, own, systems = small_inputs()
        ext = copy.deepcopy(ext)
        ext["locomo"]["cost"] = ext["locomo"]["cost"][:1]
        ET.fromstring(ext_charts.render_chart("cost", ext, own, systems, "light", "en"))


if __name__ == "__main__":
    unittest.main()
