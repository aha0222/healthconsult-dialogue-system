"""对话系统后端入口（骨架）。

计划职责：
1. 载入 skill 规范（skills/healthconsult-assistant-skill/SKILL.md）作为 system prompt
2. 接收前端请求 -> 调用 LLM -> 解析回复末尾的场景标记
3. 用 backend.app.safety.safety_checker 对回复做兜底快检
4. 把「回复正文 + 风险等级」返回给前端

当前仅为占位骨架，对话编排尚未实现，见 backend/app/dialogue/。
"""

from .paths import SKILL_MD


def load_skill_prompt() -> str:
    """载入 skill 规范文本，供 system prompt 使用。"""
    return SKILL_MD.read_text(encoding="utf-8")


def handle_user_message(user_text: str) -> dict:
    """处理一条用户消息（占位实现）。"""
    raise NotImplementedError("对话编排尚未实现，见 backend/app/dialogue/orchestrator.py")
