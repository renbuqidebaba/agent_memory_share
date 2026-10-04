# 自动采集与核验记录（交接格式 v2）

旧 `create` / `handoff_create` 继续创建 v1 包。推荐使用 CLI `create --capture` 或 MCP
`handoff_capture` 创建 v2 包：v2 必须有当前领取者的成功核验记录才能接受。
旧程序会拒绝不认识的 v2 包；数据库从版本 0/1 事务升级到 2，不重写旧交接内容。

## 一次完整操作

以下命令在已经激活的安装环境中运行；`/path/to/repo` 是 Git 仓库根目录。
非 Git 目录必须通过 artifacts 或 `--include` 指定至少一个实际文件。

```bash
# 可选：执行明确选定的验收命令，保存真实退出码与有限日志。
agent-handoff run-check --workspace /path/to/repo -- python -m unittest discover -v

# payload.json 填目标、进度等；CHECK_ID 来自上一步。
# 新增未跟踪文件需用 artifacts 或可重复的 --include 明确纳入。
agent-handoff create payload.json --workspace /path/to/repo --capture --check-id CHECK_ID

agent-handoff claim HANDOFF_ID --agent agent-b
agent-handoff verify HANDOFF_ID --workspace /path/to/repo --agent agent-b
agent-handoff ack HANDOFF_ID --agent agent-b --accept --verification-id VERIFICATION_ID

# 接受后继续工作，独立执行完成检查，任务确实完成才关闭。
agent-handoff complete HANDOFF_ID --agent agent-b
agent-handoff verifications HANDOFF_ID
```

检查失败时 `run-check` 退出码为 2，仍保存真实失败记录；可以将失败记录纳入交接包。
命令无法启动、参数错误等为退出码 1。检查日志中的命令不会自动执行；运行器只执行本次明确给定的 argv，
不使用 shell。MCP 对应工具为 `handoff_run_check` / `handoff_capture` / `handoff_verify` / `handoff_ack`。
`handoff_verify` 传 `agent_id` 保存核验记录；省略则只预览，不能用于接受 v2 包。

## 记录内容与范围

- `workspace_snapshot`：Git HEAD、分支、当前跟踪文件改动列表、文件 SHA-256 与可执行位、所选依赖文件摘要、diff。
- `observed_checks`：服务实际执行的 argv、退出码、超时状态、日志、时间，以及执行前的文件证据。
- `validation` 和任务描述：发送者的声明；它们不会升级为工具实测结果。
- v2 包接受时引用 `verification_id`。记录绑定任务、领取者、领取租约、交接包内容摘要及核验工作区。
- 接受时再次检查相关工作区；文件变化、核验失败、跨任务引用或租约重领后的旧记录均被拒绝。
- `handoff_get` 返回核验记录；CLI `verifications` 与 HTTP `GET /v1/handoffs/{id}/verifications` 可查看历史。
- 列表新增接收者和工作区筛选；接收者筛选也包括未指定接收者的包，最多返回 100 项。

检查运行后修改了其核验范围内的代码，或检查命令自己修改了这些代码，该记录不能附到新交接包；应重新运行检查。
服务不会接收调用方伪造的 `workspace_snapshot` / `observed_checks`，这些字段由采集入口生成。
这仍是同一可信本机用户的证据记录，不提供远程身份认证或抵抗数据库所有者篡改的签名。

## 采集限制

Git 仓库要求传根目录。自动纳入跟踪文件改动和根目录常用依赖清单；未跟踪文件必须明确选择。
自动采集拒绝符号链接和越界路径，跳过 `.env`、常见密钥文件、`.git` 等路径；排除项和未纳入项会列出。
这些规则不能识别所有写在普通源码中的秘密，发送方仍需检查实际 diff 和日志。
默认上限为 200 个文件、每文件 10 MiB、diff 128 KiB、每个日志流 64 KiB；截断会明确标记。
Git 操作超时 15 秒，检查命令超时范围 1–600 秒。

包中现在可能包含所选改动的 diff 和检查日志，完整文件仍留在工作区。采集进程的 Python/系统信息
标为 `capture_runtime`，不代表目标应用实际使用的运行环境。
文件系统没有与 SQLite 共用事务；接受前复核能发现已发生的变化，不会锁住之后的工作区修改。
文件一致不证明代码正确；`complete` 仍是接收方声明，应在实际验收后调用。

## 接入体验

MCP 暴露 `prepare_handoff(workspace, task_id)` 和 `resume_handoff(handoff_id, workspace, agent_id)`
两个 Prompt，客户端支持时可显式调用。它们提供操作步骤，不会自行获取会话历史或启动其他 Agent。
使用同一数据库的客户端可通过任务/工作区/接收者筛选交接列表。

真实效果实验准备、运行与统计见 [EVALUATION.md](EVALUATION.md)。
