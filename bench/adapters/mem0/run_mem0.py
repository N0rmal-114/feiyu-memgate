"""Benchmark adapter for mem0 (PyPI `mem0ai`), run inside the fmg-bench-mem0 container.

    python run_mem0.py --mode verbatim --out /out/mem0-verbatim.json
    python run_mem0.py --mode infer    --out /out/mem0.json [--limit N] [--budget-cny 3]

Contract: bench/CONTRACT.md. Two modes:
- verbatim: Memory.add(text, infer=False): no LLM, text stored as is and only embedded (isolates retrieval).
- infer:    Memory.add(text, infer=True) with the DeepSeek LLM: mem0 extracts facts (its normal operation).

Both: embeddings from the shared bge-m3 endpoint (OpenAI-compatible, 1024 dims), local on-disk Qdrant,
retrieval through Memory.search (mem0's hybrid semantic + BM25 + entity scoring). Each stored item carries
metadata source_id=<memory id>, read back from the search result to map items to source memories.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

WARMUP_QUERY = "this is a warm-up question"
USER = "bench"
FAKE_KEY = "bench-only-fake-key-0000000000000000"  # embedder ignores it; verbatim mode never calls the LLM
LLM_MODEL = "deepseek-flash"
TOP = 10


def load(data_dir, name):
    with open(os.path.join(data_dir, name), encoding="utf-8") as f:
        return json.load(f)


def build_config(args, infer):
    llm_key = os.environ.get("DEEPSEEK_API_KEY") if infer else None
    return {
        "embedder": {"provider": "openai", "config": {
            "model": "bge-m3", "embedding_dims": 1024,
            "openai_base_url": args.embedder.rstrip("/") + "/v1", "api_key": FAKE_KEY}},
        "vector_store": {"provider": "qdrant", "config": {
            "collection_name": "fmg_bench", "embedding_model_dims": 1024,
            "path": "/tmp/qdrant", "on_disk": True}},
        "llm": {"provider": "deepseek", "config": {
            "model": LLM_MODEL, "api_key": llm_key or FAKE_KEY,
            "deepseek_base_url": "https://api.deepseek.com"}},
        "history_db_path": "/tmp/mem0-history.db",
    }


def balance():
    """CNY balance of the DeepSeek account (None when unavailable). Never prints the key."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    req = urllib.request.Request("https://api.deepseek.com/user/balance",
                                 headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            infos = json.load(r)["balance_infos"]
        return float([i for i in infos if i["currency"] == "CNY"][0]["total_balance"])
    except Exception:  # noqa: BLE001 - the balance is optional; None when it cannot be read
        return None


class Usage:
    """Counts the tokens of the real LLM calls by wrapping the OpenAI client call mem0 makes."""

    def __init__(self):
        self.prompt = self.completion = self.cached = self.calls = 0

    def wrap(self, memory):
        create = memory.llm.client.chat.completions.create

        def counted(*a, **kw):
            resp = create(*a, **kw)
            u = getattr(resp, "usage", None)
            if u is not None:
                self.prompt += getattr(u, "prompt_tokens", 0) or 0
                self.completion += getattr(u, "completion_tokens", 0) or 0
                self.cached += getattr(u, "prompt_cache_hit_tokens", 0) or 0
            self.calls += 1
            return resp

        memory.llm.client.chat.completions.create = counted

    @property
    def total(self):
        return self.prompt + self.completion


def source_of(hit):
    meta = hit.get("metadata") or {}
    return meta.get("source_id")


def to_items(hits):
    """Map search hits to contract items: id = source memory, best hit per memory, others null."""
    items, seen = [], set()
    for h in hits:
        mid = source_of(h)
        if mid is not None:
            if mid in seen:
                continue
            seen.add(mid)
        items.append({"id": mid, "score": round(float(h.get("score", 0.0)), 4)})
    return items


def est_cost(usage, args):
    if args.rate_in is None or args.rate_out is None:
        return None
    cached_rate = args.rate_in_cached if args.rate_in_cached is not None else args.rate_in
    return ((usage.prompt - usage.cached) * args.rate_in + usage.cached * cached_rate
            + usage.completion * args.rate_out) / 1e6


