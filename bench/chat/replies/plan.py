"""Step 2 of the reply-quality evaluation: which hand-over every configuration gives the writer at every turn.

    python -B bench/chat/replies/plan.py --split bench/chat/dev --cache <vectors-dev.json> --texts <texts-reply.json> \\
        --work <work folder> [--configs base,after,recent,cap3,strict,combo,combo-strict,aa] [--sessions-per-world N]

What it does. Every configuration (the table CONFIGS below: a session_lab spec and a tone) is replayed over the sessions of
the split with session_lab.replay_session (imported, not copied), from cached vectors: no embedder, no network, no docker.
For every (configuration, turn) the memories given in the main block and in the second block become a hand-over: the
persona line of the world, the scripted conversation so far in the current conversation (a `new_conversation` turn
starts an empty history; every user message of it carries, as `notes`, the blocks given at its turn under the same
configuration, since in the product a block stays in the conversation), the user's message, and the block texts as the
plugin writes them (handover.mjs, which imports
plugins/dsh-feiyu-memgate/src/block.js; needs `node`). Hand-overs with the same text share one reply: the reply key is a
short hash of the text (plus the salt of `aa`, a second independent writing of `base`, the noise floor).

Writes to the work folder: plan.json (per configuration and turn the reply key, the ids of the memories given and those in front of the model),
handovers.json (key -> what the writer is shown), memtext.json ("world/id" -> memory text, for the judge's sheets) and
prints the cost table: per configuration the turns, the turns whose hand-over differs from `base`, the distinct replies
to write and the pairs to judge. Nothing is generated here.

The "more memories" line: the lab's lookup has no count of what the gateway did not return, so the line is only counted
for what `noise:cap` cuts (the plugin adds the cut to it); it is zero otherwise. `today` of a turn is --today plus the
`gap_days` of the new conversations before it in the session (the plugin's showAge is on by default).

--sessions-per-world N keeps N sessions of every world (default: all): the N whose seeded hash of world name and session id
is smallest (--seed, default the seed of the toolkit), in their own order, so the same sessions are chosen on every run and
for every configuration; the cost table says how many sessions and turns the sample has (also in plan.json, "sample").

A split folder named `test` needs --final, as in session_lab.py; on it this script prints counts only (it prints no
memory, message or reply text on any split). Deterministic.
"""
import argparse
import datetime
import importlib.util
import json
import math
import os
import subprocess
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

# The configurations, by name: the session_lab spec (see its module docstring for the syntax), the tone of the main block
# and a salt (only `aa`: the same hand-over written again, so that its key differs). Add a line to add a configuration.
CONFIGS = {
    "base": {"spec": "product", "tone": "normal", "salt": ""},
    "after": {"spec": "after:max=2", "tone": "normal", "salt": ""},
    "recent": {"spec": "recent:size=3", "tone": "normal", "salt": ""},
    "cap3": {"spec": "noise:cap=3", "tone": "normal", "salt": ""},
    "strict": {"spec": "product", "tone": "strict", "salt": ""},
    "combo": {"spec": "after:max=2;recent:size=3;noise:cap=3", "tone": "normal", "salt": ""},
    "combo-strict": {"spec": "after:max=2;recent:size=3;noise:cap=3", "tone": "strict", "salt": ""},
    "aa": {"spec": "product", "tone": "normal", "salt": "aa"},
}
DEFAULT_TODAY = "2026-10-01"  # after the newest memory of the dev worlds
LANGUAGE = "zh"


def parse_names(text):
    """The configuration names of a comma list; `base` is always first. SystemExit on an unknown name."""
    names = [n.strip() for n in (text or ",".join(CONFIGS)).split(",") if n.strip()]
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        raise SystemExit("unknown configuration %s (known: %s)" % (", ".join(unknown), ", ".join(CONFIGS)))
    return [shared.BASE] + [n for n in dict.fromkeys(names) if n != shared.BASE]


def sample_sessions(worlds, per_world=None, seed=shared.SEED):
    """The worlds with at most `per_world` of their sessions (all when it is None): the ones with the smallest hash of
    seed, world name and session id, kept in the order of the world file. The choice depends on nothing else, so it is the
    same on every run and for every configuration. The worlds are copied, not changed."""
    if per_world is None:
        return list(worlds)
    sampled = []
    for world in worlds:
        sessions = world.get("sessions") or []
        ranked = sorted(sessions, key=lambda s: shared.digest("%d|%s|%s" % (seed, world["world"], s["id"])))
        chosen = {s["id"] for s in ranked[:per_world]}
        sampled.append({**world, "sessions": [s for s in sessions if s["id"] in chosen]})
    return sampled


