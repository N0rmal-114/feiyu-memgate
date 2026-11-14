# 配置参考

网关的配置是一个 TOML 文件。先用 `feiyu-memgate check --config <文件>` 检查，它会打印这套拼图是怎么拼的，不显示任何密钥。

可以直接改的例子：

- [examples/quickstart/gateway.toml](../examples/quickstart/gateway.toml)：一个 SQLite 文件，不用向量模型
- [examples/multi-tenant/gateway.toml](../examples/multi-tenant/gateway.toml)：每个群一个 SQLite 文件，外加可公开清单
- [examples/openviking/gateway.toml](../examples/openviking/gateway.toml)：OpenViking 向量后端，每个群一个账号
- [examples/hybrid/gateway.toml](../examples/hybrid/gateway.toml)：关键词和向量合在一起的混合后端
- [examples/custom-stage/gateway.toml](../examples/custom-stage/gateway.toml)：放在配置旁边的自制召回步骤

**每一项设置在网关建立时就检查**：不认识的设置名、类型不对、超出范围，启动时就报错并指出是哪一项，不会等到请求来了才出错。客户端的某项限制如果没有任何拼图去执行（写了 `daily_writes` 却没配 `daily_cap` 守卫；`public = true` 却没有 `redact`），也拒绝启动，因为那等于一个看不见的漏洞。

## 密钥从哪来

配置文件里不放任何密钥。

- 客户端的令牌：`token_env = "变量名"`，网关启动时从这个环境变量读。**至少 32 个字符**；太短的、或者是示例里的占位符 `change-me` 的，一律不加载，这个客户端就无法认证（启动日志里会说是哪个客户端，不显示值）。两个客户端用了同一个令牌，启动报错（说出两个客户端的名字，不显示令牌）：同一个令牌分不出是谁。配置的令牌不能以 `fmg1.` 开头，那是铸造出来的租户令牌的标记。
- `tenant_binding = "token"` 的客户端，`token_env` 里放的是**铸造密钥**，规则同上（至少 32 个字符，不能和任何别的客户端的令牌或铸造密钥相同）。它自己不能用来认证，只用来铸造令牌，见下文“令牌绑定租户”。**铸造密钥一定要用下面的命令随机生成，不能用短语或手打的字符串**：拿到任何一个铸造出来的令牌的人，都能在自己的机器上离线猜铸造密钥，网关的限流管不到。`feiyu-memgate check` 对不像随机生成的铸造密钥（短于 43 个字符，或者用到的不同字符少于 16 个）打印一行提醒。
- 拼图的选项：名字以 `_env` 结尾的，会换成环境变量的值再传给拼图。`key_env = "OV_PRIVATE_KEY"` 等于 `key = <OV_PRIVATE_KEY 的值>`。变量没设置时启动报错。

生成一个令牌：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## `[server]`

| 设置 | 默认 | 意思 |
|---|---|---|
| `host` | `"127.0.0.1"` | 监听地址 |
| `port` | `8080` | 监听端口 |
| `max_connections` | `32` | 同时处理的连接数。超过的连接立刻得到 503（`busy`） |
| `read_deadline` | `10.0` | 一个请求最多用多少秒发完。慢慢发的连接会被切断 |
| `pool_size` | `8` | 访问存储的工作线程数 |
| `store_max_inflight` | `pool_size - 2`（至少 1） | 一个存储同时最多有几个调用在进行。一个卡住的存储因此占不满所有工作线程 |
| `tenant_pool_total` | `pool_size - 2`（至少 1） | 多租户请求同时最多几个在进行。多租户请求访问存储时，总会给别的客户端留下 `pool_size - tenant_pool_total` 个工作线程 |
| `tenant_pool_per_tenant` | `2` | 一个租户同时最多几个请求在进行 |

工作线程不够时不排队：召回最多等 0.25 秒（时限很短时更少），还没轮到的存储这次不查，计入结果里的 `busy`；写入等不到工作线程就回 503 `busy`，什么都没写。多租户的请求数超过上面两个上限，也是 503 `busy`。

网关说的是明文 HTTP。要跨机器用，请放在内网里或者前面加一层 TLS 反向代理。

## `[stores.<名字>]`：共享存储

