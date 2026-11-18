"""bench/chat/vector_lab.py: the pipeline copy, each variant's arithmetic, the refusals, the cache.

The vectors are two-dimensional unit vectors given as angles, so every expected number can be written down
by hand. Nothing here needs a model; the cache test talks to the fake embeddings server of the backend tests
on 127.0.0.1.
"""
import importlib.util
import io
import json
import math
import os
import sys
import tempfile
import unittest

from feiyu_memgate import registry
from feiyu_memgate.core import stages as stages_module
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall
from feiyu_memgate.model import Hit

from tests.test_backend_embedding import FakeEmbeddingServer

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


lab = _load("vector_lab")


def angle(degrees):
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)]


def world_of(angles, queries, case_extra=None):
    """A world whose memories sit at the given angles (ids = the keys) and a single case per query angle."""
    memories = {mid: {"text": "memory %s" % mid, "date": "2026-01-01", "cluster": None, "role": "filler"}
                for mid in angles}
    cases = []
    for name, (degrees, direct, chain) in queries.items():
        case = {"id": name, "type": "remark-assoc", "history": [], "message": "query %s" % name,
                "direct": direct, "chain": chain, "also_ok": []}
        case.update(case_extra or {})
        cases.append(case)
    vectors = {"memory %s" % mid: angle(deg) for mid, deg in angles.items()}
    vectors.update({"query %s" % name: angle(spec[0]) for name, spec in queries.items()})
    return {"world": "w", "memories": memories, "cases": cases}, vectors


def engine_of(angles, queries, scale=None):
    world, vectors = world_of(angles, queries)
    scale = scale or lab.Scale(0.0, 1.0, 0.3, 0.6)
    return lab.Engine(world, lambda text: vectors[text], scale)


ANGLES = {"a": 0, "b": 20, "c": 60, "d": 120, "e": 200}
QUERIES = {"q1": (5, ["a"], ["c"])}


def cos(x, y):
    return math.cos(math.radians(x - y))


class PipelineCopyTest(unittest.TestCase):
    def test_stage_list_is_the_gateways_default_without_linked(self):
        # the lab's baseline is the pipeline before linked recall: the gateway's default stages minus `linked`
        names = [type(s) for s in lab.default_stages()]
        wanted = [type(registry.create("recall_stage", name)) for name in stages_module.DEFAULT_STAGES
                  if name != "linked"]
        self.assertEqual(names, wanted)
        self.assertIn("linked", stages_module.DEFAULT_STAGES)
        self.assertNotIn(stages_module.Linked, names)

    def test_baseline_delivers_what_the_lines_let_through(self):
        engine = engine_of(ANGLES, QUERIES)
        result = engine.evaluate(engine.cases[0], lab.parse_spec("baseline"))
        # scores (floor 0, ceil 1) are the cosines 0.996, 0.966, 0.574; the line 0.3 keeps three, the ratio line
        # 0.6 x 0.996 = 0.598 drops the third
        self.assertEqual([m for m, _ in result["delivered"]], ["a", "b"])
        self.assertAlmostEqual(result["delivered"][0][1], cos(5, 0), places=6)
        self.assertEqual(result["order"][:3], ["a", "b", "c"])
        self.assertEqual(result["final"], ["a", "b"])

    def test_floor_ceil_mapping_and_the_zero_score_cut(self):
        scale = lab.Scale(0.4, 0.8, 0.3, 0.6)
        self.assertAlmostEqual(scale.score(0.6), 0.5)
        self.assertEqual(scale.score(0.3), 0.0)
        self.assertEqual(scale.score(0.95), 1.0)
        engine = engine_of(ANGLES, QUERIES, scale)
        result = engine.evaluate(engine.cases[0], lab.parse_spec("baseline"))
        # c (cosine 0.574) maps to 0.436: over the 0.3 line but under 0.6 x best; d and e are under the floor
        self.assertEqual([m for m, _ in result["delivered"]], ["a", "b"])

    def test_nothing_reaches_the_line_means_silence(self):
        engine = engine_of({"a": 0, "b": 90}, {"q": (225, [], [])})
        result = engine.evaluate(engine.cases[0], lab.parse_spec("baseline"))
        self.assertEqual(result["delivered"], [])

    def test_limit_cuts_the_list(self):
        angles = {"m%d" % i: i for i in range(8)}  # eight memories within 7 degrees of each other
        engine = engine_of(angles, {"q": (3, [], [])})
        self.assertEqual(len(engine.evaluate(engine.cases[0], lab.parse_spec("baseline"))["final"]), 5)
        self.assertEqual(len(engine.evaluate(engine.cases[0], lab.parse_spec("widen:limit=7"))["final"]), 7)