def days_of(session):
    """{turn number: days after the start of the session} (a new conversation adds its gap_days)."""
    out, day = {}, 0
    for number, turn in enumerate(session.get("turns") or [], 1):
        if turn.get("new_conversation"):
            day += int(turn.get("gap_days") or 0)
        out[number] = day
    return out


def today_of(start, day):
    """The date `day` days after `start` ("YYYY-MM-DD")."""
    return (datetime.date.fromisoformat(start) + datetime.timedelta(days=day)).isoformat()


def cut_by_cap(engine, record, noise, position):
    """How many new memories of the main lookup `noise:cap` left out at this turn (the plugin's "more" grows by it): the
    uncapped part is taken from session_lab's own function. `record` must have been made with trace=True."""
    if not noise or noise.get("cap") is None or not record["query"]:
        return 0
    lab = shared.session_lab()
    arrived = {m["id"] for m in record["main"]} | {m["id"] for m in record["extra"]}
    before = {mid: None for mid in record["shown_ids"] if mid not in arrived}
    uncapped = lab.noise_lab.keep_session_part(engine.found(record["query"], noise), before, {**noise, "cap": None}, position)
    return max(0, len(uncapped) - len(record["main"]))


def replay(engines, config, replies, start):
    """[turn entry] for every turn of every session under one parsed config, in the order of the engines and sessions.
    An entry holds the labels of the turn, the hand-over without blocks, the memories given as {text, date} lists, and where
    the earlier turns of the same conversation are in the list: `earlier` (the turns with a user line, the ones whose
    messages are in `history`, in order) and `earlier_all` (every earlier turn): their blocks stay in the conversation."""
    lab = shared.session_lab()
    entries = []
    for engine in engines:
        for session in engine.sessions:
            records = lab.replay_session(engine, session, config, replies, trace=True)
            days = days_of(session)
            conversation_all, conversation_user = [], []
            for record, (number, turn, history, _, _, position) in zip(records, lab.walk_turns(session)):
                memories = engine.world["memories"]
                if position == 1:  # a new conversation: the earlier ones are gone with their blocks
                    conversation_all, conversation_user = [], []

                def given(ids):
                    return [{"id": mid, "text": engine.text_of[mid], "date": memories[mid].get("date")} for mid in ids]

                entries.append({
                    "world": engine.name, "session": session["id"], "turn": number, "kind": record["kind"],
                    "direct": record["direct"], "also_ok": record["also_ok"],
                    "persona": engine.world.get("persona") or "",
                    "history": [dict(h) for h in history], "said": turn.get("user") or "",
                    "today": today_of(start, days[number]),
                    "main": given([m["id"] for m in record["main"]]),
                    "extra": given([m["id"] for m in record["extra"]]),
                    "related": {m["id"]: m["related"] for m in record["main"] + record["extra"]},
                    "more": cut_by_cap(engine, record, config["noise"], position),
                    "earlier": list(conversation_user), "earlier_all": list(conversation_all),
                    "related_ids": set(record["direct"]) | set(record["also_ok"]) | engine.members(record["cluster"]),
                })
                conversation_all.append(len(entries) - 1)
                if turn.get("user"):
                    conversation_user.append(len(entries) - 1)
    return entries


def run_node(requests, node, plugin=None):
    """{id: {main, extra}} from handover.mjs for a list of requests (SystemExit when node is missing or fails)."""
    command = [node, os.path.join(_HERE, "handover.mjs")] + (["--plugin", plugin] if plugin else [])
    try:
        done = subprocess.run(command, input=json.dumps(requests, ensure_ascii=False).encode("utf-8"),
                              capture_output=True, check=False)
    except OSError as error:
        raise SystemExit("cannot run %s (%s): the block wording comes from handover.mjs" % (node, error))
    if done.returncode != 0:
        raise SystemExit("handover.mjs failed: %s" % done.stderr.decode("utf-8", "replace").strip()[:500])
    return json.loads(done.stdout.decode("utf-8"))


def block_notes(blocks):
    """The block texts of one turn in the order they are given (main block, then the second one)."""
    return [text for text in (blocks["main"], blocks["extra"]) if text]


