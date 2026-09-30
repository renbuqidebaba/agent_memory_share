"""对话消息摄入：总结管道的输入源。"""
from fastapi import APIRouter

from ..models import MessageBatch, MessageIn
from ..storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/v1/messages", tags=["messages"])
store = SQLiteStore()


@router.post("")
def message_add(body: MessageIn):
    return store.add_message(**body.model_dump())


@router.post("/batch")
def message_add_batch(body: MessageBatch):
    return {"added": [store.add_message(**m.model_dump())["id"] for m in body.messages]}
