# Public datasets: results

The project's own question set is small and was written for this project. The same measurement, retrieval and
delivery only (no answer generation, no judge model), was repeated on two published datasets, PerLTQA and LoCoMo, for
this project and for the projects it is compared with. This folder holds the results of those runs and the code that
turns them into tables and charts.

**In this folder:** the result files (`results/`), the scorer that defines every figure (`score.py`), the report and
chart generators (`report.py`, `charts.py`), the price table used for the cost figures (`prices.json`) and the packer
of the per-question files (`pack.py`).

**Not in the repository:** the dataset files (licensed CC BY-NC 4.0, which does not fit this MIT repository), the code
that loads them and runs the systems, and the adapters written for dsh-fish-memory, dsh-prime-memory, dsh-soul-md and
Hindsight. The tables and charts can be regenerated from the files here; re-scoring from the per-question files needs
the datasets prepared as described below.

## Datasets

| Dataset | Source and revision | File (sha256) | Licence |
|---|---|---|---|
| PerLTQA, Chinese part | [Elvin-Yiming-Du/PerLTQA](https://github.com/Elvin-Yiming-Du/PerLTQA) at `8d9e198` | `Dataset/zh/perltmem.json` (`5869ee39…ee0ab7b`), `Dataset/zh/perltqa.json` (`aa19a062…e8ca9bd`) | CC BY-NC 4.0 |
| LoCoMo | [snap-research/locomo](https://github.com/snap-research/locomo) at `3eb6f2c` | `data/locomo10.json` (`79fa87e9…a698ff4`) | CC BY-NC 4.0 |

| Dataset | Stores | Memories | Answerable questions | No-answer questions | Median memory length |
|---|---:|---:|---:|---:|---:|
| PerLTQA | 32 | 2,155 | 8,588 | 0 | 260 characters |
| LoCoMo | 10 | 5,882 | 1,535 | 446 | 116 characters |

A store is one independent memory store. Every system builds each store separately and is asked that store's
questions. How the datasets were turned into stores:

- **PerLTQA.** A store is a character that has questions (32 of the 141 characters of the memory file), named `p01`
  to `p32` in the order of the question file. A memory is one stored record: a profile entry, a social relationship,
  an event (its content), or a dialogue (all its lines, one per line). The question type is the section the question
  sits in (profile, social relationship, events, dialogues). The evidence of a question is the record it references;
  5 questions whose reference names no record are left out.
- **LoCoMo.** A store is one conversation (`conv-26` to `conv-50`). A memory is one dialogue turn,
  `<speaker>: <text>`, dated with its session's day; image captions are not used. The evidence of a question is the
  turns its dialogue ids name; 5 questions whose ids name no turn are left out. Categories 1 to 4 are multi-hop,
  temporal, open-domain and single-hop; category 5 (adversarial) is the set of no-answer questions.
- In both, memories are numbered `m000001`, `m000002`, ... in ingest order: by date, undated first, ties in dataset
  order. Questions are numbered `q00001`, ... in dataset order. Result files refer to memories and questions by these
  ids only and hold no memory or question text.

## Files

| File | What it does |
|---|---|
| `score.py` | the definition of every figure (in its docstring), computed from the per-question files and the stores; writes `<config>.metrics.json`, `same-stores-*.json` and `tables.md`. It needs the stores, so it can be run only where the datasets have been prepared |
| `report.py` | `results/summary.md` and `results/summary.json` from the metrics files; `--readme zh` or `--readme en` prints the tables used in the top-level README |
| `charts.py` | the charts of the top-level README section (`docs/assets/charts/ext-*.svg`) from `results/summary.json` |
| `pack.py` | compresses the per-question files for the repository, and restores them (`--unpack`) |
| `prices.json` | the list prices used for the cost figures, with their date |

```bash
python -I bench/external/report.py            # results/summary.md and results/summary.json
python bench/external/charts.py               # docs/assets/charts/ext-*.svg
bash bench/render_charts.sh                   # PNG copies of the charts
python -I bench/external/pack.py --unpack     # only to read the per-question files
```

## Result files

| File under `results/<dataset>/` | Content |
|---|---|
| `<config>.metrics.json` | every figure of one configuration on the stores it ran on, overall and per question type, with its usage record |
| `same-stores-<label>.json`, `.md` | every configuration that covers a given set of stores, scored on exactly those stores |
| `latency-summary.json` | p50 and p95 of the latency pass, and how each figure was measured |
| `tables.md` | all of the above as tables |
| `<config>.json.gz` | the per-question record: for every question the ranked memory ids with scores, the delivered memory ids, and the milliseconds of the call |

`results/summary.md` and `results/summary.json` collect both datasets. `results/longmemeval/` holds one run of this
project's keyword configuration on LongMemEval-S, which is not part of the comparison (see the end of this page).

Spending is part of the results. A usage record has `llm_calls`, `llm_input_tokens`, `llm_output_tokens`,
`llm_cached_input_tokens`, `llm_cost_usd`, the wall-clock `ingest_seconds` and `question_seconds`, and for this
project's configurations the embedding requests. The LLM fields of this project's configurations are measured zeros:
its stores call no language model.

## Limits that decide what can be delivered

The gateway hands over at most 1,500 characters per answer (the `budget` stage leaves out a memory that does not fit,
whole) and drops an item longer than 65,536 characters. Nothing is shortened to fit. No evidence memory of either
dataset is longer than 1,500 characters (PerLTQA: 8,588 evidence items; LoCoMo: 2,358), so every one of them can be
delivered. The embedding model (bge-m3) reads about 2,048 tokens of a text; both datasets stay far below that.

## Which stores each configuration ran on

Configurations that call no model ran on every store, with one exception. Configurations that call a model ran on a
sample: the first stores in ascending id order (PerLTQA `p01`, `p02`, ...; LoCoMo `conv-26`, `conv-30`, `conv-41`),
so a longer run only adds stores and never replaces one. mem0 with LLM extraction is the paid configuration that ran
on everything.

| Configuration | PerLTQA (32 stores) | LoCoMo (10 stores) | Why |
|---|---|---|---|
| every configuration without a model, except Hindsight | all | all | |
| `mem0-llm-*` (both models) | all | all | |
| `graphiti-deepseek-flash` | `p01` to `p24` | first 3 | cost |
| `graphiti-claude-haiku-5-5` | first 10 | first 3 | cost |
| `hindsight-chunks`, `hindsight-llm-*` | first 5 | `conv-26` | running time: 25 to 65 minutes per store |
| `dsh-fish-memory-expand-*` | first 3 | `conv-26` | sample for configurations that call a model |

`hindsight-chunks` also has results for seven more LoCoMo stores (`conv-42` to `conv-50`) from before the sample was
fixed; they are in its files and in `tables.md`, and are used in no comparison. A sampled configuration is never
compared with a figure over other stores: every system is scored on exactly the stores of the sample
(`same-stores-*`, `results/summary.md`).

## Delivered items that are not a whole memory

- dsh-prime-memory shortens a memory over 500 characters (or over what is left of its 2,000-character budget) when it
  injects it. A shortened item is matched to its memory by its unique prefix and marked `shortened`, with the
  injected length recorded (`chars`); the scorer counts it as that memory delivered, with the recorded length.
- Hindsight with a model delivers observations a model wrote. An observation is mapped to the memories its source
  facts came from; one whose sources the server did not return has no id and counts as a delivered item that is not
  evidence (`unmapped_items`).

## Configurations that call a language model

mem0 with LLM extraction, Graphiti, Hindsight and the keyword expansion of dsh-fish-memory each ran with
`deepseek-flash` and with `claude-haiku-5-5`.

- **Thinking mode is off for both models.** This is a setting of this benchmark, made to bound the running time; it
  is not the providers' default, so the figures are not those of a default-settings run. Each usage record says so
  (`llm_thinking: "disabled"`) and carries the API's own reasoning-token counter, which must stay zero
  (`llm_reasoning_tokens`). For `deepseek-flash` the request carries `thinking: {"type": "disabled"}` on the chat
  completions endpoint; for `claude-haiku-5-5` the same field on the Messages API.
- **A store counts only if its run is valid.** A store is rejected, and not merged, when any memory could not be
  processed because its model call failed, when the run recorded no model calls or no tokens, when the API reported
  reasoning tokens despite the setting, or when more than 5% of the extractions could not be parsed.
- **Cost** is the list price of the counted tokens (`prices.json`, prices of 2026-11-16), each `deepseek-flash` call
  at the peak or off-peak rate of the window it was made in. Cache writes of `claude-haiku-5-5` are counted at the
  normal input rate, which slightly underestimates the Hindsight row that uses the cache. Only valid runs are counted.

## Latency

Quality figures come from runs in which several stores were processed in parallel. Latency comes from a separate
pass: the questions of the median-size store of each dataset (PerLTQA `p15`, 69 memories, 214 questions; LoCoMo
`conv-41`, 663 memories, 193 questions) asked one at a time into a fresh store on an idle machine. Every configuration
that uses vectors makes a real request to the embedding model for each question, and that request is part of the
figure; this holds for this project's configurations too (no vector cache in this pass). The store of a configuration
that calls a model cannot be rebuilt without paying again, so those configurations are reported from the parallel
runs and labelled "measured under load"; `latency-summary.json` says which kind each figure is.

## LongMemEval (not part of the comparison)

LongMemEval-S (cleaned, MIT) has 500 questions, each with its own haystack of about 490 turns: 500 separate stores
and 246,750 memories. One run was made, this project's keyword configuration, both profiles
(`results/longmemeval/`, 470 answerable and 30 no-answer questions): Hit@1 / Hit@5 / Hit@10 are 3.2 / 9.1 / 12.8%,
the delivered hit is 10.2%, the quiet rate on the no-answer questions is 46.7% (n = 30); at session level Hit@10 is
65.1%. It is left out of the comparison for two reasons:

- Every question needs its own store. Embedding the 189,522 distinct texts takes about 10 hours on the benchmark's
  embedder, and the systems that call a model would need hundreds of hours.
- 72,940 of the 246,750 turns (29.6%) are longer than 1,500 characters and can never be delivered under the gateway's
  output budget; 21 of the 886 evidence items are among them.
