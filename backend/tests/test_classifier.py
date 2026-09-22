"""classifier.py 单元测试：快路径、歧义判定与 LLM 兜底（fake LLM，不联网）。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.dialogue.classifier import (
    SceneRiskClassifier,
    _implied_risk,
    build_classifier_prompt,
    parse_classification,
)
from backend.app.dialogue.retriever import CorpusItem, HashEmbedder, Retriever


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, temperature=None, max_tokens=None, model=None):
        self.calls += 1
        return self.reply


def _classifier(reply='{"risk":"R1","scenes":["M1"],"confidence":0.9}', **settings_kwargs):
    retriever = Retriever([], HashEmbedder(), None)
    return SceneRiskClassifier(
        llm=FakeLLM(reply),
        settings=Settings(**settings_kwargs),
        retriever=retriever,
    )


def test_fast_path_medication_example():
    classifier = _classifier()
    result = classifier.classify(
        "这两天总是忘记吃药，有时候一天吃两次怎么办？", allow_llm=False
    )
    assert result.risk == "R1"
    assert result.scenes == ["S2"]
    assert result.ambiguous is False
    assert result.source == "keyword"


def test_no_scene_marks_ambiguous():
    classifier = _classifier()
    result = classifier.classify("心里堵得慌，也不知道为啥", allow_llm=False)
    assert result.ambiguous is True
    assert "no_scene_match" in result.reasons


def test_emergency_is_confident():
    classifier = _classifier()
    result = classifier.classify("胸口闷得慌，喘不上气，后背也疼", allow_llm=False)
    assert result.risk == "R3"
    assert result.scenes[0] == "E1"
    assert result.ambiguous is False


def test_llm_fallback_used_when_ambiguous():
    classifier = _classifier()
    result = classifier.classify("心里堵得慌，也不知道为啥", allow_llm=True)
    assert result.source == "llm"
    assert result.risk == "R1"
    assert result.scenes == ["M1"]
    assert "llm_fallback" in result.reasons


def test_llm_fallback_disabled_returns_fast():
    classifier = _classifier(classifier_llm_fallback=False)
    result = classifier.classify("心里堵得慌，也不知道为啥", allow_llm=True)
    assert result.source == "keyword"


def test_llm_unparsed_falls_back_to_fast():
    classifier = _classifier(reply="抱歉，我无法判断。")
    result = classifier.classify("心里堵得慌", allow_llm=True)
    assert result.source == "keyword"
    assert "llm_unparsed" in result.reasons


def test_force_llm_on_confident_input():
    classifier = _classifier(reply='{"risk":"R2a","scenes":["S2"],"confidence":0.8}')
    result = classifier.classify("我想停药", allow_llm=True, force_llm=True)
    assert result.source == "llm"
    assert result.risk == "R2a"


def test_parse_classification_variants():
    assert parse_classification('```json\n{"risk":"r2a","scenes":["s2","S2"]}\n```') == (
        "R2a",
        ["S2"],
        0.7,
    )
    assert parse_classification("不是 JSON") is None
    assert parse_classification('{"risk":"bad","scenes":["S1"]}') is None


def test_implied_risk():
    assert _implied_risk(["S2"]) == "R1"
    assert _implied_risk(["E1"]) == "R2b"
    assert _implied_risk(["M2"]) == "R3"
    assert _implied_risk(["L1"]) == "R0"


def test_prompt_includes_examples():
    item = CorpusItem(id="C001", user="血压高", risk="R1", scenes=["S3"])
    prompt = build_classifier_prompt("血压有点高", [(item, 0.9)])
    assert "血压高" in prompt
    assert "风险 R1" in prompt
