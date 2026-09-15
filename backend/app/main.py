"""对话系统后端入口（FastAPI）。

职责：
1. 载入 skill 规范（skills/healthconsult-assistant-skill/SKILL.md）作为 system prompt
2. 接收前端请求 -> 调用 LLM -> 解析回复末尾的场景标记
3. 用 backend.app.safety.safety_checker 对回复做兜底快检
4. 把「回复正文 + 风险等级」返回给前端
5. 用 SQLite 持久化会话/消息/审计日志，支持流式（SSE）输出
6. API Key 鉴权、按 IP 限流、结构化访问日志

启动：
    set DEEPSEEK_API_KEY=sk-xxx
    python -m uvicorn backend.app.main:app --reload --port 8000
"""

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .alerts import send_alert
from .cache import TTLCache, is_cacheable_request, is_cacheable_result
from .config import get_settings
from .dialogue.llm_client import LLMError
from .dialogue.memory import MemoryManager
from .dialogue.orchestrator import DialogueOrchestrator
from .dialogue.prompt import (
    DEFAULT_PERSONALITY,
    PERSONALITY_DESCRIPTIONS,
    PERSONALITY_OVERLAYS,
    normalize_personality,
)
from .dialogue.taxonomy import RISK_LABELS, SCENE_GROUPS, SCENE_LABELS, RISK_LEVELS, SCENES
from .logging_config import log_event, setup_logging
from .paths import validate_paths
from .safety.semantic_checker import SemanticChecker
from .schemas import (
    AuditRecord,
    ChatRequest,
    ChatResponse,
    DeleteResponse,
    HealthResponse,
    PersonalityInfo,
    SessionDetail,
    SessionMessage,
    SessionSummary,
    TaxonomyResponse,
)
from .security import get_client_ip, rate_limit, require_api_key
from .storage import Database

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)

