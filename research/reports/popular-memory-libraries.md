# 热门记忆库都没赢过 OpenViking

> 调研时间：2026-11（文中数据截至 2026-11-10）。结论针对当时的版本，各项目之后可能已有变化。
>
> 说明：报告里的"小肥鱼"指 feiyu-memgate 的来源项目（一个陪伴机器人），它当时的记忆方案是 OpenViking v0.5.3 加官方 DSH 插件。

**结论：这次能查到的热门 DSH 记忆库和记忆插件，没有一个值得用来替换小肥鱼现在的 OpenViking v0.5.3 加官方插件。** 先说范围：抖音没有查成。抖音搜索页弹出扫码登录框，结果列表是空的，按规定没有登录也没有绕过，所以抖音的热度、评论、博主一概没有数据。下面的"热门"只来自 B站公开页面，再加 GitHub 和 npm 的公开数据。B站只搜了 8 个关键词里的 4 个，每个只读了第一页。播放量、star、下载量都只是弱信号，三套榜单对不上，star 还涨得很快。热门项目大多是 2 到 12 周龄的个人作品，没有一个有独立评测证明召回比 OpenViking 好。唯一公开的评测题型和接法都和日常陪伴对话不同，不能直接拿来比（第 3 节）。这些项目大多靠词面匹配检索，换个说法就找不到，而 OpenViking 有语义检索。值得借鉴的是几个小设计，下面第 4 节列出。

## 三套榜单互不重合：B站看话题，GitHub 看 star，npm 看装机

