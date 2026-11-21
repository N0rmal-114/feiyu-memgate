"""Choose the defaults of the `newer_first` recall stage on the development set (bench/dev).

    python bench/experiments/newer_first/run.py collect --backend embedding --embed-url URL --out FILE
    python bench/experiments/newer_first/run.py report --cand sqlite=FILE --cand embedding=FILE ...
    python bench/experiments/newer_first/run.py report --collect-sqlite --cand embedding=FILE --cand hybrid=FILE

Rule (fixed before any number was looked at, see README.md): among the grid points whose "first item
correct" over all answerable questions is at most one question lower than with the stage off on EVERY
backend, take the one with the highest mean of (a) over the backends; ties go to the larger band, then to
related = 0. bench/data (the held-out set) is never read: bench/dev_eval.py refuses it and so does this.

Metrics, per backend and grid point (default stage list with newer_first inserted after relative_score):
  (a) updated questions, delivered by the gateway: new memory delivered ahead of the stale one or the stale
      one absent, counted over the questions where the new memory was delivered at all.
  (b) the same on the ranked candidate list with every threshold off (the stage's prepare() only), top 10,
      counted over the questions where the new memory is in that top 10.
  (c) all answerable questions: first delivered item is an expected memory; delivered hit, precision and quiet
      rate (they must not move: the stage never changes which candidates reach the limit, unless it does).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, BENCH)

import dev_eval as dev  # noqa: E402  (bench/dev_eval.py; it puts src/ on sys.path)
import score  # noqa: E402
from feiyu_memgate import registry  # noqa: E402
from feiyu_memgate.model import Candidate  # noqa: E402

BANDS = (0.80, 0.85, 0.90, 0.95, 1.0)  # 1.0 = stage off
RELATED = (0, 0.15, 0.3)
BACKENDS = ("sqlite", "embedding", "hybrid")


def points():
    out = [(1.0, 0)]
    out += [(b, r) for b in BANDS[:-1] for r in RELATED]
    return out


def specs_for(band, related):
    specs = dev.stage_list(None)
    if band >= 1.0:
        return specs
    at = next(i for i, s in enumerate(specs) if s["type"] == "relative_score") + 1
    specs.insert(at, {"type": "newer_first", "band": band, "related": related})
    return specs


def updated(queries):
    return {q: v for q, v in queries.items() if v.get("type") == "updated"}


def fresh_counts(id_lists, queries):
    """(fresh-first count, questions where the new memory is in the list) over the updated questions."""
    won = seen = 0
    for qid, q in updated(queries).items():
        ids = id_lists[qid]
        new = q["expect"][0]
        if new not in ids:
            continue
        seen += 1
        won += 1 if score.fresh_first(ids, new, q.get("stale", [])) else 0
    return won, seen


def ranked_after_prepare(cand, queries, band, related):
    """Top 10 ids of each question's raw candidate list after newer_first.prepare() alone (no thresholds)."""
    stage = None
    if band < 1.0:
        stage = registry.create("recall_stage", {"type": "newer_first", "band": band, "related": related})
    out = {}
    for qid in queries:
        cands = [Candidate(score=c["score"], source="private", store=None, ref=c["ref"], text=c.get("text") or "",
                           date=c.get("date")) for c in cand["questions"][qid]]
        if stage is not None:
            cands = stage.prepare(cands, None)
        cands.sort(key=lambda c: -c.score)
        out[qid] = [cand["ids"][c.ref] for c in cands][:dev.TOP]
    return out


def first_correct(answers, queries):
    wanted = [q for q, v in queries.items() if v["expect"]]
    hit = sum(1 for q in wanted if answers[q]["deployed"] and answers[q]["deployed"][0]["id"] in queries[q]["expect"])
    return hit, len(wanted)


def evaluate_point(gateway, cand, queries, band, related):
    dev.set_stages(gateway, specs_for(band, related))
    answers = dev.run_questions(gateway, cand, queries)
    delivered = {q: [d["id"] for d in answers[q]["deployed"]] for q in queries}
    m = dev.metrics(answers, queries)
    a_won, a_seen = fresh_counts(delivered, queries)
    b_won, b_seen = fresh_counts(ranked_after_prepare(cand, queries, band, related), queries)
    f_hit, f_n = first_correct(answers, queries)
    return {"a": (a_won, a_seen), "b": (b_won, b_seen), "first": (f_hit, f_n), "hit": m["deployed_hit"],
            "prec": m["precision"], "quiet": m["quiet"], "delivered": delivered}


def run_backend(cand, queries):
    gateway = dev.replay_gateway(cand, queries)
    try:
        return {p: evaluate_point(gateway, cand, queries, *p) for p in points()}
    finally:
        gateway.close()


def pct(won, seen):
    return "-" if not seen else "%d/%d (%.0f%%)" % (won, seen, 100.0 * won / seen)


def share(won, seen):
    return 0.0 if not seen else won / seen


