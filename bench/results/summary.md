# 记忆检索基准结果

数据集版本 v1：240 条记忆，160 个问题（lexical 45、paraphrase 55、updated 20、quiet 40）。

## 怎么读

每个系统读入同一批 240 条记忆，回答同一批 160 个问题；这里只是把各自的原始答案按同一把尺子打分。“排名”类指标（Hit@1、Hit@5、MRR@10、Recall@5）看的是系统不设阈值时前 10 条里有没有正确的记忆，反映检索本身的能力；“实际交付”类指标（命中、噪声、条数、安静率）看的是系统按默认设置真正会交给模型的内容，反映开箱即用的体验。安静率是对“记忆里没有答案”的问题什么都不返回的比例，越高越好，它和命中率此消彼长，不能只看其中一个。新旧优先率只统计“事实被更新过”的问题，看新记忆是否排在旧记忆前面。分题型的表更有信息量：关键词检索通常在“字面”题上很强、在“改写”题上很弱，语义检索相反。延迟是单机上每个问题的墙钟时间，只能在同一台机器上的同一批结果之间比较；入库时间、模型调用量和费用由各系统自报。240 条记忆的数据集很小，几个百分点的差距不应当被当作结论。精确率是交给模型的条目里正确的占比（等于 1 减噪声，只统计交付了东西的可答问题，分母见各表下的说明），交付 F1 是交付命中和精确率的调和平均，两个都高才高：什么都不交精确率没有定义，全交命中高但精确率低。平均字数是每个问题平均交给模型多少字的记忆原文，直接反映会占掉多少上下文；Hit@k 曲线是 k 从 1 到 10 时前 k 条里有正确记忆的比例，看一个系统要翻到第几条才够用。“新旧优先”看不设阈值的排名里新记忆是否排在旧记忆前面；“交付中新旧优先”看实际交给模型的那几条：在期望的新记忆被交付了的更新题里，旧记忆没有被一起交付、或者排在新记忆后面的占比，括号里是“成立数/这样的题数”，表里的分母每个系统不同（新记忆没交付的题不算），所以要连同题数一起读。各系统的模式和注意事项见文末的备注，比较前请先读它们。

## 总表（按 Hit@5 排序）

| 系统 | Hit@1 | Hit@5 | MRR@10 | Recall@5 | 交付命中 | 噪声 | 平均条数 | 安静率 | 新旧优先 | p50 ms | p95 ms | 入库 s | tokens | 费用 CNY | 精确率 | 交付 F1 | 平均字数 | 家族 | 检索类型 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| feiyu-memgate-embedding-embeddinggemma | 81.7% | 99.2% | 0.887 | 99.2% | 97.5% | 36.2% | 2.04 | 37.5% | 40.0% | 12.65 | 38.68 | 2.7 | 0 | 0.00 | 63.8% | 77.1% | 48 | feiyu-memgate | vector |
| feiyu-memgate-embedding-embeddinggemma-precise | 81.7% | 99.2% | 0.887 | 99.2% | 76.7% | 14.2% | 0.81 | 72.5% | 40.0% | 12.62 | 39.63 | 2.5 | 0 | 0.00 | 85.8% | 81.0% | 20 | feiyu-memgate | vector |
| feiyu-memgate-embedding | 81.7% | 98.3% | 0.890 | 98.3% | 96.7% | 43.1% | 2.46 | 32.5% | 50.0% | 24.36 | 51.34 | 6.4 | 0 | 0.00 | 56.9% | 71.6% | 59 | feiyu-memgate | vector |
| feiyu-memgate-embedding-precise | 81.7% | 98.3% | 0.890 | 98.3% | 81.7% | 17.5% | 0.98 | 65.0% | 50.0% | 24.71 | 51.34 | 6.5 | 0 | 0.00 | 82.5% | 82.1% | 24 | feiyu-memgate | vector |
| feiyu-memgate-openviking | 81.7% | 98.3% | 0.889 | 98.3% | 90.8% | 24.1% | 1.50 | 40.0% | 50.0% | 25.73 | 53.22 | 69.3 | 0 | 0.00 | 75.9% | 82.7% | 36 | feiyu-memgate | vector |
| feiyu-memgate-openviking-precise | 81.7% | 98.3% | 0.889 | 98.3% | 87.5% | 19.8% | 1.17 | 40.0% | 50.0% | 25.28 | 52.85 | 71.8 | 0 | 0.00 | 80.2% | 83.7% | 28 | feiyu-memgate | vector |
| mem0-verbatim | 81.7% | 98.3% | 0.890 | 98.3% | 100.0% | 95.0% | 20.00 | 0.0% | 50.0% | 35.40 | 79.89 | 9.3 | 0 | 0.00 | 5.0% | 9.5% | 482 | other | vector |
| memsearch | 85.0% | 98.3% | 0.912 | 98.3% | 98.3% | 80.3% | 5.00 | 0.0% | 60.0% | 26.36 | 52.68 | 13.6 | 0 | 0.00 | 19.7% | 32.8% | 120 | other | hybrid |
| openviking | 81.7% | 98.3% | 0.889 | 98.3% | 99.2% | 90.1% | 9.94 | 0.0% | 50.0% | 24.92 | 51.80 | 71.8 | 0 | 0.00 | 9.9% | 18.0% | 240 | other | vector |
| openviking-unfiltered | 81.7% | 98.3% | 0.889 | 98.3% | 99.2% | 90.1% | 9.99 | 0.0% | 50.0% | 24.92 | 51.80 | 71.8 | 0 | 0.00 | 9.9% | 18.0% | 241† | other | vector |
| feiyu-memgate-hybrid | 83.3% | 96.7% | 0.896 | 96.7% | 92.5% | 37.4% | 2.01 | 35.0% | 50.0% | 27.90 | 55.21 | 6.4 | 0 | 0.00 | 62.6% | 74.7% | 49 | feiyu-memgate | hybrid |
| feiyu-memgate-hybrid-precise | 83.3% | 96.7% | 0.896 | 96.7% | 65.0% | 9.6% | 0.66 | 75.0% | 50.0% | 27.89 | 55.14 | 6.6 | 0 | 0.00 | 90.4% | 75.6% | 16 | feiyu-memgate | hybrid |
| feiyu-memgate-embedding-qwen3-embedding | 84.2% | 95.8% | 0.900 | 95.8% | 94.2% | 29.3% | 1.76 | 32.5% | 50.0% | 75.49 | 85.86 | 10.4 | 0 | 0.00 | 70.7% | 80.8% | 43 | feiyu-memgate | vector |
| feiyu-memgate-embedding-qwen3-embedding-precise | 84.2% | 95.8% | 0.900 | 95.8% | 85.0% | 18.1% | 0.97 | 37.5% | 50.0% | 74.64 | 83.83 | 10.3 | 0 | 0.00 | 81.9% | 83.4% | 24 | feiyu-memgate | vector |
| feiyu-memgate-full | 83.3% | 95.0% | 0.896 | 95.0% | 93.3% | 39.9% | 2.08 | 35.0% | 50.0% | 33.45 | 54.47 | 6.4 | 0 | 0.00 | 60.1% | 73.1% | 50 | feiyu-memgate | hybrid |
| feiyu-memgate-full-precise | 84.2% | 95.0% | 0.899 | 95.0% | 65.0% | 9.8% | 0.67 | 75.0% | 50.0% | 33.08 | 55.03 | 6.4 | 0 | 0.00 | 90.2% | 75.6% | 16 | feiyu-memgate | hybrid |
| mem0 | 77.5% | 95.0% | 0.851 | 95.0% | 99.2% | 95.0% | 19.76 | 0.0% | 60.0% | 69.33 | 609.84 | 471.2 | 2059186 | 0.53 | 5.0% | 9.6% | 478 | other | extraction |
| feiyu-memgate-embedding-bge-base-zh | 82.5% | 93.3% | 0.872 | 93.3% | 88.3% | 29.8% | 1.51 | 37.5% | 45.0% | 19.11 | 46.73 | 3.5 | 0 | 0.00 | 70.2% | 78.2% | 37 | feiyu-memgate | vector |
| feiyu-memgate-embedding-bge-base-zh-precise | 82.5% | 93.3% | 0.872 | 93.3% | 72.5% | 13.8% | 0.77 | 65.0% | 45.0% | 18.17 | 45.36 | 3.4 | 0 | 0.00 | 86.2% | 78.7% | 19 | feiyu-memgate | vector |
| feiyu-memgate-embedding-bge-small-zh | 75.8% | 92.5% | 0.833 | 92.5% | 86.7% | 31.0% | 1.79 | 20.0% | 35.0% | 6.43 | 32.87 | 1.0 | 0 | 0.00 | 69.0% | 76.8% | 43 | feiyu-memgate | vector |
| feiyu-memgate-embedding-bge-small-zh-precise | 75.8% | 92.5% | 0.833 | 92.5% | 76.7% | 20.6% | 0.94 | 47.5% | 35.0% | 6.33 | 30.68 | 1.0 | 0 | 0.00 | 79.4% | 78.0% | 23 | feiyu-memgate | vector |
| dsh-mnemon-embedding | 80.0% | 90.8% | 0.845 | 90.8% | 87.5% | 61.4% | 2.24 | 0.0% | 55.0% | 78.08 | 105.26 | 12.2 | 0 | 0.00 | 38.6% | 53.6% | 54 | other | hybrid |
| feiyu-memgate-sqlite | 61.7% | 81.7% | 0.708 | 81.7% | 46.7% | 10.7% | 0.55 | 70.0% | 55.0% | 2.17 | 3.10 | 0.0 | 0 | 0.00 | 89.3% | 61.3% | 14 | feiyu-memgate | keyword |
| feiyu-memgate-sqlite-precise | 61.7% | 81.7% | 0.708 | 81.7% | 39.2% | 7.0% | 0.41 | 75.0% | 55.0% | 2.15 | 3.04 | 0.0 | 0 | 0.00 | 93.0% | 55.1% | 10 | feiyu-memgate | keyword |
| lingshu-dsh-memory | 65.0% | 81.7% | 0.726 | 81.7% | 0.0% | 100.0% | 4.00 | 0.0% | 55.0% | 2.18 | 2.63 | 4.1 | 0 | 0.00 | 0.0% | 0.0% | 90 | other | keyword |
| seekmemory | 50.8% | 64.2% | 0.571 | 64.2% | 65.0% | 82.8% | 4.09 | 22.5% | 45.0% | 1.97 | 2.92 | 0.2 | 0 | 0.00 | 17.2% | 27.3% | 98 | other | keyword |
| seekmemory-aged | 52.5% | 64.2% | 0.582 | 64.2% | 65.0% | 82.8% | 4.09 | 22.5% | 45.0% | 1.82 | 2.74 | 0.2 | 0 | 0.00 | 17.2% | 27.3% | 98 | other | keyword |
| meow-memory | 51.7% | 63.3% | 0.566 | 63.3% | 56.7% | 69.1% | 1.80 | 10.0% | 45.0% | 1.99 | 3.00 | 0.2 | 0 | 0.00 | 30.9% | 40.0% | 44 | other | keyword |
| dsh-mnemon | 38.3% | 42.5% | 0.401 | 42.5% | 38.3% | 62.9% | 1.02 | 0.0% | 5.0% | 33.09 | 39.36 | 3.0 | 0 | 0.00 | 37.1% | 37.7% | 25 | other | keyword |
| graphiti | 12.5% | 13.3% | 0.129 | 13.3% | 13.3% | 53.5% | 0.31 | 82.5% | 5.0% | 390.93 | 848.18 | 1346.7 | 1176519 | 2.00 | 46.5% | 20.7% | 8 | other | graph |
| seekmemory-aged-detail | 0.0% | 0.0% | 0.000 | 0.0% | 0.0% | - | 0.00 | 100.0% | 0.0% | 0.09 | 0.13 | 0.2 | 0 | 0.00 | - | - | 0 | other | keyword |

精确率和交付 F1 只统计交付了东西的可答问题（共 120 个可答问题；各系统的分母：feiyu-memgate-embedding-embeddinggemma 119，feiyu-memgate-embedding-embeddinggemma-precise 103，feiyu-memgate-embedding 118，feiyu-memgate-embedding-precise 106，feiyu-memgate-openviking 117，feiyu-memgate-openviking-precise 117，mem0-verbatim 120，memsearch 120，openviking 120，openviking-unfiltered 120，feiyu-memgate-hybrid 117，feiyu-memgate-hybrid-precise 80，feiyu-memgate-embedding-qwen3-embedding 120，feiyu-memgate-embedding-qwen3-embedding-precise 120，feiyu-memgate-full 117，feiyu-memgate-full-precise 80，mem0 120，feiyu-memgate-embedding-bge-base-zh 117，feiyu-memgate-embedding-bge-base-zh-precise 94，feiyu-memgate-embedding-bge-small-zh 119，feiyu-memgate-embedding-bge-small-zh-precise 110，dsh-mnemon-embedding 120，feiyu-memgate-sqlite 59，feiyu-memgate-sqlite-precise 50，lingshu-dsh-memory 120，seekmemory 116，seekmemory-aged 116，meow-memory 115，dsh-mnemon 120，graphiti 24，seekmemory-aged-detail 0）；什么都没交的可答问题不进分母，所以交得很少的系统精确率可以很高。

