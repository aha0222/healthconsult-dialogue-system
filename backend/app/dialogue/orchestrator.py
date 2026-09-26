"""对话编排：分类预判 → 检索语料 → 调 LLM → 解析标记 → 安全兜底。

一次对话的流程：
    用户输入
      -> classifier 关键词快路径预判 (risk, scenes)（无歧义时 0 token）
      -> 非高风险时用 Retriever + Reranker 取 Top-N 相似语料作参考样例；
         典型低风险（R0/R1）改用精简 prompt，减少 system prompt token
      -> 组装 system prompt（完整/精简 SKILL.md + 人格覆盖 + 样例）
      -> 按预判风险路由模型（高风险走强模型）
      -> 调用 LLM（要求末尾带场景标记）
      -> 解析并剥离标记，得到 (回复正文, 风险等级)
      -> safety_checker.check_reply 兜底快检
      -> 高风险时可选 semantic_checker 语义复核
      -> 命中硬红线时，替换为按风险等级预置的安全话术
      -> 返回结构化结果

高风险（R3/R2b）始终使用完整 SKILL.md + 强模型，且不注入检索样例，保证安全优先。
"""

import logging
from dataclasses import replace

from ..config import Settings, get_settings
from ..safety.safety_checker import (
    check_reply,
    detect_forbidden_literal,
    detect_internal_leak_literal,
    has_internal_leak,
)
from .classifier import SceneRiskClassifier
from .llm_client import LLMClient
from .markers import infer_risk_local, infer_tags_local, parse_marker
from .prompt import (
    DEFAULT_PERSONALITY,
    build_examples_block,
    build_system_prompt,
    normalize_personality,
    risk_label,
)
from .reranker import rerank
from .retriever import Retriever, build_embedder, load_corpus
from .routing import select_model
from .taxonomy import HIGH_RISKS, SCENE_LABELS, canonical_risk, normalize_scenes

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

