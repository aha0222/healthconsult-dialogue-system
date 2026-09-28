"""config.py 回归测试：确保 .env / 环境变量中的风险等级配置被正确读取与校验。

回归 T1/T2：SEMANTIC_CHECK_RISKS=ALERT_RISKS=R3,M0,S0 这类历史遗留配置
不得静默把 R2b 排除在语义复核/告警之外。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.dialogue.orchestrator import DialogueOrchestrator


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, messages, **kwargs):
        return self.reply


class FakeSemanticChecker:
    def __init__(self, result=None):
        self.result = result or {"ok": True, "parsed": True, "issues": []}
        self.called = 0

    def check(self, user_text, reply, risk, scenes=None):
        self.called += 1
        return self.result


def test_legacy_risk_config_falls_back_to_safe_default(monkeypatch):
    monkeypatch.setenv("SEMANTIC_CHECK_RISKS", "R3,M0,S0")
    monkeypatch.setenv("ALERT_RISKS", "R3,M0,S0")

    settings = Settings.from_env()

    assert {"R3", "R2b"} <= settings.semantic_check_risks
    assert {"R3", "R2b"} <= settings.alert_risks
    assert "M0" not in settings.semantic_check_risks
    assert "S0" not in settings.semantic_check_risks
    assert settings.config_warnings


def test_valid_risk_config_is_kept(monkeypatch):
    monkeypatch.setenv("SEMANTIC_CHECK_RISKS", "R3,R2b")
    monkeypatch.setenv("ALERT_RISKS", "R3,R2b")

    settings = Settings.from_env()

    assert settings.semantic_check_risks == {"R3", "R2b"}
    assert settings.alert_risks == {"R3", "R2b"}
    assert settings.config_warnings == []


def test_r2b_reply_still_semantically_checked_with_legacy_env(monkeypatch):
    """回归：即便环境里写的是历史配置 R3,M0,S0，R2b 回复也必须走语义复核。"""
    monkeypatch.setenv("SEMANTIC_CHECK_RISKS", "R3,M0,S0")
    monkeypatch.setenv("ALERT_RISKS", "R3,M0,S0")

    settings = Settings.from_env()
    settings.runtime_classifier = False
    checker = FakeSemanticChecker()
    orch = DialogueOrchestrator(
        llm=FakeLLM("这个情况别拖，今天就联系医生看看。[RISK:R2b]\n[SCENE:E1]"),
        settings=settings,
        semantic_checker=checker,
    )

    result = orch.respond("我早上大便发黑了")

    assert result["risk"] == "R2b"
    assert result["semantic_checked"] is True
    assert checker.called == 1
