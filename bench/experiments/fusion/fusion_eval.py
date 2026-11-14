"""Fusion experiment for the hybrid backend, on the DEVELOPMENT set only (bench/dev).

    # 1. candidate lists of the two children (cached; ids and scores only, no memory text)
    python bench/experiments/fusion/fusion_eval.py --collect --embed-url http://fmg-bench-embedder:11434/v1
    # 2. offline evaluation of every fusion variant through the real default recall stages
    python bench/experiments/fusion/fusion_eval.py

Decision rule, written down before any number was looked at:

  * every variant gets the same grid as `bench/dev_eval.py --search` (min_score x relative_score ratio, with
    the default stage list otherwise) and is judged by its best grid point under the `balanced` objective,
    with dev_eval's own tie-breaks (deployed hit, lower min_score, lower ratio);
  * a variant is ADMISSIBLE when its ranked Hit@5 (top 10, thresholds off) is within one answerable question
    of variant A (the current probabilistic OR) and its quiet rate at its chosen point is within 5 points of A's;
  * among admissible variants the highest `balanced` wins; ties (equal to 1e-9) go to the higher Hit@1, then to
    the simpler variant (A, B, C, D, E in that order, variants added later after that);
  * the winner must beat A's `balanced` by at least one question's worth (1/110 = 0.91 points); otherwise A stays.

Each child's candidate list is what the real hybrid asks for: top K = 3 x 15 = 45 hits (the gateway fetches 15
for a limit of 5 and the hybrid over-fetches threefold). The fused list is cut to 15, like the backend does.
Memory text is read from bench/dev/memories.json at replay time and never stored in the cache.

The embedding model name, endpoint and the embedding child's floor/ceil are arguments, so the script can be
re-run for a smaller or weaker model: pass --model, --embed-url, --floor, --ceil and (if its own default
threshold differs from the shipped 0.50) --embed-min.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "bench"))
sys.path.insert(0, os.path.join(ROOT, "src"))

import dev_eval  # noqa: E402

CACHE = os.path.join(HERE, "child_candidates.json")
K = dev_eval.FETCH * 3
CUT = dev_eval.FETCH          # the hybrid hands back `limit` = 15 fused hits
CHILDREN = ("sqlite", "embedding")
RRF_K = 60
BONUS = 0.10
EPS = 1e-9


# ------------------------------------------------------------------ collecting

def collect(args, memories, queries):
    """{"sqlite": {qid: [[ref, score], ...]}, "embedding": {...}, "meta": {...}} (ids and scores only)."""
    out = {"meta": {"k": K, "model": args.model, "floor": args.floor, "ceil": args.ceil}}
    for name in CHILDREN:
        options = {"floor": args.floor, "ceil": args.ceil} if name == "embedding" else {}
        spec = dev_eval.store_spec(name, args.embed_url, args.model, options)
        gateway = dev_eval.build(spec)
        try:
            backend = gateway.stores["private"]
            backend.find(dev_eval.WARMUP_QUERY, 1, timeout=dev_eval.EMBED_TIMEOUT)
            dev_eval.ingest(backend, memories)
            out[name] = {}
            for qid, q in queries.items():
                hits = sorted(backend.find(q["query"], K, timeout=dev_eval.EMBED_TIMEOUT), key=lambda h: -h.score)
                out[name][qid] = [[h.ref, float(h.score)] for h in hits]
        finally:
            gateway.close()
    with open(args.cache, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f)
        f.write("\n")
    return out


# ------------------------------------------------------------------ fusion variants
# Each takes: lists = [[(ref, score), ...] per child, best first]; mins = each child's own default_min_score.
# It returns [(ref, score)] sorted best first. Scores stay in 0..1.

def _merge(lists):
    merged = {}
    for index, hits in enumerate(lists):
        for ref, score in hits:
            merged.setdefault(ref, [None] * len(lists))[index] = min(1.0, max(0.0, score))
    return merged


def _rescale(score, floor_point):
    """Child score -> 0..1 with the child's own default threshold mapped to 0.5."""
    return min(1.0, score / (2.0 * floor_point))


