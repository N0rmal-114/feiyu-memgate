"""bench/chat/session_lab.py: the replay of sessions, the product, the variants, flips, the tables and the refusals.

Memories are orthogonal unit vectors (one axis each) and every query is a mix of a few axes, so which memories a
lookup finds can be written down by hand: with the scale used here a query of weights {m1: 1, m2: 0.8} finds m1 and m2
(scores .78 and .63), and nothing else. Nothing here needs a model or a network.
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


session_lab = _load("session_lab")
model_lab = session_lab.model_lab
lab = session_lab.lab

SCALE = (0.0, 1.0, 0.3, 0.6)
DIM = 12
CLUSTERS = {"m1": "c1", "m2": "c1", "m3": "c2", "m4": "c2", "m5": "c3", "m6": "c3"}  # m7 and m8 belong to none
MEMORIES = ["m%d" % i for i in range(1, 9)]


def axis(weights):
    """A vector with the given weight on the axis of every memory named (m1 is axis 0)."""
    vector = [0.0] * DIM
    for mid, weight in weights.items():
        vector[int(mid[1:]) - 1] = float(weight)
    return vector


def turn(kind, user, cluster, direct, also_ok=(), new=False):
    out = {"kind": kind, "user": user, "assistant": "a reply", "cluster": cluster, "direct": list(direct),
           "also_ok": list(also_ok)}
    if new:
        out["new_conversation"] = True
        out["gap_days"] = 1
    return out


def world_of(sessions, name="w"):
    memories = {mid: {"text": "item%s" % mid[1:], "date": "2026-01-01", "cluster": CLUSTERS.get(mid), "role": "filler"}
                for mid in MEMORIES}
    return {"world": name, "persona": "p", "clusters": {}, "memories": memories, "cases": [], "sessions": sessions}


def engine_of(sessions, vectors=None):
    """A SessionEngine; `vectors` maps a user line or a reply text to {memory id: weight}."""
    table = {"item%s" % mid[1:]: axis({mid: 1}) for mid in MEMORIES}
    table.update({text: axis(weights) for text, weights in (vectors or {}).items()})
    return session_lab.SessionEngine(world_of(sessions), lambda text: table[text], lab.Scale(*SCALE))


def replay(engine, spec, replies=None, session=0, trace=False):
    return session_lab.replay_session(engine, engine.sessions[session], session_lab.parse_spec(spec), replies or {}, trace)


def ids(records, block):
    return [[m["id"] for m in r[block]] for r in records]


# Six turns in two conversations; m1 and m2 are the cluster c1, the idle turn is a line that is not worth a lookup.
S = {"id": "s1", "turns": [
    turn("open", "chat one alpha", "c1", ["m1"]),
    turn("continue", "chat two alpha", "c1", ["m2"]),
    turn("continue", "chat three alpha", "c1", ["m1"]),
    turn("idle", "ok", None, []),
    turn("nextday", "chat five alpha", "c1", ["m1"], new=True),
    turn("continue", "chat six alpha", "c1", ["m2"]),
]}
S_VECTORS = {"chat one alpha": {"m1": 1}, "chat two alpha": {"m1": 1, "m2": 0.8}, "chat three alpha": {"m1": 1},
             "chat five alpha": {"m1": 1}, "chat six alpha": {"m1": 1, "m2": 0.8},
             "reply two": {"m3": 1, "m4": 0.8}, "reply three": {"m1": 1, "m7": 0.8}, "reply four": {"m6": 1},
             "reply five": {"m1": 1, "m2": 0.8, "m5": 0.7}}
S_REPLIES = {"w/s1/2": "reply two", "w/s1/3": "reply three", "w/s1/4": "reply four", "w/s1/5": "reply five"}


def s_engine():
    return engine_of([S], S_VECTORS)


class ProductTest(unittest.TestCase):
    def test_a_memory_in_the_history_is_not_given_again_and_a_new_conversation_forgets(self):
        records = replay(s_engine(), "product")
        # t3 and t4 bring nothing (m1 is in front, the idle line is no query); t5 is a new conversation: m1 again
        self.assertEqual(ids(records, "main"), [["m1"], ["m2"], [], [], ["m1"], ["m2"]])
        self.assertEqual(ids(records, "extra"), [[]] * 6)
        self.assertEqual([r["shown"] for r in records], [1, 2, 2, 2, 1, 2])
        self.assertEqual([r["conversation"] for r in records], [1, 1, 1, 1, 2, 2])
        self.assertEqual([r["new_conversation"] for r in records], [False] * 4 + [True, False])
        self.assertEqual([r["last_of_conversation"] for r in records], [False, False, False, True, False, True])
        self.assertIsNone(records[3]["query"])

    def test_in_front_and_fresh_are_not_the_same(self):
        records = replay(s_engine(), "product")
        # t3: m1 is in front from turn 1 but did not arrive now; the idle turn has no direct memory at all
        self.assertEqual([r["in_front"] for r in records], [True, True, True, None, True, True])
        self.assertEqual([r["fresh"] for r in records], [True, True, False, None, True, True])

    def test_the_main_lookup_is_the_product_lookup_of_the_v0_query(self):
        engine = s_engine()
        records = replay(engine, "product")
        self.assertEqual(records[1]["query"], "chat two alpha")
        self.assertEqual([(m["id"], round(m["score"], 3)) for m in records[1]["main"]], [("m2", 0.625)])
        want = engine.lookup(engine.cosines_for("chat two alpha", None))["items"]
        self.assertEqual([(m, round(s, 6)) for m, s in want], [("m1", 0.780869), ("m2", 0.624695)])

    def test_a_short_line_borrows_the_previous_line_of_its_own_conversation_only(self):
        session = {"id": "s", "turns": [turn("open", "chat one alpha", "c1", ["m1"]),
                                        turn("idle", "ok then", None, []),
                                        turn("nextday", "ok then", "c1", ["m1"], new=True)]}
        self.assertEqual(session_lab.turn_queries(session),
                         [(1, "chat one alpha"), (2, "chat one alpha\nok then"), (3, "ok then")])

    def test_labels_and_relations_of_the_arrivals(self):
        records = replay(s_engine(), "product")
        self.assertEqual(records[1]["main"][0]["related"], True)  # m2 belongs to the turn's cluster c1
        self.assertEqual((records[1]["cluster"], records[1]["prev_cluster"]), ("c1", "c1"))
        self.assertEqual((records[0]["prev_cluster"], records[4]["prev_cluster"]), (None, None))  # t4 is idle
        self.assertEqual(records[1]["direct"], ["m2"])

    def test_a_memory_that_is_labelled_also_ok_counts_as_related(self):
        session = {"id": "s", "turns": [turn("open", "chat one alpha", None, [], also_ok=["m7"])]}
        engine = engine_of([session], {"chat one alpha": {"m7": 1}})
        self.assertEqual(replay(engine, "product")[0]["main"][0]["related"], True)
        session["turns"][0]["also_ok"] = []
        self.assertEqual(replay(engine_of([session], {"chat one alpha": {"m7": 1}}), "product")[0]["main"][0]["related"],
                         False)


class AfterTest(unittest.TestCase):
    def test_the_reply_lookup_arrives_one_turn_late_and_never_repeats_what_is_shown(self):
        records = replay(s_engine(), "after:max=2", S_REPLIES)
        # reply 2 finds m3 and m4 (t3); reply 3 finds m1 (shown) and m7 (the idle t4); reply 4's m6 is lost at the new
        # conversation; reply 5 finds m1 (shown), m2 and m5, and m2 is this turn's own main (t6): only m5 is extra
        self.assertEqual(ids(records, "extra"), [[], [], ["m3", "m4"], ["m7"], [], ["m5"]])
        self.assertEqual(ids(records, "main"), [["m1"], ["m2"], [], [], ["m1"], ["m2"]])
        self.assertEqual([r["left"] for r in records], [[], ["m3", "m4"], ["m7"], ["m6"], ["m2", "m5"], []])
        self.assertEqual([e["from"] for e in records[2]["extra"]], ["after", "after"])
        self.assertEqual([r["shown"] for r in records], [1, 2, 4, 5, 1, 3])

    def test_max_limits_what_is_left(self):
        records = replay(s_engine(), "after:max=1", S_REPLIES)
        self.assertEqual(ids(records, "extra"), [[], [], ["m3"], ["m7"], [], []])
        self.assertEqual([r["left"] for r in records][1], ["m3"])

    def test_the_leftovers_are_dropped_at_a_new_conversation(self):
        records = replay(s_engine(), "after:max=2", S_REPLIES)
        self.assertEqual(records[3]["left"], ["m6"])
        self.assertNotIn("m6", [m["id"] for m in records[4]["extra"]])

    def test_a_turn_without_a_reply_text_leaves_nothing(self):
        records = replay(s_engine(), "after:max=2", {"w/s1/3": "reply three"})
        self.assertEqual(ids(records, "extra"), [[], [], [], ["m7"], [], []])
        self.assertEqual(replay(s_engine(), "after:max=2", {}), replay(s_engine(), "product"))

    def test_the_reply_text_is_only_a_query_and_the_lookup_is_the_product_lookup(self):
        engine = s_engine()
        records = replay(engine, "after:max=3", {"w/s1/5": "reply five"})
        self.assertEqual(records[4]["left"], ["m2", "m5"])  # the lookup's own order: scores .548 and .480
        self.assertEqual([(m, round(s, 3)) for m, s in engine.found("reply five")],
                         [("m1", 0.685), ("m2", 0.548), ("m5", 0.48)])

    def test_the_extra_block_holds_a_memory_once(self):
        session = {"id": "s", "turns": [turn("open", "chat one alpha", "c1", ["m1"]),
                                        turn("continue", "chat two alpha", "c1", ["m2"])]}
        vectors = {"chat one alpha": {"m1": 1}, "chat two alpha": {"m1": 1, "m2": 0.8}, "reply one": {"m2": 1, "m3": 0.8}}
        records = replay(engine_of([session], vectors), "after:max=2", {"w/s/1": "reply one"})
        self.assertEqual(ids(records, "main"), [["m1"], ["m2"]])
        self.assertEqual(ids(records, "extra"), [[], ["m3"]])  # m2 is the main of that turn

    def test_the_turn_numbers_run_through_the_whole_session(self):
        # turn 5 is the first turn of the second conversation, and its reply is "w/s1/5"
        records = replay(s_engine(), "after:max=2", {"w/s1/5": "reply five"})
        self.assertEqual(ids(records, "extra"), [[], [], [], [], [], ["m5"]])  # m2 is the main block of turn 6


class CompactTest(unittest.TestCase):
    def test_compaction_empties_shown_before_the_turns_it_names(self):
        records = replay(s_engine(), "compact:every=2")
        # conversation 1: turns 1 to 4; before turn 3 ((3 - 1) % 2 == 0) the blocks are gone and m1 comes again
        self.assertEqual(ids(records, "main"), [["m1"], ["m2"], ["m1"], [], ["m1"], ["m2"]])
        self.assertEqual([r["shown"] for r in records], [1, 2, 1, 1, 1, 2])
        self.assertEqual([r["fresh"] for r in records], [True, True, True, None, True, True])

    def test_every_three_hits_turn_four_and_a_new_conversation_counts_from_one(self):
        records = replay(s_engine(), "compact:every=3")
        self.assertEqual([r["shown"] for r in records], [1, 2, 2, 0, 1, 2])
        records = replay(s_engine(), "compact:every=1")
        self.assertEqual(ids(records, "main"), [["m1"], ["m1", "m2"], ["m1"], [], ["m1"], ["m1", "m2"]])

    def test_the_history_lines_stay_for_the_query(self):
        session = {"id": "s", "turns": [turn("open", "chat one alpha", "c1", ["m1"]), turn("idle", "ok then", None, [])]}
        records = replay(engine_of([session], {"chat one alpha": {"m1": 1}, "chat one alpha\nok then": {"m1": 1}}),
                         "compact:every=1")
        self.assertEqual(records[1]["query"], "chat one alpha\nok then")
        self.assertEqual(ids(records, "main"), [["m1"], ["m1"]])


P = {"id": "p", "turns": [
    turn("continue", "chat one alpha", "c1", ["m1"]),
    turn("continue", "chat two alpha", "c2", ["m2"]),
    turn("continue", "chat about item2 again", "c2", ["m3"]),
    turn("continue", "chat about item3 four", "c3", ["m4"]),
    turn("continue", "chat five alpha", "c3", ["m5"]),
]}
P_VECTORS = {"chat one alpha": {"m1": 1}, "chat two alpha": {"m2": 1}, "chat about item2 again": {"m3": 1},
             "chat about item3 four": {"m4": 1}, "chat five alpha": {"m5": 1}, "reply p4": {"m6": 1}}


def p_engine():
    return engine_of([P], P_VECTORS)


class PoolTest(unittest.TestCase):
    def test_the_pool_brings_back_nothing_while_the_blocks_are_in_the_history(self):
        records = replay(p_engine(), "pool:size=3")
        self.assertEqual(ids(records, "extra"), [[]] * 5)
        records = replay(p_engine(), "pool:size=3;compact:every=100")
        self.assertEqual(ids(records, "extra"), [[]] * 5)

    def test_after_a_compaction_the_strongest_come_back_strongest_first(self):
        records = replay(p_engine(), "pool:size=2;compact:every=2", trace=True)
        # t3: the line mentions item2 (m2: 1 x .8 x .8 + 1 = 1.8) and the history is empty: m2 comes back, alone,
        # because m1 (.64) is under 1. t4 mentions item3, but the blocks of t3 are in front. t5 (compaction): m2
        # (1.792) and m3 (1.44) come back, strongest first
        self.assertEqual(ids(records, "main"), [["m1"], ["m2"], ["m3"], ["m4"], ["m5"]])
        self.assertEqual(ids(records, "extra"), [[], [], ["m2"], [], ["m2", "m3"]])
        self.assertEqual([e["from"] for e in records[4]["extra"]], ["pool", "pool"])
        self.assertEqual([r["shown"] for r in records], [1, 2, 2, 3, 3])

    def test_size_limits_the_pick(self):
        records = replay(p_engine(), "pool:size=1;compact:every=2")
        self.assertEqual(ids(records, "extra"), [[], [], ["m2"], [], ["m2"]])

    def test_the_strengths_follow_pool_js(self):
        records = replay(p_engine(), "pool:size=2;compact:every=2", trace=True)
        after_t3 = records[2]["pool"]
        self.assertAlmostEqual(after_t3["m1"], 0.64, places=4)
        self.assertAlmostEqual(after_t3["m2"], 2.8, places=4)  # entered at t2, mentioned at t3, given again at t3
        self.assertAlmostEqual(after_t3["m3"], 1.0, places=4)
        self.assertEqual(list(after_t3), ["m1", "m2", "m3"])
        final = records[4]["pool"]
        self.assertAlmostEqual(final["m1"], 0.4096, places=4)
        self.assertAlmostEqual(final["m2"], 2.792, places=4)
        self.assertAlmostEqual(final["m3"], 2.44, places=4)  # 1 x .8 + 1 (mention at t4) = 1.8, x .8 + 1 (given again)
        self.assertAlmostEqual(final["m4"], 0.8, places=4)
        self.assertAlmostEqual(final["m5"], 1.0, places=4)

    def test_the_second_block_takes_after_first_then_the_pool_and_holds_nobody_twice(self):
        replies = {"w/p/4": "reply p4"}  # finds m6 after turn 4
        records = replay(p_engine(), "after:max=1;pool:size=2;compact:every=2", replies)
        self.assertEqual(ids(records, "extra")[4], ["m6", "m2", "m3"])
        self.assertEqual([e["from"] for e in records[4]["extra"]], ["after", "pool", "pool"])

    def test_a_memory_in_the_main_block_is_not_brought_back(self):
        vectors = dict(P_VECTORS, **{"chat five alpha": {"m2": 1, "m5": 0.8}})
        records = replay(engine_of([P], vectors), "pool:size=2;compact:every=2")
        self.assertEqual(ids(records, "main")[4], ["m2", "m5"])
        self.assertEqual(ids(records, "extra")[4], ["m3"])

    def test_a_new_conversation_starts_with_an_empty_pool(self):
        session = {"id": "q", "turns": [turn("continue", "chat one alpha", "c1", ["m1"]),
                                        turn("continue", "chat about item1 again", "c1", ["m2"]),
                                        turn("nextday", "chat three alpha", "c1", ["m1"], new=True)]}
        vectors = {"chat one alpha": {"m1": 1}, "chat about item1 again": {"m2": 1}, "chat three alpha": {"m3": 1}}
        records = replay(engine_of([session], vectors), "pool:size=3", trace=True)
        self.assertEqual(ids(records, "extra"), [[], [], []])
        self.assertEqual(records[2]["pool"], {"m3": 1.0})


class RecentTest(unittest.TestCase):
    R = {"id": "r", "turns": [
        turn("open", "chat one alpha", "c1", ["m1"]),
        turn("continue", "chat about item1 again", "c1", ["m2"]),
        turn("idle", "ok", None, []),
        turn("nextday", "chat four alpha", "c1", ["m1"], new=True),
        turn("continue", "chat five alpha", "c2", ["m3"]),
    ]}
    VECTORS = {"chat one alpha": {"m1": 1}, "chat about item1 again": {"m1": 1, "m2": 0.8}, "chat four alpha": {"m3": 1},
               "chat five alpha": {"m3": 1}}

    def test_the_strongest_of_the_last_conversation_come_in_at_the_new_conversation_only(self):
        records = replay(engine_of([self.R], self.VECTORS), "recent:size=2", trace=True)
        # at the end of conversation 1: m1 1.44 (1, then x .8 + 1 from the mention, then x .8), m2 .8: only m1 reaches 1
        self.assertEqual(ids(records, "main"), [["m1"], ["m2"], [], ["m3"], []])
        self.assertEqual(ids(records, "extra"), [[], [], [], ["m1"], []])
        self.assertEqual(records[3]["extra"][0]["from"], "recent")
        self.assertEqual((records[3]["in_front"], records[3]["fresh"]), (True, True))
        self.assertEqual(replay(engine_of([self.R], self.VECTORS), "product")[3]["in_front"], False)

    def test_what_the_new_turn_brings_itself_is_not_given_twice(self):
        vectors = dict(self.VECTORS, **{"chat four alpha": {"m1": 1, "m3": 0.8}})
        records = replay(engine_of([self.R], vectors), "recent:size=2")
        self.assertEqual(ids(records, "main")[3], ["m1", "m3"])
        self.assertEqual(ids(records, "extra")[3], [])

    def test_size_and_the_strength_floor(self):
        session = {"id": "r", "turns": [turn("open", "chat one alpha", None, []),
                                        turn("continue", "chat about item1 and item2", None, []),
                                        turn("nextday", "chat four alpha", None, [], new=True)]}
        vectors = {"chat one alpha": {"m1": 1, "m2": 0.8}, "chat about item1 and item2": {"m5": 1},
                   "chat four alpha": {"m3": 1}}
        engine = engine_of([session], vectors)
        # m1 and m2 enter together (1 each), a line mentioning both gives .8 + 1 = 1.8 each; m5 enters at 1
        self.assertEqual(ids(replay(engine, "recent:size=1"), "extra")[2], ["m1"])
        self.assertEqual(ids(replay(engine, "recent:size=5"), "extra")[2], ["m1", "m2", "m5"])  # strongest, then entry order
        self.assertEqual(ids(replay(engine, "product"), "extra")[2], [])


class RulesTest(unittest.TestCase):
    POOL = "pool:size=2;compact:every=2"

    def test_the_defaults_reproduce_the_ported_pool(self):
        for spec in (self.POOL + ";rules", self.POOL + ";rules:from=1,decay=0.8,used=none,score=off"):
            self.assertEqual(replay(p_engine(), spec, trace=True), replay(p_engine(), self.POOL, trace=True), spec)

    def test_from_is_the_pick_threshold(self):
        # t3: m2 is 1.8 and m1 .64; under from=0.5 both qualify (strongest first), under from=2 neither does
        self.assertEqual(ids(replay(p_engine(), self.POOL + ";rules:from=0.5"), "extra")[2], ["m2", "m1"])
        self.assertEqual(ids(replay(p_engine(), self.POOL + ";rules:from=2"), "extra")[2], [])
        self.assertEqual(ids(replay(p_engine(), self.POOL + ";rules:from=1.8"), "extra")[2], ["m2"])  # 1.8 is enough

    def test_decay_is_the_per_turn_factor(self):
        # without decay m1 is still 1 at turn 3 and m2 is 1 + 1 (mention): both come back, strongest first
        records = replay(p_engine(), "pool:size=3;compact:every=2;rules:decay=1", trace=True)
        self.assertEqual(ids(records, "extra")[2], ["m2", "m1"])
        self.assertAlmostEqual(records[1]["pool"]["m1"], 1.0)
        records = replay(p_engine(), "pool:size=3;compact:every=2;rules:decay=0.5", trace=True)
        self.assertAlmostEqual(records[1]["pool"]["m1"], 0.5)
        self.assertEqual(ids(records, "extra")[2], ["m2"])  # m2 is .5 + 1 at turn 3, m1 .25

    def test_used_reply_adds_one_to_every_item_the_reply_mentions_at_the_end_of_the_turn(self):
        replies = {"w/p/1": "we talked of item1 then"}
        records = replay(p_engine(), self.POOL + ";rules:used=reply", replies, trace=True)
        self.assertAlmostEqual(records[0]["pool"]["m1"], 2.0)  # entered at 1, the reply mentions item1
        # t3: m1 2 x .8 x .8 = 1.28 and m2 .8 + 1 = 1.8 come back, strongest first (m1 is under 1 without the reply)
        self.assertEqual(ids(records, "extra")[2], ["m2", "m1"])
        self.assertEqual(ids(replay(p_engine(), self.POOL + ";rules:used=none", replies), "extra")[2], ["m2"])
        # no reply text for the turn, or a reply that mentions nothing: nothing happens
        self.assertEqual(replay(p_engine(), self.POOL + ";rules:used=reply", {}), replay(p_engine(), self.POOL))
        other = replay(p_engine(), self.POOL + ";rules:used=reply", {"w/p/1": "nothing at all here"})
        self.assertEqual(other, replay(p_engine(), self.POOL))

    def test_used_reply_counts_the_item_that_entered_this_turn(self):
        records = replay(p_engine(), "pool:size=2;rules:used=reply", {"w/p/2": "item2 and item3"}, trace=True)
        self.assertAlmostEqual(records[1]["pool"]["m2"], 2.0)  # entered at turn 2 and mentioned by that turn's reply

    def test_score_on_enters_with_the_lookup_score_and_adds_the_new_score_when_given_again(self):
        session = {"id": "t", "turns": [turn("open", "chat one alpha", "c1", ["m1"]),
                                        turn("continue", "chat two alpha", "c1", ["m2"])]}
        vectors = {"chat one alpha": {"m1": 1, "m2": 0.8}, "chat two alpha": {"m2": 1}}
        records = replay(engine_of([session], vectors), "compact:every=1;rules:score=on", trace=True)
        self.assertAlmostEqual(records[0]["pool"]["m1"], 0.780869, places=4)
        self.assertAlmostEqual(records[0]["pool"]["m2"], 0.624695, places=4)
        # t2: decay, then m2 given again with its new score 1.0
        self.assertAlmostEqual(records[1]["pool"]["m2"], 0.8 * 0.624695 + 1.0, places=4)
        self.assertAlmostEqual(records[1]["pool"]["m1"], 0.8 * 0.780869, places=4)
        records = replay(engine_of([session], vectors), "compact:every=1", trace=True)
        self.assertAlmostEqual(records[0]["pool"]["m1"], 1.0)  # off: strength 1

    def test_score_on_gives_extra_block_memories_half_a_point(self):
        records = replay(p_engine(), self.POOL + ";rules:score=on", trace=True)
        # t3: m2 comes back as an extra (known: + .5); it was 1.0 at turn 2, .8 + 1 at turn 3
        self.assertAlmostEqual(records[2]["pool"]["m2"], 0.8 + 1 + 0.5, places=4)
        self.assertAlmostEqual(records[2]["pool"]["m3"], 1.0, places=4)  # the main memory's lookup score
        recent = replay(engine_of([RecentTest.R], RecentTest.VECTORS), "recent:size=2;rules:score=on", trace=True)
        self.assertAlmostEqual(recent[3]["pool"]["m1"], 0.5, places=4)  # new in the pool, an extra: 0.5
        self.assertEqual(ids(recent, "extra")[3], ["m1"])

    def test_the_rules_apply_to_recent_too(self):
        engine = engine_of([RecentTest.R], RecentTest.VECTORS)
        self.assertEqual(ids(replay(engine, "recent:size=2;rules:from=0.5"), "extra")[3], ["m1", "m2"])
        self.assertEqual(ids(replay(engine, "recent:size=2;rules:from=2"), "extra")[3], [])


class PrimeTest(unittest.TestCase):
    VECTORS = {"chat one alpha": {"m1": 1, "m7": 0.8}, "chat two alpha": {"m5": 1, "m1": 0.2},
               "chat about item1 again": {"m5": 1, "m1": 0.2}}

    def records(self, spec, line="chat two alpha"):
        session = {"id": "z", "turns": [turn("open", "chat one alpha", None, []), turn("continue", line, None, ["m1"])]}
        return replay(engine_of([session], self.VECTORS), spec)

    def test_a_recent_memory_needs_less_similarity_and_an_unrelated_one_stays_out(self):
        # t2's query is far from m1 (score .196: under the .3 line) and has nothing to do with m7; both were in play
        # (strength .8). m1: .196 + .15 x .8 = .316 reaches the line; m7: 0 + .12 does not
        records = self.records("prime:bonus=0.15,size=3;compact:every=1")
        self.assertEqual(ids(records, "main"), [["m1", "m7"], ["m5"]])
        self.assertEqual(ids(records, "extra"), [[], ["m1"]])
        extra = records[1]["extra"][0]
        self.assertEqual(extra["from"], "prime")
        self.assertAlmostEqual(extra["score"], 0.196116 + 0.15 * 0.8, places=5)
        self.assertEqual(records[1]["sources"], {"after": 0, "pool": 0, "recent": 0, "prime": 1})
        self.assertEqual((records[1]["in_front"], records[1]["fresh"]), (True, True))
        self.assertEqual(records[0]["sources"], {"after": 0, "pool": 0, "recent": 0, "prime": 0})

    def test_a_smaller_bonus_is_not_enough_and_a_shown_memory_is_not_primed(self):
        self.assertEqual(ids(self.records("prime:bonus=0.05,size=3;compact:every=1"), "extra"), [[], []])  # .236
        self.assertEqual(ids(self.records("prime:bonus=0.15,size=3"), "extra"), [[], []])  # m1 is still in front

    def test_the_bonus_counts_a_strength_of_one_at_most(self):
        # the line mentions item1: m1 is .8 + 1 = 1.8 but the bonus takes min(1, strength)
        records = self.records("prime:bonus=0.15,size=3;compact:every=1", "chat about item1 again")
        self.assertAlmostEqual(records[1]["extra"][0]["score"], 0.196116 + 0.15, places=5)

    def test_best_value_first_and_at_most_size(self):
        session = {"id": "z", "turns": [turn("open", "chat one alpha", None, []), turn("continue", "chat two alpha", None, [])]}
        vectors = {"chat one alpha": {"m1": 1, "m2": 0.8, "m3": 0.7},
                   "chat two alpha": {"m5": 1, "m1": 0.2, "m2": 0.3, "m3": 0.25}}
        engine = engine_of([session], vectors)
        # strength .8 each, so the bonus is .24 for all three; the scores of m2 > m3 > m1 decide
        records = replay(engine, "prime:bonus=0.3,size=2;compact:every=1")
        self.assertEqual(ids(records, "extra")[1], ["m2", "m3"])
        records = replay(engine, "prime:bonus=0.3,size=3;compact:every=1")
        self.assertEqual(ids(records, "extra")[1], ["m2", "m3", "m1"])
        self.assertEqual([m["from"] for m in records[1]["extra"]], ["prime"] * 3)

    def test_without_a_query_only_the_bonus_counts(self):
        session = {"id": "z", "turns": [turn("open", "chat one alpha", None, []), turn("idle", "ok", None, [])]}
        engine = engine_of([session], {"chat one alpha": {"m1": 1}})
        self.assertEqual(ids(replay(engine, "prime:bonus=0.4,size=3;compact:every=1"), "extra")[1], ["m1"])  # .4 x .8
        self.assertEqual(ids(replay(engine, "prime:bonus=0.3,size=3;compact:every=1"), "extra")[1], [])  # .24

    def test_the_previous_conversation_stays_available_for_two_turns(self):
        turns = list(RecentTest.R["turns"])
        turns[4] = turn("continue", "chat five alpha", "c2", ["m3"])
        turns.append(turn("continue", "chat six alpha", "c2", ["m3"]))
        session = {"id": "r2", "turns": turns}
        vectors = {"chat one alpha": {"m1": 1}, "chat about item1 again": {"m1": 1, "m2": 0.8},
                   "chat four alpha": {"m3": 1}, "chat five alpha": {"m3": 1, "m1": 0.2},
                   "chat six alpha": {"m3": 1, "m2": 0.2}}
        records = replay(engine_of([session], vectors), "prime:bonus=0.15,size=3")
        # t4 (first turn of the new conversation): nothing in the old pool is near the line (value .15, under .3);
        # t5 (second turn): m1 comes from the old pool (bonus .15 + score .196); t6 (third turn): the old pool is no
        # longer consulted, so m2 (old, .8: .196 + .12 would have reached the line) stays out
        self.assertEqual(ids(records, "extra"), [[], [], [], [], ["m1"], []])
        self.assertEqual(records[4]["extra"][0]["from"], "prime")

    def test_the_old_pool_is_consulted_at_the_first_turn_of_the_new_conversation(self):
        vectors = dict(RecentTest.VECTORS, **{"chat four alpha": {"m3": 1, "m1": 0.2}})
        records = replay(engine_of([RecentTest.R], vectors), "prime:bonus=0.15,size=3")
        self.assertEqual(ids(records, "extra")[3], ["m1"])
        self.assertEqual(replay(engine_of([RecentTest.R], vectors), "product")[3]["in_front"], False)
        self.assertEqual(records[3]["in_front"], True)

    def test_prime_works_with_after_and_rules(self):
        records = replay(s_engine(), "after:max=1;prime:size=1;rules:used=none", S_REPLIES)
        self.assertEqual(ids(records, "extra")[2], ["m3"])  # the reply lookup's memory comes first
        self.assertEqual([r["sources"]["after"] for r in records], [0, 0, 1, 1, 0, 0])

    def test_prime_with_pool_or_recent_is_an_error(self):
        for spec in ("prime;pool", "pool:size=2;prime", "prime;recent:size=1"):
            with self.assertRaises(SystemExit):
                session_lab.parse_spec(spec)


class SourcesTest(unittest.TestCase):
    def test_every_record_counts_where_the_extra_memories_came_from(self):
        records = replay(p_engine(), "after:max=1;pool:size=2;compact:every=2", {"w/p/4": "reply p4"})
        self.assertEqual(records[4]["sources"], {"after": 1, "pool": 2, "recent": 0, "prime": 0})
        self.assertEqual(records[2]["sources"], {"after": 0, "pool": 1, "recent": 0, "prime": 0})
        recent = replay(engine_of([RecentTest.R], RecentTest.VECTORS), "recent:size=2")
        self.assertEqual(recent[3]["sources"]["recent"], 1)
        self.assertEqual(sum(sum(r["sources"].values()) for r in replay(p_engine(), "product")), 0)


class NoiseSessionTest(unittest.TestCase):
    """The noise filters inside the replay (their own arithmetic is tested in test_bench_chat_noise.py)."""

    @staticmethod
    def one(lines, vectors, spec, new_at=None):
        turns = [turn("continue", line, None, [], new=(i == new_at)) for i, line in enumerate(lines)]
        return replay(engine_of([{"id": "n", "turns": turns}], vectors), spec)

    def test_defaults_reproduce_the_product_exactly(self):
        product = replay(s_engine(), "product", S_REPLIES, trace=True)
        for spec in ("noise", "noise:", "noise:hub=0", "noise:each=-100", "noise:top=-100", "noise:lead=0.0001",
                     "noise:cap=100", "noise:hub=0,top=-100,each=-100,lead=0.0001,cap=100"):
            self.assertEqual(replay(s_engine(), spec, S_REPLIES, trace=True), product, spec)
        variant = replay(s_engine(), "after:max=2;pool:size=2;compact:every=2", S_REPLIES, trace=True)
        self.assertEqual(replay(s_engine(), "after:max=2;pool:size=2;compact:every=2;noise:hub=0,cap=100", S_REPLIES,
                                trace=True), variant)

    def test_the_spec_part(self):
        self.assertIsNone(session_lab.parse_spec("noise")["noise"])
        config = session_lab.parse_spec("noise:cap=2;compact:every=3")
        self.assertEqual(config["noise"], dict(session_lab.noise_lab.DEFAULTS, cap=2))
        self.assertEqual(session_lab.spec_label(config), "noise:cap=2;compact:every=3")
        self.assertEqual(session_lab.spec_label(session_lab.parse_spec("noise:hub=1,k=5;rules")),
                         "rules:from=1.0,decay=0.8,used=none,score=off;noise:hub=1.0,k=5")
        everything = "after:max=1;noise:hub=0.5,k=3,top=2.0,each=1.0,lead=0.9,cap=2;compact:every=3"
        self.assertEqual(session_lab.spec_label(session_lab.parse_spec(everything)),
                         "after:max=1;noise:hub=0.5,k=3,top=2.0,each=1.0,lead=0.9,cap=2;compact:every=3")
        self.assertEqual(session_lab.parse_spec(session_lab.spec_label(session_lab.parse_spec(everything))),
                         session_lab.parse_spec(everything))
        self.assertEqual(session_lab.spec_label(session_lab.reference_config(session_lab.parse_spec(
            "noise:cap=1;compact:every=3"))), "compact:every=3")

    def test_bad_noise_values(self):
        for text in ("noise:hub=-1", "noise:hub=x", "noise:k=0", "noise:k=1.5", "noise:top=nan", "noise:each=inf",
                     "noise:lead=0", "noise:lead=-0.5", "noise:cap=0", "noise:cap=x", "noise:nope=1", "noise:top",
                     "noise:cap=1;noise:cap=2"):
            with self.assertRaises(SystemExit, msg=text):
                session_lab.parse_spec(text)

    def test_top_silences_a_lookup_that_has_no_standout(self):
        # eight orthogonal memories, a query of m1 alone: z of m1 is (1 - .125) / .3307 = 2.646
        lines, vectors = ["chat one alpha"], {"chat one alpha": {"m1": 1}}
        self.assertEqual(ids(self.one(lines, vectors, "noise:top=2.5"), "main"), [["m1"]])
        self.assertEqual(ids(self.one(lines, vectors, "noise:top=2.7"), "main"), [[]])

    def test_each_keeps_only_the_memories_that_stand_out(self):
        # q = {m1: 1, m2: .8}: z of m1 is 1.97 and of m2 1.46
        lines, vectors = ["chat one alpha"], {"chat one alpha": {"m1": 1, "m2": 0.8}}
        self.assertEqual(ids(self.one(lines, vectors, "product"), "main"), [["m1", "m2"]])
        self.assertEqual(ids(self.one(lines, vectors, "noise:each=1.5"), "main"), [["m1"]])
        self.assertEqual(ids(self.one(lines, vectors, "noise:each=2.5"), "main"), [[]])

    def test_the_second_lookup_is_filtered_too(self):
        replies = {"w/s1/2": "reply two"}
        records = replay(s_engine(), "after:max=2;noise:top=100", replies)
        self.assertEqual(ids(records, "extra"), [[]] * 6)
        self.assertEqual([r["left"] for r in records], [[]] * 6)
        self.assertEqual(ids(replay(s_engine(), "after:max=2", replies), "extra")[2], ["m3", "m4"])
        # reply two = {m3: 1, m4: .8}: z 1.97 and 1.46, so each=1.5 leaves m3 alone
        self.assertEqual(ids(replay(s_engine(), "after:max=2;noise:each=1.5", replies), "extra")[2], ["m3"])

    def test_hub_acts_through_the_found_cache_and_changes_nothing_in_an_orthogonal_world(self):
        engine = s_engine()
        plain = engine.found("chat two alpha")
        self.assertEqual(engine.found("chat two alpha", session_lab.parse_spec("noise:hub=1")["noise"]), plain)
        self.assertEqual(len(engine._found), 2)  # one entry per set of lookup filters

    def test_lead_when_the_conversation_continues_what_the_model_has(self):
        lines = ["chat one alpha", "chat two alpha", "chat three alpha"]
        vectors = {"chat one alpha": {"m1": 1}, "chat two alpha": {"m1": 1, "m2": 0.8},
                   "chat three alpha": {"m3": 1, "m4": 0.8}}
        # t2: the best memory m1 is in front already; m2 scores .625 against .781, a ratio of .80
        self.assertEqual(ids(self.one(lines, vectors, "product"), "main"), [["m1"], ["m2"], ["m3", "m4"]])
        self.assertEqual(ids(self.one(lines, vectors, "noise:lead=0.7"), "main"), [["m1"], ["m2"], ["m3", "m4"]])
        self.assertEqual(ids(self.one(lines, vectors, "noise:lead=0.9"), "main"), [["m1"], [], ["m3", "m4"]])
        # a ratio above 1 keeps nothing new while the best memory is shown, and never touches a turn whose best is new
        self.assertEqual(ids(self.one(lines, vectors, "noise:lead=1.01"), "main"), [["m1"], [], ["m3", "m4"]])

    def test_lead_after_a_compaction_sees_the_emptied_history(self):
        lines = ["chat one alpha", "chat two alpha"]
        vectors = {"chat one alpha": {"m1": 1}, "chat two alpha": {"m1": 1, "m2": 0.8}}
        records = self.one(lines, vectors, "noise:lead=1.01;compact:every=1")
        self.assertEqual(ids(records, "main"), [["m1"], ["m1", "m2"]])  # m1 is no longer shown: the best is new

    def test_cap_spares_the_first_turn_of_a_conversation_and_counts_new_memories_only(self):
        lines = ["chat one alpha", "chat two alpha", "chat three alpha"]
        vectors = {"chat one alpha": {"m1": 1, "m2": 0.8, "m3": 0.7}, "chat two alpha": {"m4": 1, "m5": 0.8, "m6": 0.7},
                   "chat three alpha": {"m1": 1, "m2": 0.8, "m3": 0.7}}
        product = [["m1", "m2", "m3"], ["m4", "m5", "m6"], ["m1", "m2", "m3"]]
        self.assertEqual(ids(self.one(lines, vectors, "product", new_at=2), "main"), product)
        self.assertEqual(ids(self.one(lines, vectors, "noise:cap=1", new_at=2), "main"),
                         [["m1", "m2", "m3"], ["m4"], ["m1", "m2", "m3"]])  # turns 1 and 3 open a conversation
        self.assertEqual(ids(self.one(lines, vectors, "noise:cap=2", new_at=2), "main")[1], ["m4", "m5"])
        # without a new conversation the third turn is capped as well; m1 to m3 are not yet shown at turn 3? they are
        self.assertEqual(ids(self.one(lines, vectors, "noise:cap=1"), "main"), [["m1", "m2", "m3"], ["m4"], []])

    def test_the_cap_follows_skip_repeats(self):
        lines = ["chat one alpha", "chat two alpha"]
        vectors = {"chat one alpha": {"m1": 1}, "chat two alpha": {"m1": 1, "m2": 0.8, "m3": 0.7}}
        # t2 finds m1 (shown), m2, m3: the first new one is m2, not m1
        self.assertEqual(ids(self.one(lines, vectors, "noise:cap=1"), "main"), [["m1"], ["m2"]])

    def test_the_order_hub_lookup_top_each_then_skip_lead_cap(self):
        lines = ["chat one alpha", "chat two alpha"]
        vectors = {"chat one alpha": {"m1": 1}, "chat two alpha": {"m1": 1, "m2": 0.8, "m3": 0.7}}
        # z at t2: m1 1.67, m2 1.19, m3 .95. each=1 drops m3 inside the lookup, then m1 is skipped (shown), then lead .75
        # (m2 scores .548 against .685: .80, kept) and cap 1: m2
        records = self.one(lines, vectors, "noise:each=1.0,lead=0.75,cap=1")
        self.assertEqual(ids(records, "main"), [["m1"], ["m2"]])
        self.assertEqual(ids(self.one(lines, vectors, "noise:each=1.0,lead=0.85,cap=1"), "main"), [["m1"], []])
        self.assertEqual(ids(self.one(lines, vectors, "noise:each=1.5"), "main"), [["m1"], []])  # m2 under 1.5

    def test_the_report_has_the_unrelated_arrivals(self):
        base = session_lab.run_variant([s_engine()], session_lab.parse_spec("product"), S_REPLIES)
        variant = session_lab.run_variant([s_engine()], session_lab.parse_spec("after:max=2"), S_REPLIES)
        self.assertEqual(base["summary"]["all"]["unrelated_per_turn"], 0.0)
        self.assertAlmostEqual(variant["summary"]["all"]["unrelated_per_turn"], 4 / 6)
        text = "\n".join(session_lab.report_lines(variant, [("product", base)]))
        self.assertIn("unrelated arrived (product) | unrelated arrived (variant) |", text)
        self.assertIn("| 100.0 | 0.00 | 0.67 |", text)

    def test_noise_removes_arrivals_and_the_report_counts_the_turns_it_lost(self):
        engine = s_engine()
        base = session_lab.run_variant([engine], session_lab.parse_spec("product"), {})
        quiet = session_lab.run_variant([engine], session_lab.parse_spec("noise:top=100"), {})
        self.assertEqual(quiet["summary"]["all"]["arrived_per_turn"], 0.0)
        self.assertEqual(quiet["summary"]["all"]["nothing"], 1.0)
        found = session_lab.flips(base["records"], quiet["records"])
        self.assertEqual((found["gained"], found["lost"]), (0, 5))  # all five turns with a direct memory lose it
        self.assertEqual(found["kinds"]["continue"], [0, 3])
        text = "\n".join(session_lab.report_lines(quiet, [("product", base)]))
        self.assertIn("| continue | 0 | 3 |", text)
        self.assertIn("| all | 0 | 5 |", text)

    def test_the_noise_grid(self):
        specs = session_lab.grid_noise()
        self.assertEqual(len(specs), 12 + 5 + 4 + 5 + 3)
        self.assertEqual(specs[:3], ["noise:hub=0.25,k=5", "noise:hub=0.25,k=10", "noise:hub=0.25,k=20"])
        self.assertEqual(specs[12:17], ["noise:top=%s" % t for t in (1.5, 2.0, 2.5, 3.0, 3.5)])
        self.assertEqual(specs[17:21], ["noise:each=%s" % z for z in (0.5, 1.0, 1.5, 2.0)])
        self.assertEqual(specs[21:26], ["noise:lead=%s" % r for r in (0.7, 0.8, 0.9, 0.95, 1.01)])
        self.assertEqual(specs[26:], ["noise:cap=1", "noise:cap=2", "noise:cap=3"])
        for spec in specs:
            self.assertEqual(len({n for n, v in session_lab.parse_spec(spec)["noise"].items() if v is not None} - {"k"}), 1)


class PoolPortTest(unittest.TestCase):
    """The pool's rules and constants, equal to plugins/dsh-feiyu-memgate/src/pool.js."""

    def test_the_constants(self):
        self.assertEqual((session_lab.DECAY, session_lab.FORGET_BELOW, session_lab.PICK_FROM, session_lab.MAX_ITEMS,
                          session_lab.MENTION_MIN), (0.8, 0.2, 1, 24, 3))

    def test_decay_drops_an_item_when_it_falls_under_the_floor(self):
        state = session_lab.new_pool()
        session_lab.enter(state, ["a"])
        for _ in range(7):
            session_lab.advance(state)
        self.assertAlmostEqual(state["items"]["a"]["strength"], 0.8 ** 7)  # .2097: kept
        session_lab.advance(state)  # .1678: forgotten
        self.assertNotIn("a", state["items"])
        self.assertEqual(state["turn"], 8)

    def test_a_mention_adds_one_after_the_decay_and_cannot_revive_a_dropped_item(self):
        state = session_lab.new_pool()
        session_lab.enter(state, ["a", "b"])
        texts = {"a": "item7", "b": "other"}
        session_lab.advance(state, "talking about item7 now", texts)
        self.assertAlmostEqual(state["items"]["a"]["strength"], 1.8)
        self.assertAlmostEqual(state["items"]["b"]["strength"], 0.8)
        state = session_lab.new_pool()
        session_lab.enter(state, ["a"])
        for _ in range(7):
            session_lab.advance(state)
        session_lab.advance(state, "item7", texts)
        self.assertEqual(state["items"], {})

    def test_giving_again_adds_one_and_takes_the_turn(self):
        state = session_lab.new_pool()
        session_lab.advance(state)
        session_lab.enter(state, ["a"])
        session_lab.advance(state)
        session_lab.enter(state, ["a", "b"])
        self.assertAlmostEqual(state["items"]["a"]["strength"], 1.8)
        self.assertEqual((state["items"]["a"]["turn"], state["items"]["b"]["turn"]), (2, 2))
        self.assertEqual(list(state["items"]), ["a", "b"])

    def test_at_most_24_items_and_the_weakest_then_the_oldest_then_the_first_entered_go(self):
        state = session_lab.new_pool()
        session_lab.advance(state)
        session_lab.enter(state, ["i%02d" % i for i in range(24)])
        session_lab.advance(state)
        session_lab.enter(state, ["new"])
        self.assertEqual(len(state["items"]), 24)
        self.assertNotIn("i00", state["items"])  # equal strength and turn: the first entered goes
        self.assertIn("new", state["items"])
        state = session_lab.new_pool()
        session_lab.advance(state)
        session_lab.enter(state, ["i00"])
        session_lab.advance(state)
        session_lab.enter(state, ["i%02d" % i for i in range(1, 24)])
        session_lab.enter(state, ["i00"])  # i00 now has strength 2
        session_lab.enter(state, ["new"])
        self.assertIn("i00", state["items"])
        self.assertNotIn("i01", state["items"])
        self.assertEqual(len(state["items"]), 24)
        # of equal strength the one given longest ago goes, whatever its place in the order of entry
        state = {"turn": 6, "items": {"a": {"strength": 1.0, "turn": 5}, "b": {"strength": 1.0, "turn": 2}}}
        state["items"].update({"x%02d" % i: {"strength": 5.0, "turn": 6} for i in range(22)})
        session_lab.enter(state, ["new"])
        self.assertNotIn("b", state["items"])
        self.assertIn("a", state["items"])
        self.assertEqual(len(state["items"]), 24)

    def test_pick_by_strength_then_recency_then_order_with_a_floor_and_a_skip(self):
        state = {"turn": 5, "items": {
            "a": {"strength": 1.0, "turn": 1}, "b": {"strength": 2.0, "turn": 1}, "c": {"strength": 1.0, "turn": 3},
            "d": {"strength": 0.99, "turn": 5}, "e": {"strength": 1.0, "turn": 3}, "f": {"strength": 2.0, "turn": 0}}}
        self.assertEqual(session_lab.pick(state, 10), ["b", "f", "c", "e", "a"])  # d is under 1
        self.assertEqual(session_lab.pick(state, 3), ["b", "f", "c"])
        self.assertEqual(session_lab.pick(state, 10, skip=lambda mid: mid in ("b", "c")), ["f", "e", "a"])
        self.assertEqual(session_lab.pick(state, 0), [])

    def test_mentions_in_chinese_need_three_shared_characters(self):
        mentions = session_lab.mentions
        self.assertTrue(mentions("最近想吃冰淇淋了", "主人喜欢吃冰淇淋"))
        self.assertTrue(mentions("我也喜欢吃", "主人喜欢吃冰淇淋"))  # 喜欢吃
        self.assertFalse(mentions("冰淇好吃", "主人喜欢吃冰淇淋"))  # two characters are not a mention
        self.assertFalse(mentions("冰淇，淋", "主人喜欢吃冰淇淋"))  # punctuation ends a run
        self.assertFalse(mentions("今天天气不错", "主人喜欢吃冰淇淋"))
        self.assertFalse(mentions("", "主人喜欢吃冰淇淋"))
        self.assertFalse(mentions("主人喜欢吃冰淇淋", ""))

    def test_mentions_in_ascii_need_a_whole_word_that_is_not_common(self):
        mentions = session_lab.mentions
        self.assertTrue(mentions("my cat likes tuna", "tuna sandwich on friday"))
        self.assertTrue(mentions("TUNA again", "tuna sandwich"))  # case does not matter
        self.assertFalse(mentions("the cat", "the dog"))  # a stop word
        self.assertFalse(mentions("a category", "my cat"))  # inside another word
        self.assertFalse(mentions("go to it", "go home"))  # under three characters
        self.assertTrue(mentions("see item12 now", "item12"))
        self.assertFalse(mentions("see item12 now", "item1"))
        self.assertTrue(mentions("喜欢tuna", "tuna"))  # a word beside Chinese characters

    def test_a_replayed_mention_uses_the_memory_text(self):
        records = replay(p_engine(), "pool:size=2;compact:every=2", trace=True)
        self.assertGreater(records[2]["pool"]["m2"], records[1]["pool"]["m2"])  # "item2" in the line of turn 3


