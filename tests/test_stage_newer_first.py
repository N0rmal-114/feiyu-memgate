"""The newer_first recall stage: among candidates that score alike, the newer dated one ranks first."""
import io
import contextlib
import unittest

from feiyu_memgate import cli, config, registry
from feiyu_memgate.core import stages
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext
from feiyu_memgate.errors import ConfigError
from feiyu_memgate.model import Candidate

FAKE_TOKEN = "newer-first-test-fake-token-0000000000"


class NewerFirstTest(unittest.TestCase):
    def setUp(self):
        self.pool = Pool()
        self.addCleanup(self.pool.close)

    def cands(self, rows):
        """rows: (score, date) or (score, date, text)."""
        out = []
        for i, row in enumerate(rows):
            text = row[2] if len(row) > 2 else "text %d" % i
            out.append(Candidate(score=row[0], source="s", store=None, ref="r%d" % i, text=text, date=row[1]))
        return out

    def run_stage(self, rows, **options):
        stage = stages.NewerFirst(**options)
        out = stage.prepare(self.cands(rows), RecallContext("q", 5, pool=self.pool))
        return sorted((c.ref, c.score) for c in out)  # by ref: the list order is checked on its own

    # -- the rule
    def test_newer_takes_the_higher_score_inside_the_band(self):
        out = self.run_stage([(0.80, "2025-01-01"), (0.78, "2026-01-01")])
        self.assertEqual(out, [("r0", 0.78), ("r1", 0.80)])

    def test_newest_first_among_three(self):
        out = dict(self.run_stage([(0.90, "2025-01-01"), (0.88, "2025-06-01"), (0.86, "2026-01-01")]))
        self.assertEqual((out["r2"], out["r1"], out["r0"]), (0.90, 0.88, 0.86))

    def test_older_with_lower_score_stays_below(self):
        rows = [(0.80, "2026-01-01"), (0.78, "2025-01-01")]
        self.assertEqual(self.run_stage(rows), [("r0", 0.80), ("r1", 0.78)])

    def test_tied_scores_newer_comes_first_after_the_recall_sort(self):
        stage = stages.NewerFirst()
        cands = self.cands([(0.80, "2025-01-01"), (0.80, "2026-01-01"), (0.30, None)])
        out = stage.prepare(cands, RecallContext("q", 5, pool=self.pool))
        out.sort(key=lambda c: -c.score)  # what recall() does next: stable
        self.assertEqual([c.ref for c in out], ["r1", "r0", "r2"])

    def test_places_of_other_candidates_are_kept(self):
        stage = stages.NewerFirst()
        cands = self.cands([(0.80, "2025-01-01"), (0.10, None), (0.79, "2026-01-01"), (0.20, "2027-01-01")])
        out = stage.prepare(cands, RecallContext("q", 5, pool=self.pool))
        self.assertEqual([c.ref for c in out][1], "r1")
        self.assertEqual([c.ref for c in out][3], "r3")

    def test_similarity_is_kept(self):
        stage = stages.NewerFirst()
        cands = self.cands([(0.80, "2025-01-01"), (0.78, "2026-01-01")])
        stage.prepare(cands, RecallContext("q", 5, pool=self.pool))
        self.assertEqual([c.meta["similarity"] for c in cands], [0.80, 0.78])

    def test_existing_similarity_is_not_overwritten(self):
        cands = self.cands([(0.80, "2025-01-01"), (0.78, "2026-01-01")])
        cands[0].meta["similarity"] = 0.99
        stages.NewerFirst().prepare(cands, RecallContext("q", 5, pool=self.pool))
        self.assertEqual(cands[0].meta["similarity"], 0.99)

    # -- invariants
    def test_multiset_of_scores_unchanged(self):
        rows = [(0.95, "2025-03-01"), (0.93, "2024-01-01"), (0.91, None), (0.90, "2026-02-02"),
                (0.89, "bad"), (0.50, "2027-01-01"), (0.92, "2025-12-31")]
        out = self.run_stage(rows)
        self.assertEqual(sorted(s for _, s in out), sorted(r[0] for r in rows))

    def test_undated_and_invalid_dates_untouched(self):
        rows = [(0.90, None), (0.89, "2025-01-01"), (0.88, "not a date"), (0.87, "2026-01-01"), (0.86, "")]
        out = dict(self.run_stage(rows))
        self.assertEqual((out["r0"], out["r2"], out["r4"]), (0.90, 0.88, 0.86))
        self.assertEqual((out["r3"], out["r1"]), (0.89, 0.87))

    def test_outside_band_untouched(self):
        rows = [(0.90, "2025-01-01"), (0.60, "2026-06-01"), (0.89, "2026-01-01")]
        out = dict(self.run_stage(rows, band=0.9))
        self.assertEqual(out["r1"], 0.60)
        self.assertEqual((out["r2"], out["r0"]), (0.90, 0.89))

    def test_band_edge_is_inside(self):
        out = dict(self.run_stage([(1.0, "2025-01-01"), (0.5, "2026-01-01")], band=0.5))
        self.assertEqual((out["r0"], out["r1"]), (0.5, 1.0))

    def test_band_one_only_exchanges_ties(self):
        rows = [(0.80, "2025-01-01"), (0.79, "2026-01-01")]
        self.assertEqual(dict(self.run_stage(rows, band=1.0)), {"r0": 0.80, "r1": 0.79})
        tied = dict(self.run_stage([(0.80, "2025-01-01"), (0.80, "2026-01-01")], band=1.0))
        self.assertEqual(tied, {"r0": 0.80, "r1": 0.80})

    def test_equal_dates_are_stable(self):
        rows = [(0.80, "2025-01-01"), (0.79, "2025-01-01"), (0.78, "2025-01-01")]
        self.assertEqual(self.run_stage(rows), [("r0", 0.80), ("r1", 0.79), ("r2", 0.78)])

    def test_min_days(self):
        rows = [(0.80, "2025-01-01"), (0.79, "2025-01-03")]
        self.assertEqual(dict(self.run_stage(rows, min_days=3)), {"r0": 0.80, "r1": 0.79})
        self.assertEqual(dict(self.run_stage(rows, min_days=2)), {"r0": 0.79, "r1": 0.80})
        self.assertEqual(dict(self.run_stage(rows, min_days=0)), {"r0": 0.79, "r1": 0.80})

    def test_datetime_strings_are_dated(self):
        out = dict(self.run_stage([(0.80, "2025-01-01T10:00:00"), (0.79, "2026-01-01T08:30:00")]))
        self.assertEqual((out["r0"], out["r1"]), (0.79, 0.80))

    def test_nothing_dropped_and_empty_input(self):
        stage = stages.NewerFirst()
        ctx = RecallContext("q", 5, pool=self.pool)
        self.assertEqual(stage.prepare([], ctx), [])
        one = self.cands([(0.7, "2025-01-01")])
        self.assertEqual(stage.prepare(one, ctx), one)
        many = self.cands([(0.9, "2025-01-01"), (0.8, None), (0.1, "2026-01-01")])
        self.assertEqual(len(stage.prepare(many, ctx)), 3)

    def test_non_positive_best_score_is_left_alone(self):
        rows = [(0.0, "2025-01-01"), (-0.1, "2026-01-01")]
        self.assertEqual(self.run_stage(rows), [("r0", 0.0), ("r1", -0.1)])

    # -- related
    def test_related_guard(self):
        rows = [(0.80, "2025-01-01", "lives in Hangzhou"), (0.79, "2026-01-01", "plays the accordion on Sundays")]
        self.assertEqual(dict(self.run_stage(rows, related=0.3)), {"r0": 0.80, "r1": 0.79})
        related = [(0.80, "2025-01-01", "lives in Hangzhou"), (0.79, "2026-01-01", "lives in Suzhou")]
        self.assertEqual(dict(self.run_stage(related, related=0.3)), {"r0": 0.79, "r1": 0.80})

    def test_related_zero_is_off(self):
        rows = [(0.80, "2025-01-01", "aaaa"), (0.79, "2026-01-01", "zzzz")]
        self.assertEqual(dict(self.run_stage(rows, related=0)), {"r0": 0.79, "r1": 0.80})

    # -- options
    def test_option_validation(self):
        for bad in ({"band": 0}, {"band": 1.1}, {"band": True}, {"band": "0.9"}, {"related": -0.1},
                    {"related": 2}, {"related": True}, {"min_days": -1}, {"min_days": float("nan")},
                    {"min_days": True}, {"min_days": "1"}):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                stages.NewerFirst(**bad)
        stage = stages.NewerFirst()
        self.assertEqual((stage.band, stage.related, stage.min_days), (0.85, 0.0, 1.0))
        stages.NewerFirst(band=1, related=1, min_days=0)

    # -- placement
    def test_placement_check(self):
        def built(*names):
            return [registry.create("recall_stage", n) for n in names]
        self.assertEqual(stages.misplaced(built("min_score", "relative_score", "newer_first", "budget")), [])
        self.assertEqual(stages.misplaced(built("min_score", "newer_first", "relative_score")), ["newer_first"])
        self.assertEqual(stages.misplaced(built("newer_first", "min_score")), ["newer_first"])
        self.assertEqual(stages.misplaced(built("min_score", "budget")), [])
        self.assertEqual(stages.misplaced([]), [])

    def test_default_stages_hold_it_right_after_the_thresholds(self):
        names = stages.DEFAULT_STAGES
        self.assertEqual(names[names.index("relative_score") + 1], "newer_first")

    # -- registration
    def test_registered(self):
        stage = registry.create("recall_stage", {"type": "newer_first", "band": 0.8})
        self.assertIsInstance(stage, stages.NewerFirst)
        self.assertEqual((stage.name, stage.band), ("newer_first", 0.8))

    def test_pieces_lists_it(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["pieces"])
        self.assertEqual(code, 0)
        self.assertTrue(any(line.strip().startswith("newer_first") for line in out.getvalue().splitlines()))

    # -- end to end
    def gateway(self, stage_list, backend="sqlite"):
        store = {"type": backend, "path": ":memory:"} if backend == "sqlite" else {"type": backend}
        cfg = {
            "stores": {"private": store},
            "clients": {"owner": {"token_env": "GATEWAY_TOKEN_OWNER", "recall": ["private"], "write": "private"}},
            "recall": {"stages": stage_list},
        }
        gateway = config.build_gateway(cfg, env={"GATEWAY_TOKEN_OWNER": FAKE_TOKEN}, log=lambda line: None)
        self.addCleanup(gateway.close)
        return gateway

    def fill(self, gateway):
        backend = gateway.stores["private"]
        backend.put("住在杭州西湖边的老房子", ref="old", date="2025-01-10")
        backend.put("搬到了苏州住在金鸡湖边的新房子", ref="new", date="2026-05-20")
        backend.put("养了一条黄狗", ref="dog", date="2025-03-03")

    def recall_ids(self, gateway, query="住在哪里的房子"):
        reply = gateway.recall("owner", {"query": query})
        self.assertEqual(reply.status, 200)
        return [r["id"] for r in reply.body["results"]]

    def test_end_to_end_newer_first(self):
        for backend in ("sqlite", "inmemory"):
            with self.subTest(backend=backend):
                base = ["min_score", {"type": "relative_score", "ratio": 0.0}]
                plain = self.gateway(base, backend)
                self.fill(plain)
                on = self.gateway(base + [{"type": "newer_first", "band": 0.5}], backend)
                self.fill(on)
                before, after = self.recall_ids(plain), self.recall_ids(on)
                self.assertEqual(sorted(before), sorted(after))  # the same set is delivered
                self.assertIn("old", after)
                self.assertIn("new", after)
                self.assertLess(after.index("new"), after.index("old"))

    def test_end_to_end_scores_unchanged_as_a_set(self):
        base = ["min_score", {"type": "relative_score", "ratio": 0.0}]
        plain, on = self.gateway(base), self.gateway(base + [{"type": "newer_first", "band": 0.5}])
        self.fill(plain)
        self.fill(on)
        def scores(g):
            return sorted(r["score"] for r in g.recall("owner", {"query": "住在哪里的房子"}).body["results"])

        self.assertEqual(scores(plain), scores(on))


if __name__ == "__main__":
    unittest.main()
