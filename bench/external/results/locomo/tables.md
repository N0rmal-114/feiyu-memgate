# locomo

## dsh-fish-memory-expand-claude-haiku-5-5 | question set: all questions of the dataset (1 worlds, 197 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 32 | 9.4 | 18.8 | 28.1 | 13.5 (29) | 12.5 | 8.0 | 2.12 | 481 | 2.8 |
| 2-temporal | 37 | 29.7 | 62.2 | 64.9 | - | 59.5 | 23.9 | 2.92 | 627 | 2.9 |
| 3-open-domain | 11 | 18.2 | 27.3 | 27.3 | 18.8 (8) | 27.3 | 9.8 | 2.64 | 479 | 3.0 |
| 4-single-hop | 70 | 18.6 | 28.6 | 28.6 | 50.0 (1) | 27.1 | 12.5 | 2.76 | 539 | 2.7 |
| ALL answerable | 150 | 19.3 | 34.7 | 37.3 | 15.6 (38) | 32.0 | 14.3 | 2.65 | 544 | 2.7 |

No-answer questions: n 47, quiet rate 2.1 %, items 2.72, chars 537, p50 3.0 ms

## dsh-fish-memory-expand-deepseek-flash | question set: all questions of the dataset (1 worlds, 197 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 32 | 9.4 | 18.8 | 21.9 | 12.1 (29) | 12.5 | 7.5 | 2.16 | 476 | 2.1 |
| 2-temporal | 37 | 35.1 | 62.2 | 64.9 | - | 59.5 | 23.2 | 2.89 | 633 | 2.1 |
| 3-open-domain | 11 | 18.2 | 27.3 | 27.3 | 18.8 (8) | 27.3 | 9.8 | 2.64 | 479 | 2.0 |
| 4-single-hop | 70 | 20.0 | 28.6 | 30.0 | 50.0 (1) | 27.1 | 11.7 | 2.90 | 564 | 1.8 |
| ALL answerable | 150 | 21.3 | 34.7 | 36.7 | 14.5 (38) | 32.0 | 13.6 | 2.72 | 556 | 2.0 |

No-answer questions: n 47, quiet rate 2.1 %, items 2.79, chars 543, p50 2.5 ms

## dsh-fish-memory | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 13.1 | 26.6 | 31.2 | 15.0 (277) | 22.0 | 8.4 | 3.26 | 637 | 2.6 |
| 2-temporal | 320 | 23.1 | 42.8 | 48.4 | 28.5 (40) | 39.7 | 15.5 | 3.22 | 631 | 2.9 |
| 3-open-domain | 92 | 5.4 | 15.2 | 18.5 | 9.6 (50) | 13.0 | 4.1 | 3.24 | 606 | 2.7 |
| 4-single-hop | 841 | 17.6 | 30.4 | 32.5 | 17.4 (46) | 27.9 | 10.0 | 3.39 | 635 | 3.2 |
| ALL answerable | 1535 | 17.2 | 31.4 | 34.7 | 15.9 (413) | 28.4 | 10.5 | 3.32 | 633 | 3.0 |

No-answer questions: n 446, quiet rate 0.4 %, items 3.28, chars 624, p50 3.6 ms

## dsh-mnemon-embedding | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 22.0 | 44.7 | 53.2 | 27.8 (277) | 37.9 | 14.5 | 3.62 | 434 | 143.9 |
| 2-temporal | 320 | 45.3 | 67.5 | 72.8 | 49.6 (40) | 58.4 | 22.1 | 2.98 | 406 | 161.2 |
| 3-open-domain | 92 | 10.9 | 32.6 | 39.1 | 19.1 (50) | 25.0 | 9.0 | 3.09 | 364 | 155.1 |
| 4-single-hop | 841 | 36.0 | 58.3 | 65.9 | 53.3 (46) | 51.8 | 15.9 | 3.56 | 448 | 142.8 |
| ALL answerable | 1535 | 33.9 | 56.2 | 63.4 | 31.7 (413) | 49.1 | 16.5 | 3.42 | 432 | 148.4 |

No-answer questions: n 446, quiet rate 0.0 %, items 3.25, chars 392, p50 140.0 ms

