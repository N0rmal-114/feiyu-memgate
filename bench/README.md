# 记忆检索基准

这个目录是一把给“聊天机器人长期记忆”做横向对比的尺子：同一批记忆、同一批问题，每个系统由一个**适配器**
读入记忆、回答问题，写出一个 JSON 文件，再由同一个打分脚本算成数字。

| 路径 | 是什么 |
|---|---|
| `data/` | 测试集：240 条记忆、160 个问题（字面、改写、更新、没有答案四类），版本 v1，已冻结，说明见 `data/README.md` |
| `CONTRACT.md` | 适配器要写的结果文件的格式和规则（不许按问题调参、用默认设置、每条记忆只入库一次等） |
| `score.py` | 打分脚本：读 `data/` 和 `results/raw/*.json`，写 `results/summary.json` 和 `results/summary.md` |
| `adapters/` | 各系统的适配器；`feiyu_memgate_*.py` 是本项目的四个 |
| `results/` | `raw/` 是各系统的原始答案，`summary.md` 是打分后的总表；`design/features.md` 是各被测项目的功能事实对照（形态、检索方式、写入是否调模型、默认交付、遗忘、许可证），`design/self-check.md` 是本项目自己的设计自查 |
| `design_probe.py` | 设计自查的探针：在进程内实际运行本项目，测十项设计性质，写 `results/design/feiyu-memgate.json`；只测 feiyu-memgate |
| `dev/` | 开发集：160 条记忆、110 个问题，选默认阈值用，说明见 `dev/README.md`，**不用来报告结果** |
| `dev_eval.py` | 开发集评测和阈值网格搜索：选默认值用的工具，见下；每个后端的搜索结果在 `results/dev/` |
| `run_all.sh` | 最后的留出集运行：逐个适配器、打分、遗忘模拟、图表，见“复现” |
| `forgetting_sim.py` | 遗忘曲线的模拟，和这里的检索基准无关 |
| `tiers.json` | 部署档位图 `tiers` 用的资源数据（模型文件大小、常驻内存、维度、查询延迟、在哪测的），来自开发集运行时的测量，见 `experiments/light_models/` |
| `experiments/` | 不属于产品的实验（每个有自己的 README）：`newer_first`、`fusion`、`light_models`（更轻的向量模型，`setup.sh` 起第二个 embedder 容器） |

## 测试集不许看：开发集和测试集分开

`data/` 是**留出的测试集**。任何算法或参数（分数线、`floor`/`ceil`、`relative_score` 的比例、融合方式、打分公式）
都只能在**开发集**上选：先是 `examples/eval/`（46 条记忆、30 个问题，其中 8 个记忆里没有答案），后来是大得多的 `dev/`
（160 条记忆、110 个问题，其中 30 个没有答案）；两份的文字都和测试集不同。选的时候不看测试集的问题、期望答案和逐题结果，
也不在上面跑任何东西；设计定下来以后才在测试集上跑，跑完不再按它的结果改设计。`examples/eval` 太小（一个问题 4.5 个百分点），
选出的分数线在测试集上偏严，所以默认的 `min_score` 和 `relative_score` 比例后来在 `dev/` 上按一条事先写定的规则重新选了一次
（见下和“运行记录”）。
各后端默认值的来历和开发集上看到的数字见 [../docs/backends.md](../docs/backends.md)。

## 在开发集上评测：`dev_eval.py`

```bash
python bench/dev_eval.py --backend sqlite --search             # 固定规则的网格搜索，写 results/dev/sqlite.md
python bench/dev_eval.py --backend sqlite                      # 默认的召回步骤
python bench/dev_eval.py --backend sqlite --ratio 0.7          # 改 relative_score 的比例（没有就加在 min_score 后面）
python bench/dev_eval.py --backend sqlite --stages min_score,fill_text,redact,dedupe,budget
python bench/dev_eval.py --backend sqlite --sweep              # 分数线 x 比例的整张表
python bench/dev_eval.py --backend hybrid --embed-url http://127.0.0.1:11434/v1 --model bge-m3 --sweep
python bench/dev_eval.py --backend embedding --embed-url ... --option floor=0.4 --option ceil=0.8
```

