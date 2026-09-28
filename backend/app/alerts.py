"""高危场景告警：记录告警日志，并可选推送到 webhook。

触发条件（满足其一）：
  - 回复命中硬红线（fallback_used=True）
  - 风险等级在 ALERT_RISKS 集合内（默认 R3/R2b）

隐私：日志与（默认脱敏的）webhook 只保留结构化字段与原文字数/哈希指纹，
不落老人原话与回复正文；如确需原文可通过 ALERT_WEBHOOK_REDACT=0 关闭脱敏。
推送在后台线程执行，失败不影响主链路。
"""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from .config import get_settings
from .dialogue.taxonomy import canonical_risk
from .logging_config import log_event

alert_logger = logging.getLogger("xiaonuan.alert")

# 原始对话文本字段：不入日志、默认不入 webhook
SENSITIVE_FIELDS = ("user_message", "reply")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="alert")


def _fingerprint(text: str) -> str:
    """原文的短哈希指纹，用于关联/去重而不泄露内容。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def sanitize_alert(record: dict) -> dict:
    """移除原始对话文本，替换为长度与哈希指纹。"""
    safe = {key: value for key, value in record.items() if key not in SENSITIVE_FIELDS}
    for field in SENSITIVE_FIELDS:
        text = record.get(field)
        if text is None:
            continue
        text = str(text)
        safe[f"{field}_len"] = len(text)
        safe[f"{field}_sha256"] = _fingerprint(text)
    return safe


def should_alert(risk, fallback_used, settings=None) -> bool:
    settings = settings or get_settings()
    if fallback_used:
        return True
    return canonical_risk(risk) in settings.alert_risks


def _post_webhook(url: str, payload: dict) -> bool:
    try:
        httpx.post(url, json=payload, timeout=3.0)
        return True
    except Exception as exc:  # 告警失败不能影响对话
        alert_logger.warning("告警 webhook 推送失败: %s", exc)
        return False


def send_alert(record: dict, settings=None, background: bool = False) -> bool:
    """按需发送告警。返回是否推送了 webhook。

    background=True 时把网络推送丢到后台线程，避免阻塞对话响应。
    """
    settings = settings or get_settings()
    risk = record.get("risk")
    fallback_used = bool(record.get("fallback_used"))
    if not should_alert(risk, fallback_used, settings):
        return False

    safe = sanitize_alert(record)
    log_event(alert_logger, "high_risk_alert", **safe)

    url = settings.alert_webhook_url
    if not url:
        return False
    payload = safe if settings.alert_webhook_redact else dict(record)
    if background:
        _executor.submit(_post_webhook, url, payload)
        return True
    return _post_webhook(url, payload)
