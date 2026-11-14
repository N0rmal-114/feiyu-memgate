# Result contract

Every system in the comparison is driven by an **adapter**: a small script that ingests the benchmark
memories into the system, asks the benchmark questions, and writes **one JSON file**:

    bench/results/raw/<system>.json

`bench/score.py` validates that file against this contract and turns it into metrics. The scorer never talks
to a system; the adapter never computes metrics. Dataset: `bench/data/` (version `v1`, frozen).

## File format

```
{
  "system": "name",
  "version": "x.y or commit",
  "mode": "free text, e.g. 'verbatim ingest, bge-m3 embeddings, no LLM'",
  "how": "exactly which functions/endpoints of the system were called",
  "ingest_seconds": 12.3,
  "llm_tokens": 0,
  "llm_cost_cny": 0.0,
  "answers": {
    "q001": {
      "ranked":   [{"id": "m012", "score": 0.83}, ...],   // up to 10, best first, no threshold
      "deployed": [{"id": "m012", "score": 0.83}, ...],   // what the system would hand to the model by default
      "ms": 4.1
    }
  },
  "notes": ["anything a reader must know: skipped features, mapping tricks, failures"]
}
```

Fields:

- `system`: a short unique name (also used as the row label); two files may not share a name.
- `version`, `mode`, `how`: non-empty strings. `how` names the real calls (functions, endpoints), not a summary.
- `ingest_seconds`: wall-clock seconds to ingest all 240 memories (including any extraction or embedding the
  system does at write time). `llm_tokens` and `llm_cost_cny`: total spent on language-model calls for ingest and
  all questions together; `0` when the system uses no LLM. Self-reported; numbers >= 0.
- `answers`: one entry for **every** question id in `queries.json`, no extra ids.
- `ranked`: the system's best guess, up to 10 items, best first, **without any threshold or budget**. Used for
  Hit@1, Hit@5, MRR@10, Recall@5 and the fresh-first rate. Return whatever the system's search returns for a top-10
  request; fewer than 10 is fine when it has nothing more.
- `deployed`: what the system would actually give the model with its **default settings**: its own score threshold,
  result limit and token or character budget. An empty list means "nothing to say". Used for deployed hit, noise,
  number of items and the quiet rate.
- `score`: a finite number (any scale; it is not compared across systems).
- `ms`: wall-clock milliseconds of the single call that produces `deployed`, measured around that call only.
- `notes`: list of strings (may be empty, but say something when anything was skipped, mapped or failed).

## Mapping items to memories

`id` is the id of the **source memory** (`m001` ...). A system that rewrites memories (extraction, summaries,
chunking, merged facts) must map each returned item back to the memory it came from and the adapter must say
how in `how` and `notes` (for example: the id is kept as metadata and read back; or the returned text is matched
to a memory by exact text; or by the highest bigram overlap). Rules:

- An item that maps to no memory uses `"id": null`. It counts as noise (never as a hit). `null` may repeat.
- A memory may appear **at most once** per list. If several chunks or facts come from the same memory, keep the
  best-scoring one at the position of its best item. The scorer rejects duplicates.
- Do not guess a mapping to be generous. When a rewritten item mixes several memories, map it to the one it
  mostly states, or to `null` when unsure, and say so in `notes`.

## Rules for adapters

1. **Ingest every memory exactly once, in date order** (the order of ids in `memories.json`, which is date order),
   with its memory id attached as metadata or reference, and its date where the system has a field for dates.
   Use only `text`, `date` and `id`; the `source` and `kind` fields are for analysis, not for the systems
   (a system with a native notion of a source or a type may be given them, and `notes` must say so).
2. **No tuning on the queries.** The questions and expected answers must not influence the adapter: no
   parameter search, no query rewriting rules written after reading them, no prompts fitted to them.
   Choose settings before the first run and keep them.
3. **Default settings** of the system, unless a note in the result says what was changed and why (for example,
   a required model name, an API key, a database path). Changing a setting to make the system work at all is fine;
   changing it to score better is not.
4. **One warm-up query** (not from `queries.json`) before timing starts, so that model loading and caches are
   not counted against the first real question.
5. **Timings** are wall-clock per question on the same machine for all systems, one question at a time,
   no batching, no concurrency.
6. A question the system fails on (error, timeout) gets empty `ranked` and `deployed` lists and a note counting
   the failures; do not leave it out.
7. Language-model calls are allowed only if the system needs them for its own normal operation; they are counted
   in `llm_tokens` and `llm_cost_cny` and described in `mode`. The adapter itself must not call a model to help
   answer, rerank or judge.
8. Never include secrets (keys, tokens) in the file, and keep it free of anything but the fields above.

## What the scorer reports

On answerable questions (lexical, paraphrase, updated), from `ranked`: Hit@1, Hit@5, MRR@10, Recall@5. From
`deployed`: hit (any expected memory present), noise (share of returned items that are not expected, averaged
over the questions that returned something), average number of items. On quiet questions, from `deployed`: the quiet
rate (share answered with nothing). On updated questions, from `ranked`: the fresh-first rate (the newer memory
ranks above the stale one; a missing stale memory is a win only if the newer one is present). Latency p50 and p95
(nearest rank), ingest seconds, LLM tokens and cost as reported. Run `python bench/score.py`; it writes
`bench/results/summary.json` and `bench/results/summary.md` and lists every raw file it had to leave out.

A reference adapter is in `bench/adapters/feiyu_memgate_sqlite.py`.
