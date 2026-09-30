"""总结管道（S2）：原始对话 → 事实抽取（semantic 层）+ 滚动摘要（summary 层）。

未配置 LLM provider 时优雅降级为 skipped，不影响记忆主流程。
"""
import json
import re

from .. import config
from ..llm.base import get_provider
from ..storage.sqlite_store import SQLiteStore

FACT_PROMPT = """你是记忆提炼器。从对话中提取值得长期保留的事实，输出 JSON 数组，每项格式：
{"content": "一条独立、自包含的事实陈述", "tags": ["标签"]}
只提取：用户偏好/背景、任务状态与决策、明确的事实与结论。
不提取：客套话、临时性闲聊、一次性指令。没有可提取内容时输出 []。
只输出 JSON，不要任何其他文字。"""

SUMMARY_PROMPT = """你是会话摘要器。基于「已有摘要」和「新增对话」生成一份更新后的滚动摘要。
要求：保留任务目标、已完成事项、关键决策、待办问题；压缩细节；200 字以内；直接输出摘要文本。"""

store = SQLiteStore()


def _parse_json_array(text: str) -> list:
    """容错解析 LLM 输出的 JSON 数组。"""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def run_summary(session_id: str, agent_id: str, user_id: str | None = None,
                force: bool = False) -> dict:
    provider = get_provider()
    if provider is None:
        return {
            "status": "skipped",
            "reason": "LLM provider 未配置（设置 MEMORY_LLM_BASE_URL / MEMORY_LLM_API_KEY 后可用）",
        }

    messages = store.get_unsummarized_messages(session_id)
    if not force and len(messages) < config.SUMMARY_MIN_MESSAGES:
        return {
            "status": "skipped",
            "reason": f"未总结消息 {len(messages)} 条 < 阈值 {config.SUMMARY_MIN_MESSAGES}（可用 force=true 强制执行）",
        }
    if not messages:
        return {"status": "skipped", "reason": "没有未总结的消息"}

    transcript = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)

    # 0) 原始片段快照 → episodic 层（交接时可溯源原始上下文）
    episodic_mem = store.add_memory(
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        layer="episodic",
        content=transcript[:4000],
        tags=["session-log"],
        embedding=provider.embed(transcript[:2000]),
    )

    # 1) 事实抽取 → semantic 层（内容完全一致的去重）
    facts = _parse_json_array(provider.complete(FACT_PROMPT, transcript))
    added_facts = []
    for fact in facts:
        content = (fact.get("content") or "").strip()
        if not content:
            continue
        if store.find_semantic_by_content(agent_id, content):
            continue
        mem = store.add_memory(
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
            layer="semantic",
            content=content,
            tags=fact.get("tags") or [],
            embedding=provider.embed(content),
        )
        added_facts.append(mem["id"])

    # 2) 滚动摘要 → summary 层
    prev = store.get_latest_summary(agent_id, session_id)
    prev_text = prev["content"] if prev else "（无）"
    summary_text = provider.complete(
        SUMMARY_PROMPT, f"已有摘要：\n{prev_text}\n\n新增对话：\n{transcript}"
    ).strip()
    summary_mem = store.add_memory(
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        layer="summary",
        content=summary_text,
        tags=["rolling-summary"],
        embedding=provider.embed(summary_text),
    )

    store.mark_messages_summarized([m["id"] for m in messages])

    return {
        "status": "ok",
        "messages_processed": len(messages),
        "episodic_id": episodic_mem["id"],
        "facts_added": len(added_facts),
        "fact_ids": added_facts,
        "summary_id": summary_mem["id"],
        "summary": summary_text,
    }
