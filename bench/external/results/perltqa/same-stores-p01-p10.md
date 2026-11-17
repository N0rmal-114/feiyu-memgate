# perltqa: p01-p10 (p01,p02,p03,p04,p05,p06,p07,p08,p09,p10)

| system | stores | questions | Hit@1 | Hit@5 | Hit@10 | deliv. hit | precision | items | chars | quiet | LLM | calls | input tok | output tok | USD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dsh-fish-memory | 10 | 2343 | 35.0 | 57.2 | 59.5 | 44.4 | 31.4 | 1.48 | 516 | - | - | - | - | - | - |
| dsh-mnemon-embedding | 10 | 2343 | 69.9 | 89.9 | 91.2 | 84.9 | 32.0 | 3.12 | 691 | - | - | - | - | - | - |
| dsh-mnemon | 10 | 2343 | 60.6 | 72.2 | 73.4 | 59.7 | 51.1 | 1.22 | 431 | - | - | - | - | - | - |
| dsh-prime-memory-embedding | 10 | 2343 | 19.3 | 86.2 | 96.2 | 92.6 | 18.5 | 5.00 | 1421 | - | - | - | - | - | - |
| dsh-prime-memory | 10 | 2343 | 31.2 | 86.0 | 93.0 | 88.3 | 18.3 | 4.92 | 1547 | - | - | - | - | - | - |
| dsh-soul-md | 10 | 2343 | 56.3 | 85.2 | 91.2 | 85.2 | 17.3 | 4.98 | 1491 | - | - | - | - | - | - |
| feiyu-memgate-embedding-precise | 10 | 2343 | 47.0 | 84.2 | 90.3 | 60.3 | 43.6 | 1.82 | 343 | - | - | - | - | - | - |
| feiyu-memgate-embedding | 10 | 2343 | 47.0 | 84.2 | 90.3 | 80.4 | 30.5 | 3.55 | 633 | - | - | - | - | - | - |
| feiyu-memgate-hybrid-precise | 10 | 2343 | 69.0 | 95.6 | 97.8 | 83.5 | 58.3 | 1.78 | 489 | - | - | - | - | - | - |
| feiyu-memgate-hybrid | 10 | 2343 | 69.0 | 95.6 | 97.8 | 94.4 | 36.2 | 3.37 | 832 | - | - | - | - | - | - |
| feiyu-memgate-sqlite-precise | 10 | 2343 | 67.3 | 87.7 | 91.3 | 81.7 | 59.8 | 1.72 | 634 | - | - | - | - | - | - |
| feiyu-memgate-sqlite | 10 | 2343 | 67.3 | 87.7 | 91.3 | 83.9 | 55.9 | 1.95 | 711 | - | - | - | - | - | - |
| graphiti-claude-haiku-5-5 | 10 | 2343 | 42.9 | 72.7 | 76.5 | 76.5 | 22.8 | 5.39 | 1023 | - | claude-haiku-5-5 | 3347 | 11144644 | 564289 | 1.397 |
| graphiti-deepseek-flash | 10 | 2343 | 47.3 | 78.9 | 82.4 | 82.4 | 25.1 | 5.05 | 1101 | - | deepseek-flash | 4360 | 7460244 | 430147 | 2.488 |
| lingshu-dsh-memory | 10 | 2343 | 70.8 | 89.5 | 92.3 | 8.2 | 2.1 | 4.00 | 1472 | - | - | - | - | - | - |
| mem0-llm-claude-haiku-5-5 | 10 | 2343 | 45.6 | 81.0 | 85.8 | 90.5 | 6.7 | 14.17 | 3761 | - | claude-haiku-5-5 | 664 | 10026041 | 152702 | 1.079 |
| mem0-llm-deepseek-flash | 10 | 2343 | 47.9 | 77.4 | 83.0 | 87.7 | 7.2 | 13.01 | 3436 | - | deepseek-flash | 664 | 6162357 | 100290 | 0.488 |
| mem0-verbatim | 10 | 2343 | 46.9 | 84.3 | 90.5 | 95.3 | 4.8 | 20.00 | 3692 | - | - | - | - | - | - |
| memsearch | 10 | 2343 | 52.4 | 88.6 | 93.0 | 88.6 | 17.7 | 5.00 | 1138 | - | - | - | - | - | - |
| meow-memory | 10 | 2343 | 69.8 | 88.4 | 92.0 | 81.4 | 40.8 | 2.00 | 621 | - | - | - | - | - | - |
| openviking | 10 | 2343 | 47.0 | 84.1 | 90.5 | 90.5 | 9.2 | 9.94 | 1814 | - | - | - | - | - | - |
| seekmemory | 10 | 2343 | 38.5 | 51.9 | 85.1 | 77.3 | 15.4 | 6.07 | 623 | - | - | - | - | - | - |

Not covering all of these stores (not scored): dsh-fish-memory-expand-claude-haiku-5-5 (missing 7 stores); dsh-fish-memory-expand-deepseek-flash (missing 7 stores); hindsight-chunks (missing 5 stores); hindsight-llm-claude-haiku-5-5 (missing 5 stores); hindsight-llm-deepseek-flash (missing 5 stores)
