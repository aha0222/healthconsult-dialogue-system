"""后端运行时配置：从环境变量 / .env 读取，供 LLM 调用与 HTTP 服务使用。

约定（与 tools/ 保持一致）：
    DEEPSEEK_API_KEY      兼容 OpenAI 接口的 API Key
    DEEPSEEK_BASE_URL     接口地址，默认 https://api.deepseek.com
    DEEPSEEK_MODEL        模型名，默认 deepseek-flash
    CORS_ORIGINS          允许的前端来源，逗号分隔，默认 *
    MAX_HISTORY           携带的历史消息条数上限，默认 10
    DB_PATH               SQLite 数据库文件路径
    BACKEND_API_KEY       后端访问密钥（为空则关闭鉴权，仅建议本地开发）
    RATE_LIMIT_PER_MINUTE 每 IP 每分钟请求上限（0 表示关闭），默认 60
    LOG_LEVEL             日志级别，默认 INFO
    LOG_FORMAT            日志格式 json / plain，默认 plain
    SEMANTIC_CHECK        是否开启高风险语义复核（1/true 开启），默认开启
    SEMANTIC_CHECK_RISKS  需要语义复核的风险等级，逗号分隔，默认 R3,R2b
    SEMANTIC_CHECK_FALLBACK 语义复核判定不安全时是否替换为安全话术，默认开启
    ALERT_RISKS           触发告警的风险等级，逗号分隔，默认 R3,R2b
    ALERT_WEBHOOK_URL     告警 webhook 地址（为空则仅记录日志）
    SUMMARY_ENABLED       是否开启长会话滚动摘要与画像（默认开启）
    SUMMARY_THRESHOLD     会话消息数超过该值才触发摘要，默认 20
    SUMMARY_KEEP_RECENT   摘要时保留最近多少条原文，默认 10
    MAX_MESSAGE_CHARS     单条用户消息最大字符数，默认 2000
    MAX_HISTORY_ITEMS     单次请求携带的历史消息条数上限，默认 20
    MAX_TOKENS            单次回复最大 token，默认 600
    TEMPERATURE           采样温度，默认 0.7
    THINKING_ENABLED      是否开启 DeepSeek 思考模式（默认关闭，实时对话更快）
    ROUTING_ENABLED       是否按风险路由模型（默认关闭）
    MODEL_FAST            低风险用的便宜模型（默认同 DEEPSEEK_MODEL）
    MODEL_STRONG          高风险用的更强模型（默认同 DEEPSEEK_MODEL）
    CACHE_ENABLED         是否开启回复缓存（默认关闭）
    CACHE_TTL_SECONDS     缓存有效期秒数，默认 300
    CACHE_MAX_SIZE        缓存最大条目数，默认 256
    CLASSIFIER_LLM_FALLBACK  本地分类歧义时是否调 LLM 兜底（默认开启）
    RETRIEVER_TOP_K       Retriever 召回候选数，默认 10
    RERANKER_TOP_N        Reranker 精排后交给 LLM 的候选数，默认 3
    EMBEDDING_BACKEND     嵌入后端 local / api / hash，默认 local（不可用时回退 hash）
    EMBEDDING_MODEL       本地嵌入模型名，默认 BAAI/bge-small-zh-v1.5
    EMBEDDING_MODEL_PATH  本地嵌入模型目录（优先级高于 EMBEDDING_MODEL）
    EMBEDDING_BASE_URL    API 嵌入接口地址（EMBEDDING_BACKEND=api 时必填）
    EMBEDDING_API_KEY     API 嵌入接口密钥
    EMBEDDING_CACHE_DIR   向量缓存目录，默认 <repo>/.cache/embeddings
    CORPUS_PATH           标注语料 JSONL 路径，默认 backend/app/dialogue/corpus/scene_risk_corpus.jsonl
    RUNTIME_CLASSIFIER    运行时是否用分类器预判 risk/scenes（默认开启）
    RUNTIME_RETRIEVAL     运行时是否检索相似语料注入生成 prompt（默认开启）
    PROMPT_COMPACT        典型低风险是否用精简 prompt（默认开启，减少 token）
    EXEMPLAR_CORPUS_PATH  运行时检索用的带回复语料，默认 skills/.../corpus/v0.4.0_corpus_expanded.jsonl（1725 条 / 24 场景）
    EXEMPLAR_EMBEDDING_BACKEND 运行时检索的嵌入后端，默认 hash（零下载、可移植；可设 local/api）
    VOICE_ENABLED         是否启用语音能力（独立 voice_service），默认关闭
    OFFLINE_MODE          是否用本地/私有端点 LLM 替代云端，默认关闭（走云端）
    SPEAKER_DETECT_ENABLED 是否启用说话人角色识别（默认关闭，恒按老人处理）
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache

from .dialogue.taxonomy import canonical_risk
from .paths import REPO_ROOT

try:  # .env 为可选依赖，缺失时静默跳过
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

DEFAULT_DB_PATH = REPO_ROOT / "backend" / "data" / "sessions.db"
DEFAULT_SEMANTIC_RISKS = "R3,R2b"
DEFAULT_ALERT_RISKS = "R3,R2b"
# 显式本地来源：127.0.0.1:8000 / localhost:8000，以及 file:// 页面的 Origin（null）
DEFAULT_CORS_ORIGINS = "http://127.0.0.1:8000,http://localhost:8000,null"
DEFAULT_CORPUS_PATH = (
    REPO_ROOT / "backend" / "app" / "dialogue" / "corpus" / "scene_risk_corpus.jsonl"
)
DEFAULT_EMBEDDING_CACHE_DIR = REPO_ROOT / ".cache" / "embeddings"
DEFAULT_EXEMPLAR_CORPUS_PATH = (
    REPO_ROOT / "skills" / "healthconsult-assistant-skill"
    / "examples" / "corpus" / "v0.4.0_corpus_expanded.jsonl"
)


def _split_origins(raw: str):
    return [item.strip() for item in raw.split(",") if item.strip()]


def _split_set(raw: str):
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _split_set_multi(raw: str):
    """逗号分隔、保留原始大小写的集合（用于 API Key / 受信代理）。"""
    return {item.strip() for item in (raw or "").split(",") if item.strip()}


def _canonical_only(raw: str) -> set:
    """把逗号分隔的风险等级规范化为合法集合（保持 R2a/R2b 大小写）。"""
    return {
        canonical
        for canonical in (canonical_risk(item) for item in (raw or "").split(","))
        if canonical
    }


def _canonical_risk_set(raw: str, default: str) -> tuple[set, list]:
    """规范化逗号分隔的风险等级配置。

    非法等级（如历史遗留的 M0/S0）会被过滤并记录告警，同时回退到安全默认值，
    避免配置写错时静默缩小语义复核/告警范围。
    返回 (合法集合, 告警列表)。
    """
    tokens = [item.strip() for item in (raw or "").split(",") if item.strip()]
    valid = set()
    invalid = []
    for token in tokens:
        canonical = canonical_risk(token)
        if canonical:
            valid.add(canonical)
        else:
            invalid.append(token)

    if invalid:
        return _canonical_only(default), [
            f"非法风险等级配置 {invalid}，已回退安全默认 {default}"
        ]
    if not valid:
        return _canonical_only(default), []
    return valid, []


def _to_bool(raw: str, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    cors_origins: list = field(
        default_factory=lambda: _split_origins(DEFAULT_CORS_ORIGINS)
    )
    max_history: int = 10
    temperature: float = 0.7
    max_tokens: int = 600
    thinking_enabled: bool = False
    db_path: str = str(DEFAULT_DB_PATH)
    backend_api_key: str = ""
    rate_limit_per_minute: int = 60
    log_level: str = "INFO"
    log_format: str = "plain"
    semantic_check: bool = True
    semantic_check_risks: set = field(
        default_factory=lambda: _canonical_only(DEFAULT_SEMANTIC_RISKS)
    )
    semantic_check_fallback: bool = True
    alert_risks: set = field(default_factory=lambda: _canonical_only(DEFAULT_ALERT_RISKS))
    alert_webhook_url: str = ""
    summary_enabled: bool = True
    summary_threshold: int = 20
    summary_keep_recent: int = 10
    max_message_chars: int = 2000
    max_history_items: int = 20
    routing_enabled: bool = False
    model_fast: str = ""
    model_strong: str = ""
    cache_enabled: bool = False
    cache_ttl_seconds: int = 300
    cache_max_size: int = 256
    classifier_llm_fallback: bool = True
    retriever_top_k: int = 10
    reranker_top_n: int = 3
    embedding_backend: str = "local"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_model_path: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_cache_dir: str = str(DEFAULT_EMBEDDING_CACHE_DIR)
    corpus_path: str = str(DEFAULT_CORPUS_PATH)
    runtime_classifier: bool = True
    runtime_retrieval: bool = True
    prompt_compact: bool = True
    exemplar_corpus_path: str = str(DEFAULT_EXEMPLAR_CORPUS_PATH)
    exemplar_embedding_backend: str = "hash"
    config_warnings: list = field(default_factory=list)
    environment: str = "development"
    backend_api_keys: set = field(default_factory=set)
    trusted_proxies: set = field(default_factory=set)
    alert_webhook_redact: bool = True
    max_profile_chars: int = 1000
    # ── 第三阶段新增能力开关（默认全部关闭，保持现有行为不变）──────────
    # 语音能力（独立进程 voice_service，前端直连；关闭时系统退化为打字版）
    voice_enabled: bool = False
    # 离线/私有化：用本地或私有端点 LLM 替代云端（providers.get_llm 依据此切换）
    offline_mode: bool = False
    # 说话人角色识别（关闭时恒按"老人本人"处理，与现状一致）
    speaker_detect_enabled: bool = False

    @property
    def fast_model(self) -> str:
        return self.model_fast or self.model

    @property
    def strong_model(self) -> str:
        return self.model_strong or self.model

    @classmethod
    def from_env(cls) -> "Settings":
        semantic_risks, semantic_warnings = _canonical_risk_set(
            os.environ.get("SEMANTIC_CHECK_RISKS", DEFAULT_SEMANTIC_RISKS),
            DEFAULT_SEMANTIC_RISKS,
        )
        alert_risks, alert_warnings = _canonical_risk_set(
            os.environ.get("ALERT_RISKS", DEFAULT_ALERT_RISKS),
            DEFAULT_ALERT_RISKS,
        )
        return cls(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
            cors_origins=_split_origins(
                os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
            ),
            max_history=int(os.environ.get("MAX_HISTORY", "10")),
            db_path=os.environ.get("DB_PATH", str(DEFAULT_DB_PATH)),
            backend_api_key=os.environ.get("BACKEND_API_KEY", ""),
            rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            log_format=os.environ.get("LOG_FORMAT", "plain"),
            semantic_check=_to_bool(os.environ.get("SEMANTIC_CHECK"), True),
            semantic_check_risks=semantic_risks,
            semantic_check_fallback=_to_bool(
                os.environ.get("SEMANTIC_CHECK_FALLBACK"), True
            ),
            alert_risks=alert_risks,
            alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL", ""),
            summary_enabled=_to_bool(os.environ.get("SUMMARY_ENABLED"), True),
            summary_threshold=int(os.environ.get("SUMMARY_THRESHOLD", "20")),
            summary_keep_recent=int(os.environ.get("SUMMARY_KEEP_RECENT", "10")),
            max_message_chars=int(os.environ.get("MAX_MESSAGE_CHARS", "2000")),
            max_history_items=int(os.environ.get("MAX_HISTORY_ITEMS", "20")),
            temperature=float(os.environ.get("TEMPERATURE", "0.7")),
            max_tokens=int(os.environ.get("MAX_TOKENS", "600")),
            thinking_enabled=_to_bool(os.environ.get("THINKING_ENABLED"), False),
            routing_enabled=_to_bool(os.environ.get("ROUTING_ENABLED"), False),
            model_fast=os.environ.get("MODEL_FAST", ""),
            model_strong=os.environ.get("MODEL_STRONG", ""),
            cache_enabled=_to_bool(os.environ.get("CACHE_ENABLED"), False),
            cache_ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "300")),
            cache_max_size=int(os.environ.get("CACHE_MAX_SIZE", "256")),
            classifier_llm_fallback=_to_bool(
                os.environ.get("CLASSIFIER_LLM_FALLBACK"), True
            ),
            retriever_top_k=int(os.environ.get("RETRIEVER_TOP_K", "10")),
            reranker_top_n=int(os.environ.get("RERANKER_TOP_N", "3")),
            embedding_backend=os.environ.get("EMBEDDING_BACKEND", "local").strip().lower(),
            embedding_model=os.environ.get(
                "EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5"
            ),
            embedding_model_path=os.environ.get("EMBEDDING_MODEL_PATH", ""),
            embedding_base_url=os.environ.get("EMBEDDING_BASE_URL", ""),
            embedding_api_key=os.environ.get("EMBEDDING_API_KEY", ""),
            embedding_cache_dir=os.environ.get(
                "EMBEDDING_CACHE_DIR", str(DEFAULT_EMBEDDING_CACHE_DIR)
            ),
            corpus_path=os.environ.get("CORPUS_PATH", str(DEFAULT_CORPUS_PATH)),
            runtime_classifier=_to_bool(os.environ.get("RUNTIME_CLASSIFIER"), True),
            runtime_retrieval=_to_bool(os.environ.get("RUNTIME_RETRIEVAL"), True),
            prompt_compact=_to_bool(os.environ.get("PROMPT_COMPACT"), True),
            exemplar_corpus_path=os.environ.get(
                "EXEMPLAR_CORPUS_PATH", str(DEFAULT_EXEMPLAR_CORPUS_PATH)
            ),
            exemplar_embedding_backend=os.environ.get(
                "EXEMPLAR_EMBEDDING_BACKEND", "hash"
            ).strip().lower(),
            environment=os.environ.get("ENVIRONMENT", "development").strip().lower(),
            backend_api_keys=_split_set_multi(os.environ.get("BACKEND_API_KEYS", "")),
            trusted_proxies=_split_set_multi(os.environ.get("TRUSTED_PROXIES", "")),
            alert_webhook_redact=_to_bool(
                os.environ.get("ALERT_WEBHOOK_REDACT"), True
            ),
            max_profile_chars=int(os.environ.get("MAX_PROFILE_CHARS", "1000")),
            voice_enabled=_to_bool(os.environ.get("VOICE_ENABLED"), False),
            offline_mode=_to_bool(os.environ.get("OFFLINE_MODE"), False),
            speaker_detect_enabled=_to_bool(
                os.environ.get("SPEAKER_DETECT_ENABLED"), False
            ),
            config_warnings=semantic_warnings + alert_warnings,
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def all_backend_api_keys(self) -> set:
        """所有被接受的后端密钥（单 key 与多 key 兼容）。"""
        keys = set(self.backend_api_keys)
        if self.backend_api_key:
            keys.add(self.backend_api_key)
        return keys

    @property
    def auth_enabled(self) -> bool:
        return bool(self.all_backend_api_keys)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
