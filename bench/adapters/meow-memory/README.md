# meow-memory adapter

**System**: `meow-memory` (npm, version 0.32.6, MIT), a long-term memory plugin for the DSH desktop agent app.
Seven SQLite tables (soul/user/project/fact/lesson/topic/rules), memories written by the agent through tools,
in-process BM25-style keyword retrieval, per-message "keyword hit" injection, background reflection and dream
consolidation (both LLM-driven, not used here). Source: the published npm package meow-memory 0.32.6, unpacked by the user (`MEOW_MEMORY_PKG` points at the
folder that holds its `package.json`), copied into a docker build context at build time.

## What is called (real code, unmodified `lib/index.js`)

- Write: the plugin's `memory_remember` tool, via `registerMemoryTools(register)` and `tool.execute(args, exec)`.
  One call per memory, in date order, text verbatim, `level: fact`, `project:` the global marker, `importance: 1`
  (schema default).
- `ranked`: the `memory_search` tool with `k: 10` (`explain: true` only to recover the order, see below).
  Retrieval is `search()` in `bm25.ts`: tokens = CJK character bigrams and lower-cased word runs, BM25 (k1 1.5,
  b 0.75) over title + content + keywords, times a recency weight for importance < 3.
- `deployed`: `buildHitInjection(db, ws, sid, query, {})`, the function the host runs before every user turn.
  It scores only the *keywords* of fact/lesson/rules/topic rows (`keywordHitScore`) and injects the top
  `hitTopK` = 2, no score threshold. The first-turn snapshot (`buildInjection`) only injects soul/user rows and
  global rules with importance >= 2, so it returns no fact memories; it is not part of `deployed`.

Language is the default `zh` prompt language: Chinese is tokenized as overlapping character bigrams by the package
itself (no segmenter needed); English stemming/stop-words only apply when the language is set to `en`.

## Host glue stubbed (not retrieval logic)

- tool registry (a collector), the `exec` object (`agent.session.header.cwd` and `id`), a tmpfs workspace and HOME;
- `@deepseek-ai/dsh-llm` (DSH-internal peer; two message helpers, never used on this path) is a stub in `stubs/`;
  `@deepseek-ai/schemastery` is the real npm package, installed during `docker build`;
- the clock: the tool has no date field, so `Date.now` is set to each memory's date while writing and to the day
  after the last memory for all questions (decided before the first run).

## Things a reader must know

- `memory_remember` requires LLM-written `keywords`. No LLM is used; the adapter passes all unique tokens of the text,
  produced by the plugin's own exported `tokenize()`. LLM-chosen keywords would change the `deployed` numbers.
- The tool re-sorts search hits by `updated_at`; ranking order is recovered from `explain.bm25 * recencyWeight`.
- `hit_count`/`last_accessed_at` (lookup side effects that feed the recency weight) are reset by SQL after each call so
  every question sees the same state. Each question uses a fresh session id.
- Latency (`ms`) times `buildHitInjection` only. It re-tokenizes every candidate on each call.

## Run

`bash bench/adapters/meow-memory/run.sh` assembles the context in `$FMG_BENCH_HOME/work/meow-memory/`,
builds `fmg-bench-meow-memory`, runs it with `--network none --cap-drop ALL --read-only` (data mounted read-only), and
copies the result to `bench/results/raw/meow-memory.json`. Then `python bench/score.py`.
