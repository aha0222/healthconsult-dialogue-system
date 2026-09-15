"""分级标签体系（双维度）——单一事实源。

每条回复必须同时携带两个维度的标签：
  维度一 · 风险等级（唯一）：R3 / R2b / R2a / R1 / R0
  维度二 · 场景类别（可交叉，1~3 个）：S1-S4 / M1-M2 / L1-L4 / E1 / N1-N3 / X1-X2

线格式（置于回复正文末尾）：
    [RISK:R1]
    [SCENE:S2]
    [SCENE:S3]

本模块被 SKILL.md 之外的代码共同引用：markers / safety_checker /
orchestrator / sampling / tools / 前端标签表。
"""

# ── 维度一：风险等级（综合危害，含健康 + 人身/财产安全）────────────

RISK_LEVELS = ("R3", "R2b", "R2a", "R1", "R0")

RISK_ORDER = {"R0": 0, "R1": 1, "R2a": 2, "R2b": 3, "R3": 4}

RISK_LABELS = {
    "R3": "极高风险",
    "R2b": "高风险",
    "R2a": "中高风险",
    "R1": "中风险",
    "R0": "低风险",
}

RISK_DEFINITIONS = {
    "R3": "生命安全或人身安全直接受威胁（急症、心理危机、入室/火灾等）",
    "R2b": "可能导致健康损害或财产损失的高风险行为，须今天/明天处理（咳血/黑便/摔倒不能站、诈骗进行中）",
    "R2a": "需要专业介入但非紧急（自行调药/换药/停药、异常感知、慢病失控）",
    "R1": "需要专业指导但非紧急（症状持续、用药疑问、慢病指标波动、情绪困扰）",
    "R0": "日常健康咨询、生活方式建议与情感陪伴（饮食/运动/作息/社交/闲聊）",
}

# 高风险等级：触发模型路由到强模型、语义复核与告警
HIGH_RISKS = ("R3", "R2b")

# ── 维度二：场景类别（可交叉）────────────────────────────────────

SCENE_GROUPS = {
    "S": "身体状况",
    "M": "心理情绪",
    "L": "日常生活",
    "E": "紧急情况",
    "N": "人身环境安全",
    "X": "其他",
}

SCENES = (
    "S1", "S2", "S3", "S4",
    "M1", "M2",
    "L1", "L2", "L3", "L4",
    "E1",
    "N1", "N2", "N3",
    "X1", "X2",
)

SCENE_LABELS = {
    "S1": "症状咨询",
    "S2": "用药管理",
    "S3": "慢病管理",
    "S4": "就医引导",
    "M1": "情绪陪伴",
    "M2": "心理危机",
    "L1": "饮食营养",
    "L2": "运动康复",
    "L3": "作息睡眠",
    "L4": "社交活动",
    "E1": "急症识别",
    "N1": "人身安全",
    "N2": "环境安全",
    "N3": "诈骗财产",
    "X1": "闲聊",
    "X2": "系统功能",
}

SCENE_DEFINITIONS = {
    "S1": "描述身体不适、询问症状含义或应对方式",
    "S2": "询问药物用法、剂量、副作用、能否停换、漏服处理",
    "S3": "高血压、糖尿病等慢病的日常监测与生活方式调整",
    "S4": "是否需要就医、挂什么科、如何向医生描述病情",
    "M1": "孤独、焦虑、低落等情绪表达，需要共情陪伴",
    "M2": "自伤/自杀倾向、严重抑郁等需要紧急干预",
    "L1": "适合老年人的饮食建议与禁忌",
    "L2": "适合的运动方式、康复训练",
    "L3": "睡眠问题与作息调整",
    "L4": "社交建议、活动推荐",
    "E1": "胸痛、卒中、跌倒等急症的识别与应对",
    "N1": "敲门求救、尾随、入室威胁等人身安全",
    "N2": "起火、煤气泄漏、漏电漏水等环境安全",
    "N3": "诈骗电话、冒充公检法、可疑链接等财产风险",
    "X1": "与健康无关的日常闲聊",
    "X2": "对系统本身功能/身份的询问",
}