## dsh-mnemon | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 11.3 | 20.2 | 24.5 | 10.7 (277) | 14.9 | 11.8 | 1.29 | 242 | 75.3 |
| 2-temporal | 320 | 27.5 | 41.2 | 43.1 | 26.2 (40) | 28.1 | 23.4 | 1.18 | 232 | 77.0 |
| 3-open-domain | 92 | 5.4 | 13.0 | 15.2 | 5.8 (50) | 8.7 | 8.2 | 1.11 | 206 | 74.6 |
| 4-single-hop | 841 | 23.7 | 33.1 | 34.7 | 18.1 (46) | 23.4 | 20.1 | 1.16 | 219 | 83.3 |
| ALL answerable | 1535 | 21.1 | 31.2 | 33.4 | 12.4 (413) | 22.0 | 18.5 | 1.18 | 225 | 80.1 |

No-answer questions: n 446, quiet rate 0.0 %, items 1.08, chars 199, p50 77.0 ms

## dsh-prime-memory-embedding | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 5.7 | 18.4 | 44.3 | 21.2 (277) | 31.2 | 7.6 | 5.00 | 649 | 90.9 |
| 2-temporal | 320 | 9.4 | 32.5 | 63.4 | 39.0 (40) | 54.1 | 11.1 | 5.00 | 646 | 90.5 |
| 3-open-domain | 92 | 4.3 | 18.5 | 26.1 | 13.5 (50) | 20.7 | 5.0 | 5.00 | 588 | 97.4 |
| 4-single-hop | 841 | 8.4 | 34.7 | 59.2 | 44.2 (46) | 48.6 | 9.9 | 5.00 | 638 | 92.1 |
| ALL answerable | 1535 | 7.9 | 30.3 | 55.4 | 24.6 (413) | 44.9 | 9.4 | 5.00 | 639 | 91.9 |

No-answer questions: n 446, quiet rate 0.0 %, items 5.00, chars 623, p50 93.5 ms

## dsh-prime-memory | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 4.6 | 13.8 | 31.2 | 14.1 (277) | 23.8 | 5.5 | 5.00 | 678 | 0.8 |
| 2-temporal | 320 | 4.4 | 26.2 | 54.1 | 30.2 (40) | 42.8 | 8.8 | 5.00 | 651 | 0.8 |
| 3-open-domain | 92 | 3.3 | 9.8 | 26.1 | 16.2 (50) | 18.5 | 4.3 | 5.00 | 632 | 0.9 |
| 4-single-hop | 841 | 3.4 | 23.3 | 54.0 | 27.2 (46) | 45.9 | 9.3 | 5.00 | 657 | 0.9 |
| ALL answerable | 1535 | 3.8 | 21.4 | 48.1 | 17.4 (413) | 39.5 | 8.2 | 5.00 | 658 | 0.9 |

No-answer questions: n 446, quiet rate 0.0 %, items 5.00, chars 651, p50 0.9 ms

## dsh-soul-md | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 13.8 | 30.9 | 40.1 | 18.7 (277) | 30.9 | 7.9 | 5.00 | 945 | 13.6 |
| 2-temporal | 320 | 31.9 | 53.1 | 61.6 | 34.8 (40) | 53.1 | 10.8 | 5.00 | 983 | 14.2 |
| 3-open-domain | 92 | 10.9 | 20.7 | 27.2 | 11.5 (50) | 20.7 | 4.6 | 5.00 | 973 | 14.3 |
| 4-single-hop | 841 | 27.1 | 45.7 | 52.3 | 33.3 (46) | 45.7 | 9.3 | 5.00 | 969 | 13.5 |
| ALL answerable | 1535 | 24.7 | 43.0 | 50.5 | 21.0 (413) | 43.0 | 9.1 | 5.00 | 968 | 13.6 |

No-answer questions: n 446, quiet rate 0.0 %, items 5.00, chars 960, p50 13.3 ms

## feiyu-memgate-embedding-precise | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 15.2 | 40.4 | 53.5 | 28.1 (277) | 28.4 | 14.0 | 3.00 | 326 | 10.5 |
| 2-temporal | 320 | 32.2 | 59.4 | 72.5 | 48.8 (40) | 43.8 | 31.1 | 1.99 | 242 | 10.4 |
| 3-open-domain | 92 | 10.9 | 31.5 | 40.2 | 21.2 (50) | 18.5 | 10.4 | 2.30 | 241 | 10.6 |
| 4-single-hop | 841 | 24.1 | 54.6 | 66.7 | 54.3 (46) | 38.8 | 21.6 | 2.66 | 307 | 10.6 |
| ALL answerable | 1535 | 23.4 | 51.6 | 63.9 | 32.2 (413) | 36.7 | 21.4 | 2.56 | 293 | 10.5 |

