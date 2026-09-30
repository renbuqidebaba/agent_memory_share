"""总结管道 API：触发提炼 + 读取最新摘要。"""
from typing import Optional

from fastapi import APIRouter

from ..models import SummaryTrigger
from ..pipeline.summarize import run_summary
from ..storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/v1/summary", tags=["summary"])
store = SQLiteStore()


@router.post("/trigger")
def summary_trigger(body: SummaryTrigger):
    return run_summary(
        session_id=body.session_id,
        agent_id=body.agent_id,
        user_id=body.user_id,
        force=body.force,
    )


@router.get("")
def summary_get(agent_id: str, session_id: Optional[str] = None):
    summary = store.get_latest_summary(agent_id, session_id)
    return {"summary": summary}