`--backend` 是 `sqlite`、`inmemory`、`embedding`、`hybrid`；后两个需要一个兼容 OpenAI 的 embeddings 接口。
`--stages` 写逗号分隔的名字或 JSON 列表；`--min-score`、`--ratio`、`--gap` 设对应步骤的选项。
每一行输出：Hit@1、Hit@5（后端自己的排名，没有阈值）、交付命中（网关按这套步骤和默认的 5 条真正会交出去的里有没有对的）、
对的记忆拿到的比例、噪声（交出去的里不是对的占比）、安静率（没有答案的问题什么都不交的比例）、每题平均条数。
定义和 `score.py` 一致（指标代码就是从那里导入的）。`--data DIR` 选开发集（默认 `bench/dev`），它拒绝读 `data/`。

`--search` 是选默认阈值的那条规则（事先写定，之后不改）：网格 `min_score` 0.05 到 0.80（步长 0.05）x `relative_score` 比例
0.50 到 0.95（步长 0.05）或不用该步骤，其余默认召回步骤不变，limit 5；目标
`balanced = (n可答 x 交付F1 + n安静 x 安静率) / (n可答 + n安静)`，交付 F1 是交付命中和精确率的调和平均；
`balanced` 最高的格子胜出，平手取交付命中高的，再取 `min_score` 低的，再取比例低的。每个后端的候选列表（前 15 条和分数）只算一次，
网格在列表上用真实的召回步骤离线跑。向量、混合后端在 docker 里连 embeddings 接口跑
（`docker run --rm --network fmg-bench -v <仓库>:/repo -w /repo -e PYTHONPATH=/repo/src python:3.12-slim python bench/dev_eval.py --backend hybrid --embed-url http://fmg-bench-embedder:11434/v1 --search`）；
`openviking` 用 `bash bench/adapters/openviking/run.sh dev` 生成候选列表，再 `python bench/dev_eval.py --backend openviking --candidates <列表文件> --search`。
`--save-candidates FILE` 保存候选列表。

`--search --profile broad` 用同一份候选列表和同一张网格，换一个事先写定的目标，选第二档“宽松”分数线（`profile = "broad"`，
产品里怎么用见 [../docs/backends.md](../docs/backends.md) 的“两档分数线”），写 `results/dev/<名字>-broad.md`：在精确率 >= 50% 且安静率
>= 该后端默认点安静率一半的格子里取交付命中最高的，平手取 `balanced` 高的，再取 `min_score` 高的，再取比例高的（不用 `relative_score` 算最低）；
精确档是后端声明的默认点（向量后端看 `--model` 的预设）。`--name` 改输出文件的名字（比如 `embedding-bge-small-zh`）。
例：`python bench/dev_eval.py --backend sqlite --search --profile broad`。

## 跑一个适配器

四个 feiyu-memgate 适配器都在 `adapters/`，结果默认写到 `results/raw/<系统名>.json`，`--out` 可以改目录：

```bash
python bench/adapters/feiyu_memgate_sqlite.py                  # 关键词后端，不需要任何服务
# 向量、混合、全开三个要连 embeddings 接口（默认地址是基准用的 docker 网络里的 fmg-bench-embedder）：
python bench/adapters/feiyu_memgate_embedding.py --embed-url http://127.0.0.1:11434/v1 --model bge-m3
python bench/adapters/feiyu_memgate_hybrid.py    --embed-url http://127.0.0.1:11434/v1 --model bge-m3
python bench/adapters/feiyu_memgate_full.py      --embed-url http://127.0.0.1:11434/v1 --model bge-m3
```

| 系统名 | 配置 |
|---|---|
| `feiyu-memgate-sqlite` | `sqlite` 后端 + 默认召回步骤 |
| `feiyu-memgate-embedding` | `embedding` 后端（bge-m3）+ 默认召回步骤 |
| `feiyu-memgate-hybrid` | `hybrid`（sqlite + embedding，概率“或”融合）+ 默认召回步骤 |
| `feiyu-memgate-full` | `hybrid` + 默认召回步骤 + `freshness`（遗忘账本，临时文件，不做清理，记忆带自己的日期） |

