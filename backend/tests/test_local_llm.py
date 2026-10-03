"""LocalLLM 端到端测试（成员 B 交付）。

起一个本地 HTTP 服务器模拟 llama-server 的 OpenAI 兼容接口，
验证 endpoint 后端的完整路径。不依赖真实 llama-server，也不需要联网。

覆盖的关键点：
  · 探活用 /v1/models，且 base_url 自动补 /v1
  · chat / chat_stream 与 LLMClient 的签名一致、行为一致
  · **不发送 `extra_body={"thinking": ...}`** —— 那是 DeepSeek 专有字段，
    严格 OpenAI 兼容的端点会直接 400
  · 解析结果按配置指纹缓存，避免每请求探活一次
  · 端点不可达时抛 LLMError 且**绝不**回退云端 LLMClient
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import config as config_mod  # noqa: E402
from backend.app.dialogue.llm_client import LLMClient, LLMError  # noqa: E402
from backend.app.providers import local_llm  # noqa: E402
from backend.app.providers.local_llm import LocalLLM  # noqa: E402

REPLY = "您说的黑便不能当成吃的东西染的，今天就得去医院。"


class _Handler(BaseHTTPRequestHandler):
    """够用的 OpenAI 兼容假端点。把收到的请求体记在 server.requests 里。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._json({"object": "list",
                        "data": [{"id": "local", "object": "model", "owned_by": "llama"}]})
        else:
            self._json({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
        self.server.requests.append(payload)  # type: ignore[attr-defined]

        if not self.path.endswith("/chat/completions"):
            self._json({"error": {"message": "not found"}}, 404)
            return

        if payload.get("stream"):
            self._sse()
            return

        self._json({
            "id": "chatcmpl-test", "object": "chat.completion", "model": "local",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": REPLY}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
        })

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def chunk(text):
            return ("data: " + json.dumps({
                "id": "chatcmpl-test", "object": "chat.completion.chunk",
                "model": "local",
                "choices": [{"index": 0, "delta": {"content": text}}],
            }, ensure_ascii=False) + "\n\n").encode()

        for piece in ("您说的黑便", "不能当成吃的东西染的，", "今天就得去医院。"):
            self.wfile.write(chunk(piece))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


@pytest.fixture
def fake_endpoint():
    """起一个本地假端点，返回 (base_url, requests 列表)。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address
    yield f"http://127.0.0.1:{port}", server.requests
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def _clean_caches():
    """每个测试前后清掉进程级缓存，避免相互污染。"""
    local_llm._RESOLVED.clear()
    local_llm._ENDPOINT_CLIENTS.clear()
    yield
    local_llm._RESOLVED.clear()
    local_llm._ENDPOINT_CLIENTS.clear()


def _settings(monkeypatch, **env):
    monkeypatch.setenv("OFFLINE_LLM_BACKEND", env.pop("backend", "endpoint"))
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    settings = config_mod.Settings()
    settings.offline_mode = True
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)
    return settings


def test_endpoint_chat_returns_reply(monkeypatch, fake_endpoint):
    base, _ = fake_endpoint
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", base)
    llm = LocalLLM(_settings(monkeypatch))
    out = llm.chat([{"role": "user", "content": "我大便发黑"}])
    assert out == REPLY


def test_endpoint_base_url_gets_v1_and_probe_hits_models(monkeypatch, fake_endpoint):
    """根地址不带 /v1 时自动补齐；探活走 /v1/models。"""
    base, _ = fake_endpoint
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", base)   # 故意不带 /v1
    llm = LocalLLM(_settings(monkeypatch))
    llm.chat([{"role": "user", "content": "你好"}])
    assert llm.backend_name == "endpoint"
    # 能拿到回复即说明 SDK 拼出的路径正确（否则会 404 抛 LLMError）


def test_no_thinking_field_sent(monkeypatch, fake_endpoint):
    """绝不能把 DeepSeek 专有的 thinking 字段发给本地端点。"""
    base, requests = fake_endpoint
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", base)
    llm = LocalLLM(_settings(monkeypatch))
    llm.chat([{"role": "user", "content": "你好"}])
    body = requests[-1]
    assert "thinking" not in body, f"请求体里出现了 thinking 字段：{body}"
    assert body["messages"] == [{"role": "user", "content": "你好"}]


def test_stream_yields_pieces_in_order(monkeypatch, fake_endpoint):
    base, _ = fake_endpoint
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", base)
    llm = LocalLLM(_settings(monkeypatch))
    out = "".join(llm.chat_stream([{"role": "user", "content": "我大便发黑"}]))
    assert out == REPLY


def test_signature_matches_llm_client(monkeypatch, fake_endpoint):
    """签名必须与 LLMClient 一致，调用点才能零改动替换。"""
    import inspect

    for name in ("chat", "chat_stream"):
        a = inspect.signature(getattr(LocalLLM, name))
        b = inspect.signature(getattr(LLMClient, name))
        assert list(a.parameters) == list(b.parameters), f"{name} 参数不一致"


def test_resolution_is_cached_across_instances(monkeypatch, fake_endpoint):
    """解析结果按配置指纹缓存：第二次构造不再探活。

    main.py 的 get_memory() 没有单例缓存，每请求都构造 MemoryManager；
    若每次都探活，健康时每请求多一次网络往返，端点挂掉时每请求多等数秒。
    """
    base, requests = fake_endpoint
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", base)
    settings = _settings(monkeypatch)

    LocalLLM(settings).chat([{"role": "user", "content": "第一次"}])
    n_after_first = len(requests)

    LocalLLM(settings).chat([{"role": "user", "content": "第二次"}])
    # 第二次只应多出 1 个请求（chat 本身），不应再有探活请求
    assert len(requests) - n_after_first == 1, (
        f"第二次调用产生了 {len(requests) - n_after_first} 个请求，探活似乎没被缓存"
    )


def test_endpoint_down_raises_without_cloud_fallback(monkeypatch):
    """端点不可达且进程内后端不可用 → LLMError，且不回退云端。"""
    monkeypatch.setenv("OFFLINE_LLM_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OFFLINE_LLM_MODEL_PATH", "")
    monkeypatch.delenv("OFFLINE_LLM_STRICT", raising=False)
    llm = LocalLLM(_settings(monkeypatch, backend="auto"))

    with pytest.raises(LLMError) as exc:
        llm.chat([{"role": "user", "content": "你好"}])
    assert not isinstance(llm._backend, LLMClient)
    assert "不会回退到云端" in str(exc.value)


def test_probe_timeout_is_bounded(monkeypatch):
    """探活要有独立且较短的超时，不能拖到完整调用超时。"""
    monkeypatch.setenv("OFFLINE_LLM_PROBE_TIMEOUT", "1")
    assert local_llm._env_float("OFFLINE_LLM_PROBE_TIMEOUT", 3.0) == 1.0