No-answer questions: n 446, quiet rate 10.8 %, items 2.29, chars 248, p50 10.5 ms

## feiyu-memgate-embedding | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 15.2 | 40.4 | 53.5 | 28.1 (277) | 37.2 | 10.7 | 4.87 | 535 | 48.1 |
| 2-temporal | 320 | 32.2 | 59.4 | 72.5 | 48.8 (40) | 59.1 | 16.1 | 4.51 | 524 | 48.6 |
| 3-open-domain | 92 | 10.9 | 31.5 | 40.2 | 21.2 (50) | 30.4 | 8.0 | 4.77 | 489 | 49.4 |
| 4-single-hop | 841 | 24.1 | 54.6 | 66.7 | 54.3 (46) | 53.4 | 12.4 | 4.79 | 545 | 48.8 |
| ALL answerable | 1535 | 23.4 | 51.6 | 63.9 | 32.2 (413) | 50.2 | 12.6 | 4.75 | 535 | 48.8 |

No-answer questions: n 446, quiet rate 0.2 %, items 4.74, chars 510, p50 48.6 ms

## feiyu-memgate-hybrid-precise | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 22.7 | 52.1 | 62.1 | 32.0 (277) | 41.8 | 20.6 | 3.29 | 656 | 22.4 |
| 2-temporal | 320 | 50.0 | 65.0 | 71.6 | 42.1 (40) | 56.6 | 39.9 | 2.23 | 491 | 22.5 |
| 3-open-domain | 92 | 14.1 | 29.3 | 35.9 | 21.1 (50) | 16.3 | 14.2 | 1.75 | 357 | 22.7 |
| 4-single-hop | 841 | 38.2 | 60.4 | 68.6 | 50.4 (46) | 46.0 | 29.3 | 2.47 | 468 | 23.1 |
| ALL answerable | 1535 | 36.4 | 58.0 | 66.1 | 33.7 (413) | 45.7 | 29.0 | 2.53 | 501 | 22.8 |

No-answer questions: n 446, quiet rate 18.4 %, items 2.06, chars 405, p50 23.0 ms

## feiyu-memgate-hybrid | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 22.7 | 52.1 | 62.1 | 32.0 (277) | 47.2 | 13.2 | 4.93 | 972 | 23.4 |
| 2-temporal | 320 | 50.0 | 65.0 | 71.6 | 42.1 (40) | 63.4 | 14.9 | 4.71 | 1012 | 23.4 |
| 3-open-domain | 92 | 14.1 | 29.3 | 35.9 | 21.1 (50) | 28.3 | 6.2 | 4.88 | 1010 | 24.0 |
| 4-single-hop | 841 | 38.2 | 60.4 | 68.6 | 50.4 (46) | 58.9 | 13.5 | 4.80 | 917 | 23.9 |
| ALL answerable | 1535 | 36.4 | 58.0 | 66.1 | 33.7 (413) | 55.8 | 13.3 | 4.81 | 953 | 23.7 |

No-answer questions: n 446, quiet rate 0.0 %, items 4.82, chars 958, p50 24.0 ms

## feiyu-memgate-sqlite-precise | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 13.5 | 31.6 | 43.6 | 20.6 (277) | 31.2 | 8.3 | 4.71 | 1248 | 15.1 |
| 2-temporal | 320 | 29.4 | 44.1 | 53.1 | 25.4 (40) | 42.8 | 15.4 | 4.31 | 1169 | 14.1 |
| 3-open-domain | 92 | 5.4 | 16.3 | 27.2 | 15.6 (50) | 14.1 | 3.7 | 4.76 | 1331 | 16.1 |
| 4-single-hop | 841 | 20.9 | 37.2 | 46.5 | 25.7 (46) | 34.6 | 10.8 | 4.35 | 1184 | 15.8 |
| ALL answerable | 1535 | 20.4 | 36.4 | 46.2 | 21.0 (413) | 34.5 | 10.9 | 4.43 | 1201 | 15.4 |

