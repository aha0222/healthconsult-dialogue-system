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


def test_voice_stream_contract():
    """实现后：发送音频 + commit，应能收到 final 事件且带 text 字段。"""
    import numpy as np

    with client.websocket_connect("/voice/stream") as ws:
        # 1 秒 440Hz 正弦波（16kHz 单声道 int16），足以触发 speech_start / final
        samples = (
            np.sin(2 * np.pi * 440 * np.arange(16000) / 16000) * 8000
        ).astype(np.int16)
        ws.send_bytes(samples.tobytes())
        ws.send_json({"event": "commit"})

        got = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg["event"] in ("final", "error"):
                got = msg
                break
        assert got is not None
        if got["event"] == "final":
            assert "text" in got
        else:
            assert "code" in got
