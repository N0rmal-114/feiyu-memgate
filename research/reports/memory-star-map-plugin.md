# 「记忆星图」DSH 插件评估

> 调研时间：2026-11-10。结论针对当时的版本，各项目之后可能已有变化。

静态评估：只读网页和源码，没有安装、运行任何东西。源码浅克隆到本地（`--depth 1`，所以看不到提交历史）。

## 1. 是什么 / 在哪

重名很多。搜到至少 5 个第三方项目自称「记忆星图」或叫 `dsh-memory-starmap`，都是个人作者，不是 DSH 官方出品。没有确认用户在应用内看到的是哪一个，下面按可能性列出：

| 候选 | 链接 | 数据来源 | 备注 |
|---|---|---|---|
| A. Snowstupid/dsh-memory-starmap（v0.2.0） | https://github.com/Snowstupid/dsh-memory-starmap | 读 `~/.dsh/sessions` 里的对话存档 | 最像"记忆星图"这个名字：八字形星带，同时给模型提供召回工具。已克隆审读 |
| B. yuyun-git/dsh-meow-constellation（v0.1.0） | https://github.com/yuyun-git/dsh-meow-constellation | 读 meow-memory 的 SQLite（`memory.db`） | 副标题「记忆星图」，3D 星图。已克隆审读 |
| C. instant-beep/dsh-memory-starmap（v1.0.0，私有包 `@local/…`） | https://github.com/instant-beep/dsh-memory-starmap | DSH 记忆系统、会话、skills、`~/.mnemon` | 纯可视化，3D。已克隆审读（`index.js:289-290,670`） |
| D. ggxx001/dsh-memory-starmap | https://github.com/ggxx001/dsh-memory-starmap | 读 `http://127.0.0.1:8801/api/all`（某个"记忆台"本地服务） | 自己不存数据。只看了 README，未读源码 |
| E. tletletle/dsh-mnemos | https://github.com/tletletle/dsh-mnemos | 自己的本地记忆库，设置页里带「记忆星图」标签页 | 是完整记忆插件，不只是可视化。只看了 README |

另有相近的非星图记忆插件：dsh-memory（Starry0214）、dsh-meow-memory、dsh-mnemon、dsh-layered-memory、dsh-memory-engine、dsh-graphmemory、dsh-dejavu-memory（它的图谱界面只在另一个宿主的版本里有）。mem0、Honcho、ReMe、OpenViking 也都有 DSH 集成（来源：搜索结果，未逐个核实）。

下面的深入评估以 A 为主，B、C 作对照。

## 2. 成熟度

- A：0 星、0 fork、MIT、无 release、无 npm 发布证据。GitHub 页显示 27 次提交，最近一次 2026-11-08。作者个人。README 说可以发 npm，但没有证实已发布（未核实）。
- B：0 星、MIT、v0.1.0。README 写了 npm 发布步骤，release 页显示还没发布。
- C：私有包名，最近提交 2026-11-09。
- D、E：0 星；E 的 README 写明多项功能"未实测"，`dsh plugin add` 安装路径没验证过。
- 共同点：全部是刚出现的小项目（几天到十几天），无社区、无长期维护记录。issue 数和贡献者数 WebFetch 没取到（未核实）。

结论：成熟度低，都是"个人作品"级别。

## 3. 怎么工作（A 为准）

- 存储：不建自己的记忆库。读 DSH 的会话存档（`lib/store.js:16,162-169`，`.jsonl.zstd`），建一个索引缓存在插件目录，用户的隐藏/摘要修改存到 `~/.dsh/mobius-memory.json`（`store.js:25,447`）。
- 抽取：没有任何大模型调用，也没有嵌入。分类靠关键词，检索靠字符 bigram + IDF + 同义词扩展，README 明说向量召回没实现。所以抽取成本为 0，但也没有语义理解。
- 召回：注册一个 `mobius_recall` 工具，加一行系统提示的索引（`lib/model.js:1-60`）。只按需调用，默认 800 字预算，返回的是原文摘录，带日期和"谁说的"。
- 星图界面：本机网页 `/mobius/app`，一个 1916 行的单页 `public/index.html`；星点就是对话片段，可搜索、隐藏、改摘要。
- 额外功能：还能在界面里编辑 `~/.dsh/AGENTS.md` 和各工作区的 AGENTS.md（`lib/rules.js:140-170`），写之前做 mtime 检查和 `.bak` 备份。
- 导出/导入/删除：没看到专门的导出导入。"隐藏"只是覆盖层标记，不删原始会话。

