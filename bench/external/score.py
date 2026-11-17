"""Metrics of the external runs, from the per-question results files and the stores they were run on.

    python bench/external/score.py [--dataset locomo ...] [--results DIR] [--worlds-root DIR]

Reads bench/external/results/<dataset>/<system>.json (ids and numbers only; kept gzip-compressed in the repository,
see pack.py) and the stores as they were run (for the evidence, the memory lengths and the question types), and writes
<system>.metrics.json next to each results file and tables.md per dataset (numbers only). The stores hold the text of
the datasets, which is licensed CC BY-NC and is not in the repository, so this script documents how every figure is
computed and can be run only where the datasets have been prepared; report.py and charts.py need the metrics files
only. Definitions, as in bench/score.py where there is one:

  answerable question   has at least one resolved evidence memory and is not a no-answer question
  Hit@k                 at least one evidence memory among the first k of `ranked` (k = 1, 5, 10)
  evidence@10           for questions with two or more evidence memories: the share of them in the top 10
  session Hit@k         (LongMemEval) a retrieved memory belongs to one of the answer sessions
  delivered hit         at least one evidence memory among the delivered memories (`deployed`)
  precision             of the delivered memories, the share that is evidence, averaged over the questions that
                        delivered anything (n given); an item without an id counts as not evidence
  items, chars          delivered memories and their characters per question (an item without an id counts with
                        the mean memory length of its world, as bench/score.py does; an item the adapter records
                        as delivered shortened counts with its recorded length)
  quiet rate            on the no-answer questions: the share where nothing was delivered
  p50 ms                nearest-rank median of the Gateway.recall call
  over-budget           answerable questions with an evidence memory longer than the gateway's 1500-character output
                        budget (it can never be delivered), and those whose evidence is all longer than that
Every figure is reported with the number of questions behind it.
"""
import argparse
import collections
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

BUDGET_CHARS = 1500
RESULTS = os.path.join(HERE, "results")
WORLDS = os.path.join(HERE, "data", "worlds")  # <dataset>/<store>/: the stores as they were run (not in the repository)
_spec = importlib.util.spec_from_file_location("ext_bench_score", os.path.join(ROOT, "bench", "score.py"))
bench_score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench_score)


def read_world(directory):
    """(memories, queries, side) of one store: memories.json and queries.json in the format of bench/data, and
    external.json with, per question, its type, whether it has no answer, and (LongMemEval) its answer sessions."""
    def load(name):
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            return json.load(f)
    return load("memories.json"), load("queries.json"), load("external.json")


def mean(values):
    return sum(values) / len(values) if values else None


def ids_of(items):
    return [i["id"] for i in items]


def chars_of(items, memories, fill):
    """Characters of the delivered items: the adapter's own `chars` when it records one (an item it delivered
    shortened), else the memory's length, else (no id) the mean memory length of the world."""
    total = 0
    for item in items:
        if isinstance(item.get("chars"), (int, float)):
            total += item["chars"]
        else:
            total += len(memories[item["id"]]["text"]) if item["id"] is not None else fill
    return total


def question_figures(ans, q, memories, session_of, answer_sessions):
    """The per-question figures of one answerable question."""
    expect = set(q["expect"])
    ranked, deployed = ids_of(ans["ranked"]), ids_of(ans["deployed"])
    fill = mean([len(m["text"]) for m in memories.values()]) if memories else 0
    out = {"ms": ans["ms"], "items": len(deployed), "chars": chars_of(ans["deployed"], memories, fill)}
    for k in (1, 5, 10):
        out["hit%d" % k] = 1.0 if expect & set(ranked[:k]) else 0.0
    if len(expect) >= 2:
        out["evidence10"] = len(expect & set(ranked[:10])) / len(expect)
    out["delivered_hit"] = 1.0 if expect & set(deployed) else 0.0
    if deployed:
        out["precision"] = sum(1 for i in deployed if i in expect) / len(deployed)
    if answer_sessions:
        wanted = set(answer_sessions)
        for k in (1, 5, 10):
            out["session_hit%d" % k] = 1.0 if wanted & {session_of.get(i) for i in ranked[:k]} else 0.0
        out["session_delivered_hit"] = 1.0 if wanted & {session_of.get(i) for i in deployed} else 0.0
    big = [len(memories[e]["text"]) > BUDGET_CHARS for e in q["expect"]]
    out["any_over_budget"], out["all_over_budget"] = float(any(big)), float(all(big))
    return out


