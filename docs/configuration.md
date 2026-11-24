# 配置参考

网关的配置是一个 TOML 文件。先用 `feiyu-memgate check --config <文件>` 检查，它会打印这套拼图是怎么拼的，不显示任何密钥。

可以直接改的例子：

- [examples/quickstart/gateway.toml](../examples/quickstart/gateway.toml)：一个 SQLite 文件，不用向量模型
- [examples/multi-tenant/gateway.toml](../examples/multi-tenant/gateway.toml)：每个群一个 SQLite 文件，外加可公开清单
- [examples/two-hosts/gateway.toml](../examples/two-hosts/gateway.toml)：DSH 和 QQ 机器人共用一个网关，身份表认出同一个主人，群里的记忆另放
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

所有存储都可以再写一项 `audience`（`"owner"`、`"group"`、`"public"`，默认 `"owner"`）：里面的内容最多可以给谁看，见下面的“受众”一节。它由配置读取这一步取走，不会交给后端，所以后端不需要认识它。

自己写的后端：`type = "模块:类名"`，见 [pieces.md](pieces.md)。

## `[tenants.<名字>]`：租户存储

表名同时是租户存储的来源名，不能和共享存储重名。可以再写 `audience`（`"owner"`、`"group"`、`"public"`，默认 `"group"`：每个租户的存储只给这个租户看），规则见下面的“受众”一节。

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
| `public` | `false` | 内容会被别人看到：改用公开脱敏。只写了 `audience` 不写 `public` 时由它推出 |
| `audience` | 见“受众”一节 | 这个客户端展示的内容会被谁看到：`"owner"`、`"group"`、`"public"`。启动时拿它和 `recall` 里每个存储的受众核对 |
| `explain` | `false` | 允许这个客户端用 `POST /v1/explain`：召回之外还能看到每条候选的分数和被哪一步拿掉。必须是布尔值；只给主人级别的客户端：要有 `recall`，受众是 `owner`，否则启动报错，见 [security.md](security.md) |
| `origins` | `["said", "tool"]` | 这个客户端写入的消息可以写哪些 `origin`（来历）：`"said"`、`"tool"`、`"curated"`、`"derived"` 里的若干个，其他的值启动报错；可以写 `[]`（一个来历都不许写）。消息写了不在这里的来历，整个请求 403 `forbidden`。来历只是宿主自己说的记录，网关不凭它给任何权限 |
| `fields` | `[]` | 召回的每一项里带上哪些记忆字段（`"speaker"`、`"about"`、`"channel"`、`"audience"`、`"origin"`、`"event_date"`、`"kind"` 里的若干个，其他的值启动报错）。不写时召回的回答和以前一模一样。受众不是 `owner` 的客户端不能列 `speaker` 和 `about`（只有主人级的客户端能看到一条记忆说的是谁），启动报错 |
| `daily_writes` | 不限 | 每天最多写几次（多租户按租户分别计） |
| `daily_writes_total` | 不限 | 整个客户端每天最多写几次（多租户是所有租户合计） |
| `rate_limit` | `60` | 每分钟最多几次请求（多租户按租户分别计） |
| `rate_limit_total` | `rate_limit` 的 10 倍 | 多租户客户端整个每分钟最多几次请求（所有租户合计）。`tenant_binding = "token"` 时一个租户最多用掉其中 `rate_limit` 次；默认模式下一个调用方就能用完 |
| `pin_writes` | `false` | 这个客户端写入的记忆自动钉住，不会被遗忘，见 [forgetting.md](forgetting.md) |
| `platforms` | `[]` | 这个客户端可以报的账号平台（账号 `aiocqhttp:10001` 的平台是 `aiocqhttp`），每个符合 `[a-z0-9][a-z0-9_.-]{0,31}`。写入消息的 `speaker` 和 `about`、召回的 `speaker` 的平台必须在这里，否则 403 `forbidden`；不写就一个账号都报不了。必须有 `[identity]`，否则启动报错（没有谁来执行它），见下面“`[identity]`：身份表” |
| `only_for` | `[]` | 只替这几个人说话：`[identity.people]` 里的人名。写了以后，这个客户端写入的每条 `user` 消息、每次召回都必须带一个属于这几个人的 `speaker`，否则整个请求 403 `forbidden`。必须有 `[identity]`，名字必须在表里，否则启动报错 |

