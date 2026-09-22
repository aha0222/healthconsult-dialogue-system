"""场景 + 风险分级：本地关键词/规则快路径，歧义时 LLM 兜底。

设计目标（两步对话的第一步）：
    典型情况用关键词/规则在 <1ms 内直接定出 (风险等级, 场景类别)，0 token；
    只有"没匹配上、有歧义、或多场景冲突"时才调用一次 LLM 做兜底分类。
    兜底时用 Retriever 召回 + Reranker 精排出的 Top-N 相似语料作为少样本参考，
    而不是把整本规则/全库塞进 prompt。

第二步（按 risk + scenes 检索语料喂给回复生成）复用 retriever/reranker。
"""

import json
import logging
import re
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..safety.safety_checker import detect_scenes
from .llm_client import LLMClient
from .markers import infer_tags_local
from .reranker import rerank
from .retriever import Retriever, build_embedder, load_corpus
from .taxonomy import RISK_ORDER, canonical_risk, normalize_scenes

logger = logging.getLogger("xiaonuan.classifier")

# 场景隐含的最低风险（用于"风险与场景矛盾"检测；未列出的场景按 R0）
SCENE_MIN_RISK = {
    "E1": "R2b",
    "M2": "R3",
    "N1": "R3",
    "N2": "R3",
    "N3": "R2b",
    "S2": "R1",
}

# 单独出现不足以独立决策的泛化词
WEAK_KEYWORDS = ("药", "睡", "吃", "走", "量", "指标", "活动")

# 足够具体、可支撑快路径独立决策的信号词
STRONG_SIGNALS = (
    # 急症 / 心理危机 / 人身安全
    "胸痛", "胸口", "喘不上气", "呼吸困难", "说胡话", "意识不清", "心梗",
    "中风", "卒中", "半边", "嘴歪", "晕倒", "咳血", "便血", "黑便", "吐血",
    "摔倒", "骨折", "不想活", "想死", "自杀", "绝望", "撑不下去",
    "敲门", "尾随", "入室", "撬门", "煤气", "燃气", "起火", "着火", "漏电",
    "诈骗", "中奖", "转账", "公检法", "验证码",
    # 用药管理（含漏服/重复/记混）
    "换药", "停药", "加药", "减药", "剂量", "漏服", "忘吃", "忘记吃药",
    "忘吃药", "漏吃", "多吃", "吃两次", "一天吃两次", "重复吃", "吃重",
    "饭前", "饭后", "记混", "补药",
    # 症状 / 慢病 / 情绪
    "血压", "血糖", "糖尿病", "高血压", "血脂", "尿酸", "慢病",
    "头晕", "头疼", "便秘", "腿疼", "腰酸", "心慌", "不舒服", "难受",
    "乏力", "恶心", "膝盖", "人影", "幻觉", "黑影",
    "睡不着", "失眠", "早醒", "半夜醒", "没人管我", "被抛弃", "没人要",
    "没有意义", "难受想哭", "孤单", "情绪低落", "提不起兴趣",
)

_HIGH_RISK_SCENES = ("E1", "M2", "N1", "N2", "N3")

CLASSIFIER_PROMPT = """你是老年健康陪护对话的标签分类器。请判断用户输入的风险等级与场景类别。

【风险等级（唯一）】
R3 生命/人身安全直接受威胁（急症、自杀、入室、火灾煤气）
R2b 今天/明天必须处理（咳血黑便、摔倒不能站、发热三天不退、诈骗进行中）
R2a 需专业介入但非紧急（自行调药/换药/停药、异常感知、慢病失控）
R1 需专业指导但非紧急（症状持续、用药疑问、慢病指标波动、情绪困扰）
R0 日常咨询与陪伴（饮食、运动、作息、社交、闲聊）

【场景类别（1~3 个）】
S1症状 S2用药 S3慢病 S4就医 M1情绪陪伴 M2心理危机
L1饮食 L2运动 L3作息 L4社交 E1急症 N1人身安全 N2环境安全 N3诈骗 X1闲聊 X2系统功能

{examples}【用户输入】
{user}

只输出 JSON，不要解释：{{"risk": "R1", "scenes": ["S2"], "confidence": 0.8}}"""


@dataclass
class Classification:
    risk: str
    scenes: list = field(default_factory=list)
    source: str = "keyword"
    confidence: float = 1.0
    ambiguous: bool = False
    reasons: list = field(default_factory=list)
    candidates: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "risk": self.risk,
            "scenes": self.scenes,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "ambiguous": self.ambiguous,
            "reasons": self.reasons,
            "candidates": [
                {
                    "id": item.id,
                    "user": item.user,
                    "risk": item.risk,
                    "scenes": item.scenes,
                    "score": round(score, 4),
                }
                for item, score in self.candidates
            ],
        }


