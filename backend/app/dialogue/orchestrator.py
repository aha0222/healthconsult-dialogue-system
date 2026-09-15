"""对话编排：载入 skill → 调 LLM → 解析标记 → 安全兜底。

一次对话的流程：
    用户输入
      -> 组装 system prompt（SKILL.md + 人格覆盖）
      -> 调用 LLM（要求末尾带场景标记）
      -> 解析并剥离标记，得到 (回复正文, 风险等级)
      -> safety_checker.check_reply 兜底快检
      -> 高风险时可选 semantic_checker 语义复核
      -> 命中硬红线时，替换为按风险等级预置的安全话术
      -> 返回结构化结果
"""

import logging

from ..config import Settings, get_settings
from ..safety.safety_checker import check_reply, has_internal_leak
from .llm_client import LLMClient
from .markers import infer_tags_local, parse_marker
from .prompt import (
    DEFAULT_PERSONALITY,
    build_system_prompt,
    normalize_personality,
    risk_label,
)
from .routing import select_model
from .taxonomy import SCENE_LABELS, canonical_risk, normalize_scenes

logger = logging.getLogger("xiaonuan.dialogue")

# 命中硬红线时使用的安全兜底话术（按风险等级）
RISK_FALLBACKS = {
    "R3": (
        "您现在的症状像是急症，不能在家等。请马上打 120，"
        "或者让身边的人帮您叫急救。等待时保持坐位或半卧，别进食进水，"
        "把医保卡和常用药准备好。我在这儿陪着您。"
    ),
    "R2b": (
        "这个情况别拖，今天或明天一定要去医院。把症状开始的时间和表现记下来，"
        "带上正在吃的药，让家人陪您去。要是路上加重了，立刻打 120。"
    ),
    "R2a": (
        "您说的这个情况，我建议尽快安排一次就诊，别自己处理。"
        "把不舒服的时间和表现记下来，带上正在吃的药，去对应科室让医生看看。"
    ),
    "R1": (
        "您先别着急。这几天固定时间量一量、记下来，"
        "把数值和感受带给医生或药师看，让他们帮您判断。"
        "要是越来越不舒服，就及时联系医生。"
    ),
    "R0": (
        "我在听您说。您先别着急，把情况慢慢讲给我听，咱们一起想办法。"
        "有需要的话，记得联系家人或医生。"
    ),
}

# 场景专属兜底（同一风险等级下不同场景话术不同，优先于风险兜底）
SCENE_FALLBACKS = {
    "E1": RISK_FALLBACKS["R3"],
    "N1": (
        "您先别开门，也别出去，把门锁好。马上打 110 报警，"
        "告诉对方您已经报警了，然后到安全的地方等着。我在这儿陪着您，别慌。"
    ),
    "N2": (
        "您先别慌。如果闻到煤气味，赶紧打开窗户、关掉燃气总阀，"
        "不要开灯也不要打电话，先到屋外去，再打 119 或燃气公司电话。"
    ),
    "N3": (
        "这多半是骗局，您千万别转账、别扫码、别透露银行卡和验证码。"
        "先挂断，给子女打个电话确认一下，实在拿不准就打 110。"
    ),
    "M2": (
        "您愿意跟我说这些，我很心疼，也谢谢您信任我，您不是一个人。"
        "请您现在就拨打心理援助热线 400-161-9995，"
        "或者让家人陪您去心理科看看。我会一直在这儿听您说。"
    ),
    "M1": (
        "听您这么说，我心里也沉甸甸的。这些情绪不是您的错，也不丢人。"
        "咱们可以先去社区心理科聊聊，我陪您把想说的话一起记下来。"
    ),
}

GENERIC_FALLBACK = (
    "您先别着急，把情况慢慢讲给我听。"
    "要是身体不舒服，及时联系家人或医生，别自己硬扛。"
)

# 泄露内部规则/身份被套话时，用以保持在角色内的温和兜底话术
LEAK_DEFLECTION = (
    "这些是我心里琢磨的活儿，说出来怕您听着费劲。"
    "您就跟我说说最近哪儿不舒服、心里有啥放不下的，我好好陪您想想办法，成吗？"
)

# 流式输出时，末尾标记最长约 32 字符，留足余量避免标记中途闪现
MARKER_HOLDBACK = 48


def safe_fallback(risk, scenes=None) -> str:
    """按主场景/风险等级取安全兜底话术。"""
    for scene in normalize_scenes(scenes):
        if scene in SCENE_FALLBACKS:
            return SCENE_FALLBACKS[scene]
    canonical = canonical_risk(risk)
    if canonical in RISK_FALLBACKS:
        return RISK_FALLBACKS[canonical]
    if canonical:
        prefix = canonical[0].upper()
        if prefix == "R":
            return RISK_FALLBACKS["R1"]
    return GENERIC_FALLBACK


