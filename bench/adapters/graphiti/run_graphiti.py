"""Benchmark adapter: Graphiti (graphiti-core) with an embedded Kuzu graph, DeepSeek LLM, shared bge-m3 embedder.

Runs inside the container (see Dockerfile / run.sh). Two phases, resumable through /db/state.json:

  ingest : Graphiti.add_episode(name=<memory id>, episode_body=<text>, reference_time=<memory date>)
           one episode per memory, strictly in date order. Every LLM call is counted through a thin
           counting proxy around the AsyncOpenAI client that is handed to Graphiti (configuration only,
           Graphiti's code is not patched).
  answer : Graphiti.search(query)  (its out-of-the-box hybrid edge search: BM25 + cosine, RRF rerank,
           default limit 10). No LLM is called at query time, only the embedder.

Usage: run_graphiti.py --out PATH [--limit-new N] [--extras K|all] [--answer]
"""
import argparse
import asyncio
import json
import os
import time
import urllib.request
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore")

from openai import AsyncOpenAI  # noqa: E402

from graphiti_core import Graphiti  # noqa: E402
from graphiti_core.cross_encoder.client import CrossEncoderClient  # noqa: E402
from graphiti_core.driver.kuzu_driver import KuzuDriver  # noqa: E402
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig  # noqa: E402
from graphiti_core.llm_client.config import LLMConfig  # noqa: E402
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient  # noqa: E402
from graphiti_core.nodes import EpisodeType  # noqa: E402

DATA = "/data"
DB = "/db/graph.kuzu"
STATE = "/db/state.json"
LLM_MODEL = "deepseek-flash"
LLM_BASE = "https://api.deepseek.com/v1"
EMB_BASE = "http://fmg-bench-embedder:11434/v1"
EMB_MODEL = "bge-m3"
WARMUP_QUERY = "这是一个用来热身的问题"
TOP = 10
BUDGET_CNY = 5.0
# Conservative prices (CNY per million tokens) used ONLY for the stop rule; the reported cost is the
# balance difference. Overridable through the environment after the probe, see README.
PRIOR_SPEND = float(os.environ.get("PRIOR_SPEND_CNY", "0"))  # spend of earlier aborted runs, see README
PRICE_IN = float(os.environ.get("PRICE_IN", "2.0"))
PRICE_OUT = float(os.environ.get("PRICE_OUT", "8.0"))


class _Counter:
    def __init__(self):
        self.calls = 0
        self.errors = 0
        self.prompt = 0
        self.completion = 0
        self.cache_hit = 0


COUNT = _Counter()


class _Completions:
    def __init__(self, inner):
        self._inner = inner

    async def create(self, *args, **kwargs):
        COUNT.calls += 1
        try:
            resp = await self._inner.create(*args, **kwargs)
        except Exception:
            COUNT.errors += 1
            raise
        u = getattr(resp, "usage", None)
        if u is not None:
            COUNT.prompt += u.prompt_tokens or 0
            COUNT.completion += u.completion_tokens or 0
            COUNT.cache_hit += getattr(u, "prompt_cache_hit_tokens", 0) or 0
        return resp


class _Chat:
    def __init__(self, inner):
        self.completions = _Completions(inner.completions)


class CountingClient:
    """Looks like AsyncOpenAI to OpenAIGenericClient (it only uses chat.completions.create)."""

    def __init__(self, inner):
        self.chat = _Chat(inner.chat)


class PassThroughReranker(CrossEncoderClient):
    """Graphiti's default reranker is an OpenAI model. search() (the default recipe) never calls it;
    it is only needed to construct Graphiti without an OpenAI key. Keeps the input order."""

    async def rank(self, query, passages):
        return [(p, 1.0 / (i + 1)) for i, p in enumerate(passages)]


