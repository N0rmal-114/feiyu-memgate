# External datasets: summary

Generated on 2026-11-27 by `bench/external/report.py` from the metrics files of this folder; definitions in `bench/external/score.py`. Rows of this project in bold are the default profile.

## PerLTQA, all 32 stores, 8,588 answerable questions

| System | Retrieval | Hit@1 | Hit@5 | Hit@10 | Items delivered | Delivered hit | Precision | Delivered F1 | Characters |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **feiyu-memgate sqlite** | keyword | 65.8% | 87.3% | 90.8% | 2.02 | 83.0% | 54.2% | 65.6% | 748 |
| feiyu-memgate sqlite (precise profile) | keyword | 65.8% | 87.3% | 90.8% | 1.80 | 81.1% | 58.1% | 67.7% | 671 |
| **feiyu-memgate embedding** | vector | 45.4% | 83.3% | 89.7% | 3.52 | 78.2% | 30.1% | 43.5% | 636 |
| feiyu-memgate embedding (bge-m3, precise profile) | vector | 45.4% | 83.3% | 89.7% | 1.76 | 56.0% | 41.6% | 47.7% | 317 |
| **feiyu-memgate hybrid** | hybrid | 67.3% | 95.0% | 97.3% | 3.44 | 93.5% | 35.4% | 51.3% | 862 |
| feiyu-memgate hybrid (precise profile) | hybrid | 67.3% | 95.0% | 97.3% | 1.83 | 82.7% | 57.0% | 67.5% | 498 |
| dsh-mnemon + embeddings | hybrid | 69.4% | 89.7% | 90.9% | 3.15 | 83.5% | 31.5% | 45.8% | 701 |
| lingshu | keyword | 69.4% | 88.6% | 92.1% | 4.00 | 7.4% | 1.9% | 3.0% | 1,473 |
| memsearch | hybrid | 51.9% | 87.7% | 92.6% | 5.00 | 87.7% | 17.5% | 29.2% | 1,165 |
| meow-memory | keyword | 69.5% | 87.4% | 91.6% | 2.00 | 80.4% | 40.3% | 53.6% | 644 |
| dsh-soul-md | keyword | 58.9% | 87.1% | 93.0% | 4.98 | 87.1% | 17.6% | 29.3% | 1,556 |
| dsh-prime-memory | keyword | 25.0% | 85.4% | 93.2% | 4.95 | 88.2% | 17.9% | 29.8% | 1,557 |
| dsh-prime-memory + embeddings | hybrid | 15.0% | 83.9% | 92.2% | 5.00 | 88.7% | 17.8% | 29.6% | 1,389 |
| mem0 (verbatim) | vector | 45.5% | 83.4% | 89.8% | 20.00 | 94.8% | 4.7% | 9.0% | 3,827 |
| OpenViking | vector | 45.4% | 83.3% | 89.7% | 9.92 | 89.7% | 9.2% | 16.6% | 1,873 |
| mem0 (LLM extraction, claude-haiku-5-5) | extraction | 44.3% | 79.9% | 85.0% | 14.02 | 89.5% | 6.7% | 12.5% | 3,882 |
| mem0 (LLM extraction, deepseek-flash) | extraction | 47.9% | 79.0% | 84.4% | 13.08 | 89.5% | 7.4% | 13.6% | 3,576 |
| dsh-mnemon | keyword | 59.2% | 73.1% | 74.4% | 1.25 | 58.7% | 49.5% | 53.7% | 466 |
| SeekMemory | keyword | 37.6% | 51.1% | 85.5% | 6.04 | 77.4% | 15.3% | 25.5% | 633 |

## PerLTQA: configurations left out

On this dataset these configurations stored over 5% fewer memories than they were sent; they are in no table and no chart of this dataset:

- dsh-fish-memory: 1,361 of 2,155 memories stored (63.2%).
- dsh-fish-memory + claude-haiku-5-5 expansion: 155 of 225 memories stored (68.9%).
- dsh-fish-memory + deepseek-flash expansion: 155 of 225 memories stored (68.9%).

The project accepts at most 8 memories a day (the default of its dailyQuota setting). This benchmark writes each memory on its own date, and the writes of a day beyond the eighth were refused: a LoCoMo session has dozens of turns on one day, and the PerLTQA memories without a date were given the earliest day of their store. This comes from the way the benchmark fed the store, which does not fit that setting: a result on these stores says nothing about the project.

## PerLTQA, Hit@5 by question type

