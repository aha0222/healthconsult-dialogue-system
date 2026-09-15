"""System prompt 组装：载入 skill 规范并叠加人格覆盖。

- 基础规范来自 skills/healthconsult-assistant-skill/SKILL.md
- 人格覆盖来自该 skill 的 docs/personalities.md（四版人格）
"""

from ..paths import SKILL_MD
from .taxonomy import RISK_LABELS, SCENE_LABELS

DEFAULT_PERSONALITY = "温婉邻居型"

# 人格覆盖片段（追加在 SKILL.md 之后）
PERSONALITY_OVERLAYS = {
    "温婉邻居型": "",
    "贴心闺女型": (
        "\n\n【人格覆盖 · 贴心闺女型】切换为亲切软糯风格。短句为主、口语化，"
        "可偶尔用反问句“您说是不是嘛”。称呼：咱 / 您呀 / 您老人家。"
        "急症等严肃场景需收敛语气，保持稳重。"
    ),
    "素朴家常型": (
        "\n\n【人格覆盖 · 素朴家常型】切换为朴素接地气风格，用生活类比解释医学概念。"
        "称呼：您 / 老哥 / 老姐。不用叠词、不撒娇，短句、大白话。"
    ),
    "从容守护型": (
        "\n\n【人格覆盖 · 从容守护型】切换为淡定从容风格，条理清晰、信息密度高。"
        "用“第一/第二/第三”或“首先/然后/最后”结构化表达。称呼：您，不加修饰，"
        "不用叠词与语气词。"
    ),
}

PERSONALITY_DESCRIPTIONS = {
    "温婉邻居型": "温婉端庄，先安抚再建议",
    "贴心闺女型": "亲切软糯，情感模式",
    "素朴家常型": "朴素接地气，大白话",
    "从容守护型": "淡定从容，一二三讲清楚",
}

# 风险等级 / 场景类别 → 中文标签（与 taxonomy 保持一致）
__all__ = [
    "RISK_LABELS",
    "SCENE_LABELS",
    "DEFAULT_PERSONALITY",
    "PERSONALITY_OVERLAYS",
    "PERSONALITY_DESCRIPTIONS",
    "load_skill_prompt",
    "normalize_personality",
    "build_system_prompt",
    "risk_label",
]


def load_skill_prompt() -> str:
    """载入 skill 规范文本，供 system prompt 使用。"""
    if not SKILL_MD.is_file():
        raise FileNotFoundError(
            f"找不到 skill 规范文件：{SKILL_MD}。"
            "请确认仓库结构完整，或设置环境变量 XIAONUAN_SKILL_DIR。"
        )
    return SKILL_MD.read_text(encoding="utf-8")


def normalize_personality(personality: str) -> str:
    """未知人格回退到默认人格。"""
    if personality in PERSONALITY_OVERLAYS:
        return personality
    return DEFAULT_PERSONALITY


def build_system_prompt(personality: str = DEFAULT_PERSONALITY) -> str:
    """组装 system prompt = SKILL.md + 人格覆盖。"""
    personality = normalize_personality(personality)
    return load_skill_prompt() + PERSONALITY_OVERLAYS[personality]


def risk_label(risk: str) -> str:
    """风险等级 → 中文标签。"""
    return RISK_LABELS.get(risk, risk or "")