B站按播放量最高的记忆类视频，是玛露Marrow 讲 SurfCore 的那条，**80.2 万播放**，其后是凌曦沫呀的"永久保存记忆"21.4 万、渐行渐远的烟火讲 dsh-meow-memory 的 16.0 万、啾灵讲 dsh-core-memory 的 14.6 万。("大肥鱼 记忆"搜索榜首其实是梗视频，不是记忆库。)([SurfCore 视频](https://www.bilibili.com/video/BV1Rbxg6WER5/)、[meow-memory 视频](https://www.bilibili.com/video/BV18Ubv6tEWN/)、[啾灵视频](https://www.bilibili.com/video/BV1Qops6SEuE/))。粉丝只有几百的 UP 主也能做出 5 到 16 万播放，说明传播靠"大肥鱼失忆"这个话题本身，播放量说明不了项目质量。反复出现在多个视频里的是 dsh-agent-soul、dsh-soul、dsh-hippocampus，出现次数多也不代表更好用。

GitHub 的 star 榜是另一副面孔：dsh-mnemon 956 星，dsh-memory-evolve 753 星，adoresever/graph-memory 675 星，灵枢（FuRongJun-1999/dsh-memory）646 星，dsh-meow-memory 383 星([GitHub 搜索](https://api.github.com/search/repositories?q=dsh+memory&sort=stars&per_page=30))。mnemon 和 evolve 两个项目在 8 月 22 日的一篇盘点里分别是 136 星和 205 星，约 11 周涨了 4 到 7 倍([CSDN 盘点](https://deepseek.csdn.net/6a88f272662f9a54cb9f592e.html))，所以 star 只能当热度信号，不能当成熟度。npm 周下载又是第三种排序：dsh-mnemon 36,738，meow-memory 12,248，官方 `@openviking/dsh-memory-plugin` 12,982，灵枢 9,253([npm 搜索](https://registry.npmjs.org/-/v1/search?text=dsh+memory&size=40)、[mnemon 下载](https://api.npmjs.org/downloads/point/last-week/dsh-mnemon))。evolve 和 noema 在 npm 上查不到，只能用 GitHub 直装，没法拿下载量交叉验证。一些盘点文章的"热度排序"实际是 star 排序([CSDN 分类](https://deepseek.csdn.net/6a8e795b10ee7a33f29ee22f.html))。

## 逐个看质量：工程外壳最好的是 mnemon，最贴近陪伴的是 SurfCore，没有一个有召回数据

下表是静态审查（只读源码，没有安装运行）的结果。

| 项目 | 成熟度 | 怎么存和检索 | 额外模型调用 |
|---|---|---|---|
| dsh-mnemon | 956 星，84 个 release，CI 齐全，94.7% 的提交来自一人 | 多后端编排层，可把 OpenViking 当后端 | 默认开着后台"空闲复盘"副会话 |
| dsh-memory-evolve | 753 星，不上 npm，无 CI，10-29 后无提交 | 模型自己写 Markdown，没有向量 | 无后台调用 |
| dsh-meow-memory | 383 星，13 位贡献者，无 CI | SQLite 加 BM25，主 agent 手动写入 | 反思和"做梦"整理 |
| 灵枢 dsh-memory | 646 星，344 个 issue，体量巨大 | 原文落盘加词面检索，需 Python | 插件自身为零 |
| SurfCore | 636 星，约 2 周龄，无 CI | 规则打分加每日衰减，整张记忆卡常驻 | 记忆本身为零 |
| dsh-rofm | 13 星，工程纪律最好 | 压缩时归档原文，2/3-gram 关键词召回 | 评分靠主模型调用工具 |
| dsh-soul | 74 星，15 天内改过 3 次定位 | SQLite 图加全文，面向编码任务，抽取的是任务约束而不是人物记忆 | 默认零，演化模式花钱 |
| dsh-hippocampus | 29 星，只有 2 个提交，许可为非商用 | 原话落盘加词面检索 | 零 |

数据来源见各仓库：[mnemon](https://github.com/omdsh-dev/dsh-mnemon)、[evolve](https://github.com/csyangwen/dsh-memory-evolve)、[meow](https://github.com/Phant0Meow/dsh-meow-memory)、[灵枢](https://github.com/FuRongJun-1999/dsh-memory)、[SurfCore](https://github.com/Mieksido/surfcore)、[rofm](https://github.com/wall1234567/dsh-rofm)、[soul](https://github.com/icyaaaww/dsh-soul)、[hippocampus](https://github.com/coner13/dsh-hippocampus)。dsh-agent-soul 没有进表，因为它不是一个记忆插件，而是约 22 个子插件的大型个人框架，只借鉴思路([仓库](https://github.com/cyanwell/dsh-agent-soul))。

成熟度上，没有一个项目超过半年，也没有一个是多人审查过的成熟库。mnemon 的工程化最完整，有 Windows CI 和发布前校验，但它 0.5.x 里还在修基础安装问题([issues](https://github.com/omdsh-dev/dsh-mnemon/issues))。工作方式上，除了 OpenViking 和 SurfCore 的可选向量模式，其余**全是词面检索**。B站高赞评论正是批评这一点：纯文本记忆依赖模型自觉更新、没有语义匹配、旧记忆会过时漂移([啾灵视频评论区](https://www.bilibili.com/video/BV1Qops6SEuE/))。召回质量方面，三份审查都没有找到任何一个项目对 LongMemEval、LoCoMo 或自建问答集做过独立评测。mnemon 只有 28 条记忆的内部小基准，项目自己标为历史结果([release notes](https://github.com/omdsh-dev/dsh-mnemon))。

费用方面，mnemon 的"频繁整理导致费用暴增"在代码上成立，但不是 bug 那么简单。它的空闲复盘默认开启，每个会话最多 20 次，重启后额度重置，历史上出现过空跑，复盘副会话默认继承主会话的模型([issue 524](https://github.com/omdsh-dev/dsh-mnemon/issues/524)、[issue 525](https://github.com/omdsh-dev/dsh-mnemon/issues/525))。按 DeepSeek 官方 flash 价格估算([定价页](https://api-docs.deepseek.com/quick_start/pricing))，单次复盘约 0.1 到 0.6 元，一个活跃会话一天封顶大约 2 到 11 元，这是推算不是实测。如果主会话用的是 pro 模型，同样次数会贵 3 到 5 倍。其余项目的默认记忆路径基本不额外调用模型，成本只体现为每轮多带几百到两千多字的上下文，不是区分好坏的决定因素。B站上"每天花费 0.9 元"这类视频标题([视频](https://www.bilibili.com/video/BV1shxF6GEgg/))没有可核实的配置，不能采信。

## 唯一的公开评测测的不是陪伴记忆，不能直接比

B站上唯一带评测的是 AGI探索者符蕴道的《DSH 插件测评》，**94 秒**，约 1.4 万播放([视频](https://www.bilibili.com/video/BV1m5nS6BEHm))。视频里列的是灵枢、memsearch、OpenViking、deja-vu、ReMe 加一条朴素 BM25 基线，没有给出每家分数，视频也没有字幕，所以视频里的具体数字无法核实。评测仓库 hive-memory-bench 是公开的，判分是机械比对([仓库](https://github.com/FuRongJun-1999/hive-memory-bench))。成绩表在 2026-11-09 改过一次口径，下面用的是改后的同口径成绩：灵枢约 86%，SurfCore 82.8%，**OpenViking 0.4.22 为 76.6%**，dsh-rofm 62.5%，dsh-soul 50.0%（等于不给材料），上限锚 87.7%。

这个结果不能直接拿来说灵枢比 OpenViking 好。题目是对一部虚构小说做"六要素逐字核对"，考的是多槽位核对，不是日常陪伴对话的记忆；OpenViking 的接法是由评测配置的，取 top-10 整章拼接约 2.5 万字，不是官方插件的默认注入方式；被测版本是 0.4.22，不是小肥鱼在用的 0.5.3；OpenViking 那一行也没有在最新一轮重跑。改口径之前的成绩表里，OpenViking 逐字保真 100%、保地基 68.4% 到 70.0%，与灵枢持平或靠前([评测仓库](https://github.com/FuRongJun-1999/hive-memory-bench))。同一套基准上的名次随口径变化很大，说明它测不出可靠的先后。另外 dsh-hippocampus 和 dsh-agent-soul 根本不在被测名单里。

## 不换，但有五个小设计可以借鉴

小肥鱼（feiyu-memgate 的来源项目）当时的方案是 OpenViking 服务加官方插件，用 DeepSeek 抽取记忆，本机 bge-m3 做向量。官方插件 `@openviking/dsh-memory-plugin` 由 OpenViking 的 GitHub Actions 发布，npm 版本 0.5.34，周下载约 1.3 万，要求另备 OpenViking 服务([npm 搜索](https://registry.npmjs.org/-/v1/search?text=dsh+memory&size=40))。上游仓库约 4.19 万星([GitHub](https://api.github.com/search/repositories?q=topic:dsh-plugin+memory&sort=stars&per_page=30))，是这次看到的唯一由机构维护、有 LLM 抽取加向量检索的路线。热门个人项目在语义召回、跨平台共享（OpenViking 同时服务 DSH 和 AstrBot）和去重整理上都没有超过它的证据，所以没有替换的理由。mnemon 能把 OpenViking 当后端接入，但那等于在已有的 LLM 抽取之外再叠一条复盘链路，费用叠加，而且 mnemon 对 OpenViking 的支持还有未关闭的缺口(#452、#453)。上一轮评估的几个「记忆星图」插件也是同样的结论：都是 0 星小项目，只做可视化，数据源对不上 OpenViking，不构成替代([记忆星图插件评估](memory-star-map-plugin.md))。

可借鉴的有五点，都只是想法，没有在小肥鱼的环境里验证过。第一，灵枢的做法：写入前对密钥、密码、手机号、身份证号做正则脱敏，每轮自动注入有硬字符上限（至多 4 条、1400 字符），读取被截断时一定带"续读"提示，不静默丢内容([灵枢仓库](https://github.com/FuRongJun-1999/dsh-memory))。可以对照检查现有方案有没有同样的做法。第二，meow 的来源确认：来源不明（非本机创建）的记忆库默认不自动注入，要手动确认。第三，hippocampus 的"原话账本"：只追加不改写的原话落盘，不依赖模型记得去记，证据不会被摘要吃掉；它不能替代语义召回，只能当补充，而且许可是非商用([仓库](https://github.com/coner13/dsh-hippocampus))。第四，SurfCore 把"会衰减的注入卡"和"永不删的全量存档"分开，避免遗忘等于丢数据。第五，mnemon 的教训是反面的：后台整理必须有次数上限、固定到便宜模型，并且额度要跨重启保留。

## 结论

热度和质量在这个生态里基本脱钩：播放量最高的 SurfCore 是整个平台而不是记忆库，star 最高的 mnemon 有最好的工程外壳却仍在修基础故障，唯一的公开评测考的不是陪伴对话的记忆。所以"有没有更好的"这个问题，目前证据只能回答"没有看到"，而不是"确定没有"。真正缺的是一份在小肥鱼自己的中文陪伴对话上、用 OpenViking 0.5.3 和官方插件的默认接法跑出来的对照，这需要运行第三方代码，这次没有做。

尚待补充的有两件事：一是登录抖音看一下实际热门的视频和项目名，再对号入座；二是确认 B站上没搜的四个关键词（「大肥鱼 DSH」「大肥鱼 记忆库」「大肥鱼 记忆插件」「DSH 记忆」）有没有遗漏的热门项目。
