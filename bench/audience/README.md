# 受众隔离场景

一组端到端的场景，不用任何模型，检查两件事：一条记忆不会到达它不是给的那些人；同一个人在每个宿主上都是同一个人。每个场景是一个 JSON 文件，讲一件小事，从上往下读就是一段经过；`run.py` 把它建成真正的网关（`config.build_gateway`，真的 SQLite 文件，放在临时文件夹里，所以文件可以复制），按顺序执行，逐项核对。以后加功能，加的是场景文件，不是新的测试代码。

场景里的句子都是编的，由语言模型写出，再另外检查过一遍：说的是茶、猫、搬家这类无害的事；账号（`aiocqhttp:10001`、`aiocqhttp:30003` 等）、群号和人名也都是编的，令牌和替身码的密钥由 `run.py` 自己造。没有真实的人、账号或路径。

## 运行

```bash
python -B bench/audience/run.py                          # 全部场景
python -B bench/audience/run.py owner-private-vs-group   # 只跑指定的（按 id）
python -B bench/audience/run.py --json out.json          # 同时写出结果
python -B bench/audience/run.py --list                   # 只列出 id
python -B -m unittest tests.test_bench_audience          # 测试：每个场景通过、文件格式合格、关掉保护后场景会失败
```

每个没通过的检查打印一行（场景、第几步、它的 `why`、看到的情况），最后是一张表（场景、检查数、失败数）；有检查没通过，退出码是 1。只用标准库，没有时钟和随机数参与（存储给记忆起的编号用计数器），同一个场景每次的结果一样。

## 场景文件

```json
{
  "id": "owner-private-vs-group",
  "title": "中文标题",
  "checks": ["store_rule", "stamp_at_write"],
  "setup": {
    "people": {"owner": {"owner": true, "accounts": ["dsh:local", "aiocqhttp:10001"]}},
    "stores": {"private": {"audience": "owner"}},
    "tenants": {"group": {"audience": "group", "ids": ["50001", "60002"]}},
    "clients": {"owner_bot": {"recall": ["private"], "write": "private"}}
  },
  "steps": [ ... ]
}
```

- `id` 是文件名（不带 `.json`）；`title` 是中文标题；`checks` 是这个场景要证明的保护（取自下面的名单）。
- `setup` 是装置：`people` 是身份表（不写就没有 `[identity]`）；`stores` 每个存储一个 SQLite 文件，可以写 `audience`，也可以写 `file`（用另一个存储的文件，两个名字一个文件）；`tenants` 是目录型的租户来源（每个群一个文件，`ids` 是建好就有的群，`create` 默认 `false`）；`clients` 是客户端的策略键（`recall`、`write`、`tenants`、`audience`、`platforms`、`only_for`、`fields`、`explain` 等，照配置文件写；令牌由 `run.py` 设，请求频率和每日写入上限也由它放宽）。
- `steps` 按顺序执行。会下结论的步骤叫检查，必须有中文的 `why`，没通过时打印它。

### 步骤

| `do` | 做什么 |
|---|---|
| `remember` | 一个客户端存一句话：`client`、`text`，可选 `as`（说话人的账号）、`tenant_id`、`keys`（消息里的 `channel`、`kind`、`audience` 等）、`role`。默认应该成功（200）；写了 `status` 就是检查，要求拿到这个状态码 |
| `recall` | 一个客户端召回：`client`、`query`，可选 `as`、`tenant_id`、`limit`、`mode`（`recall`，或 `explain` 带解释的召回）。先走和 HTTP 服务一样的权限一步，再召回。下面的期望至少写一个 |
| `put_raw` | 一条记录直接放进存储文件：`store`（租户来源时加 `tenant_id`）、`text`、`how`。`direct` 是直接 `put()`，没有受众；`legacy` 是旧版本写的 SQL（四个列加词组索引），没有受众 |
| `copy_store` | 把一个存储的文件复制成另一个名字的文件（`from`、`to`），之后要用 `reconfigure` 把它配出来 |
| `reconfigure` | 装置变了：`patch` 是对 `setup` 的合并补丁（对象合并，`null` 删除，其余替换），网关在同一批文件上重建。写 `refuse`（错误信息里应有的一段文字，通常是设置的名字）时，要求新的装置起不来，补丁也不留下 |
| `restamp` | 用 `restamp` 命令给一个存储重新打标记（`store`、`mode` 是 `missing` 或 `all`），命令运行时网关是停的；写 `changed` 就核对改了几条 |
| `forget_restore` | 用遗忘服务把一个存储里的记忆全部归档并删掉，再把 `texts` 里的逐条恢复；核对删掉了、也恢复了 |
| `files` | 存储文件的字节里，`lack` 里的字符串一个都不许出现，`have` 里的每个都要出现 |
| `codes` | 读 `where` 里几个存储记录的说话人，每个存储里正好有一个替身码（`anon:` 开头），`expect` 要求几个存储里的码 `all_different` 或 `all_same` |

