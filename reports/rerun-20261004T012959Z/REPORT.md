# 测试重跑报告

- 完成时间：2026-10-04T09:30:13+08:00（Asia/Shanghai）。
- 结论：56/56 通过，失败 0，跳过 0；进程退出码 0；耗时 12.468 秒。
- 本次仅重跑既有完整测试并保存报告，未修改业务代码或测试用例。

## 执行与环境

```bash
.venv/bin/python scripts/run_handoff_tests.py --require-mcp --output reports/rerun-20261004T012959Z/results.json
```

- Python：3.12.14 (main, Sep 29 2026, 14:49:50) [Clang 22.1.3 ]。
- 系统：macOS-14.2.1-x86_64-i386-64bit；SQLite：3.53.1。
- 依赖版本：`{"fastapi": "0.142.2", "pydantic": "2.13.5", "httpx": "0.28.1", "httpx2": "2.13.1", "mcp": "2.3.0"}`。
- 依赖检查：`uv pip check --python .venv/bin/python`，退出码 0；57 个已安装包兼容。

## 分项结果

| 范围 | 执行 | 通过 |
|---|---:|---:|
| HTTP API（TestClient） | 10 | 10 |
| 文件与 Git 证据 | 14 | 14 |
| CLI 子进程 | 5 | 5 |
| MCP SDK / stdio 子进程 | 5 | 5 |
| 迁移、备份、导出与清理 | 8 | 8 |
| 状态机与并发 | 14 | 14 |

## 结果核对

- 测试运行器自动发现 tests/test_*.py；静态审查确认预期 56 项，实际记录 56 个唯一测试 ID，全部通过，无跳过。
- `--require-mcp` 强制要求 MCP 依赖；5 项 MCP 测试通过官方 SDK 启动独立 stdio 服务，未使用模拟 MCP 服务。
- 测试入口使用临时数据库并清除 HANDOFF_* 路径覆盖；单项测试和 CLI/MCP 子进程各自使用隔离数据。
- 测试前后对 36 个源码、测试、脚本及依赖文件计算 SHA-256，全部一致。工作区有未提交文件；Git HEAD `e14be0890417feed50be4afc5d6dc757421831e0` 仅作基线标记，实际测试内容以 source-before.json 为准。
- 逐项 JSON 结果、退出码与文本日志的 `Ran 56 tests` / `OK` 已相互核对。

## 结论边界

本次验证当前环境下既有自动化套件；不包含真实大模型跨会话接手、具体 MCP 产品客户端、跨平台兼容或性能压测。并发用例验证正确性，不代表吞吐量评估。

## 原始证据

- [逐项测试结果](results.json)
- [测试日志](results.log)
- [控制台输出](console.log)
- [执行命令与退出码](execution.json)
- [测试前文件摘要](source-before.json)
- [测试后文件摘要](source-after.json)