| System | Retrieval | profile (n = 357) | relationships (n = 897) | events (n = 4,501) | dialogues (n = 2,833) |
|---|---|---:|---:|---:|---:|
| **feiyu-memgate sqlite** | keyword | 3.6% | 48.4% | 96.3% | 95.8% |
| **feiyu-memgate embedding** | vector | 91.0% | 95.2% | 86.4% | 73.6% |
| **feiyu-memgate hybrid** | hybrid | 91.6% | 96.3% | 96.0% | 93.4% |
| dsh-mnemon + embeddings | hybrid | 89.9% | 92.8% | 92.4% | 84.5% |
| lingshu | keyword | 4.8% | 67.7% | 96.6% | 93.1% |
| memsearch | hybrid | 91.6% | 94.9% | 90.7% | 80.0% |
| meow-memory | keyword | 5.6% | 56.3% | 95.9% | 94.1% |
| dsh-soul-md | keyword | 30.5% | 70.2% | 97.3% | 83.6% |
| dsh-prime-memory | keyword | 6.4% | 67.8% | 92.0% | 90.5% |
| dsh-prime-memory + embeddings | hybrid | 40.1% | 87.8% | 86.8% | 83.6% |
| mem0 (verbatim) | vector | 91.0% | 95.2% | 86.5% | 73.7% |
| OpenViking | vector | 91.0% | 95.1% | 86.4% | 73.7% |
| mem0 (LLM extraction, claude-haiku-5-5) | extraction | 84.3% | 80.6% | 82.1% | 75.8% |
| mem0 (LLM extraction, deepseek-flash) | extraction | 78.7% | 79.4% | 85.9% | 67.8% |
| dsh-mnemon | keyword | 5.9% | 35.8% | 80.6% | 81.3% |
| SeekMemory | keyword | 28.9% | 77.1% | 50.8% | 46.1% |

## PerLTQA, the first 10 stores (p01 – p10), 2,343 answerable questions

| System | Retrieval | Hit@1 | Hit@5 | Hit@10 | Items delivered | Delivered hit | Precision | Delivered F1 | Characters |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Graphiti (deepseek-flash) | graph | 47.3% | 78.9% | 82.4% | 5.05 | 82.4% | 25.1% | 38.5% | 1,101 |
| Graphiti (claude-haiku-5-5) | graph | 42.9% | 72.7% | 76.5% | 5.39 | 76.5% | 22.8% | 35.1% | 1,023 |
| **feiyu-memgate sqlite** | keyword | 67.3% | 87.7% | 91.3% | 1.95 | 83.9% | 55.9% | 67.1% | 711 |
| **feiyu-memgate hybrid** | hybrid | 69.0% | 95.6% | 97.8% | 3.37 | 94.4% | 36.2% | 52.3% | 832 |

## PerLTQA, the first 5 stores (p01 – p05), 1,352 answerable questions

| System | Retrieval | Hit@1 | Hit@5 | Hit@10 | Items delivered | Delivered hit | Precision | Delivered F1 | Characters |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Hindsight (no model) | hybrid | 74.9% | 96.8% | 97.6% | 16.76 | 98.2% | 6.8% | 12.7% | 2,709 |
| Hindsight (deepseek-flash) | extraction | 57.2% | 93.0% | 96.3% | 35.07 | 96.7% | 2.9% | 5.6% | 10,268 |
| Hindsight (claude-haiku-5-5) | extraction | 54.7% | 91.6% | 95.9% | 40.19 | 98.2% | 2.6% | 5.0% | 11,772 |
| **feiyu-memgate sqlite** | keyword | 68.9% | 88.5% | 91.6% | 1.94 | 85.1% | 56.7% | 68.1% | 724 |
| **feiyu-memgate hybrid** | hybrid | 71.2% | 96.2% | 98.0% | 3.32 | 95.1% | 37.2% | 53.5% | 848 |

## LoCoMo, all 10 stores, 1,535 answerable questions

