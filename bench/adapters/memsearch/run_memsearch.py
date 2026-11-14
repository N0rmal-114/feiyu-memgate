"""memsearch adapter: Markdown memory files, `memsearch index` CLI for ingest, MemSearch.search for queries.

Runs inside the container (see run.sh). Contract: bench/CONTRACT.md.

Layout (the one memsearch documents for memory notes): <dir>/memory/YYYY-MM-DD.md daily files, one
`### HH:MM` section per note with an HTML anchor comment under the heading (the plugin's own
`<!-- session:.. -->` anchor style). The anchor `<!-- id:m001 -->` carries the memory id; memsearch strips
HTML comments before embedding but keeps them in the stored chunk content, so the id is read back from the
returned chunk. The HH:MM value is a per-day sequence (00:01, 00:02, ...), not a real time.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from importlib import metadata

EMBED_URL = os.environ.get("BENCH_EMBED_URL", "http://fmg-bench-embedder:11434/v1")
MODEL = "bge-m3"
FAKE_KEY = "bench-only-fake-key"  # the endpoint ignores it; the openai SDK insists on one
WARMUP = "这是一个用来热身的问题"
ID_RE = re.compile(r"<!--\s*id:(m\d+)\s*-->")


def write_markdown(memories, root):
    """One file per date, one section per memory, memories in id (= date) order."""
    mem_dir = os.path.join(root, "memory")
    os.makedirs(mem_dir, exist_ok=True)
    by_day = {}
    for mid, m in sorted(memories.items(), key=lambda kv: (kv[1]["date"], kv[0])):
        by_day.setdefault(m["date"], []).append((mid, m["text"]))
    for day, items in by_day.items():
        parts = []
        for n, (mid, text) in enumerate(items, 1):
            parts.append("### 00:%02d\n<!-- id:%s -->\n%s\n" % (n, mid, text))
        with open(os.path.join(mem_dir, day + ".md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(parts))
    return mem_dir


def embed_flags():
    return ["--provider", "openai", "--model", MODEL, "--base-url", EMBED_URL, "--api-key", FAKE_KEY]


def to_items(hits):
    """Map chunks to memory ids; a memory appears once, at its best position; unmapped -> null."""
    out, seen, unmapped = [], set(), 0
    for h in hits:
        ids = ID_RE.findall(h.get("content", ""))
        mid = ids[0] if len(ids) == 1 else None
        if mid is None:
            unmapped += 1
        elif mid in seen:
            continue
        else:
            seen.add(mid)
        out.append({"id": mid, "score": round(float(h.get("score", 0.0)), 4)})
    return out, unmapped


async def main_async(args):
    from memsearch.core import MemSearch

    with open(os.path.join(args.data, "memories.json"), encoding="utf-8") as f:
        memories = json.load(f)
    with open(os.path.join(args.data, "queries.json"), encoding="utf-8") as f:
        queries = json.load(f)

    root = os.path.join(args.work, "notes")
    db = os.path.join(args.work, "milvus.db")
    t0 = time.perf_counter()
    mem_dir = write_markdown(memories, root)
    # The documented ingest entry point: `memsearch index <paths>` (hybrid dense + BM25 collection).
    cp = subprocess.run(["memsearch", "index", mem_dir, "--milvus-uri", db] + embed_flags(),
                        capture_output=True, text=True)
    ingest_seconds = time.perf_counter() - t0
    sys.stderr.write(cp.stdout[-500:] + cp.stderr[-500:])
    if cp.returncode != 0:
        raise SystemExit("memsearch index failed with code %d" % cp.returncode)

    ms = MemSearch([mem_dir], embedding_provider="openai", embedding_model=MODEL,
                   embedding_base_url=EMBED_URL, embedding_api_key=FAKE_KEY, milvus_uri=db)
    await ms.search(WARMUP, top_k=5)  # one warm-up query before timing

    answers, failures, unmapped = {}, 0, 0
    for qid, q in queries.items():
        try:
            ranked_hits = await ms.search(q["query"], top_k=10)
            t1 = time.perf_counter()
            dep_hits = await ms.search(q["query"], top_k=5)  # the memory-recall skill: --top-k 5
            ms_ = (time.perf_counter() - t1) * 1000.0
            ranked, u1 = to_items(ranked_hits)
            deployed, u2 = to_items(dep_hits)
            unmapped += u1 + u2
        except Exception as exc:  # contract rule 6: empty lists + counted failure
            failures += 1
            sys.stderr.write("query %s failed: %s\n" % (qid, type(exc).__name__))
            ranked, deployed, ms_ = [], [], 0.0
        answers[qid] = {"ranked": ranked, "deployed": deployed, "ms": round(ms_, 3)}

    version = metadata.version("memsearch")
    notes = [
        "Installed from the scanned source checkout (commit 369ae1da48b7, pyproject version %s) with pip inside the image build." % version,
        "Ingest: memories written as Markdown, memory/YYYY-MM-DD.md, one '### 00:NN' section per memory plus an "
        "'<!-- id:mNNN -->' anchor line; indexed with the CLI `memsearch index` (default chunking: 1500 chars, split on headings). "
        "Every chunk therefore holds exactly one memory; the id is read from the anchor in the returned chunk content. "
        "The HH:MM heading values are a per-day sequence, not real times. The memory text is verbatim; no source/kind fields used.",
        "ingest_seconds covers writing the Markdown files plus the `memsearch index` subprocess (python start-up, embedding via the shared bge-m3 endpoint, Milvus Lite insert).",
        "Embeddings: provider 'openai' with base_url http://fmg-bench-embedder:11434/v1, model bge-m3 (memsearch's documented OpenAI-compatible setting). Dimension auto-detected (1024).",
        "Search is memsearch's default hybrid retrieval (dense COSINE + BM25 fused by RRF k=60, score normalised to 0..1); no reranker (default off), no threshold exists in the library.",
        "ranked = MemSearch.search(query, top_k=10). deployed = MemSearch.search(query, top_k=5): the DSH plugin has no automatic injection (it only adds a reminder to use the memory-recall skill); that skill runs `memsearch search \"<q>\" --top-k 5 --json-output`, with no score threshold and no character budget. The CLI calls the same MemSearch.search; it was called in-process so `ms` excludes CLI process start-up (a few seconds of python/pymilvus import per CLI call in real use).",
        "In the real plugin the agent decides whether to search and writes its own query text; here the benchmark question is passed verbatim.",
        "Not used (default off / needs an LLM): compact/summarise, plugin turn capture, maintenance tasks, reranker, file watcher.",
        "%d chunk(s) could not be mapped to a memory id; %d query failure(s)." % (unmapped, failures),
    ]
    result = {
        "system": "memsearch", "version": version,
        "mode": "verbatim Markdown ingest (memory/YYYY-MM-DD.md), hybrid dense+BM25 on Milvus Lite, bge-m3 embeddings via OpenAI-compatible endpoint, no LLM, no reranker",
        "how": "CLI `memsearch index <memory dir> --provider openai --model bge-m3 --base-url <embedder>/v1` for ingest; "
               "memsearch.core.MemSearch.search(query, top_k=10) for ranked; MemSearch.search(query, top_k=5) for deployed "
               "(the DSH memory-recall skill's `memsearch search --top-k 5`); chunk -> memory id via the '<!-- id:mNNN -->' anchor in chunk content",
        "ingest_seconds": round(ingest_seconds, 3), "llm_tokens": 0, "llm_cost_cny": 0.0,
        "answers": answers, "notes": notes,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("wrote %s (ingest %.1fs, failures %d, unmapped %d)" % (args.out, ingest_seconds, failures, unmapped))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--out", default="/out/memsearch.json")
    ap.add_argument("--work", default="/tmp/ms")
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
