# 更新记录

## 未发布

这一版改了 HTTP 接口、配置和 Python 接口，有不兼容的地方，升级前请看“要改的地方”。

### 要改的地方

- **有公开客户端的配置，要给它读的存储声明受众，否则启动报错。** 存储和租户来源多了 `audience`（`"owner"`、`"group"`、`"public"`），客户端也可以写；启动时核对：客户端的 `recall` 里不能有受众比它自己窄的存储，带租户的群级客户端也不能读声明为 `group` 的共享存储。没声明的存储是 `owner`，租户来源是 `group`；不写 `audience` 的客户端，不公开的是 `owner`，`public = true` 的带 `tenants` 是 `group`、不带是 `public`。所以升级后，`public = true` 的客户端读着的共享存储要补一行：在这个存储的表里写 `audience = "public"`（确认里面的内容可以给任何人看之后），例如 `[stores.public_list]` 里加 `audience = "public"`；用代码建 `Gateway` 的，传 `audiences={"public_list": "public"}`。只读 `"@tenant"` 的公开客户端不用改。写入不受影响，`feiyu-memgate check` 会对“受众不是主人的客户端写入主人的存储”提醒一行，并显示每个存储、租户来源和客户端的受众。说明见 docs/configuration.md 的“受众”一节。
- **往打过标记的存储里直接写记忆，要带受众。** 每条记忆现在有自己的受众，召回时检查：直接 `put()` 进存储、不带 `fields={"audience": ...}` 的记忆（旧版本往文件里写、直接的 SQL 也一样）没有受众，按 `"owner"` 处理，只给主人级的客户端看。存储是 `group` 或 `public` 级、并且要给别人看的，`put()` 时写 `fields={"audience": "public"}`，或者事后运行 `feiyu-memgate restamp --config FILE --store NAME --missing`。网关建好时已经给存储里原有的记忆盖了存储声明的受众，通过网关写入的记忆都有受众，不用改。
- **写了自己的召回步骤列表、又有受众不是主人的客户端的配置，要把 `"record_audience"` 加进 `[recall]` 的 `stages`，否则启动报错。** 默认的一套已经有它（在 `linked` 后面、`fill_text` 前面）；`examples/multi-tenant/gateway.toml` 已经加上。
- 分数线的默认档位从 `precise` 改成 `broad`：`min_score` 和 `relative_score` 不写 `profile` 时用每个存储的 `broad_*` 分数线。用向量存储时每个问题平均交约 2.5 条记忆（以前约 1 条），漏答更少，交出去的杂项更多，对没有答案的问题更少保持安静。原因：项目用在聊天陪伴上，一个话题通常牵到好几条记忆，每轮 1 条太少。两档的数值没有改。要回到以前的行为，在配置里写 `[recall] profile = "precise"`（或只在某一步上写 `profile = "precise"`）。`default_min_score` 等属性名不变，它们是 `precise` 档的分数线（`default_` 是历史命名）；没有声明 `broad_*` 的存储两档都用 `default_*`。每题交得更多，记忆块也更长：记忆块会留在对话历史里的聊天程序，上下文会涨得更快。
- 默认召回步骤多了一步 `newer_first`（在 `relative_score` 后面）：通过分数线的候选里，分数不低于最高分 85% 的几条按日期新的在前。它不改变交付哪些记忆，只改顺序；记忆没有日期时不起作用。不想要它，在 `stages` 里自己列一套不含它的。
- 默认召回步骤又多了一步 `linked`（在 `newer_first` 后面、`fill_text` 前面）：用 `embedding` 或带 `embedding` 子后端的 `hybrid` 存储时，一次召回可能多返回最多两条和最前面两条记忆连着的记忆（总数仍不超过 `limit`；候选数已经够 `limit` 条，或者一条候选都没有时什么都不加）。接上的记忆和别的记忆一样过 `fill_text`、`redact`、`dedupe`、`budget`，HTTP 回答的字段不变。只用关键词存储（`sqlite`、`inmemory`）或 `openviking` 时没有变化。每跟着一条候选，`embedding` 要把已存的向量扫一遍，所以一次召回最多多两遍扫描。要回到以前的行为：在 `[recall]` 里写一个不含 `linked` 的 `stages` 列表，或者把它的 `max_added` 设成 0（`{ type = "linked", max_added = 0 }`）。写了自己的 `stages` 的配置不变。
- `embedding` 后端按模型名选用预设（`floor`、`ceil`、默认分数线、查询前缀）。`bge-m3` 的行为不变；配置里用的是 bge-small-zh-v1.5、bge-base-zh-v1.5、embeddinggemma、qwen3-embedding 的，默认分数线会变成各自的预设；其他模型沿用 bge-m3 的数值并在启动时给一行警告。手写过 `floor`、`ceil` 或分数线的不受影响。向量库现在还记着建库时的文档前缀，不一致会拒绝打开。
- 配置里的普通令牌不能以 `fmg1.` 开头（这个前缀留给租户绑定令牌），否则启动报错。
- 客户端配置里的 `token_env` 变成可选：不写表示这个客户端没有令牌，不能通过 HTTP 认证，只能在进程内使用。
- 多租户客户端的 `recall` 里要写 `"@tenant"` 才会搜租户自己的存储，它在列表里的位置就是搜索和排列的顺序（以前总是自动加在最后）。没有 `tenants` 的客户端写 `"@tenant"` 启动报错。
- 错误回答多了稳定的 `code`：`{"error": "...", "code": "..."}`。程序请按 `code` 判断，`error` 的措辞可能改。全部错误码见 api.md。
- 格式不对的 `tenant_id` 现在是 400 `bad_tenant`（以前是 403）；没登记的租户仍是 403，`code` 为 `unknown_tenant`。
- 召回时网关内部的意外错误是 500 `internal`（以前是 503）。
- 召回步骤的默认一套里去掉了 `drop_marker`，要用请自己加在 `fill_text` 后面。
- `min_score` 不写 `value` 时用存储自己声明的分数线（`sqlite` 等的 `precise` 档是 0.3，默认档位的值见最上面一条），都没有才是 0.5；例子里手写的 0.3 去掉了。
- `openviking` 存储的分数现在是换算后的 0..1（原来是 OpenViking 的原始分数）；手写过它的 `min_score` 的，原始分数 x 对应现在的 `(x - 0.5) / 0.5`，或在存储里写 `floor = 0`、`ceil = 1` 保持原样。
- `cold_deadline` 默认是 `deadline` 和 8 秒中大的那个；`tenant_pool_total` 默认是 `pool_size - 2`（以前是 3）。
- `[server]`、`[recall]`、`[write]`、`[redaction]`、`[forgetting]` 里不认识的设置名、类型不对、超出范围，启动时报错。
- 遗忘曲线（`freshness`）第二次修订，行为变了（原因和模拟见 docs/design/2026-11-14-forgetting-curve.md 的第二个“修订”）：
  - 新鲜度对所有状态是同一个有界公式 `floor + (1 - floor) × 保持率`，`floor` 默认从 0.3 改成 **0.7**，钉住的恒为 1。“降权”不再是排序状态（以前降权的记忆没有保底、直接乘保持率），只是报告里的状态。手写过 `floor` 的请重新看一下：现在旧记忆最多损失 `1 - floor` 的分数。
  - `freshness` 多了 `credit`（默认 `"top"`：召回时只有最相关的一条，和相似度不低于它 `credit_ratio`（默认 0.9）倍的几条记一次“用到”；`"all"` 是原来的做法，`"none"` 召回从不记）。**默认行为变了**：以前返回的每一条都记。
  - 清理的“刹车”改成限速：每次清理、每个存储最多忘 `max(max_forget_min, max_forget_fraction × 条数)` 条，先忘保持率最低的，其余留到后面的清理（报告里的 `held`）。`max_forget_fraction` 默认从 0.2 改成 **0.02**，新增 `max_forget_min`（默认 5）。以前超过比例整个存储都不删，现在每次都会删一小批。网关后台清理的日志行多了 `held=`。`forget sweep --force` 仍然取消限制。
