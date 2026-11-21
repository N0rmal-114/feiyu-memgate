"""Steps 3 and 5 of the reply-quality evaluation: cut the input files of the writers and of the judges.

    python -B bench/chat/replies/prep.py write --work <work folder>
    python -B bench/chat/replies/prep.py judge --work <work folder>

`write` takes the distinct hand-overs of plan.json that have no reply yet (work/replies.json, made by merge.py) and cuts
them into work/in/reply-NN.json, 24 items each, shuffled with a fixed seed across worlds and configurations. An item is
{key, persona, history: [{role, content, notes?}], said, notes: [block texts in order]}: a user message of the history
carries, as `notes`, the blocks that were given at its turn (they stay in the conversation); no label, no configuration name.
The writer's sheet is TASK-REPLY.md; its output, {key: reply}, goes to work/out/reply-NN.json.

`judge` runs after the replies are merged. For every configuration other than base and every turn whose reply key differs
from the base reply key it makes one pair (base reply, configuration reply), shown as `a` and `b` in the order of a seeded
coin that is recorded only in work/pairs.json. The same pair in two configurations is judged once. About one pair in ten is
added a second time with the order swapped (the consistency check). Items are cut into work/in/judge-NN.json, 20 each:
{key, persona, history, said, a, b, relevant: [texts], related_given: [texts], other_given: [texts]}. The history is role and
content only (no blocks). By the labels of the turn: `relevant` is the text of its direct and also_ok memories, given or
not; `related_given` the memories of its cluster that were in front of either side (given at this turn or in an earlier
block of the conversation) and are not in `relevant`; `other_given` the memories in front of either side that are not related
to the turn. For a pair shared by several configurations the lists are the union over all of them. The judge's sheet is
TASK-JUDGE.md; its output, {key: judgement string},
goes to work/out/judge-NN.json. Pairs already judged (work/judgements.json) and pairs with a missing reply are left out.

File numbers are never reused: a new cut continues after the highest number in work/in. Items already in an input file that
has no output file yet are not cut again (they are out with a writer or a judge); items whose output file exists but gave no
usable answer are. Judge items are shuffled with the seed, and neither an `aa` twin of a hand-over nor the swapped repeat of a
pair shares a file with its twin. Deterministic. Prints counts only.
"""
import argparse
import importlib.util
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_shared():
    """shared.py of this folder, loaded by path (once, whoever asks first)."""
    key = "bench_chat_replies_shared"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(_HERE, "shared.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


shared = _load_shared()


def _optional(path):
    """The JSON document at `path`, or {} when there is no such file."""
    return shared.read_json(path) if os.path.isfile(path) else {}


def pending_keys(work, prefix):
    """The keys asked in an input file <prefix>-NN.json of work/in whose answer file work/out/<prefix>-NN.json does not exist
    yet: they are out with a writer or a judge, so a new cut must not hand them out a second time. (A key whose answer file
    exists but gave no usable answer is not pending: merge.py lists it and the next cut takes it again.)"""
    keys = set()
    for number, path in shared.numbered_files(os.path.join(work, "in"), prefix):
        if not os.path.isfile(os.path.join(work, "out", "%s-%02d.json" % (prefix, number))):
            keys.update(item["key"] for item in shared.read_json(path))
    return keys


def writer_items(handovers, replies, seed=shared.SEED, skip=()):
    """The items for the writers: every hand-over key without a reply (and not in `skip`), shuffled with `seed`, as
    {key, persona, history, said, notes}. Items with the same material (an `aa` twin of a base hand-over) never share a
    file of REPLY_ITEMS."""
    keys = sorted(key for key in handovers if key not in replies and key not in skip)
    random.Random(seed).shuffle(keys)
    items = [{"key": key, **{name: handovers[key][name] for name in ("persona", "history", "said", "notes")}} for key in keys]
    return shared.spread(items, shared.REPLY_ITEMS, shared.handover_text, seed)


def build_pairs(plan, replies):
    """({pair id: pair}, [(config, turn index, pair id)], [(config, turn index)]) for every configuration other than base and
    every turn whose reply key differs from the base's. A pair is {base_key, key, base: "a"|"b", turn, config, uses}: the first
    configuration and turn that have it stand for it (`turn` labels the item), and `uses` lists every (configuration, turn
    index) that shares it, so that the judge's lists can be made from all of them. A turn whose two replies are not both
    there is not built; it is listed in the third list."""
    base = plan["configs"][shared.BASE]["turns"]
    pairs, uses, missing = {}, [], []
    for name, config in plan["configs"].items():
        if name == shared.BASE:
            continue
        for index, (a, b) in enumerate(zip(base, config["turns"])):
            if a["key"] == b["key"]:
                continue
            if a["key"] not in replies or b["key"] not in replies:
                missing.append((name, index))
                continue
            pid = shared.pair_id(a["key"], b["key"])
            pair = pairs.setdefault(pid, {"base_key": a["key"], "key": b["key"], "base": shared.base_side(pid), "turn": index,
                                          "config": name, "uses": []})
            pair["uses"].append((name, index))
            uses.append((name, index, pid))
    return pairs, uses, missing


def judge_lists(pair, plan, memtext):
    """(relevant, related_given, other_given): lists of memory texts for the judge, by the labels of the pair's turn.
    `relevant`: the turn's direct and also_ok memories, given or not. `related_given`: memories of the turn's cluster that
    were in front of either side (given at this turn or in an earlier block of this conversation) and are not in
    `relevant`. `other_given`: memories in front of either side that are not related to the turn. In front is the union
    over base and every configuration that shares the pair."""
    turn = plan["turns"][pair["turn"]]

    def text_of(mid):
        return memtext["%s/%s" % (turn["world"], mid)]

    relevant_ids = list(dict.fromkeys(turn["direct"] + turn["also_ok"]))
    related, other = {}, {}
    for name, index in pair["uses"]:
        for side in (shared.BASE, name):
            for item in shared.front_of(plan["configs"][side]["turns"][index]):
                if item["id"] in relevant_ids:
                    continue
                (related if item["related"] else other)[item["id"]] = None
    return [text_of(m) for m in relevant_ids], [text_of(m) for m in related], [text_of(m) for m in other]


def judge_item(pid, pair, side_of_base, plan, handovers, replies, memtext):
    """The item a judge sees for a pair, with the base reply on `side_of_base` ("a" or "b"). The history is role and content
    only: the judge gets the lists, not the blocks."""
    hand = handovers[pair["key"]]
    texts = {"base": replies[pair["base_key"]], "other": replies[pair["key"]]}
    shown = {side_of_base: texts["base"], shared.other_side(side_of_base): texts["other"]}
    relevant, related_given, other_given = judge_lists(pair, plan, memtext)
    return {"key": pid, "persona": hand["persona"],
            "history": [{"role": h["role"], "content": h["content"]} for h in hand["history"]], "said": hand["said"],
            "a": shown["a"], "b": shown["b"], "relevant": relevant, "related_given": related_given,
            "other_given": other_given}


def pair_signature(item):
    """What a pair and its swapped repeat have in common: the message and the two replies, whichever side they are on."""
    return (item["persona"], item["said"], tuple(sorted((item["a"], item["b"]))))


def judge_items(plan, handovers, replies, memtext, judged, skip=()):
    """(items, pairs.json document, number of pairs left out for a missing reply) for the pairs not judged yet (and not in
    `skip`, the ones out with a judge). The items are shuffled with the seed, and a pair and its swapped repeat never share
    a file of JUDGE_ITEMS."""
    pairs, _, missing = build_pairs(plan, replies)
    document, items = {}, []
    for pid in sorted(pairs):
        pair = pairs[pid]
        lists = judge_lists(pair, plan, memtext)
        document[pid] = {"base": pair["base"], "base_key": pair["base_key"], "key": pair["key"],
                         "has_u": bool(lists[0] or lists[1])}
        if pid not in judged and pid not in skip:
            items.append(judge_item(pid, pair, pair["base"], plan, handovers, replies, memtext))
        if shared.repeat_selected(pid):
            rid = shared.repeat_id(pid)
            document[rid] = {"base": shared.other_side(pair["base"]), "base_key": pair["base_key"], "key": pair["key"],
                             "has_u": document[pid]["has_u"], "repeat_of": pid}
            if rid not in judged and rid not in skip:
                items.append(judge_item(rid, pair, shared.other_side(pair["base"]), plan, handovers, replies, memtext))
    items = shared.spread(shared.shuffled(items), shared.JUDGE_ITEMS, pair_signature)
    return items, document, len(missing)


def cut(folder, prefix, items, size):
    """Write `items` into <prefix>-NN.json files of `size` in `folder`, numbers continuing after the highest there is;
    returns the paths."""
    number = shared.next_number(folder, prefix)
    paths = []
    for part in shared.chunks(items, size):
        path = os.path.join(folder, "%s-%02d.json" % (prefix, number))
        shared.write_json(path, part)
        paths.append(path)
        number += 1
    return paths


def command_write(work):
    """The `write` command."""
    handovers = shared.read_json(os.path.join(work, "handovers.json"))
    replies = _optional(os.path.join(work, "replies.json"))
    pending = pending_keys(work, "reply")
    items = writer_items(handovers, replies, skip=pending)
    paths = cut(os.path.join(work, "in"), "reply", items, shared.REPLY_ITEMS)
    shared.emit("%d hand-overs without a reply (of %d; %d more are in input files that have no output file yet), "
                "%d files written to %s" % (len(items), len(handovers), len(pending), len(paths), os.path.join(work, "in")))
    return 0


def command_judge(work):
    """The `judge` command."""
    plan = shared.read_json(os.path.join(work, "plan.json"))
    handovers = shared.read_json(os.path.join(work, "handovers.json"))
    memtext = shared.read_json(os.path.join(work, "memtext.json"))
    replies = _optional(os.path.join(work, "replies.json"))
    judged = _optional(os.path.join(work, "judgements.json"))
    pending = pending_keys(work, "judge")
    items, document, missing = judge_items(plan, handovers, replies, memtext, judged, pending)
    shared.write_json(os.path.join(work, "pairs.json"), document)
    paths = cut(os.path.join(work, "in"), "judge", items, shared.JUDGE_ITEMS)
    shared.emit("%d pairs and repeats in pairs.json, %d items to judge in %d files (%d more are in input files that have no "
                "output file yet); %d (configuration, turn) pairs left out for a missing reply" % (
                    len(document), len(items), len(paths), len(pending), missing))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("write", "judge"))
    parser.add_argument("--work", required=True, help="the work folder of plan.py")
    args = parser.parse_args(argv)
    return command_write(args.work) if args.command == "write" else command_judge(args.work)


if __name__ == "__main__":
    sys.exit(main())
