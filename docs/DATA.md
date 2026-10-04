# 数据、升级与保留

## 位置与兼容

默认 SQLite 路径是 `$XDG_DATA_HOME/agent-handoff/memory.db`；未设置 XDG 时为
`~/.local/share/agent-handoff/memory.db`。名称 `memory.db` 保留，避免旧安装升级后找不到数据。
配置读取顺序为：数据库文件先看 `HANDOFF_DB_PATH`，否则 `MEMORY_DB_PATH`；
若都未设置，则目录先看 `HANDOFF_DATA_DIR`，否则 `MEMORY_DATA_DIR`，最后是默认目录。
显式数据库文件路径优先于目录配置。CLI、MCP 如要共享数据，必须解析到同一文件。

SQLite `PRAGMA user_version` 当前为 2。启动时会把早期 `user_version=0/1` 的数据库在事务中
升级为 2，新增核验与检查记录表，保留交接 ID、内容和事件；看到比程序更高的版本会拒绝启动，不尝试降级。
升级旧数据前，可在仍运行旧程序时先执行 SQLite 在线备份，或停止所有进程后复制数据库及关联 WAL 文件。
新 CLI 的 `backup` 命令不先打开应用存储，可在升级前使用。

## 备份、导出与恢复

```bash
agent-handoff backup backups/2026-10-04.db
agent-handoff export > handoffs.json
agent-handoff schema > handoff.schema.json
```

`backup` 使用 SQLite backup API 包含已提交的 WAL 数据，拒绝覆盖现有目标，也拒绝目标使用源库
及其 `-wal` / `-shm` / `-journal` 路径。备份是可供 SQLite 直接打开的数据库文件。
恢复前应停止所有使用原数据库的进程，在安全位置保留现有数据库及侧文件，然后把备份复制到目标路径；
恢复后的应用使用该路径重新启动。请先在临时目录验证备份的 `PRAGMA integrity_check` 与交接查询。

`export` 向 stdout 输出 JSON 快照，`kind=agent-handoff-export`、`schema_version=1`、
`handoffs`、`events`，以及 `handoff_verifications`、`handoff_check_runs`。它没有导入功能，不能代替 SQLite 可恢复备份。
导出不会自动刷新包的 TTL 状态；状态是导出事务开始时数据库已记录的值。
导出可能包含任务描述、绝对路径和验证细节；默认终端重定向权限受 shell 的 umask 控制，
应存放在只有当前用户可读的目录并按私有数据处理。

## 清理

```bash
agent-handoff purge --older-than-days 30
agent-handoff backup backups/before-purge.db
agent-handoff purge --older-than-days 30 --apply
```

无 `--apply` 只预览 ID 和数量。实际清理只删除 `updated_at` 早于阈值、
状态已落库为 `completed`、`rejected` 或 `expired` 的包及其关联交接事件。
`pending`、`claimed`、`accepted` 不清理。自然 TTL 到期状态需经过读取/列出/操作刷新后才成为
`expired`；这次状态更新时间也是清理计时起点。包和关联事件在同一事务删除，
另写入不含被删 ID 的聚合 `handoff.purged` 事件。
SQLite 删除不等同于安全擦除磁盘上的旧页、WAL 或备份；需要物理级删除时应按部署环境处理。

包的核验记录随包删除。独立检查记录以及旧记忆、消息和总结 API 的记录不随 handoff purge 删除；
检查记录可能被多个交接引用，目前保留在库中，备份和导出包含这些记录。
新数据库只创建交接包、事件、核验记录和检查记录表，不创建旧记忆/消息/全文检索表。
已有数据库的旧表与数据保持原样，但不再提供其业务接口；路径兼容变量仍保留。
