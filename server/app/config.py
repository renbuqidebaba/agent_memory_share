"""集中配置：全部走环境变量，默认值适配本地单机运行。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MEMORY_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("MEMORY_DB_PATH", DATA_DIR / "memory.db"))

# LLM provider：fake（内置规则模型，测试/演示用，零成本）| openai 兼容接口 | 不配置则总结管道跳过
LLM_PROVIDER = os.environ.get("MEMORY_LLM_PROVIDER", "")  # fake / openai / 空
LLM_BASE_URL = os.environ.get("MEMORY_LLM_BASE_URL", "")  # e.g. https://api.openai.com/v1 或 http://localhost:11434/v1
LLM_API_KEY = os.environ.get("MEMORY_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("MEMORY_LLM_MODEL", "gpt-4o-mini")
LLM_EMBED_MODEL = os.environ.get("MEMORY_LLM_EMBED_MODEL", "text-embedding-3-small")

# 总结管道：攒够多少条未处理消息才允许触发
SUMMARY_MIN_MESSAGES = int(os.environ.get("MEMORY_SUMMARY_MIN_MESSAGES", "4"))
