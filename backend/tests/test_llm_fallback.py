"""在线档的网络兜底：云端连不上时切本地档，但必须可见、不静默。

对应组长清单第 5 项（断网优雅降级）。四条关键行为：
  1. 关闭时零变化（默认值即现有行为，报错不换档）；
  2. 连接级失败 → 切本地，且状态可被 /api/health 与响应 notice 读到；
  3. 内容级失败（400/参数错）**不**换档——否则配置错误会被伪装成断网；
  4. 流式吐过 delta 之后不换档，避免两段话被拼在一起。
"""

import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.dialogue.llm_client import LLMError  # noqa: E402
from backend.app.providers import get_llm, llm_status  # noqa: E402
from backend.app.providers.fallback import (  # noqa: E402
    BACKEND_CLOUD,
    BACKEND_LOCAL,
    FallbackLLM,
    backend_status,
    mark_degraded,
    probe_primary,
    reset_state,
)


def connection_error():
    from openai import APIConnectionError

    return APIConnectionError(
        request=httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    )


class FakeLLM:
    """记录调用次数的假档位。reply 为 None 时按 error 抛错。"""

    def __init__(self, reply="本地回复", calls=None, error=None):
        self.reply = reply
        self.calls = calls if calls is not None else []
        self.error = error

    def chat(self, messages, **kwargs):
        self.calls.append("chat")
        if self.error is not None:
            raise self.error
        return self.reply

    def chat_stream(self, messages, **kwargs):
        self.calls.append("chat_stream")
        if self.error is not None:
            raise self.error
        for i in range(0, len(self.reply), 3):
            yield self.reply[i : i + 3]


