"""bench/chat/model_lab.py: the product baseline, the three variants, flips, per-kind and rank metrics, the refusals.

The vectors are two-dimensional unit vectors given as angles, so every expected number can be written down by
hand. Nothing here needs a model or a network.
"""
import contextlib
import importlib.util
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_CHAT = os.path.join(_ROOT, "bench", "chat")
_FIXTURE = os.path.join(_ROOT, "tests", "fixtures", "chat", "fixture-world.json")


def _load(name):
    key = "bench_chat_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(_CHAT, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


model_lab = _load("model_lab")
lab = model_lab.lab
run = model_lab.run
methods = model_lab.methods

SCALE = (0.0, 1.0, 0.3, 0.6)


def angle(degrees):
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)]


def cos(x, y):
    return math.cos(math.radians(x - y))


def engine_of(angles, queries, generated=None, extra=None, clusters=None, direct=None):
    """A LabEngine over memories at `angles` (ids = the keys) and one assoc case per query angle. `extra` maps the
    strings of generated texts to angles; `direct`/`clusters` label a case or a memory when a test needs it."""
    memories = {mid: {"text": "memory %s" % mid, "date": "2026-01-01", "cluster": (clusters or {}).get(mid),
                      "role": "filler"} for mid in angles}
    cases = [{"id": name, "type": "remark-assoc", "history": [], "message": "query %s" % name,
              "direct": (direct or {}).get(name, [[], []])[0], "chain": (direct or {}).get(name, [[], []])[1],
              "also_ok": []} for name in queries]
    vectors = {"memory %s" % mid: angle(deg) for mid, deg in angles.items()}
    vectors.update({"query %s" % name: angle(deg) for name, deg in queries.items()})
    vectors.update({text: angle(deg) for text, deg in (extra or {}).items()})
    world = {"world": "w", "memories": memories, "cases": cases}
    return model_lab.LabEngine(world, lambda text: vectors[text], lab.Scale(*SCALE), generated or {})


# six memories; the remark at 5 degrees reaches a and b only, the generated text at 85 degrees reaches c and d
NEAR = {"a": 0, "b": 15, "c": 70, "d": 105, "e": 160, "f": 230}
# five memories crowd round the remark (the limit is reached), z1 and z2 are far from it and close to the text
CROWD = {"m1": 0, "m2": 3, "m3": 6, "m4": 9, "m5": 12, "z1": 80, "z2": 95, "y": 200}


def near():
    return engine_of(NEAR, {"q1": 5}, {"reply": {"w/q1": "reply q1"}}, {"reply q1": 85, "query q1\nreply q1": 60})


def crowd(extra=None, generated=None):
    return engine_of(CROWD, {"q": 6}, generated or {"reply": {"w/q": "reply q"}}, dict({"reply q": 85}, **(extra or {})))


def final_of(engine, spec, case=0):
    return engine.evaluate(engine.cases[case], model_lab.parse_spec(spec))["final"]


class ProductBaselineTest(unittest.TestCase):
    ANGLES = {"m001": 10, "m002": 18, "m003": 100, "m004": 106, "m005": 112, "m006": 190, "m007": 196, "m008": 280,
              "m009": 286, "m010": 50, "m011": 150, "m012": 240}
    QUERY_ANGLES = {"r01": 28, "r02": 118, "r03": 262, "n01": 104, "n02": 193, "f01": 190, "f02": 108, "q01": 70}

    def setUp(self):
        with open(_FIXTURE, encoding="utf-8") as handle:
            self.world = json.load(handle)
        vectors = {m["text"]: angle(self.ANGLES[mid]) for mid, m in self.world["memories"].items()}
        for case in self.world["cases"]:
            query = methods.queries_for("v0", case["history"], case["message"])[0]
            vectors[query] = angle(self.QUERY_ANGLES[case["id"]])
        self.vectors = vectors
        self.scale = lab.Scale(*SCALE)

    def test_product_is_vector_labs_append_within_run(self):
        theirs = lab.Engine(self.world, lambda text: self.vectors[text], self.scale)
        mine = model_lab.LabEngine(self.world, lambda text: self.vectors[text], self.scale)
        reference = lab.parse_spec(model_lab.PRODUCT_SPEC)
        product = model_lab.parse_spec("product")
        added = 0
        for case in self.world["cases"]:
            want, got = theirs.evaluate(case, reference), mine.evaluate(case, product)
            self.assertEqual(got["delivered"], want["delivered"], case["id"])
            self.assertEqual(got["added"], want["added"], case["id"])
            self.assertEqual(got["final"], want["final"], case["id"])
            added += len(want["added"])
        self.assertGreater(added, 0)  # the comparison has linked additions in it
        self.assertEqual(model_lab.run_variant([mine], product)["summary"], lab.summary_of([theirs], reference))

    def test_headline_numbers_are_vector_labs(self):
        mine = model_lab.LabEngine(self.world, lambda text: self.vectors[text], self.scale)
        summary = model_lab.run_variant([mine], model_lab.parse_spec("product"))["summary"]
        shown = model_lab.headline(summary)
        self.assertEqual(shown[:6], list(lab.headline(summary)))
        self.assertEqual(shown[6][0], "named remark recall")
        self.assertEqual(shown[6][1], model_lab._percent(summary["overall"]["remark-named"]["direct_recall"]))

    def test_every_variant_without_texts_is_the_product(self):
        mine = model_lab.LabEngine(self.world, lambda text: self.vectors[text], self.scale)
        product = [r["final"] for r in mine.records(model_lab.parse_spec("product"))]
        for spec in ("second:src=reply", "second:src=reply,merge=rank", "cue:mode=max", "cue2:min=0.1"):
            self.assertEqual([r["final"] for r in mine.records(model_lab.parse_spec(spec))], product, spec)