| System | Retrieval | Hit@1 | Hit@5 | Hit@10 | Items delivered | Delivered hit | Precision | Delivered F1 | Characters | Quiet rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **feiyu-memgate sqlite** | keyword | 20.4% | 36.4% | 46.2% | 4.70 | 34.7% | 9.1% | 14.4% | 1,273 | 0.0% |
| feiyu-memgate sqlite (precise profile) | keyword | 20.4% | 36.4% | 46.2% | 4.43 | 34.5% | 10.9% | 16.5% | 1,201 | 0.4% |
| **feiyu-memgate embedding** | vector | 23.4% | 51.6% | 63.9% | 4.75 | 50.2% | 12.6% | 20.1% | 535 | 0.2% |
| feiyu-memgate embedding (bge-m3, precise profile) | vector | 23.4% | 51.6% | 63.9% | 2.56 | 36.7% | 21.4% | 27.0% | 293 | 10.8% |
| **feiyu-memgate hybrid** | hybrid | 36.4% | 58.0% | 66.1% | 4.81 | 55.8% | 13.3% | 21.5% | 953 | 0.0% |
| feiyu-memgate hybrid (precise profile) | hybrid | 36.4% | 58.0% | 66.1% | 2.53 | 45.7% | 29.0% | 35.5% | 501 | 18.4% |
| mem0 (LLM extraction, claude-haiku-5-5) | extraction | 40.7% | 73.7% | 81.2% | 19.04 | 86.3% | 5.8% | 10.8% | 3,058 | 0.0% |
| mem0 (LLM extraction, deepseek-flash) | extraction | 33.6% | 70.0% | 79.8% | 18.68 | 85.7% | 5.8% | 10.8% | 2,917 | 0.0% |
| mem0 (verbatim) | vector | 36.5% | 65.1% | 72.0% | 20.00 | 78.2% | 4.7% | 8.9% | 2,588 | 0.0% |
| memsearch | hybrid | 37.9% | 64.1% | 73.3% | 5.00 | 63.8% | 13.9% | 22.8% | 689 | 0.0% |
| dsh-mnemon + embeddings | hybrid | 33.9% | 56.2% | 63.4% | 3.42 | 49.1% | 16.5% | 24.7% | 432 | 0.0% |
| OpenViking | vector | 23.3% | 51.9% | 63.8% | 10.00 | 63.8% | 7.1% | 12.8% | 1,124 | 0.0% |
| dsh-soul-md | keyword | 24.7% | 43.0% | 50.5% | 5.00 | 43.0% | 9.1% | 15.0% | 968 | 0.0% |
| meow-memory | keyword | 29.3% | 42.0% | 45.2% | 2.00 | 37.8% | 19.2% | 25.5% | 302 | 0.0% |
| dsh-mnemon | keyword | 21.1% | 31.2% | 33.4% | 1.18 | 22.0% | 18.5% | 20.1% | 225 | 0.0% |
| dsh-prime-memory + embeddings | hybrid | 7.9% | 30.3% | 55.4% | 5.00 | 44.9% | 9.4% | 15.6% | 639 | 0.0% |
| SeekMemory | keyword | 15.1% | 26.1% | 38.6% | 5.49 | 26.8% | 6.0% | 9.8% | 524 | 0.0% |
| dsh-prime-memory | keyword | 3.8% | 21.4% | 48.1% | 5.00 | 39.5% | 8.2% | 13.5% | 658 | 0.0% |

## LoCoMo: configurations left out

On this dataset these configurations stored over 5% fewer memories than they were sent; they are in no table and no chart of this dataset:

- dsh-fish-memory: 2,176 of 5,882 memories stored (37.0%).
- dsh-fish-memory + claude-haiku-5-5 expansion: 152 of 419 memories stored (36.3%).
- dsh-fish-memory + deepseek-flash expansion: 152 of 419 memories stored (36.3%).

The project accepts at most 8 memories a day (the default of its dailyQuota setting). This benchmark writes each memory on its own date, and the writes of a day beyond the eighth were refused: a LoCoMo session has dozens of turns on one day, and the PerLTQA memories without a date were given the earliest day of their store. This comes from the way the benchmark fed the store, which does not fit that setting: a result on these stores says nothing about the project.

- lingshu: 1,888 of 5,882 memories stored (32.1%).

The project's write gate puts a new memory that resembles a stored one into a review queue, or asks for confirmation. Nobody reviews during a run, so these memories are not in the store.

## LoCoMo, Hit@5 by question type

