"""后端运行时配置：从环境变量 / .env 读取，供 LLM 调用与 HTTP 服务使用。

约定（与 tools/ 保持一致）：
    DEEPSEEK_API_KEY      兼容 OpenAI 接口的 API Key
    DEEPSEEK_BASE_URL     接口地址，默认 https://api.deepseek.com/v1
    DEEPSEEK_MODEL        模型名，默认 deepseek-chat
    CORS_ORIGINS          允许的前端来源，逗号分隔，默认 *
    MAX_HISTORY           携带的历史消息条数上限，默认 10
    DB_PATH               SQLite 数据库文件路径
    BACKEND_API_KEY       后端访问密钥（为空则关闭鉴权，仅建议本地开发）
    RATE_LIMIT_PER_MINUTE 每 IP 每分钟请求上限（0 表示关闭），默认 60
    LOG_LEVEL             日志级别，默认 INFO
    LOG_FORMAT            日志格式 json / plain，默认 plain
    SEMANTIC_CHECK        是否开启高风险语义复核（1/true 开启），默认开启
    SEMANTIC_CHECK_RISKS  需要语义复核的风险等级，逗号分隔，默认 R3,M0,S0
    SEMANTIC_CHECK_FALLBACK 语义复核判定不安全时是否替换为安全话术，默认开启
    ALERT_RISKS           触发告警的风险等级，逗号分隔，默认 R3,M0,S0
    ALERT_WEBHOOK_URL     告警 webhook 地址（为空则仅记录日志）
    SUMMARY_ENABLED       是否开启长会话滚动摘要与画像（默认开启）
    SUMMARY_THRESHOLD     会话消息数超过该值才触发摘要，默认 20
    SUMMARY_KEEP_RECENT   摘要时保留最近多少条原文，默认 10
    MAX_MESSAGE_CHARS     单条用户消息最大字符数，默认 2000
    MAX_HISTORY_ITEMS     单次请求携带的历史消息条数上限，默认 20
    MAX_TOKENS            单次回复最大 token，默认 600
    TEMPERATURE           采样温度，默认 0.7
    ROUTING_ENABLED       是否按风险路由模型（默认关闭）
    MODEL_FAST            低风险用的便宜模型（默认同 DEEPSEEK_MODEL）
    MODEL_STRONG          高风险用的更强模型（默认同 DEEPSEEK_MODEL）
    CACHE_ENABLED         是否开启回复缓存（默认关闭）
    CACHE_TTL_SECONDS     缓存有效期秒数，默认 300
    CACHE_MAX_SIZE        缓存最大条目数，默认 256
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache

from .paths import REPO_ROOT

try:  # .env 为可选依赖，缺失时静默跳过
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

DEFAULT_DB_PATH = REPO_ROOT / "backend" / "data" / "sessions.db"
DEFAULT_SEMANTIC_RISKS = "R3,M0,S0"
DEFAULT_ALERT_RISKS = "R3,M0,S0"


def _split_origins(raw: str):
    return [item.strip() for item in raw.split(",") if item.strip()]


def _split_set(raw: str):
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _to_bool(raw: str, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    cors_origins: list = field(default_factory=lambda: ["*"])
    max_history: int = 10
    temperature: float = 0.7
    max_tokens: int = 600
    db_path: str = str(DEFAULT_DB_PATH)
    backend_api_key: str = ""
    rate_limit_per_minute: int = 60
    log_level: str = "INFO"
    log_format: str = "plain"
    semantic_check: bool = True
    semantic_check_risks: set = field(
        default_factory=lambda: _split_set(DEFAULT_SEMANTIC_RISKS)
    )
    semantic_check_fallback: bool = True
    alert_risks: set = field(default_factory=lambda: _split_set(DEFAULT_ALERT_RISKS))
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

    @property
    def fast_model(self) -> str:
        return self.model_fast or self.model

    @property
    def strong_model(self) -> str:
        return self.model_strong or self.model

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            cors_origins=_split_origins(os.environ.get("CORS_ORIGINS", "*")),
            max_history=int(os.environ.get("MAX_HISTORY", "10")),
            db_path=os.environ.get("DB_PATH", str(DEFAULT_DB_PATH)),
            backend_api_key=os.environ.get("BACKEND_API_KEY", ""),
            rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            log_format=os.environ.get("LOG_FORMAT", "plain"),
            semantic_check=_to_bool(os.environ.get("SEMANTIC_CHECK"), True),
            semantic_check_risks=_split_set(
                os.environ.get("SEMANTIC_CHECK_RISKS", DEFAULT_SEMANTIC_RISKS)
            ),
            semantic_check_fallback=_to_bool(
                os.environ.get("SEMANTIC_CHECK_FALLBACK"), True
            ),
            alert_risks=_split_set(os.environ.get("ALERT_RISKS", DEFAULT_ALERT_RISKS)),
            alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL", ""),
            summary_enabled=_to_bool(os.environ.get("SUMMARY_ENABLED"), True),
            summary_threshold=int(os.environ.get("SUMMARY_THRESHOLD", "20")),
            summary_keep_recent=int(os.environ.get("SUMMARY_KEEP_RECENT", "10")),
            max_message_chars=int(os.environ.get("MAX_MESSAGE_CHARS", "2000")),
            max_history_items=int(os.environ.get("MAX_HISTORY_ITEMS", "20")),
            temperature=float(os.environ.get("TEMPERATURE", "0.7")),
            max_tokens=int(os.environ.get("MAX_TOKENS", "600")),
            routing_enabled=_to_bool(os.environ.get("ROUTING_ENABLED"), False),
            model_fast=os.environ.get("MODEL_FAST", ""),
            model_strong=os.environ.get("MODEL_STRONG", ""),
            cache_enabled=_to_bool(os.environ.get("CACHE_ENABLED"), False),
            cache_ttl_seconds=int(os.environ.get("CACHE_TTL_SECONDS", "300")),
            cache_max_size=int(os.environ.get("CACHE_MAX_SIZE", "256")),
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.backend_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
