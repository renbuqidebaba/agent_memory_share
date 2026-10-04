"""SQLite 连接与 schema 初始化。

CLI、MCP 共用本地数据库。PRAGMA user_version 记录数据库格式版本。
事件随状态在同一事务提交；显式清理历史交接时也会清理关联事件。
"""
import sqlite3
from .config import DB_PATH

SCHEMA_VERSION = 2

EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoff_verifications (
  id TEXT PRIMARY KEY,
  handoff_id TEXT NOT NULL REFERENCES handoffs(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL,
  claim_until TEXT NOT NULL,
  payload_digest TEXT NOT NULL,
  workspace TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verifications_handoff ON handoff_verifications(handoff_id);
CREATE TABLE IF NOT EXISTS handoff_check_runs (
  id TEXT PRIMARY KEY,
  workspace TEXT NOT NULL,
  result TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

SCHEMA = """
-- Local event history, not a cross-device replication protocol.
CREATE TABLE IF NOT EXISTS events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  type        TEXT NOT NULL,
  payload     TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
  id           TEXT PRIMARY KEY,
  task_id      TEXT NOT NULL,
  from_agent   TEXT NOT NULL,
  to_agent     TEXT,
  status       TEXT NOT NULL CHECK (status IN ('pending','claimed','accepted','rejected','completed','expired')),
  payload      TEXT NOT NULL,
  claimed_by   TEXT,
  claim_until  TEXT,
  response     TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handoffs_task ON handoffs(task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_handoffs_status ON handoffs(status, expires_at);

"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"数据库格式 {version} 高于本程序支持的 {SCHEMA_VERSION}；请升级程序")
            if version == 0:
                # Version 0 is the pre-release schema. All additions are idempotent;
                # existing rows, IDs and events are retained without rewriting payloads.
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
            if version < 2:
                for statement in EVIDENCE_SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        conn.close()