| System | Retrieval | single-hop (n = 841) | multi-hop (n = 282) | temporal (n = 320) | open-domain (n = 92) |
|---|---|---:|---:|---:|---:|
| **feiyu-memgate sqlite** | keyword | 37.2% | 31.6% | 44.1% | 16.3% |
| **feiyu-memgate embedding** | vector | 54.6% | 40.4% | 59.4% | 31.5% |
| **feiyu-memgate hybrid** | hybrid | 60.4% | 52.1% | 65.0% | 29.3% |
| mem0 (LLM extraction, claude-haiku-5-5) | extraction | 77.3% | 72.3% | 73.8% | 45.7% |
| mem0 (LLM extraction, deepseek-flash) | extraction | 74.9% | 64.2% | 71.2% | 39.1% |
| mem0 (verbatim) | vector | 66.7% | 61.3% | 71.9% | 39.1% |
| memsearch | hybrid | 67.1% | 53.9% | 73.1% | 37.0% |
| dsh-mnemon + embeddings | hybrid | 58.3% | 44.7% | 67.5% | 32.6% |
| OpenViking | vector | 55.2% | 40.1% | 59.4% | 31.5% |
| dsh-soul-md | keyword | 45.7% | 30.9% | 53.1% | 20.7% |
| meow-memory | keyword | 48.0% | 25.2% | 46.6% | 21.7% |
| dsh-mnemon | keyword | 33.1% | 20.2% | 41.2% | 13.0% |
| dsh-prime-memory + embeddings | hybrid | 34.7% | 18.4% | 32.5% | 18.5% |
| SeekMemory | keyword | 31.2% | 16.0% | 28.1% | 4.3% |
| dsh-prime-memory | keyword | 23.3% | 13.8% | 26.2% | 9.8% |

## LoCoMo, the first 3 stores (conv-26 – conv-41), 383 answerable questions

| System | Retrieval | Hit@1 | Hit@5 | Hit@10 | Items delivered | Delivered hit | Precision | Delivered F1 | Characters | Quiet rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Graphiti (deepseek-flash) | graph | 28.5% | 55.1% | 62.9% | 8.81 | 62.9% | 8.4% | 14.7% | 1,548 | 0.0% |
| Graphiti (claude-haiku-5-5) | graph | 24.5% | 53.8% | 64.0% | 9.32 | 64.0% | 7.9% | 14.1% | 1,498 | 0.0% |
| **feiyu-memgate sqlite** | keyword | 20.6% | 36.6% | 47.3% | 4.70 | 34.7% | 9.3% | 14.7% | 1,285 | 0.0% |
| **feiyu-memgate hybrid** | hybrid | 37.9% | 62.4% | 69.2% | 4.84 | 55.6% | 12.4% | 20.3% | 1,013 | 0.0% |

## LoCoMo, the first store (conv-26), 150 answerable questions

| System | Retrieval | Hit@1 | Hit@5 | Hit@10 | Items delivered | Delivered hit | Precision | Delivered F1 | Characters | Quiet rate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Hindsight (no model) | hybrid | 48.0% | 76.0% | 83.3% | 59.48 | 94.7% | 2.0% | 3.9% | 8,989 | 0.0% |
| Hindsight (deepseek-flash) | extraction | 44.7% | 73.3% | 83.3% | 88.86 | 91.3% | 1.3% | 2.6% | 15,298 | 0.0% |
| Hindsight (claude-haiku-5-5) | extraction | 43.3% | 73.3% | 84.7% | 86.82 | 92.7% | 1.4% | 2.8% | 14,995 | 0.0% |
| **feiyu-memgate sqlite** | keyword | 23.3% | 40.7% | 52.7% | 4.51 | 35.3% | 10.4% | 16.1% | 1,298 | 0.0% |
| **feiyu-memgate hybrid** | hybrid | 37.3% | 64.0% | 72.7% | 4.87 | 56.0% | 11.8% | 19.5% | 1,022 | 0.0% |

## LLM usage