另外六行是同一个项目的别的配置（`bench/systems.json` 里标了 `"variant": true`，在表里，不在五张基准图里；它们有自己的一张图 `tiers`）：

| 系统名 | 配置 |
|---|---|
| `feiyu-memgate-embedding-bge-small-zh`、`-bge-base-zh`、`-embeddinggemma`、`-qwen3-embedding` | `embedding` 后端换成更轻的模型，用后端里这个模型的预设（`floor`、`ceil`、分数线、前缀），默认召回步骤；模型由第二个 embedder 容器 `fmg-bench-embedder-light` 提供，适配器参数 `--embed-url`、`--model`、`--system-suffix`（见 `run_all.sh`） |
| `feiyu-memgate-sqlite-broad`、`feiyu-memgate-embedding-broad`（bge-m3） | 同一个后端，`[recall] profile = "broad"`（宽松分数线，适配器参数 `--profile broad`；`embedding` 那个还要 `--system-suffix broad`） |
四个都只用文档里的默认值，没有按测试集调过。`--data DIR` 可以让它们读另一个目录的数据（形状和 `data/` 一样），
冒烟测试用，不要拿它去跑测试集来调参。

## 跑全部并打分

其他系统的适配器各有一个文件夹，里面的 `README.md`、`build.sh`、`run.sh` 说明怎么构建和运行（多数在容器里，
需要同一个 embeddings 接口）。每个适配器都写一个 `results/raw/<系统名>.json`；全部跑完后：

```bash
python bench/score.py          # 校验每个结果文件，写 results/summary.md 和 results/summary.json
```

不符合 `CONTRACT.md` 的结果文件会被点名并排除在总表之外（退出码 1），其余照常打分。

## 复现

`bash bench/run_all.sh` 按固定顺序逐个跑所有适配器（一次一个，时间才干净），然后打分、跑遗忘模拟（两个后端）、生成图表；
`RENDER=1` 再把图表渲染成 PNG（约 20 分钟），`SKIP="lingshu openviking"` 跳过某些步骤。它什么都不构建，假定镜像已经在本机
（每个适配器的 `build.sh` 或 `README.md` 写了构建命令）；`meow-memory`、`seekmemory`、`lingshu` 的 `run.sh` 会用解压好的第三方源码
拼构建上下文再 `docker build`（镜像是最新的就是缓存命中），所以它们的源码变量要设。用 LLM 的两个付费模式
（`mem0` 的 LLM 提取、`graphiti`）**不在** `run_all.sh` 里（重跑要花钱）；`mem0` 只跑 `MODE=verbatim`。
`lingshu-dsh-memory` 为了遵守插件自己的写入速率限制，约要 30 分钟，放在最后。
`run_all.sh` 里还有六个 feiyu-memgate 的配置行（两个宽松档、四个更轻的向量模型）；四个轻模型需要第二个 embedder 容器
`fmg-bench-embedder-light`，用 `bash bench/experiments/light_models/setup.sh` 准备（下载四个模型、起容器、验证维度，约 1.3 GB；
下载那一步需要临时接外网，服务容器只接内部网络 `fmg-bench`）。没有它，这四行跑不出来，其余照常。