def summarize(rows):
    """Aggregate per-question figures (a list of dicts) into one metrics dict, with the n of every figure."""
    out = {"questions": len(rows)}
    for key in ("hit1", "hit5", "hit10", "delivered_hit", "items", "chars", "session_hit1", "session_hit5",
                "session_hit10", "session_delivered_hit", "evidence10", "precision"):
        values = [r[key] for r in rows if key in r]
        if values:
            out[key] = mean(values)
            if len(values) != len(rows):
                out[key + "_n"] = len(values)
    for key in ("any_over_budget", "all_over_budget"):
        values = [r[key] for r in rows if key in r]
        if values:
            out[key.replace("_over_budget", "_over_budget_questions")] = int(sum(values))
    out["p50_ms"] = bench_score.percentile([r["ms"] for r in rows], 50)
    return out


def load_subset_ids(path):
    with open(path, encoding="utf-8") as f:
        return set(json.load(f)["questions"])


def usage_of(worlds, whole):
    """The usage record of a selection of stores: the numeric fields summed over them, the names kept from `whole`."""
    out = {k: v for k, v in whole.items() if isinstance(v, str)}
    for w in worlds:
        for k, v in w["usage"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = round(out.get(k, 0) + v, 6)
    return out


def score_file(path, root, only=None, label=None):
    """Metrics of one results file. `only`: a set of global question ids ('<world>/<qid>') to score (a subset file);
    `label` names that question set in the output."""
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    dataset = result["dataset"]
    if only is not None:
        result["answers"] = {g: a for g, a in result["answers"].items() if g in only}
        result["worlds"] = {w: v for w, v in result["worlds"].items() if any(g.startswith(w + "/") for g in result["answers"])}
        if all("usage" in v for v in result["worlds"].values()):  # the spending of these stores only, not of the file
            result["usage"] = usage_of(result["worlds"].values(), result["usage"])
    groups, cache = collections.defaultdict(list), {}
    quiet_rows, counts = [], collections.Counter()
    for gid, ans in result["answers"].items():
        wid, qid = gid.rsplit("/", 1)
        if wid not in cache:
            cache = {wid: read_world(os.path.join(root, dataset, wid))}  # one world at a time
        memories, queries, side = cache[wid]
        q = queries[qid]
        meta = side["questions"][qid]
        if meta["no_answer"]:
            fill = mean([len(m["text"]) for m in memories.values()])
            deployed = ids_of(ans["deployed"])
            quiet_rows.append({"ms": ans["ms"], "items": len(deployed), "quiet": 1.0 if not deployed else 0.0,
                               "chars": chars_of(ans["deployed"], memories, fill)})
            continue
        row = question_figures(ans, q, memories, side.get("sessions", {}), meta.get("answer_sessions"))
        groups[meta["type"]].append(row)
        groups["ALL answerable"].append(row)
        counts["answerable"] += 1
    metrics = {t: summarize(rows) for t, rows in sorted(groups.items())}
    if quiet_rows:
        metrics["no-answer"] = {"questions": len(quiet_rows), "quiet_rate": mean([r["quiet"] for r in quiet_rows]),
                                "items": mean([r["items"] for r in quiet_rows]),
                                "chars": mean([r["chars"] for r in quiet_rows]),
                                "p50_ms": bench_score.percentile([r["ms"] for r in quiet_rows], 50)}
    all_ms = [r["ms"] for rows in [groups.get("ALL answerable", []), quiet_rows] for r in rows]
    out = {"system": result["system"], "dataset": dataset, "profile": result["profile"],
           "question_set": label or (result.get("subset") or {}).get("file") or "all questions of the dataset",
           "subset_size": None if only is None else len(only),
           "llm_model": result.get("llm_model"), "llm_thinking": result.get("llm_thinking"),
           "embedding_model": result.get("embedding_model"),
           "stopped": result.get("stopped"), "subset": result.get("subset"), "worlds": len(result["worlds"]),
           "world_ids": sorted(result["worlds"]),  # the stores this scored set covers
           "memories": sum(w.get("memories") or 0 for w in result["worlds"].values()),
           "failed_worlds": sorted(result.get("failed_worlds") or {}),
           "questions": len(result["answers"]), "p50_ms_all": bench_score.percentile(all_ms, 50),
           "recall_not_200": sum(w["recall_not_200"] for w in result["worlds"].values()),
           "ranked_failed": sum(w["ranked_failed"] for w in result["worlds"].values()),
           "unmapped_items": sum(w["unmapped_items"] for w in result["worlds"].values()),
           "recall_notes": dict(sum((collections.Counter(w["recall_notes"]) for w in result["worlds"].values()),
                                    collections.Counter())),
           "usage": result["usage"], "metrics": metrics}
    return out


def only_for_stores(path, stores):
    """The question ids ('<store>/<qid>') of a results file that belong to `stores`, and the stores the file lacks."""
    with open(path, encoding="utf-8") as f:
        result = json.load(f)
    have = {g.rsplit("/", 1)[0] for g in result["answers"]}
    return {g for g in result["answers"] if g.rsplit("/", 1)[0] in set(stores)}, [w for w in stores if w not in have]


def compare_stores(folder, root, stores, label):
    """One table of every results file of `folder` that covers ALL of `stores`, scored on exactly those stores
    (written to <folder>/same-stores-<label>.md and .json). Files that miss a store are listed, not scored."""
    rows, skipped = [], {}
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json") or name.endswith(".metrics.json") or name.startswith(("latency", "same-stores")):
            continue
        only, missing = only_for_stores(os.path.join(folder, name), stores)
        if missing or not only:
            skipped[name[:-5]] = missing or list(stores)
            continue
        sc = score_file(os.path.join(folder, name), root, only, label)
        rows.append((sc["system"], sc))
    head = ("| system | stores | questions | Hit@1 | Hit@5 | Hit@10 | deliv. hit | precision | items | chars | quiet "
            "| LLM | calls | input tok | output tok | USD |\n" + "|---" * 16 + "|\n")
    lines = []
    for system, sc in rows:
        m = sc["metrics"].get("ALL answerable", {})
        q = sc["metrics"].get("no-answer", {})
        u = sc.get("usage") or {}
        paid = bool(sc.get("llm_model"))
        lines.append("| %s | %d | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            system, len(sc["world_ids"]), sc["questions"], pct(m.get("hit1")), pct(m.get("hit5")), pct(m.get("hit10")),
            pct(m.get("delivered_hit")), pct(m.get("precision")), num(m.get("items"), 2), num(m.get("chars"), 0),
            pct(q.get("quiet_rate")), sc.get("llm_model") or "-",
            "%d" % u.get("llm_calls", 0) if paid else "-", "%d" % u.get("llm_input_tokens", 0) if paid else "-",
            "%d" % u.get("llm_output_tokens", 0) if paid else "-", num(u.get("llm_cost_usd"), 3) if paid else "-"))
    text = "# %s: %s (%s)\n\n%s%s\n" % (os.path.basename(folder), label, ",".join(stores), head, "\n".join(lines))
    if skipped:
        text += "\nNot covering all of these stores (not scored): %s\n" % "; ".join(
            "%s (missing %s)" % (n, ",".join(w) if len(w) < 4 else "%d stores" % len(w)) for n, w in sorted(skipped.items()))
    with open(os.path.join(folder, "same-stores-%s.md" % label), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    with open(os.path.join(folder, "same-stores-%s.json" % label), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"stores": stores, "label": label, "systems": {n: sc for n, sc in rows}, "not_covering": skipped}, f, indent=1)
    return text


