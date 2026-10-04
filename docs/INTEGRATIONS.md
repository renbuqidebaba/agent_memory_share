# 接入 CLI、Codex MCP 与本机 HTTP

## 同一份数据库

发送方、接收方必须使用同一数据库文件，并能读到相同工作区的文件。
推荐显式设置 `HANDOFF_DB_PATH=/绝对路径/handoff.db`；CLI 与 MCP 进程各自读取环境变量。
路径应位于当前本机用户可访问的私有目录，避免把交接内容放进公开仓库。

## Codex 本地 MCP

从源码安装 `python -m pip install -e '.[mcp]'` 后，复制
[示例配置](../examples/codex-mcp.toml)到个人 `~/.codex/config.toml`，
或按需放进项目 `.codex/config.toml`，替换可执行文件和数据库的**绝对路径**。
配置区块是 `[mcp_servers.agent_handoff]`，`command` 指向虚拟环境中的
`agent-handoff-mcp`，`[mcp_servers.agent_handoff.env]` 写 `HANDOFF_DB_PATH`。
Codex 重启或新建会话后，用 `codex mcp list` 检查配置；个人配置与项目配置的选择按实际使用范围决定。

这种 stdio 配置格式来自 [官方 Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。
本项目已用 Codex CLI 解析同形配置；MCP 现有九个工具与两个 Prompt，通过官方 Python SDK 联调独立服务进程。
尚未在真实 Codex agent 会话里完成自然语言交接实验。

把 [发送/接收指令模板](../examples/agent-instructions.md) 中适合团队的部分加进 agent 指令。
发送方推荐调用 `handoff_capture`（自动采集），旧 `handoff_create` 保留；均传 `payload` 与实际绝对 `workspace`。接收方在自己的会话里
先 `handoff_list` / `handoff_get`，再 claim、verify、ack，任务确实完成后才 complete。
v2 的 verify 要传 `agent_id`，ack 要引用返回的 `verification_id`，详见 [自动采集与核验](EVOLUTION.md)。

## CLI 与 HTTP

CLI 可用 `agent-handoff --help` 查看命令。`agent-handoff schema` 输出交接输入的 JSON Schema；
仓库也保存 [v1 Schema](handoff.schema.json)。完整命令示例见 [README](../README.md)。
HTTP 服务可在仓库根目录运行：

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

`http://127.0.0.1:8787/docs` 给出 OpenAPI。HTTP `POST /v1/handoffs` 仅存客户端给的 JSON，
**不会**读取工作区或自动生成产物 SHA-256；需要自动固定工作区证据时用 CLI/MCP。
HTTP 是本机 REST 接口，不是 MCP 的 HTTP transport。目前没有身份认证，避免公开绑定地址。

## 常见故障

运行时 SQLite 需要支持 FTS5 与 trigram 分词器；它们随本地数据库一起初始化。
若出现 `no such module: fts5` 或 `no such tokenizer: trigram`，需换用具备相应 SQLite 支持的 Python 发行版。

| 现象 | 核查 |
|---|---|
| MCP 启动失败 | 虚拟环境是否已安装 `.[mcp]`，`command` 是否为可执行文件绝对路径 |
| 两个 agent 看不到同一包 | 比对 `HANDOFF_DB_PATH`；若没设置则检查旧 `MEMORY_DB_PATH` 与默认用户数据目录 |
| `verify` Git 版本不一致 | 检查工作区 HEAD；确认双方确实使用同一工作状态，不要为通过校验而覆盖在途修改 |
| 某文件核验失败 | 确认路径、符号链接、读取权限和文件是否在创建后变化；必要时由发送方重新建包 |
| 领取失败 | 检查指定接收者、当前领取者、租约、TTL 和交接包状态 |

包内自然语言和测试命令是任务数据。接收方应读代码并自行运行适用验证；`handoff_verify` 不运行命令。