环境变量（所有 `adapters/**.sh` 都 source `adapters/_env.sh` 来得到它们）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `FMG_REPO` | 仓库根目录（从脚本自己的位置算出；有 `cygpath` 时用 `cygpath -m` 的盘符形式，docker 在 Windows 上需要这种写法） | 仓库位置 |
| `FMG_BENCH_HOME` | 仓库旁边的 `feiyu-memgate-bench` 文件夹 | 仓库外的暂存空间：构建上下文在 `$FMG_BENCH_HOME/work/<系统>`，自己取来的第三方源码可以放在 `$FMG_BENCH_HOME/vendor/<名字>` |
| `FMG_LLM_ENV_FILE` | `$FMG_BENCH_HOME/deepseek.env` | 只有两个付费模式用：定义 `DEEPSEEK_API_KEY` 的 env 文件，从不打印；文件不存在时脚本报错退出 |
| `MEOW_MEMORY_PKG` | 无 | npm 包 `meow-memory` 0.32.6 解压后的文件夹（里面有 `package.json`） |
| `LINGSHU_PKG` | 无 | npm 包 `@furongjun1999/dsh-memory` 0.10.1 解压后的文件夹 |
| `MEMSEARCH_SRC` | 无 | `github.com/zilliztech/memsearch` 的检出，提交 `369ae1da48b7`（0.4.23），只 `memsearch/build.sh` 用 |
| `MNEMON_SRC` | 无 | `github.com/omdsh-dev/dsh-mnemon` 的检出，版本 0.5.45、提交 `1dec529`，只 `dsh-mnemon/build.sh` 用 |
| `SEEKMEMORY_SRC` | 无 | `github.com/NeverENG/SeekMemory` 的检出，提交 `794ec5d` |

没有默认值的变量没设时，对应脚本用一行话说明要哪个包或仓库、哪个版本或提交，然后退出。

需要的东西：Docker 和 Git Bash（Windows 上）；一个内部网络 `fmg-bench`（`docker network create --internal fmg-bench`，容器没有外网）和
网络上一个叫 `fmg-bench-embedder` 的容器，在 11434 端口提供兼容 OpenAI 的 embeddings 接口，模型 `bge-m3`（1024 维，CPU 就行，
例如装了 `bge-m3` 的 Ollama；拉模型时需要先临时接外网）；还要第二个容器 `fmg-bench-embedder-light`（同一个网络，同一种接口，
四个较轻的模型：bge-small-zh-v1.5、bge-base-zh-v1.5、embeddinggemma、qwen3-embedding:0.6b，名字和用法见 `experiments/light_models/`，
准备脚本 `experiments/light_models/setup.sh`；只有留出集上的四个轻模型行用到它）；`python:3.12-slim` 和各适配器自己的镜像。
所有第三方代码只在容器里跑（无网络，或只接 `fmg-bench`），仓库只读挂载，只有 `results/raw` 可写。

## 运行记录

这一节只写做过什么，数字在 `results/summary.md`。

- **日期。** 开发集 `bench/dev` 上的阈值搜索和最后的留出集运行都在 2026-11-15（本机时间；最后一次顺序运行 00:12 到 00:53，
  其中 `lingshu-dsh-memory` 一个就是 31 分钟）。更早的运行在 2026-11-14。同一天稍后（03:22 到 03:28）又单独重跑了 feiyu-memgate 的
  行并补上六个新配置行，见下面的“第四次”。
- **所有参数都只在开发集上选。** 开发集有两份：`examples/eval`（46 条记忆、30 个问题）和 `bench/dev`（160 条记忆、110 个问题）。
  `embedding`、`openviking` 的 `floor`/`ceil`、`hybrid` 的融合方式、关键词的加权方式是更早在 `examples/eval`
  上定的，之后没有动；**`min_score` 的默认值和 `relative_score` 的比例**（`sqlite`/`inmemory` 0.30 / 0.75，`embedding` 0.50 / 0.85，
  `hybrid` 0.70 / 0.85，`openviking` 0.55 / 0.95）是在 `bench/dev` 上按上面写的规则选的，选之前没有看留出集的问题、答案或逐题结果。
  前 5 名和选定的格子在 `results/dev/<后端>.md`。规则没有写到的一层平手（比例）取了更宽松的一个，这是在看到开发集数字时补上的，
  文档里写明了。
