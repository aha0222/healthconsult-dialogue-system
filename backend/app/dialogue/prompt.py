"""System prompt 组装：载入 skill 规范并叠加人格覆盖。

- 基础规范来自 skills/healthconsult-assistant-skill/SKILL.md
- 人格覆盖来自该 skill 的 docs/personalities.md（四版人格）
- 典型低风险场景可选用「精简 prompt」：只保留规范性小节，去掉静态正/反示例，
  由运行时检索到的真实语料替代示例，从而在保证安全约束的前提下减少 token。
"""

import re
from functools import lru_cache

from ..paths import SKILL_MD
from .taxonomy import RISK_LABELS, SCENE_LABELS

DEFAULT_PERSONALITY = "温婉邻居型"

# 精简 prompt 保留的 SKILL.md 小节编号（去掉示例 10/11、验证 12、生产架构 14 等）
COMPACT_SECTION_NUMBERS = frozenset({1, 2, 3, 4, 5, 6, 8, 9, 13, 15})

_SECTION_RE = re.compile(r"^##\s+(\d+)\.", re.M)

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
    "COMPACT_SECTION_NUMBERS",
    "load_skill_prompt",
    "load_compact_prompt",
    "build_examples_block",
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


@lru_cache(maxsize=1)
def load_compact_prompt() -> str:
    """从 SKILL.md 抽取规范性小节，组成精简 prompt（去掉示例等非规范内容）。

    以 SKILL.md 为单一事实源，按二级标题 `## N.` 切分，只保留
    `COMPACT_SECTION_NUMBERS` 中的小节；解析不出小节时回退完整文本。
    """
    full = load_skill_prompt()
    matches = list(_SECTION_RE.finditer(full))
    if not matches:
        return full
    blocks = []
    for index, match in enumerate(matches):
        number = int(match.group(1))
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(full)
        if number in COMPACT_SECTION_NUMBERS:
            blocks.append(full[start:end].rstrip())
    return "\n\n".join(blocks) if blocks else full


def build_examples_block(items, max_reply_chars: int = 220) -> str:
    """把检索到的相似语料（含回复）拼成可供 LLM 参考的少数样本块。"""
    lines = []
    for item in items or []:
        user = (getattr(item, "user", "") or "").strip()
        assistant = (getattr(item, "assistant", "") or "").strip()
        if not user or not assistant:
            continue
        if len(assistant) > max_reply_chars:
            assistant = assistant[:max_reply_chars].rstrip() + "…"
        lines.append(f"老人说：{user}\n小暖答：{assistant}")
    if not lines:
        return ""
    header = "【相似真实回复参考（仅借鉴语气与做法，禁止照搬内容或据此诊断开药）】"
    return "\n\n" + header + "\n" + "\n\n".join(lines)


def normalize_personality(personality: str) -> str:
    """未知人格回退到默认人格。"""
    if personality in PERSONALITY_OVERLAYS:
        return personality
    return DEFAULT_PERSONALITY


def build_system_prompt(
    personality: str = DEFAULT_PERSONALITY,
    compact: bool = False,
    examples_block: str = "",
) -> str:
    """组装 system prompt =（完整/精简）SKILL.md + 人格覆盖 + 可选相似样例。"""
    personality = normalize_personality(personality)
    base = load_compact_prompt() if compact else load_skill_prompt()
    return base + PERSONALITY_OVERLAYS[personality] + (examples_block or "")


def risk_label(risk: str) -> str:
    """风险等级 → 中文标签。"""
    return RISK_LABELS.get(risk, risk or "")
