"""用户级档案与权威画像。

职责：
1. 把欢迎流程采集的 10 项资料映射为结构化画像（conditions / medications 直通，
   过敏史 / 健康困扰 / 行动自理进入 notes，姓名 / 年龄 / 居住 / 紧急联系信息独立存放）；
2. 对敏感字段（紧急联系电话等）脱敏，落库前统一处理；
3. 合并「用户自述画像」与「对话提炼画像」，冲突时以用户自述为准；
4. 生成唯一的「【已知信息】」块，供 system prompt 注入。
"""

import json
import re

from .sampling import redact

PROFILE_KEYS = ["conditions", "medications", "family", "preferences", "notes"]

PROFILE_LABELS = {
    "conditions": "慢病/健康状况",
    "medications": "用药",
    "family": "家属",
    "preferences": "偏好",
    "notes": "其他",
}

COLLECTED_FIELDS = [
    "name",
    "age",
    "living",
    "conditions",
    "medications",
    "allergies",
    "healthConcerns",
    "mobility",
    "emergencyContact",
    "emergencyPhone",
]

DEMOGRAPHIC_KEYS = ["name", "age", "living", "emergencyContact", "emergencyPhone"]

DEMOGRAPHIC_LABELS = {
    "name": "称呼",
    "age": "年龄",
    "living": "居住",
    "emergencyContact": "紧急联系人",
    "emergencyPhone": "紧急联系电话",
}

DISCLAIMER = (
    "以上信息来自用户自述与既往对话，未经医疗核实，仅供个性化陪伴参考；"
    "不得据此诊断、开药、调药或判断是否就医。"
)


def load_json_dict(raw) -> dict:
    """把数据库里的 JSON 字符串解析为 dict；失败返回空 dict。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def split_list(value) -> list[str]:
    """把自由文本切成列表（中英文标点、顿号、分号、换行均可分隔）。"""
    if not value:
        return []
    parts = re.split(r"[,，、;；/\\|\n]+", str(value))
    return [p.strip() for p in parts if p.strip()]


def mask_phone(value) -> str:
    """紧急联系电话脱敏：保留前 3 后 4 位，中间遮蔽。"""
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return text
    if len(digits) <= 7:
        return digits[0] + "****" + digits[-1] if len(digits) > 1 else "****"
    return digits[:3] + "****" + digits[-4:]


def redact_collected(collected) -> dict:
    """对采集字段统一脱敏：手机号遮蔽，其余字段清除手机号/身份证/银行卡。"""
    cleaned = {}
    for key in COLLECTED_FIELDS:
        value = (collected or {}).get(key)
        value = str(value).strip() if value is not None else ""
        cleaned[key] = mask_phone(value) if key == "emergencyPhone" else redact(value)
    return cleaned


def map_collected_to_profile(collected: dict) -> dict:
    """采集字段 → 结构化画像。"""
    profile = {key: [] for key in PROFILE_KEYS}
    profile["conditions"] = split_list(collected.get("conditions"))
    profile["medications"] = split_list(collected.get("medications"))
    notes = []
    if collected.get("allergies"):
        notes.append("过敏史：" + collected["allergies"])
    if collected.get("healthConcerns"):
        notes.append("健康困扰：" + collected["healthConcerns"])
    if collected.get("mobility"):
        notes.append("行动/自理：" + collected["mobility"])
    profile["notes"] = notes
    return profile


def _merge_list(primary, secondary) -> list[str]:
    """合并并去重，primary（用户自述）在前，保持权威顺序。"""
    merged = []
    seen = set()
    for value in list(primary or []) + list(secondary or []):
        text = str(value).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return merged


def merge_profiles(user_profile, memory_profile) -> dict:
    """合并用户自述画像与对话提炼画像；冲突以用户自述为准。"""
    user = user_profile or {}
    memory = memory_profile or {}
    merged = {}
    for key in PROFILE_KEYS:
        primary = user.get(key) if isinstance(user.get(key), list) else []
        secondary = memory.get(key) if isinstance(memory.get(key), list) else []
        merged[key] = _merge_list(primary, secondary)
    return merged


def build_collected_summary(collected: dict) -> str:
    """从采集字段生成用户级确定性摘要（用于展示与 API，不注入 prompt）。"""
    collected = collected or {}
    parts = []
    name = collected.get("name") or "这位老人"
    age = collected.get("age")
    living = collected.get("living")
    head = name
    if age:
        head += f"，{age} 岁"
    if living:
        head += f"，{living}"
    parts.append(head)

    conditions = split_list(collected.get("conditions"))
    if conditions:
        parts.append("基础病：" + "、".join(conditions))
    medications = split_list(collected.get("medications"))
    if medications:
        parts.append("用药：" + "、".join(medications))
    if collected.get("allergies"):
        parts.append("过敏史：" + collected["allergies"])
    return "；".join(parts) + "。"


def build_known_info(merged_profile, collected, conversation_summary=None) -> str:
    """生成唯一注入的「【已知信息】」块。

    同时包含用户基本信息（敏感字段已脱敏）、合并后的结构化画像、
    可选的历史对话摘要，以及「未经医疗核实」的免责声明。
    """
    collected = collected or {}
    merged = merged_profile or {}

    lines = []
    for key in DEMOGRAPHIC_KEYS:
        value = collected.get(key)
        if value:
            lines.append(f"- {DEMOGRAPHIC_LABELS.get(key, key)}：{value}")

    for key in PROFILE_KEYS:
        values = merged.get(key) or []
        if values:
            lines.append(f"- {PROFILE_LABELS[key]}：{'、'.join(values)}")

    if conversation_summary:
        lines.append(f"- 近期摘要：{conversation_summary}")

    if not lines:
        return ""
    return "【已知信息】\n" + "\n".join(lines) + "\n\n" + DISCLAIMER


def build_known_info_from_text(profile_text: str, conversation_summary=None) -> str:
    """兼容旧版自由文本 user_profile：作为单一已知信息注入。"""
    text = (profile_text or "").strip()
    if not text:
        return ""
    lines = ["【已知信息】", text]
    if conversation_summary:
        lines.append(f"近期摘要：{conversation_summary}")
    return "\n".join(lines) + "\n\n" + DISCLAIMER