def balance():
    req = urllib.request.Request("https://api.deepseek.com/user/balance",
                                 headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        info = json.load(r)["balance_infos"]
    return float([b for b in info if b["currency"] == "CNY"][0]["total_balance"])


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def est_cost(prompt, completion):
    return (prompt * PRICE_IN + completion * PRICE_OUT) / 1e6


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"done": [], "uuid": {}, "failed": [], "seconds": 0.0, "calls": 0, "errors": 0, "prompt": 0,
            "completion": 0, "cache_hit": 0, "per_episode": {}, "balance_start": None, "budget_stop": False}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def build():
    llm_inner = AsyncOpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=LLM_BASE)
    # DeepSeek has JSON mode but not json_schema: the client variant for generic providers has a
    # documented json_object mode. max_tokens 8192 is DeepSeek's output cap (Graphiti default is 16384).
    llm = OpenAIGenericClient(
        config=LLMConfig(api_key=os.environ["DEEPSEEK_API_KEY"], model=LLM_MODEL, small_model=LLM_MODEL,
                         base_url=LLM_BASE),
        client=CountingClient(llm_inner), max_tokens=8192, structured_output_mode="json_object")
    emb = OpenAIEmbedder(OpenAIEmbedderConfig(api_key="none", base_url=EMB_BASE, embedding_model=EMB_MODEL,
                                              embedding_dim=1024))
    driver = KuzuDriver(db=DB)
    return Graphiti(graph_driver=driver, llm_client=llm, embedder=emb, cross_encoder=PassThroughReranker())


def priority_ids(queries):
    prio = set()
    for q in queries.values():
        prio |= set(q.get("expect") or []) | set(q.get("stale") or [])
    return prio


def target_ids(memories, queries, extras):
    order = sorted(memories, key=lambda i: (memories[i]["date"], i))
    prio = priority_ids(queries)
    if extras == "all":
        return order, prio
    rest = [i for i in order if i not in prio][:int(extras)]
    chosen = prio | set(rest)
    return [i for i in order if i in chosen], prio


def ref_time(date):
    return datetime.fromisoformat(date).replace(tzinfo=timezone.utc)


async def ingest(g, memories, ids, st, limit_new):
    new = 0
    for mid in ids:
        if mid in st["done"] or mid in st["failed"]:
            continue
        if limit_new is not None and new >= limit_new:
            break
        if est_cost(st["prompt"] + COUNT.prompt, st["completion"] + COUNT.completion) >= BUDGET_CNY * 0.97:
            st["budget_stop"] = True
            print("budget stop before", mid, flush=True)
            break
        m = memories[mid]
        c0 = (COUNT.calls, COUNT.prompt, COUNT.completion)
        t0 = time.perf_counter()
        try:
            res = await g.add_episode(name=mid, episode_body=m["text"], source_description="personal memory",
                                      reference_time=ref_time(m["date"]), source=EpisodeType.text)
            st["uuid"][res.episode.uuid] = mid
            st["done"].append(mid)
        except Exception as e:  # keep going; the failure is counted and reported
            st["failed"].append(mid)
            print("FAILED", mid, type(e).__name__, str(e)[:120].replace(m["text"], "<text>"), flush=True)
        dt = time.perf_counter() - t0
        st["seconds"] += dt
        st["per_episode"][mid] = {"s": round(dt, 2), "calls": COUNT.calls - c0[0],
                                  "in": COUNT.prompt - c0[1], "out": COUNT.completion - c0[2]}
        new += 1
        # fold the counters into the persisted state
        for k, v in (("calls", COUNT.calls), ("errors", COUNT.errors), ("prompt", COUNT.prompt),
                     ("completion", COUNT.completion), ("cache_hit", COUNT.cache_hit)):
            st[k] += v
        COUNT.calls = COUNT.errors = COUNT.prompt = COUNT.completion = COUNT.cache_hit = 0
        save_state(st)
        print("%s done=%d fail=%d calls=%d in=%d out=%d est=%.3f CNY last=%.1fs" % (
            mid, len(st["done"]), len(st["failed"]), st["calls"], st["prompt"], st["completion"],
            est_cost(st["prompt"], st["completion"]), dt), flush=True)


def earliest_memory(edge, uuid2mid, memories):
    """Rule: an edge maps to the memory of the earliest (by memory date) episode listed in edge.episodes,
    i.e. the episode that first extracted the fact; unmappable (no episode known) -> None."""
    cands = [uuid2mid[u] for u in (edge.episodes or []) if u in uuid2mid]
    if not cands:
        return None
    return min(cands, key=lambda i: (memories[i]["date"], i))