class SecondTest(unittest.TestCase):
    def test_the_first_lookup_is_the_product_lookup(self):
        engine = near()
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("product"))
        # scores (floor 0, ceil 1): a .996, b .985, c .423; the ratio line 0.6 x .996 drops c; no neighbour reaches 0.6
        self.assertEqual(result["final"], ["a", "b"])
        self.assertIsNone(result["second"])

    def test_fill_appends_the_second_lookups_new_hits_best_first(self):
        engine = near()
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("second:src=reply"))
        self.assertEqual([m for m, _ in result["second"]["items"]], ["c", "d"])  # scores .966 and .940
        self.assertEqual(result["final"], ["a", "b", "c", "d"])
        self.assertEqual([(a["id"], a["kind"]) for a in result["added"]], [("c", "second"), ("d", "second")])
        self.assertAlmostEqual(result["added"][0]["score"], cos(70, 85), places=5)
        self.assertEqual(result["text"], "reply q1")
        self.assertEqual(result["query2"], "reply q1")

    def test_extra_limits_the_additions(self):
        engine = near()
        self.assertEqual(final_of(engine, "second:src=reply,extra=1"), ["a", "b", "c"])
        self.assertEqual(final_of(engine, "second:src=reply,extra=0"), ["a", "b"])

    def test_within_stops_at_the_limit_and_beyond_does_not(self):
        engine = crowd()
        product = final_of(engine, "product")
        self.assertEqual(len(product), 5)  # the limit
        self.assertEqual(final_of(engine, "second:src=reply,mode=within"), product)
        self.assertEqual(final_of(engine, "second:src=reply,mode=beyond"), product + ["z1", "z2"])
        self.assertEqual(final_of(engine, "second:src=reply,mode=beyond,extra=1"), product + ["z1"])

    def test_min_keeps_weak_second_hits_out(self):
        engine = near()
        self.assertEqual(final_of(engine, "second:src=reply,min=0.95"), ["a", "b", "c"])  # d scores .940
        self.assertEqual(final_of(engine, "second:src=reply,min=0.99"), ["a", "b"])
        engine = crowd()
        self.assertEqual(final_of(engine, "second:src=reply,mode=beyond,min=0.99"),
                         final_of(engine, "product") + ["z1"])  # z1 .996, z2 .985

    def test_rank_merges_by_the_higher_score_and_cuts_at_the_limit(self):
        engine = near()
        self.assertEqual(final_of(engine, "second:src=reply,merge=rank"), ["a", "b", "c", "d"])
        engine = crowd()
        merged = final_of(engine, "second:src=reply,merge=rank")
        # m3 1.0, m4 and m2 .9986, z1 .9962, m5 and m1 .9945, z2 .985: the cut at five keeps z1, drops z2
        self.assertEqual(len(merged), 5)
        self.assertIn("z1", merged)
        self.assertNotIn("z2", merged)
        self.assertEqual(merged[:3], ["m3", "m4", "m2"])

    def test_rank_with_min_drops_weak_second_hits_before_the_union(self):
        engine = crowd()
        self.assertNotIn("z1", final_of(engine, "second:src=reply,merge=rank,min=0.999"))

    def test_both_asks_with_the_remark_and_the_text(self):
        engine = near()
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("second:src=reply,q=both"))
        self.assertEqual(result["query2"], "query q1\nreply q1")
        # the joint query sits at 60 degrees (c scores .985): c and d come in as fill
        self.assertEqual(result["final"], ["a", "b", "c", "d"])

    def test_a_missing_or_empty_text_is_the_first_lookup_alone(self):
        for generated in ({}, {"reply": {}}, {"reply": {"w/other": "x"}}, {"reply": {"w/q1": "   "}}):
            engine = engine_of(NEAR, {"q1": 5}, generated, {"reply q1": 85})
            result = engine.evaluate(engine.cases[0], model_lab.parse_spec("second:src=reply"))
            self.assertEqual(result["final"], ["a", "b"], generated)
            self.assertIsNone(result["second"])
            self.assertEqual(result["added"], [])

    def test_the_source_kind_is_chosen_by_src(self):
        engine = engine_of(NEAR, {"q1": 5}, {"reply": {"w/q1": "reply q1"}, "need": {"w/q1": "need q1"}},
                           {"reply q1": 85, "need q1": 15})
        self.assertEqual(final_of(engine, "second:src=need"), ["a", "b"])  # the need text finds a and b again
        self.assertEqual(final_of(engine, "second:src=reply"), ["a", "b", "c", "d"])


class CueTest(unittest.TestCase):
    def cued(self, cues, **kwargs):
        generated = {"cue": {"w/%s" % mid: "cue %s" % mid for mid in cues}}
        return engine_of(NEAR, {"q1": 5}, generated, {"cue %s" % mid: deg for mid, deg in cues.items()}, **kwargs)

    def test_blend_is_the_weighted_cosine_and_a_memory_without_cue_keeps_its_own(self):
        engine = self.cued({"c": 10})
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("cue:mode=blend,w=0.5"))
        self.assertAlmostEqual(result["cosines"]["c"], 0.5 * cos(70, 5) + 0.5 * cos(10, 5), places=5)
        self.assertAlmostEqual(result["cosines"]["a"], cos(0, 5), places=5)
        self.assertAlmostEqual(result["cosines"]["d"], cos(105, 5), places=5)
        self.assertEqual(result["final"], ["a", "b", "c"])  # c's score .709 is over the lines

    def test_a_small_weight_does_not_lift_the_memory_over_the_lines(self):
        engine = self.cued({"c": 10})
        # 0.7 x .423 + 0.3 x .996 = .595 < 0.6 x .996
        self.assertEqual(final_of(engine, "cue:mode=blend,w=0.3"), ["a", "b"])
        self.assertEqual(final_of(engine, "cue:mode=blend,w=0"), ["a", "b"])

    def test_max_counts_the_cue_only_when_it_beats_the_text_by_more_than_the_offset(self):
        engine = self.cued({"c": 10})
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("cue:mode=max,d=0.05"))
        self.assertAlmostEqual(result["cosines"]["c"], cos(10, 5) - 0.05, places=5)
        self.assertEqual(result["final"], ["a", "b", "c"])
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("cue:mode=max,d=0.7"))
        self.assertAlmostEqual(result["cosines"]["c"], cos(70, 5), places=5)  # .996 - .7 = .296 < .423: the text stays
        self.assertEqual(result["final"], ["a", "b"])

    def test_the_cue_text_uses_the_document_prefix(self):
        scale = lab.Scale(*SCALE)
        scale.query_prefix, scale.document_prefix = "Q:", "D:"
        memories = {"a": {"text": "ta", "date": "2026-01-01", "cluster": None, "role": "filler"}}
        case = {"id": "q", "type": "remark-assoc", "history": [], "message": "mq", "direct": [], "chain": [], "also_ok": []}
        vectors = {"D:ta": angle(0), "Q:mq": angle(10), "D:ca": angle(10), "Q:reply": angle(0)}
        engine = model_lab.LabEngine({"world": "w", "memories": memories, "cases": [case]}, lambda t: vectors[t], scale,
                                     {"cue": {"w/a": "ca"}, "reply": {"w/q": "reply"}})
        result = engine.evaluate(case, model_lab.parse_spec("cue:mode=blend,w=1;second:src=reply"))
        self.assertAlmostEqual(result["cosines"]["a"], 1.0, places=5)  # the cue (document prefix) is at the remark's angle
        strings = model_lab.needed_strings([{"world": "w", "memories": memories, "cases": [case]}], scale,
                                           {"cue": {"w/a": "ca"}, "reply": {"w/q": "reply"}},
                                           [model_lab.parse_spec("cue:w=1;second:src=reply")])
        self.assertEqual(set(strings), {"D:ta", "Q:mq", "D:ca", "Q:reply"})

    def test_a_cue_is_not_needed_for_every_memory(self):
        engine = self.cued({})
        self.assertEqual(final_of(engine, "cue:mode=blend,w=0.9"), ["a", "b"])


