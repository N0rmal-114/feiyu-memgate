# 进程内（嵌入）部署

默认的部署是网关单独一个进程，聊天程序用 `MemoryClient` 经 HTTP 访问它。对一台小机器上只有一个进程的机器人，这意味着多管一个进程、一个端口、一个令牌。进程内模式把网关放进聊天程序自己的进程里，用 `LocalClient` 调用，**调用代码和 `MemoryClient` 一样**，以后想拆出去只改创建客户端的那一行。

    from feiyu_memgate.client import LocalClient, MemoryClient

    memory = LocalClient.from_config("gateway.toml", client="owner")        # 进程内
    memory = MemoryClient("http://127.0.0.1:8080", token)                   # HTTP

    memory.recall("我爱喝什么茶")            # 之后的调用完全相同
    memory.remember_status("我爱喝乌龙茶")

可运行的例子在 `examples/embedded/`（配置、`bot.py`）。

## 怎么选

| | 进程内库 | 本机旁路（loopback sidecar） | 独立服务 |
|---|---|---|---|
| 进程数 | 1（网关在聊天进程里） | 2（网关另起，只听 127.0.0.1） | 2 个或更多，可在另一台机器 |
| 端口 | 无 | 1 个本机端口 | 1 个对外或内网端口 |
| 存储密钥在哪 | 聊天进程里 | 网关进程里，聊天进程拿不到 | 网关所在的机器上 |
| 聊天进程被攻破后谁能读存储 | 攻击者就在持钥进程里：能直接打开数据库文件、读密钥，能冒用任何策略 | 只能用手里那一个令牌允许的操作；读不到别的存储，拿不到密钥 | 同左；网络上还要防别的主机来连 |
| 多语言客户端 | 只有 Python | 任何会发 HTTP 的语言 | 任何会发 HTTP 的语言 |
| 运维成本 | 最低：没有令牌、端口、进程要管 | 中：多一个进程要拉起和监控，令牌要保管 | 最高：网络、TLS（网关本身只说 HTTP，要自己在前面加）、多机部署 |

经验法则：

- 一个 Python 聊天程序、一个人用、机器小，而且你信得过这个进程里跑的所有代码（包括插件）：进程内。
- 聊天程序装了第三方插件、连着公网、跑着会执行模型输出的工具，或者不止一个程序要共用记忆：用 HTTP（旁路或独立服务）。安全模型见 `docs/security.md`：它的核心是“聊天程序拿不到存储密钥”，进程内模式正好放弃这一条。
- 聊天程序不是 Python：只能用 HTTP。

## 进程内模式的代价

1. **信任边界没有了。** 聊天进程持有存储的密钥，也能直接打开 SQLite 文件（或读到 OpenViking 的密钥）。`LocalClient` 按名字取某个客户端策略，不验证任何东西：能调用这个对象的代码就拥有那个策略的全部权限，也能自己再构造一个更宽的 `LocalClient`。所以每个客户端的策略（读哪些存储、能不能写、每天写入上限、限流、公开脱敏）在这里**防的是你自己代码路径上的失误**，比如把群聊的调用接到了私聊的存储上；**防不了被攻破的进程**。要防后者，用 HTTP。
2. **限额只在本进程内有效。** 限流、每日写入上限和租户预算都记在进程内存里。重启后每日上限从零计数；两个进程各自打开同一个数据库，各有一份限额，不会合并。
3. **只能 Python。** 并且网关和聊天程序共用一个解释器、一套依赖，网关的线程池（默认的工作线程）和聊天程序抢同一台机器的 CPU。
4. **崩溃连坐。** 聊天进程退出时网关随之退出；没有 `close()` 的异常退出可能让后台的写入跟踪、清理中断在半途（存储本身的事务不受影响）。
5. **访问日志默认写到标准错误**，和 `serve` 一样，每次调用一行，只有路由、状态、客户端名、耗时和备注，没有内容；方法一栏是 `LOCAL` 而不是 `POST`。想接到自己的日志里，给 `from_config` 传 `log=函数`。

## 和 `MemoryClient` 的差别

`LocalClient` 是 `MemoryClient` 的子类，所有公开方法（`configured`、`tenant_configured`、`recall_detail`、`recall`、`remember_messages`、`remember_status`、`remember`、`report_used`）直接继承：参数检查、超限不发送、网关答复到返回值（`"saved"` / `"skipped"` / `"failed"` / `"limited"` / `"unsure"` / `None`）的映射、永不抛异常，都是同一份代码。只有传输换了：不开套接字，按 HTTP 服务器的顺序直接调用网关的 `admit()`，然后是 `recall()` / `remember()` / `used()`。所以客户端策略、限流、每日上限、召回阶段、写入守卫、脱敏和遗忘账本的行为和 `serve` 下相同，契约测试（`tests/test_client_local.py`）对两种传输跑同一组断言。

确实不同的地方：