def fake(world, session, turn_number, kind, in_front, **extra):
    out = {"world": world, "session": session, "turn": turn_number, "kind": kind, "in_front": in_front,
           "fresh": in_front, "main": [], "extra": [], "shown": 0, "last_of_conversation": False,
           "conversation": 1}
    out.update(extra)
    return out


class SummaryTest(unittest.TestCase):
    def test_stats_of_the_product_by_hand(self):
        summary = session_lab.run_variant([s_engine()], session_lab.parse_spec("product"), {})["summary"]
        total = summary["all"]
        self.assertEqual((total["n"], total["n_direct"]), (6, 5))
        self.assertEqual((total["in_front"], total["fresh"]), (1.0, 0.8))
        self.assertAlmostEqual(total["main_per_turn"], 4 / 6)
        self.assertEqual((total["main_related"], total["extra_per_turn"], total["extra_related"]), (1.0, 0.0, None))
        self.assertAlmostEqual(total["nothing"], 2 / 6)
        cont = summary["kinds"]["continue"]
        self.assertEqual((cont["n"], cont["in_front"]), (3, 1.0))
        self.assertAlmostEqual(cont["fresh"], 2 / 3)
        idle = summary["kinds"]["idle"]
        self.assertEqual((idle["n"], idle["n_direct"], idle["in_front"], idle["nothing"]), (1, 0, None, 1.0))
        self.assertEqual(summary["conversations"], {"n": 2, "shown_end": 2.0})
        self.assertEqual(summary["worlds"], {"w": 1.0})
        self.assertEqual(set(summary["kinds"]), {"open", "continue", "idle", "nextday"})

    def test_stats_of_a_variant_by_hand(self):
        summary = session_lab.run_variant([s_engine()], session_lab.parse_spec("after:max=2"), S_REPLIES)["summary"]
        total = summary["all"]
        # extras: m3 and m4 (t3), m7 (t4), m5 (t6): none belongs to the turn or to the turn before it
        self.assertAlmostEqual(total["extra_per_turn"], 4 / 6)
        self.assertEqual((total["extra_related"], total["extra_prev"], total["extra_neither"]), (0.0, 0.0, 1.0))
        self.assertAlmostEqual(total["arrived_per_turn"], 8 / 6)
        self.assertEqual(total["nothing"], 0.0)
        self.assertEqual(summary["kinds"]["idle"]["nothing"], 0.0)
        self.assertEqual(summary["kinds"]["idle"]["arrived_per_turn"], 1.0)
        self.assertEqual(summary["conversations"]["shown_end"], 4.0)  # 5 memories in front after t4, 3 after t6

    def test_an_extra_memory_of_the_previous_turns_cluster_is_told_from_one_of_neither(self):
        session = {"id": "s", "turns": [turn("open", "chat one alpha", "c1", ["m1"]), turn("idle", "ok", None, [])]}
        vectors = {"chat one alpha": {"m1": 1}, "reply one": {"m2": 1}}
        records = replay(engine_of([session], vectors), "after:max=2", {"w/s/1": "reply one"})
        extra = records[1]["extra"][0]
        self.assertEqual((extra["id"], extra["related"], extra["prev"], extra["neither"]), ("m2", False, True, False))

    def test_flips_by_hand(self):
        base = [fake("w", "s", 1, "open", False), fake("w", "s", 2, "continue", False), fake("w", "s", 3, "continue", True),
                fake("w", "s", 4, "idle", None), fake("w", "s", 5, "return", True), fake("v", "s", 1, "open", True)]
        variant = [fake("w", "s", 1, "open", False), fake("w", "s", 2, "continue", True), fake("w", "s", 3, "continue", False),
                   fake("w", "s", 4, "idle", None), fake("w", "s", 5, "return", False), fake("v", "s", 1, "open", True)]
        found = session_lab.flips(base, variant)
        self.assertEqual((found["gained"], found["lost"]), (1, 2))
        self.assertEqual(found["kinds"], {"open": [0, 0], "continue": [1, 1], "return": [0, 1]})
        self.assertAlmostEqual(found["p"], 1.0)  # 2 x (1 + 2) / 8 is over 1
        many = session_lab.flips([fake("w", "s", i, "continue", False) for i in range(8)],
                                 [fake("w", "s", i, "continue", True) for i in range(8)])
        self.assertAlmostEqual(many["p"], 2 / 256)
        same = session_lab.flips(base, base)
        self.assertEqual((same["gained"], same["lost"], same["p"]), (0, 0, 1.0))

    def test_flips_are_matched_by_world_session_and_turn(self):
        base = [fake("a", "s", 1, "open", False), fake("b", "s", 1, "open", True)]
        variant = [fake("a", "s", 1, "open", True), fake("b", "s", 1, "open", False)]
        found = session_lab.flips(base, variant)
        self.assertEqual((found["gained"], found["lost"]), (1, 1))

    def test_a_flip_end_to_end_lost_by_compaction_and_gained_by_the_reply_lookup(self):
        lose = {"id": "s2", "turns": [turn("open", "chat one beta", "c1", ["m1"]), turn("continue", "chat two beta", "c1", ["m1"])]}
        engine = engine_of([lose], {"chat one beta": {"m1": 1}, "chat two beta": {"m7": 1}})
        base = session_lab.run_variant([engine], session_lab.parse_spec("product"), {})
        compact = session_lab.run_variant([engine], session_lab.parse_spec("compact:every=1"), {})
        found = session_lab.flips(base["records"], compact["records"])
        self.assertEqual((found["gained"], found["lost"]), (0, 1))  # m1 left the history before turn 2 and was not found
        self.assertEqual(found["kinds"], {"open": [0, 0], "continue": [0, 1]})
        gain = {"id": "s3", "turns": [turn("open", "chat one gamma", "c1", ["m1"]), turn("continue", "chat two gamma", "c1", ["m1"])]}
        engine = engine_of([gain], {"chat one gamma": {"m7": 1}, "chat two gamma": {"m7": 1}, "reply g": {"m1": 1}})
        replies = {"w/s3/1": "reply g"}
        base = session_lab.run_variant([engine], session_lab.parse_spec("product"), replies)
        after = session_lab.run_variant([engine], session_lab.parse_spec("after:max=1"), replies)
        found = session_lab.flips(base["records"], after["records"])
        self.assertEqual((found["gained"], found["lost"], found["p"]), (1, 0, 1.0))
        self.assertEqual(found["kinds"], {"open": [0, 0], "continue": [1, 0]})

    def test_the_report_tables_of_a_small_case(self):
        replies = S_REPLIES
        engine = s_engine()
        base = session_lab.run_variant([engine], session_lab.parse_spec("product"), replies)
        variant = session_lab.run_variant([engine], session_lab.parse_spec("after:max=2"), replies)
        text = "\n".join(session_lab.report_lines(variant, [("product", base)]))
        for wanted in ("variant: after:max=2", "6 turns of 1 sessions in 1 worlds",
                       "| kind | turns | in front % (product) | in front % (variant) | fresh % (product) | fresh % (variant) |",
                       "| open | 1 | 100.0 | 100.0 | 100.0 | 100.0 |", "| continue | 3 | 100.0 | 100.0 | 66.7 | 66.7 |",
                       "| idle | 1 | - | - | - | - |", "| all | 6 | 100.0 | 100.0 | 80.0 | 80.0 |",
                       "| flips of in front | gained vs product | lost vs product |", "| all | 0 | 0 |",
                       "sign test on the overall flips against product (exact, two-sided): p = 1.0000",
                       "| arrived per turn | turns | main (product) | main (variant) | main related % (product) |",
                       "| all | 6 | 0.67 | 0.67 | 100.0 | 100.0 | 0.67 | 0.0 | 0.0 | 100.0 |",
                       "| nothing arrived % | 100.0 | 0.0 |", "| arrived per turn | 0.00 | 1.00 |",
                       "| memories in front at its end (mean) | 2.00 | 4.00 |", "| w | 100.0 | 100.0 |"):
            self.assertIn(wanted, text)

    def test_a_variant_that_compacts_is_also_shown_against_the_product_under_the_same_compaction(self):
        config = session_lab.parse_spec("pool:size=2;compact:every=2")
        self.assertEqual(session_lab.spec_label(session_lab.reference_config(config)), "compact:every=2")
        self.assertIsNone(session_lab.reference_config(session_lab.parse_spec("compact:every=2")))
        self.assertIsNone(session_lab.reference_config(session_lab.parse_spec("after:max=1")))
        engine = p_engine()
        base = session_lab.run_variant([engine], session_lab.parse_spec("product"), {})
        reference = session_lab.run_variant([engine], session_lab.reference_config(config), {})
        variant = session_lab.run_variant([engine], config, {})
        text = "\n".join(session_lab.report_lines(variant, [("product", base), ("compact:every=2 only", reference)]))
        self.assertIn("in front % (compact:every=2 only)", text)
        self.assertIn("gained vs compact:every=2 only", text)
        self.assertIn("against compact:every=2 only", text)


