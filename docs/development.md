# 开发指南

这份文档写给要在本仓库里改代码的人：要装什么、代码在哪、怎么跑测试、插件里哪些部分是生成的或打包进去的、CI 检查什么、有哪些约定、发版时仓库里能看到什么。只想用 feiyu-memgate 或在它的基础上做自己的东西，看 [extending.md](extending.md)。接口的逐项说明在 [api.md](api.md)，怎么写一块拼图在 [pieces.md](pieces.md)，各部分怎么配合在 [architecture.md](architecture.md)。

文中的命令按 bash 写，路径相对于仓库根目录（Windows 上用 Git Bash）；PowerShell 里用 `$env:名字 = "值"` 设置环境变量。Windows 的控制台默认不是 UTF-8，`feiyu-memgate` 的中文输出会变成乱码，设 `PYTHONUTF8=1` 就好（只影响显示）。

## 需要什么

| 要什么 | 版本 | 出处 |
|---|---|---|
| Python | 3.11 或更高 | `pyproject.toml` 的 `requires-python` |
| 网关的依赖 | 没有，什么都不用装 | `pyproject.toml` 的 `dependencies = []` |
| `cryptography`（可选） | 42 或更高 | `pyproject.toml` 的可选依赖 `backup`；只有加密备份用得上，没装时相关的测试会跳过 |
| Node.js | 22.19 或更高 | 两个插件的 `package.json` 的 `engines.node`；只有碰 `plugins/` 才需要 |