租户的存储只有在 `recall` 里写了 `"@tenant"` 才会被搜，位置就是它在列表里的位置；没有 `tenants` 的客户端写 `"@tenant"` 启动报错。一个群聊机器人通常这样写：

```toml
[clients.group]
token_env = "GATEWAY_TOKEN_GROUP"
tenants = "group"
recall = ["public_list", "@tenant"]   # 先搜共享的可公开清单（它的 [stores.public_list] 要写 audience = "public"），再搜这个群自己的
write = "@tenant"
public = true
daily_writes = 30                     # 每个群每天 30 次
daily_writes_total = 500              # 所有群合计每天 500 次
```

### 受众：存储给谁看，客户端被谁看

以前 `recall` 里写什么存储，网关就读什么存储，没有人检查。把放私聊记忆的存储写进群聊客户端的名单，网关照常启动，私下说的话就会出现在群里。现在配置要说清楚两件事，启动时核对，对不上就拒绝启动。

三个级别，从窄到宽：`owner`（只给主人）、`group`（给一个群的成员）、`public`（谁都可以看）。

- **存储的受众**：里面的内容最多可以给谁看。写在 `[stores.<名字>]` 和 `[tenants.<名字>]` 里。
- **客户端的受众**：它展示的内容会被谁看到。写在 `[clients.<名字>]` 里。

两条规则：

1. 客户端只能把受众不比自己窄的存储写进 `recall`。主人级的客户端什么都能读；群级的客户端能读 `group` 和 `public` 的存储；公开的客户端只能读 `public` 的存储。`"@tenant"` 的受众是这个客户端的租户来源的受众。
2. 带 `tenants` 的群级客户端同时服务很多个群。一个共享存储如果是 `group`，它只属于其中一个群，所以这样的客户端不能读它：它读的共享存储必须是 `public`。

没写的时候：存储是 `owner`，租户来源是 `group`。客户端不写 `audience` 时，不公开的是 `owner`；`public = true` 的，带 `tenants` 是 `group`，不带是 `public`。只写 `audience` 不写 `public` 时，`public` 由它推出（`owner` 为 `false`，其余为 `true`）；两个都写而互相矛盾（`audience = "owner"` 配 `public = true`，或 `group`、`public` 配 `public = false`）会启动报错。

写入不按受众拒绝：整理“可公开清单”的客户端本来就是主人级的客户端往 `public` 存储里写。反过来，受众不是 `owner` 的客户端往 `owner` 的存储里写，`feiyu-memgate check` 会打印一行提醒。

```toml
[stores.private]
type = "sqlite"
path = "data/private.db"                # 不写 audience：owner，群聊客户端读它会启动报错

[stores.public_list]
type = "sqlite"
path = "data/public-list.db"
audience = "public"                     # 里面的内容确认过可以给任何人看

[clients.group]
tenants = "group"
recall = ["public_list", "@tenant"]     # public_list 是 public，@tenant 取租户来源的受众（默认 group）
public = true                           # 带 tenants，所以客户端的受众是 group
```

已有的配置里，公开客户端读着的存储现在要补一行 `audience`；写成 `public` 就是你确认它里面的内容可以给任何人看。网关检查的是这些声明对不对得上，声明本身由你负责，见 [security.md](security.md)。

默认（`tenant_binding = "request"`）时 `tenant_id` 是客户端自己报的。网关只保证一个请求只碰到它报的那个租户的存储；客户端能不能代表这个租户，要由客户端自己（比如机器人框架给的群号）保证。这只适合一个可信的进程拿着这个令牌；`feiyu-memgate check` 对这样的客户端会打印一行提醒。令牌要交给别的程序时，用下面的令牌绑定。两种模式的取舍见 [security.md](security.md)。

