#!/bin/bash
# 启动 Agent Handoff HTTP 服务（本地单机）
# 可选环境变量：
#   MEMORY_LLM_BASE_URL / MEMORY_LLM_API_KEY / MEMORY_LLM_MODEL  配置后总结管道生效
#   MEMORY_PORT  默认 8787
cd "$(dirname "$0")"
PORT="${HANDOFF_PORT:-${MEMORY_PORT:-8787}}"
PY="${HANDOFF_PYTHON:-${MEMORY_PYTHON:-../.venv/bin/python}}"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
