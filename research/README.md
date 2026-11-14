# 调研：其他记忆项目的对比

这个目录收录 feiyu-memgate 的来源项目（陪伴机器人"小肥鱼"）在 2026 年 11 月上旬做的一轮调研：当时有哪些热门的开源记忆库和记忆插件，它们和小肥鱼在用的方案（OpenViking v0.5.3 加官方 DSH 插件，DeepSeek 抽取，本地 bge-m3 向量）比起来怎么样，有什么设计值得借鉴。调研全部是静态审查：读公开页面、读源码、查 GitHub 和 npm 的公开数据，没有安装或运行被评估的项目，所以召回质量和实际费用都没有实测。各项目发展很快，结论只对当时的版本成立。

和 feiyu-memgate 的关系：feiyu-memgate 是从小肥鱼里抽出来的、可替换后端的记忆网关。下面第三节把报告里的建议和它已有的功能对了一遍，并明确标出哪些还没做。其中八个项目后来在同一份数据上做了实测，数字见 [bench/results/summary.md](../bench/results/summary.md)，功能事实对照见 [bench/results/design/features.md](../bench/results/design/features.md)。

注意：调研时在 B 站看到一个叫“feiyu-memory（肥鱼日记）”的插件，它是另一位作者的第三方项目，名字和本仓库相近，但两者无关。

## 目录

- [reports/popular-memory-libraries.md](reports/popular-memory-libraries.md)：B站、GitHub、npm 上的热门 DSH 记忆库横向评估，含唯一一份公开评测的核查。抖音未能查成（需要登录）。
- [reports/seekmemory-and-ido1um.md](reports/seekmemory-and-ido1um.md)：SeekMemory 与 deepseek-memory-ido1um 两个新项目的评估，补充了上一份的借鉴清单。
- [reports/memory-star-map-plugin.md](reports/memory-star-map-plugin.md)："记忆星图"类可视化插件（重名项目很多）的评估。

## 对比表

下表只收报告里写到的内容；报告没有说的格子写"报告未涉及"，不做猜测。OpenViking 一行是报告里作为对照基线的方案。

