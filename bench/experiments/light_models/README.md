# 更轻的向量模型：每一档的代价和收益

只用开发集：`bench/dev`（160 条记忆、110 个问题：lexical 30、paraphrase 38、updated 12、quiet 30）和第二份开发集
`examples/eval`（46 条记忆、30 个问题）。**没有碰留出集 `bench/data`。** 下面的数字是选默认值的依据，不是系统表现。
所有测量都经过真实的 `EmbeddingBackend`（SQLite 文件、`put_many`、真实的 embeddings 接口），再用 `bench/dev_eval.py`
的网格、真实的召回阶段和 `balanced` 目标离线重放。

## 模型和下载

| 档 | 模型名（接口里的名字） | 来源 | 量化 | 文件大小 | 维度 | 语言 |
|---|---|---|---|---|---|---|
| 顶 | `bge-m3` | 原有（`fmg-bench-embedder`） | Ollama 默认 | 1157671200 B（约 1.16 GB） | 1024 | 多语言 |
| 中上 | `qwen3-embedding:0.6b` | Ollama 官方库 | Q8_0 | 639150592 B（639 MB） | 1024 | 多语言 |
| 中 | `embeddinggemma` | Ollama 官方库 | bf16（`latest` 标签） | 621867104 B（622 MB） | 768 | 多语言 |
| 轻 | `hf.co/CompendiumLabs/bge-base-zh-v1.5-gguf:Q8_0` | CompendiumLabs 对 BAAI 模型的 GGUF 转换 | Q8_0 | 110001568 B（110 MB） | 768 | 只有中文 |
| 最轻 | `hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16` | 同上 | F16 | 47886240 B（47.9 MB） | 512 | 只有中文 |

**和任务描述不一致的一处**：bge-base-zh 的 F16 文件（204053600 B，页面上的真实大小，估计 200 MB 是对的）下载完整、校验和一致，但
这个 Ollama 版本（0.40.15）拒绝加载（`failed to read key-value pairs`，请求返回 500）。读文件头发现原因是 F16 和 F32 两个文件里
`tokenizer.ggml.bos_token_id`、`eos_token_id` 各写了两次，Q8_0 和 Q4_K_M 没有这个问题。所以 bge-base-zh 用同一个仓库里精度
最高的、能加载的 Q8_0（110 MB，另外下载了这一个文件），F16 的标签已从卷里删掉。别的没有换来源。
模型是否在 Ollama 的模板里加了前缀：五个模型的 `TEMPLATE` 都是 `{{ .Prompt }}`，接口自己不加任何前缀。

准备和服务的命令在 `setup.sh`（拉取用默认网桥上的临时容器，服务容器 `fmg-bench-embedder-light` 只接内部网络 `fmg-bench`，
卷 `fmg-bench-models-light` 只读挂载，CPU，`OLLAMA_MAX_LOADED_MODELS=1`）。四个模型都在 `/v1/embeddings` 上验证过维度。

## 前缀：没有前缀 vs 各模型说明里推荐的前缀

推荐的写法（出处在 `models.py`）：bge v1.5 中文模型的查询指令 `为这个句子生成表示以用于检索相关文章：`（文档不加；说明里写 v1.5 不加也行）；
embeddinggemma 查询 `task: search result | query: `、文档 `title: none | text: `；qwen3-embedding 查询
`Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:`、文档不加；bge-m3 说明里不要求指令，不比较。
规则（事先定）：按排名的 MRR@10，再按 Hit@5（不设分数线，只看余弦排序），取好的；平手用没有前缀的。

| 模型 | 方案 | MRR@10 | Hit@1 | Hit@5 | 留用 |
|---|---|---|---|---|---|
| bge-small-zh | 无前缀 | 0.815 | 73.8 | 91.2 | 是 |
| bge-small-zh | 推荐前缀 | 0.805 | 73.8 | 90.0 | |
| bge-base-zh | 无前缀 | 0.848 | 77.5 | 95.0 | |
| bge-base-zh | 推荐前缀 | 0.874 | 81.2 | 97.5 | 是 |
| embeddinggemma | 无前缀 | 0.877 | 78.8 | 98.8 | 是 |
| embeddinggemma | 推荐前缀 | 0.852 | 75.0 | 98.8 | |
| qwen3-embedding | 无前缀 | 0.813 | 72.5 | 95.0 | |
| qwen3-embedding | 推荐前缀 | 0.821 | 73.8 | 93.8 | 是 |

