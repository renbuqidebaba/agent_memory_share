"""集中配置：全部走环境变量，默认值适配本地单机运行。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# 安装后 site-packages 通常不可写，默认使用用户数据目录。
USER_DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "agent-handoff"
DATA_DIR = Path(os.environ.get("HANDOFF_DATA_DIR", os.environ.get("MEMORY_DATA_DIR", USER_DATA_DIR))).expanduser()
# Keep the existing filename and legacy variables so upgrades find existing data.
DB_PATH = Path(os.environ.get("HANDOFF_DB_PATH", os.environ.get("MEMORY_DB_PATH", DATA_DIR / "memory.db"))).expanduser()

# LLM provider：fake（内置规则模型，测试/演示用，零成本）| openai 兼容接口 | 不配置则总结管道跳过
LLM_PROVIDER = os.environ.get("MEMORY_LLM_PROVIDER", "")  # fake / openai / 空
LLM_BASE_URL = os.environ.get("MEMORY_LLM_BASE_URL", "")  # e.g. https://api.openai.com/v1 或 http://localhost:11434/v1
LLM_API_KEY = os.environ.get("MEMORY_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("MEMORY_LLM_MODEL", "gpt-4o-mini")
LLM_EMBED_MODEL = os.environ.get("MEMORY_LLM_EMBED_MODEL", "text-embedding-3-small")

# 总结管道：攒够多少条未处理消息才允许触发
SUMMARY_MIN_MESSAGES = int(os.environ.get("MEMORY_SUMMARY_MIN_MESSAGES", "4"))