class WidenTest(unittest.TestCase):
    def test_lower_lines_let_the_third_memory_in(self):
        engine = engine_of(ANGLES, QUERIES)
        case = engine.cases[0]
        self.assertEqual(engine.evaluate(case, lab.parse_spec("widen:ratio=0.5"))["final"], ["a", "b", "c"])
        self.assertEqual(engine.evaluate(case, lab.parse_spec("widen:min_score=0.6,ratio=0.0"))["final"], ["a", "b"])
        self.assertEqual(engine.evaluate(case, lab.parse_spec("widen:min_score=0.0,ratio=0.0"))["final"],
                         ["a", "b", "c"])  # d and e score 0 and are never fetched

    def test_limit(self):
        engine = engine_of(ANGLES, QUERIES)
        result = engine.evaluate(engine.cases[0], lab.parse_spec("widen:ratio=0.0,limit=2"))
        self.assertEqual(result["final"], ["a", "b"])


class SmoothTest(unittest.TestCase):
    def setUp(self):
        self.space = lab.Space([lab.unit32(angle(x)) for x in (0, 20, 60, 120)])

    def expected(self, vectors, i, alpha, neighbours):
        """norm(v_i + alpha * sum cos_ij v_j) written out for 2-d vectors."""
        total = list(vectors[i])
        for j, weight in neighbours:
            total = [t + alpha * weight * v for t, v in zip(total, vectors[j])]
        norm = math.hypot(*total)
        return [t / norm for t in total]

    def test_one_neighbour(self):
        out = lab.smooth(self.space, alpha=1.0, k=1, tau=0.9, rounds=1)
        plain = [angle(x) for x in (0, 20, 60, 120)]
        for i, neighbours in ((0, [(1, cos(0, 20))]), (1, [(0, cos(0, 20))]), (2, []), (3, [])):
            for got, want in zip(out.vectors[i], self.expected(plain, i, 1.0, neighbours)):
                self.assertAlmostEqual(got, want, places=5)

    def test_k_and_tau_pick_the_neighbours(self):
        plain = [angle(x) for x in (0, 20, 60, 120)]
        two = lab.smooth(self.space, alpha=0.5, k=2, tau=0.45, rounds=1)  # a: b (0.94), then c and d tie at 0.5
        want = self.expected(plain, 0, 0.5, [(1, cos(0, 20)), (2, cos(0, 60))])
        for got, expect in zip(two.vectors[0], want):
            self.assertAlmostEqual(got, expect, places=5)
        one = lab.smooth(self.space, alpha=0.5, k=1, tau=0.45, rounds=1)
        want = self.expected(plain, 0, 0.5, [(1, cos(0, 20))])
        for got, expect in zip(one.vectors[0], want):
            self.assertAlmostEqual(got, expect, places=5)
        high = lab.smooth(self.space, alpha=0.5, k=5, tau=0.99, rounds=1)  # nobody that close: nothing changes
        for before, after in zip(self.space.vectors, high.vectors):
            for x, y in zip(before, after):
                self.assertAlmostEqual(x, y, places=6)

    def test_two_rounds_is_applying_it_twice(self):
        twice = lab.smooth(self.space, 0.5, 3, 0.5, 2)
        again = lab.smooth(lab.smooth(self.space, 0.5, 3, 0.5, 1), 0.5, 3, 0.5, 1)
        for x, y in zip(twice.vectors, again.vectors):
            for a, b in zip(x, y):
                self.assertAlmostEqual(a, b, places=6)

    def test_smoothed_vectors_are_unit_and_pull_neighbours_together(self):
        out = lab.smooth(self.space, 1.0, 3, 0.5, 1)
        for vector in out.vectors:
            self.assertAlmostEqual(math.hypot(*vector), 1.0, places=5)
        before = lab._dot(self.space.vectors[0], self.space.vectors[1])
        self.assertGreater(lab._dot(out.vectors[0], out.vectors[1]), before)

    def test_search_over_smoothed_vectors(self):
        # b at 20 and c at 45 are both far from a query at 90, but c pulled towards b and d moves it closer
        engine = engine_of({"b": 20, "c": 45, "d": 70}, {"q": (90, [], [])}, lab.Scale(0.0, 1.0, 0.3, 0.6))
        plain = engine.evaluate(engine.cases[0], lab.parse_spec("baseline"))["cosines"]["b"]
        smoothed = engine.evaluate(engine.cases[0], lab.parse_spec("smooth:alpha=1,k=2,tau=0.5"))["cosines"]["b"]
        self.assertGreater(smoothed, plain)


