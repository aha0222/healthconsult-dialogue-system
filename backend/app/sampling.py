"""在线采样：把生产环境审计记录导出为可离线质检的样本。

闭环：
    线上对话 -> audit_log -> export_samples() -> JSONL
    -> tools/validate_outputs.py --mode generated_sft -> 发现问题 -> 补规则/补用例

导出时会：
  1. 对手机号/身份证/银行卡做脱敏（可关闭）；
  2. 按 risk 还原场景标记，使样本符合 skill 输出契约，便于复用现有质检。
"""

import re

from .dialogue.markers import risk_to_marker
from .storage import Database

# --only-flagged 默认关注的高危等级
DEFAULT_FLAGGED_RISKS = ("R3", "M0", "S0")

# 顺序敏感：先身份证（18位）再银行卡（16-19位）再手机号（11位）
ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
BANK_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def redact(text: str) -> str:
    """脱敏手机号、身份证、银行卡号。"""
    if not text:
        return text
    text = ID_RE.sub("[身份证]", text)
    text = BANK_RE.sub("[银行卡]", text)
    text = PHONE_RE.sub("[手机号]", text)
    return text


def audit_to_sample(record: dict, redact_text: bool = True) -> dict:
    """把一条 audit_log 记录转成质检样本格式。"""
    user = record.get("user_message") or ""
    reply = record.get("reply") or ""
    if redact_text:
        user = redact(user)
        reply = redact(reply)

    risk = record.get("risk") or ""
    marker = risk_to_marker(risk)
    assistant = f"{reply}\n\n{marker}" if marker else reply

    return {
        "sample_id": f"online_{record.get('id')}",
        "user": user,
        "assistant": assistant,
        "risk_level": risk,
        "metadata": {
            "source": "online",
            "session_id": record.get("session_id"),
            "fallback_used": bool(record.get("fallback_used")),
            "violations": record.get("violations") or [],
            "model": record.get("model"),
            "created_at": record.get("created_at"),
        },
    }


def export_samples(
    db: Database,
    since=None,
    only_flagged: bool = False,
    risks=None,
    limit: int = 1000,
    redact_text: bool = True,
):
    """从审计表导出样本列表。only_flagged 默认附带高危等级。"""
    if only_flagged and risks is None:
        risks = DEFAULT_FLAGGED_RISKS
    records = db.list_audit(
        limit=limit, since=since, only_flagged=only_flagged, risks=risks
    )
    return [audit_to_sample(r, redact_text=redact_text) for r in records]
