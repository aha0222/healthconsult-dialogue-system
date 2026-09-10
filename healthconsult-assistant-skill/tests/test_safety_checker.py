"""
safety_checker.py 单元测试

用法：
    pytest tests/test_safety_checker.py
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "scripts"))

from safety_checker import (
    check_reply,
    clean_negations,
    validate_sample,
    _extract_fields,
)

REDLINE_PATH = SCRIPT_DIR / "redline_cases.jsonl"


def load_redline_cases():
    cases = []
    with open(REDLINE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


@pytest.mark.parametrize("case", load_redline_cases(), ids=lambda c: c["sample_id"])
def test_redline_cases(case):
    """红线测试集：正例应全部通过，反例必须出现 fatal"""
    _, violations = validate_sample(case, mode="generated_sft")
    fatal = [reason for severity, reason in violations if severity == "fatal"]
    is_pass = len(fatal) == 0

    assert is_pass == case["should_pass"], (
        f"{case['sample_id']}: expected pass={case['should_pass']}, got pass={is_pass}, "
        f"fatal={fatal}"
    )

    if not case["should_pass"]:
        for expected in case["expected_reasons"]:
            assert any(expected in reason for reason in fatal), (
                f"{case['sample_id']}: expected to find '{expected}' in fatal violations, "
                f"got {fatal}"
            )


def test_check_reply_detects_forbidden():
    """基本禁止话术检测"""
    assert check_reply("我给你开药") == ["禁止话术: 开药(我给你开药)"]
    assert check_reply("你自己加半片试试") == ["禁止话术: 引导加药(你自己加)"]


def test_check_reply_ignores_negated_forbidden():
    """否定句式中的安全警告不应被误杀"""
    assert check_reply("不要自己加药") == []
    assert check_reply("千万别停掉试试") == []
    assert check_reply("不要躺一会儿看看") == []


def test_clean_negations():
    """否定过滤替换正确"""
    cleaned = clean_negations("不要自己加药，您要去问医生")
    assert "自己加药" not in cleaned
    assert "[安全警告已过滤]" in cleaned


def test_extract_fields_from_various_formats():
    """_extract_fields 兼容多种字段格式"""
    sample1 = {
        "sample_id": "s1",
        "user": "u1",
        "assistant": "a1",
        "sub_scene": "血压",
        "risk_level": "R1",
    }
    assert _extract_fields(sample1) == ("s1", "u1", "a1", "血压", "R1")

    sample2 = {
        "id": "s2",
        "user_input": "u2",
        "reply": "a2",
        "metadata": {"scene": "跌倒", "risk_level": "R2b"},
    }
    assert _extract_fields(sample2) == ("s2", "u2", "a2", "跌倒", "R2b")

    sample3 = {
        "messages": [
            {"role": "user", "content": "u3"},
            {"role": "assistant", "content": "a3"},
        ]
    }
    assert _extract_fields(sample3) == ("", "u3", "a3", "", "")


def test_scene_marker_missing_is_fatal_in_generated():
    """generated_sft 模式下缺少末尾场景标记必须报 fatal"""
    sample = {
        "sample_id": "marker_missing_generated",
        "user": "你好",
        "assistant": "您好，请按时服药，有不适及时就医。",
    }
    _, violations = validate_sample(sample, mode="generated_sft")
    assert any(sev == "fatal" and reason == "missing_scene_marker" for sev, reason in violations)


def test_scene_marker_missing_is_warning_in_source():
    """source_sample 模式下缺少末尾场景标记只报 warning"""
    sample = {
        "sample_id": "marker_missing_source",
        "user": "你好",
        "assistant": "您好，请按时服药，有不适及时就医。",
    }
    _, violations = validate_sample(sample, mode="source_sample")
    assert any(sev == "warning" and reason == "missing_scene_marker" for sev, reason in violations)
    assert not any(sev == "fatal" for sev, reason in violations)


def test_valid_scene_marker_passes():
    """带合法末尾场景标记的回复不应报缺失"""
    sample = {
        "sample_id": "marker_valid",
        "user": "血压有点高",
        "assistant": "您固定早晚各量一次，把数值记下来带给医生看。[RISK:R1]",
    }
    _, violations = validate_sample(sample, mode="generated_sft")
    assert not any(reason == "missing_scene_marker" for _, reason in violations)


def test_stray_scene_marker_detected():
    """正文中残留标记片段必须报 fatal"""
    sample = {
        "sample_id": "marker_stray",
        "user": "你好",
        "assistant": "前面说了 [RISK:R1] 后面还有话。\n[RISK:R0]",
    }
    _, violations = validate_sample(sample, mode="generated_sft")
    assert any(reason == "stray_scene_marker" for _, reason in violations)


def test_placeholder_detection():
    """占位符必须报 fatal"""
    sample = {
        "sample_id": "placeholder_test",
        "user": "我血压高",
        "assistant": "您好[姓名]，请记录血压。",
    }
    _, violations = validate_sample(sample, mode="generated_sft")
    assert any(reason == "placeholder_hit" for _, reason in violations)


def test_prompt_leak_detection():
    """prompt 泄露必须报 fatal"""
    sample = {
        "sample_id": "leak_test",
        "user": "你好",
        "assistant": "你是一个小暖健康助手。",
    }
    _, violations = validate_sample(sample, mode="generated_sft")
    assert any(reason == "prompt_leak" for _, reason in violations)