| System | Dataset | Stores | Memories | Calls | Input tokens (of which cache hits) | Output tokens | Cost (USD) | Per 1,000 memories (USD) | Write time (s per memory) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Graphiti (claude-haiku-5-5) | PerLTQA | 10 | 664 | 3,347 | 11,144,644 (0) | 564,289 | 1.397 | 2.10 | 5.4 |
| Graphiti (deepseek-flash) | PerLTQA | 24 | 1,589 | 10,553 | 17,996,457 (1,879,415) | 1,035,447 | 6.089 | 3.83 | 6.0 |
| Hindsight (claude-haiku-5-5) | PerLTQA | 5 | 361 | 716 | 7,047,823 (3,646,443) | 506,158 | 0.630 | 1.75 | 3.5 |
| Hindsight (deepseek-flash) | PerLTQA | 5 | 361 | 747 | 5,175,865 (2,749,056) | 429,449 | 0.630 | 1.75 | 3.4 |
| mem0 (LLM extraction, claude-haiku-5-5) | PerLTQA | 32 | 2,155 | 2,155 | 32,527,632 (0) | 489,304 | 3.497 | 1.62 | 1.9 |
| mem0 (LLM extraction, deepseek-flash) | PerLTQA | 32 | 2,155 | 2,155 | 20,000,381 (16,344,832) | 328,494 | 1.589 | 0.74 | 1.8 |
| Graphiti (claude-haiku-5-5) | LoCoMo | 3 | 1,451 | 4,955 | 14,865,175 (0) | 619,415 | 1.796 | 1.24 | 4.2 |
| Graphiti (deepseek-flash) | LoCoMo | 3 | 1,451 | 5,001 | 8,463,882 (839,296) | 326,362 | 1.645 | 1.13 | 3.8 |
| Hindsight (claude-haiku-5-5) | LoCoMo | 1 | 419 | 697 | 6,566,072 (3,558,585) | 214,131 | 0.443 | 1.06 | 1.6 |
| Hindsight (deepseek-flash) | LoCoMo | 1 | 419 | 696 | 4,312,975 (2,438,400) | 138,612 | 0.372 | 0.89 | 1.4 |
| mem0 (LLM extraction, claude-haiku-5-5) | LoCoMo | 10 | 5,882 | 5,882 | 79,031,305 (0) | 404,502 | 8.105 | 1.38 | 1.2 |
| mem0 (LLM extraction, deepseek-flash) | LoCoMo | 10 | 5,882 | 5,882 | 50,523,528 (44,444,800) | 355,384 | 2.517 | 0.43 | 1.3 |

## Latency

| System | PerLTQA p50 | PerLTQA p95 | LoCoMo p50 | LoCoMo p95 | How measured |
|---|---:|---:|---:|---:|---|
| feiyu-memgate sqlite | 5.4 ms | 7.5 ms | 17.6 ms | 20.2 ms | separate sequential pass on an idle machine |
| feiyu-memgate sqlite (precise profile) | 5.4 ms | 7.4 ms | 17.1 ms | 20.4 ms | separate sequential pass on an idle machine |
| feiyu-memgate embedding (bge-m3, precise profile) | 12.7 ms | 33.0 ms | 22.2 ms | 50.5 ms | separate sequential pass on an idle machine |
| feiyu-memgate embedding | 13.9 ms | 30.6 ms | 23.4 ms | 50.5 ms | separate sequential pass on an idle machine |
| feiyu-memgate hybrid (precise profile) | 18.5 ms | 41.4 ms | 35.4 ms | 50.0 ms | separate sequential pass on an idle machine |
| feiyu-memgate hybrid | 19.7 ms | 49.0 ms | 40.6 ms | 68.7 ms | separate sequential pass on an idle machine |
| dsh-prime-memory | 0.9 ms | 1.2 ms | 0.8 ms | 1.2 ms | separate sequential pass on an idle machine |
| dsh-soul-md | 1.3 ms | 1.7 ms | 12.6 ms | 14.3 ms | separate sequential pass on an idle machine |
| SeekMemory | 1.9 ms | 5.2 ms | 10.6 ms | 12.8 ms | separate sequential pass on an idle machine |
| lingshu | 2.1 ms | 2.4 ms | - | - | separate sequential pass on an idle machine |
| meow-memory | 2.7 ms | 3.9 ms | 7.3 ms | 8.8 ms | separate sequential pass on an idle machine |
| memsearch | 12.2 ms | 14.5 ms | 88.1 ms | 174.1 ms | separate sequential pass on an idle machine |
| OpenViking | 14.6 ms | 29.1 ms | 16.3 ms | 30.1 ms | separate sequential pass on an idle machine |
| mem0 (verbatim) | 14.7 ms | 28.5 ms | 98.7 ms | 122.6 ms | separate sequential pass on an idle machine |
| dsh-mnemon | 18.4 ms | 27.2 ms | 38.9 ms | 50.9 ms | separate sequential pass on an idle machine |
| dsh-prime-memory + embeddings | 21.9 ms | 23.6 ms | 22.0 ms | 23.2 ms | separate sequential pass on an idle machine |
| dsh-mnemon + embeddings | 30.8 ms | 40.2 ms | 69.6 ms | 88.8 ms | separate sequential pass on an idle machine |
| Graphiti (deepseek-flash) | 51.2 ms | 173.5 ms | 78.1 ms | 110.7 ms | measured during the parallel runs (under load) |
| Graphiti (claude-haiku-5-5) | 76.3 ms | 221.2 ms | 72.6 ms | 104.9 ms | measured during the parallel runs (under load) |
| mem0 (LLM extraction, deepseek-flash) | 86.8 ms | 261.8 ms | 178.7 ms | 342.8 ms | measured during the parallel runs (under load) |
| mem0 (LLM extraction, claude-haiku-5-5) | 96.6 ms | 272.6 ms | 70.3 ms | 202.3 ms | measured during the parallel runs (under load) |
| Hindsight (claude-haiku-5-5) | 2152.2 ms | 2798.4 ms | 2558.5 ms | 2807.4 ms | measured during the parallel runs (under load) |
| Hindsight (deepseek-flash) | 2174.2 ms | 2657.5 ms | 2451.6 ms | 2859.2 ms | measured during the parallel runs (under load) |
| Hindsight (no model) | 2935.4 ms | 3071.4 ms | 1800.2 ms | 2233.7 ms | separate sequential pass on an idle machine |

