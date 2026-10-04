> 历史验证记录：本文对应移除 HTTP/旧记忆服务之前的版本。当前架构与验证见 [接口精简报告](interface-cleanup-20261004/REPORT.md)。

# Agent Handoff 本地开源准备检查

此文件记录 0.1.0a1 候选在当前工作区的实际验证。旧的 `TEST_REPORT.md` 对应此前的本地 MVP 快照，计数不能作为本版结果引用。

## 已完成的本地验证

| 项目 | 结果 | 证据/重跑命令 |
|---|---|---|
| 完整专项回归（状态机、并发、HTTP、CLI、MCP、数据维护、脚本边界） | 60/60 通过，0 跳过 | `.venv/bin/python scripts/run_handoff_tests.py --require-mcp --output reports/local/release-tests.json` |
| 旧记忆 API 回归 | 21/21 通过 | `.venv/bin/python scripts/smoke_test.py` |
| 原交接和 MCP 冒烟脚本 | 均通过 | `.venv/bin/python scripts/handoff_test.py`；`.venv/bin/python scripts/mcp_test.py` |
| 静态检查与依赖锁一致性 | 通过 | `.venv/bin/ruff check server scripts tests`；`uv lock --check` |
| 确定性编码交接演示 | 发送方留下失败测试；接收方核验后修复并通过测试，状态 `completed` | `.venv/bin/python scripts/demo_handoff.py --output reports/local/demo` |
| wheel/sdist 元数据 | 构建与 `twine check` 通过 | `.venv/bin/python -m build`；`.venv/bin/python -m twine check --strict dist/*` |
| 干净虚拟环境中的 wheel | CLI 完整流程、导出、SQLite 备份、HTTP 回环健康检查、官方 MCP 客户端七个工具完整流程通过；导入来自 venv 的 site-packages | `.venv/bin/python scripts/check_wheel.py dist/*.whl --constraints reports/local/locked-requirements.txt --output reports/local/wheel.json` |
| Codex MCP 配置格式 | 通过本机 Codex CLI `mcp get` 解析 | 见 `docs/INTEGRATIONS.md`；未运行真实 Codex agent 会话 |

本地平台为 macOS Intel、Python 3.12。CI 文件配置了 Linux 3.10/3.12/3.14 与 macOS 3.12；当前没有远程仓库，因此这些矩阵作业尚未实际运行。
报告中的本地路径及生成日志留在忽略的 `reports/local/`；不要将真实用户数据库和交接内容加入发布包。

## 审查后修复

- 演示脚本清除继承的 `GIT_*`，禁用全局 Git 配置、模板钩子及提交签名，避免误写入外部仓库。回归用真实外部仓库和索引路径验证。
- 效果实验汇总拒绝 `NaN` 与无穷大。缺失 token 数据以空值保留。
- SQLite 备份拒绝以源数据库及其 `-wal`、`-shm`、`-journal` 路径为目标，防止报告成功后文件被 SQLite 清理。

## 尚待实际使用验证

- 三组真实 agent 接手实验尚未执行；脚本演示只能证明流程可运行，不能证明效率提升。见 `docs/EVALUATION.md`。
- 尚未在实际 Codex 客户端会话中验证本项目 MCP 工具的自然语言调用与交接体验。
- 当前不支持远程共享认证、跨用户隔离和文件产物传输。Windows 未验证。
- 目标仓库、线上 CI 和发布动作尚未处理；本阶段只完成本地发布候选。
