"""HTTP API 的请求/响应模型。"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .config import get_settings
from .dialogue.prompt import DEFAULT_PERSONALITY

_settings = get_settings()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=_settings.max_message_chars)


class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=_settings.max_message_chars,
        description="老人说的话",
    )
    personality: str = Field(default=DEFAULT_PERSONALITY, description="人格风格")
    history: List[ChatMessage] = Field(
        default_factory=list,
        max_length=_settings.max_history_items,
        description="历史消息",
    )
    session_id: Optional[str] = Field(default=None, description="会话标识（可选，当前无状态）")


class ChatResponse(BaseModel):
    reply: str
    risk: str
    risk_label: str
    violations: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    semantic_checked: bool = False
    cached: bool = False
    personality: str
    model: str
    session_id: Optional[str] = None


class PersonalityInfo(BaseModel):
    name: str
    description: str
    default: bool = False


class HealthResponse(BaseModel):
    status: str
    model: str
    has_api_key: bool
    auth_enabled: bool = False


class SessionSummary(BaseModel):
    id: str
    personality: str
    created_at: str
    updated_at: str
    message_count: int = 0


class SessionMessage(BaseModel):
    role: str
    content: str
    risk: Optional[str] = None
    created_at: str


class SessionDetail(BaseModel):
    id: str
    personality: str
    created_at: str
    updated_at: str
    summary: Optional[str] = None
    profile: Optional[dict] = None
    messages: List[SessionMessage] = Field(default_factory=list)


class DeleteResponse(BaseModel):
    deleted: bool


class AuditRecord(BaseModel):
    id: int
    session_id: Optional[str] = None
    personality: Optional[str] = None
    user_message: str
    reply: str
    risk: Optional[str] = None
    violations: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: str
