"""双维度标记解析与本地兜底分级。

LLM 正常会在回复末尾输出 `[RISK:Rx]` 与若干 `[SCENE:xx]`；本模块负责解析并剥离；
若 LLM 漏标，则用本地关键词做一次保守分级，返回 (风险等级, 场景列表)。
"""

import re

from ..safety.safety_checker import detect_scenes
from .taxonomy import (
    MAX_SCENES,
    extract_tags,
    strip_tags,
    tags_to_markers,
)

# 本地兜底分级关键词
_R3_KEYWORDS = [
    "喘不上气", "呼吸困难", "说胡话", "意识不清", "叫不醒",
    "心梗", "中风", "卒中", "大出血", "胸痛", "半边", "嘴歪",
    "晕倒", "晕厥", "压榨", "吐血",
]
_R2B_KEYWORDS = [
    "咳血", "便血", "黑便", "摔倒", "摔了", "不能站",
    "视力突然", "发热三天", "高烧不退",
]
_R2A_KEYWORDS = [
    "换药", "停药", "加药", "减药", "换那个", "换一种", "换别的",
    "幻觉", "黑影", "人影", "不认识人", "找不到家",
]
_R1_KEYWORDS = [
    "血压", "血糖", "药", "睡不着", "便秘", "头晕", "胃口",
    "腿疼", "腰酸", "失眠", "心慌",
    # 情绪困扰（M1 场景中需要专业关注的一类）
    "没人管我", "被抛弃", "没人要", "没有意义", "难受想哭",
    "天天一个人", "情绪低落", "提不起兴趣", "孤单",
]
_CHEST_EMERGENCY_RE = re.compile(r'胸[口闷疼痛慌紧]')
_CHEST_COMPANION_RE = re.compile(r'后背|肩|臂|喘|汗|冷|压')
_STROKE_RE = re.compile(r'嘴.{0,3}[歪斜]|口.{0,3}[歪斜]|说话含糊|口齿不清|半边|一侧.{0,4}[麻无力动]')


def _contains_any(text: str, keywords) -> bool:
    return any(k in text for k in keywords)


def _is_emergency(text: str) -> bool:
    """是否属于需要立即 120 的极高危急症（区别于 R2b 的紧急就医）。"""
    if _contains_any(text, _R3_KEYWORDS):
        return True
    if _CHEST_EMERGENCY_RE.search(text) and _CHEST_COMPANION_RE.search(text):
        return True
    if _STROKE_RE.search(text):
        return True
    return False


def parse_marker(raw: str):
    """从 LLM 原始回复中剥离双维度标记。

    返回 (回复正文, 风险等级或 None, 场景列表)。风险取首个合法 RISK，
    场景收集全部合法 SCENE（去重、最多 MAX_SCENES 个）。
    """
    raw = (raw or "").strip()
    risk, scenes = extract_tags(raw)
    if risk is None and not scenes:
        return raw, None, []
    return strip_tags(raw), risk, scenes[:MAX_SCENES]


def append_marker(content: str, risk, scenes=None) -> str:
    """给正文补上双维度标记；已有合法 RISK 标记则不重复追加。"""
    content = content or ""
    if parse_marker(content)[1] is not None:
        return content
    markers = tags_to_markers(risk, scenes)
    if not markers:
        return content
    return f"{content}\n\n" + "\n".join(markers)


def infer_tags_local(user_text: str):
    """LLM 漏标时的本地兜底分级（保守，仅供参考），返回 (风险等级, 场景列表)。"""
    text = user_text or ""
    scenes = detect_scenes(text)

    risk = "R0"
    if any(scene in scenes for scene in ("M2", "N1", "N2")) or _is_emergency(text):
        risk = "R3"
    elif "E1" in scenes or "N3" in scenes or _contains_any(text, _R2B_KEYWORDS):
        risk = "R2b"
    elif _contains_any(text, _R2A_KEYWORDS):
        risk = "R2a"
    elif _contains_any(text, _R1_KEYWORDS):
        risk = "R1"

    if not scenes:
        scenes = ["S1"] if risk != "R0" else ["X1"]

    return risk, scenes[:MAX_SCENES]


def infer_risk_local(user_text: str) -> str:
    """本地兜底分级：仅返回风险等级（路由/评测复用）。"""
    return infer_tags_local(user_text)[0]
