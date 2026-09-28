"""security.py 单元测试：滑动窗口限流、客户端 IP、启动安全校验。"""

import sys
from pathlib import Path

import pytest
from starlette.requests import Request

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import security
from backend.app.config import Settings
from backend.app.security import SlidingWindowLimiter


def _request(headers=None, client=("1.2.3.4", 1234)):
    scope = {
        "type": "http",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "client": client,
    }
    return Request(scope)


def test_limiter_allows_up_to_limit():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("ip", 2) is True
    assert limiter.allow("ip", 2) is True
    assert limiter.allow("ip", 2) is False


def test_limiter_isolated_per_key():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("a", 1) is True
    assert limiter.allow("b", 1) is True
    assert limiter.allow("a", 1) is False


def test_limiter_reset():
    limiter = SlidingWindowLimiter()
    limiter.allow("ip", 1)
    limiter.reset()
    assert limiter.allow("ip", 1) is True


def test_xff_ignored_when_no_trusted_proxy(monkeypatch):
    monkeypatch.setattr(security, "get_settings", lambda: Settings())
    request = _request({"x-forwarded-for": "9.9.9.9"})
    assert security.get_client_ip(request) == "1.2.3.4"


def test_xff_ignored_for_untrusted_source(monkeypatch):
    monkeypatch.setattr(
        security, "get_settings", lambda: Settings(trusted_proxies={"10.0.0.1"})
    )
    request = _request({"x-forwarded-for": "9.9.9.9"}, client=("1.2.3.4", 1))
    assert security.get_client_ip(request) == "1.2.3.4"


def test_xff_trusted_when_proxy_trusted(monkeypatch):
    monkeypatch.setattr(
        security, "get_settings", lambda: Settings(trusted_proxies={"10.0.0.1"})
    )
    request = _request(
        {"x-forwarded-for": "9.9.9.9, 8.8.8.8"}, client=("10.0.0.1", 1)
    )
    assert security.get_client_ip(request) == "9.9.9.9"


def test_xff_trusted_via_cidr(monkeypatch):
    monkeypatch.setattr(
        security, "get_settings", lambda: Settings(trusted_proxies={"10.0.0.0/8"})
    )
    request = _request({"x-forwarded-for": "9.9.9.9"}, client=("10.1.2.3", 1))
    assert security.get_client_ip(request) == "9.9.9.9"


def test_startup_security_warns_without_auth():
    warnings = security.check_startup_security(Settings())
    assert any("无鉴权" in w for w in warnings)


def test_startup_security_warns_on_wildcard_cors():
    warnings = security.check_startup_security(
        Settings(cors_origins=["*"], backend_api_key="secret")
    )
    assert any("CORS" in w for w in warnings)


def test_startup_security_raises_in_production_without_auth():
    with pytest.raises(RuntimeError):
        security.check_startup_security(Settings(environment="production"))


def test_startup_security_ok_in_production_with_auth(monkeypatch):
    settings = Settings(environment="production", backend_api_key="secret")
    assert security.check_startup_security(settings) == []