class Cue2Test(unittest.TestCase):
    def cued(self, cues):
        generated = {"cue": {"w/%s" % mid: "cue %s" % mid for mid in cues}}
        return engine_of(NEAR, {"q1": 5}, generated, {"cue %s" % mid: deg for mid, deg in cues.items()})

    def test_the_best_cue_hits_are_appended(self):
        engine = self.cued({"c": 10, "d": 40})  # cue scores .996 and .819
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("cue2:"))
        self.assertEqual(result["final"], ["a", "b", "c"])  # d is under 0.85 x the best
        self.assertEqual([(a["id"], a["kind"]) for a in result["added"]], [("c", "cue2")])
        self.assertEqual(final_of(engine, "cue2:ratio=0.8"), ["a", "b", "c", "d"])
        self.assertEqual(final_of(engine, "cue2:ratio=0.8,extra=1"), ["a", "b", "c"])

    def test_min_and_ratio_are_both_needed(self):
        engine = self.cued({"c": 10, "d": 40})
        self.assertEqual(final_of(engine, "cue2:ratio=0.8,min=0.9"), ["a", "b", "c"])
        self.assertEqual(final_of(engine, "cue2:min=0.999"), ["a", "b"])

    def test_delivered_memories_are_not_added_again_but_still_set_the_best_score(self):
        engine = self.cued({"a": 3, "c": 40})  # a's cue scores .999 and is delivered; c's .819 is under the ratio
        self.assertEqual(final_of(engine, "cue2:ratio=0.85"), ["a", "b"])
        self.assertEqual(final_of(engine, "cue2:ratio=0.8"), ["a", "b", "c"])

    def test_within_the_limit_or_beyond(self):
        # five memories close to the remark fill the list; the cue memory z1 is far from it
        engine = engine_of(CROWD, {"q": 6}, {"cue": {"w/z1": "cue z1"}}, {"cue z1": 6})
        product = final_of(engine, "product")
        self.assertEqual(len(product), 5)
        self.assertEqual(final_of(engine, "cue2:mode=within"), product)
        self.assertEqual(final_of(engine, "cue2:mode=beyond"), product + ["z1"])

    def test_a_quiet_turn_can_still_get_items(self):
        # nothing reaches the lines for a remark at 225 degrees, but the cue of a is close to it
        engine = engine_of({"a": 0, "b": 90}, {"q": 225}, {"cue": {"w/a": "cue a"}}, {"cue a": 225})
        self.assertEqual(final_of(engine, "product"), [])
        self.assertEqual(final_of(engine, "cue2:"), ["a"])

    def test_combined_with_second_and_cue(self):
        engine = engine_of(NEAR, {"q1": 5}, {"reply": {"w/q1": "reply q1"}, "cue": {"w/e": "cue e"}},
                           {"reply q1": 85, "cue e": 5})
        self.assertEqual(final_of(engine, "second:src=reply,extra=1;cue2:extra=1"), ["a", "b", "c", "e"])
        # with the cue applied first, e's cue (.996 - .05) lifts it over the lines of the first lookup already
        self.assertEqual(final_of(engine, "cue:mode=max,d=0.05;second:src=reply,extra=1;cue2:extra=1"),
                         ["a", "b", "e", "c"])


class SignTestAndFlipsTest(unittest.TestCase):
    @staticmethod
    def record(case_id, kind, final, direct=(), world="w", extra=None):
        out = {"world": world, "id": case_id, "type": kind, "final": list(final),
               "labels": {"direct": list(direct), "chain": [], "also_ok": []}}
        out.update(extra or {})
        return out

    def test_the_sign_test_by_hand(self):
        self.assertEqual(model_lab.sign_test(0, 0), 1.0)
        self.assertEqual(model_lab.sign_test(1, 0), 1.0)  # 2 x (1/2)
        self.assertEqual(model_lab.sign_test(3, 3), 1.0)
        self.assertAlmostEqual(model_lab.sign_test(5, 0), 0.0625)  # 2 x 1/32
        self.assertAlmostEqual(model_lab.sign_test(8, 2), 0.109375)  # 2 x (1 + 10 + 45) / 1024
        self.assertAlmostEqual(model_lab.sign_test(2, 8), 0.109375)
        self.assertAlmostEqual(model_lab.sign_test(10, 0), 2 / 1024)

    def test_flips_of_all_three_kinds(self):
        base = ([self.record("r%d" % i, "remark-assoc", [], ["x"]) for i in range(10)]
                + [self.record("s%d" % i, "remark-assoc", ["x"], ["x"]) for i in range(2)]
                + [self.record("q1", "quiet", []), self.record("q2", "quiet", ["x"]), self.record("q3", "quiet", [])]
                + [self.record("f1", "follow-up", [], ["x"]), self.record("f2", "follow-up", ["x"], ["x"]),
                   self.record("f3", "follow-up", ["x"], ["x"])])
        # eight of the first ten gain the direct memory, both s cases lose it, q1 and q2 change, f1 gains, f2 loses
        variant = ([self.record("r%d" % i, "remark-assoc", ["x"] if i < 8 else [], ["x"]) for i in range(10)]
                   + [self.record("s%d" % i, "remark-assoc", [], ["x"]) for i in range(2)]
                   + [self.record("q1", "quiet", ["y"]), self.record("q2", "quiet", []), self.record("q3", "quiet", [])]
                   + [self.record("f1", "follow-up", ["x"], ["x"]), self.record("f2", "follow-up", [], ["x"]),
                      self.record("f3", "follow-up", ["x"], ["x"])])
        found = model_lab.flips(base, variant)
        self.assertEqual((found["assoc_gained"], found["assoc_lost"]), (8, 2))
        self.assertAlmostEqual(found["assoc_p"], 0.109375)
        self.assertEqual((found["quiet_broken"], found["quiet_gained"]), (1, 1))
        self.assertEqual((found["followup_gained"], found["followup_lost"]), (1, 1))
        same = model_lab.flips(base, base)
        self.assertEqual([same[k] for k in ("assoc_gained", "assoc_lost", "quiet_broken", "quiet_gained",
                                            "followup_gained", "followup_lost")], [0] * 6)
        self.assertEqual(same["assoc_p"], 1.0)

    def test_cases_are_matched_by_world_and_id(self):
        base = [self.record("r1", "remark-assoc", [], ["x"], world="a"), self.record("r1", "remark-assoc", ["x"], ["x"], world="b")]
        variant = [self.record("r1", "remark-assoc", ["x"], ["x"], world="a"), self.record("r1", "remark-assoc", [], ["x"], world="b")]
        found = model_lab.flips(base, variant)
        self.assertEqual((found["assoc_gained"], found["assoc_lost"]), (1, 1))


