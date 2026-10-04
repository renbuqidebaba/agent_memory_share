"""交接事件日志：与状态变化在同一事务中保存，按显式保留规则清理。"""
import json
import sqlite3
from datetime import datetime, timezone


def log_event(conn: sqlite3.Connection, event_type: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO events (type, payload, created_at) VALUES (?, ?, ?)",
        (event_type, json.dumps(payload, ensure_ascii=False), _now()),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
