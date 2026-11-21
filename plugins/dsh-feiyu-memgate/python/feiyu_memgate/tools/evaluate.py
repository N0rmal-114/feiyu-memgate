"""Recall evaluation: measure how well a store finds made-up memories, so the score
threshold and result count can be chosen from data instead of by feel.

Nothing here talks to a real store unless you point it at one: `run_eval` takes any
backend, writes the example memories into it (or, with seed=False, uses what is
already there), asks the example questions and prints a table of hit rates per score
threshold and result count. It recommends a line; it changes no setting.

Use a throw-away store for seeding (the "inmemory" or "sqlite" backend, or a scratch
account of a service): never seed a store that holds real memories.

Standard library only.
"""
import statistics
from collections import namedtuple

from ..core.pool import NULL_CTL

THRESHOLDS = (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
LIMITS = (3, 5, 8)

# File names a store keeps for its own bookkeeping (folder summaries); they are never
# real memories, so they must not take a result slot or count as noise.
SKIP_NAMES = frozenset({".abstract.md", ".overview.md", "soul.md", "identity.md"})

Row = namedtuple("Row", "threshold limit hit noise quiet")
# wanted_*: per query that should find something, the best score among its expected memories (lowest, median);
# other_*: per query, the best score among memories it should not get (median, highest). None when there are none.
Spread = namedtuple("Spread", "wanted_low wanted_mid other_mid other_high")


def _clean(items, skip=SKIP_NAMES):
    """Drop folder summaries, then best score first, as the gateway does before it applies the limit."""
    kept = [(u, s) for u, s in items if u.rsplit("/", 1)[-1].lower() not in skip]
    return sorted(kept, key=lambda item: -item[1])


def score_rows(found, expect, thresholds=THRESHOLDS, limits=LIMITS):
    """found: query id -> [(ref, score)] in any order. One Row per (threshold, limit)."""
    found = {q: _clean(items) for q, items in found.items()}
    rows = []
    wanted = [q for q in expect if expect[q]]
    silent = [q for q in expect if not expect[q]]
    for threshold in thresholds:
        for limit in limits:
            kept = {q: [u for u, s in found.get(q, [])[:limit] if s >= threshold] for q in expect}
            hits = sum(1 for q in wanted if any(u in expect[q] for u in kept[q]))
            total = sum(len(kept[q]) for q in expect)
            extra = sum(1 for q in expect for u in kept[q] if u not in expect[q])
            quiet = sum(1 for q in silent if not kept[q])
            rows.append(Row(threshold, limit,
                            hits / len(wanted) if wanted else 0.0,
                            extra / total if total else 0.0,
                            quiet / len(silent) if silent else 1.0))
    return rows


def score_spread(found, expect):
    """Where the scores sit, to show whether any one score line can split wanted memories from the rest."""
    found = {q: _clean(items) for q, items in found.items()}
    wanted, other = [], []
    for q, want in expect.items():
        items = found.get(q, [])
        good = [s for u, s in items if u in want]
        if want and good:
            wanted.append(max(good))
        bad = [s for u, s in items if u not in want]
        if bad:
            other.append(max(bad))

    def r(value):
        return None if value is None else round(value, 3)
    return Spread(r(min(wanted)) if wanted else None, r(statistics.median(wanted)) if wanted else None,
                  r(statistics.median(other)) if other else None, r(max(other)) if other else None)


def _spread_line(spread):
    def f(value):
        return "无" if value is None else f"{value:.2f}"
    if spread == Spread(None, None, None, None):
        return "分数分布：没有找到任何记忆"
    return (f"分数分布：该找到的记忆 最低 {f(spread.wanted_low)}、中位 {f(spread.wanted_mid)}；"
            f"不该出现的记忆里的最高分 中位 {f(spread.other_mid)}、最高 {f(spread.other_high)}")


def best_row(rows):
    """Among rows with noise <= 20% and quiet >= 80%: highest hit, then higher threshold."""
    good = [r for r in rows if r.noise <= 0.2 and r.quiet >= 0.8 and r.hit > 0]
    if not good:
        return None
    return max(good, key=lambda r: (r.hit, r.threshold, -r.limit))


def _pct(value):
    return f"{value * 100:.0f}%"


def _line(row):
    return (f"分数线 {row.threshold:.2f}  条数 {row.limit}  "
            f"命中 {_pct(row.hit)}  噪声 {_pct(row.noise)}  安静 {_pct(row.quiet)}")


def run_eval(backend, memories, queries, *, thresholds=THRESHOLDS, limits=LIMITS, fetch=30, seed=True, out=print):
    """Score `backend` on the example questions.

    memories  {id: text}; queries  {query id: {"query": str, "expect": [memory id, ...]}}.
    With seed=True each memory is written through backend.put(text, ref=id); with
    seed=False the backend must already hold memories under those ids.
    Returns {"rows": [Row], "best": Row | None, "spread": Spread} and prints the table through `out`.
    """
    if seed:
        put = getattr(backend, "put", None)
        if put is None:
            raise ValueError("这个存储没有 put()，没法写入示例记忆："
                             "请先把示例记忆放进去，再用 seed=False（命令行 --no-seed）评测")
        for ref, text in memories.items():
            put(text, ref=ref)
        out(f"已写入 {len(memories)} 条假记忆。")
    # Called outside the pool: the backend gets NULL_CTL, never None.
    found = {qid: [(h.ref, h.score) for h in backend.find(item["query"], fetch, None, NULL_CTL)]
             for qid, item in queries.items()}
    expect = {qid: list(item["expect"]) for qid, item in queries.items()}
    rows = score_rows(found, expect, thresholds, limits)
    for row in rows:
        out(_line(row))
    spread = score_spread(found, expect)
    out(_spread_line(spread))
    best = best_row(rows)
    if best is None:
        out("没有哪一行同时满足噪声不超过 20% 且安静不低于 80%。")
    else:
        out("推荐：" + _line(best) + "（只是建议，没有改任何设置）")
    return {"rows": rows, "best": best, "spread": spread}