def ingest(memory, memories, infer, args, usage):
    """Add each memory once in date order. Returns (added_ids, failures, stopped_reason, events)."""
    order = sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0]))
    if args.limit:
        order = order[:args.limit]
    start_bal = balance() if infer else None
    done, failures, stopped, events = [], [], None, {}
    for n, (mid, m) in enumerate(order):
        if infer and args.budget_cny:
            spent = est_cost(usage, args)
            if spent is None:
                b = balance()
                spent = None if (b is None or start_bal is None) else start_bal - b
            if spent is not None and spent >= args.budget_cny:
                stopped = "budget %.2f CNY reached after %d memories" % (args.budget_cny, n)
                break
        try:
            res = memory.add(m["text"], user_id=USER, infer=infer,
                             metadata={"source_id": mid, "date": m["date"]})
            for r in res.get("results", []):
                events[r.get("event", "?")] = events.get(r.get("event", "?"), 0) + 1
            done.append(mid)
        except Exception as e:  # noqa: BLE001 - keep going; counted and reported
            failures.append("%s: %s" % (mid, type(e).__name__))
    return done, failures, stopped, events


def stored_facts(memory):
    """Every stored item with its metadata (get_all, no cap below the store size)."""
    return memory.get_all(filters={"user_id": USER}, top_k=100000).get("results", [])


