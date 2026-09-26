"""长期记忆：滚动摘要 + 老人画像。

当会话消息数超过阈值时，把"较旧"的对话交给 LLM 压缩成摘要与结构化画像，
之后构建 prompt 时注入摘要与画像，最近若干条仍保留原文，兼顾连贯与成本。

画像字段：conditions / medications / family / preferences / notes。
"""

import json
import logging
import re

from ..config import Settings, get_settings
from ..profile import (
    PROFILE_KEYS,
    PROFILE_LABELS,
    build_known_info,
    load_json_dict,
    merge_profiles,
)
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
        """返回 {history, memory_block, summary, profile, user_id}。"""
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

        user = (
            self.db.get_user(session.get("user_id")) if session.get("user_id") else None
        )
        if user:
            collected = load_json_dict(user.get("collected"))
            merged = merge_profiles(
                load_json_dict(user.get("profile")),
                load_json_dict(user.get("conversation_profile")),
            )
            conversation_summary = user.get("conversation_summary") or ""
            return {
                "history": history,
                "memory_block": build_known_info(merged, collected, conversation_summary),
                "summary": conversation_summary,
                "profile": merged,
                "user_id": user["id"],
            }

        return {
            "history": history,
            "memory_block": build_memory_block(summary, profile),
            "summary": summary,
            "profile": profile,
            "user_id": None,
        }

    def known_info_for_user(self, user) -> str:
        """为尚未产生对话记忆的新会话组装用户级「已知信息」。"""
        if not user:
            return ""
        collected = load_json_dict(user.get("collected"))
        merged = merge_profiles(
            load_json_dict(user.get("profile")),
            load_json_dict(user.get("conversation_profile")),
        )
        conversation_summary = user.get("conversation_summary") or ""
        return build_known_info(merged, collected, conversation_summary)

    def _refresh(self, session_id, session, all_messages):
        summary_upto = session.get("summary_upto") or 0
        keep = self.settings.summary_keep_recent
        older = all_messages[:-keep] if keep > 0 else all_messages
        to_summarize = [m for m in older if m["id"] > summary_upto]
        if not to_summarize:
            return

        user = (
            self.db.get_user(session.get("user_id")) if session.get("user_id") else None
        )
        if user:
            existing_summary = user.get("conversation_summary") or ""
            existing_profile = load_json_dict(user.get("conversation_profile"))
        else:
            existing_summary = session.get("summary") or ""
            existing_profile = load_profile(session.get("profile"))

        prompt = MEMORY_PROMPT.format(
            summary=existing_summary or "（无）",
            profile=(
                json.dumps(existing_profile, ensure_ascii=False)
                if existing_profile
                else "（无）"
            ),
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

        # 会话级滚动摘要（无绑定用户的会话据此注入；保持既有行为）
        self.db.update_memory(
            session_id,
            summary=parsed["summary"],
            profile=json.dumps(parsed["profile"], ensure_ascii=False),
            summary_upto=to_summarize[-1]["id"],
        )

        # 用户级：持久化对话提炼画像与对话摘要（自述画像存 profile，互不覆盖）
        if user:
            self.db.update_user_memory(
                user["id"],
                conversation_profile=json.dumps(parsed["profile"], ensure_ascii=False),
                conversation_summary=parsed["summary"],
            )