- 两个客户端用同一个令牌，启动报错。
- 写了限制却没有拼图执行时启动报错：`daily_writes` 要有 `daily_cap` 守卫，`public = true` 要有 `redact` 步骤和守卫。
- 同一个插槽里，同一个名字注册给两个不同的东西会报错。
- 自带客户端不再截断：超过 4000 字的消息、超过 16000 字的一次写入、超过 500 字的查询词都不发送（写入报 `failed`，召回不查）。
- 自己做拼图请从 `feiyu_memgate.pieces` 导入；`feiyu_memgate.core` 是内部实现。
- 自己写召回步骤的例子搬到了 `examples/custom-stage/`，配置里写 `type = "recency_stage:Recency"`。
- 检索质量（都只在开发集 `examples/eval` 上选，没有看测试集 `bench/data`，细节和数字见 backends.md）：
  - `sqlite`、`inmemory` 的分数从“问题的二元组有多少在记忆里”改成按稀有程度加权的重合度（二元组加单个汉字，权重是 BM25 逆文档频率），
    分数线默认值仍是 0.30（比例 0.75，见下面“默认阈值”）。同一条记忆的分数会变，手写过这两个后端 `min_score` 的请重新量。
    旧版本建的 sqlite 库可写方式打开时会自动重建一次索引（只读方式打开则继续只按二元组搜）。
  - `embedding` 的 `floor` 默认从 0.30 改成 0.40，分数线默认值从 0.30 改成 0.50（余弦 0.45 → 0.60）；`hybrid` 的分数线默认值从 0.30 改成 0.70（融合方式没变）。
    手写过 `floor`/`min_score` 的不受影响；用了旧默认值又想保持原样的，写 `floor = 0.30`、`min_score` 的 `value = 0.30`。
  - 默认召回步骤多了一步 `relative_score`（紧跟 `min_score`）：只留分数不低于本次最高分一定比例的候选。默认比例由后端声明。不想要它，在 `stages` 里自己列一套不含它的。
  - **默认阈值在大开发集 `bench/dev`（160 条记忆、110 个问题）上重新选了一次**（原来的在 30 题的 `examples/eval` 上选，对测试集偏严）。规则事先写定、不看测试集：`balanced = (可答题数 x 交付F1 + 安静题数 x 安静率) / 总题数`，网格 `min_score` 0.05..0.80 x 比例 0.50..0.95 或不用，见 backends.md。新值（`min_score` / 比例）：`sqlite`、`inmemory` 0.30 / 0.75，`embedding` 0.50 / 0.85，`hybrid` 0.70 / 0.85，`openviking` 0.55 / 0.95（之前在 `examples/eval` 上选的值是 0.20 / 0.6、0.40 / 0.8、0.55 / 0.8、0.5 / 通用的 0.6，从未发布）。手写过这些值的不受影响。
  - `openviking` 后端现在声明 `default_relative_ratio`（0.95）。

