# 拼图：选用、替换、自己做一块

feiyu-memgate 的每一部分都是一块拼图。你可以：

- **不要某一块**：配置里不写它就行。不想按字数封顶，就把 `budget` 从 `stages` 里去掉。
- **换一块**：不想用向量模型，把存储的 `type` 从 `openviking` 换成 `sqlite`。
- **自己做一块**：写一个类，在配置里用 `模块路径:类名` 指过去，不用改 feiyu-memgate 的任何代码。

查看现在有哪些拼图：

```bash
feiyu-memgate pieces
```

## 从哪里导入

做拼图要用到的名字都在一个模块里：

```python
from feiyu_memgate.pieces import RecallStage, KEEP, DROP, DEFER, WriteGuard, WriteRejected, register
```

`feiyu_memgate.pieces` 里有：`MemoryBackend`、`RecallStage`、`WriteGuard`、`TenantProvider`、`ForgettingCurve`、`KEEP`、`DROP`、`DEFER`、`RecallContext`、`WriteContext`、`Hit`、`Candidate`、`ClientPolicy`、`BackendError`、`ConfigError`、`WriteRejected`、`register`、`NULL_CTL`。这些是对拼图的承诺；`feiyu_memgate.core` 下面的都是内部实现，版本之间可能变，不要直接导入。

## 拼图怎么被找到

配置里的 `type` 按下面的顺序查：

1. **带冒号的路径**：`type = "my_pkg.my_module:MyBackend"`。加载配置时导入这个模块，取这个名字。配置文件所在的文件夹排在搜索路径最前面，所以放在配置旁边的 `my_stage.py` 写 `type = "my_stage:MyStage"` 就能用，不用安装（那个文件夹里不要放别的 `.py` 文件，免得和别的模块重名）。
2. **代码里注册的名字**：`@register("backend", "mine")`。内置拼图都是这样注册的；你的代码只要在加载配置之前被导入过，也可以这样用。同一个插槽里，同一个名字注册给两个不同的东西会报错，不会悄悄顶替。
3. **安装包的入口点**：别人 `pip install` 你的包就能用名字引用。在你的 `pyproject.toml` 里：

   ```toml
   [project.entry-points."feiyu_memgate.backend"]
   mine = "my_pkg.my_module:MyBackend"
   ```

   入口点的组名是 `feiyu_memgate.` 加插槽名：`backend`、`recall_stage`、`write_guard`、`redactor`、`tenants`、`forgetting_curve`、`identity`。

配置表里除了 `type` 以外的每一项，都会作为关键字参数传给拼图的构造函数。名字以 `_env` 结尾的项会先换成环境变量的值：`key_env = "MY_KEY"` 到了构造函数里是 `key="<MY_KEY 的值>"`。密钥就这样进来，不用写进配置文件。

## 每块拼图都可以有的东西

| 属性或方法 | 谁用 | 意思 |
|---|---|---|
| `name` | 召回步骤、写入守卫 | 按名字建出来时由注册表填上（用路径建的保持 `None`，用类名）。日志和访问日志里的 `stage-error:<name>` 用它 |
| `enforces` | 召回步骤、写入守卫 | 这块拼图保证了哪些客户端设置，比如 `("redaction",)`、`("daily_writes",)`。客户端写了某项限制、却没有任何拼图声明执行它，配置拒绝启动。自己做的脱敏步骤或限额守卫要声明它，才能替代内置的那块 |
| `cheap` | 召回步骤 | `True` 表示 `admit` 不做 I/O、不做重活、没有副作用。这样的步骤也会去看放不下的候选，让 `more` 更准 |
| `start(gateway)` | 任何拼图 | 网关开始服务前调用一次（起后台线程、预热）。抛异常会让启动失败，日志里只有错误类型 |
| `close()` | 任何拼图 | 网关关闭时调用一次，后启动的先关。抛异常只记日志，不影响别的拼图关闭 |

## 做一块 recall_stage（召回步骤）

召回步骤是最常自己做的一块。继承 `RecallStage`，按需要实现这几个方法：