class SpreadTest(unittest.TestCase):
    def setUp(self):
        self.scale = lab.Scale(0.0, 1.0, 0.3, 0.6)
        self.space = lab.Space([lab.unit32(angle(x)) for x in (0, 25, 50, 180)])

    def test_one_hop(self):
        out = lab.spread([0.9, 0.0, 0.0, 0.0], self.space, self.scale, beta=0.5, k=2, tau=0.8, hops=1)
        self.assertAlmostEqual(out[0], 0.9, places=5)  # b has nothing to give
        self.assertAlmostEqual(out[1], 0.5 * 0.9 * cos(0, 25), places=5)
        self.assertAlmostEqual(out[2], 0.0, places=6)  # a is not a neighbour of c (cosine 0.64 < 0.8)
        self.assertAlmostEqual(out[3], 0.0, places=6)

    def test_two_hops_damp_the_second_by_beta_again(self):
        out = lab.spread([0.9, 0.0, 0.0, 0.0], self.space, self.scale, beta=0.5, k=2, tau=0.8, hops=2)
        self.assertAlmostEqual(out[2], 0.25 * 0.9 * cos(0, 25) * cos(25, 50), places=5)
        self.assertAlmostEqual(out[1], 0.5 * 0.9 * cos(0, 25), places=5)

    def test_clamp_at_one_and_max_not_sum(self):
        out = lab.spread([0.9, 0.9, 0.0, 0.0], self.space, self.scale, beta=1.0, k=2, tau=0.8, hops=1)
        self.assertEqual(out[0], 1.0)
        self.assertEqual(out[1], 1.0)
        # c has neighbour b only (a is too far): 0.9 x cos(25 deg), not a sum over several
        self.assertAlmostEqual(out[2], 0.9 * cos(25, 50), places=5)

    def test_neighbour_similarity_uses_the_score_scale(self):
        scale = lab.Scale(0.4, 0.8, 0.3, 0.6)
        out = lab.spread([0.9, 0.0, 0.0, 0.0], self.space, scale, beta=1.0, k=2, tau=0.5, hops=1)
        self.assertAlmostEqual(out[1], 0.9 * scale.score(cos(0, 25)), places=5)
        far = lab.Space([lab.unit32(angle(x)) for x in (0, 45)])  # cosine 0.707 maps to 0.77 on this scale
        out = lab.spread([0.9, 0.0], far, scale, beta=1.0, k=1, tau=0.5, hops=1)
        self.assertAlmostEqual(out[1], 0.9 * scale.score(cos(0, 45)), places=5)
        self.assertGreater(out[1], 0.9 * cos(0, 45))

    def test_k_limits_the_neighbours(self):
        out = lab.spread([0.0, 0.9, 0.0, 0.0], self.space, self.scale, beta=1.0, k=1, tau=0.5, hops=1)
        # a's single nearest neighbour is b: it gets 0.9 x cos; c's nearest is b as well
        self.assertAlmostEqual(out[0], 0.9 * cos(0, 25), places=5)
        self.assertAlmostEqual(out[2], 0.9 * cos(25, 50), places=5)

    def test_spread_reaches_a_memory_the_query_misses(self):
        angles = {"a": 0, "b": 25, "c": 50, "z": 180}
        engine = engine_of(angles, {"q": (-10, [], ["c"])})
        case = engine.cases[0]
        base = engine.evaluate(case, lab.parse_spec("baseline"))
        self.assertNotIn("c", base["final"])
        spread_result = engine.evaluate(case, lab.parse_spec("spread:beta=1,k=2,tau=0.8,hops=2"))
        self.assertGreater(spread_result["scores"]["c"], base["scores"]["c"])
        self.assertLess(spread_result["order"].index("c"), base["order"].index("c") + 1)