## Write audit: memories sent and memories stored

| System | PerLTQA stores | PerLTQA stored / sent | LoCoMo stores | LoCoMo stored / sent | How established | Note |
|---|---:|---:|---:|---:|---|---|
| **feiyu-memgate sqlite** | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | rows counted after the ingest was run again |  |
| **feiyu-memgate embedding** | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | rows counted after the ingest was run again |  |
| **feiyu-memgate hybrid** | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | rows counted after the ingest was run again |  |
| dsh-fish-memory | 32 | 1,361 / 2,155 | 10 | 2,176 / 5,882 | record of the run | the project's daily limit on new memories; left out of the tables and charts of PerLTQA, LoCoMo |
| dsh-fish-memory + claude-haiku-5-5 expansion | 3 | 155 / 225 | 1 | 152 / 419 | record of the run | the project's daily limit on new memories; left out of the tables and charts of PerLTQA, LoCoMo |
| dsh-fish-memory + deepseek-flash expansion | 3 | 155 / 225 | 1 | 152 / 419 | record of the run | the project's daily limit on new memories; left out of the tables and charts of PerLTQA, LoCoMo |
| dsh-mnemon | 32 | 2,155 / 2,155 | 10 | 5,880 / 5,882 | record of the run | a memory identical to another one of the store is skipped |
| dsh-mnemon + embeddings | 32 | 2,155 / 2,155 | 10 | 5,880 / 5,882 | record of the run | a memory identical to another one of the store is skipped |
| dsh-prime-memory | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | rows counted after the ingest was run again |  |
| dsh-prime-memory + embeddings | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | rows counted after the ingest was run again |  |
| dsh-soul-md | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | rows counted after the ingest was run again |  |
| Graphiti (claude-haiku-5-5) | 10 | 664 / 664 | 3 | 1,451 / 1,451 | record of the run | calls a model: the count is of memories processed without a failure |
| Graphiti (deepseek-flash) | 24 | 1,566 / 1,589 | 3 | 1,448 / 1,451 | record of the run | the model's output did not parse, so these were not written; calls a model: the count is of memories processed without a failure |
| Hindsight (claude-haiku-5-5) | 5 | – / 361 (0 failed) | 1 | – / 419 (0 failed) | record of the run | the record of the run has failures only, no count of what was stored |
| Hindsight (deepseek-flash) | 5 | – / 361 (0 failed) | 1 | – / 419 (0 failed) | record of the run | the record of the run has failures only, no count of what was stored |
| Hindsight (no model) | 5 | 361 / 361 | 8 | 4,850 / 4,850 | every memory asked back after the ingest was run again |  |
| lingshu | 32 | 2,117 / 2,155 | 10 | 1,888 / 5,882 | record of the run | the project's write gate puts a memory that resembles a stored one into a review queue; left out of the tables and charts of LoCoMo |
| mem0 (LLM extraction, claude-haiku-5-5) | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | record of the run | calls a model: the count is of memories processed without a failure |
| mem0 (LLM extraction, deepseek-flash) | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | record of the run | calls a model: the count is of memories processed without a failure |
| mem0 (verbatim) | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | record of the run |  |
| memsearch | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | every memory asked back after the ingest was run again |  |
| meow-memory | 32 | 2,152 / 2,155 | 10 | 5,874 / 5,882 | record of the run | merged into a similar memory by the project |
| OpenViking | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | every memory asked back after the ingest was run again |  |
| SeekMemory | 32 | 2,155 / 2,155 | 10 | 5,882 / 5,882 | every memory asked back after the ingest was run again |  |