def _sorted(pairs):
    return sorted(pairs, key=lambda p: (-p[1], p[0]))


def fuse_or(lists, mins):
    out = []
    for ref, scores in _merge(lists).items():
        miss = 1.0
        for s in scores:
            if s is not None:
                miss *= 1.0 - s
        out.append((ref, 1.0 - miss))
    return _sorted(out)


def fuse_max(lists, mins):
    return _sorted([(ref, max(s for s in scores if s is not None)) for ref, scores in _merge(lists).items()])


def _calibrated(scores, mins):
    return [None if s is None else _rescale(s, m) for s, m in zip(scores, mins)]


def fuse_cor(lists, mins):
    out = []
    for ref, scores in _merge(lists).items():
        miss = 1.0
        for s in _calibrated(scores, mins):
            if s is not None:
                miss *= 1.0 - s
        out.append((ref, 1.0 - miss))
    return _sorted(out)


def fuse_cmax(lists, mins):
    return _sorted([(ref, max(s for s in _calibrated(scores, mins) if s is not None))
                    for ref, scores in _merge(lists).items()])


def make_cmax_bonus(bonus):
    """Calibrated max, plus `bonus` of the remaining room when a second child also has the memory at or
    above its own threshold (calibrated 0.5)."""
    def fuse(lists, mins):
        out = []
        for ref, scores in _merge(lists).items():
            cal = [s for s in _calibrated(scores, mins) if s is not None]
            top = max(cal)
            agree = sum(1 for s in cal if s >= 0.5) >= 2
            out.append((ref, top + bonus * (1.0 - top) if agree else top))
        return _sorted(out)
    return fuse


def fuse_rrf(lists, mins):
    """Order by reciprocal-rank fusion; score = calibrated max, made non-increasing along that order.

    The gateway sorts by score, so one number has to both sort and gate. Walk the RRF order and give each
    memory min(own calibrated max, previous score - EPS): the order is exactly the RRF order, no memory ever
    scores higher than its own calibrated max (so fusion cannot lift noise over a threshold), and what the
    thresholds see is a lower-or-equal copy of variant D's input."""
    rank = {}
    for index, hits in enumerate(lists):
        for position, (ref, _) in enumerate(hits, 1):
            rank[(index, ref)] = position
    rrf = {}
    for index, hits in enumerate(lists):
        for ref, _ in hits:
            rrf[ref] = rrf.get(ref, 0.0) + 1.0 / (RRF_K + rank[(index, ref)])
    own = dict(fuse_cmax(lists, mins))
    out, previous = [], 1.0
    for ref in sorted(rrf, key=lambda r: (-rrf[r], r)):
        previous = max(0.0, min(own[ref], previous - EPS))
        out.append((ref, previous))
    return out


VARIANTS = [
    ("A", "probabilistic OR (current)", fuse_or),
    ("B", "max of child scores", fuse_max),
    ("C", "calibrated OR", fuse_cor),
    ("D", "calibrated max + agreement bonus", make_cmax_bonus(BONUS)),
    ("E", "RRF order, calibrated-max score", fuse_rrf),
]
# Variants added after looking at the per-question data are appended by extra_variants() and flagged.


# ------------------------------------------------------------------ evaluation

def candidates_for(fuse, children, memories, queries, mins):
    ids = {ref: ref for ref in memories}
    questions = {}
    for qid in queries:
        lists = [[(ref, s) for ref, s in children[name][qid]] for name in CHILDREN]
        fused = fuse(lists, mins)[:CUT]
        questions[qid] = [{"ref": ref, "score": score, "text": dev_eval.memory_text(memories[ref]),
                           "date": dev_eval.memory_date(memories[ref])} for ref, score in fused]
    return {"fetch": CUT, "ids": ids, "questions": questions}


