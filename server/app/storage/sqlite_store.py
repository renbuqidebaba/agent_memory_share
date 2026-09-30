"""SQLite 存储实现：记忆 CRUD + FTS/向量混合检索 + 消息存取，所有写操作记事件日志。"""
import json
import struct
import uuid
from datetime import datetime, timezone
from typing import Optional

from ..db import get_conn
from ..events import log_event


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pack_embedding(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(x * x for x in b) ** 0.5 or 1.0
    return dot / (na * nb)


def _rrf(ranked_lists: list[list[str]], limit: int, k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion：融合多个排序列表（FTS 排名 + 向量排名）。"""
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return [iid for iid, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:limit]]


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d.pop("embedding", None)
    return d


class SQLiteStore:
    # ---------- memories ----------
    def add_memory(self, **fields) -> dict:
        memory_id = uuid.uuid4().hex
        now = _now()
        embedding = fields.get("embedding")
        conn = get_conn()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO memories
                       (id, agent_id, user_id, session_id, device_id, layer,
                        content, tags, embedding, valid_from, valid_to, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        memory_id,
                        fields["agent_id"],
                        fields.get("user_id"),
                        fields.get("session_id"),
                        fields.get("device_id", "local"),
                        fields.get("layer", "semantic"),
                        fields["content"],
                        json.dumps(fields.get("tags") or [], ensure_ascii=False),
                        _pack_embedding(embedding) if embedding else None,
                        fields.get("valid_from") or now,
                        fields.get("valid_to"),
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO memories_fts (content, id) VALUES (?, ?)",
                    (fields["content"], memory_id),
                )
                log_event(conn, "memory.add", {"id": memory_id, **{k: v for k, v in fields.items()}})
            return self.get_memory(memory_id)
        finally:
            conn.close()

    def get_memory(self, memory_id: str) -> Optional[dict]:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (memory_id,)
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    def update_memory(self, memory_id: str, **fields) -> Optional[dict]:
        existing = self.get_memory(memory_id)
        if not existing:
            return None
        sets, params = [], []
        for key in ("content", "valid_from", "valid_to"):
            if fields.get(key) is not None:
                sets.append(f"{key} = ?")
                params.append(fields[key])
        if fields.get("tags") is not None:
            sets.append("tags = ?")
            params.append(json.dumps(fields["tags"], ensure_ascii=False))
        if not sets:
            return existing
        conn = get_conn()
        try:
            with conn:
                conn.execute(
                    f"UPDATE memories SET {', '.join(sets)} WHERE id = ?",
                    (*params, memory_id),
                )
                if fields.get("content") is not None:
                    conn.execute("DELETE FROM memories_fts WHERE id = ?", (memory_id,))
                    conn.execute(
                        "INSERT INTO memories_fts (content, id) VALUES (?, ?)",
                        (fields["content"], memory_id),
                    )
                log_event(conn, "memory.update", {"id": memory_id, **fields})
            return self.get_memory(memory_id)
        finally:
            conn.close()

    def delete_memory(self, memory_id: str) -> bool:
        conn = get_conn()
        try:
            with conn:
                cur = conn.execute(
                    "UPDATE memories SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                    (_now(), memory_id),
                )
                if cur.rowcount:
                    conn.execute("DELETE FROM memories_fts WHERE id = ?", (memory_id,))
                    log_event(conn, "memory.delete", {"id": memory_id})
            return cur.rowcount > 0
        finally:
            conn.close()

    def search_memories(self, query: str, filters: dict, limit: int = 10,
                        query_embedding: Optional[list[float]] = None) -> list[dict]:
        """混合检索：FTS 关键词 + 向量余弦，RRF 融合；无 query 时按时间倒序。

        短词（<3 字符，trigram 下限）降级为 LIKE 过滤，对 FTS 和向量两条路都生效。
        """
        clauses = ["m.deleted_at IS NULL"]
        params: list = []
        for key in ("agent_id", "user_id", "session_id", "layer"):
            if filters.get(key):
                clauses.append(f"m.{key} = ?")
                params.append(filters[key])
        if filters.get("tags"):
            for tag in filters["tags"]:
                clauses.append("EXISTS (SELECT 1 FROM json_each(m.tags) WHERE value = ?)")
                params.append(tag)

        terms = query.split() if query else []
        fts_terms = [t for t in terms if len(t) >= 3]
        for t in [t for t in terms if len(t) < 3]:
            clauses.append("m.content LIKE ?")
            params.append(f"%{t}%")
        where = " AND ".join(clauses)

        conn = get_conn()
        try:
            rows = conn.execute(
                f"SELECT m.* FROM memories m WHERE {where} ORDER BY m.created_at DESC LIMIT 500",
                params,
            ).fetchall()
            if not rows:
                return []
            candidates = {r["id"]: r for r in rows}

            ranked_lists: list[list[str]] = []
            if fts_terms:
                match = " OR ".join('"' + t.replace('"', '""') + '"' for t in fts_terms)
                fts_rows = conn.execute(
                    "SELECT id FROM memories_fts WHERE memories_fts MATCH ? ORDER BY rank LIMIT 100",
                    (match,),
                ).fetchall()
                ranked_lists.append([r["id"] for r in fts_rows if r["id"] in candidates])
            if query_embedding:
                scored = [
                    (_cosine(query_embedding, _unpack_embedding(r["embedding"])), r["id"])
                    for r in rows if r["embedding"]
                ]
                scored.sort(reverse=True)
                ranked_lists.append([rid for _, rid in scored[:100]])

            if ranked_lists:
                return [_row_to_dict(candidates[i]) for i in _rrf(ranked_lists, limit)]
            return [_row_to_dict(r) for r in rows[:limit]]
        finally:
            conn.close()

    # ---------- messages ----------
    def add_message(self, **fields) -> dict:
        message_id = uuid.uuid4().hex
        now = _now()
        conn = get_conn()
        try:
            with conn:
                conn.execute(
                    """INSERT INTO messages (id, session_id, agent_id, user_id, role, content, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        message_id,
                        fields["session_id"],
                        fields["agent_id"],
                        fields.get("user_id"),
                        fields["role"],
                        fields["content"],
                        now,
                    ),
                )
                log_event(conn, "message.add", {"id": message_id, "session_id": fields["session_id"]})
            return {"id": message_id, **fields, "created_at": now}
        finally:
            conn.close()

    def get_unsummarized_messages(self, session_id: str) -> list[dict]:
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? AND summarized = 0 ORDER BY created_at",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_messages_summarized(self, message_ids: list[str]) -> None:
        if not message_ids:
            return
        conn = get_conn()
        try:
            with conn:
                conn.executemany(
                    "UPDATE messages SET summarized = 1 WHERE id = ?",
                    [(mid,) for mid in message_ids],
                )
        finally:
            conn.close()

    def get_latest_summary(self, agent_id: str, session_id: Optional[str] = None) -> Optional[dict]:
        conn = get_conn()
        try:
            if session_id:
                row = conn.execute(
                    """SELECT * FROM memories
                       WHERE agent_id = ? AND session_id = ? AND layer = 'summary' AND deleted_at IS NULL
                       ORDER BY created_at DESC LIMIT 1""",
                    (agent_id, session_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT * FROM memories
                       WHERE agent_id = ? AND layer = 'summary' AND deleted_at IS NULL
                       ORDER BY created_at DESC LIMIT 1""",
                    (agent_id,),
                ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    def find_semantic_by_content(self, agent_id: str, content: str) -> Optional[dict]:
        """事实去重：同 agent 下内容完全一致的 semantic 记忆视为重复。"""
        conn = get_conn()
        try:
            row = conn.execute(
                """SELECT * FROM memories
                   WHERE agent_id = ? AND layer = 'semantic' AND content = ? AND deleted_at IS NULL
                   LIMIT 1""",
                (agent_id, content),
            ).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()