class SpecTest(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        config = session_lab.parse_spec("after;pool:size=5;recent;compact:every=3")
        self.assertEqual(config, {"after": {"max": 2}, "pool": {"size": 5}, "recent": {"size": 3}, "prime": None,
                                  "rules": None, "noise": None, "compact": {"every": 3}})
        self.assertEqual(session_lab.spec_label(config), "after:max=2;pool:size=5;recent:size=3;compact:every=3")
        config = session_lab.parse_spec("rules;prime;compact:every=2")
        self.assertEqual(config["rules"], {"from": 1.0, "decay": 0.8, "used": "none", "score": "off"})
        self.assertEqual(config["prime"], {"bonus": 0.15, "size": 3})
        self.assertEqual(session_lab.spec_label(config),
                         "prime:bonus=0.15,size=3;rules:from=1.0,decay=0.8,used=none,score=off;compact:every=2")
        self.assertEqual(session_lab.parse_spec(session_lab.spec_label(config)), config)
        self.assertEqual(session_lab.parse_spec(session_lab.spec_label(config)), config)
        self.assertEqual(session_lab.spec_label(session_lab.parse_spec("compact:every=2;after:max=1")),
                         "after:max=1;compact:every=2")  # families in their fixed order
        for text in ("", "product", "baseline", "  "):
            self.assertEqual(session_lab.spec_label(session_lab.parse_spec(text)), "product")
            self.assertEqual(set(session_lab.parse_spec(text).values()), {None})

    def test_bad_specs(self):
        for text in ("after:max=0", "after:max=x", "after:max=1.5", "pool:size=0", "recent:size=-1", "compact", "compact:every=0",
                     "compact:every=", "after:nope=1", "after:max", "shrink:x=1", "after:max=1;after:max=2",
                     "pool:every=2", "compact:size=2", "rules:from=0", "rules:from=-1", "rules:from=nan", "rules:decay=0",
                     "rules:decay=1.5", "rules:decay=x", "rules:used=sometimes", "rules:score=maybe",
                     "prime:bonus=-0.1", "prime:bonus=x", "prime:size=0", "prime:size=1.5", "prime;pool",
                     "recent;prime", "prime:bonus=nan"):
            with self.assertRaises(SystemExit, msg=text):
                session_lab.parse_spec(text)

    def test_the_sweep_grids_parse_into_their_family(self):
        key = {"after": "after", "pool": "pool", "recent": "recent", "rules-pool": "pool", "rules-recent": "recent",
               "prime": "prime", "noise": "noise"}
        self.assertEqual(set(session_lab.GRIDS), set(key))
        for family, grid in session_lab.GRIDS.items():
            specs = grid()
            self.assertEqual(len(specs), len(set(specs)))
            for spec in specs:
                config = session_lab.parse_spec(spec)
                self.assertTrue(config[key[family]], spec)
                self.assertEqual(session_lab.spec_label(config), spec)
        self.assertEqual([len(session_lab.GRIDS[f]()) for f in ("rules-pool", "rules-recent", "prime")], [12, 12, 40])
        pools = [session_lab.parse_spec(s) for s in session_lab.grid_rules_pool()]
        self.assertTrue(all(c["compact"] == {"every": 3} and c["pool"] == {"size": 3} for c in pools))
        self.assertEqual({(c["rules"]["from"], c["rules"]["used"], c["rules"]["score"]) for c in pools},
                         {(f, u, o) for f in (0.3, 0.5, 1.0) for u in ("none", "reply") for o in ("off", "on")})
        recents = [session_lab.parse_spec(s) for s in session_lab.grid_rules_recent()]
        self.assertTrue(all(c["recent"] == {"size": 3} and not c["compact"] for c in recents))
        primes = [session_lab.parse_spec(s) for s in session_lab.grid_prime()]
        self.assertEqual({(c["prime"]["bonus"], c["prime"]["size"]) for c in primes},
                         {(b, k) for b in (0.05, 0.1, 0.15, 0.2, 0.3) for k in (2, 3)})
        self.assertEqual({(c["rules"]["used"], bool(c["compact"])) for c in primes},
                         {(u, c) for u in ("reply", "none") for c in (True, False)})
        self.assertEqual(session_lab.grid_after(), ["after:max=1", "after:max=2", "after:max=3"])
        self.assertEqual(len(session_lab.grid_pool()), 12)
        self.assertEqual(session_lab.grid_recent(),
                         ["recent:size=1", "recent:size=2", "recent:size=3", "recent:size=5"])

    def test_check_sources(self):
        session_lab.check_sources([session_lab.parse_spec("after:max=1"), session_lab.parse_spec("product")],
                                  {"reply": {"w/s/1": "x"}})
        session_lab.check_sources([session_lab.parse_spec("pool:size=1;compact:every=2")], {})
        session_lab.check_sources([session_lab.parse_spec("rules:used=reply")], {"reply": {}})
        session_lab.check_sources([session_lab.parse_spec("rules:used=none;prime")], {})
        with self.assertRaises(SystemExit):
            session_lab.check_sources([session_lab.parse_spec("prime;rules:used=reply")], {})
        with self.assertRaises(SystemExit):
            session_lab.check_sources([session_lab.parse_spec("after:max=1")], {"other": {}})


class NeededStringsTest(unittest.TestCase):
    def test_memory_texts_queries_and_only_the_replies_a_config_uses(self):
        world = world_of([S])
        scale = lab.Scale(*SCALE)
        scale.query_prefix, scale.document_prefix = "Q:", "D:"
        texts = {"reply": {"w/s1/2": "reply two", "w/other/1": "zz"}}
        plain = session_lab.needed_strings([world], scale, texts, [session_lab.parse_spec("product")])
        documents = {"D:item%d" % i for i in range(1, 9)}
        queries = {"Q:chat one alpha", "Q:chat two alpha", "Q:chat three alpha", "Q:chat five alpha", "Q:chat six alpha"}
        self.assertEqual(set(plain), documents | queries)  # the idle line "ok" is no query
        both = session_lab.needed_strings([world], scale, texts, [session_lab.parse_spec("after:max=1")])
        self.assertEqual(set(both), documents | queries | {"Q:reply two"})
        self.assertEqual(len(both), len(set(both)))

    def test_a_short_line_asks_with_the_previous_line_of_its_conversation(self):
        session = {"id": "s", "turns": [turn("open", "chat one alpha", "c1", ["m1"]), turn("idle", "ok then", None, []),
                                        turn("nextday", "ok then", "c1", ["m1"], new=True)]}
        found = session_lab.needed_strings([world_of([session])], lab.Scale(*SCALE), {}, [])
        self.assertIn("chat one alpha\nok then", found)
        self.assertIn("ok then", found)


class ExplainTest(unittest.TestCase):
    def test_the_trace_shows_the_blocks_the_labels_and_the_pool(self):
        engine = p_engine()
        text = "\n".join(session_lab.explain(engine, engine.sessions[0], session_lab.parse_spec("pool:size=2;compact:every=2"), {}))
        self.assertIn("session w/p under pool:size=2;compact:every=2", text)
        self.assertIn("turn 3 (continue, conversation 1)", text)
        self.assertIn("labels: cluster=c2 direct=['m3'] also_ok=[]", text)
        self.assertIn("query: chat about item2 again", text)
        self.assertIn("main: m3 score 1.000 [direct]", text)
        self.assertIn("extra block: m2 from pool [", text)
        self.assertIn("pool: m1 0.64, m2 2.80, m3 1.00", text)
        self.assertNotIn("item2\n", text)
        self.assertEqual(text.count("      m"), 0)
        verbose = "\n".join(session_lab.explain(engine, engine.sessions[0], session_lab.parse_spec("product"), {}, verbose=True))
        self.assertIn("      m1 item1", verbose)

    def test_after_lines_and_no_lookup(self):
        engine = s_engine()
        text = "\n".join(session_lab.explain(engine, engine.sessions[0], session_lab.parse_spec("after:max=2"), S_REPLIES))
        self.assertIn("left for the next turn: ['m3', 'm4']", text)
        self.assertIn("query: none (no lookup)", text)
        self.assertIn("extra block: m3 from after score 0.", text)
        self.assertIn("turn 5 (nextday, conversation 2, new)", text)

    def test_find_session(self):
        engine = s_engine()
        self.assertIs(session_lab.find_session([engine], "w/s1")[0], engine)
        self.assertIs(session_lab.find_session([engine], "s1")[0], engine)
        for name in ("w/nope", "x/s1"):
            with self.assertRaises(SystemExit):
                session_lab.find_session([engine], name)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.split = os.path.join(self.root, "dev")
        os.makedirs(self.split)
        self.world = world_of([S])
        self.write(os.path.join(self.split, "w.json"), self.world)
        shutil.copy(_FIXTURE, os.path.join(self.split, "fixture-world.json"))  # a world without sessions
        self.cache = os.path.join(self.root, "cache.json")
        self.replies = self.write(os.path.join(self.root, "replies.json"), {"kind": "reply", "items": S_REPLIES})

    @staticmethod
    def write(path, content):
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(content, handle, ensure_ascii=False)
        return path

    def fill_cache(self, specs):
        texts = model_lab.load_texts([self.replies])
        scale = lab.Scale.for_model("bge-m3")
        strings = session_lab.needed_strings([self.world], scale, texts, [session_lab.parse_spec(s) for s in specs])
        vectors = {s: [math.cos(math.radians(a)), math.sin(math.radians(a))]
                   for s in strings for a in [(sum(map(ord, s)) * 37) % 360]}
        with open(self.cache, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"model": "bge-m3", "vectors": vectors}, handle)

    def call(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = session_lab.main(argv)
        return code, out.getvalue()

    def test_a_run_prints_every_part_of_the_report_and_writes_the_json(self):
        specs = ["after:max=2;pool:size=2;compact:every=2", "recent:size=2"]
        self.fill_cache(specs)
        out_path = os.path.join(self.root, "out", "result.json")
        argv = ["--split", self.split, "--cache", self.cache, "--texts", self.replies, "--baseline",
                "--explain", "w/s1", "--out", out_path]
        for spec in specs:
            argv += ["--run", spec]
        code, text = self.call(argv)
        self.assertEqual(code, 0)
        for wanted in ("sessions: 1 sessions with 6 turns in 1 worlds (1 worlds without sessions skipped)",
                       "texts reply: 4 of 6 turns have a text", "variant: product",
                       "variant: after:max=2;pool:size=2;compact:every=2", "variant: recent:size=2",
                       "in front % (compact:every=2 only)", "sign test on the overall flips", "| kind | turns |",
                       "arrived per turn", "per conversation", "| world |", "session w/s1 under after:max=2",
                       "turn 6 (continue, conversation 2)"):
            self.assertIn(wanted, text)
        with open(out_path, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["split"], "dev")
        self.assertEqual([r["spec"] for r in document["runs"]], ["after:max=2;pool:size=2;compact:every=2", "recent:size=2"])
        self.assertEqual(set(document["runs"][0]), {"spec", "summary", "flips", "records"})
        self.assertEqual(set(document["runs"][0]["flips"]), {"product", "compact:every=2 only"})
        self.assertEqual(set(document["runs"][1]["flips"]), {"product"})
        self.assertEqual(len(document["runs"][0]["records"]), 6)
        self.assertIn("product", document)
        with open(out_path, "rb") as handle:
            self.assertNotIn(b"\r", handle.read())

    def test_the_product_needs_no_texts(self):
        self.fill_cache([])
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--baseline"])
        self.assertEqual(code, 0)
        self.assertIn("variant: product", text)

    def test_a_sweep_prints_a_table_per_family(self):
        grid = [spec for family in session_lab.GRIDS for spec in session_lab.GRIDS[family]()]
        self.fill_cache(grid)
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--texts", self.replies,
                                "--sweep", "after,pool,recent"])
        self.assertEqual(code, 0)
        for wanted in ("sweep after:", "sweep pool:", "sweep recent:", "| after:max=3 |", "| compact:every=3 |",
                       "| pool:size=5;compact:every=4 |", "gained vs same compaction", "| recent:size=5 |",
                       "in front % (continue)", "in front % (nextday)"):
            self.assertIn(wanted, text)
        tables = [block for block in text.split("\n\n") if block.startswith("| variant |")]
        self.assertEqual(len(tables), 3)
        for block in tables:
            rows = block.split("\n")
            self.assertTrue(all(row.count("|") == rows[0].count("|") for row in rows))
        self.assertEqual(len(tables[0].split("\n")), 2 + 1 + 3)  # header, rule, product, three settings
        self.assertEqual(len(tables[1].split("\n")), 2 + 1 + 3 + 12)  # and the three compaction-only references

    def test_the_new_sweep_families_print_their_tables(self):
        grid = [spec for family in ("rules-pool", "rules-recent", "prime") for spec in session_lab.GRIDS[family]()]
        self.fill_cache(grid)
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--texts", self.replies,
                                "--sweep", "rules-pool,rules-recent,prime"])
        self.assertEqual(code, 0)
        for wanted in ("sweep rules-pool:", "sweep rules-recent:", "sweep prime:", "| compact:every=3 |",
                       "in front % (nextday)", "in front % (return)", "gained vs same compaction"):
            self.assertIn(wanted, text)
        tables = [block for block in text.split("\n\n") if block.startswith("| variant |")]
        self.assertEqual([len(t.split("\n")) for t in tables], [2 + 1 + 1 + 12, 2 + 1 + 12, 2 + 1 + 1 + 40])
        for block in tables:
            rows = block.split("\n")
            self.assertTrue(all(row.count("|") == rows[0].count("|") for row in rows))

    def test_rules_with_reply_need_texts(self):
        self.fill_cache([])
        with self.assertRaises(SystemExit) as context:
            session_lab.main(["--split", self.split, "--cache", self.cache, "--run", "pool:size=2;rules:used=reply"])
        self.assertIn("reply", str(context.exception))

    def test_the_noise_sweep_and_diagnosing_refuse_the_test_split(self):
        folder = os.path.join(self.root, "test")
        os.makedirs(folder)
        for extra in ([], ["--final"]):
            with self.assertRaises(SystemExit) as context:
                session_lab.main(["--split", folder, "--cache", self.cache, "--sweep", "noise"] + extra)
            self.assertIn("--sweep", str(context.exception))
        self.assertFalse(os.path.exists(self.cache))

    def test_the_noise_sweep_prints_one_row_per_setting_in_grid_order(self):
        self.fill_cache(session_lab.grid_noise())
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--sweep", "noise"])
        self.assertEqual(code, 0)
        tables = [block for block in text.split("\n\n") if block.startswith("| variant |")]
        self.assertEqual(len(tables), 1)
        rows = tables[0].split("\n")
        self.assertEqual(len(rows), 2 + 1 + 29)
        self.assertTrue(all(row.count("|") == rows[0].count("|") for row in rows))
        self.assertEqual([row.split(" | ")[0][2:] for row in rows[3:]], session_lab.grid_noise())
        for wanted in ("sweep noise:", "lost (open)", "lost (named)", "lost (continue)", "lost (return)", "lost (nextday)",
                       "main related %", "arrived per turn", "unrelated arrived per turn", "idle: nothing arrived %",
                       "memories in front at the end", "in front % (nextday)", "in front % (return)"):
            self.assertIn(wanted, rows[0] + "\n" + text)

    def test_a_noise_run_prints_its_report(self):
        specs = ["noise:cap=1;compact:every=2"]
        self.fill_cache(specs)
        code, text = self.call(["--split", self.split, "--cache", self.cache, "--run", specs[0]])
        self.assertEqual(code, 0)
        for wanted in ("variant: noise:cap=1;compact:every=2", "in front % (compact:every=2 only)",
                       "unrelated arrived (variant)", "nothing arrived %"):
            self.assertIn(wanted, text)

    def test_a_missing_vector_stops_the_run(self):
        self.fill_cache([])
        with self.assertRaises(SystemExit):
            self.call(["--split", self.split, "--cache", self.cache, "--texts", self.replies, "--run", "after:max=1"])

    def test_sweep_refuses_the_test_split_even_with_final(self):
        folder = os.path.join(self.root, "test")
        os.makedirs(folder)
        for extra in ([], ["--final"]):
            with self.assertRaises(SystemExit) as context:
                session_lab.main(["--split", folder, "--cache", self.cache, "--sweep", "recent"] + extra)
            self.assertIn("--sweep", str(context.exception))
        self.assertFalse(os.path.exists(self.cache))

    def test_the_test_split_needs_final_in_every_other_mode(self):
        folder = os.path.join(self.root, "test")
        os.makedirs(folder)
        for extra in ([], ["--baseline"], ["--run", "product"], ["--run", "product", "--explain", "w/s1"]):
            with self.assertRaises(SystemExit):
                session_lab.main(["--split", folder, "--cache", self.cache] + extra)
        self.assertFalse(os.path.exists(self.cache))

    def test_worlds_without_sessions_are_skipped_and_a_split_without_any_is_refused(self):
        folder = os.path.join(self.root, "plain")
        os.makedirs(folder)
        shutil.copy(_FIXTURE, os.path.join(folder, "fixture-world.json"))
        with self.assertRaises(SystemExit) as context:
            session_lab.main(["--split", folder, "--cache", self.cache, "--baseline"])
        self.assertIn("sessions", str(context.exception))
        self.assertFalse(os.path.exists(self.cache))

    def test_argument_errors(self):
        self.fill_cache([])
        for extra in (["--sweep", "nope"], ["--sweep", ","], ["--explain", "w/s1"], ["--run", "after:max=1"],
                      ["--run", "compact"], ["--run", "pool:size=0"], ["--run", "product", "--explain", "w/none"]):
            with self.assertRaises(SystemExit, msg=" ".join(extra)), contextlib.redirect_stdout(io.StringIO()):
                session_lab.main(["--split", self.split, "--cache", self.cache] + extra)

    def test_the_cache_may_not_live_under_bench_chat(self):
        with self.assertRaises(SystemExit):
            session_lab.main(["--split", self.split, "--cache", os.path.join(_CHAT, "vectors.json"), "--baseline"])
        self.assertFalse(os.path.exists(os.path.join(_CHAT, "vectors.json")))


if __name__ == "__main__":
    unittest.main()
