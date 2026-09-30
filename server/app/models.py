"""API 请求/响应模型。"""
from typing import Literal, Optional
from pydantic import BaseModel, Field

Layer = Literal["episodic", "semantic", "summary"]


class MemoryCreate(BaseModel):
    agent_id: str
    content: str
    layer: Layer = "semantic"
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    device_id: str = "local"
    tags: list[str] = Field(default_factory=list)
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


class MemoryUpdate(BaseModel):
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


class MemorySearch(BaseModel):
    query: str = ""
    agent_id: Optional[str] = None      # None = 跨 agent 检索（P1 交接的基础）
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    layer: Optional[Layer] = None
    tags: Optional[list[str]] = None
    limit: int = 10


class MessageIn(BaseModel):
    session_id: str
    agent_id: str
    role: str                            # user / assistant / system / tool
    content: str
    user_id: Optional[str] = None


class MessageBatch(BaseModel):
    messages: list[MessageIn]


class SummaryTrigger(BaseModel):
    session_id: str
    agent_id: str
    user_id: Optional[str] = None
    force: bool = False                  # 忽略最小消息数限制
