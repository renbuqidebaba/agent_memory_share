# 交接格式与状态协议 v1 / v2

输入格式见 [JSON Schema](handoff.schema.json)，示例见 [handoff.json](../examples/handoff.json)。
`schema_version` 支持整数 `1`、`2`，省略时视为 v1，以读取早期本地包。创建时其他值会被拒绝；
核验未知版本返回失败。字段 `task_id`、`from_agent`、`goal`、`status_summary` 必填且不能只有空白。

`done_when` 是完成标准；`completed`、`decisions`、`next_steps`、`open_questions` 记录接手必需上下文。
`validation` 的 `command`、`outcome`（`passed` / `failed` / `not_run`）、`detail` 是发送方声明。
系统保存但**不会执行**这些命令；接收方需自己复验。

`artifacts[].path` 应为工作区内相对路径。CLI/MCP 创建时解析路径，拒绝绝对路径和越界符号链接，
读取文件写入 `sha256`，并在 Git 仓库内记录 HEAD 为 `source_revision`。
HTTP 创建不自动读取工作区，允许客户端提交没有哈希的引用；`verify` 会将缺少预期哈希判为失败。
`workspace_root` 是创建时的本机路径提示，不携带文件内容。移动到另一机器之前需要另行同步同一代码和产物。

## 生命周期

```text
pending --claim--> claimed --ack(accept)--> accepted --complete--> completed
                       |
                       +--ack(reject, reason)--> rejected
pending/claimed --TTL 到期--> expired
claimed --租约到期后重新 claim--> claimed
```

`rejected`、`completed`、`expired` 是终态。租约仅限制尚未 ack 的领取；已接受任务不因包 TTL 到期而自动终止。
同一有效领取者重试 claim 不延长租约；不同领取者在租约有效时会冲突。指定 `to_agent` 的包只能由该标识领取。
这里的 `agent_id` / `to_agent` 仅是客户端自报标签，不是认证。
状态变化和事件写入在同一 SQLite 事务内；自然到期状态在读/列出或领取/确认时刷新。
`list` 最多返回最近 100 个包，可按 task/status 筛选；`export` 导出全部包及交接事件。

CLI 的 `verify` 检查当前 Git HEAD（若创建时有记录）和列出文件的 SHA-256。
返回 `ok=true` 仅说明这些机械检查通过，不意味着测试正确、未列出的工作区文件一致，
也不阻止调用者之后改动文件。旧 v1 包保持原接受方式；`capture` 生成 v2 包并要求
`require_verification=true`，接受时必须引用当前领取者的成功核验记录，并再次检查相关现场。
自动采集范围、核验记录和实测检查见 [v2 使用说明](EVOLUTION.md)。

## 兼容规则

读取缺失 `schema_version` 的旧包时按 v1 解释，不改写原始数据库内容。
向 v1 添加必须理解的新语义时应提高版本，并补迁移或明确拒绝规则。
数据库版本独立于包版本，见 [数据维护](DATA.md)。
