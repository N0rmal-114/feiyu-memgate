# mem0 adapter

[mem0](https://github.com/mem0ai/mem0) (PyPI `mem0ai`) is a general-purpose memory layer for LLM agents.
Version used: **mem0ai 2.2.1** (current PyPI release when run), with `qdrant-client 1.20.0`, `spacy 3.8.18`
+ `en_core_web_sm`, `fastembed 0.9.1` (see `/opt/versions.txt` in the image).

## What is called

`run_mem0.py` uses the public `mem0.Memory` API in-process, inside the container:

- `Memory.from_config`: embedder `openai` provider pointed at the shared endpoint
  (`http://fmg-bench-embedder:11434/v1`, model `bge-m3`, `embedding_dims=1024`); vector store `qdrant` in local
  on-disk mode (`path=/tmp/qdrant`, no server); LLM `deepseek` provider (`deepseek-flash`, DeepSeek defaults for
  temperature etc.); history db in `/tmp`.
- ingest: `Memory.add(text, user_id="bench", infer=<False|True>, metadata={"source_id": id, "date": date})`,
  one memory per call, in date order.
- ranked: `Memory.search(q, filters={"user_id": "bench"}, top_k=10, threshold=0.0)`.
- deployed: `Memory.search(q, filters={"user_id": "bench"})` with mem0's defaults (`top_k=20`, `threshold=0.1`,
  no rerank). mem0 has no budget or injection format; the application builds the prompt.
- Items are mapped to source memories through the `source_id` metadata returned with each hit.

mem0 2.x search is hybrid: semantic score + BM25 (fastembed sparse vectors) + entity boost (spaCy). The extras
`mem0ai[nlp]` and `fastembed` are installed because the docs require them for that path. Both are English-only,
so on this Chinese dataset the BM25 part contributes nothing (measured: `bm25_score` 0.0 on a Chinese query)
and the ranking is effectively the bge-m3 cosine score.

## Modes (`MODE` switch in `run.sh`)

| MODE | output | LLM | network |
|---|---|---|---|
| `verbatim` | `bench/results/raw/mem0-verbatim.json` | none (`infer=False`) | `fmg-bench` only |
| `infer` | `bench/results/raw/mem0.json` | DeepSeek `deepseek-flash`, budget 3 CNY | internet + `fmg-bench` |

`infer=True` is mem0's normal path: one LLM call per `add` extracts facts, then they are embedded and stored.
In mem0ai 2.2.1 that path is additive (ADD only, hash dedup); there is no update/delete step, so nothing is
merged or deleted. The key comes from `--env-file $FMG_LLM_ENV_FILE`
(never read or printed). Token counts come from the API `usage` fields (wrapping the client call mem0 makes);
cost is the `/user/balance` difference. The budget stop uses an upper-bound token price
(`RATE_IN=2 RATE_IN_CACHED=0.2 RATE_OUT=8` CNY per 1M tokens), so it stops early rather than late.

## Reproduce

    bash bench/adapters/mem0/build.sh          # docker build (network needed once)
    bash bench/adapters/mem0/run.sh            # MODE=both: verbatim, then infer, then python bench/score.py
    MODE=verbatim bash bench/adapters/mem0/run.sh
    MODE=infer LIMIT=10 DRY=1 bash bench/adapters/mem0/run.sh   # 10-memory dry run, prints a spend report

For the full infer run use `MODE=infer RATE_IN=2 RATE_IN_CACHED=0.2 RATE_OUT=8 bash run.sh`.

## Results of the recorded run

- verbatim: 240/240 ingested in 14.5 s; no LLM.
- infer: 240/240 ingested in 471 s; 248 stored facts, 0 source memories without a surviving fact, 0 facts
  without `source_id`; 2,059,186 tokens (prompt 2,012,313 of which 1,873,792 cache hits, completion 46,873);
  cost 0.53 CNY (balance difference). Dry run on 10 memories: 81,226 prompt + 1,337 completion tokens (the
  balance showed no change at its 2-decimal resolution), which extrapolates to about 2.0 M tokens for 240.
- Facts are rewritten (names resolved, dates grounded to the current date, sometimes translated to English),
  so `ranked`/`deployed` contain the facts' source memories, not their original text.