No-answer questions: n 446, quiet rate 0.4 %, items 4.36, chars 1198, p50 15.7 ms

## feiyu-memgate-sqlite | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 13.5 | 31.6 | 43.6 | 20.6 (277) | 31.2 | 7.7 | 4.85 | 1283 | 15.1 |
| 2-temporal | 320 | 29.4 | 44.1 | 53.1 | 25.4 (40) | 42.8 | 12.2 | 4.62 | 1250 | 14.6 |
| 3-open-domain | 92 | 5.4 | 16.3 | 27.2 | 15.6 (50) | 15.2 | 4.1 | 4.86 | 1354 | 16.0 |
| 4-single-hop | 841 | 20.9 | 37.2 | 46.5 | 25.7 (46) | 34.8 | 8.9 | 4.67 | 1270 | 15.8 |
| ALL answerable | 1535 | 20.4 | 36.4 | 46.2 | 21.0 (413) | 34.7 | 9.1 | 4.70 | 1273 | 15.4 |

No-answer questions: n 446, quiet rate 0.0 %, items 4.68, chars 1289, p50 15.7 ms

## graphiti-claude-haiku-5-5 | question set: all questions of the dataset (3 worlds, 495 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 74 | 27.0 | 54.1 | 68.9 | 44.7 (71) | 68.9 | 10.8 | 9.35 | 1476 | 68.5 |
| 2-temporal | 90 | 31.1 | 62.2 | 68.9 | 50.0 (3) | 68.9 | 7.7 | 9.28 | 1523 | 73.8 |
| 3-open-domain | 19 | 10.5 | 31.6 | 52.6 | 35.4 (12) | 52.6 | 7.0 | 9.53 | 1550 | 76.9 |
| 4-single-hop | 200 | 22.0 | 52.0 | 61.0 | 33.3 (6) | 61.0 | 7.0 | 9.31 | 1490 | 71.5 |
| ALL answerable | 383 | 24.5 | 53.8 | 64.0 | 42.9 (92) | 64.0 | 7.9 | 9.32 | 1498 | 71.7 |

No-answer questions: n 112, quiet rate 0.0 %, items 9.44, chars 1513, p50 74.6 ms

## graphiti-deepseek-flash | question set: all questions of the dataset (3 worlds, 495 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 74 | 25.7 | 58.1 | 63.5 | 41.9 (71) | 63.5 | 10.6 | 8.82 | 1549 | 74.0 |
| 2-temporal | 90 | 36.7 | 70.0 | 78.9 | 83.3 (3) | 78.9 | 9.4 | 8.81 | 1580 | 75.9 |
| 3-open-domain | 19 | 5.3 | 15.8 | 31.6 | 18.8 (12) | 31.6 | 4.4 | 9.21 | 1583 | 83.7 |
| 4-single-hop | 200 | 28.0 | 51.0 | 58.5 | 25.0 (6) | 58.5 | 7.5 | 8.76 | 1529 | 77.9 |
| ALL answerable | 383 | 28.5 | 55.1 | 62.9 | 39.1 (92) | 62.9 | 8.4 | 8.81 | 1548 | 77.3 |

No-answer questions: n 112, quiet rate 0.0 %, items 8.77, chars 1498, p50 80.0 ms

## hindsight-chunks | question set: all questions of the dataset (8 worlds, 1683 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 240 | 40.4 | 72.9 | 80.4 | 52.3 (235) | 92.1 | 3.9 | 61.25 | 8768 | 2102.6 |
| 2-temporal | 267 | 62.2 | 80.1 | 86.5 | 53.6 (37) | 94.8 | 1.6 | 69.27 | 8575 | 2159.6 |
| 3-open-domain | 84 | 28.6 | 46.4 | 61.9 | 40.9 (46) | 75.0 | 1.7 | 70.89 | 8638 | 2221.5 |
| 4-single-hop | 711 | 57.7 | 79.6 | 85.9 | 67.1 (41) | 94.7 | 1.6 | 64.42 | 8746 | 2170.7 |
| ALL answerable | 1302 | 53.5 | 76.3 | 83.5 | 52.6 (359) | 92.9 | 2.0 | 65.25 | 8708 | 2154.0 |

No-answer questions: n 381, quiet rate 0.0 %, items 68.25, chars 8716, p50 2210.0 ms