# 场景必需的应急要素：回复中必须命中其一，否则视为缺失（safety_checker 使用）
# 结构：scene -> (keyword_tuple, violation_reason)
SCENE_REQUIRED_ACTIONS = {
    "N1": (
        ("110", "报警", "锁门", "别开门", "不开门", "不要开门"),
        "safety_scene_missing_emergency_response",
    ),
    "N2": (
        ("119", "火警", "燃气", "消防", "报警"),
        "environment_scene_missing_emergency_response",
    ),
    "N3": (
        ("110", "报警", "转账", "扫码", "子女", "验证码"),
        "fraud_scene_missing_response",
    ),
    "M2": (
        ("热线", "心理", "医生", "医院", "陪伴", "我在", "听您说"),
        "mental_crisis_scene_missing_professional_guidance",
    ),
    "E1": (
        ("120", "急救", "急诊", "立刻就医", "马上就医", "赶紧去"),
        "emergency_scene_missing_escalation",
    ),
}

# ── 旧码 → 新双维度映射（一次性全量迁移用）────────────────────────

LEGACY_RISK_MAP = {
    "S0": ("R3", ["N1"]),
    "S1": ("R3", ["N2"]),
    "S2": ("R2b", ["N3"]),
    "M0": ("R3", ["M2"]),
    "M1": ("R1", ["M1"]),
    "R3": ("R3", ["E1"]),
    "R2b": ("R2b", ["E1"]),
    "R2a": ("R2a", ["S2"]),
    "R2": ("R2a", ["S2"]),
    "R1": ("R1", ["S1"]),
    "R0": ("R0", ["L1"]),
    "X": ("R0", ["X1"]),
}

# 旧标记前缀 → 旧码前缀
LEGACY_MARKER_PREFIXES = ("SITUATION:", "MENTAL:", "OTHER:")

# 示例数据里的英文/中文场景名 → 新场景（迁移用）
SCENE_ALIAS_MAP = {
    "abnormal_perception": "S1",
    "blood_sugar_recording": "S3",
    "daily_health": "L1",
    "diet_consultation": "L1",
    "emergency": "E1",
    "emotional_distress": "M1",
    "emotional_support": "M1",
    "environmental_safety": "N2",
    "family_caregiver": "S1",
    "fraud_prevention": "N3",
    "medication_boundary": "S2",
    "medication_error": "S2",
    "personal_safety": "N1",
    "psychological_crisis": "M2",
    "sleep": "L3",
    "急症高危": "E1",
    "用药边界": "S2",
    "血压血糖记录": "S3",
    "睡眠饮食安全": "L3",
    "异常认知": "S1",
    "养生生活方式": "L2",
    "诱导越界拒答": "S2",
}

MAX_SCENES = 3


# ── 规范化与校验 ────────────────────────────────────────────────

def canonical_risk(value) -> str:
    """规范风险等级：R2a/R2b 的 a/b 保持小写。非法值返回空串。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    canonical = raw[0].upper() + raw[1:]
    if len(canonical) == 3 and canonical[:2] == "R2" and canonical[2].lower() in ("a", "b"):
        canonical = "R2" + canonical[2].lower()
    if canonical in RISK_LEVELS:
        return canonical
    return ""


def canonical_scene(value) -> str:
    """规范场景代码：字母大写、数字保留。非法值返回空串。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    canonical = raw[0].upper() + raw[1:]
    return canonical if canonical in SCENES else ""


def normalize_scenes(values) -> list:
    """去重、规范、保序，过滤非法值。"""
    if values is None:
        return []
    if isinstance(values, str):
        values = [v for v in values.replace("，", ",").split(",")]
    seen = []
    for value in values:
        scene = canonical_scene(value)
        if scene and scene not in seen:
            seen.append(scene)
    return seen


def is_valid_risk(value) -> bool:
    return canonical_risk(value) != ""


def is_valid_scene(value) -> bool:
    return canonical_scene(value) != ""