| 项目 | 类型/定位 | 记忆存在哪 | 怎么召回 | 怎么写入 | 遗忘/整理 | 值得借鉴的点 | 局限 |
|---|---|---|---|---|---|---|---|
| OpenViking（对照基线） | 机构维护的记忆服务加官方 DSH 插件，同时服务 DSH 和 AstrBot | 独立服务 | 向量语义检索（本地 bge-m3） | 提交会话时由大模型抽取 | 有去重整理（报告未展开） | 语义召回、跨平台共享 | 每次提交要调模型抽取，有费用 |
| [dsh-mnemon](https://github.com/omdsh-dev/dsh-mnemon) | 多后端编排层，可把 OpenViking 当后端 | 多后端 | 报告未涉及 | 后台"空闲复盘"副会话写入 | 后台整理，默认开启，每会话最多 20 次 | 工程外壳最完整（CI、发布校验）；教训是后台整理要有次数上限、固定便宜模型、额度跨重启保留 | 复盘费用叠加；0.5.x 期间仍在修安装问题 |
| [dsh-memory-evolve](https://github.com/csyangwen/dsh-memory-evolve) | 模型自己写 Markdown 的轻量记忆 | Markdown 文件 | 无向量 | 模型自己写 | 报告未涉及 | 报告未涉及 | 不上 npm，无 CI |
| [dsh-meow-memory](https://github.com/Phant0Meow/dsh-meow-memory) | SQLite 加 BM25 的插件 | SQLite | BM25 词面检索 | 主 agent 手动写入 | 反思和"做梦"整理 | 来源不明（非本机创建）的记忆库默认不自动注入，要手动确认 | 无 CI |
| [灵枢 dsh-memory](https://github.com/FuRongJun-1999/dsh-memory) | 体量很大的原文落盘插件，需 Python | 原文落盘 | 词面检索，每轮自动注入至多 4 条、1400 字符 | 写入前对密钥、密码、手机号、身份证号做正则脱敏；插件自身不调模型 | 报告未涉及 | 写入前脱敏；注入硬上限；截断时一定带"续读"提示 | 体量很大，issue 多 |
| [SurfCore](https://github.com/Mieksido/surfcore) | QQ、B站、桌宠的陪伴机器人平台，记忆只是子系统 | 记忆卡加全量存档 | 整张记忆卡常驻注入；可选向量 | 规则打分，记忆本身不调模型 | 每日衰减；"会衰减的注入卡"与"永不删的全量存档"分开 | 衰减卡与全量存档分离，避免遗忘等于丢数据 | 是整个平台而不是记忆库；约 2 周龄，无 CI |
| [dsh-rofm](https://github.com/wall1234567/dsh-rofm) | 上下文压缩时归档原文的通用会话记忆 | 归档原文 | 2/3-gram 关键词召回 | 压缩时归档；评分由主模型调用工具完成 | 压缩即归档 | 工程纪律最好；"无损压缩"思路 | 同义改写召回弱；出过会话卡死 |
| [dsh-soul](https://github.com/icyaaaww/dsh-soul) | 面向编码任务的约束、决策、产物记忆 | SQLite 图加全文 | 全文检索 | 默认零模型调用，演化模式花钱 | 图压缩（报告未展开） | 报告未涉及 | 抽取的是任务约束而不是人物记忆；15 天内改过 3 次定位 |
| [dsh-hippocampus](https://github.com/coner13/dsh-hippocampus) | 原话账本 | 原话落盘 | 词面检索 | 只追加不改写，不依赖模型记得去记 | 报告未涉及 | "原话账本"：证据不会被摘要吃掉 | 许可为非商用；只有 2 个提交 |
| [dsh-agent-soul](https://github.com/cyanwell/dsh-agent-soul) | 约 22 个子插件的大型个人框架，不是记忆插件 | 报告未涉及 | 报告未涉及 | 报告未涉及 | 报告未涉及 | 只借鉴思路 | 不是单独的记忆插件 |
| [SeekMemory](https://github.com/NeverENG/SeekMemory) | 宿主无关的内核加适配器（AstrBot、DSH） | 本机 SQLite | 每轮召回零模型调用，注入 600 字硬上限；无向量语义检索 | 抽取默认关闭或每天最多 2 次 | 遗忘曲线；夜间 dream 整理，最多 12 次调用、6 万 token，可断点续跑 | 披露闸门与 subject_key（记忆主体单独记）；Source 不可变加版本回滚；整理有预算且可续跑 | 换个说法就召回失败（#240）；DSH 适配器仅 0.0.1；基准是自建合成数据 |
| [deepseek-memory-ido1um](https://github.com/Ido1um/DeepSeek-memory-Ido1um) | 为作者自己的 QQ 机器人写的记忆内核，非通用插件 | 本地存储，聊天原文按 JSONL 保留 7 天 | 词面加槽位加主题树，无向量 | 写入分 pass、mark、block 三档，低置信先进候选池 | 日终裁决；修订留痕，墓碑代替物理删除 | 写入门；事实时间取原话被说出的那一轮；召回带出处，查不到时给诊断而不是静默返回空 | 与宿主耦合深；无基准数字 |
| 记忆星图类插件（如 [Snowstupid/dsh-memory-starmap](https://github.com/Snowstupid/dsh-memory-starmap)） | 对话存档的可视化加手动隐藏，部分带召回工具 | 读 DSH 会话存档，不建自己的库 | 字符 bigram 加 IDF，纯关键词 | 不抽取，无模型调用 | 只有手动隐藏 | 可视化界面 | 纯关键词召回，没有语义 |

## feiyu-memgate 从这些调研里采纳了什么

报告给出的借鉴建议，以及 feiyu-memgate 里对应的已有功能：

| 调研里的建议或教训 | feiyu-memgate 里的对应 |
|---|---|
| 灵枢：写入前脱敏，每轮注入有硬上限，不静默丢内容 | 写入在存储前先脱敏；召回输出有条数和字数的硬上限，且只放整条，不截断半条 |
| 召回内容回到上下文时要和指令分开（一般性的设计考虑） | 记忆当作数据而不是指令：放进带标签的块里，记忆文本里出现的标签会被转义 |
| meow 的来源确认、SeekMemory 的披露闸门与 subject_key：管住"谁的记忆能被谁用" | 按令牌授权，决定一个客户端能读写哪些存储；租户（例如每个群聊）使用相互分开的存储 |
| SeekMemory、ido1um：查不到时要给诊断，不要静默返回空 | 查询失败与查不到结果分开处理并计数 |
| SeekMemory、mnemon：后台开销要有上限、成本可预测 | 每天有写入次数上限 |
| ido1um、SeekMemory：写入要宽进严出、可追踪 | 写入结果被跟踪并如实报告，区分"已交给后端"和"已保存" |
| 召回结果去重（多个项目的去重整理思路） | 近似重复的结果会合并 |
| 成本方面：稳定的前缀才能命中提示缓存 | 召回块附在当前这条消息上，不放进系统提示词，以保住提示缓存 |
| 报告反复指出：没有可复现的召回评测，各家分数口径不一 | 提供评测工具，用来选召回分数阈值；仓库里有一份对九个系统的检索基准 |
| 导出和备份的副本也要保护 | 加密备份 |
| 报告结论：语义检索（OpenViking）与零模型的词面检索各有取舍，没有必要二选一 | 后端可插拔：向量后端接 OpenViking 或自带的 embedding、hybrid，也有不需要模型的关键词 SQLite 后端 |
| SurfCore 的"衰减注入卡加永不删的全量存档"、SeekMemory 的遗忘曲线 | 遗忘曲线（可选，默认关闭）：长期没被用到的记忆先降权，再存档后删除，存档可恢复 |

报告建议过、但 feiyu-memgate 目前**还没有实现**的部分（计划中）：

- 公开渠道的过滤与分类流水线：在内容进入记忆之前先分类、筛选，写入门（pass、mark、block）也属于这一类。
- 常驻的"核心记忆"：始终注入的一小块记忆，类似 SurfCore 的常驻记忆卡。
- 带出处、带时间戳的事实：事实时间取原话说出的那一轮，召回带出处锚点（ido1um）；ido1um 的墓碑式修订留痕。

## 阅读时的注意

- 报告里的星数、下载量、播放量都是当时的快照，且三套榜单互不一致，只能当弱信号。
- 带"未核实"的地方就是没有核实，请不要当成结论引用。
- 报告只看功能、成熟度、成本和召回，评价针对代码和文档，不针对作者；如发现与现状不符，以各项目的最新仓库为准。
