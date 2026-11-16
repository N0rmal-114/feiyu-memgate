<p align="center">
  <img src="docs/assets/banner.en.png" alt="feiyu-memgate: a memory gateway" width="100%">
</p>

<p align="center">
  <a href="README.md">中文</a> · <b>English</b>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB">
  <img alt="Runtime dependencies: standard library only" src="https://img.shields.io/badge/runtime%20deps-stdlib%20only-2E7D32">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-555555">
  <img alt="Status 0.1.0" src="https://img.shields.io/badge/status-0.1.0-B26A00">
</p>

# feiyu-memgate

A **memory access layer** for chatbots and agents. The chat program calls two endpoints: recall and remember. The gateway enforces, from configuration, who can read and write which memory, which retrieved candidates may be handed to the model, which checks run before a write, and whether the caller can tell when a backend fails.

It does not compete on retrieval algorithms. Storage and retrieval belong to the backend (four are built in: SQLite keyword, vector, hybrid and OpenViking; you can plug in your own). The gateway handles both ends of retrieval.

The result of comparing it with 8 other memory projects on the same held-out test set comes first:

| | feiyu-memgate (5 configurations; precise profile in brackets) | The other 8 projects (10 configurations) |
|---|---|---|
| Items delivered to the model by default (average per question) | 0.6 – 2.5 (0.4 – 1.2) | 0.3 – 20 |
| Share of delivered items that are correct | 57% – 89% (80% – 93%) | 0% – 47% |
| Share of questions with no answer in memory where nothing is delivered | 32.5% – 70.0% (40.0% – 75.0%) | mostly 0%, highest 82.5% |
| Retrieval ranking Hit@5 (no score threshold) | 81.7% – 98.3% | 13.3% – 98.3% |
| Share of questions where the default delivery contains the correct memory | 47% – 97% (39% – 88%) | 0% – 100% |
| When both the newer and the older memory are delivered, the newer one comes first | 80% – 100% (75% – 100%) | 50% – 80% |