def legacy_to_tags(code) -> tuple:
    """旧码 → (风险等级, [场景])。无法识别时回退 R0/X1。"""
    raw = str(code or "").strip()
    canonical = canonical_risk(raw)
    if canonical:
        return canonical, []
    upper = raw.upper()
    if upper in LEGACY_RISK_MAP:
        risk, scenes = LEGACY_RISK_MAP[upper]
        return risk, list(scenes)
    # 旧 S/M/X 码
    if upper.startswith("S") and upper in LEGACY_RISK_MAP:
        risk, scenes = LEGACY_RISK_MAP[upper]
        return risk, list(scenes)
    if upper == "X":
        return "R0", ["X1"]
    return "R0", ["X1"]


def scene_from_alias(value) -> str:
    """示例数据的场景名（英文/中文）→ 新场景代码。"""
    raw = str(value or "").strip()
    if raw in SCENE_ALIAS_MAP:
        return SCENE_ALIAS_MAP[raw]
    return canonical_scene(raw)


def max_risk(*risks) -> str:
    """取最高风险等级；空值忽略。"""
    best = ""
    for risk in risks:
        canonical = canonical_risk(risk)
        if canonical and (not best or RISK_ORDER[canonical] > RISK_ORDER[best]):
            best = canonical
    return best


# ── 标记解析 ────────────────────────────────────────────────────

import re as _re

RISK_RE = _re.compile(r"\[RISK:([^\]]+)\]", _re.IGNORECASE)
SCENE_RE = _re.compile(r"\[SCENE:([^\]]+)\]", _re.IGNORECASE)
MARKER_LIKE_RE = _re.compile(r"\[(?:RISK|SCENE)\b", _re.IGNORECASE)


def extract_tags(raw: str):
    """从文本中提取 (风险等级或 None, 场景列表)；不做剥离、不截断。"""
    text = raw or ""
    risk = None
    for match in RISK_RE.finditer(text):
        canonical = canonical_risk(match.group(1))
        if canonical:
            risk = canonical
            break

    scenes = []
    for match in SCENE_RE.finditer(text):
        for token in _re.split(r"[,\s，]+", match.group(1)):
            scene = canonical_scene(token)
            if scene and scene not in scenes:
                scenes.append(scene)
    return risk, scenes


def strip_tags(raw: str) -> str:
    """去掉文本中的所有双维度标记。"""
    return SCENE_RE.sub("", RISK_RE.sub("", raw or "")).strip()


def validate_tags(risk, scenes) -> list:
    """校验标签合法性，返回违规原因列表。"""
    issues = []
    canonical = canonical_risk(risk)
    if not canonical:
        issues.append("invalid_risk_tag")
    normalized = normalize_scenes(scenes)
    if not normalized:
        issues.append("missing_scene_tag")
    if len(normalized) > MAX_SCENES:
        issues.append("too_many_scene_tags")
    raw_count = len(scenes) if isinstance(scenes, (list, tuple)) else 0
    if raw_count and len(normalized) < raw_count:
        issues.append("duplicate_or_invalid_scene_tag")
    return issues


# ── 标记渲染 ────────────────────────────────────────────────────

def risk_marker(risk) -> str:
    canonical = canonical_risk(risk)
    return f"[RISK:{canonical}]" if canonical else ""


def scene_marker(scene) -> str:
    canonical = canonical_scene(scene)
    return f"[SCENE:{canonical}]" if canonical else ""


def tags_to_markers(risk, scenes=None) -> list:
    """返回标记字符串列表：[RISK] 在前，[SCENE] 依次在后。"""
    markers = []
    risk_token = risk_marker(risk)
    if risk_token:
        markers.append(risk_token)
    for scene in normalize_scenes(scenes):
        markers.append(scene_marker(scene))
    return markers


def format_tags(risk, scenes=None) -> str:
    """返回可直接追加到正文末尾的标记块（含前导换行）。"""
    markers = tags_to_markers(risk, scenes)
    if not markers:
        return ""
    return "\n\n" + "\n".join(markers)
