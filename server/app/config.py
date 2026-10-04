"""集中配置：全部走环境变量，默认值适配本地单机运行。"""
import os
from pathlib import Path

# 安装后 site-packages 通常不可写，默认使用用户数据目录。
USER_DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "agent-handoff"
DATA_DIR = Path(os.environ.get("HANDOFF_DATA_DIR", os.environ.get("MEMORY_DATA_DIR", USER_DATA_DIR))).expanduser()
# Keep the existing filename and legacy variables so upgrades find existing data.
DB_PATH = Path(os.environ.get("HANDOFF_DB_PATH", os.environ.get("MEMORY_DB_PATH", DATA_DIR / "memory.db"))).expanduser()