class KindTableTest(unittest.TestCase):
    def test_labelled_and_unlabelled_cases(self):
        def record(case_id, kind, hit):
            return {"world": "w", "id": case_id, "type": "remark-assoc", "kind": kind, "final": ["x"] if hit else [],
                    "labels": {"direct": ["x"], "chain": [], "also_ok": []}}

        base = [record("a", "craving", True), record("b", "craving", False), record("c", "body", False),
                record("d", None, True), record("e", None, False), record("f", "novel", False)]
        variant = [record("a", "craving", True), record("b", "craving", True), record("c", "body", False),
                   record("d", None, False), record("e", None, False), record("f", "novel", True)]
        quiet = {"world": "w", "id": "z", "type": "quiet", "kind": "craving", "final": [], "labels": {"direct": [], "chain": [], "also_ok": []}}
        rows = model_lab.kind_rows(base + [quiet], variant + [quiet])
        # the known kinds in their order, other labels after them, the cases without a kind last; quiet cases are not counted
        self.assertEqual(rows, [("craving", 2, 0.5, 1.0), ("body", 1, 0.0, 0.0), ("novel", 1, 0.0, 1.0),
                                ("unlabelled", 2, 0.5, 0.0)])

    def test_no_labelled_case_gives_only_unlabelled(self):
        engine = near()
        result = model_lab.run_variant([engine], model_lab.parse_spec("product"))
        rows = model_lab.kind_rows(result["records"], result["records"])
        self.assertEqual([r[0] for r in rows], ["unlabelled"])

    def test_the_kind_travels_from_the_case_to_the_record(self):
        engine = near()
        engine.cases[0]["kind"] = "craving"
        self.assertEqual(engine.records(model_lab.parse_spec("product"))[0]["kind"], "craving")


class RankMetricsTest(unittest.TestCase):
    def setUp(self):
        clusters = {"z1": "k", "z2": "k"}
        direct = {"q": (["z1"], ["z2"]), "p": (["m1"], [])}
        self.generated = {"reply": {"w/q": "reply q"}, "cue": {"w/z1": "cue z1"}}
        self.extra = {"reply q": 85, "cue z1": 8}
        self.engine = engine_of(CROWD, {"q": 6, "p": 0}, self.generated, self.extra, clusters=clusters, direct=direct)

    def test_product_ranks_by_the_remarks_scoring(self):
        ranks = model_lab.rank_metrics([self.engine], model_lab.parse_spec("product"))
        # q: m3 m4 m2 m5 m1 first, then z1 (rank 6); p: m1 is first. Target clusters: {z1, z2} and {m1}
        self.assertEqual(ranks["n"], 2)
        self.assertEqual(ranks["top5"], 0.5)
        self.assertEqual(ranks["median_rank"], 3.5)  # median of 6 and 1

    def test_second_uses_the_maximum_of_the_two_queries_scores(self):
        ranks = model_lab.rank_metrics([self.engine], model_lab.parse_spec("second:src=reply"))
        # q: m3 1.0, m4 and m2 .9986, then z1 .9962 at rank 4; p has no text and keeps rank 1
        self.assertEqual(ranks["top5"], 1.0)
        self.assertEqual(ranks["median_rank"], 2.5)
        case = self.engine.cases[0]
        ranking = self.engine.ranking(case, model_lab.parse_spec("second:src=reply"))
        self.assertEqual(ranking.index("z1"), 3)
        self.assertEqual(ranking.index("z2"), 6)

    def test_cue2_uses_the_maximum_of_text_and_cue_score(self):
        ranks = model_lab.rank_metrics([self.engine], model_lab.parse_spec("cue2:"))
        # z1's cue sits 2 degrees from the remark (.9994): only m3 (1.0) is ahead of it
        ranking = self.engine.ranking(self.engine.cases[0], model_lab.parse_spec("cue2:"))
        self.assertEqual(ranking.index("z1"), 1)
        self.assertEqual(ranks["top5"], 1.0)
        self.assertEqual(ranks["median_rank"], 1.5)  # z1 at rank 2, m1 at rank 1

    def test_the_labelled_memories_stand_in_for_a_world_without_clusters(self):
        engine = engine_of(CROWD, {"q": 6}, {}, None, direct={"q": (["z1"], ["m1"])})
        ranks = model_lab.rank_metrics([engine], model_lab.parse_spec("product"))
        self.assertEqual(ranks["top5"], 1.0)  # m1 is in the top 5
        self.assertEqual(ranks["median_rank"], 6.0)

    def test_only_association_remarks_count(self):
        engine = engine_of(CROWD, {"q": 6}, {}, None, direct={"q": (["z1"], [])})
        engine.cases.append({"id": "n", "type": "remark-named", "history": [], "message": "query q", "direct": ["m1"],
                             "chain": [], "also_ok": []})
        self.assertEqual(model_lab.rank_metrics([engine], model_lab.parse_spec("product"))["n"], 1)


class SweepRuleTest(unittest.TestCase):
    @staticmethod
    def fake(label, direct, chain, precision, items, quiet=4, followups=4):
        records = ([{"world": "w", "id": "q%d" % i, "type": "quiet", "final": [] if i < quiet else ["x"],
                     "labels": {"direct": [], "chain": [], "also_ok": []}} for i in range(4)]
                   + [{"world": "w", "id": "f%d" % i, "type": "follow-up", "final": ["x"] if i < followups else [],
                       "labels": {"direct": ["x"], "chain": [], "also_ok": []}} for i in range(4)])
        summary = {"overall": {"remark-assoc": {"direct_hit": direct, "chain_recall": chain, "precision": precision,
                                                "items": items}}}
        return {"label": label, "summary": summary, "records": records}

    def test_the_rule_and_the_winner(self):
        base = self.fake("product", 0.3, 0.3, 0.4, 3.0)
        rows = [self.fake("too wide", 0.9, 0.9, 0.4, 4.6),  # 4.6 > 3.0 + 1.5
                self.fake("less precise", 0.8, 0.8, 0.39, 3.5),
                self.fake("two quiet lost", 0.9, 0.9, 0.4, 3.5, quiet=2),
                self.fake("two follow-ups lost", 0.9, 0.9, 0.4, 3.5, followups=2),
                self.fake("ok low", 0.4, 0.3, 0.4, 3.2),
                self.fake("one quiet lost", 0.5, 0.4, 0.45, 4.4, quiet=3),
                self.fake("one follow-up lost", 0.5, 0.4, 0.45, 4.4, followups=3),
                self.fake("ok more items", 0.5, 0.4, 0.45, 4.5),
                self.fake("ok same again", 0.5, 0.4, 0.45, 4.4)]
        self.assertEqual([r["label"] for r in rows if model_lab.eligible(r, base)],
                         ["ok low", "one quiet lost", "one follow-up lost", "ok more items", "ok same again"])
        # the highest direct + chain (0.9) among the eligible; fewer items break a tie; grid order breaks the next
        self.assertEqual(model_lab.winner(rows, base)["label"], "one quiet lost")
        self.assertIsNone(model_lab.winner(rows[:4], base))

    def test_grids_parse_into_their_family(self):
        for family, grid in model_lab.GRIDS.items():
            specs = grid("reply-mem")
            self.assertEqual(len(specs), len(set(specs)), family)
            for spec in specs:
                self.assertTrue(model_lab.parse_spec(spec)[family], spec)
                self.assertEqual(model_lab.parse_spec(model_lab.spec_label(model_lab.parse_spec(spec))),
                                 model_lab.parse_spec(spec), spec)
        self.assertTrue(all("src=reply-mem" in s for s in model_lab.grid_second("reply-mem")))

    def test_the_sweep_runs_and_names_a_row(self):
        engine = near()
        lines = []
        rows, winners, base = model_lab.run_sweep([engine], ["second"], "reply", lines.append)
        self.assertEqual(len(rows), len(model_lab.grid_second("reply")))
        self.assertEqual(base["label"], "product")
        table = lines[0].split("\n")
        self.assertEqual(len(table), 2 + 1 + len(rows))
        self.assertTrue(all(line.count("|") == table[0].count("|") for line in table))
        self.assertTrue(table[2].endswith("| base |"))
        text = "\n".join(lines)
        self.assertIn("chosen row per family by the rule", text)
        self.assertIn("precision >= product's", text)
        self.assertIn("second: ", text)
        self.assertIn(winners["second"], [r["label"] for r in rows])  # the fixture has one case, so a row is eligible


