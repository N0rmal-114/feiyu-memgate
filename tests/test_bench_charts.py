"""The chart generator, run on a tiny synthetic summary: well-formed SVG with the expected marks."""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bench", "charts.py")
_spec = importlib.util.spec_from_file_location("bench_charts", _PATH)
charts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(charts)

SVG = "{http://www.w3.org/2000/svg}"


def record(name, family, hit1, hit5, **extra):
    quiet = extra.pop("quiet", 0.0)
    overall = {"hit1": hit1, "hit5": hit5, "deployed_hit": hit5, "precision": extra.pop("precision", 0.5),
               "avg_items": extra.pop("avg_items", 2.0), "latency_p50_ms": extra.pop("p50", 10.0),
               "quiet_rate": quiet, "delivered_f1": extra.pop("f1", 0.5), "avg_chars": extra.pop("chars", 40.0),
               "hit_at_k": extra.pop("hitk", [hit1] + [hit5] * 9),
               "delivered_fresh_first_wins": extra.pop("wins", 6),
               "delivered_fresh_first_questions": extra.pop("fresh_q", 8)}
    by_type = {k: {"hit5": hit5, "deployed_hit": round(hit5 - 0.1, 2), "questions": 5}
               for k in ("lexical", "paraphrase", "updated")}
    by_type["quiet"] = {"quiet_rate": quiet, "questions": 5}
    rec = {"system": name, "family": family, "kind": extra.pop("kind", "keyword"), "llm": extra.pop("llm", False),
           "experiment": extra.pop("experiment", False), "overall": overall, "by_type": by_type,
           "ingest_seconds": extra.pop("ingest", 1.0), "llm_tokens": extra.pop("tokens", 0)}
    rec.update(extra)
    return rec


SUMMARY = {
    "dataset": {"version": "v1", "memories": 20, "queries": 15, "run_date": "2026-01-02",
                "types": {"lexical": 5, "paraphrase": 5, "updated": 2, "quiet": 3}},
    "systems": {
        "fmg-a": record("fmg-a", "feiyu-memgate", 0.6, 0.9, quiet=0.67, precision=0.8, avg_items=1.2, f1=0.8,
                        chars=20.0, wins=7, fresh_q=7),
        "other-a": record("other-a", "other", 0.5, 0.8, kind="vector", llm=True, precision=0.1, avg_items=20.0,
                          tokens=2_000_000, ingest=400.0, chars=500.0, f1=0.2, wins=9, fresh_q=18),
        "other-b": record("other-b", "other", 0.2, 0.3, precision=0.4, avg_items=1.3, chars=5.0, f1=0.35,
                          hitk=[0.2, 0.25, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3, 0.3], wins=1, fresh_q=2),
        "variant": record("variant", "other", 0.9, 0.9, experiment=True),
        # the pair the `profiles` chart reads (the plain id is the default, broad profile; the -precise id pins the
        # other one); marked as variants so the other charts leave them out
        "feiyu-memgate-embedding": record("feiyu-memgate-embedding", "feiyu-memgate", 0.6, 0.9, quiet=0.3,
                                          precision=0.5, avg_items=2.5, variant=True, wins=3, fresh_q=3),
        "feiyu-memgate-embedding-precise": record("feiyu-memgate-embedding-precise", "feiyu-memgate", 0.6, 0.9,
                                                  quiet=0.6, precision=0.8, avg_items=1.0, variant=True,
                                                  wins=4, fresh_q=5),
        "tier-none": record("tier-none", "feiyu-memgate", 0.5, 0.8, quiet=0.9, precision=0.85, variant=True),
        "tier-small": record("tier-small", "feiyu-memgate", 0.7, 0.9, quiet=0.5, precision=0.8, variant=True),
        "tier-large": record("tier-large", "feiyu-memgate", 0.8, 0.95, quiet=0.6, precision=0.82, variant=True),
    },
    "errors": {},
}
TIERS = [  # deliberately not in memory order: the chart sorts
    {"system": "tier-large", "label": {"zh": "大", "en": "large"}, "file_mb": 1157.7, "resident_mib": 1394.7},
    {"system": "tier-none", "label": {"zh": "无模型", "en": "no model"}, "file_mb": None, "resident_mib": None},
    {"system": "tier-small", "label": {"zh": "小", "en": "small"}, "file_mb": 47.9, "resident_mib": 175.1},
    {"system": "not-run", "label": {"zh": "没跑", "en": "not run"}, "file_mb": 1.0, "resident_mib": 1.0},
]
SYSTEMS = {"fmg-a": {"label": {"zh": "甲", "en": "A"}}, "other-a": {"label": {"zh": "乙", "en": "B"}},
           "feiyu-memgate-embedding": {"label": {"zh": "feiyu-memgate embedding", "en": "feiyu-memgate embedding"}},
           "feiyu-memgate-embedding-precise": {"label": {"zh": "feiyu-memgate embedding（精确档）",
                                                         "en": "feiyu-memgate embedding (precise profile)"}}}