表名就是这个存储的来源名，会出现在召回结果的 `source` 里，客户端策略也用它引用。`type` 选后端拼图，其余是它的选项。

**`openviking`**

| 选项 | 默认 | 意思 |
|---|---|---|
| `url` | 必填 | `http://主机[:端口]` |
| `key` / `key_env` | 必填 | 这个账号的密钥 |
| `writable` | `false` | 能不能写 |
| `timeout` | `2.0` | 单次网络操作的秒数 |
| `skip_names` | 摘要和人格文件 | 这些文件名不算记忆 |
| `floor` | `0.5` | OpenViking 原始分数里相当于“完全不相关”的值，低于或等于它的记 0 分 |
| `ceil` | `1.0` | 原始分数里相当于“完全相关”的值，高于它的记 1 分，必须大于 `floor` |

`floor`/`ceil` 把原始分数线性换算成 0..1（`(原始分数 - floor) / (ceil - floor)`，超出范围的截断）。换算不改变两条结果的先后顺序，原始值留在 `Hit.meta["raw_score"]`；网关的分数线按换算后的值比，依据见 [backends.md](backends.md)。

**`sqlite`**

| 选项 | 默认 | 意思 |
|---|---|---|
| `path` | 必填 | 数据库文件 |
| `writable` | `true` | 能不能写 |
| `roles` | `["user"]` | 写入时保留哪些角色的消息 |
| `max_candidates` | `2000` | 一次搜索最多给多少候选打分 |

**`inmemory`**：`writable`（默认 `true`）、`memories`（启动时放进去的文字）。进程退出就没了，只用于测试和演示。

**`embedding`**、**`hybrid`**：本地向量后端和混合后端，选项见 [backends.md](backends.md)。`embedding` 除 `path`、`url`、`model`
外，`floor`、`ceil`（余弦换算成 0..1 的区间）、`query_prefix`、`document_prefix`（取向量前加在查询 / 记忆文字前面的文字）不写时按模型名
取预设（bge-m3、bge-small-zh、bge-base-zh、embeddinggemma、qwen3-embedding 五个有预设；别的模型用 bge-m3 的数值并在日志里警告一次，
请自己校准）。`document_prefix` 和 `model` 一样记在库里，换了会拒绝打开旧库。

自己写的后端：`type = "模块:类名"`，见 [pieces.md](pieces.md)。

## `[tenants.<名字>]`：租户存储

表名同时是租户存储的来源名，不能和共享存储重名。

**`keyfile`**：一个 JSON 文件 `{"<租户编号>": "<这个租户的密钥>"}`，每个租户用自己的密钥访问自己的账号。文件改了会自动重读；读坏了保持上一份好的。

| 选项 | 默认 | 意思 |
|---|---|---|
| `path` | 必填 | 密钥文件 |
| `store` | 必填 | 后端的配置模板，每个租户的密钥会作为 `key` 加进去，例如 `{ type = "openviking", url = "..." }` |
| `id_pattern` | `"[0-9]{5,12}"` | 租户编号必须整个匹配这个正则 |

用 `feiyu-memgate tenants add|remove|list --keys <文件>` 管理这个文件。

**`directory`**：一个文件夹，每个租户一个 `<编号>.db`。

| 选项 | 默认 | 意思 |
|---|---|---|
| `path` | 必填 | 文件夹 |
| `id_pattern` | `"[0-9A-Za-z_-]{1,64}"` | 租户编号必须整个匹配 |
| `create` | `false` | 没见过的租户要不要自动建库。`false` 时只有已经有文件的租户才有记忆 |
| `store` | 无 | 给每个租户的 SQLite 后端的额外选项 |

## `[clients.<名字>]`：谁能做什么

每张表是一个客户端，通常也是一个令牌。至少要有一个客户端。