`recall` 的期望：

- `status`：应有的状态码，默认 200。
- `absent`：这些字符串一个都不许出现在整个回答里（回答的 JSON 全文，解释召回包括候选）。
- `present`：每个字符串要出现在某条结果的文字里。
- `never`：和 `absent` 一样查整个回答，只是用来写不该出现的账号、人名这类东西。
- `fields`：键是结果文字里的一段，值是这条结果的 `fields` 应当正好等于的对象。
- `candidate_fields`：同上，看解释召回的候选，值里的每一项都要对上。

规则：每个写了 `absent` 的字符串，场景里别处必须有一个 `present` 写着同一个字符串（让一个读得到的客户端找到它），这样一个什么都不返回的存储过不了关；查询词写成存的那句话本身或很接近，记忆在没人拦的时候会被找到。每个检查都有 `why`。文件的格式由 `run.py` 里的 `validate` 检查（`tests/test_bench_audience.py` 也跑它）：不认识的键、缺 `why`、`absent` 没有对应的 `present` 都算格式不合格。

## 场景

| id | 一句话 | 证明的保护 |
|---|---|---|
| `owner-private-vs-group` | 主人的私聊记忆，在群 50001 和群 60002 里都召回不到；把它列进群客户端的 `recall`，启动被拒绝 | `store_rule`、`stamp_at_write` |
| `owner-private-vs-public` | 主人的私聊记忆，公开客户端召回不到；把它列进公开客户端的 `recall`，启动被拒绝 | `store_rule`、`stamp_at_write` |
| `group-vs-other-group` | 一个群的记忆，另一个群读不到；替所有群服务的客户端读属于一个群的共享存储，启动被拒绝 | `store_rule`、`stamp_at_write` |
| `foreign-platform-account` | 客户端报了不属于自己平台的账号，写入和召回都是 403，什么也没存 | `platform_rule` |
| `only-the-owner` | 限定只替主人说话的客户端：另一个人、陌生人、不报说话人，写入和召回都是 403，什么也没存 | `only_for` |
| `one-person-two-hosts` | 同一个人在 DSH 和 QQ 上：一边存、另一边召回得到，入库的说话人只有 `owner`，两个原始账号不在文件里 | `raw_account_gone` |
| `copied-store-file` | 主人的存储文件复制一份配成公开存储：公开客户端什么也拿不到，主人仍从原来的存储读 | `record_rule`、`stamp_at_write` |
| `two-names-one-file` | 同一个文件配两个存储名，一个主人级、一个公开：公开的名字读不到主人的记忆 | `record_rule`、`stamp_at_write` |
| `record-without-audience` | 直接放进公开存储、没有受众的记录：公开客户端看不到，主人看得到，`restamp --missing` 之后公开客户端才看得到 | `unstamped_is_owner`、`record_rule`、`stamp_at_write` |
| `older-version-row` | 旧版本写法的一行写进已打过标记的群存储：重新打标记之前群里看不到 | `unstamped_is_owner`、`record_rule` |
| `forgotten-and-restored` | 群级记忆被遗忘又恢复：仍给本群看；主人级的恢复后仍不给群看 | `record_rule`、`stamp_at_write` |
| `explain-for-the-owner` | 带解释的召回：群和公开客户端被拒绝，给它们开 `explain` 启动被拒绝；主人的解释里看得到记录的字段 | `explain_owner_only` |
| `fields-in-answers` | 群客户端的答案里只带 `kind` 和 `channel`，没有人名、账号；配 `speaker` 或 `about` 的配置起不来 | `names_owner_only` |
| `stand-in-codes` | 没登记的群友在群 A、群 B 和公开存储里各有一个替身码，三个不同；原始账号不进任何存储文件，答案里也没有账号 | `code_per_store`、`raw_account_gone` |
| `narrower-audience-by-message` | 消息要求主人级时，这条记录在群存储里只给主人；要求比存储宽的被拒绝（400） | `record_rule`、`stamp_at_write` |

