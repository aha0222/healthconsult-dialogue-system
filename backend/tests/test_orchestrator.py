"""orchestrator.py 单元测试：使用假 LLM，不发真实请求。"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.dialogue.llm_client import LLMError
from backend.app.dialogue.orchestrator import DialogueOrchestrator, safe_fallback


class FakeLLM:
    """固定回复或抛错的假客户端。"""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.last_messages = None

    def chat(self, messages, **kwargs):
        self.last_messages = messages
        if self.error:
            raise self.error
        return self.reply

    def chat_stream(self, messages, **kwargs):
        self.last_messages = messages
        if self.error:
            raise self.error
        text = self.reply or ""
        for i in range(0, len(text), 4):
            yield text[i : i + 4]


def make_orchestrator(reply=None, error=None):
    return DialogueOrchestrator(llm=FakeLLM(reply, error), settings=Settings())


class FakeSemanticChecker:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.called = 0

    def check(self, user_text, reply, risk):
        self.called += 1
        if self.error:
            raise self.error
        return self.result


def test_normal_reply_strips_marker():
    orch = make_orchestrator("您按时吃药，有不适及时联系医生。[RISK:R1]")
    result = orch.respond("我最近有点头晕")
    assert result["reply"] == "您按时吃药，有不适及时联系医生。"
    assert result["risk"] == "R1"
    assert result["fallback_used"] is False
    assert result["violations"] == []


def test_forbidden_reply_replaced_by_fallback():
    orch = make_orchestrator("药量你自己调，吃硝苯地平就行。[RISK:R2a]")
    result = orch.respond("邻居换药了，我能换吗")
    assert result["fallback_used"] is True
    assert result["reply"] == safe_fallback("R2a")
    assert result["violations"]


def test_missing_marker_inferred_without_fallback():
    orch = make_orchestrator("您把血压记下来，带给医生看看。")
    result = orch.respond("我血压有点高")
    assert result["risk"] == "R1"
    assert "missing_scene_marker" in result["violations"]
    assert result["fallback_used"] is False


def test_emergency_missing_escalation_replaced():
    orch = make_orchestrator("您先躺一会儿看看吧。[RISK:R3]")
    result = orch.respond("胸口闷得慌，喘不上气")
    assert result["fallback_used"] is True
    assert "120" in result["reply"]


def test_history_trimmed_and_system_first():
    orch = make_orchestrator("好的。[RISK:R0]")
    history = [{"role": "user", "content": f"消息{i}"} for i in range(30)]
    orch.respond("新消息", history=history)
    messages = orch.llm.last_messages
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "新消息"}
    # system + max_history(10) + 当前消息
    assert len(messages) == 1 + 10 + 1


def test_llm_error_propagates():
    orch = make_orchestrator(error=LLMError("boom"))
    with pytest.raises(LLMError, match="boom"):
        orch.respond("你好")


def test_personality_normalized_and_overlay_injected():
    orch = make_orchestrator("您好。[RISK:R0]")
    result = orch.respond("你好", personality="不存在的人格")
    assert result["personality"] == "温婉邻居型"

    orch2 = make_orchestrator("您好。[RISK:R0]")
    orch2.respond("你好", personality="从容守护型")
    system_prompt = orch2.llm.last_messages[0]["content"]
    assert "从容守护型" in system_prompt


def test_respond_stream_emits_deltas_then_done():
    orch = make_orchestrator("您记下来带给医生看。[RISK:R1]")
    events = list(orch.respond_stream("我血压有点高"))
    kinds = [kind for kind, _ in events]
    assert kinds[-1] == "done"

    text = "".join(payload for kind, payload in events if kind == "delta")
    assert "[RISK:R1]" not in text
    assert "医生" in text

    result = events[-1][1]
    assert result["reply"] == "您记下来带给医生看。"
    assert result["risk"] == "R1"
    assert result["fallback_used"] is False


def test_respond_stream_fallback_uses_done_reply():
    orch = make_orchestrator("药量你自己调。[RISK:R2a]")
    events = list(orch.respond_stream("我能自己加药吗"))
    result = events[-1][1]
    assert result["fallback_used"] is True
    assert result["reply"] == safe_fallback("R2a")


def _orch_with_semantic(reply, checker):
    return DialogueOrchestrator(
        llm=FakeLLM(reply), settings=Settings(), semantic_checker=checker
    )


def test_semantic_unsafe_triggers_fallback():
    checker = FakeSemanticChecker({"ok": False, "parsed": True, "issues": ["越界诊断"]})
    orch = _orch_with_semantic("请马上打120去医院。[RISK:R3]", checker)
    result = orch.respond("胸口疼")
    assert result["semantic_checked"] is True
    assert result["fallback_used"] is True
    assert result["reply"] == safe_fallback("R3")
    assert any(v.startswith("semantic:") for v in result["violations"])


def test_semantic_safe_keeps_reply():
    checker = FakeSemanticChecker({"ok": True, "parsed": True, "issues": []})
    orch = _orch_with_semantic("请马上打120去医院。[RISK:R3]", checker)
    result = orch.respond("胸口疼")
    assert result["semantic_checked"] is True
    assert result["fallback_used"] is False
    assert result["reply"] == "请马上打120去医院。"


def test_semantic_error_does_not_block():
    checker = FakeSemanticChecker(error=RuntimeError("boom"))
    orch = _orch_with_semantic("请马上打120去医院。[RISK:R3]", checker)
    result = orch.respond("胸口疼")
    assert result["fallback_used"] is False
    assert "semantic_check_error" in result["violations"]


def test_semantic_skipped_for_low_risk():
    checker = FakeSemanticChecker({"ok": False, "parsed": True, "issues": ["x"]})
    orch = _orch_with_semantic("我在听您说，您慢慢讲。[RISK:R0]", checker)
    result = orch.respond("今天天气不错")
    assert checker.called == 0
    assert result["semantic_checked"] is False


def test_memory_block_injected_into_system_prompt():
    orch = make_orchestrator("好的。[RISK:R0]")
    orch.respond("你好", memory_block="【历史摘要】\n老人有高血压。")
    system_prompt = orch.llm.last_messages[0]["content"]
    assert "历史摘要" in system_prompt
    assert "高血压" in system_prompt


def test_memory_block_absent_by_default():
    orch = make_orchestrator("好的。[RISK:R0]")
    orch.respond("你好")
    system_prompt = orch.llm.last_messages[0]["content"]
    assert "历史摘要" not in system_prompt