† 平均字数：这些系统交付的条目里有没有记忆编号的（openviking-unfiltered 8 条），按全部记忆的平均长度计入。

‡ 延迟和入库时间：mem0、graphiti 的结果文件是付费模式的结果，没有在最后这次逐个顺序运行里重跑（重跑要再花钱），它们的 p50/p95 和入库时间来自一台同时有别的任务在跑的机器，不能和其他行的时间直接比。

## Hit@k 曲线（前 k 条里有正确记忆的比例）

| 系统 | @1 | @2 | @3 | @4 | @5 | @6 | @7 | @8 | @9 | @10 |
|---|---|---|---|---|---|---|---|---|---|---|
| feiyu-memgate-embedding-embeddinggemma | 81.7% | 90.8% | 95.8% | 96.7% | 99.2% | 99.2% | 100.0% | 100.0% | 100.0% | 100.0% |
| feiyu-memgate-embedding-embeddinggemma-precise | 81.7% | 90.8% | 95.8% | 96.7% | 99.2% | 99.2% | 100.0% | 100.0% | 100.0% | 100.0% |
| feiyu-memgate-embedding | 81.7% | 94.2% | 95.0% | 96.7% | 98.3% | 98.3% | 98.3% | 99.2% | 99.2% | 99.2% |
| feiyu-memgate-embedding-precise | 81.7% | 94.2% | 95.0% | 96.7% | 98.3% | 98.3% | 98.3% | 99.2% | 99.2% | 99.2% |
| feiyu-memgate-openviking | 81.7% | 93.3% | 95.0% | 96.7% | 98.3% | 98.3% | 98.3% | 99.2% | 99.2% | 99.2% |
| feiyu-memgate-openviking-precise | 81.7% | 93.3% | 95.0% | 96.7% | 98.3% | 98.3% | 98.3% | 99.2% | 99.2% | 99.2% |
| mem0-verbatim | 81.7% | 94.2% | 95.0% | 96.7% | 98.3% | 98.3% | 98.3% | 99.2% | 99.2% | 99.2% |
| memsearch | 85.0% | 95.8% | 95.8% | 96.7% | 98.3% | 99.2% | 99.2% | 100.0% | 100.0% | 100.0% |
| openviking | 81.7% | 93.3% | 95.0% | 96.7% | 98.3% | 98.3% | 98.3% | 99.2% | 99.2% | 99.2% |
| openviking-unfiltered | 81.7% | 93.3% | 95.0% | 96.7% | 98.3% | 98.3% | 98.3% | 99.2% | 99.2% | 99.2% |
| feiyu-memgate-hybrid | 83.3% | 93.3% | 94.2% | 94.2% | 96.7% | 97.5% | 98.3% | 98.3% | 100.0% | 100.0% |
| feiyu-memgate-hybrid-precise | 83.3% | 93.3% | 94.2% | 94.2% | 96.7% | 97.5% | 98.3% | 98.3% | 100.0% | 100.0% |
| feiyu-memgate-embedding-qwen3-embedding | 84.2% | 94.2% | 95.8% | 95.8% | 95.8% | 96.7% | 96.7% | 96.7% | 98.3% | 98.3% |
| feiyu-memgate-embedding-qwen3-embedding-precise | 84.2% | 94.2% | 95.8% | 95.8% | 95.8% | 96.7% | 96.7% | 96.7% | 98.3% | 98.3% |
| feiyu-memgate-full | 83.3% | 94.2% | 94.2% | 94.2% | 95.0% | 97.5% | 98.3% | 98.3% | 99.2% | 100.0% |
| feiyu-memgate-full-precise | 84.2% | 93.3% | 94.2% | 94.2% | 95.0% | 97.5% | 98.3% | 98.3% | 99.2% | 100.0% |
| mem0 | 77.5% | 88.3% | 90.0% | 92.5% | 95.0% | 97.5% | 97.5% | 97.5% | 98.3% | 98.3% |
| feiyu-memgate-embedding-bge-base-zh | 82.5% | 90.0% | 90.8% | 91.7% | 93.3% | 94.2% | 94.2% | 94.2% | 94.2% | 94.2% |
| feiyu-memgate-embedding-bge-base-zh-precise | 82.5% | 90.0% | 90.8% | 91.7% | 93.3% | 94.2% | 94.2% | 94.2% | 94.2% | 94.2% |
| feiyu-memgate-embedding-bge-small-zh | 75.8% | 85.8% | 89.2% | 91.7% | 92.5% | 93.3% | 94.2% | 94.2% | 95.0% | 96.7% |
| feiyu-memgate-embedding-bge-small-zh-precise | 75.8% | 85.8% | 89.2% | 91.7% | 92.5% | 93.3% | 94.2% | 94.2% | 95.0% | 96.7% |
| dsh-mnemon-embedding | 80.0% | 86.7% | 87.5% | 90.0% | 90.8% | 90.8% | 91.7% | 91.7% | 91.7% | 91.7% |
| feiyu-memgate-sqlite | 61.7% | 74.2% | 77.5% | 80.0% | 81.7% | 81.7% | 84.2% | 85.8% | 87.5% | 88.3% |
| feiyu-memgate-sqlite-precise | 61.7% | 74.2% | 77.5% | 80.0% | 81.7% | 81.7% | 84.2% | 85.8% | 87.5% | 88.3% |
| lingshu-dsh-memory | 65.0% | 75.0% | 76.7% | 80.8% | 81.7% | 82.5% | 85.0% | 85.8% | 86.7% | 88.3% |
| seekmemory | 50.8% | 58.3% | 60.8% | 63.3% | 64.2% | 65.8% | 69.2% | 69.2% | 70.0% | 70.8% |
| seekmemory-aged | 52.5% | 59.2% | 61.7% | 63.3% | 64.2% | 65.8% | 69.2% | 69.2% | 70.0% | 70.8% |
| meow-memory | 51.7% | 54.2% | 60.0% | 62.5% | 63.3% | 64.2% | 68.3% | 69.2% | 70.0% | 70.0% |
| dsh-mnemon | 38.3% | 40.8% | 41.7% | 42.5% | 42.5% | 42.5% | 42.5% | 42.5% | 42.5% | 42.5% |
| graphiti | 12.5% | 13.3% | 13.3% | 13.3% | 13.3% | 13.3% | 13.3% | 13.3% | 13.3% | 13.3% |
| seekmemory-aged-detail | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

## 字面题（lexical）

| 系统 | Hit@1 | Hit@5 | MRR@10 | Recall@5 | 交付命中 | 噪声 | 平均条数 |
|---|---|---|---|---|---|---|---|
| feiyu-memgate-embedding-embeddinggemma | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 16.5% | 1.51 |
| feiyu-memgate-embedding-embeddinggemma-precise | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 0.0% | 1.00 |
| feiyu-memgate-embedding | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 19.7% | 1.64 |
| feiyu-memgate-embedding-precise | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 0.0% | 1.00 |
| feiyu-memgate-openviking | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 3.7% | 1.09 |
| feiyu-memgate-openviking-precise | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 0.0% | 1.00 |
| mem0-verbatim | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 95.0% | 20.00 |
| memsearch | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 80.0% | 5.00 |
| openviking | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 90.0% | 10.00 |
| openviking-unfiltered | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 90.0% | 10.00 |
| feiyu-memgate-hybrid | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 19.0% | 1.51 |
| feiyu-memgate-hybrid-precise | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 0.0% | 1.00 |
| feiyu-memgate-embedding-qwen3-embedding | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 10.6% | 1.29 |
| feiyu-memgate-embedding-qwen3-embedding-precise | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 0.0% | 1.00 |
| feiyu-memgate-full | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 27.2% | 1.78 |
| feiyu-memgate-full-precise | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 2.2% | 1.04 |
| mem0 | 97.8% | 100.0% | 0.989 | 100.0% | 100.0% | 94.9% | 19.73 |
| feiyu-memgate-embedding-bge-base-zh | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 9.3% | 1.20 |
| feiyu-memgate-embedding-bge-base-zh-precise | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 0.0% | 1.00 |
| feiyu-memgate-embedding-bge-small-zh | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 5.9% | 1.13 |
| feiyu-memgate-embedding-bge-small-zh-precise | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 0.0% | 1.00 |
| dsh-mnemon-embedding | 100.0% | 100.0% | 1.000 | 100.0% | 100.0% | 59.4% | 2.64 |
| feiyu-memgate-sqlite | 97.8% | 100.0% | 0.989 | 100.0% | 97.8% | 0.0% | 0.98 |
| feiyu-memgate-sqlite-precise | 97.8% | 100.0% | 0.989 | 100.0% | 95.6% | 0.0% | 0.96 |
| lingshu-dsh-memory | 97.8% | 100.0% | 0.989 | 100.0% | 0.0% | 100.0% | 4.00 |
| seekmemory | 97.8% | 97.8% | 0.980 | 97.8% | 97.8% | 72.9% | 4.42 |
| seekmemory-aged | 97.8% | 97.8% | 0.980 | 97.8% | 97.8% | 72.9% | 4.42 |
| meow-memory | 97.8% | 97.8% | 0.980 | 97.8% | 97.8% | 48.9% | 1.96 |
| dsh-mnemon | 95.6% | 95.6% | 0.956 | 95.6% | 93.3% | 10.0% | 1.07 |
| graphiti | 24.4% | 24.4% | 0.244 | 24.4% | 24.4% | 50.0% | 0.53 |
| seekmemory-aged-detail | 0.0% | 0.0% | 0.000 | 0.0% | 0.0% | - | 0.00 |

## 改写题（paraphrase）

| 系统 | Hit@1 | Hit@5 | MRR@10 | Recall@5 | 交付命中 | 噪声 | 平均条数 |
|---|---|---|---|---|---|---|---|
| feiyu-memgate-embedding-embeddinggemma | 81.8% | 98.2% | 0.876 | 98.2% | 96.4% | 44.3% | 2.56 |
| feiyu-memgate-embedding-embeddinggemma-precise | 81.8% | 98.2% | 0.876 | 98.2% | 69.1% | 7.3% | 0.75 |
| feiyu-memgate-embedding | 80.0% | 96.4% | 0.864 | 96.4% | 92.7% | 54.7% | 3.09 |
| feiyu-memgate-embedding-precise | 80.0% | 96.4% | 0.864 | 96.4% | 69.1% | 20.5% | 1.04 |
| feiyu-memgate-openviking | 80.0% | 96.4% | 0.864 | 96.4% | 81.8% | 30.5% | 1.76 |
| feiyu-memgate-openviking-precise | 80.0% | 96.4% | 0.864 | 96.4% | 81.8% | 24.0% | 1.29 |
| mem0-verbatim | 80.0% | 96.4% | 0.864 | 96.4% | 100.0% | 95.0% | 20.00 |
| memsearch | 83.6% | 96.4% | 0.894 | 96.4% | 96.4% | 80.7% | 5.00 |
| openviking | 80.0% | 96.4% | 0.864 | 96.4% | 98.2% | 90.2% | 10.00 |
| openviking-unfiltered | 80.0% | 96.4% | 0.864 | 96.4% | 98.2% | 90.2% | 10.00 |
| feiyu-memgate-hybrid | 81.8% | 92.7% | 0.868 | 92.7% | 85.5% | 42.4% | 2.20 |
| feiyu-memgate-hybrid-precise | 81.8% | 92.7% | 0.868 | 92.7% | 34.5% | 2.6% | 0.36 |
| feiyu-memgate-embedding-qwen3-embedding | 83.6% | 90.9% | 0.874 | 90.9% | 89.1% | 33.0% | 2.04 |
| feiyu-memgate-embedding-qwen3-embedding-precise | 83.6% | 90.9% | 0.874 | 90.9% | 85.5% | 17.6% | 1.11 |
| feiyu-memgate-full | 81.8% | 89.1% | 0.870 | 89.1% | 85.5% | 42.9% | 2.24 |
| feiyu-memgate-full-precise | 83.6% | 89.1% | 0.876 | 89.1% | 34.5% | 2.6% | 0.36 |
| mem0 | 67.3% | 92.7% | 0.769 | 92.7% | 98.2% | 95.0% | 19.76 |
| feiyu-memgate-embedding-bge-base-zh | 81.8% | 87.3% | 0.833 | 87.3% | 83.6% | 33.9% | 1.78 |
| feiyu-memgate-embedding-bge-base-zh-precise | 81.8% | 87.3% | 0.833 | 87.3% | 58.2% | 14.3% | 0.73 |
| feiyu-memgate-embedding-bge-small-zh | 70.9% | 83.6% | 0.763 | 83.6% | 80.0% | 40.6% | 2.27 |
| feiyu-memgate-embedding-bge-small-zh-precise | 70.9% | 83.6% | 0.763 | 83.6% | 65.5% | 21.1% | 0.91 |
| dsh-mnemon-embedding | 72.7% | 85.5% | 0.774 | 85.5% | 78.2% | 58.8% | 2.02 |
| feiyu-memgate-sqlite | 38.2% | 63.6% | 0.501 | 63.6% | 3.6% | 0.0% | 0.04 |
| feiyu-memgate-sqlite-precise | 38.2% | 63.6% | 0.501 | 63.6% | 0.0% | - | 0.00 |
| lingshu-dsh-memory | 43.6% | 63.6% | 0.533 | 63.6% | 0.0% | 100.0% | 4.00 |
| seekmemory | 18.2% | 32.7% | 0.251 | 32.7% | 32.7% | 90.8% | 4.00 |
| seekmemory-aged | 21.8% | 32.7% | 0.273 | 32.7% | 32.7% | 90.8% | 4.00 |
| meow-memory | 21.8% | 30.9% | 0.255 | 30.9% | 25.5% | 85.0% | 1.75 |
| dsh-mnemon | 3.6% | 10.9% | 0.070 | 10.9% | 5.5% | 94.5% | 1.00 |
| graphiti | 5.5% | 5.5% | 0.055 | 5.5% | 5.5% | 25.0% | 0.07 |
| seekmemory-aged-detail | 0.0% | 0.0% | 0.000 | 0.0% | 0.0% | - | 0.00 |