class AppendTest(unittest.TestCase):
    def test_beyond_adds_on_top_of_the_limit(self):
        engine = engine_of(ANGLES, QUERIES)
        result = engine.evaluate(engine.cases[0], lab.parse_spec("append:top=1,minimum=0.45,max_added=2,mode=beyond"))
        self.assertEqual(result["final"], ["a", "b", "c"])
        self.assertEqual(result["added"][0]["source"], "a")
        self.assertAlmostEqual(result["added"][0]["score"], cos(0, 60), places=5)

    def test_within_keeps_to_the_limit(self):
        engine = engine_of(ANGLES, QUERIES)
        case = engine.cases[0]
        full = engine.evaluate(case, lab.parse_spec("widen:limit=2;append:top=1,minimum=0.45,max_added=2,mode=within"))
        self.assertEqual(full["final"], ["a", "b"])
        room = engine.evaluate(case, lab.parse_spec("widen:limit=3;append:top=1,minimum=0.45,max_added=2,mode=within"))
        self.assertEqual(room["final"], ["a", "b", "c"])
        beyond = engine.evaluate(case, lab.parse_spec("widen:limit=2;append:top=1,minimum=0.45,max_added=2,mode=beyond"))
        self.assertEqual(beyond["final"], ["a", "b", "c"])

    def test_minimum_top_and_max_added(self):
        engine = engine_of(ANGLES, QUERIES)
        case = engine.cases[0]
        self.assertEqual(engine.evaluate(case, lab.parse_spec("append:top=1,minimum=0.6"))["added"], [])
        self.assertEqual(engine.evaluate(case, lab.parse_spec("append:top=0,minimum=0.1"))["added"], [])
        self.assertEqual(engine.evaluate(case, lab.parse_spec("append:top=2,minimum=0.1,max_added=1"))["final"],
                         ["a", "b", "c"])

    def test_nothing_delivered_nothing_added(self):
        engine = engine_of({"a": 0, "b": 90}, {"q": (225, [], [])})
        self.assertEqual(engine.evaluate(engine.cases[0], lab.parse_spec("append:minimum=0.0"))["final"], [])

    def test_combination_runs_append_on_smoothed_vectors(self):
        engine = engine_of(ANGLES, QUERIES)
        result = engine.evaluate(engine.cases[0], lab.parse_spec("smooth:alpha=1,k=2,tau=0.5;append:top=1,minimum=0.5"))
        self.assertTrue(set(result["final"]) >= {"a", "b"})


