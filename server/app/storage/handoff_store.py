"""持久化交接包与原子领取。所有状态变化和事件在同一事务内。"""
from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..db import get_conn, init_db
from ..events import log_event
from ..models import HandoffCreate
from ..handoff_artifacts import prepare_handoff, verify_handoff


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat()


def _row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json.loads(result["payload"])
    result["payload"].setdefault("schema_version", 1)
    return result


def _payload_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class HandoffConflict(Exception):
    pass


class HandoffStore:
    def __init__(self) -> None:
        init_db()

    @staticmethod
    def _expire(conn: sqlite3.Connection, now: str) -> None:
        rows = conn.execute(
            "SELECT id FROM handoffs WHERE status IN ('pending','claimed') AND expires_at <= ?",
            (now,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE handoffs SET status = 'expired', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            log_event(conn, "handoff.expired", {"id": row["id"]})

    def create(self, body: HandoffCreate, *, _observed: bool = False) -> dict:
        if not _observed and (body.workspace_snapshot is not None or body.observed_checks):
            raise ValueError("工具观测字段只能通过 capture 生成，不能由调用方声明")
        now = _now()
        handoff_id = uuid.uuid4().hex
        payload = body.model_dump(mode="json")
        conn = get_conn()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO handoffs
                       (id, task_id, from_agent, to_agent, status, payload,
                        created_at, updated_at, expires_at)
                       VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                    (handoff_id, body.task_id, body.from_agent, body.to_agent,
                     json.dumps(payload, ensure_ascii=False), _stamp(now), _stamp(now),
                     _stamp(now + timedelta(seconds=body.ttl_seconds))),
                )
                log_event(conn, "handoff.created", {"id": handoff_id, "task_id": body.task_id})
                return _row(conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone())
        finally:
            conn.close()

    def get(self, handoff_id: str) -> dict | None:
        conn = get_conn()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                self._expire(conn, _stamp(_now()))
                return _row(conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone())
        finally:
            conn.close()

    def list(self, task_id: str | None = None, status: str | None = None,
             to_agent: str | None = None, workspace: str | None = None) -> list[dict]:
        conn = get_conn()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                self._expire(conn, _stamp(_now()))
                clauses, params = [], []
                if task_id:
                    clauses.append("task_id = ?")
                    params.append(task_id)
                if status:
                    clauses.append("status = ?")
                    params.append(status)
                if to_agent:
                    clauses.append("(to_agent = ? OR to_agent IS NULL)")
                    params.append(to_agent)
                if workspace:
                    clauses.append("json_extract(payload, '$.workspace_root') = ?")
                    params.append(str(Path(workspace).resolve()))
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                rows = conn.execute(
                    f"SELECT * FROM handoffs{where} ORDER BY created_at DESC LIMIT 100", params
                ).fetchall()
                return [_row(row) for row in rows]
        finally:
            conn.close()

    def claim(self, handoff_id: str, agent_id: str, lease_seconds: int = 3600) -> dict:
        if not agent_id.strip() or not 60 <= lease_seconds <= 86400:
            raise ValueError("agent_id 不可为空，lease_seconds 必须在 60 到 86400 之间")
        now = _stamp(_now())
        lease_until = _stamp(_now() + timedelta(seconds=lease_seconds))
        conn = get_conn()
        try:
            # 过期是独立的时间状态变化，不能被后续领取冲突回滚。
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                self._expire(conn, now)
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
                if row is None:
                    raise KeyError(handoff_id)
                if row["to_agent"] and row["to_agent"] != agent_id:
                    raise HandoffConflict("此交接包指定了其他接收 agent")
                if row["status"] == "claimed" and row["claimed_by"] == agent_id and row["claim_until"] > now:
                    return _row(row)
                if row["status"] not in ("pending", "claimed") or (row["status"] == "claimed" and row["claim_until"] > now):
                    raise HandoffConflict(f"交接包当前状态为 {row['status']}，不可领取")
                conn.execute(
                    """UPDATE handoffs SET status = 'claimed', claimed_by = ?, claim_until = ?,
                       updated_at = ? WHERE id = ?""",
                    (agent_id, lease_until, now, handoff_id),
                )
                log_event(conn, "handoff.claimed", {"id": handoff_id, "agent_id": agent_id})
                return _row(conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone())
        finally:
            conn.close()

    def ack(self, handoff_id: str, agent_id: str, accepted: bool, reason: str = "",
            verification_id: str | None = None) -> dict:
        if not agent_id.strip():
            raise ValueError("agent_id 不可为空")
        now = _stamp(_now())
        conn = get_conn()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                self._expire(conn, now)
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
                if row is None:
                    raise KeyError(handoff_id)
                if row["status"] not in ("claimed",) or row["claimed_by"] != agent_id or row["claim_until"] <= now:
                    raise HandoffConflict("只有当前有效的领取者才能确认交接")
                if not accepted and not reason.strip():
                    raise ValueError("退回交接时必须说明原因")
                payload = json.loads(row["payload"])
                payload.setdefault("schema_version", 1)
                if accepted and (payload.get("require_verification") or verification_id):
                    receipt = conn.execute("SELECT * FROM handoff_verifications WHERE id = ?", (verification_id,)).fetchone()
                    if receipt is None or receipt["handoff_id"] != handoff_id or receipt["agent_id"] != agent_id:
                        raise HandoffConflict("接受交接必须引用本任务、当前领取者的核验记录")
                    if receipt["claim_until"] != row["claim_until"] or receipt["payload_digest"] != _payload_digest(payload):
                        raise HandoffConflict("核验记录已失效，请重新核验")
                    if not json.loads(receipt["result"])["ok"]:
                        raise HandoffConflict("核验记录未通过，不能接受交接")
                    # Filesystem state cannot share a SQLite transaction. Recheck as
                    # close to acceptance as possible; this is no workspace lock.
                    if not verify_handoff({"payload": payload}, receipt["workspace"])["ok"]:
                        raise HandoffConflict("核验后工作区已变化，请重新核验或退回")
                status = "accepted" if accepted else "rejected"
                conn.execute(
                    "UPDATE handoffs SET status = ?, response = ?, updated_at = ? WHERE id = ?",
                    (status, reason, now, handoff_id),
                )
                log_event(conn, f"handoff.{status}", {"id": handoff_id, "agent_id": agent_id,
                          "reason": reason, "verification_id": verification_id if accepted else None})
                return _row(conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone())
        finally:
            conn.close()

    def capture(self, body: HandoffCreate, workspace: str | Path, include: list[str] | None = None,
                check_ids: list[str] | None = None) -> dict:
        from ..workspace_capture import capture_workspace, verify_snapshot
        if body.workspace_snapshot is not None or body.observed_checks:
            raise ValueError("请提供任务描述，工具观测字段由服务采集")
        root = Path(workspace).resolve()
        names = list(dict.fromkeys([a.path for a in body.artifacts] + (include or [])))
        snapshot = capture_workspace(root, names)
        observed = []
        conn = get_conn()
        try:
            for check_id in dict.fromkeys(check_ids or []):
                row = conn.execute("SELECT * FROM handoff_check_runs WHERE id = ?", (check_id,)).fetchone()
                if row is None or row["workspace"] != str(root):
                    raise ValueError(f"检查记录不存在或属于其他工作区: {check_id}")
                check = json.loads(row["result"])
                if not check["snapshot_unchanged"] or not verify_snapshot(check["workspace_snapshot"], root)["ok"]:
                    raise HandoffConflict(f"检查结果对应的工作区已变化: {check_id}")
                observed.append({"id": check_id, **check})
        finally:
            conn.close()
        data = prepare_handoff(body, root).model_dump()
        data.update(schema_version=2, require_verification=True, workspace_snapshot=snapshot.model_dump(), observed_checks=observed)
        prepared = HandoffCreate.model_validate(data)
        if not verify_handoff({"payload": prepared.model_dump()}, root)["ok"]:
            raise HandoffConflict("创建交接前工作区发生变化，请重新采集")
        return self.create(prepared, _observed=True)

    def run_check(self, workspace: str | Path, argv: list[str], timeout_seconds: int = 120,
                  include: list[str] | None = None) -> dict:
        from ..check_runner import run_check
        result = run_check(workspace, argv, timeout_seconds, include or [])
        check_id = uuid.uuid4().hex
        conn = get_conn()
        try:
            with conn:
                conn.execute("INSERT INTO handoff_check_runs VALUES (?, ?, ?, ?)",
                    (check_id, result["workspace"], json.dumps(result, ensure_ascii=False), _stamp(_now())))
        finally:
            conn.close()
        return {"id": check_id, **result}

    def verify(self, handoff_id: str, agent_id: str, workspace: str | Path) -> dict:
        if not agent_id.strip():
            raise ValueError("核验记录需要接收者 agent_id")
        handoff = self.get(handoff_id)
        if handoff is None:
            raise KeyError(handoff_id)
        root = str(Path(workspace).resolve())
        result = verify_handoff(handoff, root)
        conn = get_conn()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
                now = _stamp(_now())
                if row is None or row["status"] != "claimed" or row["claimed_by"] != agent_id or row["claim_until"] <= now or row["expires_at"] <= now:
                    raise HandoffConflict("只有当前有效的领取者才能保存核验记录")
                digest = _payload_digest(handoff["payload"])
                current_payload = json.loads(row["payload"])
                current_payload.setdefault("schema_version", 1)
                if digest != _payload_digest(current_payload):
                    raise HandoffConflict("核验期间交接包发生变化")
                rid = uuid.uuid4().hex
                conn.execute("INSERT INTO handoff_verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, handoff_id, agent_id, row["claim_until"], digest, root, json.dumps(result), now))
                log_event(conn, "handoff.verified", {"id": handoff_id, "agent_id": agent_id,
                          "verification_id": rid, "ok": result["ok"]})
                return {**result, "verification_id": rid, "handoff_id": handoff_id,
                        "agent_id": agent_id, "workspace": root, "payload_digest": digest, "verified_at": now}
        finally:
            conn.close()

    def verifications(self, handoff_id: str) -> list[dict]:
        conn = get_conn()
        try:
            records = []
            for row in conn.execute("SELECT * FROM handoff_verifications WHERE handoff_id = ? ORDER BY created_at, id", (handoff_id,)):
                item = dict(row)
                item["result"] = json.loads(item["result"])
                records.append(item)
            return records
        finally:
            conn.close()

    def complete(self, handoff_id: str, agent_id: str) -> dict:
        if not agent_id.strip():
            raise ValueError("agent_id 不可为空")
        now = _stamp(_now())
        conn = get_conn()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
                if row is None:
                    raise KeyError(handoff_id)
                if row["status"] != "accepted" or row["claimed_by"] != agent_id:
                    raise HandoffConflict("只有已接受交接的接收者才能标记完成")
                conn.execute(
                    "UPDATE handoffs SET status = 'completed', updated_at = ? WHERE id = ?",
                    (now, handoff_id),
                )
                log_event(conn, "handoff.completed", {"id": handoff_id, "agent_id": agent_id})
                return _row(conn.execute("SELECT * FROM handoffs WHERE id = ?", (handoff_id,)).fetchone())
        finally:
            conn.close()