- **留出集一共跑过几次，为什么。** 本项目的四个适配器（`feiyu-memgate-sqlite`、`-embedding`、`-hybrid`、`-full`）和
  `feiyu-memgate-openviking`：第一次是每个适配器第一次接通时的运行（那时关键词后端还是二元组重合度、没有 `relative_score`，
  分数线来自 `examples/eval`）；第二次是加了关键词加权和 `relative_score` 以后的一次，参数来自 30 题的 `examples/eval`，
  结果是分数线偏严、漏答多（这就是后来做 `bench/dev` 的原因）；第三次是 00:12 开始的那次最后的顺序运行，参数来自 `bench/dev`。
  **第四次**（2026-11-15 03:22 到 03:28）见下一条。
  其他系统的适配器：第一次接通时的运行，加上这次；其间为了修适配器的调用方式（不是第三方代码、不是数据、不是参数）个别适配器重跑过，
  次数没有逐一记录。**没有任何设置是按留出集的结果改的**；这次运行之后也没有改任何东西。
  遗忘模拟读的也是留出集的数据（场景、种子、分组在看结果之前定下，分数线用各后端默认值），它不用来选设置。
- **第四次留出集运行：跑了什么、为什么。** 默认召回步骤在第三次之后多了 `newer_first`（只给分数相近的候选按日期重排，不改变交付哪几条），
  所以逐个重新生成了受它影响的 feiyu-memgate 行：`feiyu-memgate-sqlite`、`-embedding`、`-hybrid`、`-full`，以及 `openviking` 那一组
  （`feiyu-memgate-openviking`、`openviking`、`openviking-unfiltered`，来自同一次 `run.sh`）；同时第一次跑六个新行：`feiyu-memgate-embedding-bge-small-zh`、
  `-bge-base-zh`、`-embeddinggemma`、`-qwen3-embedding`（各自模型的预设，经第二个 embedder 容器）和宽松档的 `feiyu-memgate-sqlite-broad`、
  `feiyu-memgate-embedding-broad`（bge-m3）。运行前，旧的原始文件挪到了仓库外，用来对照。**对照的结果：** 对 sqlite、embedding、hybrid、openviking 以及
  `full`，每个问题交付的编号集合和不设阈值的排名（编号、顺序、分数）都和旧文件完全相同，只有交付列表里的先后顺序有差别（`newer_first` 造成的）；
  没有任何一个问题的交付集合变了。遗忘模拟也重跑了（它的召回步骤现在含 `newer_first`），只有“新在旧前”一列变了，见 `results/forgetting/reading.md`。
  **所有选择都来自开发集，没有看了这次的结果再改任何东西。** 宽松档的规则在看数字之前写进了 `dev_eval.py`；四个轻模型的预设来自 `experiments/light_models/`（只用 `bench/dev`）；
  这六个新行和重跑的行都只跑了一次。**这是 feiyu-memgate 的行第四次在留出集上跑**，原因是：它们的代码（默认召回步骤、预设表）在第三次之后变了，
  行的数字要对应现在的代码；每一次重跑的原因和之前一样，都是产品或适配器变了，没有哪一次是为了追数字。第三方行没有重跑：它们的代码、数据和适配器都没有变。
  机器同样不是完全空闲的（别的容器在跑），延迟只能在同一次运行的行之间比。第二个 embedder 容器里的模型每个只在自己那一行的运行里被加载。
- **没有重跑的行。** `mem0`（LLM 提取，`results/raw/mem0.json`）和 `graphiti`（`graphiti.json`）是付费模式，重新入库要花钱，
  保留之前的结果文件；它们的延迟和入库时间来自一台同时有别的任务在跑的机器，总表里有脚注。第三次运行里其余的行都逐个重跑了；第四次只重跑上一条列出的 feiyu-memgate 行，第三方行（meow-memory、seekmemory 三个文件、dsh-mnemon 两个、memsearch、mem0-verbatim、lingshu、以及上面两个付费行）没有重跑：它们的代码、适配器和数据都没有变，沿用第三次的结果文件。
- **机器不是完全空闲的。** 这台机器上同时有别的容器在跑（不属于这个基准，不能动），所以时间只能在这一次运行的行之间比较，
  而且只能当作量级。
- 这次运行里没有任何一步失败。
