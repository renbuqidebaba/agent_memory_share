"""Explicit local data export, SQLite backup and terminal-record retention."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .events import log_event


def backup_database(destination: Path) -> dict:
    """Use SQLite's snapshot API, including committed WAL data. Never overwrite."""
    if not db.DB_PATH.is_file():
        raise ValueError("数据库不存在；先创建交接包")
    destination = destination.expanduser().resolve()
    source_path = db.DB_PATH.resolve()
    protected = {source_path, *(Path(str(source_path) + suffix) for suffix in ("-wal", "-shm", "-journal"))}
    if destination in protected:
        raise ValueError("备份不能使用当前数据库或其 SQLite 临时文件路径")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        destination.chmod(0o600)
        source = sqlite3.connect(db.DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {"backup": str(destination)}


def export_data() -> dict:
    """A consistent JSON snapshot, with all handoffs rather than the list's first 100."""
    db.init_db()
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("BEGIN")
            handoffs = []
            for row in conn.execute("SELECT * FROM handoffs ORDER BY created_at, id"):
                item = dict(row)
                item["payload"] = json.loads(item["payload"])
                item["payload"].setdefault("schema_version", 1)
                handoffs.append(item)
            events = []
            for row in conn.execute("SELECT * FROM events WHERE type LIKE 'handoff.%' ORDER BY seq"):
                item = dict(row)
                item["payload"] = json.loads(item["payload"])
                events.append(item)
            evidence = {}
            for table in ("handoff_verifications", "handoff_check_runs"):
                evidence[table] = []
                for row in conn.execute(f"SELECT * FROM {table} ORDER BY created_at, id"):
                    item = dict(row)
                    item["result"] = json.loads(item["result"])
                    evidence[table].append(item)
        return {"kind": "agent-handoff-export", "schema_version": 1,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "handoffs": handoffs, "events": events, **evidence}
    finally:
        conn.close()


def purge_data(older_than_days: int, *, apply: bool = False) -> dict:
    """Default to preview. Delete only old terminal packages and their linked events."""
    if older_than_days < 1:
        raise ValueError("保留天数必须至少为 1")
    db.init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    conn = db.get_conn()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id FROM handoffs WHERE status IN ('completed','rejected','expired') AND updated_at < ?",
                (cutoff,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if apply and ids:
                for hid in ids:
                    conn.execute("DELETE FROM events WHERE type LIKE 'handoff.%' AND json_extract(payload, '$.id') = ?", (hid,))
                    conn.execute("DELETE FROM handoffs WHERE id = ?", (hid,))
                log_event(conn, "handoff.purged", {"count": len(ids), "cutoff": cutoff})
        return {"dry_run": not apply, "cutoff": cutoff, "count": len(ids), "ids": ids}
    finally:
        conn.close()
