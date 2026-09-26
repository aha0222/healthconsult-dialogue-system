"""运行时接入测试：分类器预判 + Retriever/Reranker 样例 + 精简 prompt。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.dialogue.orchestrator import DialogueOrchestrator
from backend.app.dialogue.prompt import (
    build_examples_block,
    load_compact_prompt,
    load_skill_prompt,
)
from backend.app.dialogue.retriever import CorpusItem


class FakeLLM:
    def __init__(self, reply="好的。[RISK:R0]"):
        self.reply = reply
        self.last = None

    def chat(self, messages, **kwargs):
        self.last = messages
        return self.reply

    def chat_stream(self, messages, **kwargs):
        self.last = messages
        yield self.reply


def make_orch(reply="好的。[RISK:R0]", **settings_kwargs):
    settings_kwargs.setdefault("runtime_retrieval", True)
    settings_kwargs.setdefault("exemplar_embedding_backend", "hash")
    settings = Settings(**settings_kwargs)
    return DialogueOrchestrator(llm=FakeLLM(reply), settings=settings)


# ── prompt ─────────────────────────────────────────────────────

def test_compact_prompt_smaller_and_keeps_core_sections():
    full = load_skill_prompt()
    compact = load_compact_prompt()
    assert len(compact) < len(full)
    for heading in ("角色定位", "双维度标签体系", "核心安全规则", "回复格式要求", "身份守护"):
        assert heading in compact
    assert "## 10." not in compact and "## 11." not in compact


def test_build_examples_block():
    items = [
        CorpusItem(id="C1", user="血压有点高", assistant="您先记下来带给医生看。"),
        CorpusItem(id="C2", user="无回复", assistant=""),
    ]
    block = build_examples_block(items)
    assert "相似真实回复参考" in block
    assert "老人说：血压有点高" in block
    assert "您先记下来带给医生看。" in block
    assert "无回复" not in block
    assert build_examples_block([]) == ""


# ── 运行时样例注入 ─────────────────────────────────────────────

def test_low_risk_injects_exemplars_and_compact_prompt():
    orch = make_orch()
    orch.respond("血压这两天有点高")
    system_prompt = orch.llm.last[0]["content"]
    assert "相似真实回复参考" in system_prompt
    assert "老人说：" in system_prompt
    assert len(system_prompt) < len(load_skill_prompt())


def test_high_risk_uses_full_prompt_without_exemplars():
    orch = make_orch()
    orch.respond("胸口闷得慌，喘不上气，后背也疼")
    system_prompt = orch.llm.last[0]["content"]
    assert "相似真实回复参考" not in system_prompt
    assert load_skill_prompt() in system_prompt


def test_ambiguous_uses_full_prompt_without_exemplars():
    orch = make_orch()
    orch.respond("心里堵得慌，也不知道为啥")
    system_prompt = orch.llm.last[0]["content"]
    assert "相似真实回复参考" not in system_prompt


def test_exemplar_assistant_has_no_internal_tags():
    orch = make_orch()
    orch.respond("血压这两天有点高")
    system_prompt = orch.llm.last[0]["content"]
    example_block = system_prompt.split("相似真实回复参考", 1)[1]
    assert "[RISK:" not in example_block
    assert "[SCENE:" not in example_block


# ── 分类器预判驱动 ─────────────────────────────────────────────

def test_classifier_tags_used_when_reply_missing_marker():
    orch = make_orch(reply="您把血压记下来，带给医生看看。")
    result = orch.respond("我血压有点高")
    assert result["risk"] == "R1"
    assert result["scenes"] == ["S3"]
    assert "missing_scene_marker" in result["violations"]


def test_routing_uses_classifier_risk():
    high = make_orch(
        reply="请马上打120。[RISK:R3]",
        routing_enabled=True,
        model_fast="fast-x",
        model_strong="strong-x",
    )
    assert high.respond("胸口闷得慌，喘不上气")["model"] == "strong-x"

    low = make_orch(
        reply="好的。[RISK:R1]",
        routing_enabled=True,
        model_fast="fast-x",
        model_strong="strong-x",
    )
    assert low.respond("我血压有点高")["model"] == "fast-x"


def test_runtime_flags_can_disable_retrieval():
    orch = make_orch(runtime_retrieval=False)
    orch.respond("血压这两天有点高")
    system_prompt = orch.llm.last[0]["content"]
    assert "相似真实回复参考" not in system_prompt