def pct(x):
    return "-" if x is None else "%.1f" % (100 * x)


def num(x, digits=1):
    return "-" if x is None else ("%.*f" % (digits, x))


def table(rows):
    """Markdown tables (answerable types, then the no-answer row) of one scored file."""
    head = ("| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|\n")
    lines = []
    for t, m in rows["metrics"].items():
        if t == "no-answer":
            continue
        ev = "%s (%d)" % (pct(m.get("evidence10")), m.get("evidence10_n", m["questions"])) if "evidence10" in m else "-"
        lines.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            t, m["questions"], pct(m["hit1"]), pct(m["hit5"]), pct(m["hit10"]), ev, pct(m["delivered_hit"]),
            pct(m.get("precision")), num(m["items"], 2), num(m["chars"], 0), num(m["p50_ms"])))
    text = head + "\n".join(lines) + "\n"
    if "no-answer" in rows["metrics"]:
        m = rows["metrics"]["no-answer"]
        text += "\nNo-answer questions: n %d, quiet rate %s %%, items %s, chars %s, p50 %s ms\n" % (
            m["questions"], pct(m["quiet_rate"]), num(m["items"], 2), num(m["chars"], 0), num(m["p50_ms"]))
    if "session_hit10" in rows["metrics"].get("ALL answerable", {}):
        m = rows["metrics"]["ALL answerable"]
        text += "\nSession-level (answerable, n %d): Hit@1 %s, Hit@5 %s, Hit@10 %s, delivered %s\n" % (
            m["questions"], pct(m["session_hit1"]), pct(m["session_hit5"]), pct(m["session_hit10"]),
            pct(m["session_delivered_hit"]))
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", action="append")
    parser.add_argument("--results", default=RESULTS)
    parser.add_argument("--worlds-root", default=WORLDS)
    parser.add_argument("--subset", action="append",
                        help="a subset file (question ids): also score every results file on that question set; "
                             "default: every file of bench/external/subsets/ for its dataset")
    parser.add_argument("--stores", help="comma-separated store (world) ids, or first:N (the first N stores in ascending "
                        "id order of the dataset): write one same-stores table of every results file that covers them all")
    parser.add_argument("--stores-label", default=None)
    args = parser.parse_args(argv)
    if args.stores:
        for dataset in args.dataset or []:
            folder = os.path.join(args.results, dataset)
            if args.stores.startswith("first:"):
                n = int(args.stores[6:])
                stores = sorted(d for d in os.listdir(os.path.join(args.worlds_root, dataset)) if not d.startswith("."))[:n]
            else:
                stores = args.stores.split(",")
            print(compare_stores(folder, args.worlds_root, stores, args.stores_label or "first%d" % len(stores)))
        return 0
    subset_dir = os.path.join(HERE, "subsets")
    for dataset in args.dataset or sorted(d for d in os.listdir(args.results)
                                          if os.path.isdir(os.path.join(args.results, d))):
        folder = os.path.join(args.results, dataset)
        subsets = args.subset or [os.path.join(subset_dir, n) for n in sorted(os.listdir(subset_dir))
                                  if n.startswith(dataset + "-")] if os.path.isdir(subset_dir) else []
        md = ["# %s\n" % dataset]
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json") or name.endswith(".metrics.json") or name.startswith(("latency", "same-stores")):
                continue
            path = os.path.join(folder, name)
            sets = [(None, None)] + [(load_subset_ids(f), os.path.basename(f)[:-5]) for f in subsets]
            for only, label in sets:
                scored = score_file(path, args.worlds_root, only, label)
                if only is not None and scored["questions"] == 0:
                    continue
                suffix = "" if only is None else "." + label
                with open(os.path.join(folder, name[:-5] + suffix + ".metrics.json"), "w", encoding="utf-8",
                          newline="\n") as f:
                    json.dump(scored, f, indent=1)
                    f.write("\n")
                note = ""
                if only is not None and scored["questions"] < len(only):
                    note = ", %d of the set's %d questions answered" % (scored["questions"], len(only))
                md.append("## %s | question set: %s (%d worlds, %d questions%s)\n\n%s" % (
                    scored["system"], scored["question_set"], scored["worlds"], scored["questions"], note,
                    table(scored)))
        lat = os.path.join(folder, "latency-summary.json")
        if os.path.exists(lat):
            with open(lat, encoding="utf-8") as f:
                figures = json.load(f)
            md.append("## Latency of Gateway.recall or the adapter's deployed call (ms)\n\n"
                      "| system | kind | world (memories) | questions | p50 | p95 |\n|---|---|---|---|---|---|\n"
                      + "\n".join("| %s | %s | %s | %d | %s | %s |" % (
                          n, v["kind"], "%s (%s)" % (v["world"], v["memories"]) if v["world"] else "the worlds it ran on",
                          v["questions"], num(v["p50_ms"]), num(v["p95_ms"])) for n, v in sorted(figures.items())))
        with open(os.path.join(folder, "tables.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(md))
        print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
