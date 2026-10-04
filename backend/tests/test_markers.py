"""markers.py 单元测试：双维度标记解析与本地兜底分级。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.markers import (
    append_marker,
    infer_risk_local,
    infer_tags_local,
    parse_marker,
)


def test_parse_marker_single_scene():
    reply, risk, scenes = parse_marker("您记下来带给医生看。[RISK:R1]\n[SCENE:S3]")
    assert risk == "R1"
    assert scenes == ["S3"]
    assert reply == "您记下来带给医生看。"


def test_parse_marker_multiple_scenes():
    reply, risk, scenes = parse_marker(
        "您先别开门，马上打110。\n[RISK:R3]\n[SCENE:N1]\n[SCENE:S1]"
    )
    assert risk == "R3"
    assert scenes == ["N1", "S1"]
    assert "RISK" not in reply and "SCENE" not in reply


def test_parse_marker_comma_separated_scene():
    reply, risk, scenes = parse_marker("您慢慢说。[RISK:R1][SCENE:S2,S3]")
    assert risk == "R1"
    assert scenes == ["S2", "S3"]


def test_parse_marker_missing():
    reply, risk, scenes = parse_marker("就是一句普通的话，没有标记。")
    assert risk is None
    assert scenes == []
    assert reply == "就是一句普通的话，没有标记。"


def test_parse_marker_strips_code_fence():
    """模型把整段回复包进 ``` 围栏时，围栏行不应出现在正文里。"""
    raw = "```\n咱们先坐下来歇一歇。[RISK:R0]\n[SCENE:S1]\n```"
    reply, risk, scenes = parse_marker(raw)
    assert risk == "R0"
    assert scenes == ["S1"]
    assert reply == "咱们先坐下来歇一歇。"
    assert "`" not in reply


def test_parse_marker_strips_trailing_fence_with_tags_outside():
    """正文后跟孤立围栏行、标记在围栏之后：正文干净且标记可解析。"""
    raw = "您先喝口温水。\n\n```\n\n\n```\n[RISK:R1]\n[SCENE:S2]"
    reply, risk, scenes = parse_marker(raw)
    assert risk == "R1"
    assert scenes == ["S2"]
    assert reply == "您先喝口温水。"


def test_parse_marker_backtick_wrapped_tags():
    """标记被模型写成 `[RISK:R1]` 时，剥标记须连反引号一起吃掉。"""
    raw = "慢慢说，别急。`[RISK:R1] [SCENE:M1]`"
    reply, risk, scenes = parse_marker(raw)
    assert risk == "R1"
    assert scenes == ["M1"]
    assert reply == "慢慢说，别急。"
    assert "`" not in reply


def test_parse_marker_inline_backtick_kept():
    """正文行内的零散反引号不属于围栏/标记残留，保留不动。"""
    reply, risk, scenes = parse_marker("这个叫 `地高辛` 的药。[RISK:R1]\n[SCENE:S2]")
    assert risk == "R1"
    assert reply == "这个叫 `地高辛` 的药。"


def test_infer_risk_emergency():
    assert infer_risk_local("胸口闷得慌，喘不上气，后背也疼") == "R3"
    assert infer_risk_local("半边身子突然麻了，嘴也歪了") == "R3"


def test_infer_tags_emergency():
    risk, scenes = infer_tags_local("胸口闷得慌，喘不上气，后背也疼")
    assert risk == "R3"
    assert scenes[0] == "E1"


def test_infer_risk_mental():
    assert infer_risk_local("活着没意思，不想活了") == "R3"
    risk, scenes = infer_tags_local("活着没意思，不想活了")
    assert risk == "R3"
    assert scenes[0] == "M2"


def test_infer_risk_safety():
    assert infer_risk_local("有人敲门说是查水表的") == "R3"
    assert infer_tags_local("有人敲门说是查水表的") == ("R3", ["N1"])
    assert infer_risk_local("家里闻到煤气味了") == "R3"
    assert infer_tags_local("家里闻到煤气味了") == ("R3", ["N2"])
    assert infer_risk_local("说我中奖了要我转账") == "R2b"
    assert infer_tags_local("说我中奖了要我转账") == ("R2b", ["N3"])


def test_infer_risk_daily():
    assert infer_risk_local("今天天气不错") == "R0"
    assert infer_tags_local("今天天气不错") == ("R0", ["X1"])


def test_infer_risk_medication():
    assert infer_risk_local("我想停药，不吃了行不行") == "R2a"
    assert "S2" in infer_tags_local("我想停药，不吃了行不行")[1]


def test_append_marker_adds_when_missing():
    result = append_marker("您记下来带给医生看。", "R1", ["S3"])
    assert result.endswith("[RISK:R1]\n[SCENE:S3]")
    reply, risk, scenes = parse_marker(result)
    assert risk == "R1"
    assert scenes == ["S3"]


def test_append_marker_does_not_duplicate():
    existing = "您记下来带给医生看。\n\n[RISK:R1]\n[SCENE:S3]"
    assert append_marker(existing, "R1", ["S3"]) == existing


def test_append_marker_without_risk_is_noop():
    assert append_marker("普通回复。", None) == "普通回复。"