| 设置 | 默认 | 意思 |
|---|---|---|
| `token_env` | 不写 = 没有令牌 | 放令牌的环境变量名（`tenant_binding = "token"` 时放铸造密钥，这时必填）。不写时这个客户端只是一条策略，通过 HTTP 无法认证（和变量没设置一样），只能在同一个进程里直接调用网关时使用；写了但不是变量名（比如数字、空字符串）启动报错 |
| `recall` | 不写 = 不能召回 | 能搜的存储名字列表，按列出的顺序搜。`"@tenant"` = 这次请求的租户自己的存储。`[]` = 能问但什么都不搜 |
| `write` | 不写 = 不能写 | 写进哪个存储；`"@tenant"` = 租户自己的存储 |
| `tenants` | 无 | 租户提供方的名字。写了以后每次请求都是为某一个租户做的 |
| `tenant_binding` | `"request"` | 怎么知道请求是哪个租户的：`"request"` = 请求体里的 `tenant_id`（自报）；`"token"` = 令牌本身绑定一个租户。只能和 `tenants` 一起写 |
| `tenant_token_generation` | `1` | 只在 `tenant_binding = "token"` 时可写：铸造令牌的“代”，正整数。加一后重启，这个客户端以前铸造的令牌全部失效 |
| `public` | `false` | 内容会被别人看到：改用公开脱敏 |
| `daily_writes` | 不限 | 每天最多写几次（多租户按租户分别计） |
| `daily_writes_total` | 不限 | 整个客户端每天最多写几次（多租户是所有租户合计） |
| `rate_limit` | `60` | 每分钟最多几次请求（多租户按租户分别计） |
| `rate_limit_total` | `rate_limit` 的 10 倍 | 多租户客户端整个每分钟最多几次请求（所有租户合计）。`tenant_binding = "token"` 时一个租户最多用掉其中 `rate_limit` 次；默认模式下一个调用方就能用完 |
| `pin_writes` | `false` | 这个客户端写入的记忆自动钉住，不会被遗忘，见 [forgetting.md](forgetting.md) |

租户的存储只有在 `recall` 里写了 `"@tenant"` 才会被搜，位置就是它在列表里的位置；没有 `tenants` 的客户端写 `"@tenant"` 启动报错。一个群聊机器人通常这样写：

```toml
[clients.group]
token_env = "GATEWAY_TOKEN_GROUP"
tenants = "group"
recall = ["public_list", "@tenant"]   # 先搜共享的可公开清单，再搜这个群自己的
write = "@tenant"
public = true
daily_writes = 30                     # 每个群每天 30 次
daily_writes_total = 500              # 所有群合计每天 500 次
```

默认（`tenant_binding = "request"`）时 `tenant_id` 是客户端自己报的。网关只保证一个请求只碰到它报的那个租户的存储；客户端能不能代表这个租户，要由客户端自己（比如机器人框架给的群号）保证。这只适合一个可信的进程拿着这个令牌；`feiyu-memgate check` 对这样的客户端会打印一行提醒。令牌要交给别的程序时，用下面的令牌绑定。两种模式的取舍见 [security.md](security.md)。

### 令牌绑定租户

```toml
[clients.group_bot]
token_env = "GATEWAY_MINT_GROUP"      # 铸造密钥，不是令牌；用 secrets.token_urlsafe(32) 生成
tenants = "group"
tenant_binding = "token"
recall = ["public_list", "@tenant"]
write = "@tenant"
public = true
daily_writes = 30                     # 每个群；只设合计的话一个群就能用完
daily_writes_total = 500
```

给一个租户铸造令牌（在网关主机上，用和网关同一份配置和同一个环境变量）：

```bash
feiyu-memgate tenants token --config gateway.toml --client group_bot --tenant 10001
```

标准输出上只有一行，就是这个令牌，可以直接 `TOKEN=$(feiyu-memgate tenants token ...)`；出错时标准输出什么都没有，原因写在标准错误上，退出码 1。它在这些情况下拒绝：客户端不是 `tenant_binding = "token"`，租户编号不符合租户提供方的规则（或者 UTF-8 超过 256 字节），铸造密钥的环境变量没设置或不可用。它不打开任何存储，不检查租户有没有登记（先铸造、后登记也行，登记之前令牌得到 403 `unknown_tenant`），从不打印铸造密钥。令牌要像令牌一样保管：它在被撤销之前一直有效。

令牌里还记着客户端名字、它的租户提供方和 `tenant_token_generation`：把客户端改指另一个提供方、加一代、换铸造密钥，都会让以前的令牌失效。

