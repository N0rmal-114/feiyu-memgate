"""Step 8 of the reply-quality evaluation: score every configuration against base from the judgements.

    python -B bench/chat/replies/score.py --work <work folder>

Reads work/plan.json, replies.json, judgements.json and pairs.json (made by plan.py, merge.py and prep.py judge) and prints a
Markdown report, also written as work/score.json. Per configuration against `base`:

- the turns whose hand-over equals base's (`identical`: one reply, counted as `same`, reported apart and left out of every
  rate), the turns judged, the turns not judged yet; of the judged ones the wins (the configuration's reply preferred), the
  losses (base's preferred) and `same`, and the exact two-sided sign test of wins against losses (session_lab's);
- the session-level sign test next to it: turns of one session are not independent, so per session (world and session id) the
  net result (wins minus losses over its judged turns) is taken, and the exact two-sided sign test runs over the sessions
  with a non-zero net (sessions for, against, tied); it is the stricter test and the report says so;
- the rates of `u` (use of something in `relevant` or `related_given`; turns where either is not empty for that pair, as
  pairs.json records; and `u` over turns that have a `direct` memory, the narrower definition), `d` (brought up something
  that does not belong; turns where something unrelated was in front of the configuration's side, this turn or in an
  earlier block of the conversation, with base's rate on the same turns, and also over all judged turns) and `w` (contradiction or invention; all judged turns), for the configuration and for base on the same
  turns, with the paired difference and how many turns went up and down;
- the same split by the kind of turn (open, named, continue, idle, return, nextday).

For `aa` (the same hand-over written twice) it adds the noise floor: the share of pairs not judged `same` and the share
of pairs where a flag differs. For the pairs shown a second time with the order swapped: how often the preference stayed with
the same reply, and whether `a` is preferred more often than `b` over everything judged (position bias, with a sign test).
A turn is counted once per configuration even when its pair is shared with another configuration. Prints no texts.
"""
import argparse
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
KIND_ORDER = ("open", "named", "continue", "idle", "return", "nextday")
NOISE = "aa"


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


def parse_all(judgements):
    """{key: parsed judgement} for the judgement strings that parse."""
    out = {}
    for key, text in judgements.items():
        parsed = shared.parse_judgement(text)
        if parsed:
            out[key] = parsed
    return out


def outcome(parsed, base_on):
    """Who a judgement prefers, "base", "config" or "same", and the flags per side as {"config": flags, "base": flags}, for a
    judgement of a pair whose base reply was shown on side `base_on` ("a" or "b")."""
    other = shared.other_side(base_on)
    better = parsed["better"]
    who = "same" if better == "same" else ("base" if better == base_on else "config")
    return who, {"base": parsed[base_on], "config": parsed[other]}


def turn_rows(plan, parsed, pairs, name):
    """One row per turn for configuration `name`: {index, kind, identical, judged, who, flags, has_direct, unrelated} where
    `unrelated` tells that something unrelated was given to the configuration's side (and base's: `unrelated_base`)."""
    base = plan["configs"][shared.BASE]["turns"]
    rows = []
    for index, (b, c) in enumerate(zip(base, plan["configs"][name]["turns"])):
        turn = plan["turns"][index]
        row = {"index": index, "kind": turn["kind"] or "unlabelled", "identical": b["key"] == c["key"], "judged": False,
               "who": None, "flags": None, "has_direct": bool(turn["direct"]), "has_u": bool(turn["direct"]),
               "session": "%s/%s" % (turn["world"], turn["session"]),
               "unrelated": any(not g["related"] for g in shared.front_of(c)),
               "unrelated_base": any(not g["related"] for g in shared.front_of(b))}
        if not row["identical"]:
            pid = shared.pair_id(b["key"], c["key"])
            if pid in parsed and pid in pairs:
                row["judged"] = True
                row["has_u"] = pairs[pid].get("has_u", row["has_u"])
                row["who"], row["flags"] = outcome(parsed[pid], pairs[pid]["base"])
        rows.append(row)
    return rows


def _mean(values):
    return sum(values) / len(values) if values else None


def _paired(rows, flag, select):
    """{n, config, base, diff, up, down} of a flag over the judged rows `select` picks (a pair of values per turn)."""
    chosen = [r for r in rows if r["judged"] and select(r) and r["flags"]["config"][flag] is not None
              and r["flags"]["base"][flag] is not None]
    mine = [r["flags"]["config"][flag] for r in chosen]
    theirs = [r["flags"]["base"][flag] for r in chosen]
    return {"n": len(chosen), "config": _mean(mine), "base": _mean(theirs),
            "diff": None if not chosen else _mean(mine) - _mean(theirs),
            "up": sum(1 for a, b in zip(mine, theirs) if a > b), "down": sum(1 for a, b in zip(mine, theirs) if a < b)}


