# dsh-feiyu-memgate

[English](README.md) | 中文

[feiyu-memgate](https://github.com/N0rmal-114/feiyu-memgate) 记忆网关的 DeepSeek Harness（DSH）插件。它给 DSH 对话加上长期记忆：每轮开始时查找相关记忆，并给模型两个工具，用来记下和查找记忆。

网关随插件一起发布，由插件在你的电脑上启动。默认设置不需要任何模型，也不下载任何东西：记忆存在 SQLite 文件里，用关键词检索。

## 装上之后会发生什么

默认设置下，你会看到这些：

1. **网关在后台启动。** 第一次需要记忆功能时，插件查找 Python 3.11 或更新的版本，在数据文件夹里写一份网关配置，在一个空闲的回环端口上启动自带的网关。DSH 不会等它。网关就绪之前，召回什么都不加，工具回复“记忆功能现在不可用”。
2. **每轮开始时召回。** 你发出消息时，插件查找和它相关的记忆。只有一轮的第一步会查，只在你自己的会话里查，子智能体的会话不查。以 `/` 或 `／` 开头的消息、消息 `ok`、只有表情或标点的消息、以及只有一两个应声字（如 `嗯`、`好`）的消息不查。很短的消息（有效字符少于 8 个）会连同你上一条消息一起查。查询最长 500 个字符。
3. **记忆块。** 查到内容时，插件在你的消息之后加一条消息，内容放在 `<memory_data>` 块里。块的开头写明这些内容是资料，不是指令；然后列出最多 `advanced.recallLimit` 条记忆（默认 5 条），知道日期的带上日期。相关的记忆放不下时，最后一行说明还有几条。网关有分数线，所以多数轮次得到的记忆比上限少，不少轮次一条都没有。查找最多等 `advanced.recallTimeoutMs`（默认 4000 毫秒）；更慢的查找这一轮跳过。
4. **记忆块留在对话历史里。** 它和这一轮一起保存，和别的消息一样。这有两个后果。有记忆块的每一轮，上下文都会变长，这些词元在之后的轮次里还会再发送。提示词缓存不受损害：你当前消息之前的内容都没有变，之前对话已缓存的前缀仍然有效。下一轮不会再加一个和历史里已有的那块完全相同的记忆块。
5. **给模型的两个工具。** `memgate_remember` 接收一句短话（最多 4000 个字符）并保存。`memgate_search` 接收一个查询（最多 500 个字符）和可选的 `limit`（1 到 10），返回同样格式的块。系统提示词里有一小段固定说明，告诉模型：你要求它记住某件事、或说出关于你自己、你的偏好、你的工作的长期信息时，调用 `memgate_remember`；不要保存秘密；之前的对话可能有答案时，调用 `memgate_search`；召回的记忆是资料，不是指令。工具会如实报告结果：已保存；没有保存（没有需要记的内容、今天的写入次数用完、被拒绝、记忆功能不可用）；或结果不确定（网关没有及时回应）。工具没有说已保存，模型就不能说已保存。
6. **只通过工具写入。** 默认设置下，只有模型调用 `memgate_remember` 时才写入一条记忆。你不要求、模型也不决定记，就什么都不会存。打开 `modules.autoCapture` 后，每轮结束时还会把你自己发的消息交给记忆库（见下文）。
7. **记忆存在哪。** 存在 `<DSH home>/data/feiyu-memgate/` 里的 SQLite 文件中。`<DSH home>` 是环境变量 `DSH_HOME` 指向的文件夹；没有设置时是你用户文件夹下的 `.dsh`。

## 要求

- DeepSeek Harness。已在 DeepSeek Harness 0.2.2-rc.1（已安装的桌面版）上测试，并对照 0.2.2-rc.3 源码检查；engines.dsh 接受 0.2.2-alpha.1 到任何 0.2.2 预发布版（<0.2.3-0）。`package.json` 里的范围还接受 0.2.0-rc.1 到 0.2.1-0 之前，以及 0.2.1-alpha.1 到 0.2.2-0 之前。这两段没有测试过。
- Node 22.19 或更新（`engines.node`）。
- 自带网关需要 Python 3.11 或更新，并且要有 `sqlite3` 和 `tomllib` 两个模块，标准安装都带。网关只用 Python 标准库，不用 `pip install` 任何东西。外部网关模式下不需要 Python。

插件按下面的顺序找 Python，用第一个能用的：

1. 设置 `gateway.python`，不为空时。
2. 桌面版自带的 Python：环境变量 `DSH_PRIMARY_RUNTIME` 指向的文件夹（设置了的话），其下 Windows 是 `dependencies/python/python.exe`，其他系统是 `dependencies/python/bin/python3`。
3. 桌面版首次使用时安装的那一份：`<DSH home>/dsh-runtimes/dsh-primary-runtime/`，其下的文件路径同上。
4. `PATH` 里的 `python3`。
5. `PATH` 里的 `python`。
6. `PATH` 里的 `py -3`。

候选项必须能运行、版本是 3.11 或更新、并且有那两个模块才会被采用。一个都找不到时，网关不启动，日志里有一条警告，召回什么都不加，工具回复记忆功能不可用。DSH 和你的对话不受影响。查找结果在 DSH 重启之前一直保留；改动 `gateway.python` 会重新查找。

## 安装

在插件页面安装包 `dsh-feiyu-memgate`。包自带一行默认设置的配置，不需要自己编辑任何东西。

用命令行安装，对你使用的 profile 运行：

```sh
dsh plugin --profile <name> add dsh-feiyu-memgate
```

桌面版的 profile 是 `desktop`。先启动一次桌面版，让它创建 profile，然后完全退出应用，运行下面的命令，再重新启动桌面版：

```sh
dsh plugin --profile desktop add dsh-feiyu-memgate
```

## 设置

每项设置都有三种修改方式，写入的是同一份值。

- **设置页面。** 在插件页面打开本插件那一行。页面显示各项设置和一个状态块（网关、Python、存储和记忆条数、检索方式、向量插件、复制进度）。页面依赖宿主里仍在变化的界面接口；在你的 DSH 版本上用不了的话，用另外两种方式。
- **`/memgate` 聊天命令。** 见[/memgate 命令](#memgate-命令)。
- **profile 的配置文件。** profile 的 `cordis.patch.yml` 里的 `feiyu-memgate` 这一行。另外两种方式也把改动存在这里。家目录级的补丁或 `--patch` 覆盖层如果设置了同一行，它们优先；会被它们盖掉的改动会被拒绝。

多数设置立即生效。改动 `gateway.mode`、`gateway.python`、`gateway.dataDir`、检索方式或正在使用的向量接口设置、`advanced.profile`、`advanced.dailyWrites`、`advanced.redaction`、`advanced.newerFirst`、`advanced.forgetting` 会重启网关，每日写入上限的计数随之重新开始。关掉一项功能，它在 DSH 里注册的东西就被移除。只有 `modules.autoRecall`、`modules.rememberTool`、`modules.searchTool`、`modules.autoCapture` 中至少有一个开着时，网关才会运行。

| 设置 | 作用 | 默认值 | 备注 |
|---|---|---|---|
| `modules.autoRecall` | 每轮开始时查找相关记忆，放在你的消息之后。 | 开 | `/memgate on autoRecall` |
| `modules.rememberTool` | 给模型 `memgate_remember` 工具。 | 开 | |
| `modules.searchTool` | 给模型 `memgate_search` 工具。 | 开 | |
| `modules.promptSection` | 在系统提示词里加一小段关于记忆的固定说明。 | 开 | 不管其他设置怎样，这段文字都一样，所以不会扰乱提示词缓存。 |
| `modules.autoCapture` | 每轮结束后，把这一轮你输入的消息直接交给记忆库，不经过模型。 | 关 | 只有你自己的消息，不含召回的块、注入的上下文和子智能体的轮次。超过 4000 个字符的消息跳过，不截断。每次写入都计入 `advanced.dailyWrites`。 |
| `modules.commands` | 注册 `/memgate` 聊天命令。 | 开 | 需要支持聊天命令的宿主。 |
| `modules.migrateOnTierChange` | 向量检索启用时，在后台把关键词记忆复制到它的存储里。 | 开 | 见[语义检索](#语义检索可选插件)。 |
| `retrieval.tier` | 自带网关怎么查找记忆：`auto`、`keyword`、`endpoint` 或 `local`。 | `auto` | `/memgate use <方式>`。见下表。 |
| `retrieval.url` | `endpoint` 方式：OpenAI 兼容向量接口的基础地址，例如 `http://127.0.0.1:11434/v1`。 | 空 | 必须以 `http://` 或 `https://` 开头。 |
| `retrieval.model` | `endpoint` 方式：向量模型的名字。 | 空 | 换模型会用一个新的空存储。 |
| `retrieval.apiKey` | `endpoint` 方式：接口需要密钥时填写。 | 空 | 秘密。只写不读：设置页面保存后不再显示。 |
| `gateway.mode` | `managed` 启动自带的网关；`external` 使用你自己运行的网关。 | `managed` | 见[外部网关](#外部网关)。 |
| `gateway.python` | `managed`：使用哪个 Python（3.11 或更新）。留空时按“要求”一节的顺序查找。 | 空 | |
| `gateway.dataDir` | `managed`：存放记忆库、生成的网关配置和日志的文件夹。 | 空（`<DSH home>/data/feiyu-memgate`） | |
| `external.url` | `external`：网关地址，例如 `http://127.0.0.1:8080`。 | 空 | 必须以 `http://` 或 `https://` 开头。 |
| `external.token` | `external`：那个网关给这个客户端的令牌。 | 空 | 秘密。只写不读。 |
| `advanced.recallLimit` | 每轮最多加入几条记忆（1 到 10），也是 `memgate_search` 默认返回的条数。 | 5 | 你自己运行的网关同样适用。 |
| `advanced.recallTimeoutMs` | 每轮最多等自动召回多少毫秒（500 到 20000）。更慢的查找跳过。 | 4000 | |
| `advanced.language` | 记忆块、工具回复和 `/memgate` 用的语言：`auto`（跟随系统语言）、`zh` 或 `en`。 | `auto` | 记忆本身不会被翻译。 |
| `advanced.profile` | 网关判断“相关”的严格程度：`broad` 或 `precise`。 | `broad` | `broad` 每轮返回的条数多，漏得少，杂讯也多；记忆块会留在对话里，所以上下文也涨得更快。`precise` 每轮约一条、更贴题，漏得也多。0.1.1 及之前默认是 `precise`，想要原来的行为就改回 `precise`。实测数字见网关文档的[部署档位](https://github.com/N0rmal-114/feiyu-memgate#部署档位)；在关键词存储上，网关实测换成 `broad` 的收益很小。只用于 managed 模式。 |
| `advanced.dailyWrites` | 网关每天最多收下几条记忆（0 到 100000），0 表示不限。 | 50 | 一天从本地时间零点算起。网关每次重启，计数重新开始。只用于 managed 模式。 |
| `advanced.redaction` | 保存和召回记忆时，去掉密钥、令牌、密码这类秘密。 | 开 | 基于模式匹配，认不出旁边没有关键词提示的孤立秘密。只用于 managed 模式。 |
| `advanced.newerFirst` | 相关度差不多时，较新的记忆排在前面。 | 开 | 只看日期，不判断两条记忆是否矛盾。只用于 managed 模式。 |
| `advanced.forgetting` | 实验性。很久没用到的记忆排得更靠后，最终存档并删除。 | 关 | 见下文。只用于 managed 模式。 |

### 检索方式

每种方式在数据文件夹里各用一个存储文件。`/memgate tiers` 列出各种方式、是否可用、还缺什么。正在使用的方式缺东西时，网关不启动，记忆功能保持关闭，直到补齐。

| 方式 | 怎么查找记忆 | 需要 | 存储文件 |
|---|---|---|---|
| `auto` | `dsh-feiyu-memgate-embed` 插件有模型就绪时用 `local`，否则用 `keyword`。 | 无。 | 它所选方式的文件。 |
| `keyword` | 网关里基于 SQLite 的关键词检索。不需要模型，不需要网络。 | 无。 | `memories.db` |
| `endpoint` | 通过 OpenAI 兼容的向量接口做语义检索。记忆文本和查询会发给这个接口。 | `retrieval.url` 和 `retrieval.model`；接口需要密钥时还要 `retrieval.apiKey`。 | `memories-endpoint-<model>.db` |
| `local` | 用向量插件在本机运行的模型做语义检索。 | 安装并启用向量插件，并且有模型就绪。 | `memories-local-<model>-<hash>.db` |

向量插件消失或它的模型变了，`auto` 或 `local` 下的网关会按新状态重启。

### 外部网关

`gateway.mode` 设为 `external` 时，插件什么都不启动，只用 `external.token` 里的客户端令牌连接 `external.url` 的网关。两项有一项为空，记忆功能就关闭，并在日志里记一条警告。这个模式下，`retrieval.*`、`gateway.python`、`gateway.dataDir`、复制到向量存储，以及 `advanced` 里的网关规则（`profile`、`dailyWrites`、`redaction`、`newerFirst`、`forgetting`）都不起作用：网关按它自己的配置运行。`advanced.recallLimit`、`advanced.recallTimeoutMs`、`advanced.language` 仍然有效。

几个程序共用一个网关，或者你想让存储密钥不进 DSH 进程时，优先用它。网关说的是明文 HTTP，所以只在本机或可信的网络里用，或者在前面加 TLS。插件不跟随重定向，令牌不会发到别的地址。

### 高级：遗忘

`advanced.forgetting` 是实验性功能，默认关闭。打开后，网关把很久没用到的记忆排得更靠后。按网关的默认值，一条记忆大约 389 天没被用到就会存档并删除，每次清理每个存储最多删 5 条和库内条数 2% 中较大的那个，一天清理一次。存档的文本以明文保留在数据文件夹里 90 天，之后删除。第一次清理在网关启动 24 小时之后。“用到”指在一次召回里排在靠前的位置被返回；插件不会上报模型实际用了哪几条。这个功能只在网关的模拟里测过。插件没有恢复存档记忆的命令。

### /memgate 命令

给你用的聊天命令，只能写一行。键名写成 `group.key`；只写 `recallLimit` 这样的短键名，在它只对应一项设置时也可以（`url` 不行，`retrieval` 和 `external` 里都有）。键名不分大小写。开关的值接受 `on`、`off`、`true`、`false`、`yes`、`no`、`1`、`0`，以及 `开`、`关`、`是`、`否`。你设置的秘密不会在回复里重复，命令行也不会写进会话记录。

| 子命令 | 作用 | 示例 |
|---|---|---|
| `status` | 网关状态、找到的 Python、存储和记忆条数、检索方式、向量插件、复制进度、已开的功能。 | `/memgate status` |
| `config` | 每项设置的当前值和默认值。秘密只显示已设置或未设置。 | `/memgate config` |
| `set <键> <值>` | 修改一项设置。 | `/memgate set advanced.recallLimit 3` |
| `reset <键>` | 把一项设置恢复默认（要清空文本设置，也用它）。 | `/memgate reset retrieval.apiKey` |
| `on <功能>` | 打开一项功能。功能就是 `modules.*` 设置。 | `/memgate on autoCapture` |
| `off <功能>` | 关闭一项功能。 | `/memgate off searchTool` |
| `tiers` | 各种检索方式：是否可用、正在用哪个、各需要什么。 | `/memgate tiers` |
| `use <方式>` | 选择检索方式：`auto`、`keyword`、`endpoint` 或 `local`。 | `/memgate use keyword` |
| `models` | 本地向量模型：下载大小、内存、语言、许可和状态。 | `/memgate models` |
| `install <模型> [--accept-licence <许可名称>] [--confirm]` | 下载一个模型并启用。没有向量插件时会先安装插件，这一步需要 `--confirm`（不带它时，回复会说明将要安装什么）。 | `/memgate install bge-small-zh-v1.5 --confirm` |
| `builds allow` / `builds deny` | 回答插件安装时的安装脚本问题。 | `/memgate builds deny` |
| `use <模型>` | 切换到一个已下载的模型。 | `/memgate use bge-m3` |
| `remove <模型>` | 删除一个没在使用的已下载模型。 | `/memgate remove bge-m3` |
| `cancel` | 停止正在进行的插件安装或模型下载，或者去掉一个还没回答的安装脚本问题。 | `/memgate cancel` |

模型相关的子命令和设置页面上的按钮做的是同一件事，见[在设置页面安装](#在设置页面安装)。

不带参数的 `/memgate` 和 `/memgate help` 会打印用法。

### profile 配置文件

profile 的 `cordis.patch.yml` 里，一行会整个替换同一个 `id` 那一行的 `config`，所以要把想和默认值不同的各组都写上，不改的组不写。例如，调大召回条数并打开自动记录：

```yaml
- id: feiyu-memgate
  config:
    modules:
      autoCapture: true
    advanced:
      recallLimit: 8
```

不要编辑包里自带的 `cordis.patch.yml`，它只负责用默认设置挂载插件。

## 语义检索（可选插件）

关键词检索只能找到和问题有共同用词的记忆。想连换了说法的也找到，就安装附加插件 [`dsh-feiyu-memgate-embed`](https://github.com/N0rmal-114/feiyu-memgate/tree/main/plugins/dsh-feiyu-memgate-embed)。它只在 CPU 上运行一个本地向量模型，在 `127.0.0.1` 上提供服务，再提供给本插件。模型只在你要求时才下载，文本不离开你的电脑。在 Windows 上，它的引擎需要 Microsoft Visual C++ 2015-2022 运行库（x64），大多数 Windows 已经装有。

安装它会带来这些变化：

- `retrieval.tier` 为 `auto` 时，向量插件里有模型就绪后，本插件从 `keyword` 切换到 `local`，网关用新的存储重启。模型不再就绪时，`auto` 退回 `keyword`。
- **已有的记忆在第一次启用时复制到向量存储。** 这是迁移模块，由 `modules.migrateOnTierChange` 控制。网关在向量检索（`local` 或 `endpoint`）下运行起来后，插件在后台把关键词存储（`memories.db`）里的记忆复制过去，分批进行（每批最多 500 条、最长 120 秒），只要每批都有进展就接着复制。数据文件夹里的状态文件 `migration-<store id>.json` 记录已复制的内容，所以中断的复制在重启后接着做。关键词存储只读，不会被改动。
- 进度看 `/memgate status`（“从关键词存储复制”一行：已复制 / 总数，以及被拒收的条数），或设置页面的状态块。复制还没走到的记忆，语义检索找不到。
- **比模型输入上限长的记忆，只按开头部分被检索到。** 网关每条记忆最多收 4000 个字符；模型的上限是 512 个词元（两个 bge-zh 模型，大约 500 个汉字）、2048（bge-m3）或 4095（qwen3-embedding）。默认情况下，向量插件只对放得下的部分做向量，网关校准时长文本也是这样测的，记忆全文照样保存和返回。把向量插件的设置 `model.overflow` 改成 `reject` 后，这样的文本会被拒收：复制时记为拒收，只留在关键词存储里，不会再交给这个向量存储。某一批里模型一条都不收时，复制停下，等网关下次启动再试。
- **在一种检索方式下写的记忆，和另一种方式的记忆分开存放。** 每种方式、每个模型各有自己的文件。在向量检索下保存的文本不会复制回关键词存储；换一个新模型时，它得到的只是关键词存储的复制，不含上一个模型存储里的内容。切回某种方式，仍能找到那种方式下的记忆。

`endpoint` 方式用的是你自己选的向量接口，例如本机的 Ollama 或云端服务，复制的做法相同。这时记忆文本和查询会发给那个接口（见下一节）。

### 在设置页面安装

设置页面有一节“本地向量模型”，不管向量插件装没装都会显示。每一行给出模型的档位、下载大小、运行内存、语言、许可和一句说明，并链接到[网关的部署档位](https://github.com/N0rmal-114/feiyu-memgate#部署档位)，那里有实测效果。运行内存是向量插件实测的模型进程加载后的内存（bge-small-zh-v1.5：文件 48 MB，运行时约 190 MB），不是文件大小。不按按钮就什么都不下载。在对话里用 `/memgate models` 等模型子命令也一样。

按一次“下载并启用”会发生这些事：

1. **没有向量插件时，页面先请你确认要安装的东西**：通过宿主的插件管理器从 npm 安装 `dsh-feiyu-memgate-embed`，它会带来一个含原生二进制的依赖（`node-llama-cpp`，CPU 版约 90 MB；在 Windows 和 Linux x64 上 pnpm 还会取 GPU 版本，最多约 740 MB）。模型只在 CPU 上运行；在 Windows 上，引擎需要 Microsoft Visual C++ 2015-2022 运行库（x64）。宿主本身不会为此弹出对话框，所以这是唯一的一次确认。
2. **pnpm 会先拦下 `node-llama-cpp` 的安装脚本，等你决定。** 这时什么都还没装：宿主把配置的 `package.json` 恢复原样，只在配置的 `pnpm-workspace.yaml` 的 `allowBuilds` 下写一行 `node-llama-cpp: set this to true or false`。页面会问你是否允许这个脚本。据插件作者测试，两种选择都能用，因为引擎不需要这个脚本。
   - **允许**：在这个 DSH 配置里留下长期许可，重新安装并启用插件。
   - **拒绝**：DSH 里没有按钮。页面会说出文件 `<DSH home>/profiles/<配置名>/pnpm-workspace.yaml`（显示你配置的实际路径）和要改的那一行：把 `node-llama-cpp: set this to true or false` 改成 `node-llama-cpp: false`，保存，然后再按一次“下载并启用”。插件会跳过这个脚本装好并启用。
   - 不回答也不会改动别的东西：插件的任何部分都没有安装或启用，宿主只在以后的安装里才读那一行。按“取消”会把问题从页面上去掉。

   没有你的回答，插件绝不替你允许脚本，也不改配置里的文件。
3. **下载模型**，并显示进度；许可需要接受的模型会有一个勾选框。模型就绪后，`auto` 切换到 `local`，关键词记忆按上文所述复制过去。状态块会显示检索方式和复制进度。

如果向量插件已经是配置的依赖但没有启用，比如一次停在安装脚本上的 `dsh plugin add` 之后，这个按钮会通过插件管理器启用它，而不是再装一次；如果它的文件已经不在了，就先移除再重新安装。

关掉或刷新页面不会打断设置。下载途中 DSH 重启的话，向量插件会保留已下载的部分，这一行会显示“继续下载”。失败会用平常话说明，并告诉你该怎么做：没有网络或镜像连不上、磁盘空间不够、下载文件校验失败、引擎加载不了（在 Windows 上：从微软安装 Microsoft Visual C++ 2015-2022 运行库（x64），即 `vc_redist.x64.exe`，然后重启 DSH；向量插件的状态会显示错误 `runtime-missing`）、网关找不到 Python、你没有确认安装、宿主启用不了插件、或者模型在向量插件自己的设置里被固定了。只有你能开始安装：模型的工具做不到，设置页面的接口也只接受已登录的所有者，并且要带上明确的确认。

## 数据与隐私

- **默认模式下一切都留在你的电脑上。** 自带的网关、存储和日志都在本地。插件除了访问自己网关的回环地址，不发出任何网络请求。除非你在设置页面或用 `/memgate install` 安装向量插件或模型，否则不下载任何东西；那时由宿主的插件管理器从 npm 取向量插件，再由向量插件下载模型。
- **文件。** 都在数据文件夹里，没有设置 `gateway.dataDir` 时是 `<DSH home>/data/feiyu-memgate/`：

| 文件 | 是什么 |
|---|---|
| `memories.db` | 关键词存储。 |
| `memories-endpoint-<model>.db`、`memories-local-<model>-<hash>.db` | 向量检索的存储，每个模型一个。 |
| `migration-<store id>.json` | 向量存储的复制进度：只有数量和引用，没有文本。 |
| `model-setup.json` | 从设置页面或 `/memgate install` 开始的模型设置进度：模型、进行到哪一步、等你决定的安装脚本、上次失败的代码。 |
| `forgetting-ledger-<store id>.db`、`forgetting-archive-<store id>.db` | 只在打开遗忘时出现。存档里是被删记忆的全文，没有加密。 |
| `gateway.log`、`gateway.log.1` | 网关日志，大约 1 MiB 轮转一次。 |
| `gateway-<pid>-<n>.toml` | 正在运行的进程的网关配置，进程停止时删除。里面没有令牌，也没有密钥。 |
| `pycache/` | 网关的 Python 字节码缓存。 |

- **插件不做加密。** 存储是普通的 SQLite 文件。像保护任何存着个人笔记的文件一样保护这个文件夹。
- **网关只在本机。** 它监听 `127.0.0.1`，端口每次启动时随机选一个空闲的。每次启动，插件生成一个新的随机令牌（32 字节），只存在内存里和网关的环境变量里，从不写进文件或日志。网关进程拿到的是 DSH 环境变量的副本，去掉名字含 `KEY`、`PASSWORD`、`SECRET`、`TOKEN` 的，也去掉以 `DSH_` 开头的，再加上它需要的令牌，以及 `endpoint` 方式下需要的密钥。DSH 退出时它也退出。
- **召回的记忆会发给你的模型服务商。** 记忆块是提示词的一部分，所以它和别的消息一样，会发给这段对话使用的模型服务。
- **`endpoint` 模式下离开电脑的内容。** 每条保存或复制的记忆的文本，以及每次召回的查询，都会发给你在 `retrieval.url` 里配置的向量接口。接口密钥通过环境变量交给网关，不写进文件。`external` 模式下，请求发往 `external.url` 里的地址。
- **日志只记失败的种类。** 插件不会在日志里写查询、记忆文本或令牌。
- **删除全部数据。** 退出 DSH（或把插件关掉），然后删除数据文件夹。改过 `gateway.dataDir` 的，删除那个文件夹。如果你让向量插件用它默认的文件夹，这个文件夹里也有向量插件的模型和状态文件，所以只删你想删的文件。

## 限制与没有验证的部分

- **关键词检索找不到换了说法的问题。** 在网关自己的基准测试里（240 条记忆、160 个问题、合成的中文文本），关键词存储在默认设置下，55 个换了说法的问题一个都没有交出正确的记忆；45 个用了记忆原话的问题里交出了 95.6%。语义检索在换了说法的问题上好得多。见[基准测试](https://github.com/N0rmal-114/feiyu-memgate#基准测试)。中文以外的语言没有测过。
- **记忆块留在历史里。** 每个得到记忆块的轮次都会让上下文变长（见上面第 4 点）。
- **只有模型选择保存的记忆才存在**，除非你打开 `modules.autoCapture`。网关按传入的原文存储，打开脱敏时去掉秘密。它不抽取事实，不归并记忆，也不把旧记忆标成过时。较新的记忆只是排在较旧的前面。
- **每日写入上限在内存里计数，按 DSH 进程计。** 网关每次重启就重新开始；第二个 DSH 进程有它自己的网关和自己的计数。
- **每种检索方式只有一个存储。** 插件只用一个网关客户端和每种方式一个存储，不按用户或项目分开保存记忆。
- **设置页面依赖宿主里仍在变化的界面接口。** 没有它插件照样能用：每项设置都能用 `/memgate` 或在 profile 文件里修改。
- **遗忘是实验性的**，只在模拟里测过。
- **真实环境测试。** 在 Windows 11 上已安装的 DeepSeek Harness 0.2.2-rc.1 里，用一个隔离的配置测过：插件能加载，自带网关用 PATH 上的 Python 启动，设置页面能显示和保存，状态接口和模型接口只回应所有者的会话，聊天命令可用；装上向量插件和 bge-small-zh-v1.5 后用的是 local 检索方式，换了说法的问题能找到对应的记忆。通过设置页面从 npm 安装向量插件也测过，测的是拒绝依赖的安装脚本这条路：向量插件装好并启用，模型下载完成，检索方式换成 local（允许脚本的那条路没有跑过）。没有测过：macOS 和 Linux 桌面、桌面应用自己的窗口（用的是同一宿主的 web 配置）、和真实模型的对话（自动召回和两个工具只在测试里和对着网关测过，没有在实际聊天里用过）。
- **其他版本和系统。** 可接受范围内的其他 DSH 版本、其他操作系统和其他 Python 构建，都没有在真实宿主里测过。
- 当前版本是 0.2.0。它改了一个默认值：`advanced.profile` 现在是 `broad`（原来是 `precise`）。

## 故障排查

先看 `/memgate status`。它显示网关的状态、找到的 Python、存储和记忆条数、检索方式、向量插件和复制进度。

- **找不到 Python。** `/memgate status` 里 Python 显示没有找到。安装 Python 3.11 或更新的版本，或者把 `gateway.python` 设成它的完整路径。改动设置会重新查找；如果只是装了 Python，要重启 DSH，因为查找结果在那之前一直保留。也可以改用外部网关。
- **网关启动不了，或不断停止。** 看数据文件夹里的 `gateway.log`。插件会写下“using Python”“gateway starting on port”“gateway healthy”“gateway did not become healthy in time”“gateway exited”这样的行，网关自己的输出紧跟其后。插件等一次启动最多 20 秒。网关崩溃后，插件最多重启它 5 次，间隔越来越长（1 秒，每次翻倍，最长 30 秒）。之后状态是“多次失败后已停止”，直到一次会重启网关的设置改动，或者 DSH 重启。
- **端口或权限问题。** 没有固定端口可冲突：每次启动在 `127.0.0.1` 上选一个空闲端口。有安全软件拦截 Python 的启动或回环地址的监听时，日志里会是 “did not become healthy in time”。插件不能创建或写入数据文件夹时，DSH 日志里会有 “cannot create the data folder” 或 “cannot write the gateway configuration”，记忆功能关闭；把 `gateway.dataDir` 设成你能写入的文件夹。
- **检索方式没有就绪。** `/memgate tiers` 会说缺什么，例如 `endpoint` 缺 `retrieval.url`，或 `local` 缺向量插件里就绪的模型。补齐之前记忆功能保持关闭。`/memgate use keyword` 什么都不需要。
- **本地模型加载不了。** 模型列表会说明原因和怎么办。在 Windows 上，如果向量插件的状态（`/memgate-embed status`）显示 `runtime-missing`，从微软安装 Microsoft Visual C++ 2015-2022 运行库（x64），然后重启 DSH。在此期间 `auto` 继续用 `keyword`。
- **某一轮没有出现记忆。** 可能的原因：存储是空的；消息太短，或是命令；查找用的时间超过了 `advanced.recallTimeoutMs`；同样的块已经在历史里；或者关键词检索没能匹配上换了说法的问题。
- **工具说“没有保存：今天写入记忆的次数已经用完”。** 调高 `advanced.dailyWrites`，或设为 0 表示不限。
- **关掉整个插件。** 在插件页面关掉本插件那一行。或者在 profile 的 `cordis.patch.yml` 里给 `feiyu-memgate` 这一行加 `disabled: true`。
- **只关掉一项功能。** `/memgate off <功能>`，或设置页面上的开关。例如 `/memgate off autoRecall` 保留工具，只停掉自动查找。

## 许可

MIT，见 [LICENSE](LICENSE)。`python/` 里自带的网关是同一作者的项目 [feiyu-memgate](https://github.com/N0rmal-114/feiyu-memgate)，许可相同（[python/LICENSE](python/LICENSE)）。版本是 0.1.0，提交是 `403931fee02961435abe9b09245781afcdcb19de`，记录在 [python/GATEWAY_VERSION.json](python/GATEWAY_VERSION.json) 里。