def run(args):
    infer = args.mode == "infer"
    from mem0 import Memory
    from importlib.metadata import version as pkg_version
    ver = pkg_version("mem0ai")
    memories, queries = load(args.data, "memories.json"), load(args.data, "queries.json")
    memory = Memory.from_config(build_config(args, infer))
    usage = Usage()
    if infer:
        usage.wrap(memory)
    bal0 = balance() if infer else None

    t0 = time.perf_counter()
    done, failures, stopped, events = ingest(memory, memories, infer, args, usage)
    ingest_seconds = time.perf_counter() - t0
    bal1 = balance() if infer else None

    facts = stored_facts(memory)
    by_source = {}
    for f in facts:
        by_source.setdefault(source_of(f), []).append(f.get("memory", ""))
    no_fact = [mid for mid in done if mid not in by_source]
    if args.dry:
        print(json.dumps(dry_report(done, failures, events, facts, by_source, usage, bal0, bal1, ingest_seconds),
                         ensure_ascii=False, indent=1))
        return None

    try:
        memory.search(WARMUP_QUERY, filters={"user_id": USER}, top_k=TOP)  # warm-up, not timed
    except Exception:  # noqa: BLE001 - the warm-up is not timed and not needed
        pass
    answers, qfail = {}, 0
    for qid, q in queries.items():
        try:
            ranked = to_items(memory.search(q["query"], filters={"user_id": USER},
                                            top_k=TOP, threshold=0.0)["results"])
            t1 = time.perf_counter()
            deployed_hits = memory.search(q["query"], filters={"user_id": USER})["results"]
            ms = (time.perf_counter() - t1) * 1000.0
            deployed = to_items(deployed_hits)
        except Exception:  # noqa: BLE001 - a failed query is counted and answered with empty lists
            qfail += 1
            ranked, deployed, ms = [], [], 0.0
        answers[qid] = {"ranked": ranked, "deployed": deployed, "ms": round(ms, 3)}

    cost = round(bal0 - bal1, 4) if (bal0 is not None and bal1 is not None) else 0.0
    notes = notes_for(infer, ver, done, failures, stopped, events, facts, no_fact,
                      len(by_source.get(None, [])), usage, qfail, memories)
    result = {
        "system": "mem0" if infer else "mem0-verbatim", "version": ver,
        "mode": ("add(infer=True) with DeepSeek deepseek-flash fact extraction, " if infer
                 else "add(infer=False) verbatim, no LLM, ") + "bge-m3 1024-d embeddings, local on-disk Qdrant, "
                "mem0 hybrid search (semantic + BM25 + entity), default threshold/limit for deployed",
        "how": "Memory.from_config(embedder=openai-compatible bge-m3, vector_store=qdrant path, llm=deepseek); "
               "Memory.add(text, user_id='bench', infer=%s, metadata={'source_id': id, 'date': date}) per memory; "
               "Memory.search(q, filters={'user_id':'bench'}, top_k=10, threshold=0.0) for ranked; "
               "Memory.search(q, filters={'user_id':'bench'}) with mem0 defaults (top_k=20, threshold=0.1) "
               "for deployed" % infer,
        "ingest_seconds": round(ingest_seconds, 3),
        "llm_tokens": usage.total if infer else 0,
        "llm_cost_cny": cost if infer else 0.0,
        "answers": answers, "notes": notes,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return args.out


def notes_for(infer, ver, done, failures, stopped, events, facts, no_fact, unmapped, usage, qfail, memories):
    n = [
        "mem0ai %s; extras: mem0ai[nlp] (spaCy en_core_web_sm for BM25 lemmatization and entity extraction, "
        "an English-only model applied to Chinese text) and fastembed Qdrant/bm25 sparse vectors, as the docs "
        "describe for hybrid search." % ver,
        "Each memory was added as one user message; metadata source_id=<memory id> and date were attached. "
        "No source/kind fields were given to mem0.",
        "ranked: search top_k=10 threshold=0.0 (no threshold); deployed: search with mem0 defaults "
        "(top_k=20, threshold=0.1, no rerank); ms times the deployed call only. Items are mapped to source "
        "memories via the source_id metadata of the hit; one item per memory (best score kept).",
        "%d of %d memories ingested; %d add failure(s)%s." % (len(done), len(memories), len(failures),
                                                           ("; first: " + failures[0]) if failures else ""),
        "%d search question(s) failed." % qfail,
    ]
    if not infer:
        n.append("infer=False path: no entity linking happens at write time, so the entity-boost part of "
                 "mem0's scoring has no data; this is mem0's real behaviour for infer=False.")
    else:
        n.append("Stored facts: %d from %d source memories; events %s. Source memories with no surviving fact: "
                 "%d. Facts without source_id: %d." % (len(facts), len(done), json.dumps(events), len(no_fact),
                                                       unmapped))
        n.append("mem0ai 2.x extraction is additive (ADD only, hash dedup); its add path has no update/delete "
                 "step. LLM tokens counted from the API usage fields: prompt %d (cached %d), completion %d over "
                 "%d call(s); llm_cost_cny is the DeepSeek balance difference (other workers may have spent in "
                 "the same window)." % (usage.prompt, usage.cached, usage.completion, usage.calls))
        if stopped:
            n.append("STOPPED EARLY: " + stopped)
    return n


def dry_report(done, failures, events, facts, by_source, usage, bal0, bal1, secs):
    sample = [{"source": s, "facts": t} for s, t in list(by_source.items())[:8]]
    return {
        "added": len(done), "failures": failures, "events": events, "stored_facts": len(facts),
        "facts_without_source_id": len(by_source.get(None, [])),
        "sources_with_no_fact": [m for m in done if m not in by_source],
        "tokens": {"prompt": usage.prompt, "cached": usage.cached, "completion": usage.completion,
                   "calls": usage.calls},
        "balance_before": bal0, "balance_after": bal1,
        "cost_cny": None if bal0 is None or bal1 is None else round(bal0 - bal1, 4),
        "seconds": round(secs, 1), "sample": sample,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["verbatim", "infer"], required=True)
    ap.add_argument("--data", default="/data")
    ap.add_argument("--out", default="/out/mem0.json")
    ap.add_argument("--embedder", default="http://fmg-bench-embedder:11434")
    ap.add_argument("--limit", type=int, default=0, help="ingest only the first N memories")
    ap.add_argument("--dry", action="store_true", help="ingest only, print a spend report, no questions")
    ap.add_argument("--budget-cny", type=float, default=0.0)
    ap.add_argument("--rate-in", type=float, default=None, help="CNY per 1M uncached input tokens")
    ap.add_argument("--rate-in-cached", type=float, default=None)
    ap.add_argument("--rate-out", type=float, default=None, help="CNY per 1M output tokens")
    args = ap.parse_args()
    out = run(args)
    if out:
        print("wrote " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
