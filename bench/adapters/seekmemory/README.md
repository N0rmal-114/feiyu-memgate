# SeekMemory adapter

**System:** SeekMemory (github.com/NeverENG/SeekMemory), commit `794ec5d`, cloned 2026-11-14. A host-independent
memory kernel (Source -> Artifact -> Recall) with host adapters (AstrBot and others). Local SQLite, BM25 plus a graph
activation, no vector search, no model call at recall time. Python standard library only at runtime (its only manifest,
`requirements-dev.txt`, lists contract-test tools), so the Docker image installs nothing.

## Run

    bash bench/adapters/seekmemory/run.sh

It copies the kernel directories from the checkout that `SEEKMEMORY_SRC` points at into
`$FMG_BENCH_HOME/work/seekmemory/ctx`, builds image `fmg-bench-seekmemory`, runs three containers (`--network none`,
`--cap-drop ALL`, `no-new-privileges`, 4 GB, 4 CPUs, read-only root, data mounted read-only) and then `bench/score.py`.
Outputs: `bench/results/raw/seekmemory.json`, `seekmemory-aged.json`, `seekmemory-aged-detail.json`.

## What is called, and why it is the fair entry point

- **Write.** SeekMemory's normal write path is extraction by the host LLM; that is not available here (no LLM calls).
  The kernel has no "remember this" method, so the adapter does what the project's own `benchmarks/memory_execution.py`
  does: append a `Source` (the memory text as the owner's message in one private session), build the artifact verbatim
  with `make("memory", {...}, id=<memory id>)` and commit it with `maintain.link.persist` (the kernel's own commit,
  cache update and graph linking). `level="fact"`, `shape="point"`, scope `private`, date = memory date. Not available
  without a model, hence absent: tags, emotion values, and the `supersedes` field (the only way the kernel retires an
  older memory that a newer one contradicts), so "updated" questions rely on ranking alone.
- **deployed.** `recall.pipeline.query_block(...)`, the single recall pipeline the hosts call, with the kernel's default
  config (`app.config.defaults.DEFAULTS`: 8 items, 600-character budget, evidence gate 0.5/0.3, explore 0.1), a persistent
  `HybridRetriever`, private session and owner as speaker. Items are the ids in `info["final_ids"]`, i.e. what survived
  disclosure and the character budget. The recall call is read-only (`query_block` writes nothing).
- **ranked.** The same pool and scope weighting as `query_block`, then `recall.score.select(k=10, gate=False, explore=0)`:
  its own ranking with no evidence threshold, no budget, and without the intent switch and disclosure. The score field is
  `1/rank` because the kernel returns no scores.
- **aged.** After ingest, `maintain.decay.decay_all(today = container date + 365 days)` (its own sweep; it takes the date
  as a parameter), then recall with `date` rebound in `recall.score`, `recall.temporal`, `recall.assemble` (the only places
  recall reads `date.today()`). No kernel source is modified. No recall between ingest and the sweep.
- `seekmemory-aged-detail` repeats the aged run with `level="detail"`, because `decay_all` never forgets `level="fact"`
  (`maintain/expiry_policy.py:49`) and the fact run therefore shows no forgetting. `detail` is a legitimate extraction level
  (default memory, stability 30 days). Chosen as a sensitivity run, not tuned on results.

## Results (python bench/score.py, 240 memories, 160 questions)

| row | Hit@1 | Hit@5 | MRR@10 | deployed hit | noise | items | quiet | fresh-first | p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| seekmemory | 50.8% | 64.2% | 0.571 | 65.0% | 82.8% | 4.09 | 22.5% | 45.0% | 3.24 / 5.49 |
| seekmemory-aged (fact, +365 d) | 52.5% | 64.2% | 0.582 | 65.0% | 82.8% | 4.09 | 22.5% | 45.0% | 2.96 / 6.87 |
| seekmemory-aged-detail (+365 d) | 0.0% | 0.0% | 0.000 | 0.0% | - | 0.00 | 100.0% | 0.0% | 0.16 / 0.21 |

Ingest 0.3 s, 0 LLM tokens. The aged `fact` run differs from the plain run only through the freshness term (real clock
makes the newest memories 25 days old, +365 days makes everything ~0), nothing was forgotten (0 of 240). With `detail`, all
240 memories fall below retention 0.3 after 365 days and are marked `forgotten`, so recall returns nothing: the "quiet
rate" of 100% there is the system forgetting everything, not discrimination. Forgotten memories remain in SQLite as a new
revision of the row.

## Caveats

- No tags and no `supersedes`: SeekMemory with its extraction model would populate them; this is the no-LLM floor.
- Deployed noise is high because the evidence gate is a floor relative to the best candidate, plus `recall_k` 8, and
  the system injects up to 600 characters whenever anything passes; 13 of 160 calls returned an empty block.
- The default `explore_rate` 0.1 can swap the last deployed item for a random lower-ranked one (seeded by the query text).
- Real container clock (2026-11-14) is used for the plain run; the freshness term is near zero either way.
- Latency is per `query_block` call, in-process, measured inside the container.
