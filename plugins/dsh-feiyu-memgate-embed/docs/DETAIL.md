# feiyu-memgate 本地向量模型

这个附加插件在本机 CPU 上运行一个向量模型，交给 feiyu-memgate 记忆插件使用。有了它，记忆插件的 `local`
检索方式按意思而不是按相同的字词来比较记忆，不依赖任何在线服务。没有模型时，记忆插件继续用关键词检索。

## 不主动下载任何东西

安装或启用这个插件不会下载模型，插件里也不带模型。通常的做法是在 feiyu-memgate 记忆插件的设置页上点按钮：选好的
模型会被下载、校验并启用，一步完成；关掉页面，任务也会继续。聊天命令可以分步做同样的事：

```
/memgate-embed list
/memgate-embed add bge-small-zh-v1.5
/memgate-embed status
/memgate-embed use bge-small-zh-v1.5
```

无论用哪种方式，你选的模型都记在插件自己的状态文件里，重启后照样运行。如果在本插件的 `activeModel` 设置里选了
模型，则以设置为准。

## 模型

每个文件都固定在它的 Hugging Face 仓库的某一次提交上，使用前核对大小和 SHA-256。内存是在 Linux x64
上（只用 CPU）测得的模型进程常驻内存；“峰值”包含一次达到模型上限的长输入。“完整处理”是能整段做向量的最长文本
（以 token 计，见下面的“长记忆”）。

| id | 大小 | 加载后内存 / 峰值 | 完整处理 | 许可 | 来源文件 |
|---|---|---|---|---|---|
| `bge-small-zh-v1.5` | 47.9 MB | 188 MB / 219 MB | 512 token | MIT | `CompendiumLabs/bge-small-zh-v1.5-gguf`，`bge-small-zh-v1.5-f16.gguf` |
| `bge-base-zh-v1.5` | 110 MB | 251 MB / 284 MB | 512 token | MIT | `CompendiumLabs/bge-base-zh-v1.5-gguf`，`bge-base-zh-v1.5-q8_0.gguf` |
| `qwen3-embedding-0.6b` | 639 MB | 1.35 GB / 1.77 GB | 4095 token | Apache-2.0 | `Qwen/Qwen3-Embedding-0.6B-GGUF`，`Qwen3-Embedding-0.6B-Q8_0.gguf` |
| `bge-m3` | 1.16 GB | 1.58 GB / 1.86 GB | 2048 token | MIT | `gpustack/bge-m3-GGUF`，`bge-m3-FP16.gguf` |

- 四个文件都和记忆网关校准分数时用的文件逐字节相同，网关的分数线可以直接用。各档位的召回效果见
  https://github.com/N0rmal-114/feiyu-memgate#部署档位
- 网关的中档 embeddinggemma 不提供：检查过的 GGUF 文件缺少这个模型的两个 Dense 投影层，算出的分数对不上网关的
  校准。
- 在 Windows 上（DSH 桌面版运行环境），`bge-small-zh-v1.5` 加载后工作集 139 MB，峰值 174 MB；宿主在它上面的子进程
  runner 另占约 75 MB。

## 从哪里下载，存到哪里

- 来源：`auto`（默认）同时询问 huggingface.co 和 hf-mirror.com，用先回应的那个；`huggingface`、
  `hf-mirror` 或一个 Hugging Face 兼容镜像的地址则只用它。用 `/memgate-embed origin <值>` 或在设置里修改。
  下载走宿主的网络设置，包括代理。
- 文件：DSH 主目录下的 `data/feiyu-memgate/models/<id>/`，除非设置 `dataDir` 指定了别的文件夹。中断的下载从
  断点续传；大小和 SHA-256 都对上之后，文件才改成正式的名字。`/memgate-embed remove <id>` 删除它。
- 同一时间只有一个下载；`/memgate-embed cancel` 停止它。

## 安装体积

运行模型的 node-llama-cpp 把每个平台、每种 GPU 的预编译程序都列成可选依赖，pnpm 会装上所有它排除不了的。
在 Linux x64 上用 pnpm 11.28.5 和 DSH 写入的 profile 设置实测：

| profile 设置 | node_modules |
|---|---|
| DSH 默认 | 738 MB：CUDA 版 577 MB、Vulkan 版 52 MB、ARM 版 21 MB、CPU 版 33 MB、node-llama-cpp 43 MB（Windows x64 上 745 MB） |
| 加上下面的 `ignoredOptionalDependencies` | 90 MB |

