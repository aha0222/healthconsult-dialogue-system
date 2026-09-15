"""长期记忆：滚动摘要 + 老人画像。

当会话消息数超过阈值时，把"较旧"的对话交给 LLM 压缩成摘要与结构化画像，
之后构建 prompt 时注入摘要与画像，最近若干条仍保留原文，兼顾连贯与成本。

画像字段：conditions / medications / family / preferences / notes。
"""

import json
import logging
import re

from ..config import Settings, get_settings
from ..storage import Database
from .llm_client import LLMClient
from .markers import append_marker

logger = logging.getLogger("xiaonuan.memory")

MEMORY_PROMPT = """你是一个长期记忆维护器，负责把老人与陪护助手的对话压缩成简明记忆。

请阅读【已有摘要】【已有画像】和【新增对话】，输出更新后的记忆，只输出 JSON：
{{"summary": "不超过200字的对话摘要", "profile": {{"conditions": [], "medications": [], "family": [], "preferences": [], "notes": []}}}}

要求：
- summary：按时间顺序概括重要事实与情绪，保留与健康安全相关的信息（症状、就医、用药、情绪变化）。
- profile：只记录对话中明确提到的信息，没有的字段留空数组，不要臆测、不要诊断。
- 只输出 JSON，不要多余文字。

【已有摘要】
{summary}

【已有画像】
{profile}

【新增对话】
{conversation}
"""

PROFILE_KEYS = ["conditions", "medications", "family", "preferences", "notes"]
PROFILE_LABELS = {
    "conditions": "慢病/健康状况",
    "medications": "用药",
    "family": "家属",
    "preferences": "偏好",
    "notes": "其他",
}
ROLE_LABELS = {"user": "老人", "assistant": "小暖"}


def parse_memory_result(raw: str):
    """解析记忆维护器的 JSON 输出；失败返回 None。"""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except (ValueError, TypeError):
        return None

    summary = str(data.get("summary", "") or "").strip()
    profile = data.get("profile")
    if not isinstance(profile, dict):
        profile = {}

    clean = {}
    for key in PROFILE_KEYS:
        values = profile.get(key)
        if isinstance(values, list):
            clean[key] = [str(v).strip() for v in values if str(v).strip()]
        else:
            clean[key] = []
    return {"summary": summary, "profile": clean}


def load_profile(raw):
    """把数据库里的 profile 字符串解析为 dict。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def build_memory_block(summary, profile) -> str:
    """把摘要与画像拼成注入 system prompt 的记忆块。"""
    parts = []
    if summary:
        parts.append("【历史摘要】\n" + summary)

    if profile:
        lines = []
        for key in PROFILE_KEYS:
            values = profile.get(key) or []
            if values:
                lines.append(f"- {PROFILE_LABELS[key]}：{'、'.join(values)}")
        if lines:
            parts.append("【已知信息】\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _format_conversation(messages) -> str:
    return "\n".join(
        f"{ROLE_LABELS.get(m['role'], m['role'])}：{m['content']}" for m in messages
    )


class MemoryManager:
    def __init__(self, db: Database, llm=None, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)

    def prepare(self, session_id: str) -> dict:
        """返回 {history, memory_block, summary, profile}。"""
        session = self.db.get_session(session_id) or {}
        all_messages = self.db.get_messages(session_id)

        if (
            self.settings.summary_enabled
            and len(all_messages) > self.settings.summary_threshold
        ):
            try:
                self._refresh(session_id, session, all_messages)
                session = self.db.get_session(session_id) or {}
            except Exception as exc:  # 摘要失败不阻断对话
                logger.warning("记忆摘要更新失败: %s", exc)

        summary = session.get("summary")
        profile = load_profile(session.get("profile"))
        # 历史里的助手回复补回场景标记，避免模型模仿"无标记"格式而漏标
        history = []
        for m in all_messages[-self.settings.max_history :]:
            content = m["content"]
            if m["role"] == "assistant":
                content = append_marker(content, m.get("risk"), m.get("scenes"))
            history.append({"role": m["role"], "content": content})
        return {
            "history": history,
            "memory_block": build_memory_block(summary, profile),
            "summary": summary,
            "profile": profile,
        }

    def _refresh(self, session_id, session, all_messages):
        summary_upto = session.get("summary_upto") or 0
        keep = self.settings.summary_keep_recent
        older = all_messages[:-keep] if keep > 0 else all_messages
        to_summarize = [m for m in older if m["id"] > summary_upto]
        if not to_summarize:
            return

        prompt = MEMORY_PROMPT.format(
            summary=session.get("summary") or "（无）",
            profile=session.get("profile") or "（无）",
            conversation=_format_conversation(to_summarize),
        )
        raw = self.llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500,
        )
        parsed = parse_memory_result(raw)
        if not parsed:
            return

        self.db.update_memory(
            session_id,
            summary=parsed["summary"],
            profile=json.dumps(parsed["profile"], ensure_ascii=False),
            summary_upto=to_summarize[-1]["id"],
        )