def table(backend, res):
    lines = ["### %s" % backend, "",
             "| band | related | (a) fresh-first, delivered | (b) fresh-first, ranked top 10 | first item correct | "
             "delivered hit | precision | quiet |", "|---|---|---|---|---|---|---|---|"]
    for (band, related), r in res.items():
        name = "1.0 (off)" if band >= 1.0 else "%.2f" % band
        lines.append("| %s | %s | %s | %s | %s | %.1f%% | %.1f%% | %.1f%% |" % (
            name, "-" if band >= 1.0 else related, pct(*r["a"]), pct(*r["b"]), pct(*r["first"]),
            100 * r["hit"], 100 * r["prec"], 100 * r["quiet"]))
    return lines


def same_sets(res):
    """(do hit/precision/quiet and the delivered id SETS equal the stage-off run at every point, differing questions)."""
    off = res[(1.0, 0)]
    moved = {}
    for p, r in res.items():
        diff = [q for q in off["delivered"] if set(r["delivered"][q]) != set(off["delivered"][q])]
        if diff or any(abs(r[k] - off[k]) > 1e-12 for k in ("hit", "prec", "quiet")):
            moved[p] = diff
    return moved


def choose(results):
    ok = []
    for p in points():
        if p == (1.0, 0):
            continue
        if all(results[b][p]["first"][0] >= results[b][(1.0, 0)]["first"][0] - 1 for b in results):
            mean_a = sum(share(*results[b][p]["a"]) for b in results) / len(results)
            ok.append((mean_a, p))
    ok.sort(key=lambda t: (-round(t[0], 12), -t[1][0], t[1][1]))
    return ok


def report(results, queries, memories):
    n_upd = len(updated(queries))
    lines = ["# newer_first on bench/dev", "",
             "%d memories, %d questions, %d updated, %d answerable." % (
                 len(memories), len(queries), n_upd, sum(1 for v in queries.values() if v["expect"])), ""]
    for backend, res in results.items():
        lines += table(backend, res) + [""]
    lines += ["### Does the delivered set move?", ""]
    for backend, res in results.items():
        moved = same_sets(res)
        lines.append("- %s: %s" % (backend, "no grid point changes the delivered set, hit, precision or quiet rate"
                                   if not moved else "; ".join(
                                       "band %.2f related %s: %d question(s) differ" % (p[0], p[1], len(d))
                                       for p, d in moved.items())))
    lines += ["", "### Decision", ""]
    ok = choose(results)
    base = {b: share(*results[b][(1.0, 0)]["a"]) for b in results}
    lines.append("Stage off, (a) per backend: " + ", ".join("%s %s" % (b, pct(*results[b][(1.0, 0)]["a"])) for b in results))
    lines += ["", "| band | related | mean (a) | first item correct (off -> on) per backend |", "|---|---|---|---|"]
    for mean_a, p in ok:
        lines.append("| %.2f | %s | %.1f%% | %s |" % (p[0], p[1], 100 * mean_a, ", ".join(
            "%s %d -> %d" % (b, results[b][(1.0, 0)]["first"][0], results[b][p]["first"][0]) for b in results)))
    improves = any(share(*results[b][p]["a"]) > base[b] for b in results for p in points() if p != (1.0, 0))
    if not improves:
        lines += ["", "No grid point improves (a) on any backend: opt-in, band 0.9."]
    elif ok:
        lines += ["", "Chosen: band %.2f, related %s (mean (a) %.1f%%)." % (ok[0][1][0], ok[0][1][1], 100 * ok[0][0])]
    else:
        lines += ["", "(a) improves somewhere, but no point meets the first-item-correct condition."]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="write the candidate lists of one backend (needs the embedder for vectors)")
    c.add_argument("--backend", choices=BACKENDS, required=True)
    c.add_argument("--embed-url", default=dev.DEFAULT_EMBED_URL)
    c.add_argument("--out", required=True)
    r = sub.add_parser("report", help="the table, from candidate files")
    r.add_argument("--cand", action="append", default=[], metavar="BACKEND=FILE")
    r.add_argument("--collect-sqlite", action="store_true", help="collect the sqlite lists here (no services)")
    r.add_argument("--out", help="write the markdown here")
    args = parser.parse_args(argv)
    memories, queries = dev.load("memories.json"), dev.load("queries.json")
    if args.cmd == "collect":
        spec = dev.store_spec(args.backend, args.embed_url)
        dev.save_candidates(args.out, dev.collect(spec, memories, queries))
        return 0
    cands = {}
    if args.collect_sqlite:
        cands["sqlite"] = dev.collect(dev.store_spec("sqlite"), memories, queries)
    for item in args.cand:
        name, _, path = item.partition("=")
        cands[name] = dev.load_candidates(path)
    results = {b: run_backend(cands[b], queries) for b in BACKENDS if b in cands}
    text = report(results, queries, memories)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