B 的做法类似，但数据来自 meow-memory 的 SQLite，只是只读展示（`lib/index.js:17`，`node:sqlite`）。C 是纯展示，约 2900 行客户端画 Canvas。

## 4. 和现在的 OpenViking 方案比

现在的方案：OpenViking v0.5.3 服务（DSH 和 AstrBot 共用、账号分开），用 DeepSeek API 做抽取，本机 CPU 的 bge-m3 做嵌入，加项目自己的 AstrBot 插件（背景见另一份内部报告）。

| 方面 | OpenViking | 记忆星图（A） |
|---|---|---|
| 记忆来源 | LLM 抽取后的结构化记忆，跨会话、跨 DSH 和 AstrBot | 只有 DSH 的原始对话片段，AstrBot 的聊天看不到 |
| 召回 | 向量（bge-m3）+ 服务端检索 | 纯关键词，没有语义 |
| 成本 | 抽取要调 DeepSeek API | 0 |
| 可视化 | 无（服务端自带控制台，是否有图谱未核实） | 有，这是它的强项 |
| 去重/遗忘/整理 | 有 | 没有，只有手动隐藏 |
| 成熟度 | 火山引擎官方，有版本和镜像 | 个人项目，几天龄 |

判断：
- 记忆星图 A 不会让召回更好，反而更弱。它真正新增的只有"可视化 + 手动隐藏/编辑 + 规则文件编辑界面"，召回部分和 OpenViking 重叠且更差。
- 能共存：A 读的是会话存档，不碰 OpenViking，也不替代它。两者同时开，模型会看到两个记忆工具，可能引起召回来源混乱和重复占用上下文，可以只用它的界面、关掉 `mobius_recall`（怎么关掉：未核实，需要看配置）。
- 不能替代 OpenViking：没有跨平台共享、没有语义检索、没有抽取。
- 想看 OpenViking 里的记忆画成图，A、C 都帮不上；D 读的是另一个"记忆台"服务（8801），B 读 meow-memory 的库，格式都对不上。要给 OpenViking 做可视化，得自己写一个读它 REST 的小面板。
- 切换成本：换成 A 等于放弃语义召回和 AstrBot 共享记忆，不值得。仅作为"附加看板"装，成本是一次手动安装加一个本地端口。

## 5. 建议

1. 不要用它替换 OpenViking。
2. 暂时不装。如果只是想看看，C（instant-beep）是纯只读展示；B 也是只读，但数据源是 meow-memory，对当前方案没用；A 功能最全（含规则文件编辑界面），只有需要这个界面时才考虑。
3. 待确认：用户在应用内看到的到底是哪一个（名字、作者）。确认后可对该项目补充评估（D、E 目前只看了 README，B、C 对 AstrBot 无用）。

## 来源

- 搜索结果与各仓库 README（见第 1 节链接）；列表页 https://beancookie.github.io/awesome-dsh-plugin/memory/
- 本地源码：Snowstupid/dsh-memory-starmap（`lib/store.js:16,25,162-169,447`、`lib/rules.js:140-170`、`lib/model.js:1-60`）；yuyun-git/dsh-meow-constellation 的 `lib/index.js:17`；instant-beep/dsh-memory-starmap 的 `index.js:289-290,670`
- 未核实：应用内市场的具体条目、各仓库的 issue 和贡献者数、npm 是否已发布、D 和 E 的源码行为。