## hindsight-llm-claude-haiku-5-5 | question set: all questions of the dataset (1 worlds, 197 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 32 | 28.1 | 62.5 | 87.5 | 57.2 (29) | 96.9 | 2.4 | 86.34 | 14939 | 2441.5 |
| 2-temporal | 37 | 64.9 | 94.6 | 94.6 | - | 94.6 | 1.2 | 82.03 | 14521 | 2518.7 |
| 3-open-domain | 11 | 18.2 | 45.5 | 81.8 | 50.0 (8) | 100.0 | 1.9 | 90.55 | 16132 | 2650.8 |
| 4-single-hop | 70 | 42.9 | 71.4 | 78.6 | 100.0 (1) | 88.6 | 1.0 | 88.99 | 15092 | 2572.9 |
| ALL answerable | 150 | 43.3 | 73.3 | 84.7 | 56.8 (38) | 92.7 | 1.4 | 86.82 | 14995 | 2542.3 |

No-answer questions: n 47, quiet rate 0.0 %, items 92.47, chars 16064, p50 2605.2 ms

## hindsight-llm-deepseek-flash | question set: all questions of the dataset (1 worlds, 197 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 32 | 25.0 | 65.6 | 84.4 | 54.9 (29) | 96.9 | 2.2 | 89.81 | 15436 | 2368.4 |
| 2-temporal | 37 | 73.0 | 91.9 | 94.6 | - | 97.3 | 1.2 | 82.92 | 14666 | 2402.5 |
| 3-open-domain | 11 | 36.4 | 45.5 | 63.6 | 43.8 (8) | 81.8 | 1.5 | 86.82 | 15268 | 2596.7 |
| 4-single-hop | 70 | 40.0 | 71.4 | 80.0 | 100.0 (1) | 87.1 | 1.0 | 91.89 | 15574 | 2464.4 |
| ALL answerable | 150 | 44.7 | 73.3 | 83.3 | 53.7 (38) | 91.3 | 1.3 | 88.86 | 15298 | 2436.1 |

No-answer questions: n 47, quiet rate 0.0 %, items 92.74, chars 15977, p50 2556.2 ms

## lingshu-dsh-memory | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 12.1 | 30.1 | 39.7 | 18.3 (277) | 1.4 | 0.4 | 4.00 | 786 | 2.8 |
| 2-temporal | 320 | 24.1 | 38.4 | 42.8 | 26.2 (40) | 1.6 | 0.4 | 4.00 | 777 | 2.8 |
| 3-open-domain | 92 | 10.9 | 20.7 | 29.3 | 13.2 (50) | 2.2 | 0.5 | 4.00 | 769 | 2.8 |
| 4-single-hop | 841 | 22.4 | 34.5 | 37.8 | 25.4 (46) | 1.8 | 0.4 | 4.00 | 768 | 2.8 |
| ALL answerable | 1535 | 20.1 | 33.7 | 38.7 | 19.2 (413) | 1.7 | 0.4 | 4.00 | 773 | 2.8 |

No-answer questions: n 446, quiet rate 0.0 %, items 4.00, chars 766, p50 2.7 ms

## mem0-llm-claude-haiku-5-5 | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 34.0 | 72.3 | 82.6 | 52.6 (277) | 90.8 | 9.7 | 19.13 | 3122 | 64.7 |
| 2-temporal | 320 | 52.2 | 73.8 | 81.6 | 56.2 (40) | 86.2 | 5.0 | 18.97 | 3037 | 70.0 |
| 3-open-domain | 92 | 22.8 | 45.7 | 56.5 | 31.3 (50) | 70.7 | 5.4 | 19.04 | 2954 | 73.0 |
| 4-single-hop | 841 | 40.4 | 77.3 | 83.2 | 70.3 (46) | 86.4 | 4.8 | 19.03 | 3055 | 73.3 |
| ALL answerable | 1535 | 40.7 | 73.7 | 81.2 | 52.3 (413) | 86.3 | 5.8 | 19.04 | 3058 | 70.4 |

No-answer questions: n 446, quiet rate 0.0 %, items 19.17, chars 3054, p50 70.2 ms