class SpecTest(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        config = model_lab.parse_spec("cue:mode=max;second:src=reply,q=both,merge=rank,min=0.5;cue2:extra=1")
        self.assertEqual(config["cue"], {"mode": "max", "w": 0.3, "d": 0.05})
        self.assertEqual(config["second"], {"src": "reply", "q": "both", "merge": "rank", "extra": 2, "mode": "within",
                                            "min": 0.5})
        self.assertEqual(config["cue2"], {"extra": 1, "mode": "within", "min": 0.5, "ratio": 0.85})
        self.assertEqual(model_lab.parse_spec(model_lab.spec_label(config)), config)
        self.assertEqual(model_lab.spec_label(model_lab.parse_spec("")), "product")
        self.assertEqual(model_lab.spec_label(model_lab.parse_spec("baseline")), "product")
        defaults = model_lab.parse_spec("second:src=x")["second"]
        self.assertEqual((defaults["q"], defaults["merge"], defaults["extra"], defaults["mode"], defaults["min"]),
                         ("text", "fill", 2, "within", None))
        self.assertEqual(model_lab.parse_spec("cue:")["cue"], {"mode": "blend", "w": 0.3, "d": 0.05})

    def test_the_label_lists_the_families_in_application_order(self):
        label = model_lab.spec_label(model_lab.parse_spec("cue2:;second:src=r;cue:"))
        self.assertEqual([part.split(":")[0] for part in label.split(";")], ["cue", "second", "cue2"])

    def test_bad_specs(self):
        for text in ("second", "second:src=", "second:src=cue", "second:src=x,q=sideways", "second:src=x,merge=blend",
                     "second:src=x,mode=sideways", "second:src=x,extra=-1", "second:src=x,extra=1.5",
                     "second:src=x,min=nan", "second:src=x,nope=1", "second:src=x,extra", "cue:w=2", "cue:w=-0.1",
                     "cue:mode=within", "cue:d=inf", "cue2:ratio=1.5", "cue2:min=nan", "cue2:mode=blend",
                     "shrink:x=1", "second:src=x;second:src=y", "cue:w=abc"):
            with self.assertRaises(SystemExit, msg=text):
                model_lab.parse_spec(text)


class TextsFileTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, name, content, raw=False):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content if raw else json.dumps(content, ensure_ascii=False))
        return path

    def test_kinds_are_merged_and_empty_texts_dropped(self):
        one = self.write("a.json", {"kind": "reply", "items": {"w/r01": " 天气热可以多吃冰淇淋 ", "w/r02": "second"}})
        two = self.write("b.json", {"kind": "reply", "items": {"w/r02": "   ", "w/r03": "third"}})
        three = self.write("c.json", {"kind": "cue", "items": {"w/m001": "热的时候"}})
        texts = model_lab.load_texts([one, two, three])
        self.assertEqual(texts, {"reply": {"w/r01": "天气热可以多吃冰淇淋", "w/r03": "third"}, "cue": {"w/m001": "热的时候"}})
        self.assertEqual(model_lab.load_texts([]), {})

    def test_wrong_shapes(self):
        bad = [self.write("1.json", ["not", "an", "object"]),
               self.write("2.json", {"items": {}}),
               self.write("3.json", {"kind": "", "items": {}}),
               self.write("4.json", {"kind": 3, "items": {}}),
               self.write("5.json", {"kind": "reply"}),
               self.write("6.json", {"kind": "reply", "items": ["x"]}),
               self.write("7.json", {"kind": "reply", "items": {"w/r01": 5}}),
               self.write("8.json", {"kind": "reply", "items": {"w/r01": None}}),
               self.write("9.json", "{not json", raw=True),
               os.path.join(self.root, "missing.json")]
        for path in bad:
            with self.assertRaises(SystemExit, msg=os.path.basename(path)):
                model_lab.load_texts([path])

    def test_check_sources(self):
        texts = {"reply": {"w/r01": "x"}}
        model_lab.check_sources([model_lab.parse_spec("second:src=reply"), model_lab.parse_spec("product")], texts)
        with self.assertRaises(SystemExit):
            model_lab.check_sources([model_lab.parse_spec("second:src=need")], texts)
        with self.assertRaises(SystemExit):
            model_lab.check_sources([model_lab.parse_spec("cue2:")], texts)
        model_lab.check_sources([model_lab.parse_spec("cue:")], {"cue": {}})


class NeededStringsTest(unittest.TestCase):
    def test_only_the_texts_the_configs_use_are_embedded(self):
        world = {"world": "w", "memories": {"a": {"text": "ta", "date": "2026-01-01"}},
                 "cases": [{"id": "c1", "type": "remark-assoc", "history": [], "message": "mq", "direct": [],
                            "chain": [], "also_ok": []}]}
        texts = {"reply": {"w/c1": "rt", "w/other": "zz"}, "cue": {"w/a": "ca"}}
        scale = lab.Scale(*SCALE)
        base = set(lab.needed_strings([world], scale))
        none = model_lab.needed_strings([world], scale, texts, [model_lab.parse_spec("product")])
        self.assertEqual(set(none), base)
        text = set(model_lab.needed_strings([world], scale, texts, [model_lab.parse_spec("second:src=reply")]))
        self.assertEqual(text, base | {"rt"})
        both = set(model_lab.needed_strings([world], scale, texts, [model_lab.parse_spec("second:src=reply,q=both")]))
        self.assertEqual(both, base | {"mq\nrt"})
        cue = model_lab.needed_strings([world], scale, texts, [model_lab.parse_spec("cue2:")])
        self.assertEqual(set(cue), base | {"ca"})
        self.assertEqual(len(cue), len(set(cue)))