def parse_classification(raw: str):
    """解析 LLM 返回的 JSON；失败返回 None。"""
    raw = (raw or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except (ValueError, TypeError):
        return None
    risk = canonical_risk(data.get("risk"))
    scenes = normalize_scenes(data.get("scenes"))
    if not risk:
        return None
    try:
        confidence = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = min(1.0, max(0.0, confidence))
    return risk, scenes, confidence


def _implied_risk(scenes) -> str:
    best = "R0"
    for scene in scenes or []:
        candidate = SCENE_MIN_RISK.get(scene)
        if candidate and RISK_ORDER[candidate] > RISK_ORDER[best]:
            best = candidate
    return best


def _build_examples(candidates) -> str:
    if not candidates:
        return ""
    lines = ["【相似参考】"]
    for item, _score in candidates:
        scene_text = "、".join(item.scenes) if item.scenes else "—"
        risk_text = item.risk or "—"
        lines.append(f"输入：{item.user} → 风险 {risk_text}，场景 {scene_text}")
    return "\n".join(lines) + "\n\n"


def build_classifier_prompt(user_text: str, candidates=None) -> str:
    return CLASSIFIER_PROMPT.format(
        examples=_build_examples(candidates or []),
        user=(user_text or "")[:500],
    )


class SceneRiskClassifier:
    """关键词快路径 + LLM 兜底的分级器。"""

    def __init__(self, llm=None, settings: Settings | None = None, retriever=None):
        self.settings = settings or get_settings()
        self._llm = llm
        self._retriever = retriever

    @property
    def llm(self):
        if self._llm is None:
            self._llm = LLMClient(self.settings)
        return self._llm

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            corpus = load_corpus(self.settings.corpus_path)
            embedder = build_embedder(self.settings)
            self._retriever = Retriever(
                corpus, embedder, self.settings.embedding_cache_dir
            )
        return self._retriever

    # ── 快路径 ──────────────────────────────────────────────

    def _fast_path(self, text: str) -> Classification:
        text = text or ""
        scenes = normalize_scenes(detect_scenes(text))
        risk, inferred = infer_tags_local(text)
        if not scenes:
            scenes = normalize_scenes(inferred)

        reasons = []
        ambiguous = False

        if not scenes or scenes == ["X1"]:
            ambiguous = True
            reasons.append("no_scene_match")

        implied = _implied_risk(scenes)
        if RISK_ORDER[risk] < RISK_ORDER[implied]:
            ambiguous = True
            reasons.append("risk_scene_conflict")

        weak_hit = any(k in text for k in WEAK_KEYWORDS)
        strong_hit = any(k in text for k in STRONG_SIGNALS)
        if weak_hit and not strong_hit:
            ambiguous = True
            reasons.append("weak_keyword_only")

        high_hits = [s for s in scenes if s in _HIGH_RISK_SCENES]
        if len(high_hits) >= 2:
            ambiguous = True
            reasons.append("multi_high_risk_scene")

        confidence = 0.4 if ambiguous else 0.6
        if not ambiguous:
            if scenes and scenes != ["X1"]:
                confidence += 0.2
            if risk != "R0":
                confidence += 0.2
        return Classification(
            risk=risk,
            scenes=scenes,
            source="keyword",
            confidence=min(1.0, confidence),
            ambiguous=ambiguous,
            reasons=reasons,
        )

    # ── 检索（兜底分类 + 第二步语料复用）────────────────────

    def retrieve(self, text: str, top_n=None) -> list:
        """召回 + 精排，返回 [(CorpusItem, score)]；无检索器/空语料时返回 []。"""
        top_k = self.settings.retriever_top_k
        top_n = top_n or self.settings.reranker_top_n
        try:
            retriever = self.retriever
        except Exception as exc:  # 检索失败不影响分类主链路
            logger.warning("Retriever 初始化失败: %s", exc)
            return []
        if not retriever.corpus:
            return []
        candidates = retriever.retrieve(text, top_k=top_k)
        return rerank(text, candidates, top_n=top_n)

    # ── 兜底 ────────────────────────────────────────────────

    def _llm_fallback(self, text: str, fast: Classification) -> Classification:
        candidates = self.retrieve(text)
        prompt = build_classifier_prompt(text, candidates)
        try:
            raw = self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )
        except Exception as exc:  # 调用失败回退快路径
            logger.warning("分类兜底调用失败: %s", exc)
            fast.reasons = fast.reasons + ["llm_error"]
            return fast

        parsed = parse_classification(raw)
        if not parsed:
            fast.reasons = fast.reasons + ["llm_unparsed"]
            return fast

        risk, scenes, confidence = parsed
        if not scenes:
            scenes = fast.scenes
        return Classification(
            risk=risk,
            scenes=scenes,
            source="llm",
            confidence=confidence,
            ambiguous=False,
            reasons=list(fast.reasons) + ["llm_fallback"],
            candidates=candidates,
        )

    # ── 对外入口 ────────────────────────────────────────────

    def classify(
        self, text: str, allow_llm: bool = True, force_llm: bool = False
    ) -> Classification:
        """分级：快路径优先，歧义且允许时走 LLM 兜底；force_llm 时总是走 LLM。"""
        fast = self._fast_path(text)
        if not fast.ambiguous and not force_llm:
            return fast
        if not allow_llm or not self.settings.classifier_llm_fallback:
            return fast
        return self._llm_fallback(text, fast)