## mem0-llm-deepseek-flash | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 27.3 | 64.2 | 77.3 | 48.8 (277) | 86.2 | 9.1 | 18.87 | 2941 | 170.7 |
| 2-temporal | 320 | 38.8 | 71.2 | 79.7 | 48.8 (40) | 85.0 | 5.0 | 18.55 | 2923 | 180.1 |
| 3-open-domain | 92 | 21.7 | 39.1 | 53.3 | 29.5 (50) | 66.3 | 5.0 | 18.90 | 2863 | 172.0 |
| 4-single-hop | 841 | 35.1 | 74.9 | 83.6 | 65.2 (46) | 88.0 | 5.0 | 18.64 | 2913 | 188.7 |
| ALL answerable | 1535 | 33.6 | 70.0 | 79.8 | 48.3 (413) | 85.7 | 5.8 | 18.68 | 2917 | 181.0 |

No-answer questions: n 446, quiet rate 0.0 %, items 18.86, chars 2898, p50 163.0 ms

## mem0-verbatim | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 26.2 | 61.3 | 69.5 | 40.4 (277) | 74.8 | 7.1 | 20.00 | 2625 | 195.6 |
| 2-temporal | 320 | 49.4 | 71.9 | 77.8 | 55.4 (40) | 83.1 | 4.5 | 20.00 | 2499 | 193.5 |
| 3-open-domain | 92 | 16.3 | 39.1 | 43.5 | 25.4 (50) | 52.2 | 3.3 | 20.00 | 2263 | 217.3 |
| 4-single-hop | 841 | 37.2 | 66.7 | 73.7 | 58.7 (46) | 80.4 | 4.2 | 20.00 | 2645 | 220.7 |
| ALL answerable | 1535 | 36.5 | 65.1 | 72.0 | 42.1 (413) | 78.2 | 4.7 | 20.00 | 2588 | 209.5 |

No-answer questions: n 446, quiet rate 0.0 %, items 20.00, chars 2451, p50 183.6 ms

## memsearch | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 22.7 | 53.9 | 66.3 | 35.7 (277) | 53.9 | 15.2 | 5.00 | 686 | 140.2 |
| 2-temporal | 320 | 51.2 | 73.1 | 77.2 | 50.0 (40) | 70.3 | 14.7 | 5.00 | 701 | 139.0 |
| 3-open-domain | 92 | 15.2 | 37.0 | 45.7 | 29.6 (50) | 37.0 | 8.5 | 5.00 | 651 | 152.1 |
| 4-single-hop | 841 | 40.4 | 67.1 | 77.2 | 51.4 (46) | 67.5 | 13.7 | 5.00 | 690 | 119.5 |
| ALL answerable | 1535 | 37.9 | 64.1 | 73.3 | 38.1 (413) | 63.8 | 13.9 | 5.00 | 689 | 129.3 |

No-answer questions: n 446, quiet rate 0.0 %, items 5.00, chars 674, p50 107.8 ms

## meow-memory | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 14.5 | 25.2 | 30.1 | 12.6 (277) | 21.3 | 11.5 | 2.00 | 313 | 11.7 |
| 2-temporal | 320 | 34.4 | 46.6 | 48.8 | 21.9 (40) | 45.0 | 22.8 | 2.00 | 319 | 11.3 |
| 3-open-domain | 92 | 10.9 | 21.7 | 26.1 | 16.3 (50) | 18.5 | 9.8 | 2.00 | 292 | 12.7 |
| 4-single-hop | 841 | 34.4 | 48.0 | 51.0 | 26.8 (46) | 42.7 | 21.5 | 2.00 | 293 | 11.2 |
| ALL answerable | 1535 | 29.3 | 42.0 | 45.2 | 15.5 (413) | 37.8 | 19.2 | 2.00 | 302 | 11.3 |

No-answer questions: n 446, quiet rate 0.0 %, items 2.00, chars 301, p50 11.1 ms

## openviking | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 15.2 | 40.1 | 53.5 | 28.0 (277) | 53.5 | 7.8 | 10.00 | 1129 | 22.4 |
| 2-temporal | 320 | 31.9 | 59.4 | 72.2 | 48.8 (40) | 72.2 | 7.7 | 10.00 | 1136 | 19.1 |
| 3-open-domain | 92 | 10.9 | 31.5 | 40.2 | 20.2 (50) | 40.2 | 4.6 | 10.00 | 1014 | 18.5 |
| 4-single-hop | 841 | 24.1 | 55.2 | 66.7 | 55.8 (46) | 66.7 | 6.9 | 10.00 | 1129 | 21.0 |
| ALL answerable | 1535 | 23.3 | 51.9 | 63.8 | 32.2 (413) | 63.8 | 7.1 | 10.00 | 1124 | 20.8 |