qwen3-embedding 是按规则留了前缀，但优势只有 0.008 个 MRR（Hit@5 反而少 1 题），而这条指令约 30 个词元，取一次查询向量从 27 ms
变成 74 ms（p50）。embeddinggemma 官方的两个前缀在这份中文短句数据上更差。
后端新增选项 `query_prefix` 和 `document_prefix`（见 `docs/backends.md`），预设表里留用的方案就是上表“是”的那几行。

## 分数校准：S（静态预设）对 Q（查询自适应，下面记为 Z）

**静态配方 S**（只看库本身，不需要问题；在只看过 bge-m3 之后定下，一次调整，之后没有再动）：
`floor` = 库里所有记忆两两余弦的平均值减 0.05，取整到 0.05；`ceil` = `floor` + 剩余区间（1 - `floor`）的 2/3，取整到 0.05。
起初的想法是 `floor` = 平均值本身取整，bge-m3 的平均值是 0.455，得 0.45，而已知好用的是 0.40，所以加了 0.05 的余量，并把
`ceil` 的比例定为 2/3（0.40 + 0.6 x 2/3 = 0.80）。别的模型是在这条配方定下之后才看的：

| 模型 | 留用方案 | 两两余弦平均 | floor | ceil | 最优点（min_score / 比例） |
|---|---|---|---|---|---|
| bge-m3 | 无前缀 | 0.455 | 0.40 | 0.80 | 0.50 / 0.85 |
| bge-small-zh | 无前缀 | 0.431 | 0.40 | 0.80 | 0.30 / 0.90 |
| bge-base-zh | 推荐前缀 | 0.408 | 0.35 | 0.80 | 0.25 / 0.75 |
| embeddinggemma | 无前缀 | 0.347 | 0.30 | 0.75 | 0.55 / 0.85 |
| qwen3-embedding | 推荐前缀 | 0.400 | 0.35 | 0.80 | 0.20 / 0.95 |

不改的话（继续用 bge-m3 的 0.40 / 0.80 / 0.50 / 0.85）这些模型会怎样（同一份余弦，`bench/results/dev/embedding-<模型>.md` 里的 before 行）：
`balanced` bge-small-zh 63.2（预设 72.4）、bge-base-zh 56.6（75.9，交付命中只有 27.5%）、embeddinggemma 72.9（77.8）、
qwen3-embedding 61.9（66.9）。这就是旧默认值在别的模型上“悄悄失效”的样子。

**查询自适应 Z**：搜索本来就是暴力扫描，每个问题和库里每条记忆的余弦都已经算出来了。对一个问题，取这些余弦的平均值 m 和标准差 s
（总体标准差），记忆的余弦 c 先变成 z = (c - m) / s，再 `score = clamp((z - z_floor) / (z_ceil - z_floor))`；库里少于 30 条
（或 s = 0）时退回 S。五个模型共用一组 (z_floor, z_ceil, min_score, 比例)，从 z_floor 1.0 到 3.0（步长 0.5）、z_ceil = z_floor + 1.5、2、3、4、5
和 `bench/dev_eval.py` 的完整网格里，按五个模型 `balanced` 的平均值最高选：z_floor 3.0、z_ceil 6.0、min_score 0.30、比例 0.65。
（最优点落在 z_floor 的网格边上，没有再往外扩：规则里网格是事先定的，扩了也不会补上下面最大的 7.3 点差距，见下。）

### bench/dev，每个模型：S（它自己的最优点）和 Z（共用的那一点）

排名三列是后端不设分数线的排序（低于 floor 的不返回）；“交付”三列是默认召回阶段之后。交付命中按题型是 lexical / paraphrase / updated 的命中题数
（共 30 / 38 / 12）。