The default threshold profile is the broad one (`broad`, since 2026-11-16). The first number in rows two to five is its value, the number in brackets is the precise profile (`[recall] profile = "precise"`, the previous default). The broad profile almost never misses an answer. The cost is that what it delivers is noisier, it speaks up more often when there is no answer, and the context per question is longer. The precise profile delivers about 1 item per question and most of it is correct. The cost is missed answers. The fourth row shows that retrieval itself is not better than the others: the vector configuration matches the systems that also use bge-m3, and this row does not change with the profile. The number in the last row also depends on the profile: with the broad profile more items are delivered, so the older memory more often comes along. Full data, method and limits are in the [Benchmark](#benchmark).

## What it solves

This project was extracted from the memory module of a long-running private companion bot ("Xiao Feiyu"). Every row below is a problem that bot hit in production.

| Problem seen in production | Mechanism in the gateway |
|---|---|
| The chat program holds the store keys directly, so the group-chat code path can read private-chat memory | Store keys exist only in the gateway process. Each client token is bound to an access policy: which stores it can read, which it can write, its tenant scope, and whether the setting is public |
| The backend is down, the bot keeps answering, and it just looks like it "doesn't remember" | "Found nothing" and "the query failed" are returned separately: `failed` and `busy` counters, 503 when every store fails, and `/healthz?deep=1` reports the state of each store |
| The reply says "got it", but nothing was written | Write receipts have four outcomes: saved, definite failure, limit reached, unsure. For backends with asynchronous extraction, the gateway tracks whether the item eventually became a memory |
| Recall stuffs a dozen unrelated items into the prompt | Recall pipeline: absolute score threshold, relative score threshold, near-duplicate removal, character budget. An item that does not fit is dropped whole, and the caller is told how many remain |
| Text inside a memory is executed by the model as an instruction | Recall results are wrapped in a labelled data block, and same-name labels inside the memory text are escaped |
| Keys and phone numbers get written into memory and later read back out | Redaction runs once before the write and once before the response. Clients in public settings get an extra layer of personal-information masking |
| Group members flood writes and fill the store | Per-minute request limits and per-day write limits, counted per client and per tenant, plus a cross-tenant total limit |
| A fact has a newer and an older memory, and the older one comes first | `newer_first` stage: among candidates that pass the score thresholds, the one with the newer date comes first |
| One group-chat token can read every group | Optional tenant-bound token: one derived token per tenant, and the gateway trusts only the tenant inside the token |
| Memory only grows | Optional forgetting curve: down-weight, archive and delete by use, with recovery (experimental, off by default) |

## Architecture

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture.en.dark.png">
    <img src="docs/assets/architecture.en.light.png" alt="Architecture diagram: the chat runtime calls the gateway through the client SDK; inside the gateway are authentication and policy, the recall pipeline, the write pipeline, the usage ledger and the forgetting sweeper; outside the gateway are the memory stores and the archive" width="100%">
  </picture>
</p>

- **Trust boundary**. The dashed box is the gateway process, and only it holds the store keys. The chat program gets a gateway token. A leaked token can do no more than that client's policy allows.
- **Recall path**. After authentication, rate limiting and permission checks, every store the client can read is queried in parallel. Candidates pass through the recall stages in order (by default `min_score` → `relative_score` → `newer_first` → `fill_text` → `drop_oversize` → `redact` → `dedupe` → `budget`) and come back with score, date and source.
- **Write path**. Length and format are validated, then the write guards run (by default `redact` and `daily_cap`), and the item lands in a shared store or in the current tenant's own store.
- **Forgetting path** (optional). The ledger records only the address, time and use count, never the text. The sweeper archives memories whose retention has run out, then deletes them.
- **Interface surface**. Four routes only: `GET /healthz`, `POST /v1/recall`, `POST /v1/remember`, `POST /v1/used`. There is no network endpoint for deleting, listing or exporting. Those operations are available only through the command line on the local machine.

<details>
<summary>Sequence diagram of one recall</summary>
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/recall-sequence.en.dark.png">
    <img src="docs/assets/recall-sequence.en.light.png" alt="Sequence diagram: the order of calls in one recall, from the client to the gateway, the stores and the recall stages" width="100%">
  </picture>
</p>
</details>

### Extension points

Each stage of the system is a slot with a fixed contract. A registry resolves it by name or by `module:Class`. A built-in implementation can be replaced in configuration with your own class, with no change to framework code.

| Slot | Contract | Built-in implementations |
|---|---|---|
| `backend` | Where memory is stored and how it is retrieved | `sqlite` (keyword, needs no service), `embedding` (vector), `hybrid` (multi-route fusion), `openviking`, `inmemory` |
| `recall_stage` | How candidates are filtered, ranked and limited | `min_score`, `relative_score`, `newer_first`, `source_weight`, `freshness`, `fill_text`, `drop_marker`, `drop_oversize`, `redact`, `dedupe`, `budget` |
| `write_guard` | Checks before a write reaches the store | `redact`, `daily_cap` |
| `redactor` | How text is cleaned | `secrets`, `public`, `none` |
| `tenants` | Where each tenant's own store comes from | `directory`, `keyfile` |
| `forgetting_curve` | How retention changes with time and use | `ebbinghaus` |

The gateway, the client and all built-in implementations use only the Python standard library. Only encrypted backup needs `cryptography`.

## Benchmark

The same memories and the same questions, 15 configurations of 9 projects. Each system uses its own retrieval code and default settings. The list of systems and the method come first. The data follows, by topic: ranking, delivery, question type, quiet questions, context, newer and older facts, and cost. The characteristics and trade-offs of each project are collected in [Characteristics and trade-offs of each project](#characteristics-and-trade-offs-of-each-project).

### Systems compared

| Project | Version tested | Form | Retrieval | Default write calls an LLM | Default delivery |
|---|---|---|---|---|---|
| **feiyu-memgate** (this project) | 0.1.0 | Gateway: HTTP service or in-process | Keyword, vector, hybrid, or OpenViking | No | Up to 5 items, with score thresholds and a character budget; the default broad profile delivers about 0.6 – 2.5 items per question, `profile = "precise"` about 0.4 – 1.2 |
| [memsearch](https://github.com/zilliztech/memsearch) | 0.4.23 | Library plus DSH plugin | Hybrid: vector plus BM25 | The library does not; the plugin summarizes each turn with a model by default | Top 5, no score threshold, the model decides whether to retrieve |
| [mem0](https://github.com/mem0ai/mem0) | 2.2.1 | Library | Vector plus BM25 plus entities | Yes (can be turned off) | 20 items, score threshold 0.1 |
| [OpenViking](https://github.com/volcengine/OpenViking) | v0.5.3 | Service plus DSH plugin | Vector | Yes (not when memory files are written directly) | API default 10 items, no score threshold |
| [Graphiti](https://github.com/getzep/graphiti) | graphiti-core 0.31.2 | Library | Graph: hybrid retrieval over extracted facts | Yes | 10 items, cosine floor 0.6 |
| [SeekMemory](https://github.com/NeverENG/SeekMemory) | commit 794ec5d | Kernel library plus host adapter | Keyword: BM25 plus graph activation | Yes | Up to 8 items, 600-character budget, automatic injection on every turn |
| [dsh-mnemon](https://github.com/omdsh-dev/dsh-mnemon) | 0.5.45 | DSH plugin | Keyword plus entities plus graph signals; vector optional | Yes | The runtime memory snapshot is provided automatically; Memory Spaces are recalled by a model call, at most 4 items per call |
| [lingshu (dsh-memory)](https://github.com/FuRongJun-1999/dsh-memory) | 0.10.1 | DSH plugin | Keyword: BM25 | No | Automatic injection of the 4 most recent items of the session on every turn |
| [meow-memory](https://github.com/Phant0Meow/dsh-meow-memory) | 0.32.6 | DSH plugin | Keyword: BM25 | Written by the host model through a tool | Top 2 matches injected before every message |

mem0 was tested in two modes, verbatim ingest and LLM extraction. dsh-mnemon was tested in its default mode and with vectors turned on. This project was tested in 5 configurations. That makes 15 in total. This project has 13 more variants: 4 lighter vector models, and for each configuration a version with the precise profile pinned (`-precise`; 9 in all, 5 of them in the tables below and the 4 for the lighter models in [Deployment tiers](#deployment-tiers)). Facts such as each project's forgetting mechanism and license are in [bench/results/design/features.en.md](bench/results/design/features.en.md).

### Method

- **Data**. 240 memories and 160 questions, in Chinese, about a fictional person. The questions fall into four types: 45 lexical (the question uses the same words as the memory), 55 paraphrase (reworded), 20 updated (the same fact has an old and a new memory), and 40 quiet questions (no answer in memory, so the correct behavior is to deliver nothing).
- **Two kinds of metric**. *Ranking*: no score threshold, look at where the correct memory ranks (Hit@1, Hit@5). This measures retrieval itself. *Delivered by default*: with each system's default settings, what it actually hands to the model. This measures out-of-the-box behavior.
- **Fairness**. Each system uses its own real retrieval code and default settings. All systems that need vectors share one model (bge-m3). Third-party code runs only in containers with no network. Each adapter runs alone, one after another.
- **No tuning on the test set**. The test set is frozen. This project's two sets of score thresholds (precise and broad profile) were chosen on a separate development set with completely different text (160 memories, 110 questions), by a rule written down in advance. The held-out test set was run only after the thresholds were fixed, and no setting was changed after the run. Both profiles were run once on the test set, and neither was chosen again for the sake of its numbers.
- **Default profile**. The gateway has two sets of score thresholds: `precise` and `broad`. Since 2026-11-16 the default is `broad`, because the project is used for chat companions, where a topic usually touches several memories and one item per turn is too few. This was a product decision, not a tuning step, and the values of both sets did not change. In the tables below, a row of "this project" means the result with the default profile (broad) unless it is marked "precise profile", which is the same configuration with the precise profile pinned.

The full rules are in [bench/CONTRACT.md](bench/CONTRACT.md) (Chinese). What each system was called with and what was bypassed is in the notes of [bench/results/summary.md](bench/results/summary.md) (Chinese). Reproduction steps and run logs are in [bench/README.md](bench/README.md) (Chinese).

### Overview: quality of what reaches the model

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/quality.en.dark.png">
    <img src="docs/assets/charts/quality.en.light.png" alt="Bar chart: delivered F1, precision, quiet rate and average items delivered per question for each system, sorted by delivered F1" width="100%">
  </picture>
</p>

*Delivered F1* is the harmonic mean of "the delivery contains the correct memory" and "the share of delivered items that are correct". It is high only when both are high. This project's four vector and hybrid configurations are at 71.6% – 82.7% by default (broad profile) and the keyword configuration is at 61.3%; with the precise profile pinned they are at 75.6% – 83.7% and 55.1%. Among the other projects the highest is dsh-mnemon with vectors turned on (53.6%), and the rest are at 0% – 40%. The gap comes mainly from precision: most systems deliver the top N items by default. The broad profile has lower precision than the precise one, so the delivered F1 of the vector and hybrid configurations is lower with it (`embedding` 82.1% → 71.6%, `hybrid` 75.6% → 74.7%); only the keyword configuration rises.

### Retrieval ranking

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/ranking.en.dark.png">
    <img src="docs/assets/charts/ranking.en.light.png" alt="Bar chart: Hit@5 and Hit@1 for each system" width="100%">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/hit-at-k.en.dark.png">
    <img src="docs/assets/charts/hit-at-k.en.light.png" alt="Line chart: Hit@k for each system, with k from 1 to 10" width="100%">
  </picture>
</p>

- The vector configuration's Hit@5 is 98.3%, the same as memsearch, mem0 and OpenViking, which also use bge-m3. This column is decided mainly by the embedding model, not by the gateway.
- memsearch ranks best: Hit@1 is 85.0%, and the correct memory is always within the top 8. This project's vector backend has a Hit@1 of 81.7%.
- The vector systems reach 93% – 96% within the top 2, and growth after that is slow. This is the basis for delivering 1 to 2 items per question with the precise profile.
- The curves of the keyword systems are flatter: this project's keyword backend and lingshu reach 88.3% at the 10th item, and SeekMemory and meow-memory about 70%. They do not understand semantics, and paraphrase questions are their ceiling.
- The hybrid backend has a slightly higher Hit@1 (83.3%) and a slightly lower Hit@5 (96.7%). Both differences are within two or three questions.

### Delivered by default

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/delivered.en.dark.png">
    <img src="docs/assets/charts/delivered.en.light.png" alt="Scatter plot: the horizontal axis is the average number of items delivered per question, the vertical axis is the share of delivered items that are correct, and the number after each label is the delivered hit rate" width="100%">
  </picture>
</p>

| System | Retrieval type | Hit@1 | Hit@5 | Avg. items delivered | Delivered hit | Precision | Delivered F1 | Quiet rate | p50 latency |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **feiyu-memgate embedding** | vector | 81.7% | 98.3% | 2.46 | 96.7% | 56.9% | 71.6% | 32.5% | 24 ms |
| feiyu-memgate embedding (precise profile) | vector | 81.7% | 98.3% | 0.98 | 81.7% | 82.5% | 82.1% | 65.0% | 25 ms |
| **feiyu-memgate openviking** | vector | 81.7% | 98.3% | 1.50 | 90.8% | 75.9% | 82.7% | 40.0% | 26 ms |
| feiyu-memgate openviking (precise profile) | vector | 81.7% | 98.3% | 1.17 | 87.5% | 80.2% | 83.7% | 40.0% | 25 ms |
| **feiyu-memgate hybrid** | hybrid | 83.3% | 96.7% | 2.01 | 92.5% | 62.6% | 74.7% | 35.0% | 28 ms |
| feiyu-memgate hybrid (precise profile) | hybrid | 83.3% | 96.7% | 0.66 | 65.0% | 90.4% | 75.6% | 75.0% | 28 ms |
| **feiyu-memgate full** | hybrid + freshness | 83.3% | 95.0% | 2.08 | 93.3% | 60.1% | 73.1% | 35.0% | 33 ms |
| feiyu-memgate full (precise profile) | hybrid + freshness | 84.2% | 95.0% | 0.67 | 65.0% | 90.2% | 75.6% | 75.0% | 33 ms |
| **feiyu-memgate sqlite** | keyword | 61.7% | 81.7% | 0.55 | 46.7% | 89.3% | 61.3% | 70.0% | 2 ms |
| feiyu-memgate sqlite (precise profile) | keyword | 61.7% | 81.7% | 0.41 | 39.2% | 93.0% | 55.1% | 75.0% | 2 ms |
| memsearch | hybrid | 85.0% | 98.3% | 5.00 | 98.3% | 19.7% | 32.8% | 0.0% | 26 ms |
| mem0 (verbatim ingest) | vector | 81.7% | 98.3% | 20.00 | 100.0% | 5.0% | 9.5% | 0.0% | 35 ms |
| OpenViking | vector | 81.7% | 98.3% | 9.94 | 99.2% | 9.9% | 18.0% | 0.0% | 25 ms |
| mem0 (LLM extraction) | extraction | 77.5% | 95.0% | 19.76 | 99.2% | 5.0% | 9.6% | 0.0% | 69 ms ‡ |
| dsh-mnemon + vector | hybrid | 80.0% | 90.8% | 2.24 | 87.5% | 38.6% | 53.6% | 0.0% | 78 ms |
| lingshu | keyword | 65.0% | 81.7% | 4.00 | 0.0% | 0.0% | 0.0% | 0.0% | 2 ms |
| SeekMemory | keyword | 50.8% | 64.2% | 4.09 | 65.0% | 17.2% | 27.3% | 22.5% | 2 ms |
| meow-memory | keyword | 51.7% | 63.3% | 1.80 | 56.7% | 30.9% | 40.0% | 10.0% | 2 ms |
| dsh-mnemon | keyword | 38.3% | 42.5% | 1.02 | 38.3% | 37.1% | 37.7% | 0.0% | 33 ms |
| Graphiti | graph | 12.5% | 13.3% | 0.31 | 13.3% | 46.5% | 20.7% | 82.5% | 391 ms ‡ |

*Delivered hit*: the share of questions where the default delivery contains the correct memory. *Precision*: the share of delivered items that are correct, counted only over answerable questions where something was delivered. *Delivered F1*: the harmonic mean of the two. *Quiet rate*: the share of the 40 quiet questions where nothing was delivered. Rows of this project: the bold one is the default (broad profile), the "precise profile" row after it is the same configuration with `[recall] profile = "precise"` pinned. The Hit@1 of `full` differs by one question between the profiles (83.3% against 84.2%): in this configuration the ranking is measured after the deliveries of the same question have been recorded as used, freshness reads that record, and the broad profile records more items. The ranking of the other configurations does not depend on the profile. ‡ These two rows use a paid mode and were not rerun in the final sequential run. Their latency comes from a machine that was also running other jobs.

The table shows four things:

1. **Most systems deliver the top N items by default.** So their delivered hit is close to 100%, their precision is only 5% – 39%, and they almost never stay quiet on quiet questions. This project by default (broad profile) delivers 0.55 – 2.46 items per question, with precision of 56.9% – 89.3% (56.9% – 75.9% for the vector and hybrid configurations) and a delivered hit of 46.7% – 96.7%; with the precise profile pinned it delivers 0.41 – 1.17 items per question, with precision of 80.2% – 93.0%.
2. **The default profile misses far fewer answers, and the cost is noise and less silence.** 24% – 43% of what the vector and hybrid configurations deliver is wrong. Of the 40 quiet questions only 32.5% – 40.0% get nothing (precise profile: 40.0% – 75.0%), so 60.0% – 67.5% of the questions with no answer get unrelated memories; and the characters delivered per question are 1.3 – 3.1 times those of the precise profile. This is the reverse of the earlier "few and accurate": more questions are answered, but there is more risk of forcing an unrelated memory into the conversation, and the context is longer.
3. **The cost of the precise profile is missed answers.** With `profile = "precise"` pinned, the delivered hit is 39.2% – 87.5%, lower than the systems that "give a few more". Paraphrase questions suffer most, see the next subsection.
4. **The hybrid backend does not beat vector alone on hits.** Delivered hit is 92.5% against 96.7% and ranking Hit@5 96.7% against 98.3%; its score thresholds are stricter, so it delivers less (2.01 items against 2.46). Afterwards, five fusion methods were compared on the development set, and two small models were tried in combination. None clearly beat vector alone: on reworded questions, the keyword score of the correct memory is lower than that of unrelated memories. If you have an embeddings endpoint, use `embedding`. If you do not, use `sqlite`.

Score thresholds are configuration. The default is the broad profile. If few and accurate items matter more (or the memory block stays in the conversation history and context has to be saved), write `[recall] profile = "precise"` to get the previous behavior. The measured trade-off is in [Deployment tiers](#deployment-tiers). [docs/backends.md](docs/backends.md) (Chinese) explains how the values of both profiles were chosen for each backend and how to choose again with your own data.

### By question type

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/by-type.en.dark.png">
    <img src="docs/assets/charts/by-type.en.light.png" alt="Bar chart: ranking Hit@5 in three groups, lexical, paraphrase and updated" width="100%">
  </picture>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/delivered-by-type.en.dark.png">
    <img src="docs/assets/charts/delivered-by-type.en.light.png" alt="Bar chart: delivered hit by default in three groups, lexical, paraphrase and updated" width="100%">
  </picture>
</p>

Each cell is "ranking Hit@5 / delivered hit by default":

| System | Lexical (45) | Paraphrase (55) | Updated (20) |
|---|---:|---:|---:|
| **feiyu-memgate embedding** | 100.0% / 100.0% | 96.4% / 92.7% | 100.0% / 100.0% |
| feiyu-memgate embedding (precise profile) | 100.0% / 100.0% | 96.4% / 69.1% | 100.0% / 75.0% |
| **feiyu-memgate openviking** | 100.0% / 100.0% | 96.4% / 81.8% | 100.0% / 95.0% |
| feiyu-memgate openviking (precise profile) | 100.0% / 100.0% | 96.4% / 81.8% | 100.0% / 75.0% |
| **feiyu-memgate hybrid** | 100.0% / 100.0% | 92.7% / 85.5% | 100.0% / 95.0% |
| feiyu-memgate hybrid (precise profile) | 100.0% / 100.0% | 92.7% / 34.5% | 100.0% / 70.0% |
| **feiyu-memgate full** | 100.0% / 100.0% | 89.1% / 85.5% | 100.0% / 100.0% |
| feiyu-memgate full (precise profile) | 100.0% / 100.0% | 89.1% / 34.5% | 100.0% / 70.0% |
| **feiyu-memgate sqlite** | 100.0% / 97.8% | 63.6% / 3.6% | 90.0% / 50.0% |
| feiyu-memgate sqlite (precise profile) | 100.0% / 95.6% | 63.6% / 0.0% | 90.0% / 20.0% |
| memsearch | 100.0% / 100.0% | 96.4% / 96.4% | 100.0% / 100.0% |
| mem0 (verbatim ingest) | 100.0% / 100.0% | 96.4% / 100.0% | 100.0% / 100.0% |
| OpenViking | 100.0% / 100.0% | 96.4% / 98.2% | 100.0% / 100.0% |
| mem0 (LLM extraction) | 100.0% / 100.0% | 92.7% / 98.2% | 90.0% / 100.0% |
| dsh-mnemon + vector | 100.0% / 100.0% | 85.5% / 78.2% | 85.0% / 85.0% |
| lingshu | 100.0% / 0.0% | 63.6% / 0.0% | 90.0% / 0.0% |
| SeekMemory | 97.8% / 97.8% | 32.7% / 32.7% | 75.0% / 80.0% |
| meow-memory | 97.8% / 97.8% | 30.9% / 25.5% | 75.0% / 50.0% |
| dsh-mnemon | 95.6% / 93.3% | 10.9% / 5.5% | 10.0% / 5.0% |
| Graphiti | 24.4% / 24.4% | 5.5% / 5.5% | 10.0% / 10.0% |

- **Lexical questions do not separate the systems.** When the question uses the same words as the memory, every system except Graphiti ranks above 95%.
- **Paraphrase questions separate keyword from vector.** The vector systems rank at 85% – 96% and the keyword systems at 11% – 64%.
- **The precise profile's misses are concentrated in paraphrase questions.** With the precise profile pinned, the vector backend ranks 96.4% but delivers 69.1%: the correct memory was found, but its score did not pass the threshold. The hybrid backend delivers only 34.5%, and the keyword backend 0.0%. The default broad profile closes that gap: the vector backend delivers 92.7%, the hybrid backend 85.5%, and the keyword backend still only 3.6% (its problem is retrieval, not the threshold). On lexical questions, delivery and ranking are almost the same.
- **Delivered hit can be higher than ranking Hit@5.** A system that delivers 10 to 20 items by default covers a wider range than the top 5.
- **On updated questions** this project delivers 95.0% – 100.0% by default (keyword backend 50.0%) and 70.0% – 75.0% with the precise profile (keyword backend 20.0%). When it does deliver, the order of newer and older is covered in "Order of newer and older facts" below.

### Questions with no answer

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/quiet.en.dark.png">
    <img src="docs/assets/charts/quiet.en.light.png" alt="Bar chart: the share of the 40 quiet questions where each system delivers nothing, and the average number of items delivered on those questions" width="100%">
  </picture>
</p>

40 questions have no answer in memory. Delivering anything counts as not staying quiet. This project's configurations are at 32.5% – 70.0% by default (broad profile; 32.5% – 40.0% for the vector and hybrid configurations) and at 40.0% – 75.0% with the precise profile pinned. SeekMemory is at 22.5%, meow-memory at 10%, and the other systems with no default score threshold at 0%. Graphiti is at 82.5%, and it also delivers very little on answerable questions (see the limits section). The new default is worse on this measure than the previous one: `embedding` falls from 65.0% to 32.5%, and on the questions with no answer it still delivers 2.12 items per question on average (precise profile: 0.57). For a companion bot, this measure decides whether it forces an unrelated old fact into the conversation.

### Context spent

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/context.en.dark.png">
    <img src="docs/assets/charts/context.en.light.png" alt="Dot plot (log axis): the average number of characters each system delivers per question, and how many of those characters belong to the correct memory" width="100%">
  </picture>
</p>

A memory in this data set is about 24 characters on average, so the character count follows the item count: this project delivers 14 – 59 characters per question by default (broad profile; 10 – 28 with the precise profile), meow-memory 44, dsh-mnemon with vectors turned on 54, lingshu 90, SeekMemory 98, memsearch 120, OpenViking 240, and mem0 about 480. With the broad profile the vector and hybrid configurations (36 – 59 characters) are in the same range as meow-memory and dsh-mnemon with vectors, and still below lingshu and everything after it. Memories in real conversations are longer, and the absolute values scale up in proportion. Delivered content counts toward the prompt on every turn, so this measure maps directly to cost and to the chance that the model is distracted by unrelated content.

### Order of newer and older facts

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/fresh.en.dark.png">
    <img src="docs/assets/charts/fresh.en.light.png" alt="Bar chart: among updated questions where the newer memory was delivered, the share where the older memory did not come before it" width="100%">
  </picture>
</p>

In an updated question, the same fact has a newer and an older memory. The table below looks only at questions where the newer memory was delivered, and counts those where the older one did not come before it.

| System | Before adding `newer_first` | After |
|---|---:|---:|
| feiyu-memgate embedding |  | 16/20 |
| feiyu-memgate embedding (precise profile) | 9/15 | 15/15 |
| feiyu-memgate openviking |  | 19/19 |
| feiyu-memgate openviking (precise profile) | 9/15 | 15/15 |
| feiyu-memgate hybrid |  | 16/19 |
| feiyu-memgate hybrid (precise profile) | 8/14 | 14/14 |
| feiyu-memgate full |  | 19/20 |
| feiyu-memgate full (precise profile) |  | 14/14 |
| feiyu-memgate sqlite |  | 8/10 |
| feiyu-memgate sqlite (precise profile) | 3/4 | 3/4 |
| memsearch, mem0 (LLM extraction) | | 12/20 |
| dsh-mnemon + vector | | 11/17 |
| mem0 (verbatim ingest), OpenViking | | 10/20 |
| SeekMemory | | 9/16 |
| meow-memory | | 8/10 |

Systems where the newer memory was delivered on fewer than 5 questions are not listed (the keyword backend's 3/4 with the precise profile is left over from the earlier table). Other configurations of this project, default (broad profile): bge-small-zh 11/15, bge-base-zh 12/15, embeddinggemma 12/19, qwen3-embedding 14/19; precise profile: 11/11, 8/10, 9/9, 10/10. With the broad profile every configuration except `openviking` is short of all correct, because its score thresholds are wider than the range `newer_first` works on: this stage reorders only among candidates whose score is at least 85% of the best score, and candidates outside that range keep their original position.

`newer_first` reorders only the candidates that passed the score thresholds. It does not change which memories are delivered, so the numbers in the default delivery table above did not change because of it. It does not judge whether two memories contradict each other, and it does not invalidate the older one. It has no effect on a memory without a date. By the backend's own ranking (without the recall stages), every system ranks the newer memory before the older one between 5% and 60% of the time, and this project is also at 50% – 55%. The test set has only 20 updated questions and the development set has 12, so the sample is small.

### Cost

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/cost.en.dark.png">
    <img src="docs/assets/charts/cost.en.light.png" alt="Dot plot: median latency per question, ingest time for the 240 memories, and LLM token count" width="100%">
  </picture>
</p>

| System | Latency p50 | Latency p95 | Ingest of 240 memories | LLM tokens used for writes | Cost |
|---|---:|---:|---:|---:|---:|
| **feiyu-memgate sqlite** | 2 ms | 3 ms | 0.01 s | 0 | 0 |
| **feiyu-memgate embedding** | 24 ms | 51 ms | 6.4 s | 0 | 0 |
| **feiyu-memgate hybrid** | 28 ms | 55 ms | 6.4 s | 0 | 0 |
| **feiyu-memgate full** | 33 ms | 54 ms | 6.4 s | 0 | 0 |
| **feiyu-memgate openviking** | 26 ms | 53 ms | 69.3 s | 0 | 0 |
| memsearch | 26 ms | 53 ms | 13.6 s | 0 | 0 |
| mem0 (verbatim ingest) | 35 ms | 80 ms | 9.3 s | 0 | 0 |
| OpenViking | 25 ms | 52 ms | 71.8 s | 0 | 0 |
| mem0 (LLM extraction) | 69 ms ‡ | 610 ms ‡ | 471 s ‡ | 2.06 million | 0.53 CNY |
| dsh-mnemon + vector | 78 ms | 105 ms | 12.2 s | 0 | 0 |
| lingshu | 2 ms | 3 ms | 4.1 s | 0 | 0 |
| SeekMemory | 2 ms | 3 ms | 0.2 s | 0 | 0 |
| meow-memory | 2 ms | 3 ms | 0.2 s | 0 | 0 |
| dsh-mnemon | 33 ms | 39 ms | 3.0 s | 0 | 0 |
| Graphiti | 391 ms ‡ | 848 ms ‡ | 1347 s ‡ | 1.18 million | about 2 CNY |

- The latency of the vector configurations is spent mainly on one query embedding (bge-m3 on CPU). It differs little between systems. The keyword systems are all at 2 – 3 ms. The rows of this project are the default profile; with the precise profile pinned the latency differs by at most 1 ms and the ingest time is of the same order.
- This project makes no LLM calls on write. For most third-party systems the default write path needs an LLM. We bypassed it (verbatim ingest), so their token column is 0. Only mem0's LLM extraction mode and Graphiti were run on the default path.
- The two rows connected to OpenViking ingest slowly because its index is asynchronous, and the time includes waiting for indexing to finish.
- lingshu's ingest time does not include the waiting caused by its own write rate limit.

### Limits of these numbers

- 240 memories is a small data set. One question is about 0.8 percentage points, so a gap of a few points is not a conclusion. The data is synthetic single-sentence Chinese about one fictional person.
- This project wrote the adapters. Each adapter's README states the entry point it calls and why. Bias in our choices cannot be ruled out entirely, and the authors of each project are welcome to correct us.
- For most third-party systems the default write path needs an LLM to extract or organize memories. We bypassed that (verbatim ingest) and measured only retrieval and delivery. Only mem0 and Graphiti were also run in an LLM mode.
- Graphiti was run on a stack it does not recommend (embedded Kuzu plus a small model for extraction). Only 69 fact edges were extracted from the 240 memories. This result does not represent its performance on the recommended setup.
- lingshu injects the 4 most recent items of the session on every turn, without looking at the question. A delivered hit of 0 is by design, not a retrieval failure.
- dsh-mnemon's results vary by about ±5 percentage points between runs.
- memsearch does not inject memory content automatically, and the model also decides whether to retrieve from dsh-mnemon's Memory Spaces. Here, "delivered by default" is what the model receives when it calls the recall tool once. The runtime memory that dsh-mnemon provides automatically (snapshots of USER.md and MEMORY.md) is a separate mechanism and was not measured here.
- "Incorrect" is judged against the annotated expected memory. An item that is related but not annotated also counts as noise.
- The version of each project is the version at the time of testing. It may have changed since.

## Characteristics and trade-offs of each project

Each project is written in the same format: what it is, the strengths that were measured, and the costs that were measured or stated in its documentation. The numbers come from the tests above. The mechanisms come from the source code of each project's tested version and from its own documentation. Items marked "inferred" were read from the code and not measured. This project is written first, to the same standard.

### Prompt placement, caching and background calls

Model providers bill a repeated prompt prefix at a low price. So content that changes on every turn and sits early in the prompt keeps everything after it from hitting the cache. Another kind of cost is the model calls that a memory system starts by itself in the background. Neither shows up in the retrieval metrics, so they are listed separately:

| Project | Put into the prompt without a model request | Position | Changes every turn | Background model calls and limits |
|---|---|---|---|---|
| feiyu-memgate 0.1.0 | The recalled data block, fixed format, no current time | Recommended: appended to the end of the current message and kept out of history; the integrator decides. The default broad profile delivers more items per turn, so context grows faster when the block stays in the history | Varies with the question; placed as recommended, it affects only the end | None |
| memsearch 0.4.23 (DSH plugin 0.1.8) | A fixed retrieval hint, with no memory content | After this turn's message | No | The plugin summarizes the conversation with a model once per turn by default; no per-session limit on the count was found |
| mem0 2.2.1 | None; the application assembles the prompt | – | – | Each default write calls a model once |
| OpenViking v0.5.3 | Not checked | Not checked | Not checked | Extracts with a model when a session is committed |
| Graphiti 0.31.2 | None; the caller assembles the prompt | – | – | Every write needs a model (measured at about 2.5 calls per item) |
| SeekMemory (794ec5d) | Recalled content, capped at 600 characters; a persona block | Before this turn's message (inferred); the persona is in the host context | Recall varies with the question; the persona rarely changes | Extraction at most 2 times per day; nightly consolidation at most 12 calls and 60,000 tokens per night |
| dsh-mnemon 0.5.45 | A fixed protocol description; a snapshot of runtime memory | The protocol is in the system prompt; the snapshot is appended after the current message and stays in history | No; a new snapshot is appended only when memory changes | Idle review is on by default: at least 5 minutes apart, at most 20 per session, uses the main model, and the count resets on restart by default |
| lingshu 0.10.1 | A window of the 4 most recent memories and the 10 most recent messages | The host's context block, appended only when the content changes | Probably yes (inferred: the window refers to the most recent messages) | Off by default |
| meow-memory 0.32.6 | Fixed usage guidance; a snapshot before the first message; the top 2 keyword matches before every message | The guidance is in the system prompt; the rest is before the current message | Matches vary with the question; only appended, not repeated | Reflection: one extra round after 7 consecutive tool-call steps. Consolidation: at most once per time window, 6-hour cooldown, uses the main model |

The host program assembles the final prompt. What this table verified is what each plugin hands to the host and what its own documentation says. The host-side code was not read. None of the projects has published measured cache hit rate data, and this project did not measure it either.

### feiyu-memgate (this project)

A gateway layer outside storage and retrieval. Retrieval is left to the backend.

- **Strengths**
  - More complete delivery by default (broad profile): the vector and hybrid configurations deliver about 1.5 – 2.5 items per question with a delivered hit of 90.8% – 96.7%, and the vector backend delivers 92.7% on paraphrase questions. For few and accurate items, write `[recall] profile = "precise"`: about 0.4 – 1.2 items per question, precision of 80.2% – 93.0%, and nothing delivered in 40.0% – 75.0% of the cases where there is no answer.
  - Writes call no LLM, and there are no background model calls. The keyword tier needs no service.
  - Access policy, rate limits, the daily write cap, redaction, and the distinction between failure and empty result are enforced by the gateway in one place. The chat program never gets the store keys.
  - The retrieval backend, the recall stages and the threshold profiles can all be replaced. There are measured data for six tiers, from no model to a 1.16 GB model.
  - When both the newer and the older memory are delivered, the newer one comes first (15/15 for the vector configuration with the precise profile; 16/20 for the default broad profile, see "Order of newer and older facts" above).
- **Costs**
  - The default broad profile delivers noisier items: precision of 56.9% – 75.9% for the vector and hybrid configurations, 60.0% – 67.5% of the questions with no answer get something (quiet rate 32.5% – 40.0%), 1.5 – 2.5 items per question, and about 1.3 – 3.1 times the context of the precise profile. Switching to `profile = "precise"` gives the previous behavior, at the cost of missed answers: a delivered hit of 39.2% – 87.5%; on paraphrase questions the vector backend delivers only 69.1%, the hybrid backend 34.5%, and the keyword backend 0.0%.
  - Retrieval itself is not better than that of systems using the same model. Hit@1 is lower than memsearch.
  - It stores original text only, with no extraction, merging or rewriting. An old fact is not invalidated, only ranked later.
  - It is a service or library that you have to integrate yourself, not a plugin that works once installed.
  - Where the memory block goes is decided by the integrator. The bundled AstrBot example attaches it to the current message as a temporary part that is not kept in the history. When the host does not support temporary parts, it falls back to appending the block to the text of the current message: the system prompt and the earlier history still do not change, but the block stays in the history with that message, so the context grows.
  - The vector backend is a brute-force scan. Only a few hundred items were tested here, and latency will grow linearly at larger scale. This version, after the rework into a framework, has not yet run in production for a long time.

### [memsearch](https://github.com/zilliztech/memsearch)

Memory is Markdown files, and the index can be rebuilt at any time. Hybrid retrieval of vector plus BM25.

- **Strengths**: The best ranking in this test (Hit@1 85.0%, Hit@5 98.3%, and the correct memory is always within the top 8). People can read and edit the memory directly and put it in git. It puts no memory content into the prompt, only a fixed hint, so the cache is not affected.
- **Costs**: No score threshold or character budget. It delivers the top 5 (precision 19.7%, quiet rate 0%). Whether retrieval happens depends on whether the model calls it. The DSH plugin summarizes the conversation with a model once per turn by default. No forgetting or decay.

### [mem0](https://github.com/mem0ai/mem0)

A library that, by default, uses an LLM at write time to extract the conversation into separate facts.

- **Strengths**: Extraction turns colloquial speech into facts (240 memories give 248 facts) and resolves names and dates. In verbatim mode, ranking matches the other systems that use bge-m3 (Hit@5 98.3%). It supports a per-item expiry date.
- **Costs**: The default write calls a model once per item (240 items take about 2.06 million tokens, 0.53 CNY and 471 seconds of ingest). Extraction mode ranks slightly lower (Hit@1 77.5%, Hit@5 95.0%). It returns 20 items with score threshold 0.1 by default, and measured about 20 items per question at precision 5%. Its BM25 and entity parts depend on English models and do not work for Chinese. The application assembles the prompt itself.

### [OpenViking](https://github.com/volcengine/OpenViking)

A standalone memory service. It extracts with a model when a session is committed, and retrieves by vector.

- **Strengths**: Ranking is level with the other systems that use bge-m3 (Hit@5 98.3%). The service form can be shared by several programs. Connected behind this project's gateway, it is the combination with the highest delivered F1 in this test (82.7% with the default broad profile, 83.7% with the precise profile).
- **Costs**: The API returns 10 items by default with no score threshold (precision 9.9%, quiet rate 0%). The index is asynchronous, and after 240 items are written it takes about 70 seconds before they can be retrieved. The normal write path needs model extraction, which was not tested here. The service and the vector model have to be deployed separately. The default behavior of its DSH plugin was not verified.

### [Graphiti](https://github.com/getzep/graphiti)

A temporal knowledge graph: it uses an LLM to extract entities and time-stamped facts, and retrieves over the facts.

- **Strengths**: It handles "fact superseded" explicitly: an old fact is marked invalid instead of coexisting. Facts carry time. It has a cosine floor, and delivers nothing in 82.5% of the cases where there is no answer.
- **Costs**: Nothing can be written without an LLM, and it was measured at about 2.5 calls per item (240 items take about 1.18 million tokens, about 2 CNY and 22 minutes). This run used a stack it does not recommend (embedded Kuzu plus a small model) and extracted only 69 fact edges. Hit@5 is 13.3%, which does not represent its performance on the recommended setup.

### [SeekMemory](https://github.com/NeverENG/SeekMemory)

A host-independent kernel plus adapters. BM25 plus graph activation. A full lifecycle with reinforcement, decay and reversible forgetting.

- **Strengths**: It reinforces a memory only when a reply actually used it, fully automatic. It discloses in grades by degree of fit. The injection on each turn has a hard cap of 600 characters and an evidence threshold (quiet rate 22.5%). Background consolidation has explicit limits. Latency 2 ms.
- **Costs**: No vector retrieval: paraphrase questions 32.7%, Hit@5 64.2%, precision 17.2%. Normal writes depend on extraction by the host model, which was bypassed here. The DSH adapter version is 0.0.1.

### [dsh-mnemon](https://github.com/omdsh-dev/dsh-mnemon)

A DSH plugin. Two mechanisms: runtime memory provided automatically with the conversation, and Memory Spaces recalled by a model call (keyword plus entities plus graph signals, vector optional).

- **Strengths**: Memory Spaces have their own score thresholds and admission rules, and deliver only 1 to 2 items per question by default, the most restrained outside this project. With vectors turned on, Hit@5 is 90.8% and delivered F1 is 53.6%, the highest among the other projects. The current version was designed specifically to keep the cached prefix stable: the system prompt does not change when memory is written, and when memory changes a new snapshot is appended after the conversation. Idle review has an interval and a count limit.
- **Costs**:
  - In the default mode (vectors off) Hit@5 is 42.5% and paraphrase questions 10.9%. Results vary by about ±5 percentage points between runs. Quiet rate 0%.
  - Idle review is on by default, uses the main model, and its count resets on restart by default. The discussion of cost in its public feedback centers on this item.
  - When memory changes, the whole snapshot is appended, not a diff. One consolidation runs on a correction, on a deletion, or when the accumulated snapshots reach 5. The conversation after the consolidation point has to be billed once more as uncached. How often this consolidation happens in a long session has no published measurement.
  - Earlier versions (0.2.4 and before, 0.3.2 to 0.3.5, 0.5.7 and before) had a problem where writes invalidated the cached prefix. It was fixed in 0.2.5, 0.3.6 and 0.5.8 respectively. The project itself states that it has no measured cache hit rate data.
  - With vectors turned on, latency is 78 ms, the highest among the configurations in this test that make no LLM calls.

### [lingshu (dsh-memory)](https://github.com/FuRongJun-1999/dsh-memory)

A DSH plugin. Write gating, deduplication and indexing are all rule-based code. BM25 retrieval. Memory is ordinary files.

- **Strengths**: Writes call no model, and background maintenance is off by default. When retrieving by question, its ranking is the best among the keyword systems (Hit@5 81.7%, level with this project's keyword backend). Writes have a gate (accept, merge, discard, defer) and a rate limit, and injection has a character cap. Latency 2 ms.
- **Costs**: What it injects on every turn is the 4 most recent items of the session and the 10 most recent messages, without looking at the question, so its delivered hit by question is 0. Retrieval by question needs the model to call a tool. No vectors: paraphrase questions 63.6%. The injected window refers to the most recent messages and probably differs on every turn, so the context grows with it (inferred). The write rate limit (8 per minute) slows bulk ingest.

### [meow-memory](https://github.com/Phant0Meow/dsh-meow-memory)

A DSH plugin. One SQLite file plus BM25. The host's model writes through a tool and fills in the keywords.

- **Strengths**: Very light to deploy: one SQLite file, with no extra service or model. It injects only 2 items per message, without repeats, at the end of the conversation (just before the current message), and the guidance in the system prompt is fixed. Precision is the highest among the keyword systems (30.9%). 97.8% on lexical questions, latency 2 ms.
- **Costs**: No vectors: paraphrase questions 30.9%, Hit@5 63.3%. No score threshold (quiet rate 10%). Keyword quality depends on how well the model fills them in at write time, and this test used its own tokenizer instead. Background reflection and consolidation use the main model.

## Design self-check

Properties beyond retrieval, measured one by one on this project with probe scripts. This section rates only this project. ✓ holds, ◐ partly holds.

| Property | Result | What was measured |
|---|:-:|---|
| Redaction before write | ✓ | After writing fake keys, passwords and tokens, nothing remains in the store |
| Redaction before return | ✓ | Keys written into the store directly, bypassing the gateway, do not appear in recall results |
| Memory delivered as data, cannot cross the boundary | ✓ | For a memory containing a closing tag and a fake instruction, the tags inside the data block are escaped and the boundary is exactly one pair |
| Credential-level isolation | ◐ | Clients are isolated from each other by token. Tenant binding is optional, and in the default mode the tenant id is reported by the caller |
| Failure distinguishable from empty result | ✓ | When every store errors, the reply is 503. On partial failure the reply is 200 with a `failed` count |
| Writes are capped | ✓ | With a daily cap of 5, 32 concurrent writes succeed exactly 5 times |
| Output is bounded, whole items kept or dropped | ✓ | Item limit 10. Within the character budget an item is kept whole or dropped. An oversize item is dropped whole |
| Forgetting is recoverable | ✓ | A forgotten memory is archived first and can be restored unchanged during the retention period |
| Writes do not need an LLM | ✓ | With the network disconnected, all writes and recalls on the SQLite path succeed |
| Minimal network surface, authentication required | ✓ | Only 4 routes. The other 199 combinations of method and path all return 404 or 405 |

How each check was done, the numbers, and the places where a check did not pass are in [bench/results/design/self-check.en.md](bench/results/design/self-check.en.md). The probes were written and run by this project, and they show only that these checks hold on this version.

**What other projects have and this one does not:**

- Reinforcing a memory only when a reply actually used it, fully automatic (SeekMemory). Here the chat program has to report through `/v1/used`.
- Graded disclosure by relevance: full text, paraphrase, hint, or silence (SeekMemory). Here access control is all or nothing per store.
- Marking an old fact as invalid when a new fact supersedes it (Graphiti, SeekMemory). Here the newer one is only put first, and the older one is still delivered.
- Using an LLM to extract, merge and rewrite memories from conversation (mem0, Graphiti, OpenViking and others). Here only the original text is stored, and extraction is the backend's job.
- Deduplication and merging at write time (lingshu, meow-memory, mem0). Here deduplication happens only at recall time.
- Narrowing read scope per turn (dsh-mnemon), a per-item expiry date (mem0), and memory as directly editable Markdown that can go into git (memsearch).

## Deployment tiers

Deployment has three independent choices, and the calling code does not change with them. The full description and advice on choosing are in [docs/deployment.md](docs/deployment.md) (Chinese).

**Process layout.** The gateway can run as a separate process (`feiyu-memgate serve` plus `MemoryClient`), or directly inside the chat process (`LocalClient`: no service, no port, no token). The two clients have the same methods and return values. The cost of running in-process is that the trust boundary is gone: the chat process holds the store keys itself, so the access policy guards only against mistakes in your own code, not against a compromised process.

**Retrieval tier.** Six tiers, from no model to a 1.16 GB model. The vector backend has built-in score conversion and default score thresholds for these five models.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/tiers.en.dark.png">
    <img src="docs/assets/charts/tiers.en.light.png" alt="Tier comparison chart: Hit@5, delivered hit, precision and quiet rate for the six retrieval tiers, with the resident memory and model file size of the vector model service" width="100%">
  </picture>
</p>

| Tier | Model file | Resident memory | Hit@1 | Hit@5 | Delivered hit | Precision | Delivered F1 | Quiet rate | p50 latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `sqlite` (no model) | none | negligible | 61.7% | 81.7% | 46.7% | 89.3% | 61.3% | 70.0% | 2 ms |
| bge-small-zh-v1.5 | 48 MB | 175 MiB | 75.8% | 92.5% | 86.7% | 69.0% | 76.8% | 20.0% | 6 ms |
| bge-base-zh-v1.5 | 110 MB | 238 MiB | 82.5% | 93.3% | 88.3% | 70.2% | 78.2% | 37.5% | 19 ms |
| embeddinggemma | 622 MB | 761 MiB | 81.7% | 99.2% | 97.5% | 63.8% | 77.1% | 37.5% | 13 ms |
| qwen3-embedding 0.6B | 639 MB | 1.20 GiB | 84.2% | 95.8% | 94.2% | 70.7% | 80.8% | 32.5% | 75 ms |
| bge-m3 | 1.16 GB | 1.36 GiB | 81.7% | 98.3% | 96.7% | 56.9% | 71.6% | 32.5% | 24 ms |

The quality columns come from the held-out test set, with each model using its own preset and the default profile (broad); the numbers with the precise profile pinned are in the threshold profile table below. Model file size and resident memory were measured during the development-set run (CPU, with the container loading only that one model).

- **The smallest vector model is already much better than keywords.** At 175 MiB of memory, delivered F1 goes from 61.3% to 76.8% (precise profile: 55.1% to 78.0%), and the delivered hit on paraphrase questions from 3.6% to 80.0%, so they are no longer a blind spot.
- **The cost of a small model is in ranking.** The two small Chinese models have a Hit@5 5 – 6 percentage points below bge-m3 (92.5% and 93.3% against 98.3%), and they support Chinese only.
- **embeddinggemma matches bge-m3 with a little over half the memory.** Its Hit@5 and quiet rate are both at least as high as bge-m3, and the same holds on the development set.
- **The order between neighboring tiers is not stable, and it changes with the threshold profile.** qwen3-embedding was the worst on the development set. On the test set, with the precise profile it has the highest delivered hit (85.0%) and delivered F1 (83.4%) but the lowest quiet rate (37.5%) and the highest latency (75 ms); with the broad profile the highest delivered hit is embeddinggemma's (97.5%) and qwen3-embedding has 94.2%. bge-base-zh matched bge-m3 on the development set and ranks a little below it on the test set; its delivered F1 is above bge-m3's with the broad profile (78.2% against 71.6%) and below it with the precise profile (78.7% against 82.1%). Only the three points above are reliable.
- **The score threshold has to follow the model.** Using bge-m3's precise-profile score thresholds unchanged on bge-base-zh gives a delivered hit of only 27.5% on the development set. For a model outside the built-in presets, the backend prints a one-line warning at startup.

**Threshold profile.** The default is the broad profile (`broad`): it delivers more and almost never misses an answer. `[recall] profile = "precise"` is the precise profile (the previous default): few and accurate. The measured comparison on the test set, default profile first for each configuration:

| Configuration | Profile | Delivered hit | Precision | Items delivered per question | Quiet rate |
|---|---|---:|---:|---:|---:|
| `sqlite` | broad (default) | 46.7% | 89.3% | 0.55 | 70.0% |
|  | precise | 39.2% | 93.0% | 0.41 | 75.0% |
| `embedding` (bge-m3) | broad (default) | 96.7% | 56.9% | 2.46 | 32.5% |
|  | precise | 81.7% | 82.5% | 0.98 | 65.0% |
| `hybrid` | broad (default) | 92.5% | 62.6% | 2.01 | 35.0% |
|  | precise | 65.0% | 90.4% | 0.66 | 75.0% |
| `full` | broad (default) | 93.3% | 60.1% | 2.08 | 35.0% |
|  | precise | 65.0% | 90.2% | 0.67 | 75.0% |
| `openviking` | broad (default) | 90.8% | 75.9% | 1.50 | 40.0% |
|  | precise | 87.5% | 80.2% | 1.17 | 40.0% |
| `embedding` (bge-small-zh) | broad (default) | 86.7% | 69.0% | 1.79 | 20.0% |
|  | precise | 76.7% | 79.4% | 0.94 | 47.5% |
| `embedding` (bge-base-zh) | broad (default) | 88.3% | 70.2% | 1.51 | 37.5% |
|  | precise | 72.5% | 86.2% | 0.77 | 65.0% |
| `embedding` (embeddinggemma) | broad (default) | 97.5% | 63.8% | 2.04 | 37.5% |
|  | precise | 76.7% | 85.8% | 0.81 | 72.5% |
| `embedding` (qwen3-embedding) | broad (default) | 94.2% | 70.7% | 1.76 | 32.5% |
|  | precise | 85.0% | 81.9% | 0.97 | 37.5% |

For the eight vector and hybrid configurations (everything except `sqlite`), the broad profile has a delivered hit 3.3 – 28.3 points higher than the precise one, precision 4.3 – 30.2 points lower, a quiet rate 0.0 – 40.0 points lower, and delivers 0.33 – 1.48 more items per question. The broad profile of `embedding` (bge-m3) almost never misses an answer (96.7%). The cost is one and a half more items per question and half the quiet rate. More than half of what it delivers is still correct (56.9% – 75.9% for the vector and hybrid configurations), while systems that deliver the top 5 to 20 items by default are at 5% – 20%. `openviking` differs least, because its precise thresholds are already wide. For the keyword backend the change is small (delivered hit 39.2% → 46.7%), because its misses come from retrieval itself.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/profiles.en.dark.png">
    <img src="docs/assets/charts/profiles.en.light.png" alt="Scatter plot: the position of the precise and broad profiles on items delivered per question and on the share of delivered items that are correct, and the position of the other projects" width="100%">
  </picture>
</p>

## Forgetting curve (experimental, off by default)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/memory-lifecycle.en.dark.png">
    <img src="docs/assets/memory-lifecycle.en.light.png" alt="State diagram: the lifecycle of one memory from written and active through down-weighted and expired to archived and purged, and the transitions for use, pin and restore" width="100%">
  </picture>
</p>

- **Retention**. `R = 0.5 ^ (days / half-life)`, where half-life = base × 1.6 ^ min(use count, 8) and the base defaults to 90 days. The more a memory is used, the more slowly it is forgotten.
- **Two clocks**. The retention used for ranking is counted from the memory's own date or its last use. The retention used for deletion is counted from when the ledger started managing the memory, or its last use. So turning on forgetting on a store that has existed for a long time deletes nothing that day.
- **Ranking**. The `freshness` stage multiplies the score by a factor between 0.7 and 1. It affects only order and does not take part in the score threshold check.
- **Sweep**. Expired memories are written to the archive and then deleted. Each sweep handles at most `max(5, 2%)` items per store. The archive is kept for 90 days, and during that time `feiyu-memgate forget restore` writes an item back unchanged.
- **Pin**. Memories pinned by address wildcard, by client or by hand are not down-weighted and not forgotten.

Simulation results (hybrid backend, 540 days, all 240 memories ingested on the same day; the score-threshold profile is pinned to precise and the simulation was not repeated with the broad profile; the questions fall into three groups: asked often, asked occasionally, never asked):

| Setting | Items in store | Asked often Hit@5 | Asked occasionally Hit@5 | Never asked Hit@5 | Items delivered per question | Noise |
|---|---:|---:|---:|---:|---:|---:|
| No forgetting (baseline) | 240 | 70.0% | 62.5% | 62.5% | 0.77 | 15.2% |
| Default | 57 | 70.0% | 62.5% | 0.0% | 0.49 | 10.2% |
| Default + pin identity memories | 85 | 70.0% | 62.5% | 15.0% | 0.55 | 10.6% |
| Aggressive (half-life 30 days) | 55 | 70.0% | 60.0% | 0.0% | 0.47 | 8.8% |

The store shrinks to about a quarter, and the hit rate for questions that were ever asked is essentially unchanged. Facts nobody ever asks about are almost all forgotten, which is both the design goal and the cost. Noise does not improve consistently: it fell on the hybrid backend, but on the keyword backend the default group rose from 7.8% to 10.5%.

<details>
<summary>Curves from the simulation</summary>
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/charts/forgetting.en.dark.png">
    <img src="docs/assets/charts/forgetting.en.light.png" alt="Line chart: items in store and Hit@5 for frequently asked questions over the 540-day simulation, for two backends" width="100%">
  </picture>
</p>
</details>

This feature has been verified only in simulation. Traffic was generated from a fixed distribution, there are no real usage records, and the default parameters were not tuned on real data. The mechanism, configuration and full results are in [docs/forgetting.md](docs/forgetting.md) (Chinese) and [bench/results/forgetting/summary.md](bench/results/forgetting/summary.md) (Chinese).

## Quick start

Python 3.11 or later is required. The minimal setup needs no vector model and no docker.

```bash
git clone https://github.com/N0rmal-114/feiyu-memgate.git
cd feiyu-memgate
pip install -e .
```

Prepare a token for the client (at least 32 characters) and put it in an environment variable:

```bash
export GATEWAY_TOKEN_OWNER="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

```powershell
$env:GATEWAY_TOKEN_OWNER = python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Check the configuration and start:

```bash
feiyu-memgate check --config examples/quickstart/gateway.toml
```

```bash
feiyu-memgate serve --config examples/quickstart/gateway.toml
```

In another terminal, write one memory and query it:

```python
import os
from feiyu_memgate.client import MemoryClient

client = MemoryClient("http://127.0.0.1:8080", token=os.environ["GATEWAY_TOKEN_OWNER"])
# Example memory text, in Chinese: "I am allergic to peanuts and like going to the zoo on weekends"
print(client.remember_status("我对花生过敏，周末喜欢去动物园"))   # saved
# Example query, in Chinese: "where do I like to go on weekends"
print(client.recall_detail("周末喜欢去哪"))
```

The interface is plain HTTP with JSON, so curl works too. A Windows terminal may not send Chinese text as UTF-8, and the gateway then replies 400. In that case use the Python client above.

## Using it in a chat program

```python
from feiyu_memgate.client import MemoryClient, BlockFormatter, build_query

client = MemoryClient("http://127.0.0.1:8080", token="<gateway token>")
formatter = BlockFormatter(labels={"private": "你们私聊里的"})   # label text, Chinese: "from your private chat"

def before_llm(current_message, previous_message):
    query = build_query(current_message, previous_message)   # filler replies and bare emoji are not worth a query; returns None
    if query is None:
        return ""
    detail = client.recall_detail(query)                      # does not raise; returns None if the query failed
    if not detail:
        return ""
    return formatter.format(detail["results"], more=detail["more"])

# Example memory text, in Chinese: "The owner is allergic to peanuts"
status = client.remember_status("主人对花生过敏")   # "saved" / "failed" / "limited" / "unsure"
```

We suggest appending the data block that `before_llm` returns to the end of the current message when sending it to the model. Do not store it in the conversation history and do not put it in the system prompt. That keeps the system prompt identical on every turn, so the earlier history can hit the model provider's prefix cache.

If a reply did use certain memories, report them with `client.report_used(...)`. The forgetting curve uses this to lengthen their half-lives.

If you do not want to run a separate gateway process, switch to the in-process client. The rest of the code stays the same ([examples/embedded](examples/embedded/), described in [docs/embedded.md](docs/embedded.md) (Chinese)):

```python
from feiyu_memgate.client import LocalClient

client = LocalClient.from_config("gateway.toml", client="owner")   # no service, no token
```

An AstrBot integration example is in [integrations/astrbot](integrations/astrbot/).

### DeepSeek Harness (DSH) plugins

Two DSH plugin packages live under `plugins/` in this repository. Each is published to npm:

| Package | What it does |
|---|---|
| [dsh-feiyu-memgate](plugins/dsh-feiyu-memgate/README.md) | Adds long-term memory to DSH conversations: it ships this gateway and starts it by itself, recalls at the start of every turn, gives the model a remember tool and a search tool, and has a settings page. Each feature can be switched off on its own. The default is keyword retrieval, which needs no model and downloads nothing. |
| [dsh-feiyu-memgate-embed](plugins/dsh-feiyu-memgate-embed/README.md) | Optional add-on: runs an embedding model on this machine, on the CPU only, so that reworded questions find their memories too. A model is downloaded only after you press "Download and enable" on the settings page. |

To install, add `dsh-feiyu-memgate` on DSH's plugin page, or run `dsh plugin --profile <name> add dsh-feiyu-memgate`. Requirements, settings and what was tested are in each package's README.

## Configuration examples

To switch to the vector backend, change only the store section ([examples/hybrid](examples/hybrid/gateway.toml) is a hybrid keyword plus vector configuration, and [examples/openviking](examples/openviking/gateway.toml) connects to OpenViking):

```toml
[stores.private]
type = "embedding"
path = "data/vector.db"
url = "http://127.0.0.1:11434/v1"     # any OpenAI-compatible embeddings endpoint
model = "bge-m3"                      # for a smaller model, change only this line and the store file; the score threshold preset is chosen automatically by model name
```

Multi-tenant: each group gets its own store, and a group-chat token cannot read the private-chat store ([examples/multi-tenant](examples/multi-tenant/gateway.toml)):

```toml
[tenants.group]
type = "directory"
path = "data/groups"
create = false                        # only registered groups have memory

[clients.group]
token_env = "GATEWAY_TOKEN_GROUP"
tenants = "group"                     # every request must carry tenant_id
recall = ["@tenant"]                  # "@tenant" is the current group's own store
write = "@tenant"
public = true                         # public setting: stricter redaction
daily_writes = 20                     # per group, per day
daily_writes_total = 500              # all groups combined, per day
# tenant_binding = "token"            # optional: one derived token per group, minted with feiyu-memgate tenants token
```

Write your own recall stage ([full code](examples/custom-stage/recency_stage.py)):

```python
from feiyu_memgate.pieces import RecallStage

class Recency(RecallStage):
    def prepare(self, candidates, ctx):
        for candidate in candidates:
            candidate.score *= self._factor(candidate.date)
        return candidates
```

```toml
[recall]
stages = ["min_score", { type = "recency_stage:Recency", half_life_days = 30 }, "redact", "dedupe", "budget"]
```

Put the class file next to the configuration file. No installation is needed. The contract and usage of each of the six slots are in [docs/pieces.md](docs/pieces.md) (Chinese).

## Command line

| Command | Purpose |
|---|---|
| `feiyu-memgate serve --config <file>` | Start the gateway |
| `feiyu-memgate check --config <file>` | Check the configuration and print how each component was assembled (tokens are not shown) |
| `feiyu-memgate pieces` | List all registered components |
| `feiyu-memgate eval` | Evaluate recall with your own example memories, to help choose score thresholds |
| `feiyu-memgate forget status / sweep / archive / restore / pin / unpin / explain` | Forgetting curve: status, sweep, archive, restore, pin, and explain a single item (memory text is not shown) |
| `feiyu-memgate backup` | Encrypted backup and export |
| `feiyu-memgate tenants` | Manage tenant key files. `tenants token` mints a token bound to a single tenant |

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) (Chinese) | What one recall and one write go through |
| [docs/deployment.md](docs/deployment.md) (Chinese) | Deployment options: process layout, retrieval tier and threshold profile, with the data for each and advice on choosing |
| [docs/embedded.md](docs/embedded.md) (Chinese) | Running in-process: usage, limits, and how it differs from a separate service |
| [docs/configuration.md](docs/configuration.md) (Chinese) | Every configuration option and the HTTP interface |
| [docs/backends.md](docs/backends.md) (Chinese) | How each backend works, and where the default score thresholds came from |
| [docs/pieces.md](docs/pieces.md) (Chinese) | Slot contracts, and how to choose, replace and implement them |
| [docs/forgetting.md](docs/forgetting.md) (Chinese) | Mechanism, configuration and operation of the forgetting curve |
| [docs/security.md](docs/security.md) (Chinese) | What it defends against, how, and what it cannot defend against |
| [docs/roadmap.md](docs/roadmap.md) (Chinese) | What is not done yet |
| [docs/design/](docs/design/) (Chinese) | Original design documents from the project this was extracted from, and the audit done before the rework |
| [bench/](bench/README.md) (Chinese) | Benchmark data, rules, adapters and reproduction steps |
| [bench/results/design/](bench/results/design/features.en.md) | Feature comparison of the projects, and this project's design self-check |
| [plugins/](plugins/dsh-feiyu-memgate/README.md) | The DeepSeek Harness plugin and its embedding add-on |
| [research/](research/) (Chinese) | Survey reports on similar memory projects: how they work, how mature they are, what can be borrowed |

## Status and limits

Version 0.1.0. Running the tests needs no installation:

```bash
python -m unittest discover -s tests -t .
```

There are currently 1394 tests. The gateway's approach comes from the version that actually ran in the project it was extracted from, but note the following:

- This version, after the rework into a framework, has not yet run in production for a long time.
- The forgetting curve has been verified only in simulation and is off by default.
- The logic of the AstrBot integration example is tested against stand-ins for AstrBot's interface. It has not been run in a real AstrBot.
- The DSH plugins were tested on Windows in an installed DeepSeek Harness 0.2.2-rc.1 with an isolated configuration: loading, the bundled gateway, the settings page and a local embedding model. They were not tested on macOS or Linux, in the desktop application's own window, or in a conversation with a real model. Their tests run with `node --test` in each package's folder and are not part of the number above.
- By default, a multi-tenant client still has the caller report its tenant id, and one token can read any registered tenant. To give a token to a program that is not fully trusted, use tenant-bound tokens (`tenant_binding = "token"`). A bound token has no expiry date. Revoke it by raising the generation or removing the tenant.
- When running in-process (`LocalClient`) there is no trust boundary, see "Deployment tiers" above.
- Redaction is regex-based. It cannot recognize an isolated key with no keyword hint, and instruction-like text inside memories is not rewritten.
- Limit counters are in memory and reset on restart. The forgetting archive is stored unencrypted. `/healthz?deep=1` needs no token.
- The default broad profile delivers more items and noisier ones, speaks up more often when there is no answer, and makes the context grow faster when the memory block stays in the conversation history. For few and accurate items write `[recall] profile = "precise"`; the cost is that paraphrase questions are missed often, see the data above.
- `newer_first` reorders by date only. It does not recognize whether two memories really contradict each other.
- The score threshold presets of the lighter models were each chosen on a single Chinese development set only. Multilingual behavior, concurrency, and scale beyond a few tens of thousands of items have not been tested.

## License

[MIT](LICENSE) © N0rmal