### 新增

- 记忆的字段和每条记忆自己的受众：存储记录七个可选字段（`speaker`、`about`、`channel`、`audience`、`origin`、`event_date`、`kind`；`speaker` 和 `about` 还没有东西往里填），`Hit` 和 `Candidate` 多了最后一个字段 `fields`，后端多了 `stores_fields`、`put(..., fields=)`、消息里的键 `"fields"`、`records()`、`record(ref)`（`sqlite`、`embedding`、`hybrid`、`inmemory` 记录字段，旧文件以可写方式打开时自动加列，只读打开不改；旧版本仍能读写升级后的文件，但它重写一条记忆时会清掉这一行的字段）。`POST /v1/remember` 的每条消息可以带 `channel`、`origin`、`event_date`、`kind`、`audience`（值不合规 400 `bad_messages`，`null` 等于没写）：目标存储记录字段时，网关在写入守卫之前把它们合成消息的键 `"fields"`，并给每条记忆盖上受众章（目标存储的受众，消息可以要求更窄的、不能更宽；`channel` 没写时用 `context` 里的 `channel`），不记录字段的存储收到的消息和以前一样。`ClientPolicy` 多了 `origins`（默认 `("said", "tool")`，写了别的来历 403 `forbidden`，来历只是记录、不凭它给权限）和最后一个字段 `fields`（配置里的 `fields = [...]`：召回的每一项里带上这些字段，不写时回答不变；受众不是 `owner` 的客户端不能列 `speaker` 和 `about`）。`POST /v1/explain` 的每条候选在记忆有字段时带 `fields`。
- 新的召回步骤 `record_audience`（默认的一套里，在 `linked` 后面、`fill_text` 前面）：记忆自己的受众比客户端窄就拿掉，没有受众按 `"owner"` 算；存储不记录字段或没打过标记时放行；主人级的客户端不受影响，`linked` 接上的记忆也检查。后端多了 `stamp_audience(level)`（第一次调用给没有受众的记忆盖章并记下，以后什么都不做；只有可写的、有字段列的存储能打标记）、只读属性 `checks_audience` 和 `set_audience(level, only_missing)`；网关建好时给每个共享存储打一次标记，租户存储每次由提供方交出时打。它拦住复制的存储文件、指向同一个文件的两个名字、没盖章就进来的记忆（见 docs/security.md）。受众不是主人的、能召回的客户端必须有一个执行它的召回步骤，否则启动报错；`feiyu-memgate check` 在客户端那一行写它拿到哪些字段，并对召回不逐条记录受众的存储提醒一行。
- 新命令 `feiyu-memgate restamp --config FILE --store NAME [--tenant ID] (--missing | --all)`：`--missing` 给没有受众的记忆设上存储声明的受众，`--all` 给每一条设上它（改了声明之后有意放宽或收窄整个存储）。遗忘恢复存档里没有受众的记忆（这个改动之前存的档）时，用存储声明的受众写回。说明见 docs/configuration.md 的“每条记录的受众”。
- 解释召回：新的请求 `POST /v1/explain`（docs/api.md）。回答是召回的 `results`、`more`、`failed`、`busy`，再加 `stages`（召回步骤的名字）和 `candidates`（这次看过的每条候选：`found`、`score`、`outcome`（`delivered`、`dropped`、`deferred`、`over_limit`、`not_reached`）、拿掉它的步骤 `stage`、添加它的步骤 `added_by`、脱敏并截到 300 字的 `text`、`date`），最多 100 条，多了有 `candidates_cut`。只给配置了 `explain = true` 的客户端（`ClientPolicy` 多了最后一个字段 `explain`），别的客户端 403；启动时要求这个客户端有 `recall` 且受众是 `owner`。它不运行各步骤的 `finish()`，不算一次用到；没有开解释的普通召回，回答和耗费都和以前一样。`MemoryClient` 和 `LocalClient` 多了 `explain()`，`feiyu-memgate check` 在开了它的客户端那一行写“可以查看召回的解释”。
- 受众级别（见上面“要改的地方”）：`ClientPolicy` 多了最后一个字段 `audience` 和属性 `reach`，`Gateway` 多了 `audiences=` 参数和 `audience_of(名字)`，召回步骤的 `ctx.audience` 是这次请求的客户端的受众。
- 客户端可以发送 `context`：`MemoryClient` 和 `LocalClient` 的 `recall`、`recall_detail`、`remember`、`remember_status`、`remember_messages` 多了只能按名字传的参数 `context=None`；不传时请求体和以前一样。约定三个常用的键 `channel`、`chat`、`via`，网关不解释它们，也不能把它们当身份（docs/api.md）。
- AstrBot 示例插件的主人编号要带平台前缀：`FEIYU_MEMGATE_OWNER_IDS` 写成 `平台名:编号`（例如 `aiocqhttp:10001`），同一个号码在另一个平台上不再被当成主人；没有 `:` 的条目被忽略，启动时记一条警告（只写条数）。召回和记忆的请求带上 `context`（`channel` 是平台名，`chat` 是 `private` 或 `group`）。**已经在用这个示例的请改环境变量**，否则主人的私聊不再召回。
- 关联召回：后端有了可选的能力 `neighbours(ref, limit, timeout=None, ctl=None)`（和已存的某一条最相似的最多 `limit` 条记忆，分数用后端自己的 0..1 相关度，不含它自己；`embedding` 用已存的向量，不请求 embeddings 接口，`hybrid` 问有这个方法的子后端再合并；`sqlite`、`inmemory`、`openviking` 没有），和召回步骤 `linked`（选项 `top` 默认 2、`max_added` 默认 2、`minimum` 默认 0.60；放在 `newer_first` 之后，`feiyu-memgate check` 会提醒顺序放反）。它在分数线之后，取最前面 `top` 条候选，把和它们最相似、相似度不低于 `minimum` 的记忆最多接上 `max_added` 条，总数不超过 `limit`，所以不改变“什么时候保持安静”。聊天测试题 `bench/chat`（bge-m3、`embedding` 后端、`broad` 档）的留出集上：连着的记忆被交出去的比例 29.2% → 42.7%，直接相关的那条 54.2% → 56.2%，每道闲聊交出的条数 3.73 → 4.27，交出去的条目里相关的占比 34.9% → 36.7%，追问命中和安静的比例没有变。这些数字只对 bge-m3 而言；别的模型沿用同一个 0.60，没有量过。每个划分只有 48 道闲聊，一道题约 2 个点。`RecallContext.run` 多了可选的 `until`（让等待比总时限早结束）。`bench/chat/run.py` 多了 `--linked on|off`（默认 `on`，是网关的默认步骤；`off` 是不含 `linked` 的默认步骤），用来在真实网关上量前后。设计和试过的其他做法见 docs/design/2026-11-18-linked-recall.md。
- 文档：`docs/api.md`（API 参考：HTTP 接口、Python 客户端、扩展接口、命令行、DSH 插件的接口，逐项按代码核对）。HTTP 接口和“写死的上限”两节从 `configuration.md` 移到了这里，原处留有指向新位置的说明。`docs/development.md`（开发指南）、`docs/extending.md`（二次开发指引）。
- 示例：`examples/custom-backend`（一个文件写成的存储后端）、`examples/chat-loop`（一轮对话从查询到写入的完整流程）。
- 自动检查：每次推送和每个 pull request 在 Linux 和 Windows 上运行网关和两个插件的测试（`.github/workflows/ci.yml`）。
- 记忆的日期取自消息自己的 `time`：`sqlite`、`embedding`、`hybrid`、`inmemory` 后端写入时，消息的 `time` 开头是真实存在的 `YYYY-MM-DD` 日期（后面可以接 `T` 或空格再接时间）就用这个日期，不换算时区；没有或不合格时和以前一样（当天的 UTC 日期，`inmemory` 不记日期）。**已经发送 `time` 的客户端，升级后会在记忆上看到这个日期，而不再是 UTC 的当天。** `openviking` 后端不涉及。新增 `feiyu_memgate.backends.base.message_date(message, default)`。
- `BlockFormatter.format` 多一个可选的 `today`（`YYYY-MM-DD`，`format_block` 同）：传了之后资料块写明今天的日期，每条有日期的记忆带上多久以前（今天、昨天、前天、N 天前、N 周前、N 个月前、N 年前），规则公开为 `feiyu_memgate.client.block.age_text`。不传或传的日期不合格时，输出和以前逐字相同。AstrBot 示例插件传入本机日期，`remember` 工具保存时带上本机的当前日期时间。
- DeepSeek Harness（DSH）插件，在 `plugins/` 下，发布到 npm：`dsh-feiyu-memgate`（自带网关、自动召回、记住和查找两个工具、设置页面，默认关键词检索）和可选的 `dsh-feiyu-memgate-embed`（本机 CPU 运行向量模型，模型点击后才下载）。两个包的版本号各自独立。
- AstrBot 示例插件：宿主不支持临时消息片段时，记忆块不再接到系统提示词末尾（那样每轮都会让提示词缓存失效），改为接在当前消息的文字末尾；`FEIYU_MEMGATE_FALLBACK` 可选 `message`（默认）、`system`、`off`。历史里留下的旧记忆块不会进入下一次的查询词，同一段记忆不会连续接两次。新增 `BlockFormatter.strip()`。插件的逻辑现在有测试（用模拟的 AstrBot 接口）。
- 召回步骤 `newer_first`（见上）。在开发集和测试集上，新旧两条记忆都被交付时新的排在前面的比例，默认的向量配置从约六成到全部；两份数据里这类问题分别只有 12 和 20 道。
- 租户绑定令牌：多租户客户端写 `tenant_binding = "token"` 后，`token_env` 里的密钥只用来铸造令牌，每个租户一个派生令牌（`feiyu-memgate tenants token --config <文件> --client <名字> --tenant <租户号>`），网关无状态校验，请求里的 `tenant_id` 可以不写，写了且和令牌不一致回 403 `tenant_mismatch`。`tenant_token_generation` 加一即吊销该客户端的全部派生令牌。这种客户端的限流先查每个租户自己的额度、通过了才扣总量，一个租户不能把别的租户挤掉。默认仍是原来的自报租户号，`check` 会对它给一行提醒。细节和限度见 security.md。
- 进程内客户端 `feiyu_memgate.client.LocalClient`：和 `MemoryClient` 同样的方法和返回值，不起服务、不占端口、不需要令牌，走的是网关自己的准入、召回和写入入口。代价（没有信任边界）和用法见 embedded.md，例子在 `examples/embedded/`。
- 分数线档位：`[recall] profile`（或 `min_score`、`relative_score` 的 `profile` 选项）在每个后端声明的两套分数线之间选择：`broad`（宽松，现在的默认，见“要改的地方”第一条）和 `precise`（少而准）。两档的数值、选法和实测取舍见 backends.md。
- `embedding` 后端的 `query_prefix`、`document_prefix` 选项，和上面说的五个模型的预设。
- `feiyu-memgate check` 会提醒：放错位置的召回步骤、自报租户号的多租户客户端、看起来不随机的铸造密钥、只设了合计每日上限的绑定客户端；并显示当前的分数线档位。
- 文档 deployment.md：进程形态、检索档位（从不用模型到 1.16 GB 共六档，各自的内存、延迟和质量）、分数线档位。
- 基准：四个更轻的向量模型和两个宽松档位的结果行，“交付里新的排在旧的前面”这一指标，分档对比图；混合后端的融合方式在开发集上重新比较过，结论是保持不变，并且它在测过的配置里都没有明显超过单用向量（`bench/experiments/`）。
- 基准的设计自查：`bench/design_probe.py` 在进程内实际运行本项目，测十项设计性质（只测本项目），结果和限度见 `bench/results/design/self-check.md`；被测项目的功能事实（形态、检索方式、写入是否调用大模型、默认交付、遗忘、许可证）见 `bench/results/design/features.md`。
- `POST /v1/used`：聊天程序回报这次回复真正依赖了哪几条召回结果（`{"used": [{"source", "id"}...], "tenant_id"?}`，最多 20 项），网关给这几条在账本里记一次“用到”，回 `{"status": "ok", "recorded": n}`；没配置遗忘时回 `recorded: 0`。新错误码 `bad_used`（400）。只能刷新时钟，不能删、改、读。Python 客户端：`MemoryClient.report_used(items, tenant_id=None)`。
- `feiyu_memgate.pieces`：做拼图要用的全部名字。`NULL_CTL`（什么都不做的取消控制）公开了；`ctl.cancelled` 和 `ctl.on_cancel(fn)` 让不是套接字的阻塞操作也能被取消。
- 加载配置时，配置文件所在的文件夹排在 Python 搜索路径最前面：放在配置旁边的拼图写 `模块名:类名` 就能用。
- 拼图可以有 `start(gateway)` 和 `close()`；召回步骤和写入守卫有 `name`（注册表填）和 `enforces`；召回步骤有 `cheap`；写入守卫有 `rollback(ctx)`。
- 召回结果每条带 `id`（存储里的编号）；回答多了 `busy`（几个存储忙没去查）。`Hit.meta` 会带到 `Candidate.meta`。
- 召回步骤 `relative_score`（选项 `ratio`、`gap`，`cheap`）；后端可以声明 `default_relative_ratio`，像 `default_min_score` 一样。
- `bench/dev_eval.py`：在开发集（默认 `bench/dev`）上打印命中率（Hit@1、Hit@5、交付命中）、精确率、安静率和 `balanced`；`--search` 做固定规则的阈值网格搜索并写 `bench/results/dev/<后端>.md`，`--sweep` 给出分数线 × 比例的取舍表，`--candidates` 读已算好的候选列表（`openviking` 用 `bench/adapters/openviking/run.sh dev` 生成）；`bench/README.md` 说明基准、测试集规则和各适配器的跑法。
- 基准适配器：`feiyu-memgate-sqlite`、`-embedding`、`-hybrid` 改用召回结果里的 `id`、用当前默认的召回步骤；新增 `feiyu-memgate-full`（hybrid + 默认步骤 + `freshness`，遗忘账本用临时文件，不做清理）。
- `tidy.idf_weight`、`tidy.gram_weights`、`tidy.index_grams`；`PreparedQuery` 可以带词权重。
- 写入的消息可以带 `time` 和 `speaker`；召回和写入都可以带一个小的 `context` 对象，召回步骤和写入守卫在 `ctx.context` 里看得到。
- 写入守卫或存储什么都没留时回 200 `{"status": "skipped"}`，不占当天次数；自带客户端多了 `skipped` 这个结果。
- 客户端设置 `daily_writes_total`（整个客户端每天的写入上限）和 `rate_limit_total`（多租户客户端合计的每分钟请求上限，默认 `rate_limit` 的 10 倍）。
- `[server]` 的 `store_max_inflight`：一个存储同时最多几个调用。
- `[redaction]` 的每一项可以写成带 `type` 和选项的表。
- 深度健康检查多了 `recall_busy_1h` 和写入的 `untracked`。
- 支持 `Expect: 100-continue`：检查都过了才让客户端发请求体，没过直接回答。
- 一次写入几条消息，在 `sqlite` 和 `inmemory` 存储里现在是全有或全无：以前逐条写，第三条失败时前两条已经存下，调用方只知道失败，重发会存两遍。现在先全部准备好再一次写入，中途出错或者调用方已经超时放弃，一条都不留。`embedding` 和 `hybrid` 本来就是这样；`openviking` 做不到（docs/backends.md“一次写入几条消息”）。
- `hybrid` 存储搜索时同时问它的子后端，每个都拿到完整的时限（以前一个问完再问下一个，各分一份时间），延迟是最慢的那个。某个子后端出错或超时，以前只是悄悄跳过；现在记进 `child_failures`，并在这次请求的访问日志里留下 `child-failed`。搜索结果和以前一样。
- `embedding` 存储的搜索和读取不再等写入：读走只读连接（新设置 `max_readers`，默认 8），把向量读进内存也不占写入的锁。库里多了一个写入计数（`info` 表的 `gen`），用来发现别的进程写过；旧版本打开这样的库不受影响。搜索结果和以前一样。
- 自己写的后端可以调用 `ctl.note(词)` 给这次请求的访问日志留一个词（只写原因，不写内容；`ctl` 可能没有这个方法，用 `getattr` 取）。

