# dsh-feiyu-memgate 更新记录

这是 DSH 插件 `dsh-feiyu-memgate` 的版本记录。网关本身的改动在仓库根目录的 [CHANGELOG.md](https://github.com/N0rmal-114/feiyu-memgate/blob/main/CHANGELOG.md)；附加插件 `dsh-feiyu-memgate-embed` 目前只有 0.1.0 一个版本。

每个版本下，“默认行为的变化”列出升级后不用改设置就会不一样的地方，每一项都写了怎么改回去。

## 0.3.1

日期：2026-11-17。自带网关为提交 `1a4384c`。

聊天体验的第一轮改进，设计说明见 [docs/design/2026-11-17-chat-experience.md](https://github.com/N0rmal-114/feiyu-memgate/blob/main/docs/design/2026-11-17-chat-experience.md)。

### 默认行为的变化

- **本会话里给过的记忆不再重复。** 对话历史里还留着的记忆块已经出现过的记忆，这一轮不再放一次；全部都给过时不加记忆块。以前只和上一个记忆块整块比较。改回去：`advanced.skipRepeats` 设为关。
- **记忆块写明今天的日期，每条记忆带上多久以前。** 例如“（记忆，2026-10-27，3 周前）”。`memgate_search` 返回的块也一样。改回去：`advanced.showAge` 设为关。
- **召回失败时告诉模型。** 查询超时、出错，存储失败或繁忙而没有结果，或者自带网关还在启动时，在用户消息之后加一行说明；只在连续失败的第一轮加。改回去：`advanced.failureNote` 设为关。
- **自动记录跳过无关紧要的消息。** 打开 `modules.autoCapture` 时，聊天命令、只有语气词的消息和不足 4 个字的消息不保存。长度门槛用 `advanced.captureMinChars` 调，设为 0 就不看长度。

### 新增

- 保存的记忆带上本机时区的当前时间，记忆的日期是用户所在地的日期（以前是 UTC 日期，早上记的事可能算成前一天）。
- 系统提示词里的说明有中文和英文两个版本，跟随语言设置；说明和 `memgate_remember` 的描述补充了三点要求：写成单独看也能懂的一句话，时间写成日期，事情变了要说明取代了什么。
- 四项新设置：`advanced.skipRepeats`、`advanced.showAge`、`advanced.failureNote`、`advanced.captureMinChars`。

### 没有验证的

这些改动由单元测试和模拟宿主的测试覆盖，没有在和真实模型的聊天里验证过；“今天的日期和多久以前”对回答质量的影响没有测量。

## 0.3.0

日期：2026-11-17。自带网关为提交 `403931f`（没有变）。

### 新增

- 可选的混合检索 `retrieval.hybrid`（默认关闭）：在向量检索（`endpoint`、`local`，以及 `auto` 选到 `local` 时）下同时做关键词检索并合并结果。向量存储文件不变，旁边多一个关键词索引文件；每次网关启动之前，插件先让索引和向量文件一致，所以开或关都不会丢记忆。
- `/memgate status`、`/memgate tiers` 和设置页面显示混合检索是否在用。

没有改任何默认值。

## 0.2.0

日期：2026-11-16。自带网关为提交 `403931f`。

### 默认行为的变化

- **召回档位的默认值从 `precise` 改成 `broad`。** 每轮给的记忆更多（向量检索下每个问题约 2.5 条，以前约 1 条），漏得少，夹带的无关内容也多，上下文涨得更快。改回去：`advanced.profile` 设为 `precise`。

## 0.1.1

日期：2026-11-15。

### 修好的

- 在设置页面安装向量插件时，拒绝依赖的安装脚本之后，同一条提示显示了两次。

## 0.1.0

日期：2026-11-15。

第一个版本：自带并自动启动网关，每轮自动召回，`memgate_remember` 和 `memgate_search` 两个工具，`/memgate` 聊天命令，设置页面，关键词、向量接口和本地模型三种检索方式。
