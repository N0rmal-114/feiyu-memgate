"""bench/chat/replies: keys and de-duplication, the cost table, pairs with the recorded coin, the cutting of files, the parsing
of judgement strings, the merge, scoring on a tiny made-up example and the sign test wiring.

Nothing here needs the texts of the dev data, a vector cache, docker or a network. The Node step is exercised by one test
that is skipped when `node` is not on the path.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_REPLIES = os.path.join(_ROOT, "bench", "chat", "replies")


def _load(name):
    key = "bench_chat_replies_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(_REPLIES, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


shared = _load("shared")
plan_py = _load("plan")
prep = _load("prep")
merge = _load("merge")
score = _load("score")


def hand(said, notes=(), history=()):
    return {"persona": "someone", "history": list(history), "said": said, "notes": list(notes)}


def given(*items):
    return [{"id": mid, "block": "main", "related": related} for mid, related in items]


def tiny_plan():
    """Four turns (open, idle, continue, named); base, x (identical at turn 0) and aa (the same hand-over, salted keys)."""
    turns = [{"world": "w", "session": "s01", "turn": i + 1, "kind": kind, "direct": direct, "also_ok": []}
             for i, (kind, direct) in enumerate([("open", ["m1"]), ("idle", []), ("continue", ["m2"]), ("named", ["m3"])])]
    base_given = [given(("m1", True)), given(("m9", False)), given(("m2", True)), given(("m3", True), ("m8", False))]
    x_given = [given(("m1", True)), given(), given(("m2", True), ("m7", False)), given(("m3", True))]
    configs = {
        "base": {"turns": [{"key": "b%d" % i, "text_key": "t%d" % i, "given": g} for i, g in enumerate(base_given)]},
        "x": {"turns": [{"key": "b0" if i == 0 else "x%d" % i, "text_key": "t0" if i == 0 else "u%d" % i, "given": g}
                        for i, g in enumerate(x_given)]},
        "aa": {"turns": [{"key": "a%d" % i, "text_key": "t%d" % i, "given": g} for i, g in enumerate(base_given)]},
    }
    return {"configs": configs, "turns": turns}


def tiny_inputs(plan):
    keys = {row["key"] for c in plan["configs"].values() for row in c["turns"]}
    handovers = {key: hand("said " + key) for key in keys}
    replies = {key: "reply %s %s" % (key, "x" * 20) for key in keys}
    memtext = {"w/m%d" % i: "memory %d" % i for i in range(1, 10)}
    return handovers, replies, memtext


class KeysTest(unittest.TestCase):
    def test_equal_handovers_share_a_key_and_salt_separates(self):
        a, b = hand("hello", ["n"]), hand("hello", ["n"])
        self.assertEqual(shared.reply_key(a), shared.reply_key(b))
        self.assertNotEqual(shared.reply_key(a), shared.reply_key(a, "aa"))
        self.assertNotEqual(shared.reply_key(a), shared.reply_key(hand("hello", ["m"])))
        self.assertEqual(len(shared.reply_key(a)), 10)

    def test_pair_ids_are_stable_and_ordered(self):
        self.assertEqual(shared.pair_id("k1", "k2"), shared.pair_id("k1", "k2"))
        self.assertNotEqual(shared.pair_id("k1", "k2"), shared.pair_id("k2", "k1"))
        self.assertNotEqual(shared.repeat_id("p1"), "p1")

    def test_coin_is_deterministic_and_uses_both_sides(self):
        sides = [shared.base_side("p%d" % i) for i in range(200)]
        self.assertEqual(sides, [shared.base_side("p%d" % i) for i in range(200)])
        self.assertEqual(set(sides), {"a", "b"})
        self.assertTrue(60 < sides.count("a") < 140)

    def test_about_one_pair_in_ten_is_repeated(self):
        chosen = sum(shared.repeat_selected("p%d" % i) for i in range(2000))
        self.assertTrue(120 < chosen < 280, chosen)

    def test_chunks_and_numbering_never_reuse(self):
        self.assertEqual(shared.chunks(list(range(5)), 2), [[0, 1], [2, 3], [4]])
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(shared.next_number(folder, "reply"), 1)
            for name in ("reply-01.json", "reply-07.json", "judge-02.json", "other.json"):
                open(os.path.join(folder, name), "w").close()
            self.assertEqual(shared.next_number(folder, "reply"), 8)
            self.assertEqual([n for n, _ in shared.numbered_files(folder, "reply")], [1, 7])


class ParsingTest(unittest.TestCase):
    def test_judgement_strings(self):
        parsed = shared.parse_judgement("a:u1,d0,w0;b:u0,d1,w0;better:a")
        self.assertEqual(parsed, {"a": {"u": 1, "d": 0, "w": 0}, "b": {"u": 0, "d": 1, "w": 0}, "better": "a"})
        dash = shared.parse_judgement("a:u-,d0,w1;b:u-,d0,w0;better:same")
        self.assertIsNone(dash["a"]["u"])
        self.assertEqual(dash["better"], "same")
        for bad in ("", "a:u1,d0,w0;b:u0,d1,w0", "a:u2,d0,w0;b:u0,d1,w0;better:a", "a:u1,d0,w0;b:u0,d1,w0;better:c",
                    "a:u1,d0,w0; b:u0,d1,w0;better:a", None, 5):
            self.assertIsNone(shared.parse_judgement(bad), bad)

    def test_reply_limits(self):
        self.assertIsNone(shared.reply_problem("字" * 20))
        self.assertIsNone(shared.reply_problem("字" * 60))
        self.assertEqual(shared.reply_problem("字" * 19), "length 19")
        self.assertEqual(shared.reply_problem("字" * 61), "length 61")
        self.assertEqual(shared.reply_problem("字" * 19 + "\n" + "字"), "more than one line")
        self.assertEqual(shared.reply_problem("字" * 25 + '"'), "double quote")
        self.assertEqual(shared.reply_problem("字" * 25 + "“"), "double quote")
        self.assertEqual(shared.reply_problem(5), "not a string")


class PlanTest(unittest.TestCase):
    def test_config_table_specs_parse_in_session_lab(self):
        lab = shared.session_lab()
        for name, config in plan_py.CONFIGS.items():
            parsed = lab.parse_spec(config["spec"])
            self.assertIn(config["tone"], ("normal", "strict"), name)
        self.assertEqual(lab.parse_spec(plan_py.CONFIGS["cap3"]["spec"])["noise"]["cap"], 3)
        self.assertEqual(lab.parse_spec(plan_py.CONFIGS["combo"]["spec"])["after"]["max"], 2)
        self.assertEqual(lab.parse_spec(plan_py.CONFIGS["combo"]["spec"])["recent"]["size"], 3)
        self.assertEqual(plan_py.CONFIGS["strict"]["spec"], plan_py.CONFIGS["base"]["spec"])
        self.assertEqual(plan_py.CONFIGS["combo-strict"]["spec"], plan_py.CONFIGS["combo"]["spec"])
        self.assertTrue(plan_py.CONFIGS["aa"]["salt"])

    def test_names_and_dates(self):
        self.assertEqual(plan_py.parse_names("cap3,after,cap3"), ["base", "cap3", "after"])
        self.assertEqual(plan_py.parse_names(None)[0], "base")
        with self.assertRaises(SystemExit):
            plan_py.parse_names("nope")
        session = {"turns": [{}, {}, {"new_conversation": True, "gap_days": 2}, {}]}
        self.assertEqual(plan_py.days_of(session), {1: 0, 2: 0, 3: 2, 4: 2})
        self.assertEqual(plan_py.today_of("2026-10-30", 3), "2026-11-02")

    def test_session_sample_is_deterministic_and_per_world(self):
        def world(name, count):
            return {"world": name, "memories": {}, "sessions": [{"id": "s%02d" % i, "turns": []} for i in range(1, count + 1)]}

        worlds = [world("w1", 6), world("w2", 6), world("w3", 2), {"world": "w4", "memories": {}}]
        first = plan_py.sample_sessions(worlds, 2)
        again = plan_py.sample_sessions([world("w1", 6), world("w2", 6), world("w3", 2), {"world": "w4", "memories": {}}], 2)
        ids = [[s["id"] for s in w.get("sessions", [])] for w in first]
        self.assertEqual(ids, [[s["id"] for s in w.get("sessions", [])] for w in again])
        self.assertEqual([len(i) for i in ids], [2, 2, 2, 0])  # N per world, all of a small world, none made up
        for chosen in ids:
            self.assertEqual(chosen, sorted(chosen))  # the order of the world file
        self.assertEqual(len(worlds[0]["sessions"]), 6)  # the input is not changed
        # the choice is per world (the same session id is not picked in every world) and moves with the seed
        many = [plan_py.sample_sessions([world("w%d" % n, 12)], 1)[0]["sessions"][0]["id"] for n in range(30)]
        self.assertGreater(len(set(many)), 3)
        self.assertGreater(len({plan_py.sample_sessions([world("w", 30)], 3, seed)[0]["sessions"][0]["id"] for seed in range(10)}), 1)
        # a larger sample contains the smaller one's sessions (the rank does not change), and None keeps everything
        small = {s["id"] for s in plan_py.sample_sessions([world("w", 12)], 2)[0]["sessions"]}
        large = {s["id"] for s in plan_py.sample_sessions([world("w", 12)], 5)[0]["sessions"]}
        self.assertTrue(small < large)
        self.assertEqual(len(plan_py.sample_sessions([world("w", 12)])[0]["sessions"]), 12)

    def test_cost_table_says_what_the_sample_is(self):
        rows, totals = plan_py.cost_rows(tiny_plan())
        text = "\n".join(plan_py.table_lines(rows, totals, {"worlds": 2, "sessions": 2, "turns": 9, "per_world": 1}))
        self.assertIn("sample: 2 sessions and 9 turns in 2 worlds (1 per world)", text)
        text = "\n".join(plan_py.table_lines(rows, totals, {"worlds": 2, "sessions": 6, "turns": 40, "per_world": None}))
        self.assertIn("all sessions", text)

    def test_cost_table(self):
        plan = tiny_plan()
        rows, totals = plan_py.cost_rows(plan)
        by_name = {row[0]: row for row in rows}
        self.assertEqual(by_name["base"][1:], [4, 0, 4, 0, 0])
        self.assertEqual(by_name["x"][1:], [4, 3, 4, 3, 3])  # turn 0 shares base's reply
        self.assertEqual(by_name["aa"][1:], [4, 0, 4, 4, 4])  # the same hand-overs, written again
        self.assertEqual(totals["replies"], 4 + 3 + 4)
        self.assertEqual(totals["pairs"], 7)
        lines = plan_py.table_lines(rows, totals)
        self.assertTrue(lines[0].startswith("| configuration"))


def entry_of(turn, history, main=(), extra=(), earlier=(), earlier_all=None, related=()):
    memories = lambda ids: [{"id": m, "text": "memory " + m, "date": "2026-01-01"} for m in ids]
    return {"world": "w", "session": "s01", "turn": turn, "persona": "someone", "history": history, "said": "now",
            "main": memories(main), "extra": memories(extra), "earlier": list(earlier),
            "earlier_all": list(earlier if earlier_all is None else earlier_all), "related_ids": set(related)}


class HistoryNotesTest(unittest.TestCase):
    blocks = {"main": "BLOCK1", "extra": None}

    def history(self):
        return [{"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"}]

    def test_user_messages_carry_the_blocks_of_their_turn(self):
        entry = entry_of(3, self.history(), earlier=[0, 1])
        past = [{"main": "B1", "extra": "X1"}, {"main": None, "extra": None}]
        hand_over = plan_py.build_handover(entry, {"main": "NOW", "extra": None}, past)
        self.assertEqual(hand_over["notes"], ["NOW"])
        self.assertEqual(hand_over["history"][0], {"role": "user", "content": "u1", "notes": ["B1", "X1"]})
        self.assertNotIn("notes", hand_over["history"][1])  # the assistant's line has none
        self.assertNotIn("notes", hand_over["history"][2])  # a turn without a block: the field is left out
        self.assertNotIn("notes", entry["history"][0])  # the entry's own history is not changed

    def test_the_key_hashes_what_the_writer_sees_including_earlier_blocks(self):
        entry = entry_of(3, self.history(), earlier=[0, 1])
        now = {"main": "NOW", "extra": None}
        same = {"main": None, "extra": None}
        one = plan_py.build_handover(entry, now, [{"main": "B1", "extra": None}, same])
        two = plan_py.build_handover(entry, now, [{"main": "B2", "extra": None}, same])
        none = plan_py.build_handover(entry, now, [same, same])
        again = plan_py.build_handover(entry, now, [{"main": "B1", "extra": None}, same])
        self.assertEqual(shared.reply_key(one), shared.reply_key(again))
        self.assertNotEqual(shared.reply_key(one), shared.reply_key(two))
        self.assertNotEqual(shared.reply_key(one), shared.reply_key(none))

    def test_a_new_conversation_has_no_history_and_no_notes(self):
        hand_over = plan_py.build_handover(entry_of(1, []), {"main": "NOW", "extra": "MORE"}, [])
        self.assertEqual((hand_over["history"], hand_over["notes"]), ([], ["NOW", "MORE"]))

    def test_history_and_earlier_turns_must_match(self):
        with self.assertRaises(SystemExit):
            plan_py.build_handover(entry_of(3, self.history(), earlier=[0]), self.blocks, [self.blocks])

    def test_in_front_is_this_turn_plus_earlier_blocks_labelled_by_this_turn(self):
        entries = [entry_of(1, [], main=["m1", "m2"]), entry_of(2, [], main=["m2", "m3"], extra=["m4"], earlier=[0]),
                   entry_of(3, [], main=["m5"], earlier=[0, 1], earlier_all=[0, 1], related=["m2", "m5"])]
        front = plan_py.in_front(entries, entries[2])
        self.assertEqual([f["id"] for f in front], ["m1", "m2", "m3", "m4", "m5"])  # each once, earlier first
        self.assertEqual([f["id"] for f in front if f["related"]], ["m2", "m5"])
        self.assertEqual([f["id"] for f in plan_py.in_front(entries, entries[0])], ["m1", "m2"])  # nothing before turn 1


class PrepTest(unittest.TestCase):
    def test_writer_items_skip_replied_keys_and_shuffle_with_a_seed(self):
        handovers = {"k%02d" % i: hand("said %d" % i) for i in range(30)}
        items = prep.writer_items(handovers, {"k03": "done"})
        self.assertEqual(len(items), 29)
        self.assertNotIn("k03", [item["key"] for item in items])
        self.assertEqual([i["key"] for i in items], [i["key"] for i in prep.writer_items(handovers, {"k03": "done"})])
        self.assertNotEqual([i["key"] for i in items], sorted(i["key"] for i in items))
        self.assertEqual(set(items[0]), {"key", "persona", "history", "said", "notes"})

    def test_twins_never_share_a_writer_file(self):
        handovers = {}
        for i in range(120):
            handovers["b%03d" % i] = hand("said %d" % i)
            handovers["a%03d" % i] = hand("said %d" % i)  # the aa twin: same material, other key
        items = prep.writer_items(handovers, {})
        self.assertEqual(sorted(i["key"] for i in items), sorted(handovers))
        for chunk in shared.chunks(items, shared.REPLY_ITEMS):
            said = [i["said"] for i in chunk]
            self.assertEqual(len(said), len(set(said)))
        self.assertEqual([i["key"] for i in items], [i["key"] for i in prep.writer_items(handovers, {})])

    def test_spread_leaves_what_it_cannot_separate(self):
        self.assertEqual(shared.spread(["x", "x", "y"], 3, lambda v: v), ["x", "x", "y"])  # one chunk holds everything
        self.assertEqual(shared.spread([], 3, lambda v: v), [])

    def test_a_cut_does_not_hand_out_what_is_out_with_a_writer(self):
        handovers = {"k%02d" % i: hand("said %d" % i) for i in range(30)}
        with tempfile.TemporaryDirectory() as work:
            shared.write_json(os.path.join(work, "in", "reply-01.json"), [{"key": "k00"}, {"key": "k01"}])
            shared.write_json(os.path.join(work, "in", "reply-02.json"), [{"key": "k02"}])
            shared.write_json(os.path.join(work, "out", "reply-02.json"), {"k02": "x"})  # answered, maybe unusable
            self.assertEqual(prep.pending_keys(work, "reply"), {"k00", "k01"})
            items = prep.writer_items(handovers, {}, skip=prep.pending_keys(work, "reply"))
            keys = {i["key"] for i in items}
            self.assertEqual(len(items), 28)
            self.assertFalse(keys & {"k00", "k01"})
            self.assertIn("k02", keys)  # its answer file exists, so it is cut again unless merge.py took a reply
            self.assertEqual(prep.pending_keys(work, "judge"), set())

    def test_judge_items_are_shuffled_and_repeats_stand_apart(self):
        plan = {"configs": {"base": {"turns": []}, "x": {"turns": []}}, "turns": []}
        for i in range(300):
            plan["turns"].append({"world": "w", "session": "s01", "turn": i + 1, "kind": "open", "direct": ["m1"], "also_ok": []})
            plan["configs"]["base"]["turns"].append({"key": "b%d" % i, "text_key": "t%d" % i, "given": given(("m1", True))})
            plan["configs"]["x"]["turns"].append({"key": "x%d" % i, "text_key": "u%d" % i, "given": given(("m1", True))})
        keys = {r["key"] for c in plan["configs"].values() for r in c["turns"]}
        replies = {k: "reply " + k + "." * 20 for k in keys}
        handovers = {k: hand("said " + k[1:]) for k in keys}
        items, document, _ = prep.judge_items(plan, handovers, replies, {"w/m1": "memory 1"}, {})
        order = [i["key"] for i in items]
        self.assertNotEqual(order, sorted(order))
        self.assertEqual(order, [i["key"] for i in prep.judge_items(plan, handovers, replies, {"w/m1": "memory 1"}, {})[0]])
        position = {key: n for n, key in enumerate(order)}
        repeats = [rid for rid, entry in document.items() if "repeat_of" in entry]
        self.assertTrue(repeats)
        for rid in repeats:
            first = document[rid]["repeat_of"]
            self.assertNotEqual(position[rid] // shared.JUDGE_ITEMS, position[first] // shared.JUDGE_ITEMS)
        again, _, _ = prep.judge_items(plan, handovers, replies, {"w/m1": "memory 1"}, {}, skip={order[0]})
        self.assertEqual(len(again), len(items) - 1)

    def test_three_lists_and_the_union_for_shared_pairs(self):
        def row(key, front):
            return {"key": key, "text_key": key, "given": [], "front": [{"id": i, "related": r} for i, r in front]}

        turns = [{"world": "w", "session": "s01", "turn": 1, "kind": "continue", "direct": ["m1"], "also_ok": ["m2"]}]
        plan = {"turns": turns, "configs": {
            "base": {"turns": [row("b0", [("m1", True), ("m3", True)])]},
            "x": {"turns": [row("x0", [("m1", True), ("m4", False), ("m5", True)])]},
            "y": {"turns": [row("x0", [("m1", True), ("m6", False)])]},  # y shares x's reply, so the pair
        }}
        memtext = {"w/m%d" % i: "memory %d" % i for i in range(1, 7)}
        history = [{"role": "user", "content": "u", "notes": ["BLOCK"]}]
        handovers = {"b0": {"persona": "p", "said": "s", "history": history, "notes": []},
                     "x0": {"persona": "p", "said": "s", "history": history, "notes": []}}
        replies = {"b0": "base reply", "x0": "x reply"}
        items, document, _ = prep.judge_items(plan, handovers, replies, memtext, {})
        item = next(i for i in items if i["key"] == shared.pair_id("b0", "x0"))
        self.assertEqual(item["relevant"], ["memory 1", "memory 2"])  # direct and also_ok, given or not
        self.assertEqual(item["related_given"], ["memory 3", "memory 5"])  # the cluster's, in front of either side
        self.assertEqual(item["other_given"], ["memory 4", "memory 6"])  # y's unrelated one counts: the union
        self.assertEqual(item["history"], [{"role": "user", "content": "u"}])  # no blocks for the judge
        self.assertTrue(document[item["key"]]["has_u"])
        self.assertNotIn("BLOCK", json.dumps(item, ensure_ascii=False))

    def test_has_u_is_false_when_no_relevant_and_no_related_given(self):
        plan = tiny_plan()
        handovers, replies, memtext = tiny_inputs(plan)
        _, document, _ = prep.judge_items(plan, handovers, replies, memtext, {})
        self.assertFalse(document[shared.pair_id("b1", "x1")]["has_u"])  # idle: nothing relevant, only unrelated m9
        self.assertTrue(document[shared.pair_id("b2", "x2")]["has_u"])

    def test_pairs_use_the_recorded_coin_and_share_equal_pairs(self):
        plan = tiny_plan()
        # a second configuration with the same replies as x gives the very same pairs
        plan["configs"]["y"] = json.loads(json.dumps(plan["configs"]["x"]))
        handovers, replies, memtext = tiny_inputs(plan)
        pairs, uses, missing = prep.build_pairs(plan, replies)
        self.assertEqual(missing, [])
        self.assertEqual(len(pairs), 7)  # x and y share three pairs, aa has four
        self.assertEqual(len(uses), 10)
        self.assertNotIn(shared.pair_id("b0", "b0"), pairs)
        for pid, pair in pairs.items():
            self.assertEqual(pair["base"], shared.base_side(pid))
        # a reply that is not there leaves its pairs out, and says so
        gone = {k: v for k, v in replies.items() if k != "x2"}
        pairs, _, missing = prep.build_pairs(plan, gone)
        self.assertEqual(missing, [("x", 2), ("y", 2)])
        self.assertNotIn(shared.pair_id("b2", "x2"), pairs)

    def test_judge_items_lists_and_blindness(self):
        plan = tiny_plan()
        handovers, replies, memtext = tiny_inputs(plan)
        items, document, missing = prep.judge_items(plan, handovers, replies, memtext, {})
        self.assertEqual(missing, 0)
        pid = shared.pair_id("b2", "x2")
        item = next(i for i in items if i["key"] == pid)
        self.assertEqual(set(item), {"key", "persona", "history", "said", "a", "b", "relevant", "related_given", "other_given"})
        self.assertEqual(item["relevant"], ["memory 2"])
        self.assertEqual(item["other_given"], ["memory 7"])  # base gave only m2 here; x gave the unrelated m7
        side = document[pid]["base"]
        self.assertEqual(item[side], replies["b2"])
        self.assertEqual(item[shared.other_side(side)], replies["x2"])
        # the turn with both sides unrelated: both sides' texts are listed once each
        item3 = next(i for i in items if i["key"] == shared.pair_id("b3", "x3"))
        self.assertEqual(item3["other_given"], ["memory 8"])
        # an idle turn has nothing relevant
        item1 = next(i for i in items if i["key"] == shared.pair_id("b1", "x1"))
        self.assertEqual(item1["relevant"], [])
        self.assertEqual(item1["other_given"], ["memory 9"])
        text = json.dumps(items, ensure_ascii=False)
        for name in plan["configs"]:
            self.assertNotIn('"%s"' % name, text)

    def test_repeats_are_swapped_and_recorded(self):
        plan = {"configs": {"base": {"turns": []}, "x": {"turns": []}}, "turns": []}
        for i in range(300):
            plan["turns"].append({"world": "w", "session": "s01", "turn": i + 1, "kind": "open", "direct": ["m1"], "also_ok": []})
            plan["configs"]["base"]["turns"].append({"key": "b%d" % i, "text_key": "t%d" % i, "given": given(("m1", True))})
            plan["configs"]["x"]["turns"].append({"key": "x%d" % i, "text_key": "u%d" % i, "given": given(("m1", True))})
        keys = {r["key"] for c in plan["configs"].values() for r in c["turns"]}
        replies = {k: "reply " + k + "." * 20 for k in keys}
        handovers = {k: hand("said") for k in keys}
        items, document, _ = prep.judge_items(plan, handovers, replies, {"w/m1": "memory 1"}, {})
        repeats = {k: v for k, v in document.items() if "repeat_of" in v}
        self.assertEqual(len(items), 300 + len(repeats))
        self.assertTrue(10 < len(repeats) < 60, len(repeats))
        for rid, entry in repeats.items():
            first = document[entry["repeat_of"]]
            self.assertEqual(entry["base"], shared.other_side(first["base"]))
            item = next(i for i in items if i["key"] == rid)
            self.assertEqual(item[entry["base"]], replies[entry["base_key"]])
        # what is judged already is not cut again
        again, _, _ = prep.judge_items(plan, handovers, replies, {"w/m1": "memory 1"}, {k: "x" for k in document})
        self.assertEqual(again, [])

    def test_cut_continues_numbering(self):
        with tempfile.TemporaryDirectory() as folder:
            first = prep.cut(folder, "reply", [{"key": str(i)} for i in range(5)], 2)
            second = prep.cut(folder, "reply", [{"key": "z"}], 2)
            self.assertEqual([os.path.basename(p) for p in first], ["reply-01.json", "reply-02.json", "reply-03.json"])
            self.assertEqual([os.path.basename(p) for p in second], ["reply-04.json"])
            with open(first[0], "rb") as handle:
                self.assertNotIn(b"\r", handle.read())


class MergeTest(unittest.TestCase):
    def test_merge_reports_missing_unknown_and_bad(self):
        good = "字" * 25
        merged, missing, unknown, bad = merge.merge_answers(
            [["a", "b", "c", "d"]], [{"a": good, "b": "短", "z": good, "d": good}], {"a", "b", "c", "d"}, shared.reply_problem)
        self.assertEqual(merged, {"a": good, "d": good})
        self.assertEqual(missing, ["c"])
        self.assertEqual(unknown, ["z"])
        self.assertEqual(bad, {"b": "length 1"})

    def test_a_later_good_answer_replaces_a_bad_one(self):
        good = "字" * 25
        merged, missing, _, bad = merge.merge_answers([["a"]], [{"a": "短"}, {"a": good}], {"a"}, shared.reply_problem)
        self.assertEqual((merged, missing, bad), ({"a": good}, [], {}))

    def test_judgements_that_do_not_parse_are_listed_not_guessed(self):
        merged, missing, _, bad = merge.merge_answers(
            [["a", "b"]], [{"a": "a:u1,d0,w0;b:u0,d0,w0;better:b", "b": "better:a"}], {"a", "b"}, merge._judgement_problem)
        self.assertEqual(list(merged), ["a"])
        self.assertEqual(bad, {"b": "does not parse"})

    def test_a_broken_output_file_is_named_and_its_keys_are_missing(self):
        with tempfile.TemporaryDirectory() as work:
            shared.write_json(os.path.join(work, "in", "reply-01.json"), [{"key": "a"}])
            shared.write_json(os.path.join(work, "in", "reply-02.json"), [{"key": "b"}])
            shared.write_json(os.path.join(work, "in", "reply-03.json"), [{"key": "c"}])
            os.makedirs(os.path.join(work, "out"))
            with open(os.path.join(work, "out", "reply-01.json"), "w", encoding="utf-8") as handle:
                handle.write('{"a": "cut off')
            shared.write_json(os.path.join(work, "out", "reply-02.json"), ["not", "an", "object"])
            shared.write_json(os.path.join(work, "out", "reply-03.json"), {"c": "字" * 30})
            lines, redo = merge.merge_kind(work, "reply", shared.reply_problem, {"a", "b", "c"})
            self.assertEqual(shared.read_json(os.path.join(work, "replies.json")), {"c": "字" * 30})
            self.assertTrue(any("reply-01.json, reply-02.json" in line for line in lines))
            self.assertTrue(any("missing (2)" in line for line in lines))
            self.assertGreaterEqual(redo, 2)

    def test_merge_kind_writes_files_and_counts(self):
        with tempfile.TemporaryDirectory() as work:
            shared.write_json(os.path.join(work, "in", "reply-01.json"), [{"key": "a"}, {"key": "b"}])
            shared.write_json(os.path.join(work, "out", "reply-01.json"), {"a": "字" * 30})
            lines, redo = merge.merge_kind(work, "reply", shared.reply_problem, {"a", "b"})
            self.assertEqual(redo, 1)
            self.assertEqual(shared.read_json(os.path.join(work, "replies.json")), {"a": "字" * 30})
            self.assertTrue(any("missing (1)" in line for line in lines))


def judged_example():
    """The tiny plan with judgements: x wins turn 1, loses turn 2, ties turn 3; aa differs only at turn 0."""
    plan = tiny_plan()
    handovers, replies, memtext = tiny_inputs(plan)
    _, document, _ = prep.judge_items(plan, handovers, replies, memtext, {})
    judgements = {}

    def put(base_key, key, who, base_flags, other_flags):
        pid = shared.pair_id(base_key, key)
        side = document[pid]["base"]
        other = shared.other_side(side)
        better = "same" if who == "same" else (side if who == "base" else other)
        flags = {side: base_flags, other: other_flags}

        def text(f):
            return "u%s,d%d,w%d" % (f[0], f[1], f[2])

        judgements[pid] = "a:%s;b:%s;better:%s" % (text(flags["a"]), text(flags["b"]), better)

    put("b1", "x1", "config", ("-", 1, 0), ("-", 0, 0))
    put("b2", "x2", "base", (1, 0, 0), (0, 1, 1))
    put("b3", "x3", "same", (1, 1, 0), (1, 1, 0))
    put("b0", "a0", "same", (1, 0, 0), (0, 0, 0))
    put("b1", "a1", "same", ("-", 0, 0), ("-", 0, 0))
    put("b2", "a2", "same", (1, 0, 0), (1, 0, 0))
    put("b3", "a3", "same", (1, 0, 0), (1, 0, 0))
    return plan, judgements, document


class ScoreTest(unittest.TestCase):
    def test_summary_on_the_tiny_example(self):
        plan, judgements, document = judged_example()
        result = score.score(plan, judgements, document)
        x = result["configs"]["x"]["all"]
        self.assertEqual((x["turns"], x["identical"], x["judged"], x["not_judged"]), (4, 1, 3, 0))
        self.assertEqual((x["wins"], x["losses"], x["same"]), (1, 1, 1))
        self.assertAlmostEqual(x["p"], shared.sign_test(1, 1))
        self.assertEqual(x["p"], 1.0)
        # u: only the continue (turn 2) and named (turn 3) turns have a direct memory
        self.assertEqual(x["u"]["n"], 2)
        self.assertEqual((x["u"]["config"], x["u"]["base"], x["u"]["diff"]), (0.5, 1.0, -0.5))
        self.assertEqual((x["u"]["up"], x["u"]["down"]), (0, 1))
        # d over turns where something unrelated was given to x: only turn 2 (m7)
        self.assertEqual((x["d_given"]["n"], x["d_given"]["config"], x["d_given"]["base"]), (1, 1.0, 0.0))
        self.assertEqual(x["d_all"]["n"], 3)
        self.assertAlmostEqual(x["w"]["config"], 1 / 3)
        self.assertEqual(result["configs"]["x"]["kinds"]["continue"]["losses"], 1)
        self.assertEqual(result["configs"]["x"]["kinds"]["idle"]["wins"], 1)
        self.assertEqual(result["configs"]["x"]["kinds"]["open"]["identical"], 1)

    def test_u_over_related_given_turns_and_the_old_direct_definition(self):
        plan = tiny_plan()
        for c in plan["configs"].values():  # the idle turn gets a related memory in front of both sides
            c["turns"][1]["front"] = [{"id": "m9", "related": True}]
        handovers, replies, memtext = tiny_inputs(plan)
        _, document, _ = prep.judge_items(plan, handovers, replies, memtext, {})
        self.assertTrue(document[shared.pair_id("b1", "x1")]["has_u"])
        _, judgements, _ = judged_example()
        rows = score.turn_rows(plan, score.parse_all(judgements), document, "x")
        self.assertTrue(rows[1]["has_u"])
        self.assertFalse(rows[1]["has_direct"])
        x = score.score(plan, judgements, document)["configs"]["x"]["all"]
        self.assertEqual(x["u_direct"]["n"], 2)  # turns with a direct memory: 2 and 3
        self.assertEqual(x["u"]["n"], 2)  # the idle judgement says u-, so it adds no value to the rate

    def test_session_level_sign_test(self):
        def rows(spec):
            out = []
            for session, results in spec.items():
                out += [{"session": session, "judged": True, "who": who} for who in results]
            out.append({"session": "never", "judged": False, "who": None})
            return out

        spec = {"s1": ["config", "config", "base"], "s2": ["config", "same"], "s3": ["base", "base", "config"],
                "s4": ["config", "base"], "s5": ["config"], "s6": ["same"]}
        result = score.session_test(rows(spec))
        self.assertEqual((result["sessions"], result["for"], result["against"], result["tied"]), (6, 3, 1, 2))
        self.assertAlmostEqual(result["p"], shared.sign_test(3, 1))
        # the per-turn test counts 6 wins against 4 losses; the session test works on the nets
        flags = {"config": {"u": 1, "d": 0, "w": 0}, "base": {"u": 0, "d": 0, "w": 0}}
        turns = score.summarize([{**r, "kind": "open", "identical": False, "has_direct": True, "has_u": True,
                                  "unrelated": False, "flags": flags} for r in rows(spec)])
        self.assertEqual((turns["wins"], turns["losses"]), (6, 4))
        self.assertEqual(turns["sessions"]["for"], 3)
        plan, judgements, document = judged_example()
        text = "\n".join(score.report_lines(score.score(plan, judgements, document)))
        self.assertIn("stricter", text)
        self.assertIn("p (sessions", text)

    def test_a_pair_not_judged_yet_is_counted_apart(self):
        plan, judgements, document = judged_example()
        del judgements[shared.pair_id("b3", "x3")]
        x = score.score(plan, judgements, document)["configs"]["x"]["all"]
        self.assertEqual((x["judged"], x["not_judged"]), (2, 1))

    def test_sign_test_wiring(self):
        self.assertEqual(shared.sign_test(0, 0), 1.0)
        self.assertAlmostEqual(shared.sign_test(8, 0), 2 / 256)
        self.assertEqual(shared.sign_test(3, 3), shared.session_lab().model_lab.sign_test(3, 3))

    def test_noise_floor(self):
        plan, judgements, document = judged_example()
        noise = score.score(plan, judgements, document)["configs"]["aa"]["noise"]
        self.assertEqual(noise["judged"], 4)
        self.assertEqual(noise["not_same"], 0.0)
        self.assertAlmostEqual(noise["u"], 1 / 3)  # u differs at turn 0 only; the idle turn has no u
        self.assertEqual(noise["d"], 0.0)

    def test_consistency_and_position(self):
        document = {
            "p1": {"base": "a"}, "r1": {"base": "b", "repeat_of": "p1"},
            "p2": {"base": "a"}, "r2": {"base": "b", "repeat_of": "p2"},
        }
        parsed = {
            "p1": shared.parse_judgement("a:u1,d0,w0;b:u0,d0,w0;better:a"),  # base wins
            "r1": shared.parse_judgement("a:u0,d0,w0;b:u1,d0,w0;better:b"),  # base (now on b) wins again
            "p2": shared.parse_judgement("a:u1,d0,w0;b:u0,d0,w0;better:a"),  # base wins
            "r2": shared.parse_judgement("a:u1,d0,w0;b:u0,d0,w0;better:a"),  # config (now on a) wins: not kept
        }
        out = score.consistency(parsed, document)
        self.assertEqual((out["repeats"], out["kept"], out["kept_share"]), (2, 1, 0.5))
        self.assertEqual((out["a"], out["b"]), (3, 1))
        self.assertAlmostEqual(out["p"], shared.sign_test(3, 1))

    def test_report_runs_on_the_example(self):
        plan, judgements, document = judged_example()
        lines = score.report_lines(score.score(plan, judgements, document))
        text = "\n".join(lines)
        self.assertIn("| x |", text)
        self.assertIn("Noise floor", text)
        self.assertIn("Judge consistency", text)


@unittest.skipUnless(shutil.which("node"), "node is not on the path")
class HandoverTest(unittest.TestCase):
    def run_node(self, items):
        done = subprocess.run(["node", os.path.join(_REPLIES, "handover.mjs")], input=json.dumps(items).encode("utf-8"),
                              capture_output=True, check=True)
        return json.loads(done.stdout.decode("utf-8"))

    def test_blocks_come_from_the_plugin_formatter(self):
        memory = {"text": "memory text", "date": "2026-09-27"}
        item = {"id": "1", "language": "zh", "today": "2026-10-01", "tone": "normal", "main": [memory], "extra": [memory], "more": 2}
        normal = self.run_node([item, {**item, "id": "2", "tone": "strict"},
                                {"id": "3", "language": "zh", "tone": "normal", "main": [], "extra": []}])
        self.assertTrue(normal["1"]["main"].startswith("<memory_data>\n"))
        self.assertIn("memory text", normal["1"]["main"])
        self.assertIn("2026-10-01", normal["1"]["main"])
        self.assertIn("2", normal["1"]["main"].splitlines()[-2])  # the "more" line comes before the closing tag
        self.assertNotIn("memory text", normal["1"]["main"].splitlines()[-2])
        self.assertNotEqual(normal["1"]["main"], normal["2"]["main"])  # the strict tone has its own head
        self.assertEqual(normal["1"]["extra"].count("\n- "), 1)
        self.assertNotEqual(normal["1"]["extra"].splitlines()[1], normal["1"]["main"].splitlines()[1])  # the second block's head
        self.assertEqual(normal["2"]["extra"], normal["1"]["extra"])  # the tone is the main block's only
        self.assertEqual(normal["3"], {"main": None, "extra": None})


if __name__ == "__main__":
    unittest.main()
