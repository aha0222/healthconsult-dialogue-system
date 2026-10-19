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

from .alerts import sanitize_alert, send_alert
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
from .feedback import is_repeated_question
from .logging_config import log_event, setup_logging
from .paths import validate_paths
from .providers import llm_status
from .profile import (
    build_collected_summary,
    build_known_info_from_text,
    load_json_dict,
    map_collected_to_profile,
    merge_profiles,
    redact_collected,
    sanitize_collected,
)
from .safety.semantic_checker import SemanticChecker
from .sampling import redact
from .schemas import (
    AuditRecord,
    ChatRequest,
    ChatResponse,
    DeleteResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    InterruptResponse,
    PersonalityInfo,
    ProfileRequest,
    SessionDetail,
    SessionMessage,
    SessionSummary,
    TaxonomyResponse,
    UserRecord,
)
from .security import check_startup_security, get_client_ip, rate_limit, require_api_key
from .storage import Database

settings = get_settings()
setup_logging(settings.log_level, settings.log_format)

access_logger = logging.getLogger("xiaonuan.access")
chat_logger = logging.getLogger("xiaonuan.chat")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时校验 skill 规范与安全配置，避免运行到首个请求才报错。"""
    validate_paths()
    warnings = check_startup_security(settings)
    for warning in warnings:
        logging.getLogger("xiaonuan.security").warning("配置告警: %s", warning)
    # 启动探测：在线档 + 开了网络兜底时，先探一次云端能不能连上。
    # 连不上就当场标成降级（/api/health 立刻可见），别等老人说第一句话才 502。
    if settings.llm_fallback_enabled and not settings.offline_mode:
        from .providers.fallback import mark_degraded, probe_primary

        if not probe_primary(settings):
            mark_degraded(settings, "启动探测：云端不可达")
            logging.getLogger("xiaonuan.llm.fallback").warning(
                "启动探测：云端 %s 不可达，已切本地档（LLM_FALLBACK_ENABLED=1）",
                settings.base_url,
            )
    # 审计留存：默认 0＝永久不清理。定了天数才动手，只删审计表（会话与消息不动）。
    if settings.audit_retention_days > 0:
        try:
            removed = get_db().prune_audit(settings.audit_retention_days)
            if removed:
                logging.getLogger("xiaonuan.privacy").info(
                    "审计留存：清理 %s 天前的记录 %s 条", settings.audit_retention_days, removed
                )
        except Exception as exc:  # 清理失败不该拦住启动
            logging.getLogger("xiaonuan.privacy").warning("审计清理失败: %s", exc)
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


def _redact_incoming(text: str) -> str:
    """对进入系统的对话原文做隐私脱敏（手机号/身份证/银行卡）。

    为什么在入口做一次：这段文本会去三个地方——云端 LLM、审计库、messages 表。
    在最早处脱敏，三处就都不必各自记得；而且模型从没收到过原始号码，
    回复里也就不会把它带出来。采集表单字段在 /api/profile 已经脱敏过。
    """
    return redact(text or "")


def _resolve_context(db: Database, memory: MemoryManager, request: ChatRequest):
    """返回 (session_id, history, known_info)。

    带 session_id 时从库里取历史并注入「合并后的已知信息」；否则新建会话。
    用户级档案（采集资料 + 对话提炼画像）合并为唯一来源，冲突以用户自述为准。
    """
    if request.session_id:
        session = db.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        if request.user_id and not session.get("user_id"):
            db.bind_session_user(request.session_id, request.user_id)
        context = memory.prepare(request.session_id)
        return request.session_id, context["history"], context["memory_block"]

    user_id = request.user_id
    session_id = db.create_session(normalize_personality(request.personality), user_id=user_id)
    # 客户端带过来的历史是前端内存里的原始输入，可能含未脱敏号码，同样要过一遍
    history = [
        {"role": m.role, "content": _redact_incoming(m.content)} for m in request.history
    ]
    if user_id:
        user = db.get_user(user_id)
        known_info = memory.known_info_for_user(user) if user else ""
    elif request.user_profile:
        known_info = build_known_info_from_text(redact(request.user_profile))
    else:
        known_info = ""
    return session_id, history, known_info


def _user_record(db: Database, user_id: str) -> UserRecord:
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return UserRecord(
        id=user["id"],
        display_name=user.get("display_name") or "",
        collected=sanitize_collected(load_json_dict(user.get("collected"))),
        profile=merge_profiles(
            load_json_dict(user.get("profile")),
            load_json_dict(user.get("conversation_profile")),
        ),
        summary=user.get("summary") or "",
        conversation_summary=user.get("conversation_summary") or "",
        created_at=user["created_at"],
        updated_at=user["updated_at"],
    )


def _degradation_notice() -> dict:
    """云端不可用已切本地档时，给前端一句人话提示（不静默换档）。

    读取的是进程级降级状态，所以在 orchestrator 处理完这一轮之后调用才有意义。
    """
    status = llm_status(settings)
    if not status.get("llm_degraded"):
        return {"degraded": False, "notice": None}
    return {
        "degraded": True,
        "notice": "网络连不上，小暖先用本地模型接着陪您，回复可能慢一些。",
    }


def _previous_user_message(history) -> str | None:
    """取上一条老人发言，用于判定"重复追问"。没有就返回 None（不误判）。"""
    for item in reversed(history or []):
        if isinstance(item, dict) and item.get("role") == "user" and item.get("content"):
            return item["content"]
    return None


def _record_audit(
    db, session_id, user_message, result, latency_ms, repeated_question=False
):
    """落一条审计记录，返回其 id（前端凭 id 回填赞/踩）。

    user_message 必须是**已脱敏**的文本：审计表存全文，而审计库不该成为
    隐私泄露的第二个出口。

    repeated_question 是客观交互信号：老人把同一件事又问了一遍，说明上一条回复
    没解决他的问题——这是没有人工标注时最接近"坏"的信号。
    """
    record = {
        "session_id": session_id,
        "personality": result["personality"],
        "user_message": user_message,
        "reply": result["reply"],
        "risk": result["risk"],
        "scenes": result.get("scenes", []),
        "violations": result["violations"],
        "fallback_used": result["fallback_used"],
        "model": result["model"],
        "latency_ms": latency_ms,
        "repeated_question": repeated_question,
        "llm_backend": llm_status(settings).get("llm_backend"),
    }
    audit_id = db.add_audit(**record)
    log_event(chat_logger, "chat_response", **sanitize_alert(record))
    send_alert(record, background=True)
    return audit_id


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model=settings.model,
        auth_enabled=settings.auth_enabled,
        voice_enabled=settings.voice_enabled,
        speaker_detect_enabled=settings.speaker_detect_enabled,
        config_warnings=list(settings.config_warnings),
        **llm_status(settings),
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
    # 脱敏后的原话是这条链路上唯一的"用户输入"版本：发给模型、落库、进审计
    message = _redact_incoming(request.message)
    repeated = is_repeated_question(_previous_user_message(history), message)

    cache_key = None
    if is_cacheable_request(
        message, request.history, request.session_id, settings
    ):
        cache_key = cache.make_key(
            request.personality,
            message,
            memory_block or "",
        )
        cached = cache.get(cache_key)
        if cached is not None:
            result = dict(cached)
            result["cached"] = True
            db.add_message(session_id, "user", message)
            db.add_message(
                session_id, "assistant", result["reply"], result["risk"], result.get("scenes")
            )
            result["audit_id"] = _record_audit(
                db, session_id, message, result, 0, repeated_question=repeated
            )
            result["session_id"] = session_id
            return ChatResponse(**result)

    start = time.perf_counter()
    try:
        result = orchestrator.respond(
            message=message,
            personality=request.personality,
            history=history,
            memory_block=memory_block,
            speaker_role=request.speaker_role,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    latency_ms = int((time.perf_counter() - start) * 1000)

    if cache_key and is_cacheable_result(result):
        cache.set(cache_key, dict(result))

    db.add_message(session_id, "user", message)
    db.add_message(
        session_id, "assistant", result["reply"], result["risk"], result.get("scenes")
    )
    result["audit_id"] = _record_audit(
        db, session_id, message, result, latency_ms, repeated_question=repeated
    )
    result.update(_degradation_notice())
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
    # 离线模式不需要云端 Key：LLMClient 才要求它，LocalLLM 不读。
    # 不加这个豁免，离线时前端（只调本接口）第一句话就会被 502 挡住，
    # 永远走不到本地模型。（成员 B 交付补丁 0002 第 5 处）
    if not settings.has_api_key and not settings.offline_mode:
        raise HTTPException(
            status_code=502,
            detail="未配置 DEEPSEEK_API_KEY，无法调用大模型。",
        )

    session_id, history, memory_block = _resolve_context(db, memory, request)
    message = _redact_incoming(request.message)
    repeated = is_repeated_question(_previous_user_message(history), message)

    def event_generator():
        start = time.perf_counter()
        try:
            for kind, payload in orchestrator.respond_stream(
                message=message,
                personality=request.personality,
                history=history,
                memory_block=memory_block,
                speaker_role=request.speaker_role,
            ):
                if kind == "delta":
                    yield _sse("delta", {"text": payload})
                else:
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    db.add_message(session_id, "user", message)
                    db.add_message(
                        session_id,
                        "assistant",
                        payload["reply"],
                        payload["risk"],
                        payload.get("scenes"),
                    )
                    audit_id = _record_audit(
                        db, session_id, message, payload, latency_ms,
                        repeated_question=repeated,
                    )
                    payload = dict(payload)
                    payload["session_id"] = session_id
                    payload["audit_id"] = audit_id
                    payload.update(_degradation_notice())
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
    profile = load_json_dict(session.get("profile"))
    collected = None
    summary = session.get("summary")
    conversation_summary = None
    user_id = session.get("user_id")
    if user_id:
        user = db.get_user(user_id)
        if user:
            collected = sanitize_collected(load_json_dict(user.get("collected")))
            profile = merge_profiles(
                load_json_dict(user.get("profile")),
                load_json_dict(user.get("conversation_profile")),
            )
            conversation_summary = user.get("conversation_summary")
    return SessionDetail(
        id=session["id"],
        personality=session["personality"],
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        user_id=user_id,
        summary=summary,
        conversation_summary=conversation_summary,
        profile=profile,
        collected=collected,
        messages=messages,
    )


@app.post(
    "/api/profile",
    response_model=UserRecord,
    dependencies=[Depends(require_api_key)],
)
def save_profile(request: ProfileRequest, db: Database = Depends(get_db)):
    """接收前端采集的结构化资料，脱敏落库，并立即生成画像与摘要。"""
    collected = redact_collected(
        {
            "name": request.name,
            "age": request.age,
            "living": request.living,
            "conditions": request.conditions,
            "medications": request.medications,
            "allergies": request.allergies,
            "healthConcerns": request.healthConcerns,
            "mobility": request.mobility,
            "emergencyContact": request.emergencyContact,
        }
    )
    display_name = collected.get("name") or "老人"
    profile = map_collected_to_profile(collected)
    summary = build_collected_summary(collected)
    user_id = db.upsert_user(
        request.user_id,
        display_name=display_name,
        collected=collected,
        profile=profile,
        summary=summary,
    )
    return _user_record(db, user_id)


@app.get(
    "/api/users",
    response_model=list[UserRecord],
    dependencies=[Depends(require_api_key)],
)
def list_users(limit: int = 50, db: Database = Depends(get_db)):
    return [_user_record(db, row["id"]) for row in db.list_users(limit=limit)]


@app.get(
    "/api/users/{user_id}",
    response_model=UserRecord,
    dependencies=[Depends(require_api_key)],
)
def get_user(user_id: str, db: Database = Depends(get_db)):
    return _user_record(db, user_id)


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


@app.post(
    "/api/audit/{audit_id}/feedback",
    response_model=FeedbackResponse,
    dependencies=[Depends(require_api_key), Depends(rate_limit)],
)
def submit_feedback(
    audit_id: int,
    request: FeedbackRequest,
    db: Database = Depends(get_db),
):
    """记录老人/家属对某条回复的人工评价（1 赞 / -1 踩 / 0 撤销）。

    只写反馈字段，不改写对话正文与安全判定——反馈是旁路信号，
    用于事后挖失败集，任何自动改进都必须走回归门禁，不能反过来改安全层。
    """
    if not db.set_feedback(audit_id, request.rating, request.note):
        raise HTTPException(status_code=404, detail="审计记录不存在")
    log_event(
        chat_logger, "chat_feedback", audit_id=audit_id, rating=request.rating
    )
    return FeedbackResponse(updated=True, audit_id=audit_id, feedback=request.rating)


@app.post(
    "/api/audit/{audit_id}/interrupt",
    response_model=InterruptResponse,
    dependencies=[Depends(require_api_key), Depends(rate_limit)],
)
def submit_interrupt(audit_id: int, db: Database = Depends(get_db)):
    """记录老人打断了这条回复的播报（没听完就问下一句）。"""
    if not db.mark_interrupt(audit_id):
        raise HTTPException(status_code=404, detail="审计记录不存在")
    return InterruptResponse(updated=True, audit_id=audit_id)


@app.get("/api/cache/stats", dependencies=[Depends(require_api_key)])
def cache_stats(cache: TTLCache = Depends(get_cache)):
    return {"enabled": settings.cache_enabled, **cache.stats()}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