No-answer questions: n 446, quiet rate 0.0 %, items 10.00, chars 1071, p50 20.9 ms

## seekmemory | question set: all questions of the dataset (10 worlds, 1981 questions)

| type | n | Hit@1 | Hit@5 | Hit@10 | evid@10 (n) | deliv. hit | precision | items | chars | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1-multi-hop | 282 | 6.4 | 16.0 | 28.4 | 11.5 (277) | 17.7 | 4.4 | 5.94 | 514 | 13.8 |
| 2-temporal | 320 | 20.0 | 28.1 | 39.4 | 19.2 (40) | 29.4 | 6.2 | 5.76 | 520 | 13.7 |
| 3-open-domain | 92 | 0.0 | 4.3 | 15.2 | 9.5 (50) | 2.2 | 0.5 | 5.47 | 519 | 14.9 |
| 4-single-hop | 841 | 17.8 | 31.2 | 44.2 | 26.4 (46) | 31.5 | 7.1 | 5.24 | 529 | 14.7 |
| ALL answerable | 1535 | 15.1 | 26.1 | 38.6 | 13.7 (413) | 26.8 | 6.0 | 5.49 | 524 | 14.4 |

No-answer questions: n 446, quiet rate 0.0 %, items 5.20, chars 531, p50 14.8 ms

## Latency of Gateway.recall or the adapter's deployed call (ms)

| system | kind | world (memories) | questions | p50 | p95 |
|---|---|---|---|---|---|
| dsh-fish-memory | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 1.8 | 3.5 |
| dsh-fish-memory-expand-claude-haiku-5-5 | measured under load (parallel run, the worlds it ran on) | the worlds it ran on | 197 | 2.8 | 5.7 |
| dsh-fish-memory-expand-deepseek-flash | measured under load (parallel run, the worlds it ran on) | the worlds it ran on | 197 | 2.0 | 3.9 |
| dsh-mnemon | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 38.9 | 50.9 |
| dsh-mnemon-embedding | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 69.6 | 88.8 |
| dsh-prime-memory | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 0.8 | 1.2 |
| dsh-prime-memory-embedding | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 22.0 | 23.2 |
| dsh-soul-md | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 12.6 | 14.3 |
| feiyu-memgate-embedding | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 23.4 | 50.5 |
| feiyu-memgate-embedding-precise | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 22.2 | 50.5 |
| feiyu-memgate-hybrid | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 40.6 | 68.7 |
| feiyu-memgate-hybrid-precise | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 35.4 | 50.0 |
| feiyu-memgate-sqlite | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 17.6 | 20.2 |
| feiyu-memgate-sqlite-precise | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 17.1 | 20.4 |
| graphiti-claude-haiku-5-5 | measured under load (parallel run, the worlds it ran on) | the worlds it ran on | 495 | 72.6 | 104.9 |
| graphiti-deepseek-flash | measured under load (parallel run, the worlds it ran on) | the worlds it ran on | 495 | 78.1 | 110.7 |
| hindsight-chunks | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 1800.2 | 2233.7 |
| hindsight-llm-claude-haiku-5-5 | measured under load (parallel run, the worlds it ran on) | the worlds it ran on | 197 | 2558.5 | 2807.4 |
| hindsight-llm-deepseek-flash | measured under load (parallel run, the worlds it ran on) | the worlds it ran on | 197 | 2451.6 | 2859.2 |
| lingshu-dsh-memory | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 2.1 | 2.6 |
| mem0-llm-claude-haiku-5-5 | measured under load (parallel run, the worlds it ran on) | the worlds it ran on | 1981 | 70.3 | 202.3 |
| mem0-llm-deepseek-flash | measured under load (parallel run, the worlds it ran on) | the worlds it ran on | 1981 | 178.7 | 342.8 |
| mem0-verbatim | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 98.7 | 122.6 |
| memsearch | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 88.1 | 174.1 |
| meow-memory | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 7.3 | 8.8 |
| openviking | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 16.3 | 30.1 |
| seekmemory | sequential pass on an idle machine, one world | conv-41 (663) | 193 | 10.6 | 12.8 |