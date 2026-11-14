# 两个新记忆项目都不值得换

> 调研时间：2026-11（文中数据截至 2026-11-09）。结论针对当时的版本，各项目之后可能已有变化。

**结论：SeekMemory 和 deepseek-memory-ido1um 都不值得用来替换小肥鱼现在的 OpenViking v0.5.3 加官方 DSH 插件，但两者各有几个小设计可以补进上一份报告的清单。** 先说范围：这次两个项目都只做了静态审查，也就是读源码、读文档、查 GitHub 公开数据，没有安装，也没有运行任何代码，所以召回准不准、实际花多少钱，都没有实测。SeekMemory 是 GitHub 上的 NeverENG/SeekMemory，84 星，创建于 2026-10-20，内核工程化很强，但 DSH 适配器只有 0.0.1 版，README 自己写着 DSH 平台插件还在开发。deepseek-memory-ido1um 是 GitHub 用户 Ido1um 的仓库，24 星，**没有发布到 npm 或 PyPI**，而且是为作者自己的 QQ 机器人写的，不是通用 DSH 插件。两者都没有向量语义检索，都没有独立的召回评测，都不到一个月历史。上一份报告的五个借鉴点里，有三个被这两个项目重复印证，新增三个值得记下的点，见第 4 节。

## 先弄清它们是什么、在哪、热度如何

