"""阿里云百炼 CosyVoice 云端语音合成（非流式，契约同本地 /tts -> wav）。

与本地引擎（cosyvoice / kokoro / piper）并列，作为「联网语音」。
- 鉴权：DASHSCOPE_API_KEY（sk-xxx，Bearer）
- 模型：默认 cosyvoice-v3-flash（有系统音色，开箱即用；可用 DASHSCOPE_COSYVOICE_MODEL 覆盖）
- 音色：默认 longyuan_v3（龙媛，35–40 温暖治愈；可用 DASHSCOPE_COSYVOICE_VOICE 覆盖）
- 接口：POST .../api/v1/services/audio/tts/SpeechSynthesizer（非流式返回音频 URL）

请求/响应以阿里官方文档为准；本模块只做「文字 -> wav 字节」，失败抛异常由上层回退本地。
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
TTS_PATH = "/services/audio/tts/SpeechSynthesizer"


def _api_key() -> str:
    return os.environ.get("DASHSCOPE_API_KEY", "").strip()


def _base_url() -> str:
    return os.environ.get("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _model() -> str:
    # v3.5-flash 无系统音色、需先创建自定义音色；v3-flash 有系统音色，开箱即用。
    return os.environ.get("DASHSCOPE_COSYVOICE_MODEL", "cosyvoice-v3-flash").strip()


def _voice() -> str:
    return os.environ.get("DASHSCOPE_COSYVOICE_VOICE", "longyuan_v3").strip()


def is_available() -> bool:
    """云端是否可用：只要配了 API Key 即可（不阻塞启动去探测网络）。"""
    return bool(_api_key())


# 复用连接：首次请求要付 TCP+TLS 握手的代价，之后走 keep-alive。
# 实测第一次 /tts 会比后续慢半秒到一秒，表现就是"开场空白"。
_SESSION = None
_SESSION_LOCK = threading.Lock()


def _session():
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            import requests

            _SESSION = requests.Session()
        return _SESSION


def warmup() -> None:
    """启动时预热：建好 TLS 连接，并让远端模型热一次（用最短的文本）。"""
    if not is_available():
        return
    try:
        synthesize("好")
        logger.info("云端语音预热完成")
    except Exception as exc:  # noqa: BLE001
        logger.warning("云端语音预热失败（首次合成会重试）：%s", exc)


def synthesize(text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
    """文本 -> wav 字节。任何失败都抛异常，由调用方回退本地引擎。"""
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY")

    url = f"{_base_url()}{TTS_PATH}"
    rate = max(0.5, min(2.0, float(speed or 1.0)))
    payload = {
        "model": _model(),
        "input": {
            "text": text,
            "voice": voice or _voice(),
            "format": "wav",
            "sample_rate": 24000,
            "rate": rate,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    resp = _session().post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code != 200:
        try:
            err = resp.json()
        except ValueError:
            err = {}
        detail = err.get("message")
        if not detail and isinstance(err.get("error"), dict):
            detail = err["error"].get("message")
        detail = detail or resp.text[:200]
        raise RuntimeError(f"云端合成失败：HTTP {resp.status_code} {detail}")
    try:
        data = resp.json()
        audio_url = data["output"]["audio"]["url"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"云端响应格式异常：{exc}") from exc
    if not audio_url:
        raise RuntimeError("云端未返回音频地址")

    audio_resp = _session().get(audio_url, timeout=60)
    if audio_resp.status_code != 200:
        raise RuntimeError(f"下载云端音频失败：HTTP {audio_resp.status_code}")
    return audio_resp.content


# ── 流式合成（WebSocket）──────────────────────────────────────────────
# 非流式接口要等整段合成完才返回：66 字约 3.2 秒，200 字要 10 秒以上。
# WebSocket 流式实测**首帧 0.27 秒**、66 字整段 2.8 秒，且是同一个会话，
# 语调统一——所以长回复也用它，不必再靠"分块"折中。
# 协议：run-task → (task-started) → continue-task + finish-task → 若干二进制帧(PCM16)。
WS_PATH = "/api-ws/v1/inference"


def ws_url() -> str:
    base = _base_url()
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    # https://host/api/v1 → wss://host/api-ws/v1
    if base.endswith("/api/v1"):
        base = base[: -len("/api/v1")]
    return base + WS_PATH


async def _stream_pcm_async(text: str, voice: str | None, speed: float):
    """异步生成器：逐块产出 PCM16 字节。任何失败都抛异常，由调用方回退。"""
    import asyncio
    import json
    import uuid

    import websockets

    api_key = _api_key()
    if not api_key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY")

    tid = uuid.uuid4().hex
    hdr = {"task_id": tid, "streaming": "duplex"}
    rate = max(0.5, min(2.0, float(speed or 1.0)))

    async with websockets.connect(
        ws_url(),
        additional_headers={"Authorization": f"bearer {api_key}"},
        open_timeout=10,
        max_size=None,
    ) as ws:
        await ws.send(json.dumps({
            "header": dict(hdr, action="run-task"),
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": _model(),
                "parameters": {
                    "text_type": "PlainText",
                    "voice": voice or _voice(),
                    "format": "pcm",
                    "sample_rate": 24000,
                    "rate": rate,
                },
                "input": {},
            },
        }, ensure_ascii=False))

        sent = False
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            if isinstance(raw, (bytes, bytearray)):
                if raw:
                    yield bytes(raw)
                continue
            msg = json.loads(raw)
            event = msg.get("header", {}).get("event")
            if event == "task-started" and not sent:
                await ws.send(json.dumps({
                    "header": dict(hdr, action="continue-task"),
                    "payload": {"input": {"text": text}},
                }, ensure_ascii=False))
                await ws.send(json.dumps({
                    "header": dict(hdr, action="finish-task"),
                    "payload": {"input": {}},
                }, ensure_ascii=False))
                sent = True
            elif event == "task-failed":
                detail = json.dumps(msg.get("payload", {}), ensure_ascii=False)[:300]
                raise RuntimeError(f"云端流式合成失败：{detail}")
            elif event == "task-finished":
                return


def synthesize_stream_pcm(text: str, voice: str | None = None, speed: float = 1.0):
    """同步桥接：返回 PCM16 块的生成器（24kHz 单声道）。

    FastAPI 的同步路由直接消费这个生成器；一个请求一个事件循环，连接随生成器关闭。
    """
    import asyncio

    loop = asyncio.new_event_loop()
    agen = _stream_pcm_async(text, voice, speed)

    def gen():
        try:
            while True:
                try:
                    chunk = loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
                yield chunk
        finally:
            try:
                loop.run_until_complete(agen.aclose())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    return gen()