限流的先后和默认模式不同：先查令牌所属租户自己的 `rate_limit`，过了才算进 `rate_limit_total`，所以一个租户被拒绝的请求不会用掉别的租户共享的合计；默认模式先查合计（编号是调用方随便写的），一个调用方就能把合计用光。每日上限两种模式一样：先占租户的 `daily_writes` 再占合计；租户之间互不信任时两个都要设。见 [security.md](security.md)。

拿着这种令牌的程序，请求里可以不写 `tenant_id`；写了就必须和令牌里的一样，否则 403 `tenant_mismatch`。自带的 `MemoryClient` 用 `MemoryClient(url, None, tenant_token=令牌, tenant_bound=True)`，所有调用都用这个令牌，可以不传 `tenant_id`；这种客户端不接受主令牌（给了就整个关掉，免得本该发给主客户端的调用落进租户的库）。

撤销：一个租户，从租户提供方删掉它；一个客户端的全部令牌，把 `tenant_token_generation` 加一（或者换铸造密钥）再重启网关。细节和限度见 [security.md](security.md)。

`daily_writes` 和 `daily_writes_total` 要 `[write]` 里有 `daily_cap` 守卫；`public = true` 的客户端能召回时要有 `redact` 召回步骤，能写时要有 `redact` 写入守卫。缺了启动报错，报错会说该加哪一块。

## `[recall]`

| 设置 | 默认 | 意思 |
|---|---|---|
| `deadline` | `3.0` | 一次召回的总时限（秒），包括召回步骤 |
| `cold_deadline` | `deadline` 和 `8.0` 中大的那个 | 隔了 `cold_after` 秒没有召回后，第一次召回的时限 |
| `cold_after` | `600` | 多久算“冷”。对上你的向量模型空闲多久被卸载 |
| `stages` | 见下 | 召回步骤，按顺序 |
| `profile` | `"precise"` | 分数线的档位，`"precise"` 或 `"broad"`，见下 |

时限管整个召回：存储的搜索最多用到时限前一点（留出时限的 20%，最多 0.5 秒给召回步骤），时限一到，还没问到的候选不再放进结果，计入 `more`，访问日志里记 `deadline`。

`stages` 的每一项是一个名字，或者带选项的内联表：

```toml
[recall]
stages = [
    { type = "min_score", value = 0.45, per_store = { notes = 0.3 } },
    "fill_text",
    "drop_marker",
    "drop_oversize",
    "redact",
    "dedupe",
    { type = "budget", max_chars = 1200 },
]
```

不写 `stages` 时用默认的一套：`min_score`、`relative_score`、`newer_first`、`fill_text`、`drop_oversize`、`redact`、`dedupe`、`budget`。`drop_marker`（丢掉写入自检留下的标记）不在默认里，要用就加在 `fill_text` 后面。要用遗忘曲线的 `freshness`（选项 `floor` 默认 0.7、`credit` 默认 `"top"`、`credit_ratio` 默认 0.9，见 [forgetting.md](forgetting.md)），放在 `min_score` 后面、`relative_score` 前面（它们都改分数或按分数比较，顺序见 [pieces.md](pieces.md)）。每个步骤的选项见 [pieces.md](pieces.md)。

`min_score` 不写 `value` 时，每条候选的分数线依次看：`per_store` 里这个存储的值，`value`，存储自己声明的默认分数线（`sqlite`、`inmemory` 是 0.30，`embedding` 按模型预设（bge-m3 是 0.50，其余见 [backends.md](backends.md)），`hybrid` 是 0.70，`openviking` 是 0.55；每个数是怎么来的见 [backends.md](backends.md)），都没有就是 0.5。`relative_score` 不写 `ratio` 时同理用存储的 `default_relative_ratio`（`sqlite`、`inmemory` 是 0.75，`embedding` 按模型预设（bge-m3 是 0.85），`hybrid` 是 0.85，`openviking` 是 0.95），都没有就是 0.6。所以一般不用手写分数线；要调，最好用 `feiyu-memgate eval` 或 `bench/dev_eval.py` 在自己的数据上量一下再定：