| 模型 | 方案 | Hit@1 | Hit@5 | MRR@10 | 交付命中 | 精确率 | 安静率 | balanced | lexical | paraphrase | updated |
|---|---|---|---|---|---|---|---|---|---|---|---|
| bge-m3 | S | 82.5 | 97.5 | 0.893 | 77.5 | 83.7 | 60.0 | 74.9 | 29 | 26 | 7 |
| | Z | 81.2 | 92.5 | 0.865 | 70.0 | 87.4 | 73.3 | 76.5 | 27 | 25 | 4 |
| bge-small-zh | S | 73.8 | 91.2 | 0.815 | 76.2 | 80.6 | 56.7 | 72.4 | 28 | 27 | 6 |
| | Z | 71.2 | 78.8 | 0.748 | 52.5 | 86.1 | 76.7 | 68.3 | 23 | 15 | 4 |
| bge-base-zh | S | 81.2 | 96.2 | 0.868 | 72.5 | 81.8 | 73.3 | 75.9 | 29 | 22 | 7 |
| | Z | 77.5 | 83.8 | 0.800 | 60.0 | 90.2 | 76.7 | 73.3 | 24 | 20 | 4 |
| embeddinggemma | S | 78.8 | 98.8 | 0.877 | 73.8 | 83.4 | 76.7 | 77.8 | 29 | 24 | 6 |
| | Z | 77.5 | 91.2 | 0.842 | 68.8 | 84.4 | 56.7 | 70.6 | 27 | 24 | 4 |
| qwen3-embedding | S | 73.8 | 93.8 | 0.821 | 75.0 | 74.0 | 46.7 | 66.9 | 27 | 28 | 5 |
| | Z | 68.8 | 82.5 | 0.744 | 55.0 | 81.1 | 66.7 | 65.9 | 23 | 17 | 4 |

Z 减 S 的 `balanced`（点）：bge-m3 +1.7、bge-small-zh -4.1、bge-base-zh -2.6、embeddinggemma -7.3、qwen3-embedding -1.1。
Z 的排名列更低，是因为 z 小于 z_floor 的记忆分数为 0、不返回（排序列表被截短），不是排序变了。

### examples/eval（46 条记忆，30 个问题），两种方案的设置都不变

S 用上面的 floor / ceil 和最优点，Z 用同一个共用点；这份集合的问题没有题型字段。

| 模型 | 方案 | Hit@1 | Hit@5 | 交付命中 | 精确率 | 安静率 | balanced |
|---|---|---|---|---|---|---|---|
| bge-m3 | S | 100.0 | 100.0 | 95.5 | 98.4 | 100.0 | 97.7 |
| | Z | 72.7 | 72.7 | 40.9 | 100.0 | 100.0 | 69.2 |
| bge-small-zh | S | 95.5 | 100.0 | 100.0 | 97.7 | 100.0 | 99.2 |
| | Z | 68.2 | 68.2 | 40.9 | 100.0 | 100.0 | 69.2 |
| bge-base-zh | S | 95.5 | 100.0 | 86.4 | 93.3 | 100.0 | 92.5 |
| | Z | 63.6 | 63.6 | 40.9 | 100.0 | 87.5 | 65.9 |
| embeddinggemma | S | 100.0 | 100.0 | 81.8 | 100.0 | 100.0 | 92.7 |
| | Z | 95.5 | 95.5 | 63.6 | 100.0 | 100.0 | 83.7 |
| qwen3-embedding | S | 95.5 | 100.0 | 95.5 | 93.2 | 100.0 | 95.8 |
| | Z | 72.7 | 77.3 | 54.5 | 100.0 | 87.5 | 75.1 |

平均 `balanced`：S 95.6，Z 72.6。Z 在 46 条的小库上几乎什么都不交付：库越小，“比平均值高几个标准差”越难达到，
z_floor 3.0 对 160 条的库合适，对 46 条的库就太严。S 在两份数据上都稳（S 的数是在 bench/dev 上定的，examples/eval 上没有再调）。

### 规则（事先写定）逐步应用

1. Z 要成为默认，需要：每个模型在 bench/dev 上，共用设置的 `balanced` 比 S 的该模型最优点低不超过 1.0 点，**并且** 在 examples/eval 上
   五个模型的 `balanced` 平均值不低于 S。结果：embeddinggemma 低 7.3、bge-small-zh 低 4.1、bge-base-zh 低 2.6，条件一不成立；
   examples/eval 上 Z 72.6 < S 95.6，条件二也不成立。**Z 不是默认。**
