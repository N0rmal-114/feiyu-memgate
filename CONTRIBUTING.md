# 参与贡献

欢迎提 issue 和 PR。最受欢迎的贡献是**新的拼图**：新的存储后端、新的召回步骤、新的写入守卫、新的接入示例。怎么写一块拼图见 [docs/pieces.md](docs/pieces.md)，还没做的方向见 [docs/roadmap.md](docs/roadmap.md)。

## 开发

需要 Python 3.11 或更高。不需要安装任何依赖就能跑全部测试：

```bash
python -m unittest discover -s tests -t .
```

备份加密的测试需要 `cryptography`（`pip install cryptography`），没装时会自动跳过。

- 仓库的结构、插件的测试和生成的部分、CI、发版时能看到的步骤：[docs/development.md](docs/development.md)。
- 在 feiyu-memgate 之上做自己的东西（接进聊天程序、用别的语言接入、发布成包）：[docs/extending.md](docs/extending.md)。

## 几条约定

- **网关、客户端和内置拼图只用标准库。** 网关拿着所有存储的密钥，依赖越少越好。需要第三方库的拼图请做成单独的包，通过入口点注册（见 pieces.md）。
- **日志和错误信息里不能有内容。** 不能出现查询词、记忆文字、令牌、密钥、租户编号。只写“哪一种失败”。
- **不截断。** 太长的记忆或消息整条拒绝或整条不放，不要切一半。
- **失败要分得清。** “没有”和“没查成”、“确定没存”和“不确定”要能区分，不要把失败当成空结果返回。
- **测试不碰外部服务。** 假服务绑在 `127.0.0.1` 的随机端口上；测试数据全部虚构，不能有真实的密钥、账号、群号和个人信息。
- **注释写原因。** 代码注释和文档字符串用英文，说明为什么这么做；面向用户的文档和提示用中文。

## 提交 PR 之前

- 全部测试通过。
- 新的行为有测试；修 bug 先写一个能复现它的测试。
- 新的拼图在 `docs/pieces.md` 的表格里加一行，新的设置在 `docs/configuration.md` 里加一行。

## 安全问题

请不要在公开的 issue 里贴漏洞细节，见 [docs/security.md](docs/security.md)。
