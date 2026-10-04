# 自动采集与核验记录：实现和验证报告

本轮实现工作现场自动采集、实际检查记录、v2 强制核验接受、MCP 操作模板及独立实验准备。
**自动化测试 81/81 通过，失败 0，跳过 0。真实 Agent 效果试验完成数为 0。**

## 交付能力

- CLI `create --capture` / MCP `handoff_capture` 生成 v2 包，采集 Git 改动、有限 diff、文件/依赖摘要与可执行位。
- `run-check` / `handoff_run_check` 执行明确给定的 argv，保存真实退出码、耗时、有限日志与执行前现场；不会自动执行包内声明的命令。
- `verify --agent` / `handoff_verify(agent_id=...)` 保存与任务、领取者、租约、包摘要、工作区绑定的核验记录。
- v2 接受必须引用成功记录，并再次检查现场；旧 v1 行为保留，旧程序无法将 v2 当作 v1 接受。
- 数据库升级到版本 2，核验/检查记录进入导出和备份；核验记录随包清理。
- 新增两个 MCP Prompt、接收者/工作区筛选，以及三组独立试验目录准备器和双方总成本统计。

## 测试结果

执行命令：

```bash
.venv/bin/python scripts/run_handoff_tests.py --require-mcp --output reports/evolution-20261004/results.json
```

环境：Python 3.12.14，macOS-14.2.1-x86_64-i386-64bit，SQLite 3.53.1。
依赖：`{"fastapi": "0.142.2", "pydantic": "2.13.5", "httpx": "0.28.1", "httpx2": "2.13.1", "mcp": "2.3.0"}`。耗时 67.335 秒。

| 分类 | 执行 | 通过 |
|---|---:|---:|
| EvidenceTests | 18 | 18 |
| ApiTests | 10 | 10 |
| ArtifactTests | 14 | 14 |
| CliTests | 6 | 6 |
| StoreTests | 14 | 14 |
| McpTests | 7 | 7 |
| ReleaseTests | 9 | 9 |
| ScriptTests | 3 | 3 |

核对了实际测试 ID、逐项结果、日志和退出状态。强制安装 MCP，测试未跳过。
新增覆盖包括：暂存/未暂存改动、删除和显式未跟踪文件、敏感路径与链接、Git 环境重定向、
失败检查/超时/日志截断、过期检查、伪造观测字段、跨任务/错误接收者/旧租约记录、
核验后修改、事务回滚、v1→v2 数据库迁移、HTTP/CLI/MCP 流程、Prompt 发现及未运行实验拒绝统计。

## 其他验证

- `ruff check .`：通过。
- 57 个已安装包的依赖兼容性检查：通过。
- 旧记忆功能冒烟：21/21，通过。
- 确定性演示：发送方检查退出 1，接收方检查退出 0，交接状态 completed，含核验 ID；未使用大模型。
- wheel / sdist 构建及 `twine check --strict`：通过。
- 临时干净虚拟环境安装 wheel，工作目录在源码目录之外：CLI、数据维护、本机 HTTP、MCP v1/v2 流程、9 工具及 2 Prompt 均通过，安装包导入来自该 venv 的 site-packages。
- 测试前后及报告生成时，对 42 个源码、测试、脚本及依赖声明文件的 SHA-256 比较一致。文档和 CI 不包含在这份源码摘要中。

首次隔离安装因沙箱无法连接依赖仓库而失败，保留 `wheel.log`；随后允许该次依赖下载及本机回环检查，验收通过，见 `wheel-verified.json`。
本地初次无隔离构建发现缺少 setuptools，随后使用已有 setuptools 84.0.0 完成构建；CI 已改为使用声明的隔离构建依赖。远程 CI 和跨平台矩阵本轮未执行。

## 真实实验进度

`reports/local/evolution-trials/manifest.json` 已生成一个任务、三种条件、各两次，共 6 个独立起点。
各组初始文件摘要一致，工作区和交接组数据库独立；记录标记为 not_run。
准备器和演示均不会产生真实 Agent 效果结论。独立 Agent 运行方式尚待用户确认；不能把 81 项自动化通过解释为接手效率提升。

## 使用与边界

见 [v2 使用说明](../../docs/EVOLUTION.md) 和 [实验协议](../../docs/EVALUATION.md)。
当前仍为同一可信本机用户；没有远程认证、自动唤醒或文件同步。
采集有大小限制、显式排除项，未跟踪文件需明确选择。核验保证所记录范围内的文件证据一致，
不保证代码正确，也不会锁住后续文件修改；complete 仍由接收方在实际验收后声明。

## 原始证据

- [逐项结果](results.json)、[测试日志](results.log)、[汇总及安装包摘要](summary.json)
- [安装包验收](wheel-verified.json)、[安装包日志](wheel-verified.log)
- [旧功能回归](legacy-smoke.log)、[演示输出](demo.log)
- [源码摘要（测试前）](source-before.json)、[源码摘要（测试后）](source-after.json)
