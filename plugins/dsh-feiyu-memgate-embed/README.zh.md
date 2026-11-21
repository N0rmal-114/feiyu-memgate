# dsh-feiyu-memgate-embed

[English](README.md)

DeepSeek Harness（DSH）插件 `dsh-feiyu-memgate` 的向量插件。它用
[node-llama-cpp](https://github.com/withcatai/node-llama-cpp) 在 CPU 上运行本地向量模型，并在 `127.0.0.1`
上提供一个 OpenAI 兼容的向量接口给记忆插件。记忆插件的 `local` 检索方式（以及模型就绪时的 `auto`）就会把
记忆存成向量并按向量检索。文本不离开本机，也不需要任何在线服务。

这个插件不会自作主张：插件里不带模型；没选模型时不启动进程、不占端口；只有你要求时才下载模型。通常的做法是在
记忆插件的设置页上点按钮：下载、校验、启用一步完成。`/memgate-embed` 聊天命令是另一种方式。

## 模型

四个模型，对应记忆网关校准表里的四个档位。每个文件都固定在它的 Hugging Face 仓库的某次提交上，并核对大小和
SHA-256。

| 档位 | id | 文件 | 大小 | 加载后内存 / 峰值 | 完整处理 | 许可 |
|---|---|---|---|---|---|---|
| 最轻 | `bge-small-zh-v1.5` | [CompendiumLabs/bge-small-zh-v1.5-gguf](https://huggingface.co/CompendiumLabs/bge-small-zh-v1.5-gguf) `bge-small-zh-v1.5-f16.gguf` | 47.9 MB | 188 MB / 219 MB | 512 token | MIT |
| 轻 | `bge-base-zh-v1.5` | [CompendiumLabs/bge-base-zh-v1.5-gguf](https://huggingface.co/CompendiumLabs/bge-base-zh-v1.5-gguf) `bge-base-zh-v1.5-q8_0.gguf` | 110 MB | 251 MB / 284 MB | 512 token | MIT |
| 中上 | `qwen3-embedding-0.6b` | [Qwen/Qwen3-Embedding-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF) `Qwen3-Embedding-0.6B-Q8_0.gguf` | 639 MB | 1.35 GB / 1.77 GB | 4095 token | Apache-2.0 |
| 最高 | `bge-m3` | [gpustack/bge-m3-GGUF](https://huggingface.co/gpustack/bge-m3-GGUF) `bge-m3-FP16.gguf` | 1.16 GB | 1.58 GB / 1.86 GB | 2048 token | MIT |

内存是 Linux x64 上模型进程的常驻内存；峰值包含一次达到模型上限的长输入。在 Windows 上，`bge-small-zh-v1.5`
测得工作集 139 MB（峰值 174 MB）。“完整处理”是能整段做向量的最长文本，见[长记忆](#长记忆)。各档位的召回效果：
https://github.com/N0rmal-114/feiyu-memgate#部署档位

- 四个文件都与网关校准分数线时用的文件逐字节相同（`parity: exact`），向量也和用同一文件的 Ollama 比对过：60 段
  文本的余弦都不低于 0.9996，20 个测试问题排第一的记忆全部相同。在 Windows 上，`bge-small-zh-v1.5` 与 Ollama
  完全一致（60 段文本的余弦都是 1.000000）。
- 网关的中档 embeddinggemma 不提供：检查过的 GGUF 文件缺少这个模型的两个 Dense 投影层，算出的分数对不上网关的
  校准。

## 安装

和其他 DSH 插件一样，装在 `dsh-feiyu-memgate` 旁边：在插件页面安装，或者

```
dsh plugin --profile <profile> add dsh-feiyu-memgate-embed
```

安装时会遇到两件事：

- **安装脚本提示。** node-llama-cpp 有一个安装脚本，DSH 会先拦下。对这个插件，两种决定都可以。放行时脚本只检查
  预编译程序（测试里它立即结束，没有下载也没有编译任何东西）；拒绝时也不缺任何东西（在 Linux 和 Windows 上测过）。
  插件自己从不下载或编译程序。
  - 在插件页面上，安装停住，页面提示放行脚本并重试。
  - 用 `dsh plugin add` 时，安装以 `ERR_PNPM_IGNORED_BUILDS` 停住，退出码 1。这时包已经在磁盘上，但没有启用；
    再运行一次同样的 `add` 也不会启用它。
  - 要拒绝这个脚本，在安装前把下面的内容加进 profile 的 `pnpm-workspace.yaml`（DSH 主目录下
    `profiles/<profile>/` 里）：

    ```yaml
    allowBuilds:
      node-llama-cpp: false
    ```

    如果安装已经停住了，先运行 `dsh plugin --profile <profile> remove dsh-feiyu-memgate-embed`，再重新 `add`。
- **`node_modules` 很大。** pnpm 会装上 node-llama-cpp 为各种 GPU 准备的、它排除不了的预编译程序：用 DSH 的
  profile 设置，在 Linux x64 上是 738 MB，在 Windows x64 上是 745 MB，其中插件用的 CPU 版只有 33 MB。包本身
  没法阻止；profile 可以，在 `pnpm-workspace.yaml` 里写一份 `ignoredOptionalDependencies` 列表，在 Linux 上测得
  降到 90 MB。列表和测量见 [docs/DETAIL.md](docs/DETAIL.md#安装体积)。

要求：DSH 0.2.2（rc.1 起的候选版和正式版；对照过 rc.1 到 rc.3），Node 22.19 或更新，以及 node-llama-cpp 提供了 CPU 程序的平台。在 Windows
上，这个程序需要 Microsoft Visual C++ 2015-2022 运行库（x64），大多数 Windows 已经装有。

## 使用

在记忆插件的设置页上选一个模型，点它的按钮。插件会下载它、核对 SHA-256，然后启用它。这个任务属于插件，关掉
页面也不会停；你的选择重启后照样有效。

聊天命令可以分步做同样的事：

```
/memgate-embed list                      列出模型：大小、许可、是否已下载
/memgate-embed add bge-small-zh-v1.5     下载一个模型（在后台）
/memgate-embed status                    状态、下载进度、模型文件夹
/memgate-embed use bge-small-zh-v1.5     运行它；就绪后记忆插件自动用上
```

| 命令 | 作用 |
|---|---|
| `/memgate-embed` 或 `status` | 模型和接口的状态、输入上限和截断了多少段输入、下载进度、下载来源、模型文件夹。 |
| `list` | 登记的模型。 |
| `add <id>` | 下载一个模型；同一时间只有一个下载，中断后续传。 |
| `use <id>` / `use none` | 选择运行哪个模型，或者不运行。 |
| `remove <id>` | 删除一个已下载的模型（当前模型除外）。 |
| `cancel` | 停止正在进行的下载；已下载的部分留给下次 `add`。 |
| `origin [auto\|huggingface\|hf-mirror\|<地址>]` | 查看或设置下载来源。 |

命令是给你用的，不给模型用：不向模型提供任何工具。回复语言跟随界面语言（中文或英文）。

## 设置

| 设置 | 默认 | 含义 |
|---|---|---|
| `modules.engine` | 开 | 运行当前模型并提供服务。关：没有进程、没有端口、没有服务。 |
| `modules.command` | 开 | 注册 `/memgate-embed`。 |
| `model.activeModel` | `none` | 在这里指定模型；指定后以这里为准，设置页的按钮和 `/memgate-embed use` 不再起作用。 |
| `model.autoStart` | 开 | DSH 启动时就加载模型；关闭则第一次请求时加载。 |
| `model.idleUnloadSeconds` | 0 | 空闲多少秒后卸载模型；0 表示从不卸载。 |
| `model.threads` | 0 | CPU 线程数；0 表示自动。 |
| `model.overflow` | `truncate` | 超过模型能整段处理的文本：`truncate` 用开头部分做向量，`reject` 拒绝。见[长记忆](#长记忆)。 |
| `download.origin` | `auto` | `auto`、`huggingface`、`hf-mirror` 或镜像地址；不是 `auto` 时以这里为准。 |
| `download.dataDir` | 空 | 模型和状态的文件夹；留空是 DSH 主目录下的 `data/feiyu-memgate`。 |

所有设置都不用重启 DSH 就生效。插件不改你的 DSH profile；在设置页上或用命令做的选择记在它自己的状态文件里
（数据文件夹中的 `embed-state.json`）。

## 长记忆

记忆网关接受最长 4000 字的记忆，比模型能整段处理的长（两个 bge-zh 模型是 512 个 token，大约 500 个汉字）。

- **`truncate`（默认）。** 更长的记忆照样完整保存、完整返回，但只按开头部分被检索到：向量只用放得下的开头部分。
  Ollama 默认也这样做，网关的分数就是通过 Ollama 校准的。每个模型用一段超长文本测试，四个测过的模型给出的向量
  都和 Ollama 完全一致（余弦 1.000000，token 数相同）。
- **`reject`。** 把 `overflow` 设成 `reject`，这样的记忆会被拒绝，网关把这次写入报告为失败。

上限就是校准用的 Ollama 设置实际处理的长度，经过测量：512，bge-m3 是 2048，qwen3 是 4095 个 token。

## 和记忆插件配合

插件提供 Cordis 服务 `feiyuMemgateEmbed`，里面有：

- 一份快照：状态、接口地址和密钥、模型 id、向量维度、确切文件的指纹，以及网关对这个模型的校准数值；
- 控制方法（`list`、`add`、`install`、`use`、`cancel`、`remove`、`job`、`setOrigin`），设置页的按钮用的就是它们。

`dsh-feiyu-memgate` 读取快照，在它为 `ready` 时让网关用一个指向这个接口的 `embedding` 存储。这份约定（第 2
版，也适用于其他使用者）见 [docs/SERVICE.md](docs/SERVICE.md)。

## 工作方式

- 模型在一个子进程（`src/worker.js`）里运行，用宿主的 Node 或 Electron 程序以普通 Node 方式启动；宿主有子进程
  服务时通过它启动（Windows 桌面版的宿主会在上面多加一个约 75 MB 的小 runner 进程）。崩溃后会以逐渐加长的间隔
  重启，不影响 DSH；父进程不在了它就退出。
- 模型进程监听一个回环端口，带一个随机密钥，这个密钥不出插件。插件在它前面另开一个回环接口，地址和密钥在模型
  重启、空闲卸载和切换模型时都不变，服务里给出的就是这个地址。模型加载期间到达的请求会等它加载完。
- 接口只对收到的文本本身做向量。查询或文档前缀由调用方负责（记忆网关按它的校准自己加）。超过模型上限的文本
  按 Ollama 的方式截断；`overflow` 为 `reject` 时返回 HTTP 413。
- 只用 CPU：不用 GPU，运行时不下载也不编译程序。下载用宿主的 `fetch`，所以宿主的代理设置有效。
- 日志和错误信息里不会出现记忆正文、查询或密钥。

## 测试

```
npm test                                              # node --test，不需要安装依赖
FEIYU_MEMGATE_SRC=../.. npm test                       # 另外把登记表和网关的预设比对
DSH_FEIYU_MEMGATE_SRC=../dsh-feiyu-memgate npm test   # 另外用记忆插件的读取代码检查快照
```

`tests/docker/` 里是需要真实运行环境和模型文件的检查：`install-profile.sh` 把包装进一个类似 DSH 的 pnpm
profile，`compare-models.mjs` 运行每个模型并和 Ollama 比对（包括一段超长文本），`e2e-gateway.mjs` 通过服务的
`install()` 装好模型，再让记忆插件的网关通过本插件工作。

## 卸载

在插件页面卸载，或者运行 `dsh plugin --profile <profile> remove dsh-feiyu-memgate-embed`。卸载包之后，它的数据
还留在 DSH 主目录里：

- `data/feiyu-memgate/models/`：下载的模型（每个最大 1.16 GB）。想腾出空间，在卸载前先 `/memgate-embed use none`
  再 `/memgate-embed remove <id>`，或者卸载后删掉这个文件夹。
- `data/feiyu-memgate/embed-state.json` 和 `data/feiyu-memgate/embed-worker.log`。

如果还保留记忆插件，不要删掉整个 `data/feiyu-memgate` 文件夹：记忆插件的记忆也在里面。把你在 profile 的
`cordis.patch.yml` 里为 `feiyu-memgate-embed` 写的条目删掉，否则 DSH 每次启动都会警告
`patch: entry "feiyu-memgate-embed" not found`。`pnpm-workspace.yaml` 里的 `allowBuilds` 那一行可以留着。

没有这个插件后，记忆插件改回关键词存储。启用模型期间保存的记忆留在那个模型的存储里，关键词检索找不到它们；原因见
记忆插件的说明。

## 测试过的和没有测试的

在 Linux x64（Node 24）上，以及 Windows 11 x64 的 DSH 桌面版运行环境里（DSH 0.2.2-rc.1）测试过。Windows 上的
测试包括：通过 DSH 安装并拒绝安装脚本、下载、通过服务一键安装、与 Ollama 完全相同的向量，以及记忆插件的网关在
`local` 档位上的运行。macOS 没有可用的机器，没能测试。ARM 版的 Linux 和 Windows 也没有测试。

## 许可

MIT。模型各有自己的许可，见上表。
