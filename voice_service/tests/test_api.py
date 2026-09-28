"""voice_service 契约测试。

**空壳期也必须全绿**：验证「对外契约形状」而非「识别质量」。
A 实现真实能力后，这些断言应继续成立（只有状态码从 501 变为 200）。
"""

from fastapi.testclient import TestClient

from voice_service.app import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "voice_service"
    assert body["contract"] == "v1"
    assert isinstance(body["asr"], bool)
    assert isinstance(body["tts"], bool)


def test_asr_contract_shape():
    """空壳返回 501；实现后返回 {text,is_final,confidence}。"""
    r = client.post(
        "/asr?language=zh",
        content=b"RIFF0000WAVE",
        headers={"Content-Type": "audio/wav"},
    )
    assert r.status_code in (200, 501)
    if r.status_code == 200:
        body = r.json()
        assert set(["text", "is_final", "confidence"]) <= set(body.keys())


def test_tts_contract_shape():
    r = client.post("/tts", json={"text": "你好", "format": "wav", "speed": 0.9})
    assert r.status_code in (200, 501)
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("audio/")


def test_voice_stream_reports_error_when_stub():
    """空壳期：连上后应先收到 error 事件，而不是静默挂起。"""
    with client.websocket_connect("/voice/stream") as ws:
        msg = ws.receive_json()
        assert msg["event"] == "error"