class SpaceStore:
    """A store over the lab's vectors with the backend capability `neighbours`, scored by the lab's own scale: the
    store the gateway's `linked` stage needs to do what the lab's `append` (mode within) does."""

    def __init__(self, engine, scores, order, fetch):
        self.engine = engine
        scale = engine.scale
        self.default_min_score = self.broad_min_score = scale.default_min_score
        self.default_relative_ratio = self.broad_relative_ratio = scale.default_relative_ratio
        self.found = [(mid, scores[mid]) for mid in order[:fetch] if scores[mid] > 0.0]

    def find(self, query, limit, timeout=None, ctl=None):
        position = {mid: i for i, mid in enumerate(self.engine.ids)}
        return [Hit(ref=mid, score=score, text=self.engine.texts[position[mid]], date=self.engine.dates[position[mid]])
                for mid, score in self.found]

    def neighbours(self, ref, limit, timeout=None, ctl=None):
        engine = self.engine
        position = {mid: i for i, mid in enumerate(engine.ids)}
        scored = [(engine.ids[j], engine.scale.score(cosine)) for j, cosine in engine.base.ranked(position[ref])]
        scored = sorted(((m, s) for m, s in scored if s > 0.0), key=lambda p: (-p[1], p[0]))[:limit]
        return [Hit(ref=m, score=s, text=engine.texts[position[m]], date=engine.dates[position[m]]) for m, s in scored]


class LinkedStageParityTest(unittest.TestCase):
    """The gateway's `linked` stage gives the list the lab's `append:top=2,max_added=2,mode=within` gives."""

    WORLD = {"a": 0, "b": 18, "c": 33, "d": 47, "e": 60, "f": 75, "g": 90, "h": 130, "i": 160, "j": 200, "k": 250,
             "l": 300}
    QUERIES = {"q%d" % degrees: (degrees, [], []) for degrees in (1, 20, 40, 66, 88, 100, 135, 170, 230, 280, 330)}

    def final_of_the_gateway(self, engine, case, limit, minimum):
        baseline = engine.evaluate(case, lab.parse_spec("widen:limit=%d" % limit))
        store = SpaceStore(engine, baseline["scores"], baseline["order"], min(limit * 3, 30))
        stage_list = lab.default_stages()
        stage_list.insert(3, stages_module.Linked(top=2, max_added=2, minimum=minimum))
        pool = Pool()
        self.addCleanup(pool.close)
        out = recall({"private": store}, stage_list, RecallContext("q", limit, pool=pool))
        return [item["id"] for item in out["results"]]

    def test_same_final_list_as_the_lab(self):
        scale = lab.Scale(0.0, 1.0, 0.3, 0.6)
        engine = engine_of(self.WORLD, self.QUERIES, scale)
        compared = added = 0
        for limit in (2, 3, 4, 5, 8):
            for minimum in (0.0, 0.5, 0.6, 0.8):
                spec = "widen:limit=%d;append:top=2,minimum=%s,max_added=2,mode=within" % (limit, minimum)
                for case in engine.cases:
                    wanted = engine.evaluate(case, lab.parse_spec(spec))
                    got = self.final_of_the_gateway(engine, case, limit, minimum)
                    self.assertEqual(got, wanted["final"], (spec, case["id"]))
                    compared += 1
                    added += len(wanted["added"])
        self.assertGreater(added, 20)  # the comparison really has additions in it
        self.assertEqual(compared, 5 * 4 * len(self.QUERIES))


class SpecTest(unittest.TestCase):
    def test_round_trip_and_defaults(self):
        config = lab.parse_spec("smooth:alpha=1;append:mode=within")
        self.assertEqual(config["smooth"], {"alpha": 1.0, "k": 3, "tau": 0.6, "rounds": 1})
        self.assertEqual(config["append"]["mode"], "within")
        self.assertEqual(lab.parse_spec(lab.spec_label(config)), config)
        self.assertEqual(lab.spec_label(lab.parse_spec("baseline")), "baseline")
        self.assertEqual(lab.spec_label(lab.parse_spec("widen:limit=5")), "baseline")

    def test_bad_specs(self):
        for text in ("smooth:alpha", "smooth:nope=1", "shrink:alpha=1", "append:mode=sideways", "smooth:k=0",
                     "widen:ratio=2", "smooth:k=1.5", "spread:beta=nan"):
            with self.assertRaises(SystemExit, msg=text):
                lab.parse_spec(text)