### 修好的

- 命令行在写不出中文的控制台或管道上（例如语言设为西文的 Windows，编码是 cp1252）不再以 `UnicodeEncodeError` 和退出码 1 结束：写不出的字符显示成替代符号，命令照常完成。要看到正常的中文，设环境变量 `PYTHONUTF8=1`。
- `pyproject.toml` 的构建要求改成 `setuptools>=77`：`license = "MIT"` 加 `license-files` 的写法从这个版本起才被接受。
- 没装 `cryptography` 时，测试 `test_a_wrong_key` 报错而不是跳过。
- 文档里和代码不一致的几处：`healthz?deep=1` 里 `stores` 的取值（`ok` 或 `down`）、`limit` 类型不对时的错误码、`/v1/remember` 对 `role` 和空内容的要求、`/v1/used` 的上限、`WriteRejected` 按状态码取的默认错误码。
- HTTP 服务器把 `PUT`、`DELETE`、`PATCH`、`HEAD`、`OPTIONS` 都当成 `GET` 处理，`DELETE /healthz` 会回 200 和健康信息。现在每个方法用自己的名字：路由表里没有的“方法 + 路径”回 405 `method_not_allowed`（路径不认识回 404），访问日志里是真实的方法；`HEAD` 只回状态和头，没有正文。
- 写入守卫抛异常时以前可能留下做了一半的事：现在回 500 `guard_error`，什么都不写，已经放行的守卫按相反顺序撤回。
- 每日写入上限在同时来的写入下可能被超过：现在先占名额后写，没成就还回去。
- 召回步骤出错时以前整次召回失败：`admit` 出错只丢那一条，`prepare` 出错回 503 `stage_error`，不返回没检查过的结果；`admit` 回了看不懂的答案也丢掉那一条。
- 一个存储返回了不合规的结果，以前可能让整个召回出错：现在只算它没查成。
- 召回的时限以前不管召回步骤：现在时限到了就停，剩下的计入 `more`。
- `more` 以前会把明显会被丢掉的候选也算进去：现在放不下的候选先过一遍便宜的步骤，`more` 是“最多还有几条”。
- 线程池满时以前直接放弃：现在召回最多等一小会儿（0.25 秒以内），写入等不到回 503 `busy`，并且和“存储坏了”分开统计。
- 一个卡住的存储以前能占满所有工作线程；多租户请求以前按每次存储调用记配额，一个请求能占多份。
- 编造租户编号以前能放大请求次数和写入次数，限流表也会无限变大。
- 连接数满时的 503 现在也是 JSON。
- 自带客户端把中文转义成 `\uXXXX` 发送，一次写入在限制以内也可能超过 64 KiB 被拒绝：现在按 UTF-8 发。
- 写入跟踪表满了被挤出去的写入以前不计数：现在记为 `untracked`。
- `openviking` 后端对真实的 OpenViking v0.5.3 核对后修了三处（以前只在手写的假服务器上测过）：`refs()` 翻的
  `fs/ls?recursive=true` 在真实服务器上只列目录，240 个文件一个也看不见，现在用 `fs/tree` 加 `offset` 翻页；
  `delete()` 对不存在的文件以前回 `True`（服务器回 200 而不是 404），现在先 `fs/stat` 再删，不存在回 `False`；
  分数对任何问题都偏高，默认的 `min_score` 什么都滤不掉，现在像 `embedding` 一样用 `floor`（默认 0.5）和
  `ceil`（默认 1.0）换算成 0..1，原始值在 `Hit.meta["raw_score"]`。默认值只在 `examples/eval` 开发集上用 bge-m3
  选出，依赖向量模型，说明见 backends.md。

## 0.1.0

第一个公开版本，从来源项目的记忆模块里拆出来并改成可配置的框架。

- 拼图注册表：`backend`、`recall_stage`、`write_guard`、`redactor`、`tenants` 五种插槽，支持配置里写模块路径、代码里注册、安装包入口点三种方式。
- 网关：按令牌分权限（`ClientPolicy`）、多租户隔离、限流、每日写入上限、召回总时限和冷启动时限、深度健康检查、写入结果跟踪。可以走 HTTP，也可以在同一个进程里直接调用。
- 后端：`openviking`、`sqlite`（关键词检索，不需要模型）、`inmemory`。
- 召回步骤：`min_score`、`source_weight`、`drop_marker`、`fill_text`、`drop_oversize`、`redact`、`dedupe`、`budget`。
- 写入守卫：`redact`、`daily_cap`。
- 租户：`keyfile`、`directory`。
- 客户端：`MemoryClient`、`build_query`、`BlockFormatter`。
- 工具：`feiyu-memgate` 命令行，加密备份、召回评测、租户密钥管理。
- AstrBot 接入示例。
- 文档：架构、拼图、配置、安全模型、路线图、原始设计文档、同类项目调研。
