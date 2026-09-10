"""高危场景告警：记录告警日志，并可选推送到 webhook。

触发条件（满足其一）：
  - 回复命中硬红线（fallback_used=True）
  - 风险等级在 ALERT_RISKS 集合内（默认 R3/M0/S0）

推送失败不影响主链路。
"""

import logging

import httpx

from .config import get_settings
from .logging_config import log_event

alert_logger = logging.getLogger("xiaonuan.alert")


def should_alert(risk, fallback_used, settings=None) -> bool:
    settings = settings or get_settings()
    if fallback_used:
        return True
    return (risk or "").upper() in settings.alert_risks


def send_alert(record: dict, settings=None) -> bool:
    """按需发送告警。返回是否推送了 webhook。"""
    settings = settings or get_settings()
    risk = record.get("risk")
    fallback_used = bool(record.get("fallback_used"))
    if not should_alert(risk, fallback_used, settings):
        return False

    log_event(alert_logger, "high_risk_alert", **record)

    url = settings.alert_webhook_url
    if not url:
        return False
    try:
        httpx.post(url, json=record, timeout=3.0)
        return True
    except Exception as exc:  # 告警失败不能影响对话
        alert_logger.warning("告警 webhook 推送失败: %s", exc)
        return False