### `[identity]`：身份表

宿主报告谁在说话，用账号 `<平台>:<编号>`（`aiocqhttp:10001`）。同一个人在两个宿主上是两个陌生人，各个宿主还各自存一份“谁是主人”。`[identity]` 让网关有一张人和账号的表，所有来问的宿主得到同一个答案，并拦住宿主替不属于它的账号说话。

```toml
[identity]
type = "table"
hash_key_env = "GATEWAY_IDENTITY_KEY"     # 给没登记的账号做替身码的密钥，放环境变量里

[identity.people.owner]
owner = true                              # 整张表正好一个主人
accounts = ["dsh:local", "aiocqhttp:10001"]

[identity.people.amy]
accounts = ["aiocqhttp:20002"]
```

| 设置 | 默认 | 意思 |
|---|---|---|
| `type` | 必填 | 目前只有 `"table"`；自己写的来源见 [extending.md](extending.md#写自己的身份来源) |
| `hash_key_env` | 无 | 放密钥的环境变量名，和 `token_env` 一样只写变量名。有客户端能写时必填，变量必须已设置，而且和令牌一样**至少 32 个字符、不能是 `change-me`**；所有客户端都只读时可以不写，变量没设置也不报错 |
| `people.<名字>` | | 一个人一张表。名字是 `[a-z0-9][a-z0-9_-]{0,31}`，`self` 和 `anon` 是保留的 |
| `people.<名字>.owner` | `false` | 是不是主人。整张表正好一个人写 `true` |
| `people.<名字>.accounts` | `[]` | 这个人的账号。账号是 `<平台>:<编号>`：平台是 `[a-z0-9][a-z0-9_.-]{0,31}`，冒号后至少一个字符、没有空白和控制字符，整个账号最多 64 个字符。一个账号只能列给一个人 |

启动时检查，不对就报错，错误消息说是哪一项，不显示账号，也不显示密钥：名字或账号不符合格式、用了保留名、一个账号列给了两个人、主人不是正好一个、不认识的设置名、`hash_key_env` 指的变量没设置或密钥太短（有客户端能写时）、`type` 不是登记过的来源。密钥不能写在文件里（`hash_key = "..."` 是不认识的设置）。

有 `[identity]` 时，写入消息里的 `speaker` 和 `about` 必须是账号，网关在写入守卫和脱敏之前把它们换成人名：登记在表里的换成名字，没登记的换成替身码 `anon:` 加 16 个十六进制字符，由密钥、存储名和账号算出，所以同一个账号在同一个存储里总是同一个码，换一个存储、换一个账号或换一个密钥，码就不同。换算以后原来的账号不会再出现在守卫、存储和日志里。目标存储记录字段时，换算后的 `speaker`（写了 `about` 的还有 `about`）进记忆的字段，主人级的客户端用 `fields = ["speaker"]` 能在召回结果里看到。角色是 `assistant` 的消息，说话人一律是 `self`。没有 `[identity]` 时一切照旧。请求和回答的细节见 [api.md](api.md#账号和身份)。

两个规则管着客户端能说什么：

- **`platforms`**：客户端只能报它列出的平台的账号。每个宿主一个客户端、各配各的 `platforms`，一个宿主就不能替别的宿主的账号说话。
- **`only_for`**：客户端只替名单里的人说话。一个只服务某一个人的宿主（比如这个人的私聊），其余的人说的话进不来。

一个完整的小例子：两个宿主（DSH 和 QQ 机器人）共用一个存储，同一个人在两边各有一个账号：

```toml
[stores.main]
type = "sqlite"
path = "data/main.db"

[identity]
type = "table"
hash_key_env = "GATEWAY_IDENTITY_KEY"

[identity.people.owner]
owner = true
accounts = ["dsh:local", "aiocqhttp:10001"]

[identity.people.amy]
accounts = ["aiocqhttp:20002"]

[clients.dsh]
token_env = "GATEWAY_TOKEN_DSH"
recall = ["main"]
write = "main"
platforms = ["dsh"]                       # 只能报 dsh: 开头的账号
fields = ["speaker"]                      # 主人级的客户端，召回结果里带上是谁说的

[clients.qq]
token_env = "GATEWAY_TOKEN_QQ"
recall = ["main"]
write = "main"
platforms = ["aiocqhttp"]
only_for = ["owner", "amy"]               # 只替这两个人说话：别人的消息整个请求被拒
```

`dsh` 写入 `speaker = "dsh:local"` 和 `qq` 写入 `speaker = "aiocqhttp:10001"` 的记忆，记的都是 `owner`；`qq` 报 `dsh:local` 得到 403。

`POST /v1/used` 不带 `speaker`，不受 `only_for` 管；它只能刷新记忆的时钟，不读出任何记忆。`feiyu-memgate check` 显示身份来源、每个人有几个账号（不显示账号）、谁是主人、有没有密钥（只说有或没有），客户端那一行显示它的 `platforms` 和 `only_for`；能写入、有 `[identity]` 却没设 `platforms` 的客户端会提醒一行：它报的每个说话人都会被拒绝。

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

### 每条记录的受众

上面的受众是存储这一层的声明。它只在启动时核对配置对不对得上，核对不了存储里实际放着什么：把主人的存储文件复制一份、在配置里当成 `public` 存储用，或者两个名字指向同一个文件、一个写 `owner` 一个写 `public`，声明都对得上，里面的内容却会给公开客户端看。所以每条记忆还带着自己的受众（记忆的字段 `audience`），召回时再检查一次。

- **写入时盖章。** 写进记录字段的存储（`sqlite`、`embedding`、`hybrid`、`inmemory`）的每条记忆，都带着这个存储的受众（租户的存储用租户来源的受众），消息可以要求更窄的，不能更宽（见 api.md 的 `POST /v1/remember`）。
- **旧记忆补章。** 一个存储第一次被网关打开时（可写、有字段列），里面没有受众的记忆都得到这个存储声明的受众，并在存储里记下“已经打过标记”（SQLite 类的存储是 `info` 表里的 `audience_stamp`）。共享存储在网关建好时打，租户的存储每次由提供方交出时打（第一次之后什么都不做）。只读打开的旧文件不改，也没有字段，只受存储这一层的规则管。
- **没有受众就是 `owner`。** 打过标记之后，不带受众进来的记忆（直接 `put()` 不带字段、旧版本往文件里写、直接的 SQL）保持没有受众，召回时按 `"owner"` 处理：只给主人级的客户端看，不给任何别人看（宁可少给）。给这样的记忆补上受众，用 `feiyu-memgate restamp`。
- **召回时检查。** 召回步骤 `record_audience`（默认的一套里有）拿掉受众比这个客户端窄的记忆；`linked` 接上的记忆同样检查；主人级的客户端不受影响。存储不记录字段，或者没打过标记（只读的旧存储、第三方后端），这一步放行，只有存储这一层的规则管它。解释召回里它拿掉的候选，`stage` 写 `record_audience`。

```bash
feiyu-memgate restamp --config gateway.toml --store public_list --missing   # 只给没有受众的记忆补上
feiyu-memgate restamp --config gateway.toml --store public_list --all       # 改了声明之后，整个存储都改成声明的受众
feiyu-memgate restamp --config gateway.toml --store group --tenant 10001 --all   # 一个租户自己的存储
```

`--missing` 给没有受众的记忆设上这个存储声明的受众；`--all` 给每一条设上它，是配置里改了存储的声明之后有意放宽或收窄整个存储的办法。命令打印改了几条。`check` 会对“受众不是主人的客户端召回的存储里有不逐条记录受众的”提醒一行。

## `[recall]`

| 设置 | 默认 | 意思 |
|---|---|---|
| `deadline` | `3.0` | 一次召回的总时限（秒），包括召回步骤 |
| `cold_deadline` | `deadline` 和 `8.0` 中大的那个 | 隔了 `cold_after` 秒没有召回后，第一次召回的时限 |
| `cold_after` | `600` | 多久算“冷”。对上你的向量模型空闲多久被卸载 |
| `stages` | 见下 | 召回步骤，按顺序 |
| `profile` | `"broad"` | 分数线的档位，`"broad"` 或 `"precise"`，见下 |

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

不写 `stages` 时用默认的一套：`min_score`、`relative_score`、`newer_first`、`linked`、`record_audience`、`fill_text`、`drop_oversize`、`redact`、`dedupe`、`budget`。`record_audience` 逐条检查记忆自己的受众（见“每条记录的受众”）：记忆的受众比这个客户端的窄就拿掉，不用选项；写了自己的 `stages`、又有受众不是 `owner` 且能召回的客户端时，必须把它列进去，否则启动报错：`clients.NAME shows what it recalls to other people, but no recall stage checks each record's audience: add "record_audience" to [recall] stages`。`drop_marker`（丢掉写入自检留下的标记）不在默认里，要用就加在 `fill_text` 后面。要用遗忘曲线的 `freshness`（选项 `floor` 默认 0.7、`credit` 默认 `"top"`、`credit_ratio` 默认 0.9，见 [forgetting.md](forgetting.md)），放在 `min_score` 后面、`relative_score` 前面（它们都改分数或按分数比较，顺序见 [pieces.md](pieces.md)）。每个步骤的选项见 [pieces.md](pieces.md)。

**`budget`：记忆普遍很长时，把字数预算调大。** `budget` 给一次召回交出去的总字数封顶，默认 1500 个字符；放不下的记忆整条不放，不截断，所以一条比预算还长的记忆永远交不出去。记忆是几十到几百个字符的短句时，这个默认值够用。记忆是整段的长文本（几千个字符的回话、文档片段）时，排在前面的几条常常放不下，交出去的就比 `limit` 少得多。这时自己写 `stages`，把默认的一套照抄，只改最后一项：

```toml
[recall]
stages = [
    "min_score", "relative_score", "newer_first", "linked", "record_audience",
    "fill_text", "drop_oversize", "redact", "dedupe",
    { type = "budget", max_chars = 6000 },
]
```

调多大看两头：记忆一般有多长，和每次愿意往模型的上下文里放多少字。预算越大交得越多，其中不相关的也越多。在两套英文题库上量过（只用 `embedding`，`broad` 档，`limit` 用默认的 8；这两套题库把助手的长篇回话也算作记忆）：

| 预算 | 每题交出的字符 | 交出的里面有证据的题 | 交出的里面证据占的比例 |
|---|---|---|---|
| 1500（默认） | 1242 和 1033 | 62.3% 和 78.5% | 21.2% 和 37.7% |
| 3000 | 2109 和 1916 | 62.8% 和 82.8% | 18.6% 和 33.0% |
| 6000 | 4934 和 3696 | 66.2% 和 88.2% | 17.5% 和 27.6% |
| 12000 | 9173 和 4384 | 66.2% 和 88.2% | 14.7% 和 26.6% |

第一套题库的记忆平均两千多个字符，预算从 1500 加到 3000 几乎没有用，到 6000 才见效；第二套的记忆短一些，加到 6000 以后再加就没有变化了，剩下没交出去的是被分数线挡住的，不归预算管。排名不受预算影响。这些数字来自 0.4.0 开发期间的一次测量，结果文件还没有放进仓库。写了自己的 `stages` 以后，以后的版本往默认的一套里加新步骤时，这份配置不会自动跟着多出来。

**`linked`：把和最相关的几条连着的记忆一起带回来。** 默认在 `newer_first` 后面、`fill_text` 前面。分数线决定了交哪些之后，它看排在最前面的 `top` 条候选，向它们所在的存储要“和这一条最像的记忆”（后端的可选方法 `neighbours`，见 [pieces.md](pieces.md)），把相似度不低于 `minimum` 的、还不在候选里的最多 `max_added` 条接在后面，而且只在候选数还没到这次请求的 `limit` 时才接：`limit` 仍然是“最多这么多条”。接上的候选和别的候选一样走后面的 `fill_text`、`drop_oversize`、`redact`、`dedupe`、`budget`，所以脱敏和字数上限照常管着它们；它们的分数排在所有第一轮候选后面；HTTP 回答的字段不变（回答里看不出哪条是接上的）。它在分数线之后才起作用，所以不改变“什么时候保持安静”：一条候选都没有就什么也不加。

| 选项 | 默认 | 意思 |
|---|---|---|
| `top` | `2` | 跟着最前面的几条候选去找，1 到 5 |
| `max_added` | `2` | 最多接几条，0 到 5；`0` 就是关掉 |
| `minimum` | `0.60` | 相似度不低于它才接，0 到 1，是存储自己的 0..1 分数（`embedding` 里是 `floor`/`ceil` 换算后的相关度） |

只有存储有 `neighbours` 才起作用：`embedding` 和 `hybrid`（里面有 `embedding` 子后端时）有，`sqlite`、`inmemory`、`openviking` 没有，只用关键词存储时什么都不变。每跟着一条候选，`embedding` 要把全部已存的向量过一遍（不请求 embeddings 接口），所以一次召回最多多 `top` 遍，见 [backends.md](backends.md)。出错、超时、存储没有这个方法都当作“什么也没加”，出错和超时在访问日志里记一次 `linked-failed`（不带内容）；这一步最多等 1 秒、不超过剩余时间的一半，卡住的存储不会拖掉已经查到的结果。`0.60` 是在 bge-m3 上量出来的，别的模型沿用它没有量过；数字和取舍见 [design/2026-11-18-linked-recall.md](design/2026-11-18-linked-recall.md)。

`[recall] profile = "precise"` 时，`linked` 默认什么也不加：它只在 `broad` 档下量过，`precise` 要的是少而准的答案，不该再往里添。这一档下想要它，在 `linked` 上自己写 `max_added`（写了的数永远优先，任何档位都一样），例如 `{ type = "linked", max_added = 2 }`。`feiyu-memgate check` 会在“分数线档位”后面多一行“关联召回”，写明最多加几条，或者“precise 档下不加”。

不想要它：`max_added = 0`，或者自己写一套不含 `linked` 的 `stages`（写了自己的 `stages` 的配置不会因此多出这一步）：

```toml
[recall]
stages = [
    "min_score", "relative_score", "newer_first",
    { type = "linked", top = 2, max_added = 2, minimum = 0.6 },   # 不要就去掉这一行，或把 max_added 写成 0
    "fill_text", "drop_oversize", "redact", "dedupe", "budget",
]
```

用遗忘曲线时，接上的记忆交出去了，就和别的记忆一样算“交付”：`freshness` 的 `credit` 规则照常对它们起作用（`"all"` 记一次“用到”，`"none"` 不记，`"top"` 只记相似度不低于最高者 `credit_ratio` 倍的；接上的记忆的相似度是它排在后面的那个分数，所以 `"top"` 下通常不记），见 [forgetting.md](forgetting.md)。`linked` 要放在 `freshness` 后面。

`min_score` 不写 `value` 时，每条候选的分数线依次看：`per_store` 里这个存储的值，`value`，存储按分数线档位声明的分数线：默认档位 `broad` 用 `broad_min_score`（`sqlite`、`inmemory` 是 0.25，`embedding` 按模型预设（bge-m3 是 0.30，其余见 [backends.md](backends.md)），`hybrid` 是 0.40，`openviking` 是 0.55），`precise` 档用 `default_min_score`（`sqlite`、`inmemory` 是 0.30，bge-m3 是 0.50，`hybrid` 是 0.70，`openviking` 是 0.55；每个数是怎么来的见 [backends.md](backends.md)）；存储没有声明某一档就用它的 `default_min_score`，都没有就是 0.5。`relative_score` 不写 `ratio` 时同理：`broad` 档用 `broad_relative_ratio`（`sqlite`、`inmemory` 是 0.70，bge-m3 是 0.60，`hybrid` 是 0.65，`openviking` 是 0.90），`precise` 档用 `default_relative_ratio`（`sqlite`、`inmemory` 是 0.75，bge-m3 是 0.85，`hybrid` 是 0.85，`openviking` 是 0.95），都没有就是 0.6。`default_` 前缀是历史命名，指的是 `precise` 这一档，不是默认档位。所以一般不用手写分数线；要调，最好用 `feiyu-memgate eval` 或 `bench/dev_eval.py` 在自己的数据上量一下再定：

`profile` 是 `min_score`、`relative_score` 和 `linked` 三步共用的快捷方式，默认是 `"broad"`：用存储声明的宽松分数线（`broad_min_score`、`broad_relative_ratio`），
聊天里一个话题通常牵到好几条记忆，所以默认每题交得多一些（用向量存储时约 2.5 条，`precise` 约 1 条）；代价是交出去的条目更杂、对没有答案的问题更容易开口（每个后端在开发集上的得失见 [backends.md](backends.md) 的“两档分数线”）。“约 2.5 条”是每次最多交 5 条时量的；现在默认最多 8 条，这个数字没有重测。
写 `profile = "precise"` 就回到以前的分数线：每题约 1 条，交出去的大部分是对的，漏答更多；`linked` 在这一档下默认也不再接记忆（见上面 `linked` 一节）。
不写它就是 `"broad"`。它只加到**自己没有写 `profile` 的** `min_score`、`relative_score` 和 `linked` 上（`linked` 的 `profile` 只决定 `max_added` 不写时的默认：`precise` 是 0，其余是 2），
不管 `stages` 是默认的还是自己写出来的；某一步自己写了 `profile = "precise"` 或 `"broad"`，就以它自己的为准；写了 `value` / `ratio` 的，数字永远优先于档位。
存储没有声明宽松值时（比如自己写的后端），两档对它都用 `default_min_score` / `default_relative_ratio`。`profile` 不是这两个名字之一是配置错误；`feiyu-memgate check` 会打印当前档位。

```toml
[recall]
profile = "precise"   # 回到以前的分数线；不写这一项就是 "broad"
```

在步骤上也可以单独设，只让其中一步用另一档：`{ type = "relative_score", profile = "precise" }`。

```bash
feiyu-memgate eval --store-type sqlite --store-option path=:memory: \
  --thresholds 0.10,0.15,0.20,0.25,0.30,0.35,0.40 \
  --memories examples/eval/memories.json --queries examples/eval/queries.json
```

在仓库自带的那批小虚构数据 `examples/eval` 上（问题多数是换了说法问的，对关键词检索不利；这是旧开发集，现在两档的分数线是在 `bench/dev` 上选的；下面的数字是用手写的 `--thresholds` 量的，与档位无关），`sqlite` 后端最多 5 条时：分数线 0.10 命中 100%、噪声 62%、安静 88%；0.20 命中 100%、噪声 31%、安静 100%；0.30 命中 77%、噪声 12%。再加上 `relative_score`（比例 0.6），0.20 时噪声降到 8%、命中仍是 100%（`bench/dev_eval.py` 的口径，噪声按问题平均）。关键词后端找的是“用词相同”的记忆，换个说法就可能找不到，这是它和向量后端的取舍。

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

请求体、查询词、消息、`context` 和 `/v1/used` 的上限不在配置里，是接口的一部分，写在 [api.md](api.md#写死的上限)。

## HTTP 接口

请求、回答、认证、每个接口可能回的状态码和错误码，以及 `Expect: 100-continue`，写在 [api.md](api.md#http-接口)。
