# 被测项目的功能事实

> 日期：2026-11-15。每一格只对“基准所用版本”一列写的版本负责，这些项目之后可能已经改变。

这张表把基准里的九个项目（本项目在第一行）放在一起，只写功能事实：是什么形态、怎么检索、默认写入要不要调用大模型、默认交付什么、有没有遗忘或衰减、许可证。它不是评分，也不比较好坏。检索质量的数字见 [基准总表](../summary.md)，每个系统是怎么接入和调用的见各适配器目录里的 README（`bench/adapters/<系统>/README.md`）。本项目自己的设计自查见 [self-check.md](self-check.md)。

依据：[bench/systems.json](../../systems.json)、各适配器的 README、[基准总表](../summary.md) 里每个系统的说明、[research/reports/](../../../research/reports/) 下的调研报告，以及项目自己的公开仓库页面。查不到的写“未确认”，不做推测。机器可读的版本在 [features.json](features.json)。

“注入”指在模型回答前把记忆自动放进提示词；“由模型调用”指记忆只在模型决定调用检索工具时才出现。


| 项目 | 基准所用版本 | 形态 | 怎么检索 | 默认写入是否调用大模型 | 默认交付什么 | 遗忘或衰减 | 许可证 |
|---|---|---|---|---|---|---|---|
| [feiyu-memgate](../../../README.md) | 0.1.0（2026-11-16） | 网关：HTTP 服务，也可以在聊天程序的进程内调用 | 按后端选：关键词（sqlite，字符二元组加 IDF）、向量（embedding）、混合（hybrid，概率“或”融合），或通过 openviking 后端用 OpenViking 的向量检索 | 否。用 openviking 后端时，抽取由 OpenViking 用它自己配置的模型做 | 最多 5 条（上限 10）；有分数线（min_score、relative_score；默认是宽松档 broad，向量和混合配置每题约 1.5 – 2.5 条，`profile = "precise"` 精确档约 0.7 – 1.2 条）；有字数预算（默认 1500 字，整条取舍）；由聊天程序在回复前调用召回（AstrBot 示例每轮回复前自动召回） | 可选的遗忘曲线，默认关闭：长期没被用到的记忆先降权，再存档后删除；存档在保留期内可恢复 | MIT |
| [memsearch](https://github.com/zilliztech/memsearch) | 0.4.23（提交 369ae1da48b7）；DSH 插件 @zilliz/memsearch-dsh 0.1.8 | 库（带命令行）加 DSH 插件 | 混合：稠密向量加 BM25，RRF 融合（默认 Milvus Lite）；记忆存成 Markdown 文件 | 库：否，建索引只算嵌入。DSH 插件采集对话时默认用模型总结每一轮（据项目 README 的配置说明） | 前 5 条；无分数线；无字数预算；插件不自动注入，由模型决定是否调用 memory-recall 技能 | 无衰减或过期；删除跟随 Markdown 文件 | MIT |
| [mem0](https://github.com/mem0ai/mem0) | mem0ai 2.2.1 | 库（进程内调用 Memory 接口） | 混合：语义向量加 BM25 加实体加权；默认写入时先由大模型抽取事实，存的是事实 | 是：add 的正常路径（infer=True）每次调用一次大模型；infer=False 只算嵌入 | top_k 20；分数线 0.1；无字数预算；不注入，提示词由应用自己拼 | 无自动衰减；可选按条到期日（expiration_date） | 未确认 |
| [OpenViking](https://github.com/volcengine/OpenViking) | v0.5.3（容器镜像按 digest 固定） | 服务（HTTP），另有 DSH 插件 | 向量检索记忆文件（本基准用 bge-m3）；会话提交时由大模型抽取记忆 | 是：正常写入（会话提交）由配置的模型抽取；直接写记忆文件（本基准的做法）不调模型 | API 默认 10 条；无分数线；字数预算：未确认；DSH 插件的默认值和是否自动注入：未确认 | 未确认 | 未确认 |
| [Graphiti](https://github.com/getzep/graphiti) | graphiti-core 0.31.2（嵌入式 Kuzu 驱动） | 库 | 图：大模型从文本抽取实体和带时间的事实边，在事实边上做 BM25 加向量的混合检索（RRF） | 是：没有大模型写不进去（本基准实测每条记忆约 2.5 次调用） | 默认 10 条；有余弦下限 0.6；无 token 预算；返回事实边，提示词由调用方拼 | 无时间衰减；被新事实否定的旧事实标记失效，不删除 | Apache-2.0 |
| [SeekMemory](https://github.com/NeverENG/SeekMemory) | 提交 794ec5d | 宿主无关的内核库加宿主适配器（AstrBot；DSH 适配器 0.0.1） | 关键词：BM25 加图激活，无向量检索 | 是：正常写入路径是宿主模型抽取（有每日次数上限），另有夜间整理 | 最多 8 条；有证据阈值（相对最高分）；有字数预算（600 字）；宿主每轮自动注入 | 遗忘曲线 R(t)=exp(-t/S)；定期清扫把保持率低于 0.3 的非 fact 记忆标为遗忘（行保留，可逆）；fact 级不遗忘 | Apache-2.0 |
| [dsh-mnemon](https://github.com/omdsh-dev/dsh-mnemon) | 0.5.45（提交 1dec529）；默认后端 mnemon CLI 0.2.13 | DSH 插件 | 关键词加实体加图信号（SQLite）；向量是可选项，默认关闭 | 是：默认写入路径是大模型驱动的空闲复盘 | 运行时记忆（USER.md、MEMORY.md）的快照在记忆变化时自动追加进对话；记忆空间由模型调用召回工具，每次最多 4 条、3600 字，有分数线（默认策略丢掉 0.25 以下） | 按 id 软删除；容量满时自动裁掉最弱的记忆；时间衰减：未确认 | 未确认 |
| [lingshu (dsh-memory)](https://github.com/FuRongJun-1999/dsh-memory) | npm @furongjun1999/dsh-memory 0.10.1 | DSH 插件（Node，带一个 Python 子进程） | 关键词：BM25，无向量；记忆是普通文件 | 否：写入闸门、去重和索引都是规则代码 | 每轮自动注入会话里最新的 4 条（共 1400 字）和最近 10 条消息（900 字），不看问题、无分数线；按问题检索要模型调用 cg 工具 | 只降权和折叠，不自动删除；可以手动软删到回收区 | 未确认 |
| [meow-memory](https://github.com/Phant0Meow/dsh-meow-memory) | npm meow-memory 0.32.6 | DSH 插件 | 关键词：BM25（中文按字二元组），按重要度和时间加权 | 宿主代理的模型通过工具写入并填关键词，不另起模型调用；后台反思和梦境整合另用宿主模型 | 每条用户消息前自动注入关键词命中的前 2 条；无分数线；字数预算：未确认 | 排序时按时间降权，不删除；删除是把状态改成归档（行保留） | MIT |

## 说明

- meow-memory 在 npm 上的包名是 `meow-memory`，GitHub 上的仓库叫 dsh-meow-memory。
- 基准对多数项目绕过了默认的大模型写入路径（逐字入库），只测检索和交付；“默认写入是否调用大模型”一列说的是项目本身的默认行为，不是基准里的做法。每个系统在基准里实际怎么写入，见它的适配器 README。
- OpenViking 的遗忘或衰减：来源项目的设计文档（[docs/design/2026-11-12-origin-audit.md](../../../docs/design/2026-11-12-origin-audit.md)）记有事件时间衰减和按需整理，本基准没有用到，也没有核实，所以写“未确认”。