## 更新题（updated）

| 系统 | Hit@1 | Hit@5 | MRR@10 | Recall@5 | 交付命中 | 噪声 | 平均条数 | 新旧优先 | 交付中新旧优先 | 题数 |
|---|---|---|---|---|---|---|---|---|---|---|
| feiyu-memgate-embedding-embeddinggemma | 40.0% | 100.0% | 0.667 | 100.0% | 95.0% | 59.4% | 2.70 | 40.0% | 63.2% | 12/19 |
| feiyu-memgate-embedding-embeddinggemma-precise | 40.0% | 100.0% | 0.667 | 100.0% | 45.0% | 68.6% | 1.30 | 40.0% | 100.0% | 9/9 |
| feiyu-memgate-embedding | 45.0% | 100.0% | 0.717 | 100.0% | 100.0% | 65.0% | 3.25 | 50.0% | 80.0% | 16/20 |
| feiyu-memgate-embedding-precise | 45.0% | 100.0% | 0.717 | 100.0% | 75.0% | 53.7% | 1.60 | 50.0% | 100.0% | 15/15 |
| feiyu-memgate-openviking | 45.0% | 100.0% | 0.708 | 100.0% | 95.0% | 53.7% | 2.40 | 50.0% | 100.0% | 19/19 |
| feiyu-memgate-openviking-precise | 45.0% | 100.0% | 0.708 | 100.0% | 75.0% | 53.3% | 1.65 | 50.0% | 100.0% | 15/15 |
| mem0-verbatim | 45.0% | 100.0% | 0.717 | 100.0% | 100.0% | 95.0% | 20.00 | 50.0% | 50.0% | 10/20 |
| memsearch | 55.0% | 100.0% | 0.762 | 100.0% | 100.0% | 80.0% | 5.00 | 60.0% | 60.0% | 12/20 |
| openviking | 45.0% | 100.0% | 0.708 | 100.0% | 100.0% | 90.0% | 10.00 | 50.0% | 50.0% | 10/20 |
| openviking-unfiltered | 45.0% | 100.0% | 0.708 | 100.0% | 100.0% | 90.0% | 10.00 | 50.0% | 50.0% | 10/20 |
| feiyu-memgate-hybrid | 50.0% | 100.0% | 0.735 | 100.0% | 95.0% | 65.8% | 3.20 | 50.0% | 84.2% | 16/19 |
| feiyu-memgate-hybrid-precise | 50.0% | 100.0% | 0.735 | 100.0% | 70.0% | 44.8% | 1.35 | 50.0% | 100.0% | 14/14 |
| feiyu-memgate-embedding-qwen3-embedding | 50.0% | 100.0% | 0.750 | 100.0% | 95.0% | 61.3% | 2.65 | 50.0% | 73.7% | 14/19 |
| feiyu-memgate-embedding-qwen3-embedding-precise | 50.0% | 100.0% | 0.750 | 100.0% | 50.0% | 60.0% | 1.20 | 50.0% | 100.0% | 10/10 |
| feiyu-memgate-full | 50.0% | 100.0% | 0.735 | 100.0% | 100.0% | 61.0% | 3.05 | 50.0% | 95.0% | 19/20 |
| feiyu-memgate-full-precise | 50.0% | 100.0% | 0.735 | 100.0% | 70.0% | 39.6% | 1.30 | 50.0% | 100.0% | 14/14 |
| mem0 | 60.0% | 90.0% | 0.767 | 90.0% | 100.0% | 94.9% | 19.70 | 60.0% | 60.0% | 12/20 |
| feiyu-memgate-embedding-bge-base-zh | 45.0% | 95.0% | 0.692 | 95.0% | 75.0% | 65.7% | 2.00 | 45.0% | 80.0% | 12/15 |
| feiyu-memgate-embedding-bge-base-zh-precise | 45.0% | 95.0% | 0.692 | 95.0% | 50.0% | 57.1% | 1.10 | 45.0% | 80.0% | 8/10 |
| feiyu-memgate-embedding-bge-small-zh | 35.0% | 100.0% | 0.646 | 100.0% | 75.0% | 61.7% | 2.05 | 35.0% | 73.3% | 11/15 |
| feiyu-memgate-embedding-bge-small-zh-precise | 35.0% | 100.0% | 0.646 | 100.0% | 55.0% | 65.8% | 1.50 | 35.0% | 100.0% | 11/11 |
| dsh-mnemon-embedding | 55.0% | 85.0% | 0.692 | 85.0% | 85.0% | 72.9% | 3.05 | 55.0% | 64.7% | 11/17 |
| feiyu-memgate-sqlite | 45.0% | 90.0% | 0.646 | 90.0% | 50.0% | 48.7% | 1.05 | 55.0% | 80.0% | 8/10 |
| feiyu-memgate-sqlite-precise | 45.0% | 90.0% | 0.646 | 90.0% | 20.0% | 50.0% | 0.40 | 55.0% | 75.0% | 3/4 |
| lingshu-dsh-memory | 50.0% | 90.0% | 0.667 | 90.0% | 0.0% | 100.0% | 4.00 | 55.0% | - | 0/0 |
| seekmemory | 35.0% | 75.0% | 0.532 | 75.0% | 80.0% | 84.5% | 5.15 | 45.0% | 56.2% | 9/16 |
| seekmemory-aged | 35.0% | 75.0% | 0.536 | 75.0% | 80.0% | 84.5% | 5.15 | 45.0% | 56.2% | 9/16 |
| meow-memory | 30.0% | 75.0% | 0.489 | 75.0% | 50.0% | 75.0% | 2.00 | 45.0% | 80.0% | 8/10 |
| dsh-mnemon | 5.0% | 10.0% | 0.062 | 10.0% | 5.0% | 95.0% | 1.00 | 5.0% | 100.0% | 1/1 |
| graphiti | 5.0% | 10.0% | 0.075 | 10.0% | 10.0% | 86.7% | 0.45 | 5.0% | 50.0% | 1/2 |
| seekmemory-aged-detail | 0.0% | 0.0% | 0.000 | 0.0% | 0.0% | - | 0.00 | 0.0% | - | 0/0 |

## 安静题（quiet）

记忆里没有答案的问题；交付了任何东西都算没有保持安静。

| 系统 | 安静率 | 平均条数 |
|---|---|---|
| feiyu-memgate-embedding-embeddinggemma | 37.5% | 1.60 |
| feiyu-memgate-embedding-embeddinggemma-precise | 72.5% | 0.42 |
| feiyu-memgate-embedding | 32.5% | 2.12 |
| feiyu-memgate-embedding-precise | 65.0% | 0.57 |
| feiyu-memgate-openviking | 40.0% | 1.15 |
| feiyu-memgate-openviking-precise | 40.0% | 0.95 |
| mem0-verbatim | 0.0% | 20.00 |
| memsearch | 0.0% | 5.00 |
| openviking | 0.0% | 9.78 |
| openviking-unfiltered | 0.0% | 9.97 |
| feiyu-memgate-hybrid | 35.0% | 1.73 |
| feiyu-memgate-hybrid-precise | 75.0% | 0.35 |
| feiyu-memgate-embedding-qwen3-embedding | 32.5% | 1.45 |
| feiyu-memgate-embedding-qwen3-embedding-precise | 37.5% | 0.65 |
| feiyu-memgate-full | 35.0% | 1.70 |
| feiyu-memgate-full-precise | 75.0% | 0.35 |
| mem0 | 0.0% | 19.82 |
| feiyu-memgate-embedding-bge-base-zh | 37.5% | 1.23 |
| feiyu-memgate-embedding-bge-base-zh-precise | 65.0% | 0.40 |
| feiyu-memgate-embedding-bge-small-zh | 20.0% | 1.75 |
| feiyu-memgate-embedding-bge-small-zh-precise | 47.5% | 0.62 |
| dsh-mnemon-embedding | 0.0% | 1.68 |
| feiyu-memgate-sqlite | 70.0% | 0.53 |
| feiyu-memgate-sqlite-precise | 75.0% | 0.38 |
| lingshu-dsh-memory | 0.0% | 4.00 |
| seekmemory | 22.5% | 3.33 |
| seekmemory-aged | 22.5% | 3.33 |
| meow-memory | 10.0% | 1.60 |
| dsh-mnemon | 0.0% | 1.00 |
| graphiti | 82.5% | 0.30 |
| seekmemory-aged-detail | 100.0% | 0.00 |

## 各系统的模式与备注

### feiyu-memgate-embedding-embeddinggemma（0.1.0）

