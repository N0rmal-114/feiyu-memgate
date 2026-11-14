# feiyu_memgate_recall：AstrBot 示例插件

一个很小的 AstrBot 插件，演示怎样把 feiyu-memgate 的客户端接进机器人：

1. 每次调用大模型之前，用当前消息（太短时带上上一句用户消息）去网关召回记忆，把整理好的“资料块”作为**临时消息片段**加到当前这条消息后面。临时片段会发给模型，但不会存进对话历史，系统提示词也保持不变，可以被缓存。
2. 给模型一个 `remember` 工具，用来保存一条记忆，并把真实结果告诉模型：已交给记忆服务（整理要一会儿）、看过了但没有要记的（被过滤掉了）、不确定、今天的次数用完了、没记住。
3. 可选：用了遗忘曲线（网关配置了 `[forgetting]`）时，客户端可以在回复之后调用 `MemoryClient.report_used(items, tenant_id=None)`，把这次回复真正依赖的那几条召回结果（带 `source` 和 `id` 的字典，最多 20 条）告诉网关，网关只给这几条记一次“用到”，没被用到的就会照常变旧。它不会抛异常，成功回 `True`。这个示例插件没有调用它：网关默认只给相似度最高的那一条（和它几乎一样相关的也算）记“用到”（`freshness` 的 `credit = "top"`）；想只按回报来记，把 `credit` 设成 `"none"` 并让插件调用 `report_used`。
4. 谁能用记忆：
   - 私聊，且发送者 ID 在 `FEIYU_MEMGATE_OWNER_IDS` 里：用主人的令牌，读写主人的记忆。
   - 群聊：用租户令牌，群号作为 `tenant_id`，每个群一份自己的记忆；一次回复里最多记一条。
   - 其他人：不召回，也不能记。

> 示例，按 AstrBot v4.29 的插件接口编写。AstrBot 没有装在写这个示例的机器上：插件的逻辑用模拟的 AstrBot 接口测过（`tests/test_integration_astrbot.py`），没有在真实的 AstrBot 里跑过。接口名字以你用的 AstrBot 版本为准。

## 记忆块放在哪里

放的位置决定提示词缓存还能不能命中：模型服务商对重复出现的提示词前缀按低价计费，靠前的内容一变，它后面的全部内容都要重新计费。

| 情形 | 放在哪 | 对缓存的影响 | 代价 |
|---|---|---|---|
| AstrBot 支持临时消息片段（正常情形） | 当前消息的一个临时片段 | 没有：系统提示词和历史都不变 | 无 |
| 不支持临时片段，`FEIYU_MEMGATE_FALLBACK=message`（默认） | 当前消息的文字末尾 | 没有：系统提示词和之前的历史不变 | 记忆块会随这条消息留在历史里，上下文变长 |
| 不支持临时片段，`FEIYU_MEMGATE_FALLBACK=system` | 系统提示词末尾 | 每次回复都让整段缓存失效 | 只在前两种都做不到时用 |
| `FEIYU_MEMGATE_FALLBACK=off`，或请求对象上没有可用的字段 | 不放 | 没有 | 这一轮模型看不到召回的记忆 |

走后三种情形时插件各记一条警告（每种只记一次，不含记忆内容）。记忆块留在历史里的情形下，插件在拼下一次的查询词时会先把上一条用户消息里的旧记忆块去掉；如果这次召回的内容和上一条消息里的完全相同，就不再重复放。

## 安装

1. 在 AstrBot 所用的 Python 环境里安装客户端（只用标准库，没有别的依赖；这个包没有发布到 PyPI，从仓库安装）：

   ```
   pip install git+https://github.com/N0rmal-114/feiyu-memgate.git
   ```

2. 把本文件夹整个复制到 AstrBot 的 `data/plugins/feiyu_memgate_recall/`，重启 AstrBot。

3. 设置环境变量（插件只读环境变量，不读别的配置）：

   | 变量 | 含义 |
   | --- | --- |
   | `FEIYU_MEMGATE_URL` | 网关地址，例如 `http://127.0.0.1:8080` |
   | `FEIYU_MEMGATE_TOKEN` | 主人客户端的令牌 |
   | `FEIYU_MEMGATE_TENANT_TOKEN` | 租户（群）客户端的令牌；不写就不给群开记忆 |
   | `FEIYU_MEMGATE_OWNER_IDS` | 主人的发送者 ID，逗号分隔，例如 `10001,10002` |
   | `FEIYU_MEMGATE_FALLBACK` | 可选。AstrBot 不支持临时消息片段时记忆块放哪：`message`（默认）、`system`、`off`，见上面“记忆块放在哪里” |

   缺少 URL 或令牌时插件只在启动时记一条警告，不影响机器人回复。

## 网关配置示例

下面是和这个插件配套的最小网关配置：一个主人私聊的存储，加上每个群一份自己的存储。令牌放在环境变量里（长度至少 32 个字符），不写进文件。

```toml
[server]
host = "127.0.0.1"
port = 8080

[stores.private]                     # 源名 private：插件里标成“你们私聊里的”
type = "sqlite"
path = "data/private.db"

[tenants.group]                      # 每个群一个 <群号>.db，源名 group：标成“这个群的”
type = "directory"
path = "data/groups"
id_pattern = "[0-9]{5,12}"
create = false                       # 只有登记过的群才有记忆

[clients.owner]
token_env = "FEIYU_OWNER_TOKEN"      # 对应插件的 FEIYU_MEMGATE_TOKEN
recall = ["private"]
write = "private"
daily_writes = 50

[clients.group]
token_env = "FEIYU_GROUP_TOKEN"      # 对应插件的 FEIYU_MEMGATE_TENANT_TOKEN
tenants = "group"
recall = ["@tenant"]                 # 只读本群自己的存储，读不到主人的私聊记忆
write = "@tenant"
public = true
daily_writes = 20                    # 每个群每天
daily_writes_total = 500             # 所有群加起来每天
```

其他存储和召回阶段的写法见仓库里的 `docs/configuration.md`。

## 想改的地方

- 标签：`main.py` 里的 `FORMATTER` 把网关的源名（存储名）映射成给模型看的标签，改成你自己的存储名。想用英文提示，写 `BlockFormatter(lang="en", labels=...)`。
- 过滤哪些消息不召回：用 `QueryShaper(...)`（填充字、过短的长度、命令前缀等），再在 `on_llm_request` 里用它的 `.build(...)` 代替 `build_query`。
- 超时：`RECALL_TIMEOUT`、`RECALL_BUDGET`、`REMEMBER_BUDGET`。召回超时或失败时，这一轮就当没有记忆，回复照常。

## 安全上的几点（客户端已经做到）

- 只用标准库的 `http.client`：不读代理环境变量，不跟随重定向。
- 带绝对截止时间，连接后和读取过程中都会检查；响应有大小上限。
- 日志里只写失败的种类，不写查询、记忆内容和令牌。
- 租户调用只用租户令牌并总是带 `tenant_id`；非租户调用只用主令牌并从不带 `tenant_id`，两个令牌之间没有回退。
- 记忆块里出现的标签会被转义，记忆文字不能提前结束数据块。
- `tenant_id` 和发送者 ID 只来自事件，不来自模型。
