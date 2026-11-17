# longmemeval

## feiyu-memgate-sqlite-precise | question set: all questions of the dataset (500 worlds, 500 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| ALL answerable | 470 | 3.2 | 9.1 | 12.8 | 6.0 (296) | 10.2 | 18.3 | 0.54 | 529 | 64.0 |
| knowledge-update | 72 | 4.2 | 18.1 | 23.6 | 17.9 (70) | 25.0 | 28.1 | 0.99 | 669 | 63.6 |
| multi-session | 121 | 0.0 | 1.7 | 5.0 | 2.3 (121) | 4.1 | 6.8 | 0.52 | 591 | 63.4 |
| single-session-assistant | 56 | 14.3 | 26.8 | 32.1 | - | 10.7 | 42.9 | 0.25 | 316 | 66.9 |
| single-session-preference | 30 | 0.0 | 0.0 | 0.0 | 0.0 (11) | 0.0 | 0.0 | 0.33 | 425 | 64.1 |
| single-session-user | 64 | 4.7 | 14.1 | 20.3 | 25.0 (2) | 20.3 | 30.5 | 0.67 | 605 | 63.6 |
| temporal-reasoning | 127 | 0.8 | 3.1 | 4.7 | 2.2 (92) | 4.7 | 10.0 | 0.41 | 470 | 64.1 |

No-answer questions: n 30, quiet rate 46.7 %, items 0.53, chars 586, p50 62.3 ms

Session-level (answerable, n 470): Hit@1 19.1, Hit@5 48.9, Hit@10 65.1, delivered 16.8

## feiyu-memgate-sqlite | question set: all questions of the dataset (500 worlds, 500 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| ALL answerable | 470 | 3.2 | 9.1 | 12.8 | 6.0 (296) | 10.2 | 17.8 | 0.55 | 534 | 70.8 |
| knowledge-update | 72 | 4.2 | 18.1 | 23.6 | 17.9 (70) | 25.0 | 27.4 | 1.00 | 680 | 72.2 |
| multi-session | 121 | 0.0 | 1.7 | 5.0 | 2.3 (121) | 4.1 | 6.8 | 0.52 | 591 | 70.6 |
| single-session-assistant | 56 | 14.3 | 26.8 | 32.1 | - | 10.7 | 40.0 | 0.27 | 324 | 74.3 |
| single-session-preference | 30 | 0.0 | 0.0 | 0.0 | 0.0 (11) | 0.0 | 0.0 | 0.33 | 425 | 73.3 |
| single-session-user | 64 | 4.7 | 14.1 | 20.3 | 25.0 (2) | 20.3 | 29.3 | 0.69 | 610 | 70.8 |
| temporal-reasoning | 127 | 0.8 | 3.1 | 4.7 | 2.2 (92) | 4.7 | 9.8 | 0.42 | 476 | 66.4 |

No-answer questions: n 30, quiet rate 46.7 %, items 0.57, chars 596, p50 69.8 ms

Session-level (answerable, n 470): Hit@1 19.1, Hit@5 48.9, Hit@10 65.1, delivered 17.0