class DialogueOrchestrator:
    def __init__(self, llm=None, settings: Settings | None = None, semantic_checker=None):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)
        self.semantic_checker = semantic_checker

    def _build_messages(self, message: str, personality: str, history, memory_block=None):
        system_prompt = build_system_prompt(personality)
        if memory_block:
            system_prompt = f"{system_prompt}\n\n{memory_block}"
        trimmed = (history or [])[-self.settings.max_history :]
        messages = [{"role": "system", "content": system_prompt}]
        for item in trimmed:
            role = item.get("role")
            content = item.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})
        return messages

    def respond(
        self,
        message: str,
        personality: str = DEFAULT_PERSONALITY,
        history=None,
        memory_block=None,
    ) -> dict:
        """处理一条用户消息，返回结构化结果。"""
        personality = normalize_personality(personality)
        messages = self._build_messages(message, personality, history, memory_block)
        model = select_model(message, self.settings)
        raw_reply = self.llm.chat(messages, model=model)
        return self._finalize(raw_reply, message, personality, model)

    def respond_stream(
        self,
        message: str,
        personality: str = DEFAULT_PERSONALITY,
        history=None,
        memory_block=None,
    ):
        """流式处理：逐段 yield ("delta", 文本)，最后 yield ("done", 结果字典)。

        结尾仍会做标记解析与安全兜底；命中红线时 done.reply 为安全话术，
        客户端应以 done.reply 覆盖已显示的正文。
        """
        personality = normalize_personality(personality)
        messages = self._build_messages(message, personality, history, memory_block)
        model = select_model(message, self.settings)

        buffer = ""
        emitted = 0
        for chunk in self.llm.chat_stream(messages, model=model):
            buffer += chunk
            safe = len(buffer) - MARKER_HOLDBACK
            if safe > emitted:
                yield "delta", buffer[emitted:safe]
                emitted = safe

        result = self._finalize(buffer, message, personality, model)

        if not result["fallback_used"]:
            remaining = result["reply"][emitted:]
            if remaining:
                yield "delta", remaining

        yield "done", result

    def _finalize(
        self, raw_reply: str, message: str, personality: str, model: str | None = None
    ) -> dict:
        """解析标记 -> 安全检查 -> 必要时兜底，返回结构化结果。"""
        reply, risk, scenes = parse_marker(raw_reply)

        violations = []
        if risk is None:
            risk, inferred_scenes = infer_tags_local(message)
            scenes = scenes or inferred_scenes
            violations.append("missing_scene_marker")

        reply_violations = check_reply(reply, risk, scenes, message)
        violations.extend(reply_violations)

        # 只有命中硬红线（禁止话术 / 缺失紧急要素）才替换为安全话术；
        # 仅缺少标记属于软提示，不覆盖模型回复。
        fallback_used = bool(reply_violations)
        if fallback_used:
            reply = safe_fallback(risk, scenes)

        # 身份/内部规则泄露：不解释、不展示，改用角色内的温和兜底
        if not fallback_used and has_internal_leak(reply):
            violations.append("internal_leak")
            fallback_used = True
            reply = LEAK_DEFLECTION

        # 第三层：高风险场景的 LLM 语义复核（关键词漏网之鱼的兜底）
        semantic_checked = False
        if (
            not fallback_used
            and self.semantic_checker is not None
            and (risk or "").upper() in self.settings.semantic_check_risks
        ):
            try:
                review = self.semantic_checker.check(message, reply, risk, scenes)
                semantic_checked = True
                if not review.get("ok", True):
                    violations.extend(
                        f"semantic:{issue}" for issue in review.get("issues", [])
                    )
                    if self.settings.semantic_check_fallback:
                        fallback_used = True
                        reply = safe_fallback(risk, scenes)
                if not review.get("parsed", True):
                    violations.append("semantic_check_unparsed")
            except Exception as exc:  # 复核失败不阻断主链路
                logger.warning("语义复核失败: %s", exc)
                violations.append("semantic_check_error")

        return {
            "reply": reply,
            "risk": risk,
            "scenes": scenes,
            "risk_label": risk_label(risk),
            "scene_labels": [SCENE_LABELS.get(s, s) for s in scenes],
            "violations": violations,
            "fallback_used": fallback_used,
            "semantic_checked": semantic_checked,
            "personality": personality,
            "model": model or self.settings.model,
        }
