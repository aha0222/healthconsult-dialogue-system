"""semantic_checker.py 单元测试。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.safety.semantic_checker import SemanticChecker, parse_semantic_result


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.last_messages = None

    def chat(self, messages, **kwargs):
        self.last_messages = messages
        return self.reply


def test_parse_semantic_pass():
    result = parse_semantic_result('{"violations": [], "pass": true}')
    assert result["ok"] is True
    assert result["parsed"] is True
    assert result["issues"] == []


def test_parse_semantic_fail():
    result = parse_semantic_result('{"violations": ["建议自行停药"], "pass": false}')
    assert result["ok"] is False
    assert result["issues"] == ["建议自行停药"]


def test_parse_semantic_embedded_json():
    raw = '审核结论如下：\n{"violations": [], "pass": true}\n谢谢。'
    result = parse_semantic_result(raw)
    assert result["ok"] is True
    assert result["parsed"] is True


def test_parse_semantic_invalid():
    result = parse_semantic_result("这不是 JSON")
    assert result["ok"] is True  # 解析失败不判定违规
    assert result["parsed"] is False
    assert result["issues"] == ["semantic_parse_error"]


def test_checker_builds_prompt_and_returns_result():
    llm = FakeLLM('{"violations": ["越界诊断"], "pass": false}')
    checker = SemanticChecker(llm=llm, settings=Settings())
    result = checker.check("我头晕", "你这是高血压。", "R1")
    assert result["ok"] is False
    assert "越界诊断" in result["issues"]
    prompt = llm.last_messages[0]["content"]
    assert "我头晕" in prompt
    assert "你这是高血压。" in prompt