class RefusalTest(unittest.TestCase):
    def test_the_test_split_is_refused_unless_final(self):
        for path in ("bench/chat/test", os.path.join("x", "Test"), "bench/chat/test/"):
            with self.assertRaises(SystemExit, msg=path):
                lab.refuse_test(path, False)
            lab.refuse_test(path, True)
        lab.refuse_test("bench/chat/dev", False)

    def test_load_worlds_refuses_before_reading(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "test")
            os.makedirs(folder)
            with self.assertRaises(SystemExit) as context:
                lab.load_worlds(folder)
            self.assertIn("frozen", str(context.exception))

    def test_main_refuses_the_test_split_in_every_mode(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "test")
            os.makedirs(folder)
            cache = os.path.join(root, "cache.json")
            for extra in ([], ["--baseline"], ["--sweep", "all"], ["--explain", "w/q1"]):
                with self.assertRaises(SystemExit):
                    lab.main(["--split", folder, "--cache", cache] + extra)
            self.assertFalse(os.path.exists(cache))

    def test_the_cache_may_not_live_under_bench_chat(self):
        with self.assertRaises(SystemExit):
            lab.refuse_cache_path(os.path.join(_CHAT, "vectors.json"))
        with self.assertRaises(SystemExit):
            lab.refuse_cache_path(os.path.join(_CHAT, "dev", "x", "vectors.json"))
        lab.refuse_cache_path(os.path.join(tempfile.gettempdir(), "vectors.json"))


class CacheTest(unittest.TestCase):
    def test_batches_of_at_most_32_and_no_second_request(self):
        server = FakeEmbeddingServer()
        self.addCleanup(server.stop)
        strings = ["text %d" % i for i in range(70)]
        cache = {"model": "fake", "vectors": {}}
        self.assertEqual(lab.fetch_missing(strings, cache, server.url, "fake"), 3)
        self.assertEqual([len(r["body"]["input"]) for r in server.requests], [32, 32, 6])
        self.assertTrue(all(r["path"].endswith("/embeddings") and r["body"]["model"] == "fake"
                            for r in server.requests))
        self.assertEqual(sorted(cache["vectors"]), sorted(strings))
        self.assertEqual(lab.fetch_missing(strings, cache, server.url, "fake"), 0)
        self.assertEqual(len(server.requests), 3)
        self.assertEqual(lab.fetch_missing(strings + ["one more"], cache, server.url, "fake"), 1)

    def test_missing_vectors_without_a_url_stop_the_run(self):
        with self.assertRaises(SystemExit):
            lab.fetch_missing(["x"], {"model": "m", "vectors": {}}, None, "m")

    def test_cache_file_round_trip_and_model_check(self):
        server = FakeEmbeddingServer()
        self.addCleanup(server.stop)
        world, _ = world_of(ANGLES, QUERIES)
        scale = lab.Scale(0.0, 1.0, 0.3, 0.6)
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "sub", "cache.json")
            out = io.StringIO()
            cache = lab.ensure_cache([world], path, server.url, "fake", scale, lambda line: out.write(line + "\n"))
            self.assertEqual(len(cache["vectors"]), 6)  # five memories and one query
            self.assertIn("memory a", cache["vectors"])
            used = len(server.requests)
            again = lab.ensure_cache([world], path, None, "fake", scale, lambda line: None)  # no url: no request
            self.assertEqual(again["vectors"], json.loads(json.dumps(cache["vectors"])))
            self.assertEqual(len(server.requests), used)
            with open(path, "rb") as handle:
                self.assertNotIn(b"\r", handle.read())
            with self.assertRaises(SystemExit):
                lab.ensure_cache([world], path, None, "another-model", scale, lambda line: None)

    def test_needed_strings_are_the_v0_queries(self):
        world, _ = world_of(ANGLES, {"q1": (5, [], [])})
        world["cases"].append({"id": "f1", "type": "follow-up", "history": [{"role": "user", "content": "我家的猫"}],
                               "message": "后来呢", "direct": [], "chain": [], "also_ok": []})
        world["cases"].append({"id": "n1", "type": "quiet", "history": [], "message": "嗯", "direct": [], "chain": [],
                               "also_ok": []})
        wanted = lab.needed_strings([world], lab.Scale(0.0, 1.0, 0.3, 0.6))
        self.assertIn("query q1", wanted)
        self.assertIn("我家的猫\n后来呢", wanted)  # a short message borrows the previous user turn, as in run.py
        self.assertFalse(any(s == "嗯" for s in wanted))  # a bare filler is not worth a lookup
        self.assertEqual(len(wanted), len(set(wanted)))


