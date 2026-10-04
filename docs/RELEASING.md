# 本地发布候选检查

当前阶段只准备源码与分发包；用户暂不要求处理远程仓库或发布渠道。
从仓库根目录、干净开发环境执行：

```bash
python -m pip install -e '.[test,mcp,dev]'
python scripts/run_handoff_tests.py --require-mcp --output reports/local/tests.json
python scripts/smoke_test.py
ruff check server scripts tests
python scripts/demo_handoff.py --output reports/local/demo
python -m build
python -m twine check --strict dist/*
python scripts/check_wheel.py dist/*.whl --output reports/local/wheel.json
```

若要复现锁定依赖，改用 `uv sync --locked --all-extras` 进入本地虚拟环境；
可由 `uv export --frozen --all-extras --no-emit-project --no-hashes --format requirements-txt`
生成 wheel 检查所需 constraints。
演示目录每次使用新的不存在路径。检查 wheel 后核对安装源位于干净虚拟环境。
本地报告见 [准备检查](../reports/OPEN_SOURCE_READINESS.md)。

对外发布之前，再由项目维护者确认目标仓库、许可证署名、私密漏洞报告入口、CI 在目标平台的
实际结果、包名占用、版本标签与变更日志。当前尚未执行这些远程步骤。
