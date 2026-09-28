"""alerts.py 单元测试。"""

import json
import logging
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
    assert alerts.should_alert("R2b", False, settings) is True
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


def test_send_alert_redacts_raw_text(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    settings = Settings(
        alert_webhook_url="http://example.test/hook", alert_webhook_redact=True
    )
    record = base_record(user_message="我胸口疼得厉害", reply="请马上打120")
    assert alerts.send_alert(record, settings) is True

    body = json.dumps(captured["json"], ensure_ascii=False)
    assert "我胸口疼得厉害" not in body
    assert "请马上打120" not in body
    assert captured["json"]["user_message_sha256"]
    assert captured["json"]["user_message_len"] == len("我胸口疼得厉害")


def test_send_alert_can_include_raw_when_redact_disabled(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json

    monkeypatch.setattr(alerts.httpx, "post", fake_post)
    settings = Settings(
        alert_webhook_url="http://example.test/hook", alert_webhook_redact=False
    )
    alerts.send_alert(base_record(user_message="我胸口疼"), settings)
    assert captured["json"]["user_message"] == "我胸口疼"


def test_alert_log_redacts_raw_text(caplog):
    settings = Settings()
    with caplog.at_level(logging.INFO, logger="xiaonuan.alert"):
        alerts.send_alert(
            base_record(user_message="我胸口疼", reply="请马上打120"), settings
        )

    fields = caplog.records[-1].fields
    assert "user_message" not in fields
    assert "reply" not in fields
    assert fields["user_message_sha256"]