2. 否则 S 加预设表是默认；Z 只有在每个模型都低不超过 3.0 点时才作为选项发布：最差是 -7.3，不满足。**Z 不发布，只在这里描述。**
3. 所以后端没有 `calibration` 选项，只有按模型名的预设表（`backends/embedding.py` 的 `PRESETS`）和未知模型的一次警告。
   bge-m3 的默认值没有变（预设正好是原来的 0.40 / 0.80 / 0.50 / 0.85）；其余默认值也都没有受影响。

## 每一档的代价（`costs.sh`，原始输出在 `results_costs.txt`）

内存是 `docker stats` 在只加载这一个模型、预热并跑完入库和查询之后读的（轻量容器每个模型之前重启一次；bge-m3 在原来的容器里，
没有重启，是它加载了很久之后的值）。延迟是 110 个互不相同的短中文问题，顺序、预热后，经后端的 `_embed`（每次新建一个连接，
带上留用的查询前缀）；p95 里每个模型都有 30 ms 上下的偶发慢请求（连接建立，不是模型），所以 p95 只用来比较量级。入库是 160 条记忆一次 `put_many`
（每批 32 条）。库文件按 SQLite 页（4 KB）分配：512 和 768 维的向量一条记忆占一页，所以文件大小几乎一样，向量本身是 条数 x 维度 x 4 字节。

| 档 | 模型文件 | 常驻内存 | 查询 p50 / p95 | 入库 160 条 | 维度 | 向量库文件 | 向量本身 |
|---|---|---|---|---|---|---|---|
| 无模型 | 无（`sqlite` 关键词后端） | 可以忽略（标准库，一个 SQLite 文件） | 本地字符串匹配，不请求接口 | 本地写库 | 无 | 小 | 无 |
| bge-small-zh | 47.9 MB | 175 MiB | 4.1 / 31.5 ms | 0.67 s | 512 | 675840 B | 328 KB |
| bge-base-zh | 110 MB | 238 MiB | 14.4 / 40.8 ms | 2.59 s | 768 | 675840 B | 492 KB |
| embeddinggemma | 622 MB | 761 MiB | 8.9 / 38.9 ms | 1.98 s | 768 | 675840 B | 492 KB |
| qwen3-embedding 0.6B | 639 MB | 1.20 GiB | 74.0 / 82.7 ms（不带前缀时 27.4 / 58.7） | 8.56 s | 1024 | 757760 B | 655 KB |
| bge-m3 | 1.16 GB | 1.36 GiB | 19.5 / 47.3 ms | 4.73 s | 1024 | 757760 B | 655 KB |

空闲的轻量容器（没有模型）约 113 MiB。`sqlite` 后端的指标在 `bench/results/dev/sqlite.md`（开发集 `balanced` 60.6，Hit@5 82.5，交付命中 36.2%，改写题只交付 2/38）。
测量时机器上还有别的容器，延迟只能当量级看。

### 每一档在开发集上给什么（S，各自的预设）

| 档 | Hit@5 | 交付命中 | 安静率 | balanced | 改写题交付 |
|---|---|---|---|---|---|
| sqlite（无模型） | 82.5 | 36.2 | 86.7 | 60.6 | 2/38 |
| bge-small-zh | 91.2 | 76.2 | 56.7 | 72.4 | 27/38 |
| bge-base-zh | 96.2 | 72.5 | 73.3 | 75.9 | 22/38 |
| embeddinggemma | 98.8 | 73.8 | 76.7 | 77.8 | 24/38 |
| qwen3-embedding | 93.8 | 75.0 | 46.7 | 66.9 | 28/38 |
| bge-m3 | 97.5 | 77.5 | 60.0 | 74.9 | 26/38 |

开发集是单一作者、单一虚构人物的中文短句，最小的题型只有 12 题：1 个点约 1 道题，几个点以内的差别不要当作排名。多语言能力这份数据测不到。

## 小模型上混合后端值不值得

