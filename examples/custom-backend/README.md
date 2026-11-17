# 自己写一个存储后端

一个文件就是一个完整的后端：[jsonl_backend.py](jsonl_backend.py) 把记忆存在一个 JSON Lines 文件里，一行一条，按“查询词和记忆共有多少个词”打分。[gateway.toml](gateway.toml) 用 `模块名:类名` 指向它，不用安装，也不用改 feiyu-memgate。

做后端的规则（接口、分数、出错怎么报、`ctl`、可选的能力）在 [docs/pieces.md](../../docs/pieces.md) 的“做一块 backend”一节；这个例子只用到最少的一部分：`find`、`remember`、`health`，以及 `default_min_score` / `broad_min_score` 两个分数线。

## 运行

需要 Python 3.11 或更高，不需要装任何依赖。在仓库根目录里，先在环境变量 `GATEWAY_TOKEN_OWNER` 里放一个至少 32 个字符的随机令牌，再检查配置：

```bash
export PYTHONPATH=src PYTHONUTF8=1          # 没有安装 feiyu-memgate 时，从源码运行；PYTHONUTF8 让 Windows 的终端按 UTF-8 输出
export GATEWAY_TOKEN_OWNER=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
python -B -m feiyu_memgate check --config examples/custom-backend/gateway.toml
```

```text
存储：
  notes: JsonLinesBackend，可写
租户来源：
  （没有）
客户端：
  owner: 召回 notes；写入 notes；租户 无；公开 否；每日写入上限 50；令牌 有可用令牌
召回阶段（按顺序）：min_score -> redact -> dedupe -> budget
分数线档位：min_score broad
写入关卡（按顺序）：redact -> daily_cap
```

已经安装了 feiyu-memgate 的话，`python -B -m feiyu_memgate` 换成 `feiyu-memgate` 就行。启动网关，另开一个终端存一条、再问一次（记忆文件 `data/notes.jsonl` 在启动网关时所在的文件夹里，文件夹不存在会自己建）：

```bash
python -B -m feiyu_memgate serve --config examples/custom-backend/gateway.toml
```

```bash
curl -s -X POST http://127.0.0.1:8080/v1/remember -H "Authorization: Bearer $GATEWAY_TOKEN_OWNER" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "I am allergic to peanuts", "time": "2026-11-14"}]}'
# {"status": "ok", "receipt": "w-..."}

curl -s -X POST http://127.0.0.1:8080/v1/recall -H "Authorization: Bearer $GATEWAY_TOKEN_OWNER" \
  -H "Content-Type: application/json" -d '{"query": "what am I allergic to"}'
# {"results": [{"id": "m-...", "source": "notes", "text": "I am allergic to peanuts", "score": 0.8, "date": "2026-11-14"}], "more": 0, "failed": 0, "busy": 0}
```

换一个毫不相干的问题（`{"query": "something unrelated"}`），得到 `{"results": [], "more": 0, "failed": 0, "busy": 0}`：没有共同的词，分数是 0。

## 它做了什么

- `find`：把查询词和每条记忆各切成词（连续的英文字母或数字算一个词，一个汉字算一个词，不分大小写），分数是“查询词里有多大比例的词也在这条记忆里”，在 0 到 1 之间，没有共同词的不返回。
- `remember`：把 `role` 是 `user` 的消息原样追加到文件里，返回回执；没有这样的消息就返回 `None`，客户端会得到 `skipped`。消息的 `time` 以 `YYYY-MM-DD` 开头时，这个日期作为记忆的日期，召回结果里的 `date` 就是它。
- 启动时把整个文件读进内存；最后一行写了一半（进程中途崩溃）时，读不出来的行直接跳过，日志里也不记内容。
- 写文件失败抛 `BackendError`，信息里只有错误类型，没有记忆文字；网关据此回 502，召回时把这个存储记为这次没查成。

## 它没有做什么

这是教学用的最小例子，不是能用在正式环境的存储：

- 不能删除，也不列出全部记忆（没有 `delete`、`refs`），所以不能配 `[forgetting]` 遗忘曲线；也不提供 `put`，`feiyu-memgate eval` 不能往里灌数据。
- 每次召回都扫一遍全部记忆，条数多了会变慢；全部记忆都在内存里。
- 分数只数词：没有按词的稀有程度加权，英文不去掉 `the`、`do` 这样的常见词，中文只按单个汉字，也不懂近义词。分数线 0.5 / 0.4 是随手定的，没有在数据上量过。
- 只有同一个进程里的线程安全；两个进程同时写同一个文件会互相看不到，也可能写乱。
- 不去重：同一句话存两次就是两条（召回时的 `dedupe` 步骤只在返回时去掉近似重复的）。
- 不加密，文件是明文；不记录写入之后的下场（`tracks_writes`）。
- 不处理 `ctl` 和 `timeout`：它不开网络连接，也没有会跑很久的操作。

要看一个处理了这些的实现，读 [src/feiyu_memgate/backends/sqlite.py](../../src/feiyu_memgate/backends/sqlite.py)。怎么给自己的后端写测试见 [docs/pieces.md](../../docs/pieces.md) 的“给自己的拼图写测试”；这个例子的测试在 [tests/test_examples.py](../../tests/test_examples.py)。