class ExplainTest(unittest.TestCase):
    def test_explain_shows_queries_texts_scorings_and_additions(self):
        engine = engine_of(NEAR, {"q1": 5}, {"reply": {"w/q1": "reply q1"}, "cue": {"w/c": "cue c"}},
                           {"reply q1": 85, "cue c": 10}, direct={"q1": (["a"], ["c"])})
        engine.cases[0]["kind"] = "craving"
        config = model_lab.parse_spec("second:src=reply;cue2:")
        text = "\n".join(model_lab.explain(engine, engine.cases[0], config))
        self.assertIn("case w/q1 (remark-assoc, craving)", text)
        self.assertIn("query: query q1", text)
        self.assertIn("generated text (reply): reply q1", text)
        self.assertIn("top 10 by the remark's query", text)
        self.assertIn("top 10 by the second query", text)
        self.assertIn("top 10 by the cue alone", text)
        self.assertIn("top 10 by the combined scoring", text)
        self.assertIn("1 a score 0.996", text)
        self.assertIn("direct", text)
        self.assertIn("first lookup delivered: ['a', 'b']", text)
        self.assertIn("second lookup delivered: ['c', 'd']", text)
        self.assertIn("final: ['a', 'b', 'c', 'd']", text)
        self.assertNotIn("memory a", text)
        self.assertIn("memory a", "\n".join(model_lab.explain(engine, engine.cases[0], config, verbose=True)))

    def test_a_case_without_a_text_says_so(self):
        engine = engine_of(NEAR, {"q1": 5}, {"reply": {}}, None)
        text = "\n".join(model_lab.explain(engine, engine.cases[0], model_lab.parse_spec("second:src=reply")))
        self.assertIn("none for this case", text)

    def test_find_case(self):
        engine = near()
        self.assertIs(model_lab.find_case([engine], "w/q1")[0], engine)
        self.assertIs(model_lab.find_case([engine], "q1")[0], engine)
        with self.assertRaises(SystemExit):
            model_lab.find_case([engine], "nope")


class NoiseTest(unittest.TestCase):
    """The noise filters in model_lab (their own arithmetic is tested in test_bench_chat_noise.py). The query at 5
    degrees has z 1.1818 for a and 1.1668 for b; the generated text at 85 degrees has a largest z of 1.1186 (c)."""

    def test_the_spec_part(self):
        self.assertIsNone(model_lab.parse_spec("noise")["noise"])
        self.assertIsNone(model_lab.parse_spec("noise:")["noise"])
        config = model_lab.parse_spec("noise:hub=1,k=5,top=2;second:src=reply")
        self.assertEqual(config["noise"], {"hub": 1.0, "k": 5, "top": 2.0, "each": None, "lead": None, "cap": None})
        self.assertEqual(model_lab.spec_label(config),
                         "second:src=reply,q=text,merge=fill,extra=2,mode=within;noise:hub=1.0,k=5,top=2.0")
        self.assertEqual(model_lab.spec_label(model_lab.parse_spec("noise:each=1.5")), "noise:each=1.5")
        self.assertEqual(model_lab.spec_label(model_lab.parse_spec("noise:k=7,hub=0.5")), "noise:hub=0.5,k=7")
        self.assertEqual(model_lab.parse_spec(model_lab.spec_label(config)), config)
        label = model_lab.spec_label(model_lab.parse_spec("noise:top=2;cue2:;second:src=r;cue:"))
        self.assertEqual([part.split(":")[0] for part in label.split(";")], ["cue", "second", "cue2", "noise"])

    def test_lead_and_cap_belong_to_the_session_lab(self):
        for text in ("noise:lead=0.9", "noise:cap=2", "noise:hub=1,cap=1"):
            with self.assertRaises(SystemExit, msg=text) as context:
                model_lab.parse_spec(text)
            self.assertIn("session_lab", str(context.exception))

    def test_bad_noise_values(self):
        for text in ("noise:hub=-1", "noise:hub=x", "noise:k=0", "noise:k=1.5", "noise:top=nan", "noise:each=inf",
                     "noise:nope=1", "noise:top", "noise:top=1;noise:each=1"):
            with self.assertRaises(SystemExit, msg=text):
                model_lab.parse_spec(text)

    def test_the_default_filters_reproduce_the_product_exactly(self):
        with open(_FIXTURE, encoding="utf-8") as handle:
            world = json.load(handle)
        angles = ProductBaselineTest.ANGLES
        vectors = {m["text"]: angle(angles[mid]) for mid, m in world["memories"].items()}
        for case in world["cases"]:
            query = methods.queries_for("v0", case["history"], case["message"])[0]
            vectors[query] = angle(ProductBaselineTest.QUERY_ANGLES[case["id"]])
        engine = model_lab.LabEngine(world, lambda text: vectors[text], lab.Scale(*SCALE))
        product = engine.records(model_lab.parse_spec("product"))
        for spec in ("noise", "noise:hub=0", "noise:top=-100", "noise:each=-100", "noise:hub=0,top=-100,each=-100"):
            self.assertEqual(engine.records(model_lab.parse_spec(spec)), product, spec)
        self.assertEqual(model_lab.run_variant([engine], model_lab.parse_spec("noise:hub=0"))["summary"],
                         model_lab.run_variant([engine], model_lab.parse_spec("product"))["summary"])

    def test_top_silences_the_first_lookup(self):
        engine = near()
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("noise:top=1.5"))
        self.assertEqual((result["final"], result["delivered"], result["added"]), ([], [], []))
        self.assertEqual(final_of(engine, "noise:top=1.18"), ["a", "b"])  # z 1.1818 passes
        self.assertEqual(final_of(engine, "noise:top=1.19"), [])

    def test_each_keeps_what_stands_out(self):
        engine = near()
        self.assertEqual(final_of(engine, "noise:each=1.0"), ["a", "b"])
        self.assertEqual(final_of(engine, "noise:each=1.17"), ["a"])
        self.assertEqual(final_of(engine, "noise:each=1.5"), [])

    def test_the_filters_act_on_the_second_lookup_too(self):
        engine = near()
        self.assertEqual(final_of(engine, "second:src=reply"), ["a", "b", "c", "d"])
        # the first lookup's largest z is 1.1818 and the second's 1.1186: top=1.15 silences only the second
        result = engine.evaluate(engine.cases[0], model_lab.parse_spec("second:src=reply;noise:top=1.15"))
        self.assertEqual(result["final"], ["a", "b"])
        self.assertEqual(result["second"]["items"], [])
        self.assertEqual(result["second_kept"], [])
        self.assertEqual(final_of(engine, "second:src=reply;noise:top=1.1"), ["a", "b", "c", "d"])

    def test_top_and_each_never_add_a_memory(self):
        engine = crowd()
        product = final_of(engine, "product")
        for spec in ("noise:top=0.5", "noise:top=-5", "noise:each=-0.5", "noise:each=0.5", "noise:top=1,each=1"):
            self.assertTrue(set(final_of(engine, spec)) <= set(product), spec)

    def test_hub_lowers_generic_memories_before_the_lines(self):
        # b sits between a and c, the most generic of the three; lowering it can only drop it
        angles = {"a": 0, "b": 10, "c": 20, "d": 90}
        engine = engine_of(angles, {"q": 10})
        case = engine.cases[0]
        before = engine.evaluate(case, model_lab.parse_spec("product"))
        after = engine.evaluate(case, model_lab.parse_spec("noise:hub=50,k=2"))
        self.assertIn("b", [m for m, _ in before["delivered"]])
        self.assertNotIn("b", [m for m, _ in after["delivered"]])  # its score is 0 after the penalty
        # the linked step still reaches b through its unpenalised neighbours: the penalty acts on scores only
        self.assertIn("b", [a["id"] for a in after["added"]])
        self.assertEqual(final_of(engine, "noise:hub=0,k=2"), before["final"])

    def test_the_noise_grid_and_its_sweep(self):
        specs = model_lab.grid_noise()
        self.assertEqual(len(specs), 12 + 5 + 4)
        self.assertEqual(specs[:3], ["noise:hub=0.25,k=5", "noise:hub=0.25,k=10", "noise:hub=0.25,k=20"])
        self.assertEqual(specs[12:17], ["noise:top=%s" % t for t in (1.5, 2.0, 2.5, 3.0, 3.5)])
        self.assertEqual(specs[17:], ["noise:each=%s" % z for z in (0.5, 1.0, 1.5, 2.0)])
        engine = near()
        lines = []
        rows, winners, base = model_lab.run_sweep([engine], ["noise"], None, lines.append)
        self.assertEqual([r["label"] for r in rows], specs)
        table = lines[0].split("\n")
        self.assertEqual(len(table), 2 + 1 + len(specs))
        self.assertTrue(all(line.count("|") == table[0].count("|") for line in table))
        self.assertTrue(all(line.endswith("| - |") for line in table[3:]))  # no rule marks a row
        text = "\n".join(lines)
        self.assertNotIn("chosen row per family", text)
        self.assertIn("no rule picks a row for: noise", text)
        self.assertIsNone(winners["noise"])
        self.assertIn("assoc lost", table[0])
        self.assertIn("follow-up lost", table[0])

    def test_a_mixed_sweep_still_picks_rows_for_the_other_families(self):
        engine = near()
        lines = []
        _, winners, _ = model_lab.run_sweep([engine], ["second", "noise"], "reply", lines.append)
        text = "\n".join(lines)
        self.assertIn("chosen row per family by the rule", text)
        self.assertIn("  second: ", text)
        self.assertNotIn("  noise: ", text)
        self.assertIsNotNone(winners["second"])