另有一个场景写成了普通测试，在 `tests/test_bench_audience.py` 里（类 `LinkedTests`）：`linked` 步骤接上来的记忆也要过受众检查。它需要测试用的假 embeddings 服务（`tests.test_backend_embedding.FakeEmbeddingServer`），放进场景文件会让 `run.py` 依赖测试包，所以没有放进来。公开存储里一条主人级的记忆只能作为邻居被接上：主人级的客户端拿到它，公开客户端拿不到。

## 保护的名单和“反向检查”

`checks` 里写的名字是这十项保护：

| 名字 | 保护 |
|---|---|
| `store_rule` | 启动时的规则：客户端只读受众不比自己窄的存储 |
| `record_rule` | 召回步骤 `record_audience`：记忆自己的受众比客户端窄就拿掉 |
| `unstamped_is_owner` | 没有受众的记录按主人级算 |
| `stamp_at_write` | 经网关写入的记录带上存储的受众（或消息要求的更窄的受众） |
| `platform_rule` | 客户端只能报自己平台的账号 |
| `only_for` | 客户端只替限定的人说话 |
| `code_per_store` | 替身码在不同存储里不一样 |
| `raw_account_gone` | 原始账号不进存储 |
| `explain_owner_only` | 带解释的召回只给主人级的客户端 |
| `names_owner_only` | `speaker` 和 `about` 只返回给主人级的客户端 |

光是“场景都通过”说明不了场景有用：一套什么都查不出来的场景也全部通过。所以 `tests/test_bench_audience.py` 里有反向检查：对每一项保护，用 `unittest.mock.patch` 在实现它的最小一段代码上把它关掉，要求每一个在 `checks` 里写了它的场景都失败，并且有一个有名字的检查报告失败。有三处关不到“恰好那一行”：`unstamped_is_owner`（判断写在 `record_audience` 的 `admit` 里，测试给没有受众的候选先补上 `public` 再交给原来的代码）、`only_for`（规则在网关里写了两处，测试让每个客户端策略都不带 `only_for`）、`platform_rule`（召回一侧的检查写在 `Gateway._asker` 里，没有单独的函数，测试关掉写入一侧的 `check_platform`，召回一侧的检查仍在，所以 `foreign-platform-account` 里关掉后失败的是写入的检查）。

## 没有覆盖的

- 不测模型：没有语义检索的好坏，所有场景用关键词存储（`sqlite`），查询词就是存的那句话。`linked` 是唯一用向量存储的，在测试里，用假的 embeddings 服务。
- 不测 HTTP 层的细节：场景直接调网关的 `admit`、`remember`、`recall`、`explain`（HTTP 服务先调的就是 `admit`），不经过套接字、令牌和请求体大小的检查，这些有各自的测试。
- 不测能直接改存储文件的人：能写文件的人能改受众，这和能读文件的人本来就能读全部记忆是同一个信任边界（见 [../../docs/security.md](../../docs/security.md)）。
- 不测宿主报的账号是不是真的：网关核实不了。
- 不测部署的其他部分（备份、日志、限速）。
