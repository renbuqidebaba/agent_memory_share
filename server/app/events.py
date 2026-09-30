"""append-only 事件日志：每次写操作记一条事件，P2 同步直接复制此表。"""
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