class DiagnoseTest(unittest.TestCase):
    def engine(self):
        engine = engine_of(NEAR, {"a1": 5, "a2": 85, "q1": 300}, direct={"a1": (["a"], []), "a2": (["c"], [])})
        engine.cases[2]["type"] = "quiet"
        return engine

    def test_the_numbers_by_hand(self):
        found = model_lab.diagnose_noise([self.engine()])
        assoc = found["types"]["remark-assoc"]
        # largest z: 1.1818 (a1) and 1.1186 (a2); the quiet query at 300 degrees has 1.205, higher than both
        self.assertEqual((assoc["n"], assoc["skipped"]), (2, 0))
        self.assertAlmostEqual(assoc["median"], (1.1818 + 1.1186) / 2, places=3)
        self.assertAlmostEqual(assoc["q1"], 1.1186 + 0.25 * (1.1818 - 1.1186), places=3)
        self.assertAlmostEqual(assoc["q3"], 1.1186 + 0.75 * (1.1818 - 1.1186), places=3)
        self.assertEqual(found["types"]["quiet"]["n"], 1)
        self.assertEqual(found["auc_z"], 0.0)  # the quiet case stands out more than either remark
        self.assertEqual(found["auc_top_score"], 1.0)  # but the plain top score tells them apart: .996 and .966 against .5
        self.assertEqual(found["direct_z"]["n"], 2)
        self.assertEqual(found["direct_z"]["shares"], {"1.0": 1.0, "1.5": 0.0, "2.0": 0.0})

    def test_cases_without_a_query_are_counted_apart(self):
        engine = self.engine()
        engine.cases.append({"id": "n1", "type": "quiet", "history": [], "message": "ok", "direct": [], "chain": [],
                             "also_ok": []})
        found = model_lab.diagnose_noise([engine])
        self.assertEqual(found["types"]["quiet"], dict(found["types"]["quiet"], n=1, skipped=1))

    def test_the_lines(self):
        text = "\n".join(model_lab.diagnose_lines(model_lab.diagnose_noise([self.engine()])))
        for wanted in ("| remark-assoc | 2 | 1.15 |", "| quiet | 1 | 1.21 |", "ROC-AUC", "| largest z | 0.000 |",
                       "| plain top score (the product's score of the best memory) | 1.000 |",
                       "| 1.0 | 100.0 |", "| 1.5 | 0.0 |", "| 2.0 | 0.0 |", "share (%) of 2"):
            self.assertIn(wanted, text)

    def test_no_quiet_cases_means_no_auc(self):
        engine = engine_of(NEAR, {"a1": 5}, direct={"a1": (["a"], [])})
        found = model_lab.diagnose_noise([engine])
        self.assertIsNone(found["auc_z"])
        self.assertIsNone(found["auc_top_score"])
        self.assertIn("| largest z | - |", "\n".join(model_lab.diagnose_lines(found)))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.split = os.path.join(self.root, "dev")
        os.makedirs(self.split)
        shutil.copy(_FIXTURE, os.path.join(self.split, "fixture-world.json"))
        self.cache = os.path.join(self.root, "cache.json")
        self.replies = self.write("replies.json", {"kind": "reply-mem", "items": {
            "fixture-demo/r01": "天气热可以多吃冰淇淋", "fixture-demo/r02": "养猫的人很爱它", "fixture-demo/r03": "晚上出去跑跑步"}})
        self.cues = self.write("cues.json", {"kind": "cue", "items": {
            "fixture-demo/m001": "天气很热的时候", "fixture-demo/m003": "说到家里的宠物"}})

    def write(self, name, content):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(content, handle, ensure_ascii=False)
        return path

    def fill_cache(self, specs, drop=()):
        with open(_FIXTURE, encoding="utf-8") as handle:
            world = json.load(handle)
        texts = model_lab.load_texts([self.replies, self.cues])
        scale = lab.Scale.for_model("bge-m3")
        strings = model_lab.needed_strings([world], scale, texts, [model_lab.parse_spec(s) for s in specs])
        vectors = {s: angle((sum(map(ord, s)) * 37) % 360) for s in strings if s not in drop}
        with open(self.cache, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"model": "bge-m3", "vectors": vectors}, handle)

    def call(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = model_lab.main(argv)
        return code, out.getvalue()

    def test_a_run_prints_every_part_of_the_report_and_writes_the_json(self):
        specs = ["second:src=reply-mem;cue2:min=0.5", "cue:mode=max"]
        self.fill_cache(specs)
        out_path = os.path.join(self.root, "out", "result.json")
        argv = ["--split", self.split, "--cache", self.cache, "--texts", self.replies, "--texts", self.cues,
                "--baseline", "--table", "--explain", "fixture-demo/r01", "--out", out_path]
        for spec in specs:
            argv += ["--run", spec]
        code, text = self.call(argv)
        self.assertEqual(code, 0)
        for wanted in ("texts reply-mem: 3 of 8 cases", "texts cue: 2 of 12 memories", "assoc direct hit", "named remark recall",
                       "flips against product", "sign test on the association flips", "| kind |", "unlabelled",
                       "best cluster member in the top 5", "median rank of the fact", "| world |", "fixture-demo",
                       "variant: second:src=reply-mem", "variant: cue:mode=max", "case fixture-demo/r01"):
            self.assertIn(wanted, text)
        with open(out_path, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["split"], "dev")
        self.assertEqual(len(document["runs"]), 2)
        self.assertEqual({"spec", "summary", "ranks", "flips", "kinds", "records"}, set(document["runs"][0]))
        self.assertIn("product", document)
        with open(out_path, "rb") as handle:
            self.assertNotIn(b"\r", handle.read())

    def test_the_baseline_alone_needs_no_texts(self):
        self.fill_cache([])
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--baseline"])
        self.assertEqual(code, 0)
        self.assertIn("| assoc direct hit |", text)

    def test_a_sweep_prints_the_table_and_the_rule(self):
        self.fill_cache(["second:src=reply-mem,q=both", "second:src=reply-mem"])
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--texts", self.replies,
                                "--sweep", "second", "--src", "reply-mem"])
        self.assertEqual(code, 0)
        self.assertIn("chosen row per family by the rule", text)
        self.assertIn("| product |", text)
        self.assertIn("second:src=reply-mem,q=both,merge=rank,min=0.6", text)

    def test_a_noise_sweep_prints_the_table_without_a_rule(self):
        self.fill_cache(model_lab.grid_noise())
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--sweep", "noise"])
        self.assertEqual(code, 0)
        self.assertIn("| product |", text)
        self.assertIn("| noise:hub=0.25,k=5 |", text)
        self.assertIn("| noise:each=2.0 |", text)
        self.assertNotIn("chosen row per family", text)

    def test_diagnose_prints_the_study_and_refuses_the_test_split_even_with_final(self):
        self.fill_cache([])
        out_path = os.path.join(self.root, "diag.json")
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--diagnose", "noise", "--out", out_path])
        self.assertEqual(code, 0)
        for wanted in ("| case type |", "| remark-assoc |", "| quiet |", "ROC-AUC", "| largest z |", "| plain top score"):
            self.assertIn(wanted, text)
        with open(out_path, encoding="utf-8") as handle:
            self.assertEqual(set(json.load(handle)["diagnose"]), {"types", "auc_z", "auc_top_score", "direct_z"})
        folder = os.path.join(self.root, "test")
        os.makedirs(folder)
        for extra in ([], ["--final"]):
            with self.assertRaises(SystemExit) as context:
                model_lab.main(["--split", folder, "--cache", self.cache, "--diagnose", "noise"] + extra)
            self.assertIn("--diagnose", str(context.exception))
        with self.assertRaises(SystemExit):
            model_lab.refuse_diagnose(os.path.join(self.root, "x", "Test"))
        model_lab.refuse_diagnose(self.split)
        with self.assertRaises(SystemExit):
            model_lab.main(["--split", self.split, "--cache", self.cache, "--diagnose", "other"])

    def test_the_noise_sweep_refuses_the_test_split_even_with_final(self):
        folder = os.path.join(self.root, "test")
        os.makedirs(folder)
        for extra in ([], ["--final"]):
            with self.assertRaises(SystemExit) as context:
                model_lab.main(["--split", folder, "--cache", self.cache, "--sweep", "noise"] + extra)
            self.assertIn("--sweep", str(context.exception))

    def test_lead_and_cap_are_refused_on_the_command_line(self):
        self.fill_cache([])
        for spec in ("noise:lead=0.9", "noise:cap=2"):
            with self.assertRaises(SystemExit):
                model_lab.main(["--split", self.split, "--cache", self.cache, "--run", spec])

    def test_a_missing_vector_stops_the_run(self):
        self.fill_cache(["second:src=reply-mem"], drop=("天气热可以多吃冰淇淋",))
        with self.assertRaises(SystemExit):
            self.call(["--split", self.split, "--cache", self.cache, "--texts", self.replies,
                       "--run", "second:src=reply-mem"])

    def test_sweep_refuses_the_test_split_even_with_final(self):
        folder = os.path.join(self.root, "test")
        os.makedirs(folder)
        for extra in ([], ["--final"]):
            with self.assertRaises(SystemExit) as context:
                model_lab.main(["--split", folder, "--cache", self.cache, "--sweep", "cue"] + extra)
            self.assertIn("--sweep", str(context.exception))
        self.assertFalse(os.path.exists(self.cache))
        with self.assertRaises(SystemExit):
            model_lab.refuse_sweep(os.path.join(self.root, "x", "Test"))
        model_lab.refuse_sweep(self.split)

    def test_the_test_split_needs_final_in_every_other_mode(self):
        folder = os.path.join(self.root, "test")
        os.makedirs(folder)
        for extra in ([], ["--baseline"], ["--run", "product"], ["--run", "product", "--explain", "w/q1"]):
            with self.assertRaises(SystemExit):
                model_lab.main(["--split", folder, "--cache", self.cache] + extra)
        self.assertFalse(os.path.exists(self.cache))

    def test_argument_errors(self):
        for extra in (["--sweep", "second"], ["--sweep", "nope"], ["--explain", "w/q1"], ["--run", "second:src=nope"],
                      ["--run", "cue:"], ["--run", "second"], ["--sweep", "cue"]):
            with self.assertRaises(SystemExit, msg=" ".join(extra)):
                model_lab.main(["--split", self.split, "--cache", self.cache, "--texts", self.replies] + extra)

    def test_the_cache_may_not_live_under_bench_chat(self):
        with self.assertRaises(SystemExit):
            model_lab.main(["--split", self.split, "--cache", os.path.join(_CHAT, "vectors.json"), "--baseline"])
        self.assertFalse(os.path.exists(os.path.join(_CHAT, "vectors.json")))


if __name__ == "__main__":
    unittest.main()
