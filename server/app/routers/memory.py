"""记忆五件套 API：store / search / get / update / delete。
配置了 LLM provider 时自动为内容和查询生成 embedding，启用混合检索。
"""
from fastapi import APIRouter, HTTPException

from ..llm.base import get_provider
from ..models import MemoryCreate, MemorySearch, MemoryUpdate
from ..storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/v1/memory", tags=["memory"])
store = SQLiteStore()


@router.post("")
def memory_store(body: MemoryCreate):
    fields = body.model_dump()
    provider = get_provider()
    if provider:
        fields["embedding"] = provider.embed(body.content)
    return store.add_memory(**fields)


@router.post("/search")
def memory_search(body: MemorySearch):
    filters = {
        "agent_id": body.agent_id,
        "user_id": body.user_id,
        "session_id": body.session_id,
        "layer": body.layer,
        "tags": body.tags,
    }
    query_embedding = None
    provider = get_provider()
    if provider and body.query:
        query_embedding = provider.embed(body.query)
    return {"results": store.search_memories(body.query, filters, body.limit, query_embedding)}


@router.get("/{memory_id}")
def memory_get(memory_id: str):
    mem = store.get_memory(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem


@router.patch("/{memory_id}")
def memory_update(memory_id: str, body: MemoryUpdate):
    mem = store.update_memory(memory_id, **body.model_dump(exclude_unset=True))
    if not mem:
        raise HTTPException(status_code=404, detail="memory not found")
    return mem


@router.delete("/{memory_id}")
def memory_delete(memory_id: str):
    if not store.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"status": "deleted", "id": memory_id}