用 `bench/experiments/fusion/fusion_eval.py`（另一位同事做的融合实验，没有改它；它的缓存和结果写到了别处，没有覆盖它的 `results.txt`），
关键词子后端加小向量子后端，向量子后端用上表的预设。完整输出在 `results_hybrid.txt`。A 是现在 `hybrid` 的概率“或”融合。

| 向量模型 | 只用向量 balanced | A 的 balanced | 只用向量 Hit@5 | A 的 Hit@5 |
|---|---|---|---|---|
| bge-small-zh | 72.4 | 70.4 | 91.2 | 91.2 |
| bge-base-zh | 75.9 | 76.4 | 96.2 | 96.2 |

小模型上混合也没有赢：bge-small-zh 低 2.0 点，bge-base-zh 高 0.5 点（不到一道题的分量 0.9），两者的 Hit@5 完全相同。
改写题交付也没有变多（bge-small-zh 27 变 26）。那位同事看过数据后加的变体 G（关键词权重 0.25）在 bge-base-zh 上是 77.2、Hit@5 97.5，
比只用向量高 1.3 点、多 1 题，在 bge-small-zh 上 72.1，仍然不高于只用向量；它不在 `hybrid` 里，规则下也没有被采用。
所以预期的“小模型加关键词能补上”在这份开发集上没有出现。

## 为留出集运行准备了什么（没有运行）

`bench/adapters/feiyu_memgate_embedding.py` 和 `feiyu_memgate_hybrid.py` 新增 `--system-suffix` 和 `--option KEY=JSON`，已有的 `--model`、`--embed-url` 不变。
例如（后端按模型名取预设，不用再传 floor、ceil、前缀）：

```
python bench/adapters/feiyu_memgate_embedding.py --embed-url http://fmg-bench-embedder-light:11434/v1 \
    --model hf.co/CompendiumLabs/bge-small-zh-v1.5-gguf:F16 --system-suffix bge-small-zh
```

得到 `feiyu-memgate-embedding-bge-small-zh.json`；不带新参数时系统名和文件名都和以前一样。用 `--data bench/dev` 在开发集上冒烟测试过。
后缀约定：`bge-small-zh`、`bge-base-zh`、`embeddinggemma`、`qwen3-embedding`（`bench/systems.json` 里已有这八行：embedding 和 hybrid 各四行，没有结果文件时
`bench/score.py` 和 `bench/charts.py` 都不会受影响，它们只按 `bench/results/raw` 里有的结果画）。留出集那次运行需要 `fmg-bench-embedder-light` 在运行（已留着）。

## 复现

```
bash bench/experiments/light_models/setup.sh                       # 下载、起容器、验证四个模型
# 在 docker 内网里，每个 (模型, 方案) 一次，写缓存（只含余弦，不含文字）：
python bench/experiments/light_models/collect.py --model-key bge-small-zh --variant plain --cache DIR
python bench/experiments/light_models/analyze.py prefix --cache DIR          # 前缀比较
python bench/experiments/light_models/analyze.py calibrate --cache DIR --write-md   # S 对 Z（约 8 分钟），写 bench/results/dev/embedding-*.md
REPO=... CACHE=... bash bench/experiments/light_models/costs.sh              # 内存、延迟、入库、库大小
```

`results_calibration.txt`、`results_costs.txt`、`results_hybrid.txt` 是这次的原始输出。缓存用完即删，不在仓库里。

## 局限

- 开发集小、单一作者、全是中文短句；预设里的分数线是在这份数据上选的，换成你自己的数据请用 `bench/dev_eval.py --search` 再校准。
- 五个模型的预设各自只用一份开发集选出，examples/eval 只用来检查 S 和 Z 的迁移，没有参与选择。
- Z 的网格最优点落在 z_floor 的上边（3.0）；规则里网格事先定死，没有扩大。其他形式的自适应（比如排名名次、与最高分的差）没有试。
- bge-base-zh 用的是 Q8_0 而不是 F16（见上），embeddinggemma 是 Ollama 的 bf16。
- 内存是容器级的 `docker stats`，包含运行器进程和模型文件的缓存页；延迟是在机器上还有别的容器时测的。