def make_settings(**over):
    base = dict(
        base_url="https://api.test.invalid",
        api_key="sk-test",
        llm_fallback_enabled=True,
        llm_fallback_probe_seconds=0,  # 默认粘住，不自动回探（回探单独测）
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_state()
    yield
    reset_state()


def wrap(primary, local, settings):
    return FallbackLLM(
        primary=primary,
        fallback_factory=lambda: local,
        settings=settings,
        enabled=True,
    )


# ── 判定与开关 ───────────────────────────────────────────────


def test_connection_error_walks_cause_chain():
    from backend.app.providers.fallback import _cause_is_connection_error

    wrapped = LLMError("调用大模型失败")
    wrapped.__cause__ = connection_error()
    assert _cause_is_connection_error(wrapped) is True
    assert _cause_is_connection_error(LLMError("未配置 DEEPSEEK_API_KEY")) is False


def test_get_llm_unchanged_by_default():
    """默认关闭＝现有行为：拿到的还是云端 client，不是兜底包装。"""
    from backend.app.dialogue.llm_client import LLMClient

    assert isinstance(get_llm(Settings()), LLMClient)
    assert isinstance(get_llm(Settings(llm_fallback_enabled=True)), FallbackLLM)


def test_status_reports_cloud_by_default():
    status = llm_status(Settings())
    assert status["llm_backend"] == BACKEND_CLOUD
    assert status["llm_degraded"] is False

    offline = llm_status(Settings(offline_mode=True))
    assert offline["llm_backend"] == BACKEND_LOCAL


# ── chat 换档 ────────────────────────────────────────────────


def test_connection_error_switches_to_local_and_sticks():
    settings = make_settings()
    local = FakeLLM("本地档回复")
    primary_calls = []

    class Primary:
        def chat(self, messages, **kw):
            primary_calls.append(1)
            raise LLMError("调用大模型失败") from connection_error()

    llm = wrap(Primary(), local, settings)

    assert llm.chat([{"role": "user", "content": "hi"}]) == "本地档回复"
    assert llm.degraded is True
    assert backend_status(FallbackLLM.state_key(settings))["llm_backend"] == BACKEND_LOCAL

    # 粘住：第二次不再打云端，直接走本地
    assert llm.chat([{"role": "user", "content": "hi"}]) == "本地档回复"
    assert len(primary_calls) == 1


def test_content_error_is_not_stolen():
    """400 之类的内容错不换档——否则配置错误会被伪装成断网。"""
    settings = make_settings()
    local = FakeLLM("不该用到本地")

    class Primary:
        def chat(self, messages, **kw):
            raise LLMError("调用大模型失败：Error code: 400")

    llm = wrap(Primary(), local, settings)
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert llm.degraded is False
    assert local.calls == []


def test_disabled_wrapper_never_switches():
    """enabled=False 时行为与没包装一样：连接错也原样抛出，不换档。"""
    settings = make_settings()
    local = FakeLLM("本地")

    class Primary:
        def chat(self, messages, **kw):
            raise LLMError("调用大模型失败") from connection_error()

    llm = FallbackLLM(
        primary=Primary(),
        fallback_factory=lambda: local,
        settings=settings,
        enabled=False,
    )
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert llm.degraded is False
    assert local.calls == []


def test_recovers_when_cloud_comes_back():
    settings = make_settings(llm_fallback_probe_seconds=0.0001)
    local = FakeLLM("本地")
    attempts = {"n": 0}

    class Primary:
        def chat(self, messages, **kw):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise LLMError("调用大模型失败") from connection_error()
            return "云端回复"

    llm = wrap(Primary(), local, settings)
    assert llm.chat([{"role": "user", "content": "a"}]) == "本地"
    assert llm.degraded is True
    # 等过一个时钟刻度，让回探窗口确实过去（Windows 时钟粒度约 15ms）
    time.sleep(0.03)
    # 回探窗口已过 → 再试云端，成功则恢复
    assert llm.chat([{"role": "user", "content": "b"}]) == "云端回复"
    assert llm.degraded is False


# ── 流式换档 ─────────────────────────────────────────────────


def test_stream_switches_when_nothing_yielded():
    settings = make_settings()
    local = FakeLLM("本地流式回复")

    class Primary:
        def chat_stream(self, messages, **kw):
            raise LLMError("流式调用大模型失败") from connection_error()
            yield  # pragma: no cover

    llm = wrap(Primary(), local, settings)
    text = "".join(llm.chat_stream([{"role": "user", "content": "hi"}]))
    assert text == "本地流式回复"
    assert llm.degraded is True


def test_stream_does_not_switch_after_partial_output():
    """吐过 delta 再失败：保持现状（宁缺勿串），不把两段话拼起来。"""
    settings = make_settings()
    local = FakeLLM("本地流式回复")

    class Primary:
        def chat_stream(self, messages, **kw):
            yield "前半段"
            raise LLMError("流式调用大模型失败") from connection_error()

    llm = wrap(Primary(), local, settings)
    chunks = []
    with pytest.raises(LLMError):
        for chunk in llm.chat_stream([{"role": "user", "content": "hi"}]):
            chunks.append(chunk)
    assert chunks == ["前半段"]
    assert llm.degraded is False
    assert local.calls == []


# ── 启动探测与 health ────────────────────────────────────────


def test_probe_primary_false_when_unreachable():
    # 关着的端口：拒绝连接 → 判定不可达
    assert probe_primary(Settings(base_url="http://127.0.0.1:1"), timeout=1.0) is False


def test_mark_degraded_is_visible_before_first_request():
    settings = make_settings()
    mark_degraded(settings, "启动探测：云端不可达")
    status = backend_status(FallbackLLM.state_key(settings))
    assert status["llm_degraded"] is True
    assert status["llm_backend"] == BACKEND_LOCAL
    assert "启动探测" in status["llm_degraded_reason"]


def test_health_exposes_degraded(make_client, monkeypatch):
    from backend.app import main as main_module

    monkeypatch.setattr(main_module.settings, "llm_fallback_enabled", True)
    mark_degraded(main_module.settings, "启动探测：云端不可达")

    body = make_client(reply="x").get("/api/health").json()
    assert body["llm_degraded"] is True
    assert body["llm_backend"] == BACKEND_LOCAL


def test_chat_response_carries_notice_when_degraded(make_client, monkeypatch):
    from backend.app import main as main_module

    monkeypatch.setattr(main_module.settings, "llm_fallback_enabled", True)
    mark_degraded(main_module.settings, "启动探测：云端不可达")

    client = make_client(reply="您先别急。[RISK:R1]")
    body = client.post("/api/chat", json={"message": "我血压有点高"}).json()
    assert body["degraded"] is True
    assert "本地模型" in body["notice"]

    # 哪条走了本地档要能事后查出来（notice 只在界面上闪一次）
    assert client.get("/api/audit").json()[0]["llm_backend"] == "local"