FORGETTING = {"sqlite": {"memories": 20, "timeline": [
    {"arm": arm, "day": day, "in_store": store, "hit5_hot": hit}
    for arm, rows in (("baseline", ((0, 20, 0.7), (540, 20, 0.7))),
                      ("default", ((0, 20, 0.7), (540, 5, 0.7))),
                      ("aggressive", ((0, 20, 0.7), (540, 4, 0.6))))
    for day, store, hit in rows]}}


def parse(svg):
    return ET.fromstring(svg)


def texts(root):
    return ["".join(e.itertext()) for e in root.iter(SVG + "text")]


class RenderTest(unittest.TestCase):
    def test_every_chart_is_well_formed_in_every_language_and_theme(self):
        for name in charts.CHARTS:
            for lang in charts.LANGS:
                for theme in charts.THEMES:
                    svg = charts.render_chart(name, SUMMARY, SYSTEMS, theme, lang, FORGETTING, TIERS)
                    root = parse(svg)
                    self.assertEqual(root.tag, SVG + "svg")
                    self.assertTrue(texts(root), "%s %s %s has no text" % (name, lang, theme))

    def test_title_and_source_line(self):
        root = parse(charts.render_chart("ranking", SUMMARY, SYSTEMS, "light", "en"))
        found = texts(root)
        self.assertIn("bench/results/summary.md · 20 memories, 15 questions · 2026-01-02", found)
        self.assertTrue(any(t.startswith("Hit@5 per system") for t in found))
        zh = texts(parse(charts.render_chart("ranking", SUMMARY, SYSTEMS, "light", "zh")))
        self.assertIn("bench/results/summary.md · 20 条记忆，15 个问题 · 2026-01-02", zh)

    def test_ranking_has_one_bar_and_one_tick_per_charted_system_sorted_by_hit5(self):
        root = parse(charts.render_chart("ranking", SUMMARY, SYSTEMS, "light", "en"))
        paths = [p for p in root.iter(SVG + "path")]
        ticks = [l for l in root.iter(SVG + "line") if l.get("class") == "tick"]
        self.assertEqual(len(paths), 3)            # the experiment variant is not drawn
        self.assertEqual(len(ticks), 3 + 1)        # one per bar, plus the legend's key
        labels = [t for t in texts(root) if t in ("A", "B", "other-b", "variant")]
        self.assertEqual(labels, ["A", "B", "other-b"])  # sorted descending, display labels used
        self.assertNotIn("variant", texts(root))

    def test_colour_carries_only_the_family(self):
        root = parse(charts.render_chart("ranking", SUMMARY, SYSTEMS, "light", "en"))
        fills = [p.get("fill") for p in root.iter(SVG + "path")]
        self.assertEqual(fills[0], charts.PALETTE["light"]["accent"])
        self.assertEqual(set(fills[1:]), {charts.PALETTE["light"]["neutral"]})

    def test_dark_theme_uses_the_dark_surface(self):
        svg = charts.render_chart("quiet", SUMMARY, SYSTEMS, "dark", "en")
        self.assertIn(charts.PALETTE["dark"]["surface"], svg)
        self.assertNotIn(charts.PALETTE["light"]["surface"], svg)

    def test_by_type_has_three_panels_in_the_same_order(self):
        root = parse(charts.render_chart("by-type", SUMMARY, SYSTEMS, "light", "en"))
        self.assertEqual(len(list(root.iter(SVG + "path"))), 9)      # 3 systems x 3 panels
        self.assertEqual([t for t in texts(root) if "·" in t and "q" in t][:3],
                         ["lexical · 5 q", "paraphrase · 5 q", "updated · 2 q"])

    def test_delivered_has_one_dot_per_system_and_numbers_after_labels(self):
        root = parse(charts.render_chart("delivered", SUMMARY, SYSTEMS, "light", "en"))
        self.assertEqual(len(list(root.iter(SVG + "circle"))), 3)
        found = texts(root)
        self.assertIn("A 90%", found)
        self.assertIn("B 80%", found)

    def test_scatter_labels_do_not_collide(self):
        points = [(100, 100, "feiyu-memgate hybrid 82%"), (101, 101, "feiyu-memgate full 82%"),
                  (103, 99, "another system 40%"), (400, 300, "far away 10%")]
        placed = charts.place_labels(points, [], (0, 0, 700, 400))
        boxes = [p[3] for p in placed]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                self.assertEqual(charts._overlap(boxes[i], boxes[j], 0.0), 0.0, (i, j))
            for x, y, _ in points:
                self.assertEqual(charts._overlap(boxes[i], (x - 5, y - 5, x + 5, y + 5), 0.0), 0.0)
            self.assertGreaterEqual(boxes[i][0], 0)
            self.assertLessEqual(boxes[i][2], 700)

    def test_cost_prints_tokens_only_where_non_zero(self):
        root = parse(charts.render_chart("cost", SUMMARY, SYSTEMS, "light", "en"))
        self.assertEqual([t for t in texts(root) if t.endswith(" M")], ["2.00 M"])
        self.assertIn("log scale", " ".join(texts(root)))

    def test_forgetting_has_a_line_per_arm_and_panel_and_notes_coincident_lines(self):
        root = parse(charts.render_chart("forgetting", SUMMARY, SYSTEMS, "light", "en", FORGETTING))
        self.assertEqual(len(list(root.iter(SVG + "polyline"))), 6)   # 3 arms x 2 measures
        found = " ".join(texts(root))
        self.assertIn("baseline (no forgetting)", found)
        self.assertIn("defaults", found)
        self.assertIn("baseline (no forgetting) = defaults", found)   # identical Hit@5 series

    def test_forgetting_without_data_is_skipped(self):
        self.assertIsNone(charts.render_chart("forgetting", SUMMARY, SYSTEMS, "light", "en", {}))

    def test_spread_labels_keep_a_gap_and_the_order(self):
        out = charts.spread_labels([100.0, 101.0, 102.0, 200.0], 16, 0, 300)
        self.assertEqual(out, sorted(out))
        self.assertTrue(all(b - a >= 15.999 for a, b in zip(out, out[1:])))
        self.assertAlmostEqual(out[3], 200.0)

    def test_render_all_writes_named_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = charts.render_all(SUMMARY, SYSTEMS, tmp, FORGETTING, tiers=TIERS)
            names = sorted(os.path.basename(p) for p in paths)
            self.assertEqual(len(names), len(charts.CHARTS) * 4)
            self.assertIn("quality.en.light.svg", names)
            self.assertIn("hit-at-k.zh.dark.svg", names)
            self.assertIn("profiles.en.dark.svg", names)
            self.assertIn("ranking.zh.dark.svg", names)
            self.assertIn("forgetting.en.light.svg", names)
            self.assertIn("tiers.zh.dark.svg", names)

    def test_variants_stay_out_of_the_five_benchmark_charts(self):
        for name in ("ranking", "by-type", "delivered", "quiet", "cost", "quality", "hit-at-k", "delivered-by-type",
                     "context"):
            svg = charts.render_chart(name, SUMMARY, SYSTEMS, "light", "en", FORGETTING, TIERS)
            self.assertNotIn("tier-", svg, name)
            self.assertNotIn("feiyu-memgate-embedding", svg, name)
        self.assertEqual([s["system"] for s in charts.charted(SUMMARY)], ["fmg-a", "other-a", "other-b"])

    def test_tiers_rows_are_ordered_by_memory_with_none_first(self):
        root = parse(charts.render_chart("tiers", SUMMARY, SYSTEMS, "light", "en", None, TIERS))
        found = texts(root)
        labels = [t for t in found if t in ("no model", "small", "large", "not run")]
        self.assertEqual(labels, ["no model", "small", "large"])  # "not run" has no result: no row
        self.assertIn("none", found)
        self.assertIn("175 MiB", found)
        self.assertIn("1.36 GiB", found)
        self.assertIn("47.9 MB", found)
        self.assertIn("1.16 GB", found)

    def test_tiers_has_four_bar_columns_and_one_dot_per_modelled_row(self):
        root = parse(charts.render_chart("tiers", SUMMARY, SYSTEMS, "light", "en", None, TIERS))
        bars = [p for p in root.iter(SVG + "path") if p.get("fill") == charts.PALETTE["light"]["accent"]]
        self.assertEqual(len(bars), 3 * 4)
        dots = [c for c in root.iter(SVG + "circle") if c.get("fill") == charts.PALETTE["light"]["accent"]]
        self.assertEqual(len(dots), 2)
        found = texts(root)
        for expected in ("ranked Hit@5", "delivered hit", "precision", "quiet rate", "90%", "95%"):
            self.assertIn(expected, found)

    def test_tiers_marks_the_precise_profile_of_a_tier_with_a_tick_in_the_last_three_columns(self):
        summary = copy.deepcopy(SUMMARY)
        summary["systems"]["tier-small-precise"] = record("tier-small-precise", "feiyu-memgate", 0.7, 0.6, quiet=0.8,
                                                          precision=0.9, variant=True)
        ink = charts.PALETTE["light"]["ink"]

        def ticks(s):
            root = parse(charts.render_chart("tiers", s, SYSTEMS, "light", "en", None, TIERS))
            return [ln for ln in root.iter(SVG + "line") if ln.get("stroke") == ink and ln.get("stroke-width") == "2.5"]

        self.assertEqual(len(ticks(SUMMARY)), 0)
        self.assertEqual(len(ticks(summary)), 3)                # not in the Hit@5 column: the ranking is the same
        self.assertIn("precise profile pinned", " ".join(texts(parse(
            charts.render_chart("tiers", summary, SYSTEMS, "light", "en", None, TIERS)))))

    def test_tiers_footnote_says_resources_come_from_the_development_run(self):
        for lang, word in (("en", "development run"), ("zh", "开发集运行")):
            found = " ".join(texts(parse(charts.render_chart("tiers", SUMMARY, SYSTEMS, "dark", lang, None, TIERS))))
            self.assertIn(word, found)

    def test_tiers_title_fits_the_chart_width(self):
        # the renderer measures a little wider than text_width; keep a 10 % margin so the title is never cut
        for lang in charts.LANGS:
            title = charts.TXT[lang]["t_tiers"]
            self.assertLess(charts.text_width(title, charts.SIZE_TITLE) * 1.1, charts.W - 2 * charts.MARGIN, lang)

    def test_tiers_without_data_is_skipped(self):
        self.assertIsNone(charts.render_chart("tiers", SUMMARY, SYSTEMS, "light", "en", None, []))
        self.assertIsNone(charts.render_chart("tiers", SUMMARY, SYSTEMS, "light", "en", None, TIERS[:1]))

    def test_the_tiers_file_is_ordered_by_memory_and_complete(self):
        rows = charts.load_tiers()
        self.assertEqual(len(rows), 6)
        resident = [r["resident_mib"] for r in rows]
        self.assertIsNone(resident[0])
        self.assertEqual(resident[1:], sorted(resident[1:]))
        for row in rows[1:]:
            for key in ("model", "file_mb", "dimensions", "query_ms_p50", "query_ms_p95", "measured"):
                self.assertTrue(row.get(key), (row["system"], key))


