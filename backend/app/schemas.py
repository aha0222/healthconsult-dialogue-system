"""HTTP API 的请求/响应模型。"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

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
    user_id: Optional[str] = Field(
        default=None,
        description="用户标识（绑定用户级档案，跨会话复用记忆）",
    )
    user_profile: Optional[str] = Field(
        default=None,
        max_length=_settings.max_profile_chars,
        description="用户自述的陪护信息（前端采集，未经核实，仅用于个性化陪伴）",
    )
    speaker_role: Optional[str] = Field(
        default=None,
        description="说话人角色 elder/family（缺省由后端按 SPEAKER_DETECT_ENABLED 识别）",
    )

    @field_validator("message")
    @classmethod
    def _reject_blank_message(cls, value: str) -> str:
        """拒绝纯空白消息，避免空转 LLM 与无意义落库。"""
        if not value or not value.strip():
            raise ValueError("message 不能为纯空白")
        return value


class ChatResponse(BaseModel):
    reply: str
    risk: str
    scenes: List[str] = Field(default_factory=list)
    risk_label: str
    scene_labels: List[str] = Field(default_factory=list)
    violations: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    semantic_checked: bool = False
    cached: bool = False
    personality: str
    model: str
    speaker_role: str = "elder"
    session_id: Optional[str] = None


class PersonalityInfo(BaseModel):
    name: str
    description: str
    default: bool = False


class HealthResponse(BaseModel):
    status: str
    model: str
    auth_enabled: bool = False
    voice_enabled: bool = False
    speaker_detect_enabled: bool = False
    config_warnings: List[str] = Field(default_factory=list)


class SessionSummary(BaseModel):
    id: str
    personality: str
    created_at: str
    updated_at: str
    message_count: int = 0
    user_id: Optional[str] = None


class SessionMessage(BaseModel):
    role: str
    content: str
    risk: Optional[str] = None
    scenes: List[str] = Field(default_factory=list)
    created_at: str


class SessionDetail(BaseModel):
    id: str
    personality: str
    created_at: str
    updated_at: str
    user_id: Optional[str] = None
    summary: Optional[str] = None
    conversation_summary: Optional[str] = None
    profile: Optional[dict] = None
    collected: Optional[dict] = None
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
    scenes: List[str] = Field(default_factory=list)
    violations: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: str


class TaxonomyResponse(BaseModel):
    risk_levels: List[str] = Field(default_factory=list)
    risk_labels: dict = Field(default_factory=dict)
    scenes: List[str] = Field(default_factory=list)
    scene_labels: dict = Field(default_factory=dict)
    scene_groups: dict = Field(default_factory=dict)


class ProfileRequest(BaseModel):
    """欢迎流程采集的非隐私资料（与前端字段一一对应）。"""

    user_id: Optional[str] = Field(default=None, description="已有用户 id；缺省则新建")
    name: str = Field(default="", max_length=_settings.max_profile_chars)
    age: str = Field(default="", max_length=_settings.max_profile_chars)
    living: str = Field(default="", max_length=_settings.max_profile_chars)
    conditions: str = Field(default="", max_length=_settings.max_profile_chars)
    medications: str = Field(default="", max_length=_settings.max_profile_chars)
    allergies: str = Field(default="", max_length=_settings.max_profile_chars)
    healthConcerns: str = Field(default="", max_length=_settings.max_profile_chars)
    mobility: str = Field(default="", max_length=_settings.max_profile_chars)
    emergencyContact: str = Field(default="", max_length=_settings.max_profile_chars)


class UserRecord(BaseModel):
    """用户级档案（采集资料已脱敏、画像已结构化）。"""

    id: str
    display_name: str = ""
    collected: dict = Field(default_factory=dict)
    profile: dict = Field(default_factory=dict)
    summary: str = ""
    conversation_summary: str = ""
    created_at: str
    updated_at: str