```python
from feiyu_memgate.pieces import RecallStage, KEEP, DROP, DEFER

class MyStage(RecallStage):
    def __init__(self, some_option=1):          # 配置里的选项
        self.some_option = some_option

    def prepare(self, candidates, ctx):
        """全部候选、排序之前，调用一次。只做便宜的事：丢掉一些、改分数。返回候选的列表。"""
        return candidates

    def admit(self, candidate, ctx):
        """按分数从高到低，一条一条问。回答 KEEP、DROP 或 DEFER（是好的但放不下，计入 more）。"""
        return KEEP

    def finish(self, ctx):
        """全部候选看完以后调用一次，ctx.accepted 是要返回的。只做记账（比如数用了几次），不能改结果。"""
```

- `candidate` 有 `score`、`source`（存储的名字）、`text`、`date`、`ref`、`store`、`meta`（后端在 `Hit.meta` 里给的东西会带过来，你也可以往里放）。
- `ctx` 有 `query`、`limit`、`client`、`tenant_id`、`tenant_source`、`public`、`redact`（这个客户端该用的脱敏函数）、`context`（请求里可选的 `context` 对象，已经检查过）、`accepted`（已经留下的候选）、`notes`（写进访问日志的词，只能写原因，不能写内容）、`state`（这一次请求里你自己的状态）、`time_left()`、`expired()`、`note(word)`、`run(fn, store=None)`（在工作线程上跑一个会阻塞的调用，返回 `(ok, result)`，超时自动取消；访问存储时把 `store` 传进去，每个存储的并发上限才管得到）。
- 步骤对象是所有请求共用的，**不要把请求相关的东西存在 `self` 上**，放进 `ctx.state[self]`。
- 召回的时限也管召回步骤：时限一到，还没问到的候选不再放入，计入 `more`。

步骤出错时网关这样处理（都会写一行 `piece-error` 日志，只有名字和错误类型）：

| 出了什么事 | 结果 |
|---|---|
| `admit` 抛异常 | 这一条丢掉，访问日志记 `stage-error:<name>`，召回照常 |
| `admit` 回了 `KEEP`、`DROP`、`DEFER` 以外的东西 | 这一条丢掉，记 `bad-verdict:<类名>` |
| `prepare` 抛异常，或者返回的不是候选列表 | 这次召回回 503 `stage_error`，不返回任何结果：宁可不给，也不给没检查过的 |
| `finish` 抛异常 | 记 `finish-failed`，结果照常返回 |

仓库里有一个完整的例子，[examples/custom-stage/](../examples/custom-stage/)：[recency_stage.py](../examples/custom-stage/recency_stage.py) 按日期让新的记忆排在旧的前面，[gateway.toml](../examples/custom-stage/gateway.toml) 就放在它旁边：

```toml
[recall]
stages = [
    "min_score",
    { type = "recency_stage:Recency", half_life_days = 30 },
    "redact",
    "dedupe",
    "budget",
]
```

顺序有讲究：

- `min_score` 放最前，分数线只看相似度；改分数的步骤（`source_weight`、`freshness`、上面的 `Recency`）放它后面。
- `relative_score` 放在所有改分数的步骤后面（默认的一套里没有改分数的步骤，所以它紧跟 `min_score`）。
- `newer_first` 放在 `min_score` 和 `relative_score` 后面：分数线只看相似度，它换过的分数不能再被阈值看到。
- `linked` 放在 `min_score`、`relative_score` 和 `newer_first` 后面（它要在分数线定了交哪些之后才看最前面几条），`fill_text` 前面（接上的记忆没带全文时要补读，之后还要脱敏、去重、算字数）；有 `freshness` 时也放在它后面。
- `fill_text` 要在所有读文字的步骤前面（有的后端搜索结果不带全文，要补读）。`drop_marker` 放在它后面，只出现在全文里的标记才看得到。
- `redact` 要在 `dedupe` 和 `budget` 前面，这样去重和字数算的是真正返回的文字。
- `budget` 放最后。