插件的测试不需要 `npm install`：`plugins/` 下两个包的测试直接用 `node --test` 跑，CI 里也没有安装这一步（见 [CI](#ci)）。`npm` 只用来运行 `package.json` 里的脚本（`npm run sync-client` 等）。

## 取得代码并运行

不安装也能运行：把 `src` 放进 `PYTHONPATH`，用 `python -m feiyu_memgate` 起命令行（`src/feiyu_memgate/__main__.py` 调用 `cli.main`）。测试也是这样找到包的：`tests/__init__.py` 把 `src` 放进 `sys.path`。

```bash
export PYTHONPATH=src PYTHONUTF8=1
python -B -m feiyu_memgate --help
```

```text
usage: feiyu-memgate [-h] {serve,check,pieces,backup,eval,tenants,forget} ...

可拼装的记忆网关

positional arguments:
  {serve,check,pieces,backup,eval,tenants,forget}
    serve               按配置文件启动网关
    check               检查配置并显示各部件怎么拼起来的（不显示令牌）
    pieces              列出所有可用的部件
    backup              加密备份与导出
    eval                用示例记忆评测召回质量，帮助选分数线
    tenants             管理 keyfile 租户的密钥文件；给绑定租户的客户端铸造令牌（token）
    forget              遗忘曲线：查看状态、清理、存档、恢复、钉住（输出只有数字和地址，不显示记忆正文）

options:
  -h, --help            show this help message and exit
```

`feiyu-memgate` 这条命令是 `pyproject.toml` 里 `[project.scripts]` 定义的入口：`feiyu-memgate = "feiyu_memgate.cli:main"`。要得到这条命令，装成可编辑的包（需要 `setuptools>=68`，`pyproject.toml` 的 `build-system` 指定了它，隔离构建时 pip 会自己去取；已经装有 setuptools 又不想联网时加 `--no-build-isolation`）：

```bash
pip install -e .            # 要备份加密的话 pip install -e ".[backup]"
feiyu-memgate --help
```

用 `examples/quickstart` 跑起一个网关：令牌从环境变量读，至少 32 个字符；SQLite 文件按配置写在 `data/private.db`，也就是运行命令时所在文件夹下的 `data/`（父文件夹会自己建，`.gitignore` 排除了 `*.db`）。

```bash
export GATEWAY_TOKEN_OWNER=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
python -B -m feiyu_memgate check --config examples/quickstart/gateway.toml
```

```text
存储：
  private: sqlite，可写
租户来源：
  （没有）
客户端：
  owner: 召回 private；写入 private；租户 无；公开 否；每日写入上限 50；令牌 有可用令牌
召回阶段（按顺序）：min_score -> redact -> dedupe -> budget
分数线档位：min_score broad
写入关卡（按顺序）：redact -> daily_cap
```

```bash
python -B -m feiyu_memgate serve --config examples/quickstart/gateway.toml     # 监听 127.0.0.1:8080
curl -s http://127.0.0.1:8080/healthz                                          # 另一个终端里
# {"status": "ok"}
```

接着可以用 `curl` 或 `feiyu_memgate.client` 调 `/v1/recall` 和 `/v1/remember`，见 [api.md](api.md)；[examples/chat-loop](../examples/chat-loop/README.md) 是一轮对话里用记忆的完整脚本。别的配置示例都在 `examples/` 下，各有一份 `gateway.toml`。

运行 Python 时一律加 `-B`（不写字节码缓存），原因见 [为什么 Python 要加 -B](#为什么-python-要加--b)。

## 仓库里有什么

### 顶层

| 路径 | 是什么 |
|---|---|
| `src/feiyu_memgate/` | 网关和客户端的 Python 包，只用标准库，下面分包说明 |
| `tests/` | 网关的测试，`unittest`；`test_<模块>.py` 对应源码里的模块，`test_bench_*.py` 测 `bench/` 里的脚本，`test_examples.py` 保证 `examples/` 里的示例一直能用 |
| `plugins/` | 两个 DSH 聊天宿主的插件（Node.js），见下面 |
| `integrations/astrbot/` | AstrBot 的接入示例（Python 插件），测试在 `tests/test_integration_astrbot.py`，用模拟的 AstrBot 接口 |
| `examples/` | 配置示例和可以运行的小例子：`quickstart`、`hybrid`、`openviking`、`multi-tenant`、`embedded`（进程内）、`custom-stage`（自己写召回步骤）、`custom-backend`（自己写存储后端）、`chat-loop`（一轮对话的完整流程）、`eval`（评测用的假数据） |
| `docs/` | 面向用户和开发者的文档；`docs/design/` 是设计文档，`docs/assets/` 是图 |
| `bench/` | 记忆检索基准：数据、各系统的适配器、打分脚本和结果，见 `bench/README.md` |
| `research/` | 对同类记忆项目的调研报告 |
| `.github/workflows/ci.yml` | 自动检查，见 [CI](#ci) |
| `.gitleaks.toml` | gitleaks（密钥扫描工具）的配置，只放行 `tests/` 里的虚构密钥；CI 里没有运行它 |
| `pyproject.toml`、`LICENSE`、`CHANGELOG.md`、`CONTRIBUTING.md`、`README*.md` | 包的元数据、许可证、更新记录、贡献说明、说明 |

在仓库根目录运行本地的网关，数据会落到根目录的 `data/`（被 `.gitignore` 排除）；`.venv/` 也被排除。

### `src/feiyu_memgate/`

| 路径 | 负责什么 |
|---|---|
| `__init__.py`、`__main__.py` | 版本号和几个常用名字；`python -m feiyu_memgate` 的入口 |
| `pieces.py` | 做拼图要用的全部名字，对拼图作者的承诺 |
| `registry.py` | 拼图注册表：配置里的名字（带冒号的路径、`@register`、入口点）怎么变成对象 |
| `builtins.py` | 导入它就注册全部内置拼图，注册表第一次用到时导入 |
| `model.py` | `Hit`、`Candidate`、`ClientPolicy` 这几个数据类 |
| `errors.py` | 各处共用的异常 |
| `config.py` | 读 TOML 配置，逐项检查，拼出网关 |
| `cli.py` | `feiyu-memgate` 命令行：`serve`、`check`、`pieces`、`backup`、`eval`、`tenants`、`forget` |
| `backends/` | 存储：`base.py`（`MemoryBackend`）、`inmemory.py`、`sqlite.py`、`embedding.py`、`hybrid.py`、`openviking.py` |
| `client/` | 给聊天程序用的客户端：`http.py`（`MemoryClient`）、`local.py`（`LocalClient`）、`query.py`（查询词）、`block.py`（记忆块） |
| `core/` | 内部实现：`recall.py`（召回引擎和 `RecallStage`）、`stages.py`（内置召回步骤）、`write.py`（写入守卫）、`redact.py`（脱敏）、`pool.py`（带取消的工作线程池）、`limits.py`（限流和每日写入上限）、`tidy.py`（文字规整、近似重复、关键词打分用的字符片段）、`watch.py`（失败和写入结果的计数） |
| `forgetting/` | 遗忘曲线：`curve.py`（曲线）、`ledger.py`（账本）、`archive.py`（存档）、`engine.py`（把三者接起来）、`stage.py`（召回时的排序步骤） |
| `gateway/` | `app.py`（网关本体：谁能读写什么、召回和写入的流程）、`server.py`（HTTP 服务） |
| `tenants/` | 租户存储：`base.py`（`TenantProvider`）、`keyfile.py`、`directory.py` |
| `tools/` | 命令行背后的工具：`backup.py`、`crypto.py`（加密，要 `cryptography`）、`evaluate.py`（评测）、`tenants.py`（租户密钥文件、铸造令牌） |

`feiyu_memgate.core` 是内部实现，随版本会变；对外承诺的名字都在 `pieces.py` 和 `client/__init__.py` 里。

### `plugins/dsh-feiyu-memgate/`

DSH 聊天宿主的主插件：自带并启动网关，每轮自动召回，给模型“记住”和“查找”两个工具，带设置页面。入口 `index.js` 只导出 `name`、`inject`、`Config`、`apply`。

| 路径 | 负责什么 |
|---|---|
| `src/plugin.js` | 插件在宿主里的行为：每一轮召回（在 `agent/pre-step` 里把记忆块放到用户消息后面）、两个工具、提示词里的说明段、自动记录、各功能的开关和随设置变化的重新配置 |
| `src/gateway.js` | 自带网关：找 Python、写网关的配置文件、起进程、健康检查、崩溃后重启、停止时不留进程；`TOKEN_ENV`、`PACKAGE_DIR`（`python/`） |
| `src/tiers.js` | 检索档位表：每个档位要什么、缺什么、生成怎样的 `[stores.memories]` |
| `src/config.js` | 设置的结构、`FIELDS` 字段表（设置页和 `/memgate` 命令都从它来）、读取和校验 |
| `src/client.js` | 网关客户端的 JavaScript 移植（用 `fetch`，方法都不会 reject），见 [extending.md](extending.md#用别的语言接入) |
| `src/query.js` | 查询词整形的移植 |
| `src/block.js` | 记忆块格式的移植 |
| `src/commands.js` | 聊天里的 `/memgate` 命令：状态、设置、检索档位、本地模型 |
| `src/setup.js` | 一键安装并启用本地向量模型：装附加包、下载、校验、切换档位，每一步写进数据文件夹里的状态文件 |
| `src/catalogue.js` | 设置页在附加包没装时显示的本地模型清单，其中 `<catalogue>` 标记之间是生成的 |
| `src/migrate.js`、`src/migrate.py` | 切到向量档位时，把关键词存储里的记忆分批复制过去；`.py` 用网关的 Python 解释器运行 |
| `src/mirror.py` | 混合检索：让关键词索引和向量库保持同一批记忆，起网关之前由 `gateway.js` 运行 |
| `client/src/` | 设置页（浏览器那一半）的源码：按职责拆成的一组小模块（`.cjs`，加上样式表 `theme/page.css`），分成数据表、`model/`（纯逻辑）、`kit/`（页面自己的控件）、`view/`（各部分的画法）和 `theme/`，其中 `fields.cjs` 是生成的；页面只向宿主要 `react`；分工、依赖规则和设计约定见 [client/README.md](../plugins/dsh-feiyu-memgate/client/README.md) |
| `client/art/` | 设置页顶部吉祥物的四张图（`mascot-<ready\|busy\|asleep\|trouble>.webp`），由主插件的路由提供 |
| `client/client.js` | 设置页脚本，**由 `npm run build-client` 从 `client/src/` 拼出来，不要手改**；宿主只加载这一个文件 |
| `python/` | 打包进去的网关，是 `src/feiyu_memgate/` 的一份拷贝，不要手改，见 [生成的和打包的部分](#插件里生成的和打包的部分) |
| `scripts/` | `sync-gateway.mjs`、`sync-client.mjs`、`build-client.mjs`、`sync-catalogue.mjs`，以及开发用的 `preview-client.mjs`（连同 `preview/`：编造的状态和模型清单、预览页的脚本和宿主样式变量），在浏览器里预览设置页面，见下 |
| `tools/make-parity-fixtures.py` | 用真正的 Python 客户端生成对照数据 |
| `tests/` | `node --test` 的测试；`fixtures/parity/` 是对照数据，`helpers/` 是假宿主 |
| `locale/`、`docs/`、`cordis.patch.yml` | 界面文字、插件管理页显示的说明、安装时往宿主配置里加一行的补丁 |

`plugins/dsh-feiyu-memgate-embed/` 是可选的附加包：在本机用 CPU 运行向量模型，把它作为兼容 OpenAI 的 embeddings 接口提供给网关。它对外提供的服务在 [docs/SERVICE.md](../plugins/dsh-feiyu-memgate-embed/docs/SERVICE.md) 里说明，测试在它自己的 `tests/` 下。两个插件互不在运行时导入对方。

## 运行测试

### 网关

```bash
python -B -m unittest discover -s tests -t .
```

全部测试要几分钟，不需要联网，不需要装任何东西。输出末尾的 `Ran N tests ... OK` 是结果；中间可能有测试自己打印的文字（基准的自检表、命令行的报错信息），那不是失败。

单个文件、类或用例（用模块路径，不是文件路径：从仓库根目录运行，`tests` 是包）：

```bash
python -B -m unittest tests.test_examples
python -B -m unittest tests.test_examples.RecencyExample
python -B -m unittest tests.test_examples.RecencyExample.test_newer_memory_ranks_first
python -B -m unittest tests.test_registry -v           # -v 逐条列出
```

没有装 `cryptography` 时，下面这些会跳过（会显示 `skipped 'cryptography is not installed'`，不算失败）：`tests/test_tools_crypto.py` 的 `CryptoTests`、`tests/test_tools_backup.py` 的 `SealTests`。CI 里只有一个组合（Ubuntu、Python 3.12）装了它，见 [CI](#ci)。其余会跳过的：

- `tests/test_backend_sqlite.py` 的 `SQLiteBenchmarkTest`（10 万条记忆的搜索耗时）：设 `FEIYU_MEMGATE_SLOW_TESTS=1` 才运行；
- `tests/test_tools_tenants.py` 里检查文件权限位的一个用例：Windows 不执行权限位，在 Windows 上跳过。

### 插件

两个插件各在自己的文件夹里运行（不要 `npm install`）：

```bash
cd plugins/dsh-feiyu-memgate && node --test
cd plugins/dsh-feiyu-memgate-embed && node --test
```

不设环境变量时，有些测试会跳过；设了，就多跑下面这些。CI 里都设了（见 [CI](#ci)）。

CI 用的是 Node 22，也就是插件支持的最低版本。推送之前最好也用 Node 22 跑一遍：等待辅助进程的测试文件（`tests/mirror.test.js`、附加包的 `tests/engine.test.js`）开头各有一个定时器，因为辅助进程和它的管道都是 unref 的，Node 22 不会替测试保持事件循环，更新的版本会，所以只在 Node 24 上跑发现不了这类问题。

| 变量 | 在哪个包 | 值 | 打开什么 |
|---|---|---|---|
| `FEIYU_MEMGATE_SRC` | `dsh-feiyu-memgate` | 网关源码的 `src` 文件夹（里面有 `feiyu_memgate` 包），从插件文件夹出发是 `../../src` | `tests/gateway-e2e.test.js` 的两个用例：用这份源码起一个真正的网关（`python -I -c ...` 调 `feiyu_memgate.cli`），经插件的 JavaScript 客户端做 remember、recall、used 和健康检查；再起一个混合检索的网关（假的 embeddings 接口），查两个文件里的 ref 一致、按字面和按改写都能召回、更新索引时去掉被忘掉的记忆。不设就跳过这两个 |
| `FEIYU_MEMGATE_PYTHON` | `dsh-feiyu-memgate` | 解释器的名字或路径，默认 `python` | 只有上面这两个用例用它 |
| `FEIYU_MEMGATE_SRC` | `dsh-feiyu-memgate-embed` | 网关仓库的根目录或它的 `src`，CI 里是 `../..`（两种写法都认） | `tests/models.test.js`：附加包里每个模型的校准值（`floor`、`ceil`、前缀）和网关 `backends/embedding.py` 的 `PRESETS` 一致、顺序一致；`tests/server.test.js`：用网关自己的 `_Http` 读附加包的 embeddings 接口，检查返回的向量个数和维数、别的模型名被拒绝、错误的密钥被拒绝；超长的输入也返回向量。不设就跳过 |
| `DSH_FEIYU_MEMGATE_SRC` | `dsh-feiyu-memgate-embed` | 主插件的文件夹，CI 里是 `../dsh-feiyu-memgate` | `tests/plugin.test.js` 里的一个用例多检查一步：把附加包发布的服务快照交给主插件自己的 `src/tiers.js`（`readEmbed`、`embedReady`），要读得通、算作就绪。不是跳过，是多几条断言 |

与环境变量无关的跳过：主插件里需要 Python 的测试（`gateway.test.js`、`migrate.test.js`、`mirror.test.js`、`tiers.test.js` 里的一个）在 PATH 上找不到 3.11 或更高的 Python 时跳过；`catalogue.test.js` 里和附加包比较清单的用例在旁边没有 `dsh-feiyu-memgate-embed` 文件夹时跳过。

## 预览设置页面

不需要 DSH，也不需要安装任何依赖，就能在浏览器里看设置页面：

```bash
node scripts/preview-client.mjs --react <一个带有 react 和 react-dom 18 的 node_modules 文件夹> [--port 47931]
```

在 `plugins/dsh-feiyu-memgate/` 里运行，然后打开 `http://127.0.0.1:47931/`，在顶部的栏里选情形、浅色或深色、语言、设置表单的状态和栏宽。页面脚本每次请求都从 `client/src/` 重新拼，改了源码刷新就能看到。真的部分：页面脚本和样式表、`client/art/` 里的图、读自 `CHANGELOG.md` 的更新日志；编造的部分：状态和模型清单（`scripts/preview/scenarios.mjs`）、设置表单（保存在页面里）、宿主的样式变量（`scripts/preview/host-tokens.css`，抄来的值）。所以它能看版式、文字、各种状态和两种主题，看不到页面在真正的插件页里的样子。服务只监听 `127.0.0.1`，只提供脚本里写明的几个文件；`tests/client-preview.test.js` 检查编造的数据能被页面画出来，测试不启动这个服务。

## 插件里生成的和打包的部分

主插件里有几处不是手写的：`client/src/fields.cjs`、`client/client.js`、`src/catalogue.js` 里的清单和 `python/`。改了源头，要重新生成；CI 只检查其中两条命令的结果是不是最新（`sync-client` 同时管前两个文件）。

| 命令（在 `plugins/dsh-feiyu-memgate/` 里） | 复制什么、到哪里 | 什么时候要运行 | `--check` |
|---|---|---|---|
| `npm run sync-client` | 先把 `src/config.js` 里的 `FIELDS` 字段表写成 `client/src/fields.cjs`（设置页在浏览器里运行，不能导入 `src/` 里的模块，所以要有一份拷贝），再调用 `build-client` | 改了 `src/config.js` 里的设置（加、删、改默认值、`needs`、`unit`、`experimental`）之后 | `npm run sync-client -- --check`：`fields.cjs` 或 `client/client.js` 不是最新就报错退出（状态 1），不改文件；`tests/client-page.test.js` 做同样的检查 |
| `npm run build-client` | `client/src/` 里的全部 `.cjs` 和 `.css` 文件 → `client/client.js`。宿主只加载一个脚本，所以把模块按路径排序后拼在一起，放进一个几十行的小注册表里，不编译、不压缩，结果不随平台、日期或路径变化 | 改了 `client/src/` 里的任何文件之后（改了设置的话直接运行 `sync-client`） | `npm run build-client -- --check`：`client/client.js` 不是最新就报错退出（状态 1），不改文件；CI 里由 `sync-client -- --check` 一并检查 |
| `npm run sync-catalogue` | 附加包 `../dsh-feiyu-memgate-embed/src/models.js` 里的模型清单（编号、名称、档位、下载大小、实测内存、语言、许可证）→ `src/catalogue.js` 里 `// <catalogue>` 和 `// </catalogue>` 之间，不复制下载地址和校验值。`-- --from <models.js>` 读另一份检出 | 改了附加包的模型清单之后 | `npm run sync-catalogue -- --check`：同上；旁边有附加包文件夹时 `tests/catalogue.test.js` 做同样的检查 |
| `npm run sync-gateway` | 仓库的 `src/feiyu_memgate/` 和 `LICENSE` → `python/feiyu_memgate/` 和 `python/LICENSE`（先删掉旧的），再写 `python/GATEWAY_VERSION.json`。`-- <检出的路径>` 可以指定另一个检出，默认是插件所在的仓库 | 改了 `src/feiyu_memgate/` 或 `LICENSE`，而这些改动要跟着插件一起发布之前 | **没有**。这个脚本的参数只认一个路径，写 `--check` 会被当成路径，报 `not a feiyu-memgate checkout` 退出，什么都不写 |

`sync-gateway` 的细节：

- 拷贝是逐字节的，没有 `__pycache__` 和 `.pyc`。源码在 `src/feiyu_memgate/` 和 `LICENSE` 里没有未提交的改动时，字节取自那个提交，不取工作区文件（Windows 检出可能是 CRLF，提交里是 LF）；有未提交的改动时取工作区文件，并在记录里写 `"dirty": true`。
- `GATEWAY_VERSION.json` 记录：包名、仓库地址、版本号（读 `src/feiyu_memgate/__init__.py` 的 `__version__`）、`commit`（最后一次改动过 `src/feiyu_memgate` 或 `LICENSE` 的提交，不是 HEAD：插件和网关在同一个仓库里，HEAD 随插件的每次改动移动）、`dirty`、每个文件的 sha256。里面没有本机路径。
- `tests/package.test.js` 检查这份拷贝：文件一个不多一个不少、哈希都对得上（所以手改 `python/` 会被发现）、版本号和 `python/feiyu_memgate/__init__.py` 一致、没有本机路径、`dirty` 是 `false`。所以发布要用已经提交的网关代码运行 `sync-gateway`。
- 仓库里没有任何检查比较 `python/` 和 `src/feiyu_memgate/`：改了网关却忘了 `sync-gateway`，测试不会发现。要自己看拷贝有没有落后，比较记录里的 `commit` 和 `git log -1 --format=%H -- src/feiyu_memgate LICENSE` 的输出，不一样就是落后了。

### Python 和 JavaScript 的对照数据

插件里的 `src/client.js`、`src/query.js`、`src/block.js` 是 Python 客户端（`client/http.py`、`query.py`、`block.py`）的移植，要给出和 Python 完全一样的结果。保证办法是一组“标准答案”：

- `tools/make-parity-fixtures.py` 运行真正的 Python 客户端（查询整形、记忆块格式、对着一个本地小服务器的每种状态码和错误码，以及客户端必须拒绝发送的输入），把结果写进 `tests/fixtures/parity/` 的 `query.json`、`block.json`、`outcomes.json`。输出是确定的（固定的用例、排序的键、只有 ASCII 的 JSON），同一个 Python 版本运行两次得到同样的文件。
- `tests/parity.test.js` 读这三个文件，用 JavaScript 的移植跑每一个用例，必须得到一样的答案。它只用 Node，不运行 Python。
- 改了 Python 客户端的行为，或想加用例：改 `tools/make-parity-fixtures.py`，在插件文件夹里重新生成，再改移植直到 `node --test` 通过：

  ```bash
  cd plugins/dsh-feiyu-memgate
  python -I -B tools/make-parity-fixtures.py ../..          # 参数是网关仓库的检出，它的 src 会被放进 sys.path
  node --test tests/parity.test.js
  ```

- 一个已知的差别：对照数据里记着生成它的 Python 的 Unicode 版本，Node 的 Unicode 表可能更新。某个码位旧表没有分配、新表分配成了字母或数字，会被 Node 保留、被 Python 丢掉；测试只容许这一种差别，并把它列在输出里，别的差别（不论哪个方向）都算失败。

### 为什么 Python 要加 `-B`

对插件里打包的网关（`plugins/dsh-feiyu-memgate/python/`）运行 Python 时不加 `-B`，Python 会在 `python/feiyu_memgate/` 旁边写 `__pycache__`。这会让 `tests/gateway.test.js` 里“自带网关起停之后，源码旁边没有字节码”的断言失败，也会弄脏工作区；`sync-gateway` 复制时不带它们，包的 `.npmignore` 也把它们排除在外。插件自己起网关时用 `-X pycache_prefix=<数据文件夹>/pycache`，把缓存写到数据文件夹，不写进安装目录；它起的辅助脚本用 `-I -B`。CI 里的网关测试同样加了 `-B`（`ci.yml` 里写着理由）。所以：运行本仓库的任何 Python 命令都加 `-B`，改完检查 `find plugins examples -name __pycache__` 没有输出。

`tests/gateway-e2e.test.js` 里起 `serve` 的那个进程没有加 `-B`，它从 `src/` 导入，可能在 `src/feiyu_memgate/` 下留 `__pycache__`（被 `.gitignore` 排除）。

## 规范和类型检查

测试不需要装任何东西，这两项检查需要工具，所以单独放一节。CI 里用的是固定的版本，本地照着装就行：

```bash
python -m pip install ruff==0.17.6 mypy==2.4.1    # 装进你自己的虚拟环境
python -m ruff check .                            # 规则在 pyproject.toml 的 [tool.ruff]
python -m mypy                                    # 只检查 src/feiyu_memgate，设置在 [tool.mypy]
```

两个插件各有一份 `eslint.config.mjs`，只开和正确性有关的规则（没定义的名字、没用到的变量、重复的键等），不管格式。插件的 `package.json` 里没有加任何依赖，ESLint 用 `npx` 按固定版本运行：

```bash
cd plugins/dsh-feiyu-memgate && npx --yes eslint@10.15.0 .
cd plugins/dsh-feiyu-memgate-embed && npx --yes eslint@10.15.0 .
```

几条约定：

- 一行最多 120 个字符；`tests/` 和 `bench/` 不查行长。
- 有意接住所有异常的地方（一个部件出错不能拖垮一次请求）写 `except Exception:  # noqa: BLE001 - 原因`，原因要写。
- 只为了让别的模块能从这里导入而写的导入，标 `# noqa: F401` 并说明是转出。
- 类型检查不要求给每个函数写标注，只要求检查器能看懂；确实需要 `# type: ignore` 时写明是哪一类和为什么。
- 升级工具的版本要同时改 `.github/workflows/ci.yml` 和这一节。

## CI

`.github/workflows/ci.yml`，在推送到 `main` 和每个 pull request 时运行；只用 GitHub 自己的 actions（`checkout`、`setup-python`、`setup-node`），权限只有 `contents: read`，同一个分支上新的运行取消旧的。三个任务：

| 任务 | 系统 | 版本 | 运行什么 |
|---|---|---|---|
| `gateway` | `ubuntu-latest`、`windows-latest` | Python 3.11、3.12、3.13，共 6 个组合；Ubuntu 加 Python 3.12 的那个组合另外装 `cryptography>=42` | `python -B -m unittest discover -s tests -t .`。其余组合什么都不装，所以加密备份的测试只在那一个组合里运行 |
| `plugins` | `ubuntu-latest`、`windows-latest` | Python 3.12、Node 22 | 在 `plugins/dsh-feiyu-memgate`：`node --test`（`FEIYU_MEMGATE_SRC=../../src`）、`npm run sync-client -- --check`、`npm run sync-catalogue -- --check`；在 `plugins/dsh-feiyu-memgate-embed`：`node --test`（`FEIYU_MEMGATE_SRC=../..`，`DSH_FEIYU_MEMGATE_SRC=../dsh-feiyu-memgate`） |
| `checks` | `ubuntu-latest` | Python 3.12、Node 22 | 装固定版本的 `ruff` 和 `mypy`，运行 `python -m ruff check .` 和 `python -m mypy`；在两个插件目录里各运行一次 `npx --yes eslint@10.15.0 .`。这是唯一会安装工具的任务 |

CI 不做的事：不运行 `sync-gateway`（它没有检查模式），不重新生成对照数据，不设 `FEIYU_MEMGATE_SLOW_TESTS`，不运行 gitleaks，也不构建或发布任何包。

## 约定

`CONTRIBUTING.md` 写明的：

- **网关、客户端和内置拼图只用标准库。** 网关拿着所有存储的密钥，依赖越少越好。需要第三方库的拼图做成单独的包，用入口点注册（见 [extending.md](extending.md#把自己的拼图发布成一个包)）。唯一的例外是备份加密用 `cryptography`，它是可选依赖，缺了就停下并说明，不退回明文。
- **日志和错误信息里不能有内容**：查询词、记忆文字、令牌、密钥、租户编号都不行，只写“哪一种失败”。
- **不截断**：太长的记忆或消息整条拒绝或整条不放。
- **失败要分得清**：“没有”和“没查成”、“确定没存”和“不确定”要能区分，不要把失败当成空结果返回。
- **测试不碰外部服务**：假服务绑在 `127.0.0.1` 的随机端口上；测试数据全部虚构，不能有真实的密钥、账号、群号和个人信息。
- **注释写原因**：代码注释和文档字符串用英文，说明为什么这么做；面向用户的文档和提示用中文。
- 提 PR 之前：全部测试通过；新的行为有测试，修 bug 先写一个能复现它的测试；新的拼图在 [pieces.md](pieces.md) 的表格里加一行，新的设置在 [configuration.md](configuration.md) 里加一行。

从代码和文档的做法看出的，这份指南按这些来写：

- **限制和出错时的行为要写下来。** 写在代码的文档字符串里（`client/http.py` 的上限常量、各个拼图基类的说明），也写在文档里：[api.md](api.md) 的“写死的上限”和错误码表、[configuration.md](configuration.md)。新增的限制、新的错误码、新的失败方式，要同时更新这些地方。
- **每个用户看得到的变化都在 `CHANGELOG.md` 里记一条**：会让现有用法失效的写在“未发布”一节的“要改的地方”下面，写明原因和怎么改回去。插件有自己的 [更新记录](../plugins/dsh-feiyu-memgate/CHANGELOG.md)，网关本身的改动记在根目录的 `CHANGELOG.md`。
- **接口变了要同时更新 [api.md](api.md)。** HTTP 接口、`feiyu_memgate.client`、`feiyu_memgate.pieces` 的名字、命令行，以及错误码，都在那里。`api.md` 的“稳定性说明”列出了哪些算公开接口；`feiyu_memgate.core` 不算。
- **设计上的决定放进 `docs/design/`**，新文件用日期开头，如 `2026-11-17-chat-experience.md`；插件的更新记录和 `CHANGELOG.md` 引用它们作为改动的原因。`docs/design/README.md` 描述的是最早那几篇（来源项目的设计文档），没有对新文件另写规定。
- 文本文件用 LF 换行（`.gitattributes` 是 `* text=auto eol=lf`）。

## 发版

仓库里能看到的，只有这些：

- **网关的版本号写在两处**：`pyproject.toml` 的 `[project] version` 和 `src/feiyu_memgate/__init__.py` 的 `__version__`。没有测试检查两处一致，改版本号要两处都改。
- **`CHANGELOG.md`** 现在有“未发布”和“0.1.0”两节；条目先写在“未发布”下面。
- **插件的版本号**在各自的 `package.json`（主插件 0.3.2，附加包 0.1.0）。两个插件的 `tests/package.test.js` 都把自己的版本号写死了（主插件是 `assert.equal(pkg.version, '0.3.2')`），所以改版本号时测试也要一起改。主插件的 [CHANGELOG.md](../plugins/dsh-feiyu-memgate/CHANGELOG.md) 每个版本一节，写着日期和打包进去的网关对应的提交。`engines.dsh` 的范围也由 `package.test.js` 检查。
- **打包的网关**：`plugins/dsh-feiyu-memgate/python/` 和 `python/GATEWAY_VERSION.json` 由 `npm run sync-gateway` 生成（见上面）。测试要求记录里的 `dirty` 是 `false`，所以顺序是：先提交网关的改动，再运行 `sync-gateway`，再跑测试。
- npm 包里放什么由 `package.json` 的 `files` 决定（主插件：`index.js`、`src/`、`client/`、`python/`、`locale/`、`docs/`、`cordis.patch.yml`、`icon.png`、`LICENSE`），`package.test.js` 检查列出的每个路径都存在。

仓库里没有写的（所以这里也不写）：Python 包怎么构建和上传、两个 npm 包怎么发布、有没有打标签、谁来改版本号、“未发布”一节在发布时怎么改成版本号、网关和插件的发布先后。CI 里没有发布任务。