def single_candidates(name, children, memories, queries):
    ids = {ref: ref for ref in memories}
    questions = {qid: [{"ref": ref, "score": score, "text": dev_eval.memory_text(memories[ref]),
                        "date": dev_eval.memory_date(memories[ref])} for ref, score in children[name][qid][:CUT]]
                 for qid in queries}
    return {"fetch": CUT, "ids": ids, "questions": questions}


def delivered_by_type(cand, queries, point):
    """({qid: delivered hit bool}, answers) at one grid point."""
    gateway = dev_eval.replay_gateway(cand, queries)
    try:
        dev_eval.set_stages(gateway, dev_eval.grid_specs(*point))
        answers = dev_eval.run_questions(gateway, cand, queries)
    finally:
        gateway.close()
    hits = {qid: bool(set(a["id"] for a in ans["deployed"]) & set(queries[qid]["expect"]))
            for qid, ans in answers.items() if queries[qid]["expect"]}
    return hits, answers


def summarize(cand, queries, fixed_point=None):
    """Best grid point (or `fixed_point`), its metrics, the ranked metrics with thresholds off, by-type hits."""
    results = dev_eval.search(cand, queries)
    point, m = results[0]
    if fixed_point is not None:
        point = fixed_point
        m = dict(results)[fixed_point] if fixed_point in dict(results) else m
    hits, answers = delivered_by_type(cand, queries, point)
    by_type = {}
    for qid, ok in hits.items():
        row = by_type.setdefault(queries[qid]["type"], [0, 0])
        row[0] += int(ok)
        row[1] += 1
    return {"point": point, "m": m, "hits": hits, "by_type": by_type}


def ranked_hits(cand, queries):
    """Hit@1 and Hit@5 over the answerable questions, thresholds off, top 10 (counts and shares)."""
    n = h1 = h5 = 0
    for qid, q in queries.items():
        if not q["expect"]:
            continue
        n += 1
        order = []
        for c in cand["questions"][qid]:
            if c["ref"] not in order:
                order.append(c["ref"])
        top = order[:dev_eval.TOP]
        h1 += int(bool(top[:1]) and top[0] in q["expect"])
        h5 += int(bool(set(top[:5]) & set(q["expect"])))
    return h1, h5, n


def make_weighted_or(weights):
    """OR with per-child weights (the `weights` option the backend already has); uncalibrated."""
    def fuse(lists, mins):
        out = []
        for ref, scores in _merge(lists).items():
            miss = 1.0
            for w, s in zip(weights, scores):
                if s is not None:
                    miss *= 1.0 - w * s
            out.append((ref, 1.0 - miss))
        return _sorted(out)
    return fuse


def extra_variants():
    """Added AFTER looking at the per-question data: the keyword score on the top wrong memory of a no-answer
    question (about 0.25) is higher than on the right memory of a paraphrase question (about 0.15), so the
    keyword side should count for less, not more. Two weights of the same family."""
    return [("F", "OR, keyword weight 0.5", make_weighted_or([0.5, 1.0])),
            ("G", "OR, keyword weight 0.25", make_weighted_or([0.25, 1.0]))]


# ------------------------------------------------------------------ report

def pct(x):
    return "%5.1f" % (100 * x)


