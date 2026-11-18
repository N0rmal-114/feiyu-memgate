"""Run a grid of settings over one split of the chat test set and print one comparison table.

    python -B bench/chat/sweep.py --split bench/chat/dev --backend sqlite --grid queries
    python -B bench/chat/sweep.py --split bench/chat/dev --backend hybrid --model bge-m3 --grid links --query v2
    python -B bench/chat/sweep.py --split bench/chat/dev --backend sqlite --grid my-grid.json --out sweep.json

Rules are chosen on the development split only: a split folder named `test` is refused unless --final is
given, and --final is for the one run after the rules are fixed.

--grid is the name of a built-in grid or a JSON file. A file holds a list; each entry is an object with an
optional "label" and any of the run.py settings (profile, limit, query, short, context_chars, remark_profile,
links, link_top, link_max, link_min_kw, link_min_vec, ranks). The flags of run.py that name a setting
(--query, --links, ...) are the base every entry is laid over, so `--grid links --query v2` compares the link
variants on top of v2. Built-in grids:

    queries   v0, then v1, v2 and v3 each with short 8, 12 and 16 (v0 has no `short`: it is the package's
              own build_query, which uses 8)
    links     none, then kw, vec and either, each at two minimums (kw 0.10 and 0.20, vec 0.45 and 0.60, either
              with the pairs 0.10/0.45 and 0.20/0.60); vec and either need --backend embedding or hybrid
    remarks   --remark-profile none against precise

Every world is written once and asked for every row, so a grid costs one embedding pass per world. The table
has one row per setting: rates are percent, items and noise are per case (definitions: run.py).
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name):
    """A module of this folder under the name bench_chat_<name> (see run.py for why it is not a plain import)."""
    key = "bench_chat_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, os.path.join(HERE, name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


run = _sibling("run")

FORBIDDEN_SPLIT = "test"


def grid_queries():
    rows = [("v0", {"query": "v0"})]
    for variant in ("v1", "v2", "v3"):
        for short in (8, 12, 16):
            rows.append(("%s short=%d" % (variant, short), {"query": variant, "short": short}))
    return rows


def grid_links():
    rows = [("links=none", {"links": "none"})]
    for minimum in (0.10, 0.20):
        rows.append(("links=kw min_kw=%.2f" % minimum, {"links": "kw", "link_min_kw": minimum}))
    for minimum in (0.45, 0.60):
        rows.append(("links=vec min_vec=%.2f" % minimum, {"links": "vec", "link_min_vec": minimum}))
    for kw, vec in ((0.10, 0.45), (0.20, 0.60)):
        rows.append(("links=either min_kw=%.2f min_vec=%.2f" % (kw, vec),
                     {"links": "either", "link_min_kw": kw, "link_min_vec": vec}))
    return rows


def grid_remarks():
    return [("remark-profile=none", {"remark_profile": "none"}),
            ("remark-profile=precise", {"remark_profile": "precise"})]


GRIDS = {"queries": grid_queries, "links": grid_links, "remarks": grid_remarks}


def load_grid(spec):
    """[(label, overrides)] from a built-in grid name or a JSON file."""
    if spec in GRIDS:
        return GRIDS[spec]()
    if not os.path.isfile(spec):
        raise SystemExit("--grid %r is neither a built-in grid (%s) nor a file" % (spec, ", ".join(GRIDS)))
    with open(spec, encoding="utf-8") as handle:
        entries = json.load(handle)
    if not isinstance(entries, list) or not entries or not all(isinstance(e, dict) for e in entries):
        raise SystemExit("%s must hold a non-empty list of objects" % spec)
    rows = []
    for number, entry in enumerate(entries, 1):
        entry = dict(entry)
        label = entry.pop("label", None) or ", ".join("%s=%s" % pair for pair in entry.items()) or "entry %d" % number
        rows.append((label, entry))
    return rows


def refuse_test(split, final):
    """SystemExit when `split` is the frozen test folder and --final was not given."""
    if os.path.basename(os.path.abspath(split)).lower() == FORBIDDEN_SPLIT and not final:
        raise SystemExit("refusing to sweep %s: the test split is not for choosing rules. Sweep the development "
                         "split; --final is for the single run after the rules are fixed." % split)


def format_table(rows):
    """rows: [(label, summary)] -> a Markdown table, one line per setting."""
    header = ["setting", "assoc direct_hit", "direct_recall", "chain_hit", "chain_recall", "precision", "items",
              "noise", "named direct_recall", "noise", "follow-up hit", "noise", "quiet_rate", "all items",
              "added", "chain", "noise"]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for label, summary in rows:
        overall = summary["overall"]
        assoc, named = overall.get("remark-assoc", {}), overall.get("remark-named", {})
        follow, quiet = overall.get("follow-up", {}), overall.get("quiet", {})
        everything = summary["all"]
        cells = [label,
                 run._cell("direct_hit", assoc.get("direct_hit")), run._cell("direct_recall", assoc.get("direct_recall")),
                 run._cell("chain_hit", assoc.get("chain_hit")), run._cell("chain_recall", assoc.get("chain_recall")),
                 run._cell("precision", assoc.get("precision")), run._cell("items", assoc.get("items")),
                 run._cell("noise", assoc.get("noise")),
                 run._cell("direct_recall", named.get("direct_recall")), run._cell("noise", named.get("noise")),
                 run._cell("hit", follow.get("hit")), run._cell("noise", follow.get("noise")),
                 run._cell("quiet_rate", quiet.get("quiet_rate")), run._cell("items", everything.get("items"))]
        if "added" in everything:
            cells += [str(everything["added"]), str(everything["added_chain"]), str(everything["added_noise"])]
        else:
            cells += ["-", "-", "-"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    run.add_store_arguments(parser)
    run.add_setting_arguments(parser)
    parser.add_argument("--grid", required=True, help="a built-in grid (%s) or a JSON file" % ", ".join(GRIDS))
    parser.add_argument("--final", action="store_true", help="allow the split named 'test' (one run, rules already fixed)")
    parser.add_argument("--out", metavar="JSON", help="write the settings and summaries of every row here")
    args = parser.parse_args(argv)
    refuse_test(args.split, args.final)
    base = run.settings_from_args(args)
    entries = load_grid(args.grid)
    settings_list = [run.merged_settings(base, overrides) for _, overrides in entries]
    try:
        results = run.run_split(args.split, args.backend, args.url, args.model, settings_list)
    except run.RunError as error:
        raise SystemExit("sweep stopped: %s" % error)
    rows = [(label, result["summary"]) for (label, _), result in zip(entries, results)]
    run.say(format_table(rows))
    if args.out:
        document = {"split": os.path.basename(os.path.normpath(args.split)), "backend": args.backend,
                    "rows": [{"label": label, "settings": result["settings"], "summary": result["summary"]}
                             for (label, _), result in zip(entries, results)]}
        run.write_json(args.out, document)
    return 0


if __name__ == "__main__":
    sys.exit(main())