async def answer(g, memories, queries, st, out_path, bal0):
    uuid2mid = st["uuid"]
    await g.search(WARMUP_QUERY)  # one warm-up query (not from queries.json)
    answers, fails = {}, 0
    diag = {"invalid_returned": 0, "edges_returned": 0, "updated": {}}
    for qid, q in queries.items():
        t0 = time.perf_counter()
        try:
            edges = await g.search(q["query"])  # default recipe, default limit (10)
        except Exception as e:
            fails += 1
            print("query failed", qid, type(e).__name__, flush=True)
            answers[qid] = {"ranked": [], "deployed": [], "ms": 0.0}
            continue
        ms = (time.perf_counter() - t0) * 1000.0
        items, seen = [], set()
        for pos, e in enumerate(edges[:TOP]):
            mid = earliest_memory(e, uuid2mid, memories)
            diag["edges_returned"] += 1
            if e.invalid_at is not None or e.expired_at is not None:
                diag["invalid_returned"] += 1
            if mid is not None and mid in seen:
                continue  # a memory may appear once: keep its best (first) position
            if mid is not None:
                seen.add(mid)
            # search() returns no scores: a descending rank-based number keeps the order for the scorer
            items.append({"id": mid, "score": round(1.0 / (pos + 1), 4)})
        answers[qid] = {"ranked": items, "deployed": list(items), "ms": round(ms, 3)}
        if q.get("type") == "updated":
            diag["updated"][qid] = {
                "returned": [{"mem": earliest_memory(e, uuid2mid, memories),
                              "invalid": e.invalid_at is not None or e.expired_at is not None}
                             for e in edges[:TOP]],
                "expect": q["expect"], "stale": q.get("stale")}

    stats = {}
    try:
        recs, _, _ = await g.driver.execute_query(
            "MATCH (e:RelatesToNode_) RETURN count(e) AS n, "
            "sum(CASE WHEN e.invalid_at IS NOT NULL THEN 1 ELSE 0 END) AS inv")
        stats["entity_edges"] = recs[0]["n"]
        stats["edges_with_invalid_at"] = int(recs[0]["inv"])
        recs, _, _ = await g.driver.execute_query("MATCH (n:Entity) RETURN count(n) AS n")
        stats["entity_nodes"] = recs[0]["n"]
        recs, _, _ = await g.driver.execute_query("MATCH (n:Episodic) RETURN count(n) AS n")
        stats["episodes"] = recs[0]["n"]
    except Exception as e:
        stats["error"] = type(e).__name__

    bal1 = balance()
    n_done, n_all = len(st["done"]), len(memories)
    n_prio = len(priority_ids(queries))
    cost_bal = round(bal0 - bal1 + PRIOR_SPEND, 4) if bal0 is not None else None
    cost_est = round(est_cost(st["prompt"], st["completion"]), 4)
    tokens = st["prompt"] + st["completion"]
    with open("/db/diag.txt", "w", encoding="utf-8") as f:
        json.dump({"diag": diag, "stats": stats}, f, ensure_ascii=False, indent=1, default=str)
    notes = [
        "INGESTED %d of %d memories as episodes (name = memory id); failed episodes: %d; not ingested: %d. "
        "If this is below %d it is a SUBSET: the %d memories that are expect/stale for some question were taken "
        "first, the remaining ones in date order while the 5 CNY budget lasted. The scorer still scores all 160 "
        "questions, so questions whose memories were not ingested cannot be answered." % (
            n_done, n_all, len(st["failed"]), n_all - n_done - len(st["failed"]), n_all, n_prio),
        "Failed episode ids: %s" % (",".join(st["failed"]) or "none"),
        "ranked and deployed are the SAME call: Graphiti.search(query) = recipe "
        "EDGE_HYBRID_SEARCH_RRF (BM25 + cosine over facts, RRF), default limit 10. The recipe applies its own default cosine floor "
        "(sim_min_score 0.6, search_utils.DEFAULT_MIN_SCORE) and has no token budget, so deployed = ranked = its default result and ms times that call "
        "(1 embedding request + Kuzu queries, no LLM).",
        "Returned items are fact edges. Mapping: an edge maps to the memory of the earliest episode in "
        "edge.episodes (by memory date); several edges from one memory keep the best position; unmappable -> null. "
        "search() returns no scores, so score = 1/rank (order only).",
        "Invalidated facts: of %d edges returned over all questions, %d had invalid_at/expired_at set "
        "(search() does not filter superseded edges)." % (diag["edges_returned"], diag["invalid_returned"]),
        "Graph at the end: %s" % json.dumps(stats),
        "LLM: deepseek-flash via OpenAIGenericClient(structured_output_mode='json_object', max_tokens=8192); "
        "embedder bge-m3 1024d; cross-encoder = pass-through stub (Graphiti default needs an OpenAI model; "
        "unused by search()). Kuzu embedded driver (deprecated upstream, still shipped).",
        "LLM calls: %d (%d errored), prompt tokens %d (cache hit %d), completion tokens %d. "
        "llm_cost_cny is the DeepSeek balance difference incl. %.2f CNY of earlier debugging runs (%s; other workers may have spent concurrently); "
        "token-based estimate at assumed prices (%.1f in / %.1f out CNY per M): %s." % (
            st["calls"], st["errors"], st["prompt"], st["cache_hit"], st["completion"], PRIOR_SPEND, cost_bal,
            PRICE_IN, PRICE_OUT, cost_est),
        "Budget stop reached: %s. Query failures: %d. ingest_seconds sums add_episode wall-clock over %d "
        "episodes (the graph grows during ingest)." % (st["budget_stop"], fails, n_done),
        "Every episode is text-typed (EpisodeType.text), default group (no group_id: KuzuDriver in 0.31.2 lacks the _database attribute that an explicit group_id needs), reference_time = memory date 00:00 UTC; "
        "only text, date and id were given to the system.",
    ]
    result = {
        "system": "graphiti", "version": "graphiti-core 0.31.2",
        "mode": "LLM extraction (deepseek-flash, JSON mode) of entities and temporal facts into embedded Kuzu; "
                "bge-m3 embeddings; %d of %d memories ingested" % (n_done, n_all),
        "how": "Graphiti(graph_driver=KuzuDriver, llm_client=OpenAIGenericClient(json_object), "
               "embedder=OpenAIEmbedder(bge-m3)).add_episode(name=id, episode_body=text, reference_time=date, "
               "default group (no group_id)) per memory; Graphiti.search(query) (EDGE_HYBRID_SEARCH_RRF, "
               "limit 10) for ranked and deployed",
        "ingest_seconds": round(st["seconds"], 2), "llm_tokens": tokens,
        "llm_cost_cny": cost_bal if cost_bal is not None else cost_est,
        "answers": answers, "notes": notes,
    }
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("wrote", out_path, flush=True)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/out/graphiti.json")
    ap.add_argument("--limit-new", type=int, default=None)
    ap.add_argument("--extras", default="all")
    ap.add_argument("--answer", action="store_true")
    a = ap.parse_args()
    memories, queries = load("memories.json"), load("queries.json")
    ids, prio = target_ids(memories, queries, a.extras)
    print("target episodes:", len(ids), "priority:", len(prio), flush=True)
    st = load_state()
    if st["balance_start"] is None:
        st["balance_start"] = balance()
        save_state(st)
    print("balance start (CNY):", st["balance_start"], flush=True)
    g = build()
    if not st.get("indices"):
        # Graphiti.build_indices_and_constraints() is a documented no-op on the Kuzu driver in 0.31.2
        # (its comment says setup_schema() makes the full-text indexes, but it does not), and search then
        # fails with "Table RelatesToNode_ doesn't have an index". The Kuzu driver's own maintenance
        # operation does create them; it is called here once, before the first episode.
        await g.driver.graph_ops.build_indices_and_constraints(g.driver)
        st["indices"] = True
        save_state(st)
    try:
        await ingest(g, memories, ids, st, a.limit_new)
        print("balance now:", balance(), flush=True)
        if a.answer:
            await answer(g, memories, queries, st, a.out, st["balance_start"])
    finally:
        await g.close()


if __name__ == "__main__":
    asyncio.run(main())