def run(args):
    memories, queries = dev_eval.load("memories.json"), dev_eval.load("queries.json")
    if not os.path.exists(args.cache):
        raise SystemExit("no cache %s: run with --collect first" % args.cache)
    with open(args.cache, encoding="utf-8") as f:
        children = json.load(f)
    mins = [args.sqlite_min, args.embed_min]
    n_para = sum(1 for q in queries.values() if q["type"] == "paraphrase")
    lines = []

    def out(text=""):
        print(text)
        lines.append(text)

    out("cache: %s" % json.dumps(children["meta"]))
    out("child own thresholds used for calibration: sqlite %.2f, embedding %.2f" % tuple(mins))
    out("")

    # bars to beat: each child alone at ITS OWN default point (as the gateway would run it) and at its best point
    baselines = {}
    for name, own in zip(CHILDREN, ((args.sqlite_min, args.sqlite_ratio), (args.embed_min, args.embed_ratio))):
        cand = single_candidates(name, children, memories, queries)
        rows = dev_eval.evaluate(cand, [dev_eval.grid_specs(*own)], queries)
        baselines[name] = {"cand": cand, "default_point": own, "default_m": rows[0][1], "best": summarize(cand, queries)}
    emb_default_hits, _ = delivered_by_type(baselines["embedding"]["cand"], queries, baselines["embedding"]["default_point"])

    table = []
    variants = list(VARIANTS) + [(k, d + " [added after looking]", f) for k, d, f in extra_variants()]
    for key, desc, fuse in variants:
        cand = candidates_for(fuse, children, memories, queries, mins)
        s = summarize(cand, queries)
        s["ranked"] = ranked_hits(cand, queries)
        s["key"], s["desc"] = key, desc
        table.append(s)
    for name in CHILDREN:
        b = baselines[name]
        b["best"]["ranked"] = ranked_hits(b["cand"], queries)

    def head():
        out("%-34s %6s %6s | %-18s %7s %6s %6s %6s %6s" % ("", "Hit@1", "Hit@5", "best point", "dep.hit", "prec", "quiet",
                                                          "bal", "items"))

    def row(label, s, default_m=None):
        h1, h5, n = s["ranked"]
        m = default_m or s["m"]
        point = s.get("point_override") or s["point"]
        out("%-34s %6s %6s | %-18s %7s %6s %6s %6s %6.2f" % (
            label, pct(h1 / n), pct(h5 / n), dev_eval.point_label(point).replace("min_score=", "m=").replace(
                "relative=", "r="), pct(m["deployed_hit"]), pct(m["precision"] or 0), pct(m["quiet"]),
            pct(m["balanced"]), m["items"]))

    out("== all variants, best grid point by balanced (n answerable = %d, quiet = 30) ==" % table[0]["ranked"][2])
    head()
    for name in CHILDREN:
        b = baselines[name]
        row("child alone: %s (its best)" % name, b["best"])
        d = dict(b["best"])
        d["point_override"] = b["default_point"]
        row("child alone: %s (its default)" % name, d, b["default_m"])
    for s in table:
        row("%s %s" % (s["key"], s["desc"]), s)
    out("")

    out("== delivered hit by question type at each variant's chosen point (hit/n) ==")
    types = ["lexical", "paraphrase", "updated"]
    out("%-34s %s" % ("", " ".join("%12s" % t for t in types)))
    for name in CHILDREN:
        b = baselines[name]
        by_type = delivered_by_type(b["cand"], queries, b["best"]["point"])[0]
        agg = {}
        for qid, ok in by_type.items():
            r = agg.setdefault(queries[qid]["type"], [0, 0])
            r[0] += int(ok)
            r[1] += 1
        out("%-34s %s" % ("%s alone (its best point)" % name,
                          " ".join("%12s" % ("%d/%d" % tuple(agg[t])) for t in types)))
    for name in CHILDREN:
        b = baselines[name]
        hits, _ = delivered_by_type(b["cand"], queries, b["default_point"])
        agg = {}
        for qid, ok in hits.items():
            r = agg.setdefault(queries[qid]["type"], [0, 0])
            r[0] += int(ok)
            r[1] += 1
        out("%-34s %s" % ("%s alone (its default)" % name,
                          " ".join("%12s" % ("%d/%d" % tuple(agg[t])) for t in types)))
    for s in table:
        out("%-34s %s" % ("%s %s" % (s["key"], s["desc"]),
                          " ".join("%12s" % ("%d/%d" % tuple(s["by_type"][t])) for t in types)))
    out("")

    out("== paraphrase questions (%d): delivered by the variant vs by the embedding child alone at ITS default ==" % n_para)
    para = [qid for qid, q in queries.items() if q["type"] == "paraphrase"]
    emb = {qid for qid in para if emb_default_hits[qid]}
    out("embedding alone at its default delivers %d of %d" % (len(emb), n_para))
    for s in table:
        mine = {qid for qid in para if s["hits"][qid]}
        out("%-34s delivers %2d: both %2d, only variant %2d, MISSED (embedding delivers, variant not) %2d" % (
            "%s %s" % (s["key"], s["desc"]), len(mine), len(mine & emb), len(mine - emb), len(emb - mine)))
    out("")

    # decision
    a = next(s for s in table if s["key"] == "A")
    one_question = 1.0 / len(queries)
    out("== decision rule ==")
    out("A: balanced %.4f, ranked Hit@5 %d/%d, quiet %.3f; one question = %.4f balanced, one answerable question "
        "= 1 hit" % (a["m"]["balanced"], a["ranked"][1], a["ranked"][2], a["m"]["quiet"], one_question))
    admissible = []
    for s in table:
        ok5 = s["ranked"][1] >= a["ranked"][1] - 1
        okq = s["m"]["quiet"] >= a["m"]["quiet"] - 0.05 - 1e-9
        out("%s: Hit@5 %d (>= %d: %s), quiet %.3f (>= %.3f: %s), balanced %.4f" % (
            s["key"], s["ranked"][1], a["ranked"][1] - 1, ok5, s["m"]["quiet"], a["m"]["quiet"] - 0.05, okq,
            s["m"]["balanced"]))
        if ok5 and okq:
            admissible.append(s)
    order = [v[0] for v in variants]
    admissible.sort(key=lambda s: (-round(s["m"]["balanced"], 9), -s["ranked"][0], order.index(s["key"])))
    winner = admissible[0]
    out("best admissible by (balanced, Hit@1, simplicity): %s" % winner["key"])
    if winner["key"] != "A" and winner["m"]["balanced"] < a["m"]["balanced"] + one_question - 1e-9:
        out("but it beats A by %.4f < one question's worth (%.4f): A STAYS" % (
            winner["m"]["balanced"] - a["m"]["balanced"], one_question))
        winner = a
    out("ADOPTED: %s (%s) at %s" % (winner["key"], winner["desc"], dev_eval.point_label(winner["point"])))
    best_emb = baselines["embedding"]
    out("embedding alone at its default: balanced %.4f (dep.hit %.3f, quiet %.3f); adopted: balanced %.4f -> %s" % (
        best_emb["default_m"]["balanced"], best_emb["default_m"]["deployed_hit"], best_emb["default_m"]["quiet"],
        winner["m"]["balanced"],
        "BEATS it" if winner["m"]["balanced"] > best_emb["default_m"]["balanced"] + 1e-9 else "does NOT beat it"))
    with open(os.path.join(HERE, "results.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return table


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--collect", action="store_true", help="ask the children (needs the embedder) and write the cache")
    p.add_argument("--cache", default=CACHE)
    p.add_argument("--embed-url", default=dev_eval.DEFAULT_EMBED_URL)
    p.add_argument("--model", default=dev_eval.DEFAULT_MODEL)
    p.add_argument("--floor", type=float, default=0.40, help="embedding child's floor")
    p.add_argument("--ceil", type=float, default=0.80, help="embedding child's ceil")
    p.add_argument("--embed-min", type=float, default=0.50, help="embedding child's own default min_score")
    p.add_argument("--embed-ratio", type=float, default=0.85, help="embedding child's own default ratio")
    p.add_argument("--sqlite-min", type=float, default=0.30)
    p.add_argument("--sqlite-ratio", type=float, default=0.75)
    args = p.parse_args(argv)
    memories, queries = dev_eval.load("memories.json"), dev_eval.load("queries.json")
    if args.collect:
        collect(args, memories, queries)
        print("cache written: %s" % args.cache)
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