REAL = None


def real_summary():
    """bench/results/summary.json, read once; the numbers on the charts must equal it."""
    global REAL
    if REAL is None:
        with open(charts.SUMMARY_PATH, encoding="utf-8") as f:
            REAL = json.load(f)
    return REAL


def real_systems():
    with open(charts.SYSTEMS_PATH, encoding="utf-8") as f:
        return json.load(f)


def render(name, summary=SUMMARY, systems=SYSTEMS, lang="en", theme="light"):
    return parse(charts.render_chart(name, summary, systems, theme, lang))


class NewChartsTest(unittest.TestCase):
    def test_quality_is_sorted_by_delivered_f1_with_three_bars_and_items_per_row(self):
        root = render("quality")
        found = texts(root)
        self.assertEqual([t for t in found if t in ("A", "B", "other-b")], ["A", "other-b", "B"])
        self.assertEqual(len(list(root.iter(SVG + "path"))), 7)        # 3 x 3 bars, two quiet rates are 0 (no bar)
        for expected in ("delivered F1", "precision", "quiet rate", "items/q", "80.0%", "1.20", "20.00"):
            self.assertIn(expected, found)

    def test_quality_shows_a_dash_where_there_is_no_precision(self):
        summary = copy.deepcopy(SUMMARY)
        summary["systems"]["other-b"]["overall"]["precision"] = None
        root = render("quality", summary)
        self.assertEqual(texts(root).count("–"), 1)
        self.assertEqual(len(list(root.iter(SVG + "path"))), 6)

    def test_hit_at_k_draws_coincident_lines_once_and_labels_them_together(self):
        summary = copy.deepcopy(SUMMARY)
        summary["systems"]["other-a"]["overall"]["hit_at_k"] = list(summary["systems"]["fmg-a"]["overall"]["hit_at_k"])
        root = render("hit-at-k", summary)
        self.assertEqual(len(list(root.iter(SVG + "polyline"))), 2)    # fmg-a = other-a, and other-b
        found = texts(root)
        self.assertIn("A = B", found)
        self.assertIn("other-b", found)
        blue = [p for p in root.iter(SVG + "polyline") if p.get("stroke") == charts.PALETTE["light"]["accent"]]
        self.assertEqual(len(blue), 1)                                   # the shared line wears the accent colour

    def test_hit_at_k_has_ten_points_per_line_and_the_axis_runs_0_to_100_percent(self):
        root = render("hit-at-k")
        self.assertEqual(len(list(root.iter(SVG + "polyline"))), 3)
        for line in root.iter(SVG + "polyline"):
            self.assertEqual(len(line.get("points").split()), 10)
        found = texts(root)
        for expected in ("0%", "100%", "1", "10"):
            self.assertIn(expected, found)

    def test_hit_at_k_dash_styles_differ_between_blue_lines(self):
        summary = copy.deepcopy(SUMMARY)
        summary["systems"]["other-a"]["family"] = "feiyu-memgate"
        accent = charts.PALETTE["light"]["accent"]
        dashes = {p.get("stroke-dasharray") for p in render("hit-at-k", summary).iter(SVG + "polyline")
                  if p.get("stroke") == accent}
        self.assertEqual(len(dashes), 2)

    def test_hit_at_k_end_labels_keep_a_gap(self):
        root = render("hit-at-k", real_summary(), real_systems())
        ys = sorted(float(e.get("y")) for e in root.iter(SVG + "text")
                    if float(e.get("x")) > 600 and e.get("class") in ("l", "lb"))
        self.assertGreaterEqual(len(ys), 10)
        self.assertTrue(all(b - a >= 16.9 for a, b in zip(ys, ys[1:])), ys)

    def test_delivered_by_type_uses_the_delivered_hit_in_the_by_type_order(self):
        root = render("delivered-by-type")
        found = texts(root)
        self.assertEqual(len(list(root.iter(SVG + "path"))), 9)
        self.assertEqual([t for t in found if "·" in t and "q" in t][:3],
                         ["lexical · 5 q", "paraphrase · 5 q", "updated · 2 q"])
        self.assertIn("80.0%", found)       # fmg-a: delivered hit 0.9 - 0.1
        self.assertNotIn("90.0%", found)    # ... and not its Hit@5
        names = ("A", "B", "other-b")
        self.assertEqual([t for t in found if t in names], [t for t in texts(render("by-type")) if t in names])

    def test_context_bars_are_sorted_and_labelled_with_characters_and_precision(self):
        found = texts(render("context"))
        self.assertEqual([t for t in found if "chars ·" in t],
                         ["5 chars · 40% correct", "20 chars · 80% correct", "500 chars · 10% correct"])

    def test_context_uses_dots_on_a_log_axis_only_when_one_system_dwarfs_the_rest(self):
        root = render("context")                      # 5 against 500 characters
        self.assertIn("logarithmic", " ".join(texts(root)))
        self.assertEqual(len(list(root.iter(SVG + "path"))), 0)
        summary = copy.deepcopy(SUMMARY)
        for rec in summary["systems"].values():
            rec["overall"]["avg_chars"] = 40.0
        root = render("context", summary)
        self.assertNotIn("logarithmic", " ".join(texts(root)))
        self.assertEqual(len(list(root.iter(SVG + "path"))), 6)       # a light and a full bar per system

    def test_context_without_precision_draws_only_the_tint(self):
        summary = copy.deepcopy(SUMMARY)
        summary["systems"]["other-b"]["overall"]["precision"] = None
        for rec in summary["systems"].values():
            rec["overall"]["avg_chars"] = 40.0
        root = render("context", summary)
        self.assertIn("40 chars · no precision", texts(root))
        self.assertEqual(len(list(root.iter(SVG + "path"))), 5)

    def test_fresh_leaves_systems_with_too_few_questions_to_the_footnote(self):
        root = render("fresh")
        found = texts(root)
        for expected in ("7/7", "9/18", "4/5"):         # fmg-a, other-a, the precise variant
            self.assertIn(expected, found)
        self.assertNotIn("1/2", found)                  # other-b: fewer than 5 questions, not drawn
        self.assertEqual(len(list(root.iter(SVG + "path"))), 3)
        note = " ".join(found)
        self.assertIn("other-b 1/2", note)
        self.assertIn("3/3", note)                      # the other variants are footnoted

    def test_profiles_has_a_pair_of_marks_an_arrow_and_labels_with_the_delivered_hit(self):
        root = render("profiles")
        found = texts(root)
        self.assertEqual(found.count("embedding 90%"), 2)        # one label per profile: hollow precise, filled broad
        self.assertEqual(len(list(root.iter(SVG + "polygon"))), 1)        # one arrowhead per pair
        accent = charts.PALETTE["light"]["accent"]
        marks = [c for c in root.iter(SVG + "circle") if accent in (c.get("fill"), c.get("stroke"))]
        self.assertEqual(len(marks), 2)
        self.assertTrue(any("unlabelled" in t for t in found))

    def test_profiles_without_the_precise_variant_is_skipped(self):
        summary = copy.deepcopy(SUMMARY)
        del summary["systems"]["feiyu-memgate-embedding-precise"]
        self.assertIsNone(charts.render_chart("profiles", summary, SYSTEMS, "light", "en"))

    def test_new_titles_fit_the_chart_width(self):
        for lang in charts.LANGS:
            for key in ("t_quality", "t_hitk", "t_dbytype", "t_context", "t_context_log", "t_fresh", "t_profiles"):
                title = charts.TXT[lang][key]
                self.assertLess(charts.text_width(title, charts.SIZE_TITLE) * 1.1, charts.W - 2 * charts.MARGIN,
                                (lang, key))

    def test_every_new_chart_renders_in_all_four_variants_from_the_real_results(self):
        for name in ("quality", "hit-at-k", "delivered-by-type", "context", "fresh", "profiles"):
            for lang in charts.LANGS:
                for theme in charts.THEMES:
                    svg = charts.render_chart(name, real_summary(), real_systems(), theme, lang)
                    self.assertTrue(texts(parse(svg)), (name, lang, theme))
                    self.assertIn(charts.PALETTE[theme]["surface"], svg)

    def test_numbers_on_the_real_charts_equal_summary_json(self):
        summary, systems = real_summary(), real_systems()
        recs = charts.charted(summary)
        quality = texts(render("quality", summary, systems))
        context = texts(render("context", summary, systems))
        dbt = texts(render("delivered-by-type", summary, systems))
        fresh = texts(render("fresh", summary, systems))
        for rec in recs:
            o = rec["overall"]
            for value in (o["delivered_f1"], o["precision"]):
                self.assertIn(charts.pct(value), quality)
            self.assertIn("%.2f" % o["avg_items"], quality)
            self.assertIn("%d chars · %s correct" % (round(o["avg_chars"]), charts.pct(o["precision"], 0)), context)
            for kind in ("lexical", "paraphrase", "updated"):
                self.assertIn(charts.pct(rec["by_type"][kind]["deployed_hit"]), dbt)
            wins, questions = charts.fresh_counts(rec)
            if questions >= charts.FRESH_MIN:
                self.assertIn("%d/%d" % (wins, questions), fresh)
        profiles = texts(render("profiles", summary, systems))
        for base in charts.PROFILE_BASES:
            for name in (base, base + "-precise"):
                tail = charts.pct(summary["systems"][name]["overall"]["deployed_hit"], 0)
                self.assertTrue(any(t.endswith(tail) for t in profiles), name)

    def test_the_hit_at_k_lines_carry_the_summary_series_inside_the_axis(self):
        summary = real_summary()
        root = render("hit-at-k", summary, real_systems())
        self.assertEqual(len(list(root.iter(SVG + "polyline"))), len(charts.coincident_curves(charts.hit_curves(summary))))
        ys = [float(pt.split(",")[1]) for line in root.iter(SVG + "polyline") for pt in line.get("points").split()]
        self.assertGreaterEqual(min(ys), 112.0 - 0.1)       # 100 % is the top of the plot: nothing is clipped above
        self.assertLessEqual(max(ys), 112.0 + 400.0 + 0.1)

    def test_experiments_and_variants_stay_out_of_the_other_new_charts(self):
        summary, systems = real_summary(), real_systems()
        for name in ("quality", "hit-at-k", "context", "delivered-by-type"):
            found = " ".join(texts(render(name, summary, systems)))
            for hidden in ("bge-small-zh", "unfiltered", "aged", "precise"):
                self.assertNotIn(hidden, found, name)


if __name__ == "__main__":
    unittest.main()