class MetricsAndSweepTest(unittest.TestCase):
    def test_summary_uses_run_py_metrics(self):
        engine = engine_of(ANGLES, QUERIES)
        summary = lab.summary_of([engine], lab.parse_spec("baseline"))
        assoc = summary["overall"]["remark-assoc"]
        self.assertEqual(assoc["direct_hit"], 1.0)  # a delivered
        self.assertEqual(assoc["chain_recall"], 0.0)  # c not delivered
        self.assertEqual(assoc["items"], 2.0)
        self.assertEqual(assoc["noise"], 1.0)  # b is in no label
        better = lab.summary_of([engine], lab.parse_spec("append:top=1,minimum=0.45,max_added=1"))
        self.assertEqual(better["overall"]["remark-assoc"]["chain_recall"], 1.0)
        self.assertEqual(better["all"]["added"], 1)

    @staticmethod
    def fake(direct, chain, precision, items):
        return {"overall": {"remark-assoc": {"direct_hit": direct, "chain_recall": chain, "precision": precision}},
                "all": {"items": items}}

    def test_the_rule_and_the_winner(self):
        base = self.fake(0.3, 0.3, 0.4, 3.0)
        rows = [("too wide", self.fake(0.9, 0.9, 0.4, 4.6)),  # 4.6 > 3.0 + 1.5
                ("less precise", self.fake(0.8, 0.8, 0.39, 3.5)),
                ("ok low", self.fake(0.4, 0.3, 0.4, 3.2)),
                ("ok high", self.fake(0.5, 0.4, 0.45, 4.4)),
                ("ok high but more items", self.fake(0.5, 0.4, 0.45, 4.5))]
        self.assertEqual(lab.winner(rows, base)[0], "ok high")
        self.assertIsNone(lab.winner(rows[:2], base))

    def test_sweep_table_has_the_columns_and_one_row_per_setting(self):
        engine = engine_of(ANGLES, QUERIES)
        rows = [(spec, lab.summary_of([engine], lab.parse_spec(spec))) for spec in ("baseline", "widen:ratio=0.5")]
        table = lab.sweep_table(rows, rows[0][1]).split("\n")
        self.assertEqual(len(table), 4)
        self.assertEqual(table[0].count("|"), table[2].count("|"))
        self.assertTrue(table[0].endswith("| mean items | rule |"))
        self.assertIn("| 2.000 |", table[2])
        self.assertIn("| 3.000 |", table[3])

    def test_grids_parse(self):
        for family, grid in lab.GRIDS.items():
            for spec in grid():
                self.assertTrue(lab.parse_spec(spec)[family], spec)
        self.assertTrue(lab.combo_specs({"smooth": "smooth:alpha=1", "spread": None}))


class ExplainTest(unittest.TestCase):
    def test_ids_only_unless_verbose(self):
        engine = engine_of(ANGLES, QUERIES)
        case = engine.cases[0]
        plain = "\n".join(lab.explain(engine, case, ["baseline", "append:top=1,minimum=0.45"]))
        self.assertIn("direct  a: rank 1", plain)
        self.assertIn("chain   c: rank 3", plain)
        self.assertIn("out  ->  rank 3", plain)
        self.assertNotIn("memory a", plain)
        verbose = "\n".join(lab.explain(engine, case, ["baseline"], verbose=True))
        self.assertIn("memory a", verbose)

    def test_find_case(self):
        engine = engine_of(ANGLES, QUERIES)
        self.assertIs(lab.find_case([engine], "q1")[0], engine)
        self.assertIs(lab.find_case([engine], "w/q1")[0], engine)
        with self.assertRaises(SystemExit):
            lab.find_case([engine], "nope")


if __name__ == "__main__":
    unittest.main()