def session_test(rows):
    """The sign test over sessions: per session (world and session id) the net result, wins minus losses over its judged
    turns; {"for", "against", "tied", "p"} counts the sessions with a positive, a negative and a zero net, and p is the exact
    two-sided sign test over those with a non-zero net. Turns of one session are not independent, so this is the stricter
    test."""
    net = {}
    for r in rows:
        if r["judged"]:
            net[r["session"]] = net.get(r["session"], 0) + (r["who"] == "config") - (r["who"] == "base")
    up = sum(1 for v in net.values() if v > 0)
    down = sum(1 for v in net.values() if v < 0)
    return {"sessions": len(net), "for": up, "against": down, "tied": len(net) - up - down,
            "p": shared.sign_test(up, down)}


def summarize(rows):
    """The numbers of one group of turn rows (see the module docstring)."""
    judged = [r for r in rows if r["judged"]]
    wins = sum(1 for r in judged if r["who"] == "config")
    losses = sum(1 for r in judged if r["who"] == "base")
    return {"turns": len(rows), "identical": sum(1 for r in rows if r["identical"]),
            "judged": len(judged), "not_judged": sum(1 for r in rows if not r["identical"] and not r["judged"]),
            "wins": wins, "losses": losses, "same": sum(1 for r in judged if r["who"] == "same"),
            "p": shared.sign_test(wins, losses),
            "sessions": session_test(rows),
            "u": _paired(rows, "u", lambda r: r["has_u"]),
            "u_direct": _paired(rows, "u", lambda r: r["has_direct"]),
            "d_given": _paired(rows, "d", lambda r: r["unrelated"]),
            "d_all": _paired(rows, "d", lambda r: True),
            "w": _paired(rows, "w", lambda r: True)}


def noise_floor(rows):
    """For the configuration that repeats base: the share of judged pairs not judged `same`, and the share where each flag
    differs between the two replies."""
    judged = [r for r in rows if r["judged"]]
    out = {"judged": len(judged), "not_same": _mean([int(r["who"] != "same") for r in judged])}
    for flag in ("u", "d", "w"):
        both = [r for r in judged if r["flags"]["config"][flag] is not None and r["flags"]["base"][flag] is not None]
        out[flag] = _mean([int(r["flags"]["config"][flag] != r["flags"]["base"][flag]) for r in both])
    return out


def consistency(parsed, pairs):
    """The swapped repeats: {"repeats": how many have both showings judged, "kept": how often the preference stayed with the
    same reply (base, configuration or same), "a": preferences for `a`, "b": for `b` over every judged item, "p": sign test
    of a against b}."""
    kept = repeats = a = b = 0
    for key, item in pairs.items():
        if key not in parsed:
            continue
        better = parsed[key]["better"]
        a += better == "a"
        b += better == "b"
        first = item.get("repeat_of")
        if first and first in parsed and first in pairs:
            repeats += 1
            kept += outcome(parsed[first], pairs[first]["base"])[0] == outcome(parsed[key], item["base"])[0]
    return {"repeats": repeats, "kept": kept, "kept_share": kept / repeats if repeats else None, "a": a, "b": b,
            "a_share": a / (a + b) if a + b else None, "p": shared.sign_test(a, b)}


def score(plan, judgements, pairs):
    """The whole result: per configuration its summary and its summary per kind, the noise floor of `aa` and the consistency."""
    parsed = parse_all(judgements)
    configs = {}
    for name in plan["configs"]:
        if name == shared.BASE:
            continue
        rows = turn_rows(plan, parsed, pairs, name)
        by_kind = {}
        for kind in KIND_ORDER + tuple(sorted({r["kind"] for r in rows} - set(KIND_ORDER))):
            group = [r for r in rows if r["kind"] == kind]
            if group:
                by_kind[kind] = summarize(group)
        configs[name] = {"all": summarize(rows), "kinds": by_kind}
        if name == NOISE:
            configs[name]["noise"] = noise_floor(rows)
    return {"configs": configs, "consistency": consistency(parsed, pairs)}


# ------------------------------------------------------------------ report

def _pct(value):
    return "-" if value is None else "%.1f" % (value * 100)


def _diff(value):
    return "-" if value is None else "%+.1f" % (value * 100)


def _table(header, rows):
    return ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"] + [
        "| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]


def _flag_cells(paired):
    return [paired["n"], _pct(paired["config"]), _pct(paired["base"]), _diff(paired["diff"]),
            "%d/%d" % (paired["up"], paired["down"])]