def build_handover(entry, blocks, earlier=()):
    """The hand-over of a turn entry: what a writer is shown (persona, history, said, notes in the order of the blocks).
    Every user message of the history carries, as `notes`, the blocks given at its turn under the same configuration (left
    out when there were none): `earlier` is the block texts of the entry's earlier turns with a user line, in order. A block
    stays in the conversation, so the writer sees the earlier ones as the model would."""
    history = [dict(h) for h in entry["history"]]
    users = [h for h in history if h["role"] == "user"]
    if len(users) != len(earlier):
        raise SystemExit("history of %s/%s turn %d does not match its earlier turns" % (
            entry["world"], entry["session"], entry["turn"]))
    for message, past in zip(users, earlier):
        notes = block_notes(past)
        if notes:
            message["notes"] = notes
    return {"persona": entry["persona"], "history": history, "said": entry["said"], "notes": block_notes(blocks)}


def in_front(entries, entry):
    """[{id, related}] of the memories in front of the model at this turn: the ones given at this turn and in the earlier
    blocks of this conversation, each once, `related` by the labels of THIS turn (direct, also_ok or its cluster)."""
    ids = []
    for index in entry["earlier_all"]:
        ids.extend(m["id"] for block in ("main", "extra") for m in entries[index][block])
    ids.extend(m["id"] for block in ("main", "extra") for m in entry[block])
    return [{"id": mid, "related": mid in entry["related_ids"]} for mid in dict.fromkeys(ids)]


def plan_configs(engines, names, replies, start, node="node", plugin=None):
    """(plan, handovers, memtext): the plan.json document (without the split path), {key: hand-over} and the memory texts."""
    lab = shared.session_lab()
    replayed = {}
    requests = []
    for name in names:
        entries = replay(engines, lab.parse_spec(CONFIGS[name]["spec"]), replies, start)
        replayed[name] = entries
        for index, entry in enumerate(entries):
            requests.append({"id": "%s/%d" % (name, index), "language": LANGUAGE, "today": entry["today"],
                             "tone": CONFIGS[name]["tone"], "more": entry["more"],
                             "main": [{"text": m["text"], "date": m["date"]} for m in entry["main"]],
                             "extra": [{"text": m["text"], "date": m["date"]} for m in entry["extra"]]})
    blocks = run_node(requests, node, plugin)
    handovers, owners = {}, {}
    plan = {"configs": {}, "turns": []}
    for name in names:
        rows = []
        for index, entry in enumerate(replayed[name]):
            handover = build_handover(entry, blocks["%s/%d" % (name, index)],
                                      [blocks["%s/%d" % (name, j)] for j in entry["earlier"]])
            salt = CONFIGS[name]["salt"]
            key = shared.reply_key(handover, salt)
            text = shared.handover_text(handover) + salt
            if owners.setdefault(key, text) != text:
                raise SystemExit("two different hand-overs have the key %s: widen the key" % key)
            handovers[key] = handover
            rows.append({"key": key, "text_key": shared.reply_key(handover),
                         "given": [{"id": m["id"], "block": block, "related": entry["related"][m["id"]]}
                                   for block in ("main", "extra") for m in entry[block]],
                         "front": in_front(replayed[name], entry)})
        plan["configs"][name] = {"spec": CONFIGS[name]["spec"], "tone": CONFIGS[name]["tone"],
                                 "salt": CONFIGS[name]["salt"], "turns": rows}
        if not plan["turns"]:
            plan["turns"] = [{k: e[k] for k in ("world", "session", "turn", "kind", "direct", "also_ok")}
                             for e in replayed[name]]
    memtext = {"%s/%s" % (e.name, mid): text for e in engines for mid, text in e.text_of.items()}
    return plan, handovers, memtext


def cost_rows(plan):
    """The rows of the cost table: per configuration its turns, the turns whose hand-over differs from base, the distinct
    replies it needs, the replies it adds to those of the configurations above it, and the pairs to judge. Then the
    totals (distinct across all, pairs after sharing, the swapped repeats and the number of files)."""
    base = plan["configs"][shared.BASE]["turns"]
    seen = set(row["key"] for row in base)
    rows, all_pairs = [], set()
    for name, config in plan["configs"].items():
        turns = config["turns"]
        differs = sum(1 for a, b in zip(base, turns) if a["text_key"] != b["text_key"])
        own = {row["key"] for row in turns}
        added = own - seen
        seen |= own
        pairs = {shared.pair_id(a["key"], b["key"]) for a, b in zip(base, turns) if a["key"] != b["key"]}
        all_pairs |= pairs if name != shared.BASE else set()
        rows.append([name, len(turns), differs, len(own), len(added), len(pairs)])
    repeats = sum(1 for pid in all_pairs if shared.repeat_selected(pid))
    totals = {"replies": len(seen), "reply_files": math.ceil(len(seen) / shared.REPLY_ITEMS), "pairs": len(all_pairs),
              "repeats": repeats, "judge_files": math.ceil((len(all_pairs) + repeats) / shared.JUDGE_ITEMS)}
    return rows, totals


