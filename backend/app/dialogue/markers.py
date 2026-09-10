"""场景/风险标记解析与本地兜底分级。

LLM 正常会在回复末尾输出 [RISK:R1] / [SITUATION:S0] / [MENTAL:M0] / [OTHER:X]，
本模块负责剥离标记；若 LLM 漏标，则用本地关键词做一次保守分级。
"""

import re

from ..safety.safety_checker import (
    has_s_class_indicators,
    has_m_class_indicators,
)

# 末尾优先：命中结尾的合法标记
SCENE_MARKER_END_RE = re.compile(
    r'\[(?:SITUATION:(S[0-2])|MENTAL:(M[0-1])|RISK:(R[0-3][ab]?)|OTHER:(X))\]\s*$'
)

# 任意位置兜底：正文里出现的第一处合法标记
SCENE_MARKER_ANY_RE = re.compile(
    r'\[(?:SITUATION:(S[0-2])|MENTAL:(M[0-1])|RISK:(R[0-3][ab]?)|OTHER:(X))\]'
)

_S1_KEYWORDS = [
    "起火", "着火", "冒烟", "浓烟", "煤气", "燃气", "天然气",
    "漏电", "漏水", "火警", "119",
]
_S2_KEYWORDS = [
    "诈骗", "中奖", "公检法", "转账", "冒充", "可疑链接", "扫码", "汇款",
]
_M0_KEYWORDS = [
    "不想活", "死了算了", "活着没意思", "想死", "自伤", "自杀",
    "绝望", "没希望", "撑不下去", "走不下去", "熬不下去",
]
_EMERGENCY_R3 = [
    "喘不上气", "呼吸困难", "说胡话", "意识不清", "叫不醒",
    "心梗", "中风", "卒中", "大出血",
]
_R2B_KEYWORDS = [
    "咳血", "便血", "黑便", "吐血", "摔倒", "摔了", "不能站",
    "视力突然", "发热三天", "高烧不退",
]
_R2A_KEYWORDS = [
    "换药", "停药", "加药", "减药", "幻觉", "黑影", "人影",
    "不认识人", "找不到家",
]
_R1_KEYWORDS = [
    "血压", "血糖", "药", "睡不着", "便秘", "头晕", "胃口",
    "腿疼", "腰酸", "失眠", "心慌",
]


def _contains_any(text: str, keywords) -> bool:
    return any(k in text for k in keywords)


def parse_marker(raw: str):
    """从 LLM 原始回复中剥离场景标记。

    返回 (回复正文, 风险等级或 None)。末尾标记优先，任意位置兜底。
    """
    raw = (raw or "").strip()
    m = SCENE_MARKER_END_RE.search(raw) or SCENE_MARKER_ANY_RE.search(raw)
    if not m:
        return raw, None
    risk = m.group(1) or m.group(2) or m.group(3) or m.group(4)
    reply = (raw[: m.start()] + raw[m.end():]).strip()
    return reply, risk


def infer_risk_local(user_text: str) -> str:
    """LLM 漏标时的本地兜底分级（保守，仅供参考）。"""
    text = user_text or ""

    if has_m_class_indicators(text):
        return "M0" if _contains_any(text, _M0_KEYWORDS) else "M1"

    if has_s_class_indicators(text):
        if _contains_any(text, _S1_KEYWORDS):
            return "S1"
        if _contains_any(text, _S2_KEYWORDS):
            return "S2"
        return "S0"

    if re.search(r'胸[口闷疼痛慌紧]', text) and re.search(r'后背|肩|臂|喘|汗|冷|压', text):
        return "R3"
    if re.search(r'半边|一侧.*[麻无力动]|嘴[歪斜]|口[歪斜角]', text):
        return "R3"
    if _contains_any(text, _EMERGENCY_R3):
        return "R3"
    if _contains_any(text, _R2B_KEYWORDS):
        return "R2b"
    if _contains_any(text, _R2A_KEYWORDS):
        return "R2a"
    if re.search(r'胸[口闷疼痛慌紧]', text):
        return "R2a"
    if _contains_any(text, _R1_KEYWORDS):
        return "R1"
    return "R0"
