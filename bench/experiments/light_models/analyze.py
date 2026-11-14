"""Offline steps of the light-model experiment, on caches made by collect.py (development sets only).

    python bench/experiments/light_models/analyze.py prefix  --cache DIR
    python bench/experiments/light_models/analyze.py recipe  --cache DIR
    python bench/experiments/light_models/analyze.py calibrate --cache DIR [--write-md]

prefix     per model, "no prefix" against "recommended prefix": ranked MRR@10, then Hit@5, thresholds off.
recipe     the static recipe (floor / ceil from the store itself) and what it gives per model.
calibrate  S (static recipe + best grid point per model) against Z (one query-adaptive setting shared by all
           models) with the grid and the `balanced` objective of bench/dev_eval.py --search; then both,
           unchanged, on examples/eval; then the fixed decision rule.

Decision rule (written before the numbers were looked at):
  Z becomes the default if for EVERY model its balanced with the one shared setting is at most 1.0 point
  below S's per-model best on bench/dev, AND on examples/eval its mean balanced over the models is not below S's.
  Otherwise S with presets stays the default, and Z is shipped as an option only if it is within 3.0 points for
  every model; otherwise Z is only described.

Static recipe (fixed once, after looking at bge-m3 only): floor = (mean cosine over all pairs of stored
memories - 0.05) rounded to 0.05; ceil = floor + 2/3 of the remaining range (1 - floor), rounded to 0.05.
The first idea, floor = the pair mean itself rounded to 0.05, gives 0.45 for bge-m3 where 0.40 is known to work;
the 0.05 margin and the 2/3 fraction are the one adjustment, both made before any other model was looked at.

Query-adaptive mapping Z: for a query, the cosines to all n stored memories have mean m and standard deviation
s (population); a memory with cosine c gets z = (c - m) / s and score = clamp((z - z_floor) / (z_ceil - z_floor)).
With fewer than 30 stored memories (or s = 0) the static mapping S is used instead.
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "bench"))
sys.path.insert(0, HERE)

import dev_eval as dev  # noqa: E402
import models  # noqa: E402
import score  # noqa: E402

EVAL_DIR = os.path.join(ROOT, "examples", "eval")
MIN_STORE = 30  # Z needs a distribution to stand in
Z_FLOORS = (1.0, 1.5, 2.0, 2.5, 3.0)
Z_SPANS = (1.5, 2.0, 3.0, 4.0, 5.0)  # z_ceil = z_floor + span
TYPES = ("lexical", "paraphrase", "updated")


# ------------------------------------------------------------------ caches and candidate lists

def load_cache(cache_dir, dataset, key, variant):
    with open(os.path.join(cache_dir, "%s__%s__%s.json" % (dataset, key, variant)), encoding="utf-8") as f:
        return json.load(f)


def round05(x):
    return round(round(x / 0.05) * 0.05, 2)


def recipe(pair_mean):
    """(floor, ceil) from the mean cosine between stored memories."""
    floor = round05(pair_mean - 0.05)
    return floor, round05(floor + (2.0 / 3.0) * (1.0 - floor))


def clamp01(x):
    return min(1.0, max(0.0, x))


def static_mapper(floor, ceil):
    return lambda cosines, row: [clamp01((c - floor) / (ceil - floor)) for c in cosines]


def auto_mapper(z_floor, z_ceil, floor, ceil):
    """Z, falling back to S for a small store or a flat distribution."""
    def mapper(cosines, row):
        n = len(row)
        mean = math.fsum(row) / n if n else 0.0
        var = math.fsum((c - mean) ** 2 for c in row) / n if n else 0.0
        if n < MIN_STORE or var <= 1e-12:
            return [clamp01((c - floor) / (ceil - floor)) for c in cosines]
        std = math.sqrt(var)
        return [clamp01(((c - mean) / std - z_floor) / (z_ceil - z_floor)) for c in cosines]
    return mapper


def make_cand(cache, memories, queries, mapper):
    """The candidate lists dev_eval.search expects: the 15 best memories of every question by cosine, with
    the mapped score. Memories whose mapped score is 0 are left out, as the backend does ("at or below floor"),
    so the ranked columns are what a ranked search of the shipped backend would show."""
    questions = {}
    for qid in queries:
        row = cache["cos"][qid]
        top = sorted(row.items(), key=lambda kv: -kv[1])[:dev.FETCH]
        scores = mapper([c for _, c in top], list(row.values()))
        questions[qid] = [{"ref": ref, "score": s, "text": dev.memory_text(memories[ref]),
                           "date": dev.memory_date(memories[ref])} for (ref, _), s in zip(top, scores) if s > 0.0]
    return {"fetch": dev.FETCH, "ids": {ref: ref for ref in memories}, "questions": questions}


# ------------------------------------------------------------------ metrics

def ranked_metrics(cache, queries):
    """MRR@10, Hit@1, Hit@5 of the pure cosine ranking over the answerable questions."""
    rows = []
    for qid, q in queries.items():
        if not q["expect"]:
            continue
        order = [ref for ref, _ in sorted(cache["cos"][qid].items(), key=lambda kv: -kv[1])]
        rows.append(score.retrieval_metrics(order, q["expect"]))
    n = len(rows)
    return {"mrr10": sum(r["rr"] for r in rows) / n, "hit1": sum(r["hit1"] for r in rows) / n,
            "hit5": sum(r["hit5"] for r in rows) / n}


def summarize(answers, queries):
    """dev_eval.metrics plus MRR@10 and the delivered hit per question type (hits, questions)."""
    m = dev.metrics(answers, queries)
    ranks = [score.retrieval_metrics([a["id"] for a in answers[qid]["ranked"]], q["expect"])
             for qid, q in queries.items() if q["expect"]]
    m["mrr10"] = sum(r["rr"] for r in ranks) / len(ranks)
    by_type = {}
    for qid, q in queries.items():
        if not q["expect"] or "type" not in q:
            continue
        got = {a["id"] for a in answers[qid]["deployed"]}
        hit, total = by_type.get(q["type"], (0, 0))
        by_type[q["type"]] = (hit + (1 if got & set(q["expect"]) else 0), total + 1)
    m["by_type"] = by_type
    return m


def run_point(cand, queries, point):
    """Full metrics of one (min_score, ratio) point on one candidate set."""
    gateway = dev.replay_gateway(cand, queries)
    try:
        dev.set_stages(gateway, dev.grid_specs(*point))
        return summarize(dev.run_questions(gateway, cand, queries), queries)
    finally:
        gateway.close()


# ------------------------------------------------------------------ the chosen variant per model

def prefix_table(cache_dir, queries):
    """{key: {variant: ranked metrics}} on bench/dev."""
    out = {}
    for model in models.MODELS:
        out[model["key"]] = {}
        for variant, _, _ in models.variants(model):
            out[model["key"]][variant] = ranked_metrics(load_cache(cache_dir, "dev", model["key"], variant), queries)
    return out


def choose(table):
    """The variant with the higher MRR@10, then the higher Hit@5; a tie keeps "plain" (the simpler)."""
    best = "plain"
    for variant, m in table.items():
        if (round(m["mrr10"], 12), round(m["hit5"], 12)) > (round(table[best]["mrr10"], 12),
                                                            round(table[best]["hit5"], 12)):
            best = variant
    return best


def cmd_prefix(args, queries):
    table = prefix_table(args.cache, queries)
    print("%-16s %-9s %8s %7s %7s" % ("model", "variant", "MRR@10", "Hit@1", "Hit@5"))
    for key, variants in table.items():
        for variant, m in variants.items():
            print("%-16s %-9s %8.3f %7.1f %7.1f" % (key, variant, m["mrr10"], m["hit1"] * 100, m["hit5"] * 100))
        print("  kept: %s" % choose(variants))
    return table


def kept_variants(args, queries):
    return {key: choose(v) for key, v in prefix_table(args.cache, queries).items()}


def cmd_recipe(args, queries):
    kept = kept_variants(args, queries)
    print("%-16s %-9s %9s %6s %6s" % ("model", "kept", "pair mean", "floor", "ceil"))
    for model in models.MODELS:
        cache = load_cache(args.cache, "dev", model["key"], kept[model["key"]])
        print("%-16s %-9s %9.4f %6.2f %6.2f" % ((model["key"], kept[model["key"]], cache["pair_mean"])
                                               + recipe(cache["pair_mean"])))


# ------------------------------------------------------------------ the comparison

def fmt(m):
    t = m["by_type"]
    return "%5.1f %5.1f %5.1f %5.1f %5.1f %5.1f %5.1f %5.1f   %s" % (
        m["hit1"] * 100, m["hit5"] * 100, m["mrr10"] * 100, m["deployed_hit"] * 100, (m["precision"] or 0) * 100,
        m["quiet"] * 100, m["balanced"] * 100, m["items"],
        " ".join("%s %d/%d" % (k[:4], *t[k]) for k in TYPES if k in t) if t else "-")


HEAD = "%-22s %5s %5s %5s %5s %5s %5s %5s %5s   %s" % ("", "Hit@1", "Hit@5", "MRR10", "dHit", "prec", "quiet",
                                                       "bal", "items", "delivered hit by type")


def cmd_calibrate(args, queries):
    memories = dev.load("memories.json", dev.DEV_DIR)
    kept = kept_variants(args, queries)
    ev_memories = dev.load("memories.json", EVAL_DIR)
    ev_queries = dev.load("queries.json", EVAL_DIR)
    per = {}
    for model in models.MODELS:
        key = model["key"]
        cache = load_cache(args.cache, "dev", key, kept[key])
        floor, ceil = recipe(cache["pair_mean"])
        s_cand = make_cand(cache, memories, queries, static_mapper(floor, ceil))
        s_results = dev.search(s_cand, queries)  # best first, dev_eval's tie rules
        per[key] = {"cache": cache, "floor": floor, "ceil": ceil, "s_cand": s_cand, "s_results": s_results,
                    "s_point": s_results[0][0], "z": {}}
        print("S  %-16s floor %.2f ceil %.2f best %s  balanced %.1f" % (
            key, floor, ceil, dev.point_label(s_results[0][0]), s_results[0][1]["balanced"] * 100), flush=True)
    # Z: one shared (z_floor, z_ceil, min_score, ratio) for all models
    shared = {}
    for z_floor in Z_FLOORS:
        for span in Z_SPANS:
            z_ceil = z_floor + span
            for key, p in per.items():
                cand = make_cand(p["cache"], memories, queries, auto_mapper(z_floor, z_ceil, p["floor"], p["ceil"]))
                for point, m in dev.search(cand, queries):
                    shared.setdefault((z_floor, z_ceil) + point, {})[key] = m
            print("Z grid z_floor %.1f z_ceil %.1f done" % (z_floor, z_ceil), flush=True)

    def shared_key(item):
        setting, by_model = item
        n = len(by_model)
        mean_bal = sum(m["balanced"] for m in by_model.values()) / n
        mean_hit = sum(m["deployed_hit"] for m in by_model.values()) / n
        ratio = setting[3]
        return (round(mean_bal, 12), round(mean_hit, 12), -setting[2], 1.0 if ratio is None else -ratio,
                -setting[0], -setting[1])

    best_setting, _ = max(((s, b) for s, b in shared.items() if len(b) == len(per)), key=shared_key)
    z_floor, z_ceil, z_min, z_ratio = best_setting
    print("\nshared Z setting: z_floor %.1f z_ceil %.1f min_score %.2f ratio %s" % (
        z_floor, z_ceil, z_min, "off" if z_ratio is None else "%.2f" % z_ratio))

    # report on bench/dev
    print("\n=== bench/dev (160 memories, 110 questions)")
    print(HEAD)
    verdict = {}
    for model in models.MODELS:
        key = model["key"]
        p = per[key]
        s_m = run_point(p["s_cand"], queries, p["s_point"])
        z_cand = make_cand(p["cache"], memories, queries, auto_mapper(z_floor, z_ceil, p["floor"], p["ceil"]))
        z_m = run_point(z_cand, queries, (z_min, z_ratio))
        p["s_dev"], p["z_dev"] = s_m, z_m
        print("%-16s S %s" % (key, fmt(s_m)))
        print("%-16s Z %s" % ("", fmt(z_m)))
        verdict[key] = (z_m["balanced"] - s_m["balanced"]) * 100
    print("\nZ minus S, balanced points on bench/dev: " + ", ".join("%s %+.1f" % kv for kv in verdict.items()))

    # examples/eval, unchanged settings
    print("\n=== examples/eval (%d memories, %d questions), settings unchanged" % (len(ev_memories), len(ev_queries)))
    print(HEAD)
    s_sum = z_sum = 0.0
    for model in models.MODELS:
        key = model["key"]
        p = per[key]
        cache = load_cache(args.cache, "eval", key, kept[key])
        s_cand = make_cand(cache, ev_memories, ev_queries, static_mapper(p["floor"], p["ceil"]))
        z_cand = make_cand(cache, ev_memories, ev_queries, auto_mapper(z_floor, z_ceil, p["floor"], p["ceil"]))
        s_m = run_point(s_cand, ev_queries, p["s_point"])
        z_m = run_point(z_cand, ev_queries, (z_min, z_ratio))
        p["s_eval"], p["z_eval"] = s_m, z_m
        s_sum += s_m["balanced"]
        z_sum += z_m["balanced"]
        print("%-16s S %s" % (key, fmt(s_m)))
        print("%-16s Z %s" % ("", fmt(z_m)))
    n = len(models.MODELS)
    print("\nmean balanced on examples/eval: S %.1f, Z %.1f" % (s_sum / n * 100, z_sum / n * 100))

    # the rule
    worst = min(verdict.values())
    cond1 = all(v >= -1.0 for v in verdict.values())
    cond2 = z_sum >= s_sum
    print("\nrule: every model within 1.0 below S on dev: %s (worst %+.1f); examples/eval mean Z >= S: %s"
          % (cond1, worst, cond2))
    if cond1 and cond2:
        print("decision: Z becomes the default calibration")
    elif worst >= -3.0:
        print("decision: S with presets stays the default; Z shipped as an option (within 3.0 for every model)")
    else:
        print("decision: S with presets stays the default; Z is not shipped (worst %+.1f)" % worst)

    if args.write_md:
        write_md(per, kept, queries, memories, (z_floor, z_ceil, z_min, z_ratio))
    return per


def write_md(per, kept, queries, memories, z_setting):
    """results/dev/embedding-<key>.md for the four new models, in the format of dev_eval.report()."""
    out_dir = dev.OUT_DIR
    for model in models.MODELS:
        key = model["key"]
        if key == "bge-m3":
            continue  # embedding.md already is the bge-m3 file (the recipe gives its 0.40 / 0.80)
        p = per[key]
        old_cand = make_cand(p["cache"], memories, queries, static_mapper(0.40, 0.80))
        old_m = run_point(old_cand, queries, (0.50, 0.85))
        text = dev.report("embedding, %s (%s, floor %.2f ceil %.2f)" % (key, kept[key], p["floor"], p["ceil"]),
                          dev.DEV_DIR, queries, memories, p["s_results"], (0.50, 0.85), old_m)
        text += ("\n上面的“before”是同一份余弦用 bge-m3 的旧默认（floor 0.40、ceil 0.80、min_score 0.50、比例 0.85）换算"
                 "的结果；“chosen”是按静态配方（floor %.2f、ceil %.2f）换算后的最优点。"
                 "模型：`%s`，前缀方案：%s。\n" % (p["floor"], p["ceil"], model["name"], kept[key]))
        with open(os.path.join(out_dir, "embedding-%s.md" % key), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("step", choices=("prefix", "recipe", "calibrate"))
    parser.add_argument("--cache", required=True)
    parser.add_argument("--write-md", action="store_true")
    args = parser.parse_args(argv)
    queries = dev.load("queries.json", dev.DEV_DIR)
    {"prefix": cmd_prefix, "recipe": cmd_recipe, "calibrate": cmd_calibrate}[args.step](args, queries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