# 流式链路：这些高风险输入改为全量缓冲后校验再下发，杜绝不安全文本流式泄露
HIGH_RISK_STREAM = {"R3", "R2b"}


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
    def __init__(
        self,
        llm=None,
        settings: Settings | None = None,
        semantic_checker=None,
        classifier=None,
    ):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)
        self.semantic_checker = semantic_checker
        self._classifier = classifier
        self._exemplar_retriever = None

    @property
    def classifier(self) -> SceneRiskClassifier:
        if self._classifier is None:
            self._classifier = SceneRiskClassifier(settings=self.settings)
        return self._classifier

    def _runtime_classification(self, message: str):
        """运行时快路径预判；关闭或异常时返回 None，不影响主链路。"""
        if not self.settings.runtime_classifier:
            return None
        try:
            # 实时链路不额外调 LLM：只用关键词/规则快路径，歧义交给主 LLM。
            return self.classifier.classify(message, allow_llm=False)
        except Exception as exc:  # 分类失败不阻断对话
            logger.warning("运行时分级失败，已跳过: %s", exc)
            return None

    def _get_exemplar_retriever(self):
        """惰性构建带回复的检索语料；不可用时返回 None。"""
        if self._exemplar_retriever is None:
            path = self.settings.exemplar_corpus_path
            corpus = load_corpus(path) if path else []
            if not corpus:
                self._exemplar_retriever = False
            else:
                embed_settings = replace(
                    self.settings,
                    embedding_backend=self.settings.exemplar_embedding_backend,
                )
                embedder = build_embedder(embed_settings)
                self._exemplar_retriever = Retriever(corpus, embedder, cache_dir=None)
        return self._exemplar_retriever or None

    def _retrieve_examples(self, message: str, classification, top_n=None) -> list:
        """Retriever 召回 Top-K → Reranker 精排 Top-N（只保留带回复的条目）。"""
        if not self.settings.runtime_retrieval:
            return []
        retriever = self._get_exemplar_retriever()
        if retriever is None or not retriever.corpus:
            return []
        try:
            candidates = retriever.retrieve(message, top_k=self.settings.retriever_top_k)
            ranked = rerank(
                message,
                candidates,
                top_n=top_n or self.settings.reranker_top_n,
                risk=(classification.risk if classification else None),
                scenes=(classification.scenes if classification else None),
            )
        except Exception as exc:  # 检索失败不影响对话
            logger.warning("相似语料检索失败，已跳过: %s", exc)
            return []
        return [item for item, _ in ranked if getattr(item, "assistant", "")]

    def _compose_system_prompt(self, message: str, personality: str, classification) -> str:
        """按预判结果决定 prompt 形态：高风险完整、典型低风险精简、并注入样例。"""
        risk = classification.risk if classification else None
        is_high = bool(classification and classification.ambiguous) or (
            risk in HIGH_RISKS if risk else False
        )
        examples = []
        if classification is not None and not is_high:
            examples = self._retrieve_examples(message, classification)
        compact = bool(
            self.settings.prompt_compact
            and classification is not None
            and not is_high
            and risk in ("R0", "R1")
        )
        examples_block = build_examples_block(examples) if examples else ""
        return build_system_prompt(
            personality, compact=compact, examples_block=examples_block
        )

    def _build_messages(
        self,
        message: str,
        personality: str,
        history,
        memory_block=None,
        system_prompt=None,
    ):
        if system_prompt is None:
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
        classification = self._runtime_classification(message)
        system_prompt = self._compose_system_prompt(message, personality, classification)
        messages = self._build_messages(
            message, personality, history, memory_block, system_prompt
        )
        model = select_model(
            message,
            self.settings,
            risk=(classification.risk if classification else None),
        )
        raw_reply = self.llm.chat(messages, model=model)
        return self._finalize(
            raw_reply, message, personality, model, pre_classification=classification
        )

    def respond_stream(
        self,
        message: str,
        personality: str = DEFAULT_PERSONALITY,
        history=None,
        memory_block=None,
    ):
        """流式处理：逐段 yield ("delta", 文本)，最后 yield ("done", 结果字典)。

        安全策略按输入风险分流：
        - 高风险（R3/R2b）输入：全量缓冲，完整校验后再回传，全程不吐 delta，
          命中红线时 done.reply 为保底话术，杜绝不安全文本逐字下发。
        - 低风险输入：保留流式实时性，但对已缓冲内容做增量硬红线守护，
          一旦命中立即停止下发并兜底。
        结尾仍做标记解析与安全兜底；客户端以 done.reply 覆盖已显示正文。
        """
        personality = normalize_personality(personality)
        classification = self._runtime_classification(message)
        system_prompt = self._compose_system_prompt(message, personality, classification)
        messages = self._build_messages(
            message, personality, history, memory_block, system_prompt
        )
        cls_risk = classification.risk if classification else None
        model = select_model(message, self.settings, risk=cls_risk)

        pre_risk = infer_risk_local(message)
        _, pre_scenes = infer_tags_local(message)

        # 高风险输入：先收齐、校验，再一次性回传，牺牲实时性换取零泄漏。
        # 分类器与本地关键词任一判为高风险都走缓冲，避免漏网。
        if pre_risk in HIGH_RISK_STREAM or cls_risk in HIGH_RISK_STREAM:
            buffer = ""
            for chunk in self.llm.chat_stream(messages, model=model):
                buffer += chunk
            result = self._finalize(
                buffer, message, personality, model, pre_classification=classification
            )
            yield "done", result
            return

        buffer = ""
        emitted = 0
        for chunk in self.llm.chat_stream(messages, model=model):
            buffer += chunk
            # 增量守护：对已缓冲前缀做禁止话术 + 内部规则泄露的轻量检测。
            if detect_forbidden_literal(buffer) or detect_internal_leak_literal(buffer):
                result = dict(
                    self._finalize(
                        buffer,
                        message,
                        personality,
                        model,
                        pre_classification=classification,
                    )
                )
                if not result["fallback_used"]:
                    result["fallback_used"] = True
                    result["violations"] = list(result.get("violations") or []) + [
                        "stream_safety_guard"
                    ]
                    if detect_internal_leak_literal(buffer):
                        result["reply"] = LEAK_DEFLECTION
                    else:
                        result["reply"] = safe_fallback(pre_risk, pre_scenes)
                yield "done", result
                return
            safe = len(buffer) - MARKER_HOLDBACK
            if safe > emitted:
                yield "delta", buffer[emitted:safe]
                emitted = safe

        result = self._finalize(
            buffer, message, personality, model, pre_classification=classification
        )

        if not result["fallback_used"]:
            remaining = result["reply"][emitted:]
            if remaining:
                yield "delta", remaining

        yield "done", result

    def _finalize(
        self,
        raw_reply: str,
        message: str,
        personality: str,
        model: str | None = None,
        pre_classification=None,
    ) -> dict:
        """解析标记 -> 安全检查 -> 必要时兜底，返回结构化结果。"""
        reply, risk, scenes = parse_marker(raw_reply)

        violations = []
        if risk is None:
            if pre_classification is not None and pre_classification.risk:
                risk = pre_classification.risk
                scenes = scenes or list(pre_classification.scenes)
            else:
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