access_logger = logging.getLogger("xiaonuan.access")
chat_logger = logging.getLogger("xiaonuan.chat")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时校验 skill 规范可定位，避免运行到首个请求才报错。"""
    validate_paths()
    yield


app = FastAPI(
    title="小暖健康陪护 · 后端",
    description="对话编排 + 三层安全兜底，供 frontend/ 调用。",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator: DialogueOrchestrator | None = None
_db: Database | None = None
_cache: TTLCache | None = None


def get_orchestrator() -> DialogueOrchestrator:
    """惰性构建编排器单例（可被测试依赖覆盖）。"""
    global _orchestrator
    if _orchestrator is None:
        semantic_checker = SemanticChecker(settings=settings) if settings.semantic_check else None
        _orchestrator = DialogueOrchestrator(
            settings=settings, semantic_checker=semantic_checker
        )
    return _orchestrator


def get_db() -> Database:
    """惰性构建数据库单例（可被测试依赖覆盖）。"""
    global _db
    if _db is None:
        _db = Database(settings.db_path)
    return _db


def get_memory(db: Database = Depends(get_db)) -> MemoryManager:
    """构建长期记忆管理器（可被测试依赖覆盖）。"""
    return MemoryManager(db, settings=settings)


def get_cache() -> TTLCache:
    """惰性构建回复缓存单例（可被测试依赖覆盖）。"""
    global _cache
    if _cache is None:
        _cache = TTLCache(settings.cache_max_size, settings.cache_ttl_seconds)
    return _cache


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    response.headers["X-Process-Time-Ms"] = str(duration_ms)
    log_event(
        access_logger,
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
        client=get_client_ip(request),
    )
    return response


def _resolve_context(db: Database, memory: MemoryManager, request: ChatRequest):
    """返回 (session_id, history, memory_block)。

    带 session_id 时从库里取历史并注入长期记忆；否则新建会话。
    """
    if request.session_id:
        session = db.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        context = memory.prepare(request.session_id)
        return request.session_id, context["history"], context["memory_block"]

    session_id = db.create_session(normalize_personality(request.personality))
    history = [m.model_dump() for m in request.history]
    return session_id, history, ""


def _record_audit(db, session_id, request, result, latency_ms):
    record = {
        "session_id": session_id,
        "personality": result["personality"],
        "user_message": request.message,
        "reply": result["reply"],
        "risk": result["risk"],
        "scenes": result.get("scenes", []),
        "violations": result["violations"],
        "fallback_used": result["fallback_used"],
        "model": result["model"],
        "latency_ms": latency_ms,
    }
    db.add_audit(**record)
    log_event(chat_logger, "chat_response", **record)
    send_alert(record)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model=settings.model,
        has_api_key=settings.has_api_key,
        auth_enabled=settings.auth_enabled,
    )


@app.get("/api/personalities", response_model=list[PersonalityInfo])
def personalities() -> list[PersonalityInfo]:
    return [
        PersonalityInfo(
            name=name,
            description=PERSONALITY_DESCRIPTIONS.get(name, ""),
            default=(name == DEFAULT_PERSONALITY),
        )
        for name in PERSONALITY_OVERLAYS
    ]


@app.get("/api/taxonomy", response_model=TaxonomyResponse)
def taxonomy() -> TaxonomyResponse:
    return TaxonomyResponse(
        risk_levels=list(RISK_LEVELS),
        risk_labels=RISK_LABELS,
        scenes=list(SCENES),
        scene_labels=SCENE_LABELS,
        scene_groups=SCENE_GROUPS,
    )


@app.post(
    "/api/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_api_key), Depends(rate_limit)],
)
def chat(
    request: ChatRequest,
    orchestrator: DialogueOrchestrator = Depends(get_orchestrator),
    db: Database = Depends(get_db),
    memory: MemoryManager = Depends(get_memory),
    cache: TTLCache = Depends(get_cache),
) -> ChatResponse:
    session_id, history, memory_block = _resolve_context(db, memory, request)

    cache_key = None
    if is_cacheable_request(
        request.message, request.history, request.session_id, settings
    ):
        cache_key = cache.make_key(request.personality, request.message)
        cached = cache.get(cache_key)
        if cached is not None:
            result = dict(cached)
            result["cached"] = True
            db.add_message(session_id, "user", request.message)
            db.add_message(
                session_id, "assistant", result["reply"], result["risk"], result.get("scenes")
            )
            _record_audit(db, session_id, request, result, 0)
            result["session_id"] = session_id
            return ChatResponse(**result)

    start = time.perf_counter()
    try:
        result = orchestrator.respond(
            message=request.message,
            personality=request.personality,
            history=history,
            memory_block=memory_block,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    latency_ms = int((time.perf_counter() - start) * 1000)

    if cache_key and is_cacheable_result(result):
        cache.set(cache_key, dict(result))

    db.add_message(session_id, "user", request.message)
    db.add_message(
        session_id, "assistant", result["reply"], result["risk"], result.get("scenes")
    )
    _record_audit(db, session_id, request, result, latency_ms)
    result["session_id"] = session_id
    return ChatResponse(**result)


@app.post(
    "/api/chat/stream",
    dependencies=[Depends(require_api_key), Depends(rate_limit)],
)
def chat_stream(
    request: ChatRequest,
    orchestrator: DialogueOrchestrator = Depends(get_orchestrator),
    db: Database = Depends(get_db),
    memory: MemoryManager = Depends(get_memory),
):
    if not settings.has_api_key:
        raise HTTPException(
            status_code=502,
            detail="未配置 DEEPSEEK_API_KEY，无法调用大模型。",
        )

    session_id, history, memory_block = _resolve_context(db, memory, request)

    def event_generator():
        start = time.perf_counter()
        try:
            for kind, payload in orchestrator.respond_stream(
                message=request.message,
                personality=request.personality,
                history=history,
                memory_block=memory_block,
            ):
                if kind == "delta":
                    yield _sse("delta", {"text": payload})
                else:
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    db.add_message(session_id, "user", request.message)
                    db.add_message(
                        session_id,
                        "assistant",
                        payload["reply"],
                        payload["risk"],
                        payload.get("scenes"),
                    )
                    _record_audit(db, session_id, request, payload, latency_ms)
                    payload = dict(payload)
                    payload["session_id"] = session_id
                    yield _sse("done", payload)
        except LLMError as exc:
            yield _sse("error", {"detail": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(
    "/api/sessions",
    response_model=list[SessionSummary],
    dependencies=[Depends(require_api_key)],
)
def list_sessions(limit: int = 50, db: Database = Depends(get_db)):
    return [SessionSummary(**row) for row in db.list_sessions(limit=limit)]


@app.get(
    "/api/sessions/{session_id}",
    response_model=SessionDetail,
    dependencies=[Depends(require_api_key)],
)
def get_session(session_id: str, db: Database = Depends(get_db)):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = [
        SessionMessage(
            role=m["role"],
            content=m["content"],
            risk=m.get("risk"),
            scenes=m.get("scenes") or [],
            created_at=m["created_at"],
        )
        for m in db.get_messages(session_id)
    ]
    profile = None
    if session.get("profile"):
        try:
            profile = json.loads(session["profile"])
        except (ValueError, TypeError):
            profile = None
    return SessionDetail(
        id=session["id"],
        personality=session["personality"],
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        summary=session.get("summary"),
        profile=profile,
        messages=messages,
    )


@app.delete(
    "/api/sessions/{session_id}",
    response_model=DeleteResponse,
    dependencies=[Depends(require_api_key)],
)
def delete_session(session_id: str, db: Database = Depends(get_db)):
    if not db.delete_session(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return DeleteResponse(deleted=True)


@app.get(
    "/api/audit",
    response_model=list[AuditRecord],
    dependencies=[Depends(require_api_key)],
)
def list_audit(
    limit: int = 100,
    session_id: str | None = None,
    db: Database = Depends(get_db),
):
    return [AuditRecord(**row) for row in db.list_audit(limit=limit, session_id=session_id)]


@app.get("/api/cache/stats", dependencies=[Depends(require_api_key)])
def cache_stats(cache: TTLCache = Depends(get_cache)):
    return {"enabled": settings.cache_enabled, **cache.stats()}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