`profile = "broad"` 是一个快捷方式：`min_score` 和 `relative_score` 两步改用存储声明的宽松分数线（`broad_min_score`、`broad_relative_ratio`），
多答对一些换了说法的问题，代价是交出去的条目更杂、对没有答案的问题更容易开口（每个后端在开发集上的得失见 [backends.md](backends.md) 的“两档分数线”）。
不写它就是默认的 `"precise"`，行为和以前完全一样。它只加到**自己没有写 `profile` 的** `min_score` 和 `relative_score` 上，
不管 `stages` 是默认的还是自己写出来的；某一步自己写了 `profile = "precise"` 或 `"broad"`，就以它自己的为准；写了 `value` / `ratio` 的，数字永远优先于档位。
存储没有声明宽松值时（比如自己写的后端），`"broad"` 对它就是默认分数线。`profile` 不是这两个名字之一是配置错误；`feiyu-memgate check` 会打印当前档位。

```toml
[recall]
profile = "broad"
```

在步骤上也可以单独设，只让其中一步宽松：`{ type = "relative_score", profile = "broad" }`。

```bash
feiyu-memgate eval --store-type sqlite --store-option path=:memory: \
  --thresholds 0.10,0.15,0.20,0.25,0.30,0.35,0.40 \
  --memories examples/eval/memories.json --queries examples/eval/queries.json
```

在仓库自带的那批小虚构数据 `examples/eval` 上（问题多数是换了说法问的，对关键词检索不利；这是旧开发集，现在的默认值是在 `bench/dev` 上选的），`sqlite` 后端最多 5 条时：分数线 0.10 命中 100%、噪声 62%、安静 88%；0.20 命中 100%、噪声 31%、安静 100%；0.30 命中 77%、噪声 12%。再加上 `relative_score`（比例 0.6），0.20 时噪声降到 8%、命中仍是 100%（`bench/dev_eval.py` 的口径，噪声按问题平均）。关键词后端找的是“用词相同”的记忆，换个说法就可能找不到，这是它和向量后端的取舍。

## `[write]`

| 设置 | 默认 | 意思 |
|---|---|---|
| `deadline` | `10.0` | 一次写入交给存储的总时限（秒） |
| `guards` | `["redact", "daily_cap"]` | 写入守卫，按顺序 |

`daily_cap` 的“一天”默认按 UTC 算。按北京时间：

```toml
[write]
guards = ["redact", { type = "daily_cap", utc_offset_hours = 8 }]
```

## `[redaction]`

| 设置 | 默认 | 意思 |
|---|---|---|
| `private` | `"secrets"` | 非公开客户端用的脱敏 |
| `public` | `"public"` | `public = true` 的客户端用的脱敏 |

每一项是一个名字，或者带 `type` 和选项的表（`_env` 结尾的选项照样换成环境变量的值）：

```toml
[redaction]
private = "secrets"
public = { type = "my_cleaners:make", extra_words_env = "MASK_WORDS" }
```

## `[forgetting]`

可选的遗忘曲线，每项设置见 [forgetting.md](forgetting.md)；这里只提两项限速：`max_forget_fraction`（默认 0.02）和 `max_forget_min`（默认 5）：每次清理、每个存储最多忘 `max(max_forget_min, max_forget_fraction × 该存储条数)` 条。不认识的设置名同样启动报错。

## 写死的上限

这些不在配置里，是接口的一部分：

| 项 | 上限 |
|---|---|
| 请求体 | 64 KiB |
| 查询词 | 500 字 |
| 一次召回的条数 | 默认 5，最多 10 |
| 一次写入的消息数 | 20 条 |
| 一条消息 | 4000 字（超过就拒绝，不截断） |
| 一次写入合计 | 16000 字 |
| 消息的 `time` / `speaker` | 40 字 / 64 字 |
| `context` | 16 个键；键 1 到 64 字；值是最多 200 字的字符串、数字或 `true`/`false` |

## HTTP 接口

所有接口回 JSON。出错时是 `{"error": "...", "code": "..."}`：`error` 是给人看的，措辞可能改；`code` 是稳定的，程序请按 `code` 判断（见文末的表）。

### `POST /v1/recall`

```
Authorization: Bearer <令牌>
{"query": "周末去哪玩了", "limit": 5, "tenant_id": "10001", "context": {"channel": "voice"}}
```