- 模式：verbatim ingest, embedding backend (embeddinggemma via an OpenAI-compatible endpoint, brute-force cosine), default recall stages (broad profile), no LLM
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：向量后端换成较轻的模型 embeddinggemma（622 MB，768 维，多语言），该模型的预设（floor、ceil、分数线、前缀），默认召回步骤（宽松档 broad：min_score 0.30、比例 0.60）；需要一个 embeddings 接口。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: model embeddinggemma, cosine mapped to 0..1 with floor 0.30 and ceil 0.75 (the backend's values for this model, chosen on the development sets, not on this data); query prefix none, document prefix none.
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages, whose default profile is broad (min_score with the backend's broad line 0.30, relative_score with its ratio 0.60, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that Gateway.recall call only. The broad lines were chosen on the development set by the broad rule of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best delivered hit); the precise lines are 0.55 and 0.85 (the -precise run).
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-embedding-embeddinggemma-precise（0.1.0）

- 模式：verbatim ingest, embedding backend (embeddinggemma via an OpenAI-compatible endpoint, brute-force cosine), default recall stages, no LLM, precise recall profile
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：和 feiyu-memgate-embedding-embeddinggemma 完全一样，只是固定 [recall] profile = "precise"：min_score 0.55、比例 0.85（默认的宽松档是 0.30 / 0.60）。留出集上，默认档 → 精确档：交付命中 97.5% → 76.7%，精确率 63.8% → 85.8%，安静率 37.5% → 72.5%，每题交付 2.04 → 0.81 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: model embeddinggemma, cosine mapped to 0..1 with floor 0.30 and ceil 0.75 (the backend's values for this model, chosen on the development sets, not on this data); query prefix none, document prefix none.
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with the backend's precise line 0.55, relative_score with its ratio 0.85, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall call only. The plain run of this backend uses the broad lines 0.30 and 0.60 instead.
- 备注：Precise profile: [recall] profile = 'precise' makes min_score and relative_score use the precision-leaning lines of this backend (min_score 0.55, ratio 0.85 instead of the broad 0.30 and 0.60), chosen on the development set by the balanced rule of bench/dev_eval.py (delivered F1 weighted with the quiet rate). It answers fewer questions and stays quiet more often than the default.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-embedding（0.1.0）

- 模式：verbatim ingest, embedding backend (bge-m3 via an OpenAI-compatible endpoint, brute-force cosine), default recall stages (broad profile), no LLM
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：向量后端（bge-m3，暴力余弦），默认召回步骤（宽松档 broad：min_score 0.30、比例 0.60）；需要一个 embeddings 接口。
- 备注：Backend is the embedding backend: bge-m3 vectors (1024 dimensions) from an embeddings endpoint, cosine mapped to 0..1 with floor 0.40 and ceil 0.80 (chosen on the development sets, not on this data).
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages, whose default profile is broad (min_score with the backend's broad line 0.30, relative_score with its ratio 0.60, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that Gateway.recall call only. The broad lines were chosen on the development set by the broad rule of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best delivered hit); the precise lines are 0.50 and 0.85 (the -precise run).
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-embedding-precise（0.1.0）

- 模式：verbatim ingest, embedding backend (bge-m3 via an OpenAI-compatible endpoint, brute-force cosine), default recall stages, no LLM, precise recall profile
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：和 feiyu-memgate-embedding 完全一样，只是固定 [recall] profile = "precise"：min_score 0.50、比例 0.85（默认的宽松档是 0.30 / 0.60）。留出集上，默认档 → 精确档：交付命中 96.7% → 81.7%，精确率 56.9% → 82.5%，安静率 32.5% → 65.0%，每题交付 2.46 → 0.98 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: bge-m3 vectors (1024 dimensions) from an embeddings endpoint, cosine mapped to 0..1 with floor 0.40 and ceil 0.80 (chosen on the development sets, not on this data).
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with the backend's precise line 0.50, relative_score with its ratio 0.85, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall call only. The plain run of this backend uses the broad lines 0.30 and 0.60 instead.
- 备注：Precise profile: [recall] profile = 'precise' makes min_score and relative_score use the precision-leaning lines of this backend (min_score 0.50, ratio 0.85 instead of the broad 0.30 and 0.60), chosen on the development set by the balanced rule of bench/dev_eval.py (delivered F1 weighted with the quiet rate). It answers fewer questions and stays quiet more often than the default.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-openviking（0.1.0 (OpenViking v0.5.3)）

- 模式：verbatim memory-file ingest into OpenViking, feiyu-memgate openviking backend (read-only store), default recall stages (broad profile), bge-m3 embeddings inside OpenViking, no LLM
- 调用：config.build_gateway(dict); OpenVikingBackend.put(text, ref=viking://~/memories/events/Y/M/D/<id>.md) per memory; OpenVikingBackend.find(query, 10) for ranked; Gateway.recall('owner', {'query': q}) for deployed; id = file name of the item's URI
- 注意：网关接在 OpenViking（只读存储）前面，默认召回步骤（宽松档 broad：min_score 0.55、比例 0.90）。
- 备注：Memories written with OpenVikingBackend.put (content/write, mode create) as memory files, no session or extraction, so no language model is used; llm_tokens is 0. Ingest went into its own OpenViking user.
- 备注：ingest_seconds includes the wait until a sample of 21 memories (and the last one written) found themselves (wait 66.9 s; writes alone 2.3 s, 0 put failure(s)).
- 备注：ranked: backend.find(query, 10) of the read-only store; directory summaries (.abstract.md, .overview.md) are skipped by the backend itself, so ranked can hold fewer than 10 items.
- 备注：deployed uses the default recall stages (min_score, relative_score, newer_first, fill_text, drop_oversize, redact, dedupe, budget) with the default profile (broad), none passed and the default limit of 5; the gateway asks the backend for 15 candidates; ms times that Gateway.recall call only. The backend maps OpenViking's raw score with its default floor 0.50 / ceil 1.00 (raw kept in Hit.meta); its precise lines are min_score 0.55 and relative ratio 0.95, its broad lines (the default profile) 0.55 and 0.90, so the min_score in use, 0.55, means raw 0.775.
- 备注：Backend and gateway timeouts are the defaults (backend socket timeout 2 s) except for ingest writes (30 s).
- 备注：Recall item ids: the memory id is the file name of the item's URI (id field); 0 item(s) unmapped.
- 备注：0 recall call(s) did not answer 200; 0 backend find call(s) for ranked failed.
- 备注：Gateway.recall was called directly, so the per-client rate limiter was not involved.

### feiyu-memgate-openviking-precise（0.1.0 (OpenViking v0.5.3)）

- 模式：verbatim memory-file ingest into OpenViking, feiyu-memgate openviking backend (read-only store), default recall stages, precise recall profile, bge-m3 embeddings inside OpenViking, no LLM
- 调用：config.build_gateway(dict); OpenVikingBackend.put(text, ref=viking://~/memories/events/Y/M/D/<id>.md) per memory; OpenVikingBackend.find(query, 10) for ranked; Gateway.recall('owner', {'query': q}) for deployed; id = file name of the item's URI
- 注意：和 feiyu-memgate-openviking 完全一样，只是固定 [recall] profile = "precise"：min_score 0.55、比例 0.95（默认的宽松档是 0.55 / 0.90）。留出集上，默认档 → 精确档：交付命中 90.8% → 87.5%，精确率 75.9% → 80.2%，安静率 40.0% → 40.0%，每题交付 1.50 → 1.17 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Memories written with OpenVikingBackend.put (content/write, mode create) as memory files, no session or extraction, so no language model is used; llm_tokens is 0. Ingest went into its own OpenViking user.
- 备注：ingest_seconds includes the wait until a sample of 21 memories (and the last one written) found themselves (wait 69.4 s; writes alone 2.4 s, 0 put failure(s)).
- 备注：ranked: backend.find(query, 10) of the read-only store; directory summaries (.abstract.md, .overview.md) are skipped by the backend itself, so ranked can hold fewer than 10 items.
- 备注：deployed uses the default recall stages (min_score, relative_score, newer_first, fill_text, drop_oversize, redact, dedupe, budget) with [recall] profile = 'precise' pinned and the default limit of 5; the gateway asks the backend for 15 candidates; ms times that Gateway.recall call only. The backend maps OpenViking's raw score with its default floor 0.50 / ceil 1.00 (raw kept in Hit.meta); its precise lines are min_score 0.55 and relative ratio 0.95, its broad lines (the default profile) 0.55 and 0.90, so the min_score in use, 0.55, means raw 0.775.
- 备注：Backend and gateway timeouts are the defaults (backend socket timeout 2 s) except for ingest writes (30 s).
- 备注：Recall item ids: the memory id is the file name of the item's URI (id field); 0 item(s) unmapped.
- 备注：0 recall call(s) did not answer 200; 0 backend find call(s) for ranked failed.
- 备注：Gateway.recall was called directly, so the per-client rate limiter was not involved.

### mem0-verbatim（2.2.1）

- 模式：add(infer=False) verbatim, no LLM, bge-m3 1024-d embeddings, local on-disk Qdrant, mem0 hybrid search (semantic + BM25 + entity), default threshold/limit for deployed
- 调用：Memory.from_config(embedder=openai-compatible bge-m3, vector_store=qdrant path, llm=deepseek); Memory.add(text, user_id='bench', infer=False, metadata={'source_id': id, 'date': date}) per memory; Memory.search(q, filters={'user_id':'bench'}, top_k=10, threshold=0.0) for ranked; Memory.search(q, filters={'user_id':'bench'}) with mem0 defaults (top_k=20, threshold=0.1) for deployed
- 注意：add(infer=False) 逐字入库，不用 LLM；默认检索 top_k=20、阈值 0.1，实际交付 20 条。其 BM25 和实体部分依赖英文模型，对中文不起作用，排名基本是 bge-m3 余弦。
- 备注：mem0ai 2.2.1; extras: mem0ai[nlp] (spaCy en_core_web_sm for BM25 lemmatization and entity extraction, an English-only model applied to Chinese text) and fastembed Qdrant/bm25 sparse vectors, as the docs describe for hybrid search.
- 备注：Each memory was added as one user message; metadata source_id=<memory id> and date were attached. No source/kind fields were given to mem0.
- 备注：ranked: search top_k=10 threshold=0.0 (no threshold); deployed: search with mem0 defaults (top_k=20, threshold=0.1, no rerank); ms times the deployed call only. Items are mapped to source memories via the source_id metadata of the hit; one item per memory (best score kept).
- 备注：240 of 240 memories ingested; 0 add failure(s).
- 备注：0 search question(s) failed.
- 备注：infer=False path: no entity linking happens at write time, so the entity-boost part of mem0's scoring has no data; this is mem0's real behaviour for infer=False.

### memsearch（0.4.23）

- 模式：verbatim Markdown ingest (memory/YYYY-MM-DD.md), hybrid dense+BM25 on Milvus Lite, bge-m3 embeddings via OpenAI-compatible endpoint, no LLM, no reranker
- 调用：CLI `memsearch index <memory dir> --provider openai --model bge-m3 --base-url <embedder>/v1` for ingest; memsearch.core.MemSearch.search(query, top_k=10) for ranked; MemSearch.search(query, top_k=5) for deployed (the DSH memory-recall skill's `memsearch search --top-k 5`); chunk -> memory id via the '<!-- id:mNNN -->' anchor in chunk content
- 注意：Markdown 入库，稠密 + BM25 的 RRF 融合；没有阈值和字数预算，交付 = 前 5 条；插件本身不自动注入，是否检索由模型决定。
- 备注：Installed from the scanned source checkout (commit 369ae1da48b7, pyproject version 0.4.23) with pip inside the image build.
- 备注：Ingest: memories written as Markdown, memory/YYYY-MM-DD.md, one '### 00:NN' section per memory plus an '<!-- id:mNNN -->' anchor line; indexed with the CLI `memsearch index` (default chunking: 1500 chars, split on headings). Every chunk therefore holds exactly one memory; the id is read from the anchor in the returned chunk content. The HH:MM heading values are a per-day sequence, not real times. The memory text is verbatim; no source/kind fields used.
- 备注：ingest_seconds covers writing the Markdown files plus the `memsearch index` subprocess (python start-up, embedding via the shared bge-m3 endpoint, Milvus Lite insert).
- 备注：Embeddings: provider 'openai' with base_url http://fmg-bench-embedder:11434/v1, model bge-m3 (memsearch's documented OpenAI-compatible setting). Dimension auto-detected (1024).
- 备注：Search is memsearch's default hybrid retrieval (dense COSINE + BM25 fused by RRF k=60, score normalised to 0..1); no reranker (default off), no threshold exists in the library.
- 备注：ranked = MemSearch.search(query, top_k=10). deployed = MemSearch.search(query, top_k=5): the DSH plugin has no automatic injection (it only adds a reminder to use the memory-recall skill); that skill runs `memsearch search "<q>" --top-k 5 --json-output`, with no score threshold and no character budget. The CLI calls the same MemSearch.search; it was called in-process so `ms` excludes CLI process start-up (a few seconds of python/pymilvus import per CLI call in real use).
- 备注：In the real plugin the agent decides whether to search and writes its own query text; here the benchmark question is passed verbatim.
- 备注：Not used (default off / needs an LLM): compact/summarise, plugin turn capture, maintenance tasks, reranker, file watcher.
- 备注：0 chunk(s) could not be mapped to a memory id; 0 query failure(s).

### openviking（v0.5.3）

- 模式：direct memory-file writes (no extraction), bge-m3 embeddings via ollama provider, API defaults, no LLM; directory summaries dropped from results
- 调用：POST /api/v1/content/write per memory (mode create, URI viking://user/owner/memories/events/Y/M/D/<id>.md); POST /api/v1/search/find {query, limit 10, target_uri viking://~/memories} for ranked; the same call without limit and score_threshold for deployed; id = file name of the result URI
- 注意：直接写记忆文件，不走会话提取（真实部署会用模型提取，这里没测）；API 默认值，没有阈值，交付约 10 条；默认插件设置未能核实。
- 备注：Memories were written directly as memory files (content/write, mode create) under events/YYYY/MM/DD/<id>.md with the memory date in the path; OpenViking's session/commit extraction (which needs a language model) was not used, so llm_tokens is 0. A real deployment would extract facts with a model, which this does not measure.
- 备注：Embeddings: bge-m3 (1024) through the shared embedder; indexing is asynchronous, ingest_seconds includes the wait until a sample of 21 memories (and the last one written) found themselves (wait 69.2 s; writes alone 2.6 s, 0 write failure(s)).
- 备注：Scores are OpenViking's own search scores, not comparable across systems.
- 备注：deployed: the plugin's default limit/threshold is UNVERIFIED (its package is not available offline); the API defaults stand in for it: limit 10, no score threshold. So deployed equals ranked in size.
- 备注：The search over viking://~/memories also returns directory summaries (.abstract.md/.overview.md): 8 such item(s) among the ranked results of all questions.
- 备注：0 question(s) failed (empty lists).
- 备注：ms is one find call with the API defaults, measured inside the container, including the server's query embedding through the shared embedder.
- 备注：An OV background task may try to summarise directories with its language model; no key and no internet are available, so those attempts fail harmlessly and do not affect file search.
- 备注：This file drops results that are not memory files (id would be null).

### openviking-unfiltered（v0.5.3）

- 模式：same calls as openviking, directory summaries kept as id null items
- 调用：POST /api/v1/content/write per memory (mode create, URI viking://user/owner/memories/events/Y/M/D/<id>.md); POST /api/v1/search/find {query, limit 10, target_uri viking://~/memories} for ranked; the same call without limit and score_threshold for deployed; id = file name of the result URI
- 注意：实验变体：和 openviking 同样的调用，目录摘要保留为无编号条目（全部排名里只有 8 条），两行几乎一样。
- 这是实验变体，不是默认运行；图表里不画，表里保留。
- 备注：Memories were written directly as memory files (content/write, mode create) under events/YYYY/MM/DD/<id>.md with the memory date in the path; OpenViking's session/commit extraction (which needs a language model) was not used, so llm_tokens is 0. A real deployment would extract facts with a model, which this does not measure.
- 备注：Embeddings: bge-m3 (1024) through the shared embedder; indexing is asynchronous, ingest_seconds includes the wait until a sample of 21 memories (and the last one written) found themselves (wait 69.2 s; writes alone 2.6 s, 0 write failure(s)).
- 备注：Scores are OpenViking's own search scores, not comparable across systems.
- 备注：deployed: the plugin's default limit/threshold is UNVERIFIED (its package is not available offline); the API defaults stand in for it: limit 10, no score threshold. So deployed equals ranked in size.
- 备注：The search over viking://~/memories also returns directory summaries (.abstract.md/.overview.md): 8 such item(s) among the ranked results of all questions.
- 备注：0 question(s) failed (empty lists).
- 备注：ms is one find call with the API defaults, measured inside the container, including the server's query embedding through the shared embedder.
- 备注：An OV background task may try to summarise directories with its language model; no key and no internet are available, so those attempts fail harmlessly and do not affect file search.
- 备注：This file keeps non-memory results as id null (noise) in ranked and deployed.

### feiyu-memgate-hybrid（0.1.0）

- 模式：verbatim ingest, hybrid backend (sqlite keyword + embedding bge-m3, probabilistic-OR fusion), default recall stages (broad profile), no LLM
- 调用：config.build_gateway(dict); HybridBackend.put_many([(text, id, date)]) once for all memories (written to both children under the memory id); Gateway.recall('owner', {'query': q}) for deployed; HybridBackend.find(query, 10) for ranked
- 注意：关键词 + 向量，概率“或”融合，默认召回步骤（宽松档 broad：min_score 0.40、比例 0.65）；需要一个 embeddings 接口。
- 备注：Backend is the hybrid backend: a sqlite keyword child (IDF-weighted containment of character bigrams and CJK single characters) and an embedding child (bge-m3, cosine mapped with floor 0.40 and ceil 0.80); fused score = 1 - prod(1 - score_i) with both weights 1.0. Defaults chosen on the development sets, not on this data.
- 备注：ranked scores are the fused relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages, whose default profile is broad (min_score with the hybrid's broad line 0.40, relative_score with its ratio 0.65, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that Gateway.recall call only. The broad lines were chosen on the development set by the broad rule of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best delivered hit); the precise lines are 0.70 and 0.85 (the -precise run). The recall call asks both children, one after the other.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-hybrid-precise（0.1.0）

- 模式：verbatim ingest, hybrid backend (sqlite keyword + embedding bge-m3, probabilistic-OR fusion), default recall stages, no LLM, precise recall profile
- 调用：config.build_gateway(dict); HybridBackend.put_many([(text, id, date)]) once for all memories (written to both children under the memory id); Gateway.recall('owner', {'query': q}) for deployed; HybridBackend.find(query, 10) for ranked
- 注意：和 feiyu-memgate-hybrid 完全一样，只是固定 [recall] profile = "precise"：min_score 0.70、比例 0.85（默认的宽松档是 0.40 / 0.65）。留出集上，默认档 → 精确档：交付命中 92.5% → 65.0%，精确率 62.6% → 90.4%，安静率 35.0% → 75.0%，每题交付 2.01 → 0.66 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the hybrid backend: a sqlite keyword child (IDF-weighted containment of character bigrams and CJK single characters) and an embedding child (bge-m3, cosine mapped with floor 0.40 and ceil 0.80); fused score = 1 - prod(1 - score_i) with both weights 1.0. Defaults chosen on the development sets, not on this data.
- 备注：ranked scores are the fused relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with the hybrid's precise line 0.70, relative_score with its ratio 0.85, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall call only. The plain run of this backend uses the broad lines 0.40 and 0.65 instead. The recall call asks both children, one after the other.
- 备注：Precise profile: [recall] profile = 'precise': min_score 0.70 and ratio 0.85 (the hybrid's default_min_score and default_relative_ratio, the precise lines) instead of the broad 0.40 and 0.65 of the plain run.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-embedding-qwen3-embedding（0.1.0）

- 模式：verbatim ingest, embedding backend (qwen3-embedding:0.6b via an OpenAI-compatible endpoint, brute-force cosine), default recall stages (broad profile), no LLM
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：向量后端换成较轻的模型 qwen3-embedding 0.6B（639 MB，1024 维，多语言），该模型的预设（floor、ceil、分数线、前缀），默认召回步骤（宽松档 broad：min_score 0.15、比例 0.65）；需要一个 embeddings 接口。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: model qwen3-embedding:0.6b, cosine mapped to 0..1 with floor 0.35 and ceil 0.80 (the backend's values for this model, chosen on the development sets, not on this data); query prefix set, document prefix none.
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages, whose default profile is broad (min_score with the backend's broad line 0.15, relative_score with its ratio 0.65, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that Gateway.recall call only. The broad lines were chosen on the development set by the broad rule of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best delivered hit); the precise lines are 0.20 and 0.95 (the -precise run).
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-embedding-qwen3-embedding-precise（0.1.0）

- 模式：verbatim ingest, embedding backend (qwen3-embedding:0.6b via an OpenAI-compatible endpoint, brute-force cosine), default recall stages, no LLM, precise recall profile
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：和 feiyu-memgate-embedding-qwen3-embedding 完全一样，只是固定 [recall] profile = "precise"：min_score 0.20、比例 0.95（默认的宽松档是 0.15 / 0.65）。留出集上，默认档 → 精确档：交付命中 94.2% → 85.0%，精确率 70.7% → 81.9%，安静率 32.5% → 37.5%，每题交付 1.76 → 0.97 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: model qwen3-embedding:0.6b, cosine mapped to 0..1 with floor 0.35 and ceil 0.80 (the backend's values for this model, chosen on the development sets, not on this data); query prefix set, document prefix none.
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with the backend's precise line 0.20, relative_score with its ratio 0.95, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall call only. The plain run of this backend uses the broad lines 0.15 and 0.65 instead.
- 备注：Precise profile: [recall] profile = 'precise' makes min_score and relative_score use the precision-leaning lines of this backend (min_score 0.20, ratio 0.95 instead of the broad 0.15 and 0.65), chosen on the development set by the balanced rule of bench/dev_eval.py (delivered F1 weighted with the quiet rate). It answers fewer questions and stays quiet more often than the default.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-full（0.1.0）

- 模式：verbatim ingest with dates, hybrid backend (sqlite keyword + embedding bge-m3, probabilistic-OR fusion), default recall stages (broad profile) plus freshness, forgetting ledger with default settings, no sweep, no LLM
- 调用：config.build_gateway(dict with [forgetting]); HybridBackend.put_many([(text, id, date)]) once; Forgetting.sync('private', store); Gateway.recall('owner', {'query': q}) for deployed (stages: min_score, freshness, relative_score, fill_text, drop_oversize, redact, dedupe, budget); HybridBackend.find(query, 10) scored by the freshness stage's prepare() for ranked
- 注意：混合后端 + 默认召回步骤（宽松档 broad：min_score 0.40、比例 0.65）+ freshness（遗忘账本，默认设置，不做清理）；记忆带自己的日期。
- 备注：Backend is the hybrid backend: a sqlite keyword child (IDF-weighted containment of character bigrams and CJK single characters) and an embedding child (bge-m3, cosine mapped with floor 0.40 and ceil 0.80); fused score = 1 - prod(1 - score_i) with both weights 1.0. Defaults chosen on the development sets, not on this data.
- 备注：deployed uses the default recall stages, whose default profile is broad (min_score with the hybrid's broad line 0.40, relative_score with its ratio 0.65, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that Gateway.recall call only. The broad lines were chosen on the development set by the broad rule of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best delivered hit); the precise lines are 0.70 and 0.85 (the -precise run). The recall call asks both children, one after the other.
- 备注：Everything is turned on with documented defaults: stages min_score, freshness (floor 0.7), relative_score, fill_text, drop_oversize, redact, dedupe, budget; [forgetting] with the default Ebbinghaus curve, a temporary ledger and archive; no sweep was run and nothing was forgotten.
- 备注：Memories carry their own dates; Forgetting.sync made each ledger first_seen the memory's date. The forgetting clock was pinned to the day after the newest memory so the run does not depend on today's date.
- 备注：ranked is the backend's top 10 with freshness applied (prepare() only, no use recorded); no threshold.
- 备注：Uses recorded by each deployed recall were removed again and the ledger re-synchronised, so every question was asked of the same state.
- 备注：deployed uses the default limit of 5; ms times that Gateway.recall call only (two children, then freshness reads the ledger).
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-full-precise（0.1.0）

- 模式：verbatim ingest with dates, hybrid backend (sqlite keyword + embedding bge-m3, probabilistic-OR fusion), default recall stages plus freshness, forgetting ledger with default settings, no sweep, no LLM, precise recall profile
- 调用：config.build_gateway(dict with [forgetting]); HybridBackend.put_many([(text, id, date)]) once; Forgetting.sync('private', store); Gateway.recall('owner', {'query': q}) for deployed (stages: min_score, freshness, relative_score, fill_text, drop_oversize, redact, dedupe, budget); HybridBackend.find(query, 10) scored by the freshness stage's prepare() for ranked
- 注意：和 feiyu-memgate-full 完全一样，只是固定 [recall] profile = "precise"：min_score 0.70、比例 0.85（默认的宽松档是 0.40 / 0.65）。留出集上，默认档 → 精确档：交付命中 93.3% → 65.0%，精确率 60.1% → 90.2%，安静率 35.0% → 75.0%，每题交付 2.08 → 0.67 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the hybrid backend: a sqlite keyword child (IDF-weighted containment of character bigrams and CJK single characters) and an embedding child (bge-m3, cosine mapped with floor 0.40 and ceil 0.80); fused score = 1 - prod(1 - score_i) with both weights 1.0. Defaults chosen on the development sets, not on this data.
- 备注：deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with the hybrid's precise line 0.70, relative_score with its ratio 0.85, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall call only. The plain run of this backend uses the broad lines 0.40 and 0.65 instead. The recall call asks both children, one after the other.
- 备注：Everything is turned on with documented defaults: stages min_score, freshness (floor 0.7), relative_score, fill_text, drop_oversize, redact, dedupe, budget; [forgetting] with the default Ebbinghaus curve, a temporary ledger and archive; no sweep was run and nothing was forgotten.
- 备注：Memories carry their own dates; Forgetting.sync made each ledger first_seen the memory's date. The forgetting clock was pinned to the day after the newest memory so the run does not depend on today's date.
- 备注：ranked is the backend's top 10 with freshness applied (prepare() only, no use recorded); no threshold.
- 备注：Uses recorded by each deployed recall were removed again and the ledger re-synchronised, so every question was asked of the same state.
- 备注：deployed uses the default limit of 5; ms times that Gateway.recall call only (two children, then freshness reads the ledger).
- 备注：Precise profile: [recall] profile = 'precise': the hybrid's precise lines (0.70 / 0.85) instead of the broad 0.40 / 0.65 of the plain run; the freshness stage is the same.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### mem0（2.2.1）

- 模式：add(infer=True) with DeepSeek deepseek-flash fact extraction, bge-m3 1024-d embeddings, local on-disk Qdrant, mem0 hybrid search (semantic + BM25 + entity), default threshold/limit for deployed
- 调用：Memory.from_config(embedder=openai-compatible bge-m3, vector_store=qdrant path, llm=deepseek); Memory.add(text, user_id='bench', infer=True, metadata={'source_id': id, 'date': date}) per memory; Memory.search(q, filters={'user_id':'bench'}, top_k=10, threshold=0.0) for ranked; Memory.search(q, filters={'user_id':'bench'}) with mem0 defaults (top_k=20, threshold=0.1) for deployed
- 注意：add(infer=True)，由 LLM 提取事实后入库（约 206 万 tokens）；检索默认值同逐字版，实际交付约 20 条。
- 备注：mem0ai 2.2.1; extras: mem0ai[nlp] (spaCy en_core_web_sm for BM25 lemmatization and entity extraction, an English-only model applied to Chinese text) and fastembed Qdrant/bm25 sparse vectors, as the docs describe for hybrid search.
- 备注：Each memory was added as one user message; metadata source_id=<memory id> and date were attached. No source/kind fields were given to mem0.
- 备注：ranked: search top_k=10 threshold=0.0 (no threshold); deployed: search with mem0 defaults (top_k=20, threshold=0.1, no rerank); ms times the deployed call only. Items are mapped to source memories via the source_id metadata of the hit; one item per memory (best score kept).
- 备注：240 of 240 memories ingested; 0 add failure(s).
- 备注：0 search question(s) failed.
- 备注：Stored facts: 248 from 240 source memories; events {"ADD": 248}. Source memories with no surviving fact: 0. Facts without source_id: 0.
- 备注：mem0ai 2.x extraction is additive (ADD only, hash dedup); its add path has no update/delete step. LLM tokens counted from the API usage fields: prompt 2012313 (cached 1873792), completion 46873 over 240 call(s); llm_cost_cny is the DeepSeek balance difference (other workers may have spent in the same window).

### feiyu-memgate-embedding-bge-base-zh（0.1.0）

- 模式：verbatim ingest, embedding backend (hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0 via an OpenAI-compatible endpoint, brute-force cosine), default recall stages (broad profile), no LLM
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：向量后端换成较轻的模型 bge-base-zh-v1.5（Q8_0，110 MB，768 维，只支持中文），该模型的预设（floor、ceil、分数线、前缀），默认召回步骤（宽松档 broad：min_score 0.05、比例 0.50）；需要一个 embeddings 接口。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: model hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0, cosine mapped to 0..1 with floor 0.35 and ceil 0.80 (the backend's values for this model, chosen on the development sets, not on this data); query prefix set, document prefix none.
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages, whose default profile is broad (min_score with the backend's broad line 0.05, relative_score with its ratio 0.50, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that Gateway.recall call only. The broad lines were chosen on the development set by the broad rule of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best delivered hit); the precise lines are 0.25 and 0.75 (the -precise run).
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-embedding-bge-base-zh-precise（0.1.0）

- 模式：verbatim ingest, embedding backend (hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0 via an OpenAI-compatible endpoint, brute-force cosine), default recall stages, no LLM, precise recall profile
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：和 feiyu-memgate-embedding-bge-base-zh 完全一样，只是固定 [recall] profile = "precise"：min_score 0.25、比例 0.75（默认的宽松档是 0.05 / 0.50）。留出集上，默认档 → 精确档：交付命中 88.3% → 72.5%，精确率 70.2% → 86.2%，安静率 37.5% → 65.0%，每题交付 1.51 → 0.77 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: model hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0, cosine mapped to 0..1 with floor 0.35 and ceil 0.80 (the backend's values for this model, chosen on the development sets, not on this data); query prefix set, document prefix none.
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with the backend's precise line 0.25, relative_score with its ratio 0.75, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall call only. The plain run of this backend uses the broad lines 0.05 and 0.50 instead.
- 备注：Precise profile: [recall] profile = 'precise' makes min_score and relative_score use the precision-leaning lines of this backend (min_score 0.25, ratio 0.75 instead of the broad 0.05 and 0.50), chosen on the development set by the balanced rule of bench/dev_eval.py (delivered F1 weighted with the quiet rate). It answers fewer questions and stays quiet more often than the default.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-embedding-bge-small-zh（0.1.0）

- 模式：verbatim ingest, embedding backend (hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16 via an OpenAI-compatible endpoint, brute-force cosine), default recall stages (broad profile), no LLM
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：向量后端换成较轻的模型 bge-small-zh-v1.5（F16，47.9 MB，512 维，只支持中文），该模型的预设（floor、ceil、分数线、前缀），默认召回步骤（宽松档 broad：min_score 0.10、比例 0.65）；需要一个 embeddings 接口。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: model hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16, cosine mapped to 0..1 with floor 0.40 and ceil 0.80 (the backend's values for this model, chosen on the development sets, not on this data); query prefix none, document prefix none.
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages, whose default profile is broad (min_score with the backend's broad line 0.10, relative_score with its ratio 0.65, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that Gateway.recall call only. The broad lines were chosen on the development set by the broad rule of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best delivered hit); the precise lines are 0.30 and 0.90 (the -precise run).
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### feiyu-memgate-embedding-bge-small-zh-precise（0.1.0）

- 模式：verbatim ingest, embedding backend (hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16 via an OpenAI-compatible endpoint, brute-force cosine), default recall stages, no LLM, precise recall profile
- 调用：config.build_gateway(dict); EmbeddingBackend.put_many([(text, id, date)]) once for all memories; Gateway.recall('owner', {'query': q}) for deployed; EmbeddingBackend.find(query, 10) for ranked
- 注意：和 feiyu-memgate-embedding-bge-small-zh 完全一样，只是固定 [recall] profile = "precise"：min_score 0.30、比例 0.90（默认的宽松档是 0.10 / 0.65）。留出集上，默认档 → 精确档：交付命中 86.7% → 76.7%，精确率 69.0% → 79.4%，安静率 20.0% → 47.5%，每题交付 1.79 → 0.94 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the embedding backend: model hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16, cosine mapped to 0..1 with floor 0.40 and ceil 0.80 (the backend's values for this model, chosen on the development sets, not on this data); query prefix none, document prefix none.
- 备注：ranked scores are the mapped relevance values, not comparable across systems.
- 备注：deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with the backend's precise line 0.30, relative_score with its ratio 0.90, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall call only. The plain run of this backend uses the broad lines 0.10 and 0.65 instead.
- 备注：Precise profile: [recall] profile = 'precise' makes min_score and relative_score use the precision-leaning lines of this backend (min_score 0.30, ratio 0.90 instead of the broad 0.10 and 0.65), chosen on the development set by the balanced rule of bench/dev_eval.py (delivered F1 weighted with the quiet rate). It answers fewer questions and stays quiet more often than the default.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200; 0 ranked search(es) failed.
- 备注：deployed was timed before ranked, so ms includes the real query embedding request; ranked then reuses the backend's query-embedding cache.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Memories were ingested with one put_many call (embedding requests of 32 texts); ingest_seconds covers it.

### dsh-mnemon-embedding（dsh-mnemon 0.5.45 (commit 1dec529); Memory Spaces 0.5.27, Mnemon Native provider 0.5.11, Layered strategy 0.5.13; mnemon CLI 0.2.13）

- 模式：default backend Mnemon Native (mnemon CLI, SQLite graph) with its optional embedding mode switched on (config embedding.enabled, shared bge-m3 endpoint); verbatim exact writes through service.remember (no LLM review step); default quality policy strict-v1
- 调用：write: MemorySpacesService.remember({content}) -> MnemonNativeProvider.remember -> `mnemon remember <content> --cat general --imp 3 --source user`; ranked: MemorySpacesService.search({query, limit:10, inspect:true}) with recallQuality policy exhaustive-v1 -> MnemonNativeProvider.search -> `mnemon --readonly recall <q> --limit 40 --verbose` (+ service lexical recovery via `mnemon search` when no anchor evidence); deployed: createThreeTierTurn(view).query() recall route (Layered strategy retrieval.ts: limit 6, admission of <=4 results / 3600 chars / <=1 medium / <=1 unknown, low tier dropped) over MemorySpacesService.search with default policy strict-v1 (low 0.25, high 0.6, candidate pool 18, `mnemon recall --verbose` live, access recorded)
- 注意：同 dsh-mnemon，打开了可选的向量模式（bge-m3）；每次运行约 ±5 个百分点波动；LLM 复盘被绕过。
- 备注：Backend: the default Provider of dsh-mnemon is Mnemon Native, a thin driver over the separate `mnemon` CLI (npm @mnemon-dev/mnemon 0.2.13, Go binary, SQLite). dsh-mnemon itself holds no retrieval index for Memory Spaces; the CLI returns scored candidates and dsh-mnemon applies its quality policy and per-turn admission.
- 备注：Write path: the default product write path is an LLM-driven idle review / distillation that decides what to store. No LLM budget: memories were written verbatim through the explicit remember API (service.remember, defaults category=general, importance=3, source=user, no tags/entities). The review step was bypassed and is not measured. llm_tokens=0.
- 备注：Dates: `mnemon remember` has no date argument (created_at = now). Memories were written in date order within seconds; the memory dates are not represented in the store, so any recency/temporal signal of the CLI sees all memories as written at the same moment.
- 备注：Dedup/conflict detection of the CLI stayed on. Actions returned by remember: {"added":240}. Rows stored: 240 of 240.
- 备注：ranked uses a second service instance configured with the built-in policy exhaustive-v1 (no score threshold, no tier caps), limit 10, inspect=true (readonly snapshot). Items that carry score 0 are still listed in CLI order. The CLI itself fetches a pool of 40 for smart recall; with embeddings the driver re-ranks that pool by vector similarity.
- 备注：deployed = the model-facing first Recall call of one turn under the default Layered strategy. dsh-mnemon does NOT inject Memory Space recall automatically before a turn; the model has to call the recall tool (the resident runtime-memory source, which is separate hot memory and is not benchmarked, is the only thing always injected). A quiet question therefore only returns nothing if the model calls recall and the policy admits nothing; a model might also skip the call.
- 备注：deployed scores are 0 for every item: the strategy admission (boundedModelInsights) drops the score field before the model sees the result; order is the admitted order.
- 备注：deployed ms covers the whole strategy read (service.search incl. the `mnemon` process launches, any lexical-recovery call, admission), measured around that call only. Each launch is a new process (node launcher -> native binary).
- 备注：Ordering side effect: deployed runs a live (non-readonly) recall, which records access counts for returned candidates and may influence later questions, as in real use; ranked runs first, readonly, on the same state. Questions were run in id order.
- 备注：Host glue stubbed: View/ReadGrant objects, the Source recall-route evidence() wrapper (copied logic: limit, 16000-char budget, provenance incl. relevanceTier), `dsh-mnemon` core helper barrel (real input.ts / storage-lock.ts), cordis/Loader (not needed). Nothing of the CLI, the driver, the quality policy or the strategy admission is reimplemented.
- 备注：Write failures: 0. Question failures: 0. Returned items that could not be mapped to a memory id: 0. embed-missing fill result: {"memoryBodyId":"default","filled":0,"remaining":0,"model":"bge-m3"}.

### feiyu-memgate-sqlite（0.1.0）

- 模式：verbatim ingest, sqlite keyword backend (IDF-weighted grams), default recall stages (broad profile), no embeddings, no LLM
- 调用：config.build_gateway(dict); SQLiteBackend.put(text, ref, date) per memory; SQLiteBackend.find(query, 10) for ranked; Gateway.recall('owner', {'query': q}) for deployed
- 注意：关键词后端（字符二元组 + IDF），不需要任何服务；默认召回步骤（宽松档 broad：min_score 0.25、比例 0.70），分数线在开发集上选定。
- 备注：Backend is the sqlite keyword backend: IDF-weighted containment of character bigrams and CJK single characters (a gram's weight is its BM25 inverse document frequency in the store); it does not understand meaning, so paraphrase questions are expected to be weak.
- 备注：ranked scores are the backend's own weighted-containment values, not comparable across systems.
- 备注：deployed uses the default recall stages, whose default profile is broad (min_score with the sqlite backend's broad line 0.25, relative_score with its ratio 0.70, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; no profile is passed; ms times that Gateway.recall call only. The broad lines were chosen on the development set by the broad rule of bench/dev_eval.py (precision >= 50 % and at least half of the precise quiet rate, best delivered hit); the precise lines are 0.30 and 0.75 (the -precise run).
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.

### feiyu-memgate-sqlite-precise（0.1.0）

- 模式：verbatim ingest, sqlite keyword backend (IDF-weighted grams), default recall stages, precise recall profile, no embeddings, no LLM
- 调用：config.build_gateway(dict); SQLiteBackend.put(text, ref, date) per memory; SQLiteBackend.find(query, 10) for ranked; Gateway.recall('owner', {'query': q}) for deployed
- 注意：和 feiyu-memgate-sqlite 完全一样，只是固定 [recall] profile = "precise"：min_score 0.30、比例 0.75（默认的宽松档是 0.25 / 0.70）。留出集上，默认档 → 精确档：交付命中 46.7% → 39.2%，精确率 89.3% → 93.0%，安静率 70.0% → 75.0%，每题交付 0.55 → 0.41 条；交得少而准，对没有答案的问题更常保持安静，漏答更多。
- 这是同一个后端的另一档配置（别的模型或精确档），不在五张基准图里画，表里保留。
- 备注：Backend is the sqlite keyword backend: IDF-weighted containment of character bigrams and CJK single characters (a gram's weight is its BM25 inverse document frequency in the store); it does not understand meaning, so paraphrase questions are expected to be weak.
- 备注：ranked scores are the backend's own weighted-containment values, not comparable across systems.
- 备注：deployed uses the default recall stages with [recall] profile = 'precise' pinned (min_score with the sqlite backend's precise line 0.30, relative_score with its ratio 0.75, fill_text, drop_oversize, redact, dedupe, budget) and the default limit of 5; ms times that Gateway.recall call only. The plain run of this backend uses the broad lines 0.25 and 0.70 instead.
- 备注：Deployed items are reported by the id field of each recall item; 0 item(s) named no dataset memory.
- 备注：0 recall call(s) did not answer 200.
- 备注：Gateway.recall was called directly, so the per-client rate limiter (applied only in admit) was not involved.
- 备注：Precise profile: [recall] profile = 'precise': min_score 0.30 and relative_score ratio 0.75 (the backend's default_min_score and default_relative_ratio, the precise lines) instead of the broad 0.25 and 0.70, chosen on the development set by the balanced rule of bench/dev_eval.py (delivered F1 weighted with the quiet rate). It answers fewer questions and stays quiet more often than the default.

### lingshu-dsh-memory（0.10.1）

- 模式：verbatim user messages through the plugin hook (regex redaction on, forgetting gate on), md_cg lexical/BM25 retrieval, no embeddings, no LLM; deployed = default automatic injection
- 调用：resolveToken + new MdcgClient(...) (MCP stdio to python -m md_cg.mcp_server); ingest: installMemoryHooks session/event handler (user/message) -> desensitize -> MdcgClient.remember -> mdcg_remember(gated=true); ranked: MdcgClient.read(query,{k:10}) = cg(op=read); deployed: installMemoryHooks system-prompt/assemble handler (lingshu:auto-recall timeline block + lingshu:session-window block)
- 注意：按设计每轮自动注入的是会话里最新的 4 条，不看问题，所以“交付命中”为 0 是设计使然，不是检索失败；排名用的是它的 BM25 读取。
- 备注：Ingest pacing: the plugin's default write cap is 8 writes per 60 s per layer/role (excess is DEFERred, not stored; env MDCG_WRITELIMIT=0 would disable it). The cap was left at its default and writes were paced 7600 ms apart; ingest_seconds is the time spent in the write calls only (4.1 s); total wall clock including the pacing sleeps was 1829 s.
- 备注：Ingest: every memory was sent as one user/message event through the plugin's own session/event hook (installMemoryHooks), which redacts with its regex list, then calls MdcgClient.remember -> MCP mdcg_remember(gated=true, layer=contextual, dedupe=true). It also fires a read (k=3) and a short-window write per message, as in production. Memory date is not passed: the hook path has no date field (write time is used).
- 备注：Write gate verdicts over the 240 memories (the hook lets the forgetting gate decide ACCEPT/MERGE/DROP/DEFER): {"ACCEPT":240}. First receipt shape: {"ok":true,"x":{"verdict":"ACCEPT","node_id":"mem_1794669517228_dc9c2e","gate":{"verdict":"ACCEPT","reason":"重要度 0.60≥0.3","redundancy":{"max":0,"with":null,"jaccard":0,"compared":0},"importance":{"score":0.6,"from":"hint"},"entropy":{"source_kind":"external_surprising","novelty":1,"self_information_bits":4,"normalized":1,"duplicate_with":null,"duplicate_ratio":0,"compared":0,"overwrite_of":null,"overwrite_ratio":null},"dedup_skipped":null},"written":"mem_1794669517228_dc9c2e","consistency":{"t":1794669517.2310362,"layer":"contextual","verdict":"BLINDSPOT","reason":"既有节点均未声明条件：无法建立比对路径","confl
- 备注：Memories changed by the write-path redaction (desensitize, default on): 0 [].
- 备注：ranked = MdcgClient.read(query,{k:10}) = MCP cg(op=read, query, k=10) (BM25 path of search_rrf, no threshold). cg/stg is the only tool surface exposed by default (tools: "core").
- 备注：deployed = what the per-turn system-prompt/assemble hook injects with default settings: the "lingshu:auto-recall" block (timeline of the 4 newest non-identity nodes of the session, 1400-char cap, decayed per-item 160/144/130/117 chars) PLUS the "lingshu:session-window" block (last 10 messages of the session, 900-char cap). Neither block uses the question: the hook never passes the query to retrieval. Items from both blocks are mapped to memory ids by text and merged (timeline first).
- 备注：deployed item counts per question: min 4 max 4; distinct deployed lists across 160 questions: 1. Block names seen in total: {"lingshu:auto-recall":161,"lingshu:session-window":161}.
- 备注：All 240 memories were placed in ONE session (the benchmark has no sessions); the injection is session-scoped, so this is the best case for it. A fresh hook instance was used per question so the per-session "max 2 injections per item" cap (issue #739) starts clean.
- 备注：Both injected blocks are wrapped in the plugin's own header and footer lines; those wrapper lines are skipped when mapping.
- 备注：Unmapped items (id null): ranked 0, deployed 0. Failed calls: 0.
- 备注：Timings are for the assemble handler call only (it includes two round trips to the Python brain: timeline and session recall).

### seekmemory（NeverENG/SeekMemory commit 794ec5d）

- 模式：verbatim ingest as level=fact memory artifacts via the kernel's persist(), no extraction, no embeddings (SeekMemory has no vector search), no LLM; deployed = recall.pipeline.query_block with default config (recall_k 8, budget 600 chars, evidence gate, disclosure)
- 调用：store.open_store(':memory:'); store.sources.append_sources([Source]); maintain.link.persist(make('memory', ...), artifacts, cache, graph); deployed: recall.pipeline.query_block(cache, graph, artifacts, sources, umo, text, private=True, conf=DEFAULTS, owner_umo, speaker, hybrid_retriever=HybridRetriever) -> info['final_ids']; ranked: HybridRetriever.retrieve + recall.score.select(k=10, gate=False, explore=0)
- 注意：BM25 + 图激活，没有向量检索；用真实容器日期，新鲜度项对所有记忆接近 0。
- 备注：Clock: real container date 2026-11-14 (not injected); freshness term exp(-age_days/14) is ~0 for all memories (ages 25 to 680 days).
- 备注：Write path: Source row (memory text, owner, one private session) + maintain.link.persist (commit + cache.put + link_new graph edges). Extraction is skipped because it needs an LLM; the artifact text is the memory text verbatim. No tags, no emotion values (defaults), shape=point, about=other, scope=private, said_by/subject_id=owner. The dataset fields source and kind were not given to the system.
- 备注：level=fact was chosen before the first run: 'fact' is the level for plain statements; maintain.decay.decay_plan never forgets level=fact (maintain/expiry_policy.py:49-50).
- 备注：Not applied because they need an extraction model: tags, arousal/valence, 'supersedes' (the only mechanism that retires an older memory when a newer one contradicts it). Updated questions therefore rely on ranking alone.
- 备注：ids: the artifact id is the memory id; deployed ids come from info['final_ids'] (items that reached the assembled block, after the 600-char budget). Scores are rank-derived (1/rank) because select() and query_block return no scores; they carry no meaning across systems.
- 备注：ranked: pool and scope-prior weighting replicate query_block; select(k=10, gate=False, explore=0). It bypasses the intent switch (intent 'none' for greetings/clock/operate words) and disclosure so it measures retrieval only. deployed keeps both.
- 备注：deployed uses defaults including explore_rate 0.1 (seeded by the query text, may swap the last pick for a random lower-ranked one) and the recent-context fallback for profile/topic questions.
- 备注：A persistent HybridRetriever is used (as in the host adapters); BM25 index builds on the warm-up query.
- 备注：0 question(s) with an error; 13 deployed call(s) returned an empty block.

### seekmemory-aged（NeverENG/SeekMemory commit 794ec5d）

- 模式：verbatim ingest as level=fact memory artifacts via the kernel's persist(), no extraction, no embeddings (SeekMemory has no vector search), no LLM; deployed = recall.pipeline.query_block with default config (recall_k 8, budget 600 chars, evidence gate, disclosure); AGED: after ingest, clock advanced 365 days with zero recalls, maintain.decay.decay_all swept (0 memories forgotten), then the questions were answered
- 调用：store.open_store(':memory:'); store.sources.append_sources([Source]); maintain.link.persist(make('memory', ...), artifacts, cache, graph); maintain.decay.decay_all(artifacts, cache, today); deployed: recall.pipeline.query_block(cache, graph, artifacts, sources, umo, text, private=True, conf=DEFAULTS, owner_umo, speaker, hybrid_retriever=HybridRetriever) -> info['final_ids']; ranked: HybridRetriever.retrieve + recall.score.select(k=10, gate=False, explore=0)
- 注意：实验变体：时钟拨快 365 天后跑一次衰减；没有一条被标记为遗忘（fact 级记忆不会被遗忘）。
- 这是实验变体，不是默认运行；图表里不画，表里保留。
- 备注：decay_all(today=2027-11-14) marked 0 of 240 memories state=forgotten (reversible revision, row kept); no recall happened between ingest and the sweep.
- 备注：Clock for the aged run: container date 2026-11-14 + 365 days = 2027-11-14; decay_all takes it as a parameter, recall.score/temporal/assemble `date` rebound for the run.
- 备注：Write path: Source row (memory text, owner, one private session) + maintain.link.persist (commit + cache.put + link_new graph edges). Extraction is skipped because it needs an LLM; the artifact text is the memory text verbatim. No tags, no emotion values (defaults), shape=point, about=other, scope=private, said_by/subject_id=owner. The dataset fields source and kind were not given to the system.
- 备注：level=fact was chosen before the first run: 'fact' is the level for plain statements; maintain.decay.decay_plan never forgets level=fact (maintain/expiry_policy.py:49-50).
- 备注：Not applied because they need an extraction model: tags, arousal/valence, 'supersedes' (the only mechanism that retires an older memory when a newer one contradicts it). Updated questions therefore rely on ranking alone.
- 备注：ids: the artifact id is the memory id; deployed ids come from info['final_ids'] (items that reached the assembled block, after the 600-char budget). Scores are rank-derived (1/rank) because select() and query_block return no scores; they carry no meaning across systems.
- 备注：ranked: pool and scope-prior weighting replicate query_block; select(k=10, gate=False, explore=0). It bypasses the intent switch (intent 'none' for greetings/clock/operate words) and disclosure so it measures retrieval only. deployed keeps both.
- 备注：deployed uses defaults including explore_rate 0.1 (seeded by the query text, may swap the last pick for a random lower-ranked one) and the recent-context fallback for profile/topic questions.
- 备注：A persistent HybridRetriever is used (as in the host adapters); BM25 index builds on the warm-up query.
- 备注：0 question(s) with an error; 13 deployed call(s) returned an empty block.

### meow-memory（0.32.6）

- 模式：verbatim ingest through memory_remember (level fact, global scope, importance 1), keywords derived by the plugin's tokenizer instead of an LLM, no LLM, no embeddings; default zh prompt language (CJK character bigrams)
- 调用：memory_remember.execute() per memory; ranked = memory_search.execute({query, k:10, explain:true}) (in-memory BM25 k1=1.5 b=0.75 over title+content+keywords with importance/recency weighting, ordered by bm25*recencyWeight); deployed = buildHitInjection(db, ws, sid, query, {}) = keywordHitScore over keywords of fact/lesson/rules/topic rows, hitTopK=2
- 注意：BM25 关键词检索；关键词由插件自带分词器生成（真实使用由 LLM 写关键词）；每条消息只按关键词命中注入前 2 条。
- 备注：Package: meow-memory 0.32.6, the plugin's published lib/index.js imported unmodified; host peers stubbed: @deepseek-ai/dsh-llm (message constructors only, never used on this path); @deepseek-ai/schemastery installed from npm if available in the image build (see README).
- 备注：memory_remember requires keywords (the LLM agent normally writes them). No LLM was used; keywords = all unique tokens of the memory text from the plugin's own exported tokenize(). With LLM-chosen keywords the deployed (keyword-hit) path would behave differently, probably with fewer but more curated keywords.
- 备注：Tool has no date argument: Date.now() was set to each memory's date (12:00 UTC + index seconds) during ingest, and to the day after the last memory (2026-10-21) for all questions. Chosen before any run. Recency weighting (importance < 3 only, skipped for results scoring >= 85% of the best) therefore applies as it would to genuinely old memories.
- 备注：All memories: level fact, project = global marker (so hit injection, which needs global scope or the session's current project, can see them), importance 1 (the tool's schema default), no title.
- 备注：deployed = per-message keyword-hit injection (buildHitInjection, default hitTopK 2, no score threshold). The first-turn snapshot injects only soul/user levels and global rules with importance >= 2, which are not used here, so it returns no fact memories. deployed scores are rank-derived (2,1) because the function returns ids without scores. ms times buildHitInjection only (it reads rows from SQLite and tokenizes every candidate per call).
- 备注：ranked = memory_search tool with k=10 and a fresh session per question (blind top-5 plus 5 unseen). The tool returns hits re-sorted by updated_at; the adapter restores retrieval order from explain.bm25 * explain.recencyWeight.
- 备注：Usage stats (hit_count, last_accessed_at) are reset by direct SQL after each call so all questions see the same state; this only undoes the side effect of the lookup and does not influence ranking logic.
- 备注：Dedup: memory_remember merges a new memory into an existing fact row when token Jaccard >= 0.8; merges: 0. A merged row maps to the newest memory id. Rows stored: 240 of 240 memories.
- 备注：Write failures: 0. Question failures: 0. Returned items that could not be mapped to a memory id: 0.

### dsh-mnemon（dsh-mnemon 0.5.45 (commit 1dec529); Memory Spaces 0.5.27, Mnemon Native provider 0.5.11, Layered strategy 0.5.13; mnemon CLI 0.2.13）

- 模式：default backend Mnemon Native (mnemon CLI, SQLite graph, keyword + graph signals, no embeddings: dsh-mnemon default config); verbatim exact writes through service.remember (no LLM review step); default quality policy strict-v1
- 调用：write: MemorySpacesService.remember({content}) -> MnemonNativeProvider.remember -> `mnemon remember <content> --cat general --imp 3 --source user`; ranked: MemorySpacesService.search({query, limit:10, inspect:true}) with recallQuality policy exhaustive-v1 -> MnemonNativeProvider.search -> `mnemon --readonly recall <q> --limit 40 --verbose` (+ service lexical recovery via `mnemon search` when no anchor evidence); deployed: createThreeTierTurn(view).query() recall route (Layered strategy retrieval.ts: limit 6, admission of <=4 results / 3600 chars / <=1 medium / <=1 unknown, low tier dropped) over MemorySpacesService.search with default policy strict-v1 (low 0.25, high 0.6, candidate pool 18, `mnemon recall --verbose` live, access recorded)
- 注意：默认后端 Mnemon Native（SQLite，关键词 + 实体 + 图信号打分，无向量）；每次运行结果约 ±5 个百分点波动；默认写入走 LLM 复盘，这里绕过了（逐字写入），没有测；记忆日期不进存储；插件不自动注入，由模型调用召回。
- 备注：Backend: the default Provider of dsh-mnemon is Mnemon Native, a thin driver over the separate `mnemon` CLI (npm @mnemon-dev/mnemon 0.2.13, Go binary, SQLite). dsh-mnemon itself holds no retrieval index for Memory Spaces; the CLI returns scored candidates and dsh-mnemon applies its quality policy and per-turn admission.
- 备注：Write path: the default product write path is an LLM-driven idle review / distillation that decides what to store. No LLM budget: memories were written verbatim through the explicit remember API (service.remember, defaults category=general, importance=3, source=user, no tags/entities). The review step was bypassed and is not measured. llm_tokens=0.
- 备注：Dates: `mnemon remember` has no date argument (created_at = now). Memories were written in date order within seconds; the memory dates are not represented in the store, so any recency/temporal signal of the CLI sees all memories as written at the same moment.
- 备注：Dedup/conflict detection of the CLI stayed on. Actions returned by remember: {"added":240}. Rows stored: 240 of 240.
- 备注：ranked uses a second service instance configured with the built-in policy exhaustive-v1 (no score threshold, no tier caps), limit 10, inspect=true (readonly snapshot). Items that carry score 0 are still listed in CLI order. The CLI itself fetches a pool of 40 for smart recall; with embeddings the driver re-ranks that pool by vector similarity.
- 备注：deployed = the model-facing first Recall call of one turn under the default Layered strategy. dsh-mnemon does NOT inject Memory Space recall automatically before a turn; the model has to call the recall tool (the resident runtime-memory source, which is separate hot memory and is not benchmarked, is the only thing always injected). A quiet question therefore only returns nothing if the model calls recall and the policy admits nothing; a model might also skip the call.
- 备注：deployed scores are 0 for every item: the strategy admission (boundedModelInsights) drops the score field before the model sees the result; order is the admitted order.
- 备注：deployed ms covers the whole strategy read (service.search incl. the `mnemon` process launches, any lexical-recovery call, admission), measured around that call only. Each launch is a new process (node launcher -> native binary).
- 备注：Ordering side effect: deployed runs a live (non-readonly) recall, which records access counts for returned candidates and may influence later questions, as in real use; ranked runs first, readonly, on the same state. Questions were run in id order.
- 备注：Host glue stubbed: View/ReadGrant objects, the Source recall-route evidence() wrapper (copied logic: limit, 16000-char budget, provenance incl. relevanceTier), `dsh-mnemon` core helper barrel (real input.ts / storage-lock.ts), cordis/Loader (not needed). Nothing of the CLI, the driver, the quality policy or the strategy admission is reimplemented.
- 备注：Write failures: 0. Question failures: 0. Returned items that could not be mapped to a memory id: 0.

### graphiti（graphiti-core 0.31.2）

- 模式：LLM extraction (deepseek-flash, JSON mode) of entities and temporal facts into embedded Kuzu; bge-m3 embeddings; 240 of 240 memories ingested
- 调用：Graphiti(graph_driver=KuzuDriver, llm_client=OpenAIGenericClient(json_object), embedder=OpenAIEmbedder(bge-m3)).add_episode(name=id, episode_body=text, reference_time=date, default group (no group_id)) per memory; Graphiti.search(query) (EDGE_HYBRID_SEARCH_RRF, limit 10) for ranked and deployed
- 注意：非推荐的技术栈：嵌入式 Kuzu（上游已标记弃用）+ 一个小 LLM（deepseek-flash）做提取，中文单句记忆；总共约 118 万 tokens；返回的是提取出的事实边，不是原文。
- 备注：INGESTED 240 of 240 memories as episodes (name = memory id); failed episodes: 0; not ingested: 0. If this is below 240 it is a SUBSET: the 127 memories that are expect/stale for some question were taken first, the remaining ones in date order while the 5 CNY budget lasted. The scorer still scores all 160 questions, so questions whose memories were not ingested cannot be answered.
- 备注：Failed episode ids: none
- 备注：ranked and deployed are the SAME call: Graphiti.search(query) = recipe EDGE_HYBRID_SEARCH_RRF (BM25 + cosine over facts, RRF), default limit 10. The recipe applies its own default cosine floor (sim_min_score 0.6, search_utils.DEFAULT_MIN_SCORE) and has no token budget, so deployed = ranked = its default result and ms times that call (1 embedding request + Kuzu queries, no LLM).
- 备注：Returned items are fact edges. Mapping: an edge maps to the memory of the earliest episode in edge.episodes (by memory date); several edges from one memory keep the best position; unmappable -> null. search() returns no scores, so score = 1/rank (order only).
- 备注：Invalidated facts: of 58 edges returned over all questions, 1 had invalid_at/expired_at set (search() does not filter superseded edges).
- 备注：Graph at the end: {"entity_edges": 69, "edges_with_invalid_at": 1, "entity_nodes": 125, "episodes": 240}
- 备注：LLM: deepseek-flash via OpenAIGenericClient(structured_output_mode='json_object', max_tokens=8192); embedder bge-m3 1024d; cross-encoder = pass-through stub (Graphiti default needs an OpenAI model; unused by search()). Kuzu embedded driver (deprecated upstream, still shipped).
- 备注：LLM calls: 596 (0 errored), prompt tokens 1028457 (cache hit 151040), completion tokens 148062. llm_cost_cny is the DeepSeek balance difference incl. 0.12 CNY of earlier debugging runs (2.0; other workers may have spent concurrently); token-based estimate at assumed prices (2.0 in / 8.0 out CNY per M): 3.2414.
- 备注：Budget stop reached: False. Query failures: 0. ingest_seconds sums add_episode wall-clock over 240 episodes (the graph grows during ingest).
- 备注：Every episode is text-typed (EpisodeType.text), default group (no group_id: KuzuDriver in 0.31.2 lacks the _database attribute that an explicit group_id needs), reference_time = memory date 00:00 UTC; only text, date and id were given to the system.

### seekmemory-aged-detail（NeverENG/SeekMemory commit 794ec5d）

- 模式：verbatim ingest as level=detail memory artifacts via the kernel's persist(), no extraction, no embeddings (SeekMemory has no vector search), no LLM; deployed = recall.pipeline.query_block with default config (recall_k 8, budget 600 chars, evidence gate, disclosure); AGED: after ingest, clock advanced 365 days with zero recalls, maintain.decay.decay_all swept (240 memories forgotten), then the questions were answered
- 调用：store.open_store(':memory:'); store.sources.append_sources([Source]); maintain.link.persist(make('memory', ...), artifacts, cache, graph); maintain.decay.decay_all(artifacts, cache, today); deployed: recall.pipeline.query_block(cache, graph, artifacts, sources, umo, text, private=True, conf=DEFAULTS, owner_umo, speaker, hybrid_retriever=HybridRetriever) -> info['final_ids']; ranked: HybridRetriever.retrieve + recall.score.select(k=10, gate=False, explore=0)
- 注意：365 天老化实验，不是默认运行：记忆按 detail 级写入，老化后 240 条全部被标记为遗忘，什么都召回不到（Hit@5 0%，安静率 100%）。
- 这是实验变体，不是默认运行；图表里不画，表里保留。
- 备注：decay_all(today=2027-11-14) marked 240 of 240 memories state=forgotten (reversible revision, row kept); no recall happened between ingest and the sweep.
- 备注：Clock for the aged run: container date 2026-11-14 + 365 days = 2027-11-14; decay_all takes it as a parameter, recall.score/temporal/assemble `date` rebound for the run.
- 备注：Write path: Source row (memory text, owner, one private session) + maintain.link.persist (commit + cache.put + link_new graph edges). Extraction is skipped because it needs an LLM; the artifact text is the memory text verbatim. No tags, no emotion values (defaults), shape=point, about=other, scope=private, said_by/subject_id=owner. The dataset fields source and kind were not given to the system.
- 备注：level=detail was chosen before the first run: 'fact' is the level for plain statements; maintain.decay.decay_plan never forgets level=fact (maintain/expiry_policy.py:49-50).
- 备注：Not applied because they need an extraction model: tags, arousal/valence, 'supersedes' (the only mechanism that retires an older memory when a newer one contradicts it). Updated questions therefore rely on ranking alone.
- 备注：ids: the artifact id is the memory id; deployed ids come from info['final_ids'] (items that reached the assembled block, after the 600-char budget). Scores are rank-derived (1/rank) because select() and query_block return no scores; they carry no meaning across systems.
- 备注：ranked: pool and scope-prior weighting replicate query_block; select(k=10, gate=False, explore=0). It bypasses the intent switch (intent 'none' for greetings/clock/operate words) and disclosure so it measures retrieval only. deployed keeps both.
- 备注：deployed uses defaults including explore_rate 0.1 (seeded by the query text, may swap the last pick for a random lower-ranked one) and the recent-context fallback for profile/topic questions.
- 备注：A persistent HybridRetriever is used (as in the host adapters); BM25 index builds on the warm-up query.
- 备注：0 question(s) with an error; 160 deployed call(s) returned an empty block.
