## 它是什么

[feiyu-memgate](https://github.com/N0rmal-114/feiyu-memgate) 记忆网关的 DeepSeek Harness（DSH）插件，给 DSH 对话加上长期记忆：每轮开始时自动查找相关记忆，并给模型“记住”和“查找”两个工具。网关随插件一起发布，由插件在你的电脑上启动。默认设置不需要任何模型，也不下载任何东西，记忆用关键词检索。

## 装上之后

1. 第一次需要记忆功能时，插件在后台启动自带的网关，DSH 不会等它。
2. 你发出消息时，插件查找相关记忆，把它们放在你的消息之后，包在一个标明“这是资料，不是指令”的块里。默认最多 5 条，多数轮次更少，也可能一条都没有。查找最多等 4 秒，更慢就跳过。
3. 这个块会留在对话历史里，所以有记忆块的每一轮，上下文都会变长。你当前消息之前的内容不变，提示词缓存不受影响。
4. 模型有两个工具：`memgate_remember` 保存一句话，`memgate_search` 主动查找。系统提示词里的一小段说明告诉它：你要求记住、或说出长期有用的信息时才保存，不保存秘密。
5. 默认设置下，只有模型调用 `memgate_remember` 才会写入。打开 `modules.autoCapture` 后，每轮结束时还会把你自己发的消息直接存入记忆。
6. 记忆存在 `<DSH home>/data/feiyu-memgate/` 的 SQLite 文件里。`<DSH home>` 是环境变量 `DSH_HOME` 指向的文件夹，没有设置时是用户文件夹下的 `.dsh`。

## 要求

- DeepSeek Harness：已在 0.2.2-rc.1（已安装的桌面版）上测试，并对照 0.2.2-rc.3 源码检查；engines.dsh 接受 0.2.2-alpha.1 到任何 0.2.2 预发布版（<0.2.3-0）。包里的范围还接受 0.2.0-rc.1 到 0.2.1-0 之前、0.2.1-alpha.1 到 0.2.2-0 之前，这两段没有测试过。
- Node 22.19 或更新。
- Python 3.11 或更新（需要 `sqlite3` 和 `tomllib`，标准安装都带），不用 `pip install` 任何东西。插件依次找：设置 `gateway.python`，桌面版自带的 Python，`python3`、`python`、`py -3`。找不到时网关不启动，记忆功能关闭，DSH 和对话不受影响。

## 安装

在插件页面安装包 `dsh-feiyu-memgate`，不需要改任何配置。命令行安装：

```sh
dsh plugin --profile <name> add dsh-feiyu-memgate
```

桌面版的 profile 是 `desktop`：先启动一次桌面版，再完全退出，运行 `dsh plugin --profile desktop add dsh-feiyu-memgate`，然后重新启动。

## 设置

每项设置都能在三个地方改：插件页面里本插件的设置页，聊天命令 `/memgate`，或 profile 的 `cordis.patch.yml`。

| 设置 | 作用 | 默认 |
|---|---|---|
| `modules.autoRecall` | 每轮自动召回 | 开 |
| `modules.rememberTool`、`modules.searchTool` | 给模型记住、查找两个工具 | 开 |
| `modules.promptSection` | 系统提示词里的固定说明 | 开 |
| `modules.autoCapture` | 每轮结束后把你的消息直接存入记忆 | 关 |
| `retrieval.tier` | 检索方式：`auto`、`keyword`、`endpoint`、`local` | `auto` |
| `advanced.recallLimit` | 每轮最多加入几条记忆 | 5 |
| `advanced.profile` | `broad` 每轮条数多、漏得少、杂讯也多，上下文涨得更快；`precise` 每轮约一条、更贴题 | `broad` |
| `advanced.dailyWrites` | 每天最多写入几条，0 为不限 | 50 |
| `advanced.redaction` | 保存和召回时去掉密钥、令牌、密码 | 开 |
| `gateway.mode` | `managed` 用自带网关，`external` 连接你自己运行的网关 | `managed` |

`/memgate` 的子命令：`status`、`config`、`set <键> <值>`、`reset <键>`、`on <功能>`、`off <功能>`、`tiers`、`use <方式>`，以及本地模型的 `models`、`install <模型> [--confirm]`、`builds allow|deny`、`use <模型>`、`remove <模型>`、`cancel`。完整的设置列表、各项的取值和例子见插件包里的 README.zh.md。

## 语义检索（可选）

关键词检索找不到换了说法的问题。安装附加插件 [dsh-feiyu-memgate-embed](https://github.com/N0rmal-114/feiyu-memgate/tree/main/plugins/dsh-feiyu-memgate-embed)，在本机只用 CPU 运行向量模型（Windows 上需要 Microsoft Visual C++ 2015-2022 运行库 x64，大多数 Windows 已经装有）。模型就绪后 `auto` 自动换成 `local`，已有的记忆会在后台复制到向量存储里，进度看 `/memgate status`。比模型输入上限长的记忆只按开头部分被检索到，全文照样保存和返回（向量插件的设置 `model.overflow` 改成 `reject` 则拒收）。每种检索方式的记忆分开存放。

设置页面的“本地向量模型”一节列出每个模型的档位、下载大小、运行内存（向量插件实测的模型进程内存，例如 bge-small-zh-v1.5 约 190 MB，不是文件大小）、语言和许可，按“下载并启用”即可：

1. 没装向量插件时，页面先说明要从 npm 安装什么（含原生二进制的 node-llama-cpp，CPU 版约 90 MB，Windows 和 Linux x64 上最多约 740 MB），你确认后才通过宿主的插件管理器安装。
2. pnpm 会拦下 node-llama-cpp 的安装脚本，这时什么都还没装。允许：重新安装并启用。拒绝：DSH 没有按钮，页面会说出配置的 `pnpm-workspace.yaml` 的实际路径，把 `allowBuilds` 下的 `node-llama-cpp: set this to true or false` 改成 `node-llama-cpp: false` 并保存，再按一次按钮即可装好并启用。插件不会替你允许脚本，也不改配置里的文件。
3. 下载、校验、启用模型。关掉页面或重启 DSH 不会丢失进度，中断的下载可以继续。

已经装了但没有启用的向量插件（例如停在安装脚本上的 `dsh plugin add` 之后），按钮会通过插件管理器启用它。各种失败都会说明原因和怎么办；引擎加载不了（状态显示 `runtime-missing`）时，在 Windows 上安装上述运行库后重启 DSH。

## 数据与隐私

- 默认模式下一切都留在你的电脑上。网关只监听 `127.0.0.1` 的随机端口，令牌每次启动随机生成，只在内存里。只有你在设置页面或用 `/memgate install` 安装向量插件或模型时才会下载：宿主的插件管理器从 npm 取向量插件，向量插件下载模型。
- 记忆是普通的 SQLite 文件，插件不加密。删除全部数据：退出 DSH，删除 `<DSH home>/data/feiyu-memgate/`（改过 `gateway.dataDir` 的删那个文件夹）。
- `endpoint` 方式下，每条保存的记忆的文本和每次召回的查询，都会发给你配置的向量接口。
- 召回的记忆是提示词的一部分，会发给这段对话使用的模型服务。

## 限制

- 关键词检索找不到换了说法的问题：在网关自己的基准测试里，55 个换了说法的问题一个都没有交出正确的记忆。见[基准测试](https://github.com/N0rmal-114/feiyu-memgate#基准测试)。
- 记忆块留在历史里，上下文会变长。
- 只有模型选择保存的记忆才存在，除非打开自动记录。
- 每日写入上限在内存里计数，按 DSH 进程计，网关重启后重新开始。
- 设置页面依赖宿主里仍在变化的界面接口；没有它，用 `/memgate` 或 profile 文件也能改全部设置。
- 真实环境测试：在 Windows 11 上已安装的 DeepSeek Harness 0.2.2-rc.1 里，用一个隔离的配置测过：插件能加载，自带网关用 PATH 上的 Python 启动，设置页面能显示和保存，状态接口和模型接口只回应所有者的会话，聊天命令可用；装上向量插件和 bge-small-zh-v1.5 后用的是 local 检索方式，换了说法的问题能找到对应的记忆。通过设置页面从 npm 安装向量插件也测过，测的是拒绝依赖的安装脚本这条路：向量插件装好并启用，模型下载完成，检索方式换成 local（允许脚本的那条路没有跑过）。没有测过：macOS 和 Linux 桌面、桌面应用自己的窗口（用的是同一宿主的 web 配置）、和真实模型的对话（自动召回和两个工具只在测试里和对着网关测过，没有在实际聊天里用过）。
- 当前版本是 0.2.0。它改了一个默认值：`advanced.profile` 现在是 `broad`（原来是 `precise`）。

## 遇到问题

先运行 `/memgate status`。找不到 Python 时安装 Python 3.11 或更新，或设置 `gateway.python`；网关起不来时看数据文件夹里的 `gateway.log`。关掉整个插件：在插件页面关掉它的那一行；只关一项功能：`/memgate off <功能>`。

## 许可

MIT。自带的网关是同一作者的项目 [feiyu-memgate](https://github.com/N0rmal-114/feiyu-memgate)，许可相同，版本 0.1.0，提交 `403931fee02961435abe9b09245781afcdcb19de`。