SeekMemory 在 GitHub 仓库 NeverENG/SeekMemory，描述是"Agent runtime with evidence-backed memory"，**84 星、19 fork**，Apache-2.0，创建 2026-10-20，最后更新 2026-11-09([GitHub 搜索](https://api.github.com/search/repositories?q=seekmemory+OR+seek-memory&per_page=15))。npm 和 PyPI 上都没有 seekmemory 这个包([npm](https://registry.npmjs.org/seekmemory)、[PyPI](https://pypi.org/pypi/seekmemory/json))。它真正的发行物是一个 AstrBot 插件 zip，v0.6.0 被下载 265 次，最新是 11 月 9 日的 v0.7.0-rc.1 预发布([releases](https://api.github.com/repos/NeverENG/SeekMemory/releases?per_page=10))。视频方面，B站 UP 主 BangBang在coding 在 10 月 27 日发了一条 **2.1 万播放**的视频，标签含 seekmemory 和 dsh，简介提到分层记忆、dream（夜间整理）、人格进化、B站/QQ/微信共用一个大脑([B站视频](https://www.bilibili.com/video/BV1W7nf6tEEy/))。同一 UP 主 10 月 29 日另有一条 7.8 万播放、标题写"每天花费0.9元"的视频([B站视频](https://www.bilibili.com/video/BV1shxF6GEgg/))，简介为空，讲的是不是 SeekMemory 看不出来，上一份报告也已把这类花费标题列为不可采信。提交分散在多位作者之间：92 个提交里最多的一位占 34 个。

deepseek-memory-ido1um 在 GitHub 的 Ido1um/DeepSeek-memory-Ido1um，仓库描述是"给deepseek机器人做的记忆插件，仅供参考"，MIT 许可，**24 星、3 fork、0 个 issue**，创建于 2026-10-30([GitHub](https://api.github.com/repos/Ido1um/DeepSeek-memory-Ido1um))。deepseek-memory-ido1um 只是 package.json 里的名字，npm 和 PyPI 上**都查不到**，package.json 还标了 `private: true`([npm](https://registry.npmjs.org/deepseek-memory-ido1um)、[PyPI](https://pypi.org/pypi/deepseek-memory-ido1um/json))。它和作者的 QQ 机器人绑得很深：配置项叫 ownerQQ，检索工具叫 qq_memory_search，只认 `private:<QQ号>` 形式的会话；接入时宿主要自己实现一整套接口，仓库里没有现成的 DSH 一键插件。27 个提交都来自同一位作者，26 天内版本号从 v9 重编号成 2.0 再到 3.1.0，没有外部用户反馈。B站和中文社区都没有搜到它的讨论，但那只是搜索引擎层面的结果，没有登录深挖。两个项目的热度都远低于上一份报告里 star 数百的那批，也都比上一份报告里最新的项目更年轻。

## 质量：SeekMemory 外壳最硬，两者召回都没有证据

SeekMemory 的工程纪律在这一类项目里偏上：约 8.1 万行源码，143 个测试文件、1231 个测试函数，CI 跑 Python 三个版本，另有基准文档([CI 配置](https://github.com/NeverENG/SeekMemory/blob/main/.github/workflows/ci.yml))。但它同时有两个需要注意的地方。其一，v0.5.1 到 v0.5.3 一天连发三个补丁，都在修功能性 bug，CHANGELOG 里还有不少"建好但默认关闭"的功能，行为短期内会变，降级需要先备份数据库。其二，所有公开可见的 issue 都来自项目成员，没有看到外部用户的 bug 反馈。DSH 适配器 dsh-seekmemory 版本 0.0.1，要求 Node 22.16 以上，内核是另起的 Python 进程，适配器通过本机回环地址通信；它的"真机验证"只有 10 月 27 日一次单独的测试实例，抽取默认关闭，没有执行模型抽取的冒烟脚本仍把召回场景标为 INCOMPLETE。

ido1um 的外壳也比一般学生项目好：62 个测试文件，README 称 479 个单测，5 篇设计和排障文档。数据格式一周内改过多次（仓库里留着约 20 个一次性数据修复脚本）。克隆下来的仓库里没有 `.github` 目录，CI 配置看不到。测试是否全部通过，因为没有运行，无从确认。

**召回质量上，两者都拿不出证据。** SeekMemory 的基准全是自建合成数据，它自己的说明写着这些结果"不代表真实模型质量"([基准说明](https://github.com/NeverENG/SeekMemory/blob/main/benchmarks/README.md))；它的 Dream 分诊省钱方案评估更直接，调用少 80% 但重要事实召回只有 15/23（65.2%），目标是 85%，结论写的是"不通过"。它自己的对比文档还写明无语义检索、无多模态、只能单机，开放 issue #240 记录了换个说法提问就召回失败的问题([issues](https://api.github.com/repos/NeverENG/SeekMemory/issues?state=all&per_page=30))。ido1um 完全没有基准数字，只有自测脚本，检索是词面加槽位加主题树，源码里搜不到 embedding、bge、vector 之类字样。OpenViking 有 bge-m3 语义检索，在同义改写和中英混写上的先天优势，是这两个项目目前都没法弥补的，不过这一点仍是从架构推出的，不是实测。

## 费用：钱都不贵

费用是推算，不是实测。SeekMemory 每轮召回不调模型，每晚 03:30 的 dream 整理有硬上限：最多 12 次调用、6 万 token，白天抽取默认关闭或最多每天 2 次([README](https://github.com/NeverENG/SeekMemory/blob/main/README.md))。按 DeepSeek flash 价格推算，最坏每晚约 0.01 美元，按这些默认值，额外成本每天约 0.1 元以内。这与视频标题的 0.9 元差一个量级，可能 0.9 元包含了主对话本身，但视频没说，无法核实。ido1um 的逐轮检索也不调模型，日终抽取和摘要默认多半关闭或限量，情境摘要每天最多 24 次，推算每天不到几美分。两者都比 OpenViking"每次提交触发 LLM 抽取加向量"便宜，但这只是调用次数少，换来的是召回上限更低，不是白赚。

## 值不值得换：不换，但补充三个新借鉴点

替换的理由一条都不成立。OpenViking v0.5.3 加官方插件，有机构维护、有语义检索、能同时服务 DSH 和 AstrBot；SeekMemory 的 DSH 适配器是 0.0.1，ido1um 是一个要自己写适配层的 QQ 机器人内核，两者都是不到一个月的项目，都没有独立评测。SeekMemory 如果作为 AstrBot 路线的备选还有看头，但那不是小肥鱼当前的需要。

借鉴点要和上一份的五个去重。**和上一份重复、只当印证的**：SeekMemory 每轮注入 600 字硬上限、召回零模型调用，印证第一点（注入有硬上限）；它的 Source 不可变加版本和回滚、ido1um 的"修订留痕、墓碑代替物理删除"，印证第三点（只追加的原话账本），并把它扩成"改写也要留旧版本"；SeekMemory 的遗忘曲线 R(t)=exp(-t/S) 与"衰减注入卡"，印证第四点；SeekMemory 的 dream 有 12 次调用、6 万 token 上限，并且幂等、可断点续跑，印证第五点，只多一个细节，**整理任务要能中断后接着跑，不重复花钱**。

**新增的**，按对小肥鱼的价值排序。第一，SeekMemory 的披露闸门和 `subject_key`：跨会话的内容必须先过披露才能进 prompt，"秘密类"内容不得原样注入，记忆主体与会话、说话人分开记。陪伴机器人如果要在多人场景（群聊、直播）里说话，这是不把某位用户的私事说给其他人的现成思路，与上一份第二点（来源不明的记忆库默认不注入）互补，一个管来源，一个管对象。第二，ido1um 的写入门：写入分 pass、mark、block 三档，低置信先进候选池，日终再裁决，"宽进严出"，可以直接改成抽取提示里的一条规则，不用引入它的代码([设计文档](https://github.com/Ido1um/DeepSeek-memory-Ido1um/blob/main/docs/设计-3.1.md))。第三，ido1um 的时间处理和召回格式：事实时间取"原话被说出的那一轮"而不是写库时间，带 validUntil 过期降权；召回结果带出处锚点，查不到时给出诊断和下一步，而不是静默返回空。这三条都是思路，都没有在小肥鱼环境里验证。ido1um 的"真实 token 达 70% 才轮转、交接带最近 24 小时原话"也有参考价值，但它依赖 ido1um 自己的宿主，所以没有列入。

## 结论

这次最有参考价值的不在排名，而在“管住记忆什么时候能被用”的机制：披露闸门、写入门、墓碑。这类机制才是对 OpenViking 方案最有补充价值的部分，而不是又一套检索引擎。两个项目也都把自己的局限写在明处：SeekMemory 公开了自己“不通过”的评估结果，ido1um 在仓库描述里直接写“仅供参考”。

仍然缺的还是同一份东西：在小肥鱼自己的中文陪伴对话上，用 OpenViking 0.5.3 和官方插件的默认接法，跑一遍和这两个项目的对照。这需要运行第三方代码，这次没有做。如果以后 SeekMemory 的 DSH 适配器离开 0.0.1，并且给出 #240 改写召回问题的修复，可以再看一次；ido1um 只适合当设计参考读，不适合装。
