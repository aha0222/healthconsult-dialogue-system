"""markers.py 单元测试：标记解析与本地兜底分级。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.markers import infer_risk_local, parse_marker


def test_parse_marker_trailing():
    reply, risk = parse_marker("您记下来带给医生看。[RISK:R1]")
    assert risk == "R1"
    assert reply == "您记下来带给医生看。"


def test_parse_marker_trailing_with_newline():
    reply, risk = parse_marker("您先别开门，马上打110。\n[SITUATION:S0]")
    assert risk == "S0"
    assert "SITUATION" not in reply


def test_parse_marker_any_position():
    reply, risk = parse_marker("前面 [MENTAL:M0] 后面还有话。")
    assert risk == "M0"
    assert "MENTAL" not in reply


def test_parse_marker_missing():
    reply, risk = parse_marker("就是一句普通的话，没有标记。")
    assert risk is None
    assert reply == "就是一句普通的话，没有标记。"


def test_infer_risk_emergency():
    assert infer_risk_local("胸口闷得慌，喘不上气，后背也疼") == "R3"
    assert infer_risk_local("半边身子突然麻了，嘴也歪了") == "R3"


def test_infer_risk_mental():
    assert infer_risk_local("活着没意思，不想活了") == "M0"


def test_infer_risk_safety():
    assert infer_risk_local("有人敲门说是查水表的") == "S0"
    assert infer_risk_local("家里闻到煤气味了") == "S1"
    assert infer_risk_local("说我中奖了要我转账") == "S2"


def test_infer_risk_daily():
    assert infer_risk_local("今天天气不错") == "R0"