- `limit` 可以不写；超出 1 到 10 的按 1 或 10 算，不是整数是 `bad_limit`。
- `tenant_id` 只有多租户客户端要写，而且必须写；别的客户端写了是 `bad_tenant`。`tenant_binding = "token"` 的客户端可以不写（就是令牌绑定的租户），写了必须和令牌里的一样，否则 403 `tenant_mismatch`。
- `context` 可以不写。它原样交给召回步骤和写入守卫（`ctx.context`），比如“这是语音频道”；网关自己不看内容，也不写进日志。

```json
{
  "results": [
    {"id": "m-0042", "source": "private", "text": "周末去了动物园看熊猫", "score": 0.83, "date": "2026-05-30"}
  ],
  "more": 2,
  "failed": 0,
  "busy": 0
}
```

- `id`：这条记忆在它的存储里的编号。不同存储的编号可能重复，要和 `source` 一起用。
- `more`：**最多**还有几条相关的没放下（条数、字数或时限到了）。是上限不是精确数：放不下的候选只经过便宜的步骤（`drop_marker`、`drop_oversize`、`dedupe`）筛过，其余步骤没有逐条看。
- `failed`：有几个存储这次出错或超时没查成。
- `busy`：有几个存储这次因为工作线程不够没去查（存储没坏，只是忙）。`failed` 或 `busy` 大于 0，说明结果可能不全。
- `date`：存储知道这条记忆是哪天的才有。
- 要搜的存储一个都没查成时不回空结果，而是 503：全都是忙为 `busy`，否则为 `unavailable`。“什么都没找到”和“没能去找”要分得开。

### `POST /v1/remember`

```
Authorization: Bearer <令牌>
{"messages": [{"role": "user", "content": "我对花生过敏", "time": "2026-11-14 20:15", "speaker": "阿青"}],
 "tenant_id": "10001", "context": {"channel": "voice"}}
```

- `time`、`speaker` 可以不写，写 `null` 等于没写。`time` 是这句话什么时候说的（任意格式的字符串），`speaker` 是谁说的。它们交给写入守卫和存储；公开客户端的这两项也会被脱敏。消息里的其他键丢掉。
- `tenant_id`、`context` 同上。

```json
{"status": "ok", "receipt": "w-3afa6d990f82418e8f5ff3e3aa4cd105"}
```

200 `ok` 的意思是**存储收下了**。对会在后台整理的存储（OpenViking），这还不等于“已经变成一条记忆”，整理的结果在深度健康检查里统计。

```json
{"status": "skipped"}
```

200 `skipped`：写入守卫或存储看过之后什么都没留（比如守卫把每条都筛掉了）。不算今天的写入次数，也不跟踪。

### `POST /v1/used`

聊天程序告诉网关：这次回复**真的用了**刚召回的哪几条记忆。只有配置了遗忘（`[forgetting]`）才有作用；没配置时照样回 200，`recorded` 是 0，客户端不需要知道有没有开。

```
Authorization: Bearer <令牌>
{"used": [{"source": "private", "id": "m-0042"}], "tenant_id": "10001"}
```

- `used`：最多 20 项，每项是召回结果里的 `source` 和 `id`（别的键忽略，所以可以把召回结果原样放进去）。空列表可以，什么也不记。同一条写两次算一次。
- `source` 写召回结果里的 `source`：共享存储是它的名字，租户存储是租户提供方的名字（不是 `"@tenant"`）。每一项的 `source` 都必须是这个客户端能召回的来源，否则整个请求回 403 `forbidden`；不存在的来源和没有权限的来源回答一样，所以不会泄露哪些存储存在。
- 鉴权、限流（和召回共用同一份每分钟的额度）、`tenant_id` 的规则都和 `/v1/recall` 一样：多租户客户端必须带（令牌绑定租户的可以不带，带了必须一致），别的客户端带了是 `bad_tenant`。
- 格式不对（`used` 不是列表、超过 20 项、某项缺 `source` 或 `id`、不是字符串、`id` 超过 512 字、`source` 超过 128 字）整个请求回 400 `bad_used`，一条都不记。
- 对每一条存在的记忆，在账本里记一次“用到”（和召回时记一次一样：两个计时都从现在重新算，次数加一）。网关先看账本里有没有这条，没有的话问存储能不能读出来；编造的 id 什么都不记，也不留行。**它只能刷新记忆的时钟，不能删除、改写或读出任何记忆。**
- 来源没有被 `[forgetting].stores` 管理的，不报错，也不算进 `recorded`。

