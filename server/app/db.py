"""SQLite 连接与 schema 初始化。

设计纪律（对应 PLAN.md §2）：
- 所有数据集中在这一个文件里，agent 不直接读写，统一走 Server API
- events 表是 append-only 事件日志，seq 单调递增，为未来 P2 跨设备同步预留
"""
import sqlite3
from .config import DB_PATH, DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id          TEXT PRIMARY KEY,
  agent_id    TEXT NOT NULL,
  user_id     TEXT,
  session_id  TEXT,
  device_id   TEXT NOT NULL DEFAULT 'local',
  layer       TEXT NOT NULL CHECK (layer IN ('episodic','semantic','summary')),
  content     TEXT NOT NULL,
  tags        TEXT NOT NULL DEFAULT '[]',
  embedding   BLOB,
  valid_from  TEXT,
  valid_to    TEXT,
  deleted_at  TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  agent_id    TEXT NOT NULL,
  user_id     TEXT,
  role        TEXT NOT NULL,
  content     TEXT NOT NULL,
  summarized  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

-- append-only 事件日志：P2 跨设备同步 = 复制这张表
CREATE TABLE IF NOT EXISTS events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  type        TEXT NOT NULL,
  payload     TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

-- FTS5 全文检索：trigram 分词器支持中文子串匹配（unicode61 不切分 CJK）
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content, id UNINDEXED, tokenize = 'trigram'
);

CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent_id, layer);
CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, summarized);
"""


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
