# MCP / CLI 接口精简验证报告

验证完成时间：2026-10-05（Asia/Shanghai）。目录按本次工作开始日期命名。
被测版本：0.1.0a1 的当前未提交工作区；本报告不代表已发布版本。

## 交付范围

保留两个入口：`agent-handoff` CLI 和 `agent-handoff-mcp`（stdio）。二者直接调用同一套交接核心与 SQLite 存储。

- 删除 FastAPI 应用、全部 HTTP 路由和 HTTP 启动脚本。
- 删除旧记忆 CRUD、消息收集、LLM 总结及其配置和存储实现。
- 删除旧 HTTP 测试，将仍有价值的参数、状态、权限和查询验证迁移至核心测试。
- 同步精简依赖、锁文件、CI、使用文档和示例。
- 保留交接状态机、领取租约、工作区快照、检查记录、核验回执、备份、导出及清理。

基础安装直接依赖仅为 Pydantic；MCP 为可选依赖。MCP SDK 自身仍依赖部分 HTTP 库，但本项目只运行 stdio，不提供 HTTP 服务或网络监听入口。

## 数据兼容

新数据库只创建 `handoffs`、`events`、`handoff_verifications` 和 `handoff_check_runs` 四张业务表，不创建旧记忆、消息或 FTS 表，也不要求 FTS 扩展。

升级不删除旧数据库中的历史表或数据。继续识别原有 `memory.db` 文件名及 `MEMORY_DATA_DIR` / `MEMORY_DB_PATH` 路径变量，避免已有交接数据失联；这些兼容项不提供旧服务接口。

## 实际验证结果

环境：macOS 14.2.1 Intel、Python 3.12.14、SQLite 3.53.1、Pydantic 2.13.5、MCP / mcp-types 2.3.0。

| 检查 | 结果 | 证据 |
|---|---|---|
| 完整回归（核心、CLI、MCP、证据、数据维护） | 83/83 通过，0 失败，0 跳过，41.958 秒 | [结构化结果](final-results.json)、[控制台日志](final-console.log) |
| 基础 wheel 安装 | 独立虚拟环境、源码目录外运行 CLI 完整生命周期及导出、备份、清理预览；没有 MCP、FastAPI、uvicorn | [安装验证](wheel.json) |
| wheel + MCP 安装 | 9 个工具和 2 个提示词发现成功；v1 生命周期及 v2 检查、采集、回执接受流程通过；`pip check` 通过 | [安装日志](wheel.log) |
| 安装包内容 | wheel 不含已删除的 HTTP、LLM、总结或旧存储模块 | `scripts/check_wheel.py` 的归档断言 |
| 数据兼容和依赖隔离 | 数据库版本 0/1/2 的交接记录和模拟旧数据保留；禁止虚拟表创建时可初始化；禁止导入 Web/MCP 库时 CLI 和核心正常 | `tests/test_core.py`，包含在上述 83 项中 |
| 静态检查及打包 | Ruff、`git diff --check`、wheel / sdist 构建和 `twine check --strict` 通过 | [构建日志](build.log) |

首次回归出现 1 项测试失败：新增导入拦截器把 `app.maintenance` 误判为 `app.main`。已将判断改为完整模块名或其子模块匹配；最终完整复跑 83 项全部通过。首次结果保留于 [results.json](results.json) 和 [console.log](console.log)。

## 重跑

```sh
uv sync --locked --all-extras
uv run --no-sync ruff check .
uv run --no-sync python scripts/run_handoff_tests.py --require-mcp --output reports/local/tests.json
uv run --no-sync python -m build
uv run --no-sync python -m twine check --strict dist/*
uv export --frozen --all-extras --no-emit-project --no-hashes --format requirements-txt --output-file reports/local/locked-requirements.txt
uv run --no-sync python scripts/check_wheel.py dist/*.whl --constraints reports/local/locked-requirements.txt --output reports/local/wheel.json
```

本次结果仅覆盖上述本机环境；CI 已更新，但本报告不声称远程 CI 或真实跨客户端 Agent 会话已经通过。