| 项目 | HTTP | 进程内 |
|---|---|---|
| 身份 | 令牌 | 构造时给策略名；不存在的名字在构造时抛 `ConfigError` |
| 租户 | `tenant_token` | `tenant_client`（另一个策略名）；带 `tenant_id` 的调用用它，不带的用 `client` |
| `timeout` / `deadline` | 到点关闭套接字 | 网关调用跑在一个临时守护线程里，到点放弃等待：`recall` 得 `None`，`remember` 得 `"unsure"`（它可能还会完成），`report_used` 得 `False`。网关自己的截止时间（召回、写入）照常生效 |
| 请求体检查（大小、JSON 形状） | 服务器先查 | 没有请求体；网关对同一个字典做的形状和长度检查仍在 |
| 创建时的错误 | 构造不会抛 | 策略名不对、配置有错都抛 `ConfigError`（构造之后的调用才是“永不抛异常”） |
| `from_env` | 读 `_URL`、`_TOKEN`、`_TENANT_TOKEN` | 读 `_CONFIG`（配置文件）、`_CLIENT`、`_TENANT_CLIENT`；`_CONFIG` 没设置时得到一个关闭的客户端（`configured` 为假，调用全部安静地失败），设了但不对就抛错 |

`LocalClient` 另有 `health(deep=False)`（等于 `GET /healthz`）、`for_client(...)`、`close()` 和上下文管理器。

`token_env` 与令牌：

- 客户端表里的 `token_env` 可以不写：不写的客户端只是一条策略，经 HTTP 无法认证，进程内照常用，所以进程内用法的配置可以完全不写令牌。写了也行，变量没设置时网关只会打一行“没有令牌”的提示，`from_config` 把这一行过滤掉了。
- 例外是 `tenant_binding = "token"` 的租户客户端：它必须写 `token_env`（那是铸造密钥的变量名，加载器的规则），但进程内用时这个变量可以不设置，“没有铸造密钥”的提示同样被过滤。进程内没有令牌，带 `tenant_id` 的调用就是为这个租户做的，限流照样先按租户、再按客户端合计。这样的客户端只能作为 `tenant_client`；把它作为 `client`（不带 `tenant_id` 的调用）在构造时就抛 `ConfigError`。
- 令牌设置了也无害（进程内不使用）。但两个客户端配了同一个令牌，加载器仍会拒绝。
- 例子 `examples/embedded/gateway.toml` 保留了 `token_env`，这样它也能直接给 `feiyu-memgate check` 和 `serve` 用。

## 生命周期

    with LocalClient.from_config("gateway.toml", client="owner") as memory:
        ...                       # 退出时：停掉清理线程，关闭网关和存储

- `from_config` 构建网关并调用 `Gateway.start()`；这个客户端拥有网关，`close()` 会关闭它。
- 同一个进程里要用两个策略（主人和群）：`group = memory.for_client("group")`，或在一次调用里写 `memory.for_client("owner", "group")`（第二个参数是租户策略）。它们共用一个网关，限流和每日上限按策略名分别计数，和 HTTP 下两个令牌一样。兄弟客户端不拥有网关，`close()` 它不会影响别的。
- 已经自己构建了网关：`LocalClient(gateway, client="owner")`。默认会调用 `gateway.start()`（可重复调用，不会重复启动）；`close()` 不会关你的网关，要自己关。要让它代你关，传 `close_gateway=True`。
- 线程安全：网关本来就是为多线程的 HTTP 服务器写的，`LocalClient` 只多了一个“已关闭”标志，加了锁。每次调用在自己的临时线程里跑，所以可以放心从 `asyncio.to_thread` 或线程池调用。

## 遗忘清理

配了 `[forgetting]` 时，网关的清理线程在 `start()` 之后每 `sweep_every_hours` 小时清理一次，`close()` 时停止，行为和 `serve` 下一样。要知道：

- **第一次清理在一个间隔之后，不是启动时。** 机器人如果比 `sweep_every_hours`（默认 24 小时）重启得更勤，线程永远等不到点，什么也不会被清理。这种情况下自己定时调用：`memory.gateway.sweep()`（和后台线程做的是同一件事，不取消限速），或者用命令行 `feiyu-memgate forget sweep --config gateway.toml`（SQLite 文件可以被另一个进程打开；清理和聊天同时写同一个账本时的互相等待我们没有专门测过，先 `--dry-run` 看一眼）。
- `memory.report_used(...)` 在配了遗忘时会把用到的记忆记进账本，和 HTTP 下相同；没配遗忘时它什么也不记，照样返回 `True`。
- `pin_writes` 策略写入的记忆同样会被钉住。

## 以后改成 HTTP

1. 在配置里设好令牌变量，例如 `GATEWAY_TOKEN_OWNER`（至少 32 个字符的随机串），用 `feiyu-memgate serve --config gateway.toml` 另起网关。
2. 把创建客户端的那一行换成 `MemoryClient("http://127.0.0.1:8080", token)`（或 `MemoryClient.from_env()`）。其余调用代码不用改。
3. 把进程内用的数据库文件交给网关进程去打开，并且别让聊天进程再直接打开它们（否则两边各有一份限额，也失去了拆分的意义）。
4. 注意两点行为差别：`timeout` 现在是真正的网络超时；构造客户端不再检查策略名，名字写错会在第一次调用时才以“失败”的形式表现出来。

例子里的 `bot.py` 就是这样写的：设置了环境变量 `FEIYU_MEMGATE_URL` 就走 HTTP，没设置就走进程内，其余代码不变。