插件只用 CPU 版。包本身没法阻止 pnpm 装其他的：把同样的列表写进插件自己的 package.json、用
`overrides`、用 `supportedArchitectures`，测试里都没有效果，只有 profile 的设置有效。想省下这些空间，在
安装前把下面的内容加进 profile 的 `pnpm-workspace.yaml`（DSH 主目录下 `profiles/<profile>/` 里），或者加完后
重新安装。这份列表适用于 Linux x64 和 Windows x64；其他平台要保留自己的那个包（Apple 芯片：
`mac-arm64-metal`；Intel Mac：`mac-x64`；ARM Linux：`linux-arm64`；ARM 版 Windows：`win-arm64`）。

```yaml
ignoredOptionalDependencies:
  - "@node-llama-cpp/linux-x64-cuda"
  - "@node-llama-cpp/linux-x64-cuda-ext"
  - "@node-llama-cpp/linux-x64-vulkan"
  - "@node-llama-cpp/linux-arm64"
  - "@node-llama-cpp/linux-armv7l"
  - "@node-llama-cpp/win-x64-cuda"
  - "@node-llama-cpp/win-x64-cuda-ext"
  - "@node-llama-cpp/win-x64-vulkan"
  - "@node-llama-cpp/win-arm64"
  - "@node-llama-cpp/mac-arm64-metal"
```

node-llama-cpp 还有一个安装脚本，DSH 会先拦下，等你决定。两种决定都可以。放行时，测试里这个脚本立即结束，没有
下载也没有编译任何东西；拒绝时，插件在 Linux 和 Windows 上都照常运行。插件自己从不下载或编译程序。

- 插件页面会提示放行脚本并重试。
- `dsh plugin add` 以 `ERR_PNPM_IGNORED_BUILDS` 停住，退出码 1。包留在磁盘上但没有启用；再运行一次同样的 `add`
  也不会启用。

要拒绝这个脚本，在安装前往同一个文件里加 `allowBuilds:`，下面写 `node-llama-cpp: false`。如果安装已经停住，先
`dsh plugin --profile <profile> remove dsh-feiyu-memgate-embed`，再重新 `add`，就会启用（在 Windows 上用 DSH
0.2.2-rc.1 实测）。

在 Windows 上，预编译程序从系统加载 Microsoft Visual C++ 2015-2022 运行库（MSVCP140.dll、VCRUNTIME140.dll）；
大多数 Windows 已经装有。llama.cpp 会按处理器自己选择 CPU 版本。

## 长记忆

记忆网关接受最长 4000 字的记忆，比模型能整段处理的长：两个 bge-zh 模型是 512 个 token（大约 500 个汉字），
bge-m3 是 2048 个，qwen3-embedding 是 4095 个。

- **`truncate`（默认）。** 更长的记忆照样完整保存、完整返回，但只按开头部分被检索到：向量只用放得下的开头部分。
  Ollama 默认也这样做，网关的分数就是通过 Ollama 校准的；用一段超长文本测试，四个测过的模型给出的向量都和
  Ollama 完全一致（余弦 1.000000）。
- **`reject`。** 把 `overflow` 设置改成 `reject`，这样的记忆会被拒绝，网关把这次写入报告为失败。

`/memgate-embed status` 会显示上限，以及插件启动以来截断了多少段输入。

## 限制

- 只用 CPU。同一时间只运行一个模型，在单独的进程里；这个进程崩溃不影响 DSH，插件会重启它。
- 在 Linux x64（Node 24）上，以及 Windows 11 x64 的桌面版运行环境里（DSH 0.2.2-rc.1）测试过。macOS 没有可用
  的机器，没能测试；ARM 版的 Linux 和 Windows 也没有测试。

## 卸载

卸载包之后，它的数据还留在 DSH 主目录里：

- `data/feiyu-memgate/models/`：下载的模型；
- `data/feiyu-memgate/embed-state.json` 和 `embed-worker.log`。

手动删掉它们，或者在卸载前先用 `/memgate-embed use none` 和 `/memgate-embed remove <id>` 删掉模型。还在用记忆
插件时，`data/feiyu-memgate` 里的其他文件要留着：记忆插件的记忆在里面。把 profile 的 `cordis.patch.yml` 里
`feiyu-memgate-embed` 的条目删掉，否则 DSH 每次启动都会警告 `patch: entry "feiyu-memgate-embed" not found`。
