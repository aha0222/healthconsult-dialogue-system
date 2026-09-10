"""routing.py 单元测试：按风险选择模型。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.dialogue.routing import select_model


def test_routing_disabled_uses_default_model():
    settings = Settings(model="base", model_fast="fast", model_strong="strong")
    assert select_model("胸口疼，喘不上气", settings) == "base"


def test_routing_high_risk_uses_strong():
    settings = Settings(
        routing_enabled=True, model="base", model_fast="fast", model_strong="strong"
    )
    assert select_model("胸口疼，喘不上气", settings) == "strong"
    assert select_model("活着没意思", settings) == "strong"
    assert select_model("有人敲门说是查水表的", settings) == "strong"


def test_routing_low_risk_uses_fast():
    settings = Settings(
        routing_enabled=True, model="base", model_fast="fast", model_strong="strong"
    )
    assert select_model("今天天气不错", settings) == "fast"
    assert select_model("血压有点高", settings) == "fast"


def test_routing_falls_back_to_default_when_unset():
    settings = Settings(routing_enabled=True, model="base")
    assert select_model("今天天气不错", settings) == "base"
    assert select_model("胸口疼", settings) == "base"
