# Graphiti adapter

**System**: Graphiti (`graphiti-core` on PyPI, github.com/getzep/graphiti, Apache-2.0), the temporal knowledge-graph
engine behind Zep. Each episode is run through an LLM that extracts entities and time-stamped facts (edges);
facts contradicted by newer episodes get `invalid_at`/`expired_at`; search is hybrid (embeddings + BM25 + graph).
It cannot ingest anything without an LLM.

**Version**: `graphiti-core[kuzu]==0.31.2` (pinned in `Dockerfile`), plus `httpx` (see below). Python 3.12-slim.
**Graph DB**: embedded Kuzu 0.11.3 (the driver is flagged deprecated upstream), a file in docker volume
`fmg-bench-graphiti-db` (removed after the final run). No database server.
**LLM**: `deepseek-flash`, base `https://api.deepseek.com/v1`, via `OpenAIGenericClient(structured_output_mode=
'json_object', max_tokens=8192)` (the client for generic OpenAI-compatible providers; json_object mode is its
documented option for DeepSeek). **Embedder**: `OpenAIEmbedder` -> `http://fmg-bench-embedder:11434/v1`, `bge-m3`, 1024 d.
**Reranker**: Graphiti's default cross-encoder is an OpenAI model; the adapter passes a pass-through stub
(`PassThroughReranker`). The default `search()` recipe does not use a cross-encoder, so the stub is never called.

## What is called
- Ingest: `Graphiti.add_episode(name=<memory id>, episode_body=<text>, source_description="personal memory",
  reference_time=<memory date, 00:00 UTC>, source=EpisodeType.text)` once per memory, 240 episodes in date order,
  default group. Only text, date and id are given. Episode uuid -> memory id is kept by the adapter.
- Search (ranked and deployed, one call per question): `Graphiti.search(query)` = recipe `EDGE_HYBRID_SEARCH_RRF`
  (BM25 + cosine over fact edges, RRF rerank), default limit 10. It has its own default cosine floor
  (`sim_min_score` 0.6) and no token budget; there is nothing else to call "deployed", so both lists are the same
  result and `ms` times that one call (query embedding + Kuzu queries, no LLM). One warm-up query before timing.
- Why fair: `search()` is Graphiti's documented out-of-the-box search; `search_()` with the cross-encoder recipe was
  not used (needs a reranker model and returns nodes/episodes the brief did not ask for).

## Mapping rule
An edge maps to the memory of the earliest (by memory date) episode listed in `edge.episodes`; several edges from one
memory keep the best position; no known episode -> `id: null`. `search()` returns no scores, so `score = 1/rank`.

## Result (all 240 memories ingested, no subset needed)
Score row (`python bench/score.py`, columns Hit@1 / Hit@5 / MRR@10 / Recall@5 / deployed hit / noise / avg items /
quiet rate / fresh-first / p50 ms / p95 ms / ingest s / tokens / CNY):
`graphiti | 12.5% | 13.3% | 0.129 | 13.3% | 13.3% | 53.5% | 0.31 | 82.5% | 5.0% | 391 | 848 | 1346.7 | 1176519 | 2.00`
(latency is noisy: other adapters ran on the machine). Per type Hit@5: lexical 24.4%, paraphrase 5.5%, updated 10%.

- Ingest: 240/240 episodes, 0 failed, 596 LLM calls (about 2.5 per episode), 1,028,457 prompt tokens (151,040 cache
  hits) + 148,062 completion tokens, 1346.7 s (5.6 s per episode, rising slowly as the graph grows).
- Cost: DeepSeek balance 79.58 -> 77.72 = 1.86 CNY for the final run plus 0.12 CNY of earlier debugging runs = 1.98
  (reported 2.00 in the summary after rounding in the scorer; other workers may have spent in the same window, so
  treat it as an upper bound). Budget was 5 CNY. About 0.008 CNY per memory.
- Graph afterwards: 240 episodes, 125 entity nodes, only **69 fact edges**, 1 of them invalidated.
- Only 31 of 160 questions got any result: most memories are one-sentence statements about "the owner" (a pronoun-like
  subject Graphiti does not turn into an entity), so extraction yields no entity or no second entity, hence no edge,
  and `search()` returns edges only. The episode text itself is stored but not returned by `search()`.
- Invalidated facts: search() does not filter them. 1 of 58 returned edges was invalidated (question q116, where the
  stale memory m048 was returned flagged invalid and ranked first, above the newer m196). Of the 20 "updated"
  questions, only 5 returned anything.

## Deviations and caveats
- `httpx` is installed explicitly: unpinned `openai` resolves to 3.x, which no longer depends on httpx, while
  graphiti-core 0.31.2 imports it (import error otherwise).
- Explicit `group_id` crashes on the Kuzu driver (`'KuzuDriver' has no attribute '_database'`), so the default group is used.
- `Graphiti.build_indices_and_constraints()` is a no-op on the Kuzu driver, and search then fails (missing full-text
  index). The adapter calls the driver's own `graph_ops.build_indices_and_constraints(driver)` once before ingest.
  Both are configuration/call-path workarounds; Graphiti's code was not modified. The first attempt (without the index)
  was discarded after 16 episodes.
- Kuzu's default full-text tokenizer does not segment Chinese, so the BM25 half of the hybrid search contributes
  little on this data; only the embedding half matters. Not tuned.
- 0.12 CNY of the cost is the aborted/debugging runs (probe of 8 episodes, the run without indexes, a 6-episode trace).
- Query-time ms includes a cold-ish embedder shared with other jobs.

## Reproduce
```
bash bench/adapters/graphiti/build.sh     # image fmg-bench-graphiti (internet needed for pip)
bash bench/adapters/graphiti/run.sh       # ingest (resumes from the volume if present) + answer + score
```
`run.sh` ingests from scratch when the volume `fmg-bench-graphiti-db` does not exist, which **spends about 2 CNY** and
takes about 25 minutes; set `KEEP_DB=1` to keep the volume for free re-runs of the answering phase (`FRESH=1` wipes it).
It needs the network `fmg-bench` (embedder) and the key env file; the container gets internet through the default
bridge and is then connected to `fmg-bench`. Set `PRIOR_SPEND_CNY` only to account for earlier runs.
