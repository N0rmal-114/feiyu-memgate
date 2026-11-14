# dsh-mnemon adapter

**System.** dsh-mnemon is the most starred memory plugin for the DSH desktop agent app: a memory orchestration layer
with resident runtime memory, project documents, "Memory Spaces" (long-term evidence, several swappable backends),
a "Layered" default strategy, and background idle-review sessions that write memories with an LLM.
Version: 0.5.45, commit `1dec529` (clone taken 2026-11-14).

**Default backend.** Memory Spaces default to the *Mnemon Native* Provider, a driver over the separate `mnemon` CLI
(npm `@mnemon-dev/mnemon`, pinned 0.2.13 in the Dockerfile). It stores insights in SQLite and scores recall by
keyword, entity and graph signals; vector similarity is used only when the embedding option is switched on (off by
default). The other eight Providers are off until configured.

## What is called (all real plugin code, run with `tsx` inside docker)

| Step | Entry point |
|---|---|
| write | `MemorySpacesService.remember()` -> `MnemonNativeProvider.remember()` -> `mnemon remember <text> --cat general --imp 3 --source user` |
| ranked (top 10, no threshold) | `MemorySpacesService.search({limit:10, inspect:true})` on a service with the built-in policy `exhaustive-v1`; Native driver runs `mnemon --readonly recall --limit 40 --verbose`, plus the service's own lexical recovery |
| deployed | the Layered strategy's `createThreeTierTurn(view).query()` recall route (first Recall call of a turn): limit 6, policy `strict-v1` (drop < 0.25, caps on medium/unknown), then admission of at most 4 results / 3600 characters |

Why this is the fair entry point: it is the path the model's recall tool takes, ending in the same admission
code; the CLI is the real default backend, started the way the plugin starts it (`createRunner`).

## Stubbed host glue (nothing of storage/retrieval)

- the View / ReadGrant objects handed to the strategy turn;
- the Source's recall-route `evidence()` wrapper (budget + provenance), ported from `source.ts`;
- the `dsh-mnemon` core package: only `src/sdk/input.ts`, `storage-lock.ts`, `core/definitions.ts`, `core/contracts`
  are copied from the clone, behind a two-line barrel (the real root barrel imports the whole host);
- cordis / the plugin Loader (provider assembly done by hand as in the plugin's own `tests/providers.ts`).

## Deviations from "default" (also in the result notes)

- **No LLM.** The default write path is an LLM idle review; it was bypassed with the explicit `remember` API
  (verbatim text, category general, importance 3, no tags/entities). Not measured: what the review would store.
- **No dates.** `mnemon remember` has no date argument; memories are written in date order, seconds apart.
- Embedding mode (`dsh-mnemon-embedding`) is configuration only: `embedding.enabled` with the shared endpoint
  `http://fmg-bench-embedder:11434` and model `bge-m3` (the plugin passes it to the CLI as `MNEMON_EMBED_*`).
- Dedup of the CLI stayed on; result: 240 of 240 memories added.
- `deployed` scores are 0: the strategy's admission drops the score field. The recall tool is only used when the
  model decides to call it; there is no automatic pre-turn injection of Memory Space recall, so "quiet" is not a
  property of the plugin alone.

## Run

    bash bench/adapters/dsh-mnemon/build.sh   # assemble context under work/mnemon/ctx and docker build fmg-bench-mnemon
    bash bench/adapters/dsh-mnemon/run.sh     # default (--network none) and embedding (--network fmg-bench), then score.py

Container flags: `--cap-drop ALL --security-opt no-new-privileges:true --memory 4g --cpus 4 --read-only`, tmpfs `/tmp`.