def report_lines(result):
    """The Markdown report of a `score` result."""
    lines = ["# Reply quality against base", "",
             "Pairs: wins are turns where the configuration's reply was preferred. Identical hand-overs share one reply and are "
             "counted as same, apart from the judged ones. Rates are percent; difference is configuration minus base on the "
             "same turns; up/down counts the turns where the flag rose or fell.",
             "", "Two sign tests are given. The per-turn test counts every judged turn as one trial. Turns of one session are "
             "not independent, so the session-level test (the net result of each session, wins minus losses, over the sessions "
             "that are not tied) is the stricter one: trust a result only as far as that one carries it.", ""]
    head = ["configuration", "turns", "identical", "judged", "not judged yet", "wins", "losses", "same", "p (turns)",
            "sessions judged", "sessions for", "against", "tied", "p (sessions, stricter)"]
    lines += _table(head, [[n, c["all"]["turns"], c["all"]["identical"], c["all"]["judged"], c["all"]["not_judged"],
                            c["all"]["wins"], c["all"]["losses"], c["all"]["same"], "%.4f" % c["all"]["p"],
                            c["all"]["sessions"]["sessions"], c["all"]["sessions"]["for"], c["all"]["sessions"]["against"],
                            c["all"]["sessions"]["tied"], "%.4f" % c["all"]["sessions"]["p"]]
                           for n, c in result["configs"].items()])
    flags = [("u", "u: used something relevant or related that was in front (turns where relevant or related_given is not empty)"),
             ("u_direct", "u: the same over turns that have a direct memory (the narrower definition)"),
             ("d_given", "d: brought up something that does not belong (turns where something unrelated was in front of the "
                         "configuration's side)"),
             ("d_all", "d: the same over all judged turns"), ("w", "w: contradicted or invented (all judged turns)")]
    for flag, title in flags:
        lines += ["", "## " + title, ""]
        lines += _table(["configuration", "turns", "configuration %", "base %", "difference (points)", "up/down"],
                        [[n] + _flag_cells(c["all"][flag]) for n, c in result["configs"].items()])
    lines += ["", "## By kind of turn", ""]
    rows = []
    for name, c in result["configs"].items():
        for kind, s in c["kinds"].items():
            rows.append([name, kind, s["turns"], s["identical"], s["judged"], s["wins"], s["losses"], s["same"],
                         "%.4f" % s["p"], "%d/%d/%d" % (s["sessions"]["for"], s["sessions"]["against"], s["sessions"]["tied"]),
                         "%.4f" % s["sessions"]["p"], _diff(s["u"]["diff"]), _diff(s["u_direct"]["diff"]),
                         _diff(s["d_given"]["diff"]), _diff(s["w"]["diff"])])
    lines += _table(["configuration", "kind", "turns", "identical", "judged", "wins", "losses", "same", "p",
                     "sessions for/against/tied", "p (sessions)", "u diff", "u diff (direct)", "d diff (in front)", "w diff"],
                    rows)
    noise = result["configs"].get(NOISE, {}).get("noise")
    if noise:
        lines += ["", "## Noise floor (%s: base written twice)" % NOISE, "",
                  *_table(["judged pairs", "not judged same %", "u differs %", "d differs %", "w differs %"],
                          [[noise["judged"], _pct(noise["not_same"]), _pct(noise["u"]), _pct(noise["d"]), _pct(noise["w"])]])]
    c = result["consistency"]
    lines += ["", "## Judge consistency and position", "",
              *_table(["swapped repeats judged", "preference kept", "kept %", "a preferred", "b preferred", "a share %",
                       "p (a against b)"],
                      [[c["repeats"], c["kept"], _pct(c["kept_share"]), c["a"], c["b"], _pct(c["a_share"]), "%.4f" % c["p"]]])]
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--work", required=True, help="the work folder of plan.py")
    args = parser.parse_args(argv)
    for name, step in (("plan.json", "plan.py"), ("judgements.json", "merge.py"), ("pairs.json", "prep.py judge")):
        if not os.path.isfile(os.path.join(args.work, name)):
            raise SystemExit("%s is not in %s: run %s first" % (name, args.work, step))
    plan = shared.read_json(os.path.join(args.work, "plan.json"))
    judgements = shared.read_json(os.path.join(args.work, "judgements.json"))
    pairs = shared.read_json(os.path.join(args.work, "pairs.json"))
    result = score(plan, judgements, pairs)
    shared.write_json(os.path.join(args.work, "score.json"), result)
    for line in report_lines(result):
        shared.emit(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
