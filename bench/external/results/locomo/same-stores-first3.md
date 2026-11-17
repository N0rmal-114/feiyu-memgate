# locomo: first3 (conv-26,conv-30,conv-41)

| system | stores | questions | Hit@1 | Hit@5 | Hit@10 | deliv. hit | precision | items | chars | quiet | LLM | calls | input tok | output tok | USD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dsh-fish-memory | 3 | 495 | 20.6 | 38.1 | 40.5 | 35.2 | 13.5 | 3.20 | 609 | 1.8 | - | - | - | - | - |
| dsh-mnemon-embedding | 3 | 495 | 31.9 | 55.9 | 64.0 | 48.3 | 16.3 | 3.39 | 438 | 0.0 | - | - | - | - | - |
| dsh-mnemon | 3 | 495 | 21.1 | 30.3 | 32.4 | 21.9 | 18.6 | 1.21 | 232 | 0.0 | - | - | - | - | - |
| dsh-prime-memory-embedding | 3 | 495 | 8.1 | 30.0 | 57.4 | 45.7 | 9.3 | 5.00 | 678 | 0.0 | - | - | - | - | - |
| dsh-prime-memory | 3 | 495 | 6.0 | 31.9 | 53.5 | 45.2 | 9.2 | 5.00 | 710 | 0.0 | - | - | - | - | - |
| dsh-soul-md | 3 | 495 | 26.1 | 44.6 | 50.9 | 44.6 | 9.0 | 5.00 | 954 | 0.0 | - | - | - | - | - |
| feiyu-memgate-embedding-precise | 3 | 495 | 17.0 | 49.6 | 63.7 | 31.1 | 16.3 | 2.54 | 301 | 10.7 | - | - | - | - | - |
| feiyu-memgate-embedding | 3 | 495 | 17.0 | 49.6 | 63.7 | 47.5 | 11.8 | 4.75 | 556 | 0.0 | - | - | - | - | - |
| feiyu-memgate-hybrid-precise | 3 | 495 | 37.9 | 62.4 | 69.2 | 46.5 | 27.0 | 2.72 | 556 | 17.0 | - | - | - | - | - |
| feiyu-memgate-hybrid | 3 | 495 | 37.9 | 62.4 | 69.2 | 55.6 | 12.4 | 4.84 | 1013 | 0.0 | - | - | - | - | - |
| feiyu-memgate-sqlite-precise | 3 | 495 | 20.6 | 36.6 | 47.3 | 34.2 | 10.7 | 4.45 | 1215 | 0.0 | - | - | - | - | - |
| feiyu-memgate-sqlite | 3 | 495 | 20.6 | 36.6 | 47.3 | 34.7 | 9.3 | 4.70 | 1285 | 0.0 | - | - | - | - | - |
| graphiti-claude-haiku-5-5 | 3 | 495 | 24.5 | 53.8 | 64.0 | 64.0 | 7.9 | 9.32 | 1498 | 0.0 | claude-haiku-5-5 | 4955 | 14865175 | 619415 | 1.796 |
| graphiti-deepseek-flash | 3 | 495 | 28.5 | 55.1 | 62.9 | 62.9 | 8.4 | 8.81 | 1548 | 0.0 | deepseek-flash | 5001 | 8463882 | 326362 | 1.645 |
| lingshu-dsh-memory | 3 | 495 | 16.4 | 27.9 | 32.9 | 1.8 | 0.5 | 4.00 | 745 | 0.0 | - | - | - | - | - |
| mem0-llm-claude-haiku-5-5 | 3 | 495 | 41.5 | 74.2 | 80.4 | 84.9 | 5.3 | 19.04 | 3256 | 0.0 | claude-haiku-5-5 | 1451 | 19537575 | 95848 | 2.002 |
| mem0-llm-deepseek-flash | 3 | 495 | 34.5 | 71.0 | 82.2 | 86.7 | 5.5 | 18.50 | 3053 | 0.0 | deepseek-flash | 1451 | 12471639 | 89762 | 0.626 |
| mem0-verbatim | 3 | 495 | 37.6 | 65.0 | 71.5 | 76.8 | 4.4 | 20.00 | 2649 | 0.0 | - | - | - | - | - |
| memsearch | 3 | 495 | 39.2 | 65.3 | 75.2 | 65.0 | 13.5 | 5.00 | 710 | 0.0 | - | - | - | - | - |
| meow-memory | 3 | 495 | 27.9 | 41.0 | 45.2 | 37.3 | 18.7 | 2.00 | 319 | 0.0 | - | - | - | - | - |
| openviking | 3 | 495 | 16.4 | 49.1 | 63.7 | 63.7 | 6.7 | 9.99 | 1173 | 0.0 | - | - | - | - | - |
| seekmemory | 3 | 495 | 14.9 | 23.5 | 35.2 | 23.8 | 5.3 | 5.44 | 522 | 0.0 | - | - | - | - | - |

Not covering all of these stores (not scored): dsh-fish-memory-expand-claude-haiku-5-5 (missing conv-30,conv-41); dsh-fish-memory-expand-deepseek-flash (missing conv-30,conv-41); hindsight-chunks (missing conv-30,conv-41); hindsight-llm-claude-haiku-5-5 (missing conv-30,conv-41); hindsight-llm-deepseek-flash (missing conv-30,conv-41)