不写 `stages` 时用的默认一套（`DEFAULT_STAGES`）是：`min_score`、`relative_score`、`newer_first`、`linked`、`record_audience`、`fill_text`、`drop_oversize`、`redact`、`dedupe`、`budget`。

内置步骤一览：

| 名字 | 做什么 | 选项 |
|---|---|---|
| `min_score` | 丢掉相似度低于分数线的 | `value`（不写时按 `profile` 用存储声明的分数线，存储也没有就是 0.5）、`per_store`（按存储分别设）、`profile`（`"broad"` 默认：用存储的 `broad_min_score`；或 `"precise"`：用存储的 `default_min_score`；存储没声明 `broad_min_score` 时两档都用 `default_min_score`；`value` / `per_store` 优先） |
| `relative_score` | 丢掉分数远低于本次请求里最高分的：低于 `ratio` × 最高分（可选再低于最高分 − `gap`）的不要。最高分是走到这一步的候选里最高的，放在 `min_score` 后面时就是过了分数线的最高分 | `ratio`（0..1，不写时按 `profile` 用存储声明的比例，存储也没有就是 0.6）、`gap`（默认不用）、`profile`（同 `min_score`，`"broad"` 默认用存储的 `broad_relative_ratio`，`"precise"` 用 `default_relative_ratio`；`ratio` 优先） |
| `newer_first` | 同一次请求里，分数不低于最高分 × `band` 的候选中，带日期的按“新的在前”交换分数；分数的多重集合不变，不丢任何候选。没有日期的记忆不受影响 | `band`（默认 0.85）、`related`（0..1，默认 0 = 不用：大于 0 时，两条记忆的文字字符二元组重叠不低于它才交换）、`min_days`（默认 1，日期相差不到这么多天算同一时间） |
| `linked` | 分数线之后，看最前面 `top` 条候选，向它们的存储要最相似的记忆（后端的可选方法 `neighbours`），把相似度不低于 `minimum` 的、还不是候选的最多 `max_added` 条接在最后，且只在候选数低于本次请求的 `limit` 时；没有 `neighbours` 的存储什么都不加。它在分数线之后才起作用，一条候选都没有就什么也不加（该安静时仍然安静） | `top`（默认 2，1 到 5）、`max_added`（默认 2，0 到 5，0 就是关掉）、`minimum`（默认 0.60，0 到 1） |
| `record_audience` | 记忆自己的受众（字段 `audience`）比这个客户端的受众窄就丢掉，没有受众按 `owner` 算；存储不记录字段或没打过标记（`checks_audience` 不是 `True`）时放行；`linked` 接上的记忆也检查。`enforces` 是 `("record_audience",)`：受众不是 `owner` 的客户端要有它，见 [configuration.md](configuration.md#每条记录的受众)。放在 `fill_text` 前面，不显示的记忆就不用读全文 | 无 |
| `source_weight` | 分数乘上来源权重，让一种来源排在另一种前面 | `weights`、`default` |
| `drop_marker` | 丢掉带标记串的记忆（比如写入自检留下的）。不在默认的一套里 | `markers` |
| `fill_text` | 搜索结果没带全文的，去存储里读（每次请求最多读几条，超时不读） | `max_reads`（默认 3） |
| `drop_oversize` | 太长的整条丢掉，不截断 | `max_chars`（默认 65536） |
| `redact` | 用这个客户端该用的脱敏函数清洗全文；清完是空的就丢掉 | 无 |
| `dedupe` | 近似重复的只留分数最高的一条 | `overlap`（默认 0.8） |
| `budget` | 返回的总字数封顶；放不下的整条不放，计入 `more` | `max_chars`（默认 1500） |

`drop_marker`、`drop_oversize`、`dedupe`、`relative_score`、`record_audience` 是 `cheap` 的。

**`relative_score` 是怎么来的。** 向量检索里，对的记忆通常明显高于其余的，一个全局分数线要么放进太多杂项、要么丢掉对的；
按“离最高分多远”切，能去掉大部分杂项而不用把分数线定死。它在 `prepare` 里工作，只和同一次请求里的候选比较：
几条记忆分数差不多时都留下，只剩一条很高、后面拖着一串时只留前面那条；它不判断“值不值得说话”（那是 `min_score` 的事），
所以最高分本身很低时，最高的那条也还会留着。`ratio` 是比例，对分数整体放大缩小不敏感；`default_relative_ratio` 由后端声明，
因为关键词分数里对的和错的常常很接近（`sqlite` 0.75），向量分数里则拉得开（`embedding`、`hybrid` 0.85，`openviking` 0.95）。

开发集 `bench/dev`（160 条记忆、110 个问题，`bench/dev_eval.py`）上看到的取舍，`embedding`，`min_score` 0.50：

| `ratio` | 不用 | 0.95 | 0.9 | 0.85 | 0.8 | 0.7 | 0.6 |
|---|---|---|---|---|---|---|---|
| 交付命中 | 78.8% | 73.8% | 75.0% | 77.5% | 77.5% | 77.5% | 78.8% |
| 精确率 | 62.6% | 83.8% | 82.2% | 83.7% | 78.8% | 70.8% | 64.4% |
| 每题平均条数 | 1.55 | 0.77 | 0.87 | 0.94 | 1.04 | 1.25 | 1.47 |
| balanced | 67.1% | 73.4% | 73.4% | 74.9% | 73.2% | 70.2% | 67.9% |

各后端默认值的选法（一条事先定好的规则，`balanced`：可答题的交付 F1 和没有答案的题的安静率按题数加权）和每个后端选出的值、
同一批候选上旧值的成绩，见 [backends.md](backends.md)；每个后端的前 5 名在 `bench/results/dev/`。
更早用的开发集是 `examples/eval`（46 条记忆、30 个问题），那里选出的值（`sqlite` 0.6、`embedding`/`hybrid` 0.8）在测试集上偏严。
开发集仍然有限，这是取向而不是定论。

**默认的一套里它放在 `min_score` 后面。** 之后如果有改分数的步骤（`source_weight`、`freshness`、上面的 `Recency`），
把 `relative_score` 放在它们后面：它比较的应当是决定排序的那个分数。例如带遗忘曲线时：
`min_score`、`freshness`、`relative_score`、`fill_text`、……

**`newer_first`：新旧两条互相矛盾的记忆，新的排前面。** 同一件事先后各有一条记忆（“住在甲地”“搬到乙地”）时，两条对同一个问题的分数常常很接近，
谁排第一近乎随机。它在 `prepare` 里只看本次请求的候选：分数不低于最高分 × `band` 的是带内候选；带内带有效 ISO 日期的候选之间交换分数，
使它们按新旧排列，新的在前。带内分数的多重集合不变（后面的阈值、`budget` 和调用方看到的分数范围都和原来一样）；
没有日期的候选和带外的候选不动；相似度原值留在 `meta["similarity"]`。它从不丢候选，被交付的集合只有在 limit 或 `budget` 截断列表时才会因此改变。

放在 `min_score` 和 `relative_score` 后面。例如：`min_score`、`relative_score`、`newer_first`、`fill_text`、……
`feiyu-memgate check` 会提醒顺序反了的情形（它调用 `stages.misplaced()`，返回放错位置的步骤名）。

和 `freshness` 的区别：`freshness` 按绝对年龄把分数乘一个 floor 到 1 之间的系数，两条记忆都很旧时都落在 floor，分不出先后；
`newer_first` 比较的是同一次请求里候选之间的相对新旧，不看绝对年龄，也不需要遗忘账本。两者可以一起用（`freshness` 在前）。

代价：带内一条更新但相关度稍低的记忆，现在可以排到更相关的旧记忆前面；`band` 越小，这种情形越多。
`related` 大于 0 时只在文字相近的两条之间交换，用来避开“两条毫不相干的记忆碰巧都得高分”；
但字符二元组重叠对短句里换了几个词的新旧事实很低（开发集里 12 对新旧记忆只有 0.02 到 0.14），取值要自己在数据上量，
`related` 默认不用。`newer_first` 在默认的一套里（它不改变交付哪些记忆，只改顺序）；不想要就自己写 `stages` 不列它。默认 `band` 0.85 是在开发集上选的（只有 12 道更新题，数字见 `bench/experiments/newer_first/README.md`），是取向而不是定论。

**`linked`：和最相关的几条连着的记忆一起带回来。** 一句闲聊（“最近天气有点热”）常常只和一条记忆很近（“主人喜欢吃冰淇淋”），和连着它的另一条（“上次冰淇淋掉在了地上”）没有相同的词。它在 `prepare` 里工作，在分数线之后：

1. 按分数从高到低（召回随后就按这个顺序交），取最前面的 `top` 条候选；
2. 对每一条，向**它自己的存储**调用 `neighbours(ref, 数量, 时限, ctl)`（只问有这个方法的存储），通过召回上下文的 `ctx.run` 去做，和 `fill_text` 补读全文是同一个机制：占用线程池的名额、受每个存储的并发上限管、时限一到就切断；这一步所有查询合起来最多等 1 秒、不超过剩余时间的一半，所以卡住的存储不会拖掉已经查到的第一轮结果；
3. 留下相似度不低于 `minimum`、还不是候选（同一个存储、同一个 ref）的邻居；同一条被两条候选找到只算一次，取较高的相似度，所有来源一起按相似度从高到低排；
4. 最多接 `max_added` 条，而且接完以后候选总数不超过请求的 `limit`（候选已经够 `limit` 条时，什么都不问）；
5. 接上的候选带着它是从哪条记忆找来的那条的 `source` 和 `store`；分数是“那条记忆的分数 × 邻居的相似度”，并且不高于第一轮里最低的分数，所以召回随后按分数排序时它们仍然在所有第一轮候选后面，彼此之间保持挑出来的顺序；`meta["linked_from"]` 是它是从哪条记忆找来的（`meta` 不会交给客户端，HTTP 回答没有新字段）。

出错、超时、没有这个方法都是“什么也没加”，不会让召回失败；出错和超时记一次 `linked-failed`。它从不增加“能不能开口”：没有候选就没有邻居可找。它同样接受 `feiyu-memgate check` 的顺序提醒（`Linked.after`，由 `stages.misplaced()` 检查）。

和 `freshness` 的关系：接上的记忆在 `freshness` 之后才产生，所以没有乘新鲜度，也没有 `meta["retention"]`；交付以后它按 `credit` 规则同样算“交付”（`meta["similarity"]` 是它排在后面的那个分数，`"top"` 下通常达不到最高者的 `credit_ratio` 倍，`"all"` 会记）。账本里记的 ref 和来源就是它自己的，和别的记忆没有区别。

## 做一块 backend（存储）

继承 `MemoryBackend`，最少只要实现 `find`：

```python
from feiyu_memgate.pieces import Hit, MemoryBackend, register

@register("backend", "mine")
class MyBackend(MemoryBackend):
    writable = True
    default_min_score = 0.4      # 可选：profile = "precise" 时 min_score 的分数线（default_ 是历史命名，指 precise 档，不是默认档位）
    default_relative_ratio = 0.8 # 可选：profile = "precise" 时 relative_score 的比例
    broad_min_score = 0.3        # 可选：profile = "broad"（默认档位）时的分数线；不声明就两档都用上面的 default_ 值
    broad_relative_ratio = 0.6   # 可选：profile = "broad"（默认档位）时的比例

    def __init__(self, url, key):
        ...

    def find(self, query, limit, timeout=None, ctl=None):
        """返回最多 limit 个 Hit(ref, score, text, date, meta)。score 在 0 到 1 之间，越大越相关。"""

    def read(self, ref, timeout=None, ctl=None):
        """一条记忆的全文。只有 find 没带 text 的时候才会被调用。"""

    def remember(self, messages, timeout=None, ctl=None):
        """存下 [{"role", "content", "time"?, "speaker"?}]，返回这次写入的回执编号；什么都没留就返回 None。"""

    def health(self, timeout=2.0):
        """还能不能用。不能抛异常。"""
```

要点：

- **怎么搜由你定**：向量、关键词、图、全量扫描都行。网关只要求分数在 0 到 1 之间。不同后端的分数不在一个尺度上时，声明 `default_min_score`（和 `default_relative_ratio`；这是 `precise` 档的分数线，`default_` 是历史命名；默认档位 `broad` 的分数线用 `broad_min_score`、`broad_relative_ratio` 声明，不声明就用 `default_` 的值），或者用 `min_score` 的 `per_store` 分别设分数线。
- **`find` 的结果要合规**：一个列表，每项有字符串 `ref`、有限的数字 `score`、字符串或 `None` 的 `text` 和 `date`。不合规的整个结果当作这个存储“这次没查成”，日志记 `bad-result`。`ref` 会作为结果里的 `id` 交给客户端。
- **怎么存也由你定**：原样存下来，或者交给模型抽取。看过以后什么都不该留，就返回 `None`，客户端会得到 `{"status": "skipped"}`。如果是“先收下、之后再整理”的，把 `tracks_writes` 设成 `True` 并实现 `write_state(receipt)`（返回 `"saved"`、`"empty"` 或 `None`），网关就会替你统计每次写入的下场。
- **出错就抛异常**（推荐 `BackendError`）。召回时网关把这个存储记为“这次没查成”，继续用别的存储；写入时回 502。错误信息里不要带记忆内容和密钥，日志里网关也只记错误类型。
- **`ctl`**：如果你的后端会开网络连接，连上以后调用 `ctl.attach(sock)`，结束时 `ctl.detach()`。这样召回超时的时候，网关能直接切断连接，不会被慢的上游拖住线程。不是套接字的阻塞操作，用 `ctl.on_cancel(fn)` 登记一个取消函数（超时时调用，已经超时就立刻调用），或者在循环里看 `ctl.cancelled`，是 `True` 就尽快返回。不开连接的后端不用管它。自己直接调用后端、没有 `ctl` 可给时，传 `NULL_CTL`（什么都不做的那个）。
- 想让评测工具（`feiyu-memgate eval`）能往你的后端里灌测试数据，再提供一个 `put(text, ref=None, date=None)`。
- 想让 `linked` 步骤能从最相关的几条出发找到和它们连着的记忆，提供一个可选的 `neighbours(ref, limit, timeout=None, ctl=None)`：返回和已存的这一条（`ref`）最相似的最多 `limit` 条记忆，最好的在前，是 `Hit`，`score` 用这个后端自己的 0 到 1 的相关度（和 `find` 同一个尺度），**不含这一条自己**；`ref` 不存在返回 `[]`。时限和 `ctl` 的意思和 `find` 一样；出错就抛异常，不要只返回一部分。没有这个方法的后端，这一步就不碰它。内置的 `embedding` 和 `hybrid` 有，`sqlite`、`inmemory`、`openviking` 没有。

最小的完整例子是 [backends/inmemory.py](../src/feiyu_memgate/backends/inmemory.py)。

## 做一块 write_guard（写入守卫）

```python
from feiyu_memgate.pieces import WriteGuard, WriteRejected

class NoLinks(WriteGuard):
    def before(self, messages, ctx):
        """落库之前。可以改消息后返回（返回空列表 = 这次什么都不写），也可以抛 WriteRejected 拒绝。"""
        if any("http://" in m["content"] or "https://" in m["content"] for m in messages):
            raise WriteRejected(400, "links are not stored", "has-link", code="has_link")
        return messages

    def rollback(self, ctx):
        """before 放行了，但这次写入最后没成（后面的守卫拒绝、存储出错、什么都没留）。撤回 before 做的事。"""

    def after(self, receipt, ctx):
        """存储确认收下之后。"""
```

`ctx` 有 `client`、`tenant_id`、`policy`、`public`、`redact`、`key`（客户端名，多租户时是 `客户端:租户`）、`context`（请求里可选的 `context` 对象）、`state`（这一次写入里你自己的状态）。守卫对象可以有自己的状态（计数器），但要能被多个线程同时用；要在 `rollback` 里撤回的东西放在 `ctx.state[self]`。

`WriteRejected(status, error, note="", code=None)`：`status` 是 400 到 599 的状态码，`error` 是给人看的话，`note` 进访问日志，`code` 是给程序的稳定错误码，不给就按状态码取通用值（400 是 `bad_request`，401 是 `unauthorized`，403 是 `forbidden`，404 是 `not_found`，413 是 `too_large`，429 是 `rate_limited`，503 是 `busy`；其余 4xx 是 `bad_request`，其余 5xx 是 `internal`）。全部错误码见 [api.md](api.md#错误码)。

守卫出错时：

| 出了什么事 | 结果 |
|---|---|
| `before` 抛 `WriteRejected` | 按它的状态码和错误码回答；已经放行的守卫按相反顺序 `rollback` |
| `before` 抛别的异常，或者返回的不是消息列表（忘了 `return` 也算） | 500 `guard_error`，什么都没写，已经放行的守卫都 `rollback` |
| 守卫们返回空列表，或者存储返回 `None` | 200 `skipped`，全部 `rollback`，`after` 不调用 |
| 存储出错或超时 | 502，全部 `rollback` |
| `rollback` 或 `after` 抛异常 | 只记日志，不影响别的守卫，也不影响结果 |

内置的 `redact` 清洗每条消息的 `content`、`speaker` 和 `time`；`daily_cap` 先占一个名额再写，写入没成就把名额还回去，所以同时来的写入不会超过上限。

“公开场合先分类再入库”这类筛选，就是一块写入守卫：在 `before` 里把不该进记忆的消息去掉。

## 做一块 redactor（脱敏）

一个返回 `str -> str` 函数的工厂：

```python
from feiyu_memgate.pieces import register

@register("redactor", "mine")
def make():
    return lambda text: text.replace("公司内部代号", "[已隐去]")
```

配置里选用：

```toml
[redaction]
private = "secrets"    # 非公开客户端用的
public = "mine"        # public = true 的客户端用的
```

也可以写成带 `type` 和选项的表，选项会传给工厂。内置的 `secrets` 遮掉密钥、令牌、密码这类东西；`public` 在它之上再遮手机号、邮箱、证件号和地址。它们是正则规则，会漏；真正管用的第一道线是**不让公开客户端读到不该读的存储**。

## 做一块 tenants（租户）

```python
from feiyu_memgate.pieces import TenantProvider, register

@register("tenants", "mine")
class MyTenants(TenantProvider):
    def valid_id(self, tenant_id):
        """编号的格式对不对。要严格：这个编号会出现在日志和限流的键里。"""

    def store(self, tenant_id):
        """这个租户的存储；没登记过的返回 None（请求会得到 403 unknown_tenant）。每次请求都会调用，要有缓存。"""
```

内置的两种：`keyfile`（一个 JSON 文件，租户编号对应这个租户自己的密钥，改了文件不用重启）和 `directory`（一个文件夹，每个租户一个 SQLite 文件）。

## 给自己的拼图写测试

所有接口都可以不经过 HTTP 直接测：

```python
from feiyu_memgate.backends.inmemory import InMemoryBackend
from feiyu_memgate.core.pool import Pool
from feiyu_memgate.core.recall import RecallContext, recall

pool = Pool(2)
store = InMemoryBackend(memories=["周末去了动物园"])
out = recall({"notes": store}, [MyStage()], RecallContext("动物园", 5, pool=pool))
pool.close()
```

（测试里可以用 `feiyu_memgate.core`；拼图本身只从 `feiyu_memgate.pieces` 导入。）也可以建一个 `Gateway` 直接调用 `recall(...)`、`remember(...)`，它们从不抛异常，总是返回一个带状态码的回答。仓库里的 [tests/test_examples.py](../tests/test_examples.py) 就是这样测 `Recency` 的。
