# Contributing

欢迎中文或英文 issue / pull request。当前目标是让同一可信本机用户下的编程 agent 可靠交接任务。
新功能请先说明实际失败场景；旧记忆模块以兼容和缺陷维护为主。

## 开发环境

Python 3.10+、Git；Linux/macOS。仓库根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,mcp,dev]'
python scripts/run_handoff_tests.py --require-mcp --output reports/local/tests.json
python scripts/smoke_test.py
python scripts/demo_handoff.py --output reports/local/demo
ruff check server scripts tests
python -m build
python -m twine check dist/*
python scripts/check_wheel.py dist/*.whl
```

演示输出目录必须不存在，可换一个新目录再次运行。完整专项测试缺少 MCP 依赖时会直接失败。
测试使用临时数据库，不需要 API key，也不会调用真实 LLM。配置了 `HANDOFF_*` 的开发者应使用测试入口脚本，避免直接导入应用访问自己的数据。

仓库提供 `uv.lock`，复现 CI 依赖可用 `uv sync --locked --extra test --extra mcp --extra dev`。
`pyproject.toml` 是依赖声明的唯一来源；`server/requirements.txt` 仅保留兼容入口。

## 代码布局

- `server/app/storage/handoff_store.py`：交接状态与事件事务。
- `server/app/handoff_artifacts.py`：工作区文件摘要、Git HEAD、Markdown。
- `server/app/models.py`：交接 JSON Schema 与旧 API 模型。
- `server/app/handoff_cli.py`、`handoff_mcp.py`、`routers/handoff.py`：三个入口。
- `server/app/maintenance.py`：导出、备份、明确请求的历史清理。
- `tests/`：真实 SQLite、临时文件/Git 仓库、HTTP、子进程 CLI/MCP 回归。

## 提交修改

1. 描述触发问题、预期行为和改动范围。
2. 对状态机、存储、权限边界或协议的改动补回归；文案改动不要求机械测试。
3. 运行相关检查；涉及公共入口时运行完整套件和安装包检查。
4. 更新涉及的文档及 CHANGELOG 的 Unreleased 项。
5. PR 说明实际验证结果和未验证范围，不上传真实用户数据库、密钥或私人交接内容。

格式版本与 API 兼容要求见 `docs/PROTOCOL.md`；数据库升级要求见 `docs/DATA.md`。
维护者负责范围与版本决定。小型独立贡献优先，不要求签署额外 CLA；贡献按仓库 MIT 许可证提供。
讨论聚焦代码与可复现证据，尊重参与者，不发布他人私人信息。