```json
{"status": "ok", "recorded": 1}
```

`recorded` 是真的记下的条数；它是网关对“id 是否存在”说的唯一的话。

### `GET /healthz`

不需要令牌。`/healthz` 只回 `{"status": "ok"}`。`/healthz?deep=1` 多回这些（只有名字和数字，没有内容）：

```json
{
  "status": "ok",
  "stores": {"private": "ok"},
  "recall_failed_1h": 0,
  "recall_busy_1h": 0,
  "writes_24h": {"saved": 3, "empty": 0, "missing": 0, "untracked": 0, "pending": 1}
}
```

- `stores`：每个共享存储现在答不答应。
- `recall_failed_1h`：最近 1 小时有几次召回里有存储没查成（出错、超时，或者召回步骤出错）。
- `recall_busy_1h`：最近 1 小时有几次召回因为工作线程不够，有存储没去查（没有存储出错时才算在这里）。
- `writes_24h`：最近 24 小时的写入，存下了几次、整理后什么都没留几次、一直没消息几次、还没等到结果就被挤出跟踪表几次（`untracked`，同时等待的写入超过 200 次时）、还在等几次。只统计会回报写入结果的存储。

### `Expect: 100-continue`

客户端发 `Expect: 100-continue`（HTTP/1.1）时，网关先看令牌、权限、限流和 `Content-Length`，都通过了才回 `HTTP/1.1 100 Continue` 让它发请求体；不通过就直接回最终的答案，请求体不用发。不发这个头的客户端不受影响。

### 错误码

| `code` | 状态码 | 意思 |
|---|---|---|
| `bad_request` | 400 | 请求体不是 JSON 对象，或者 `Content-Length` 不对 |
| `bad_query` | 400 | 查询词空、不是字符串或超过 500 字 |
| `bad_limit` | 400 | `limit` 不是整数 |
| `bad_messages` | 400 | 消息格式不对、太长或太多 |
| `bad_context` | 400 | `context` 不合规 |
| `bad_tenant` | 400 | `tenant_id` 格式不对，或者这个客户端不该带 `tenant_id` |
| `bad_used` | 400 | `/v1/used` 的 `used` 不合规：不是列表、超过 20 项、某项缺 `source` 或 `id`、类型不对或太长 |
| `unauthorized` | 401 | 令牌不对或没带（铸造密钥本身当令牌用也是这个） |
| `forbidden` | 403 | 这个客户端不许做这件事（`/v1/used` 里：来源不是它能召回的） |
| `unknown_tenant` | 403 | 这个租户没登记 |
| `tenant_mismatch` | 403 | 令牌绑定了一个租户，请求体里的 `tenant_id` 是别的（或者格式不对）；什么都没做 |
| `not_found` | 404 | 没有这个路径 |
| `method_not_allowed` | 405 | 这个路径不收这个方法 |
| `length_required` | 411 | 没有 `Content-Length` |
| `too_large` | 413 | 请求体超过 64 KiB |
| `rate_limited` | 429 | 请求太快 |
| `daily_limit` | 429 | 今天的写入次数用完了 |
| `internal` | 500 | 网关内部出错（日志里有错误类型）。写入时**不确定**写没写进去 |
| `guard_error` | 500 | 一块写入守卫出错：没写，已经做过的守卫都撤回了 |
| `backend_unavailable` | 502 | 写入时存储出错或超时。**可能已经写进去了，也可能没有** |
| `busy` | 503 | 工作线程、租户名额或连接数满了，什么都没做，可以稍后重试 |
| `unavailable` | 503 | 召回时要搜的存储都没查成 |
| `stage_error` | 503 | 一块召回步骤在整理全部候选时出错，这次不返回任何结果 |

自己写的写入守卫拒绝时可以给自己的 `code`（见 [pieces.md](pieces.md)），不给就按状态码取上表里的通用值。

写入出错时，除了 `backend_unavailable`、`internal` 和不认识的 5xx，其余的都**确定没写进去**。自带的客户端（`MemoryClient`）据此把写入结果报成 `failed`、`limited` 或 `unsure`。
