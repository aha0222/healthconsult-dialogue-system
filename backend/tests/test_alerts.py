"""alerts.py 单元测试。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import alerts
from backend.app.config import Settings


def base_record(**overrides):
    record = {
        "session_id": "s1",
        "personality": "温婉邻居型",
        "user_message": "胸口疼",
        "reply": "请马上打120",
        "risk": "R3",
        "violations": [],
        "fallback_used": False,
        "model": "deepseek-chat",
        "latency_ms": 10,
    }
    record.update(overrides)
    return record


def test_should_alert_by_risk():
    settings = Settings()
    assert alerts.should_alert("R3", False, settings) is True
    assert alerts.should_alert("M0", False, settings) is True
    assert alerts.should_alert("R0", False, settings) is False


def test_should_alert_on_fallback():
    settings = Settings()
    assert alerts.should_alert("R0", True, settings) is True


def test_send_alert_without_webhook_logs_only():
    settings = Settings()
    assert alerts.send_alert(base_record(), settings) is False


def test_send_alert_posts_to_webhook(monkeypatch):
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["json"] = json

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    settings = Settings(alert_webhook_url="http://example.test/hook")

    assert alerts.send_alert(base_record(), settings) is True
    assert calls["url"] == "http://example.test/hook"
    assert calls["json"]["risk"] == "R3"


def test_send_alert_skips_low_risk(monkeypatch):
    def fake_post(*args, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("不应推送低危告警")

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    settings = Settings(alert_webhook_url="http://example.test/hook")
    assert alerts.send_alert(base_record(risk="R0"), settings) is False
