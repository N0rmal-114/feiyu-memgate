# memsearch adapter

memsearch (zilliztech/memsearch, MIT) is a Markdown-first memory library: notes live in `.md` files, are chunked
on headings, embedded, and searched with Milvus (Lite by default) using hybrid dense + BM25 retrieval fused by RRF.
A DeepSeek Harness plugin (`@zilliz/memsearch-dsh` 0.1.8) adds turn capture and a `memory-recall` skill.

- Version: memsearch **0.4.23**, installed with `pip install /src` from a copy of a checkout of
  github.com/zilliztech/memsearch at commit 369ae1da48b7 (`MEMSEARCH_SRC` points at it). Not the PyPI release; the build needs
  PyPI only for dependencies (pymilvus 3.0.3, milvus-lite 3.2.2 resolved at build time).
- Image `fmg-bench-memsearch` (python:3.12-slim). Build: `bash bench/adapters/memsearch/build.sh`.
  Run: `bash bench/adapters/memsearch/run.sh` (network `fmg-bench`, read-only root, caps dropped, 4 GB, 4 CPUs),
  which writes `bench/results/raw/memsearch.json` and runs `python bench/score.py`.

## What is called
- Layout: `memory/YYYY-MM-DD.md` daily files (the layout in the docs and plugin), one `### 00:NN` section per memory
  and an `<!-- id:mNNN -->` anchor line below the heading. Memory text verbatim. memsearch strips HTML comments before
  embedding but stores them in the chunk, so each returned chunk maps to exactly one memory id via the anchor.
- Ingest: CLI `memsearch index <dir> --provider openai --model bge-m3 --base-url http://fmg-bench-embedder:11434/v1`.
- ranked: `MemSearch.search(query, top_k=10)`. deployed: `MemSearch.search(query, top_k=5)`, because the plugin's only
  recall path is the `memory-recall` skill, which runs `memsearch search --top-k 5`; there is no threshold or budget.
- LLM: none (compact, capture summaries, reranker, watcher all unused).

## Caveats
- The plugin has no automatic injection; in real use the agent chooses whether to search and what query to write.
- Search was called in-process; the CLI adds python/pymilvus start-up per call, which `ms` does not include.
- The per-day `### 00:NN` headings are synthetic sequence numbers.
- Timings are noisy (shared embedder, other workers).