def table_lines(rows, totals, sample=None):
    """The cost table as Markdown lines; `sample` ({"worlds", "sessions", "turns", "per_world"}) adds what the table covers."""
    header = ["configuration", "turns", "hand-over differs from base", "distinct replies", "added to those above",
              "pairs to judge"]
    lines = []
    if sample:
        lines += ["sample: %(sessions)d sessions and %(turns)d turns in %(worlds)d worlds (%(how)s)" % {
            **sample, "how": "all sessions" if sample["per_world"] is None else "%d per world" % sample["per_world"]}, ""]
    lines += ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    lines += ["",
              "replies to write in all: %(replies)d (%(reply_files)d files of %(per)d)" % {**totals, "per": shared.REPLY_ITEMS},
              "pairs to judge after sharing: %(pairs)d, plus %(repeats)d swapped repeats (%(judge_files)d judge files of %(per)d)"
              % {**totals, "per": shared.JUDGE_ITEMS}]
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--split", required=True, help="folder with the world files (a folder named test needs --final)")
    parser.add_argument("--cache", required=True, help="vector cache of vector_lab (never fetched into here)")
    parser.add_argument("--texts", action="append", default=[], metavar="FILE", help="texts file of kind reply (scripted-session replies)")
    parser.add_argument("--work", required=True, help="work folder for the outputs")
    parser.add_argument("--configs", help="comma list of %s (base is always included)" % ", ".join(CONFIGS))
    parser.add_argument("--model", default=None, help="embedding model name of the cache (default: session_lab's)")
    parser.add_argument("--today", default=DEFAULT_TODAY, help="the date of the start of a session (default %s)" % DEFAULT_TODAY)
    parser.add_argument("--final", action="store_true", help="allow the split named 'test' (counts only are printed)")
    parser.add_argument("--sessions-per-world", type=int, default=None, metavar="N",
                        help="keep N sessions of every world, chosen by a seeded hash of the session id (default: all)")
    parser.add_argument("--seed", type=int, default=shared.SEED, help="seed of that choice (default %d)" % shared.SEED)
    parser.add_argument("--node", default="node", help="the node executable")
    parser.add_argument("--plugin", help="the plugin folder (default: plugins/dsh-feiyu-memgate of this repository)")
    args = parser.parse_args(argv)

    if args.sessions_per_world is not None and args.sessions_per_world < 1:
        raise SystemExit("--sessions-per-world must be at least 1")
    names = parse_names(args.configs)
    lab_module = shared.session_lab()
    lab, model_lab = lab_module.lab, lab_module.model_lab
    lab.refuse_test(args.split, args.final)
    datetime.date.fromisoformat(args.today)
    texts = model_lab.load_texts(args.texts)
    configs = [lab_module.parse_spec(CONFIGS[n]["spec"]) for n in names]
    lab_module.check_sources(configs, texts)
    model = args.model or lab.DEFAULT_MODEL
    scale = lab.Scale.for_model(model)
    worlds = [w for w in lab.load_worlds(args.split, args.final) if w.get("sessions")]
    if not worlds:
        raise SystemExit("no world of this split has sessions")
    worlds = sample_sessions(worlds, args.sessions_per_world, args.seed)
    cache = lab.load_cache(args.cache, model)
    missing = [s for s in lab_module.needed_strings(worlds, scale, texts, configs) if s not in cache["vectors"]]
    if missing:
        raise SystemExit("%d vectors are missing from the cache (this script never fetches them)" % len(missing))
    engines = lab_module.build_engines(worlds, cache, scale)
    plan, handovers, memtext = plan_configs(engines, names, texts.get(lab_module.REPLY_KIND, {}), args.today, args.node, args.plugin)
    plan["split"] = os.path.basename(os.path.normpath(args.split))
    plan["today"] = args.today
    sessions = sum(len(e.sessions) for e in engines)
    plan["sample"] = {"worlds": len(engines), "sessions": sessions, "turns": len(plan["turns"]),
                      "per_world": args.sessions_per_world, "seed": args.seed}
    shared.write_json(os.path.join(args.work, "plan.json"), plan)
    shared.write_json(os.path.join(args.work, "handovers.json"), handovers)
    shared.write_json(os.path.join(args.work, "memtext.json"), memtext)
    rows, totals = cost_rows(plan)
    shared.emit("split %s: %d worlds, %d sessions, %d turns; work folder %s" % (
        plan["split"], len(engines), sessions, len(plan["turns"]), args.work))
    shared.emit("")
    for line in table_lines(rows, totals, plan["sample"]):
        shared.emit(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
