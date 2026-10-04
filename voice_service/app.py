"""小暖语音服务（契约 v1 · Day0 冻结 · 已实现）。

┌─ 这是什么 ────────────────────────────────────────────────────────────┐
│ 一个**独立进程 / 独立端口**（默认 127.0.0.1:8100）的语音能力服务，      │
│ 与主后端（backend/）完全解耦。前端直接连本服务（不经主后端）。          │
└──────────────────────────────────────────────────────────────────────┘

职责边界（不要越界）：
  ✔ 只做「音频 -> 文本」(ASR) 与「文本 -> 音频」(TTS)、以及流式语音事件。
  ✘ 不做对话编排、不碰 safety_checker、不直连大模型。
  ✘ 不读写主后端的 SQLite 会话库。
  对话由主后端负责；主后端产出的回复文本再交回本服务 /tts 播报。

契约（v1，签名冻结，A 与组长共同遵守）：
  GET  /health        -> {"status","service","contract","asr","tts"}
  POST /asr           -> body: 原始音频字节(wav); query: language=zh
                         resp: {"text": str, "is_final": true, "confidence": float}
  POST /tts           -> json {"text","voice","format","speed"}
                         resp: audio/wav 字节流（Content-Type: audio/wav）
  WS   /voice/stream  -> 双向事件流，事件名见 README.md
                         speech_start / partial / final / reply_audio / interrupt / error

当前状态：**已实现**。
  - /health        永远 200；模型就绪时 asr/tts 为 true。
  - /asr          本地中文识别；模型未就绪返回 501，音频解码失败返回空文本。
  - /tts          本地中文合成（wav）；模型未就绪返回 501。
  - /voice/stream 流式识别（speech_start/partial/final）+ 打断（interrupt）。
  - 签名与事件名与契约 v1 一致，不得改动。

依赖：fastapi / uvicorn（见 requirements.txt；空壳期不依赖 python-multipart）。
启动：python -m voice_service   （等价于 uvicorn voice_service.app:app --port 8100）
"""

from __future__ import annotations

import os
from typing import Optional

import asyncio
import json
import logging
import threading
import time

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import CONTRACT_VERSION, DEFAULT_PORT, SERVICE_NAME, __version__
from . import asr as _asr
from . import tts as _tts
from . import vad as _vad

logger = logging.getLogger(__name__)

app = FastAPI(title="小暖语音服务", version=__version__)

# 启动时后台预热 TTS 模型（加载进显存/内存），避免首个播报请求等待
# 模型加载（GPU 上实测首次约 30s+，会触发前端超时回退浏览器 TTS）。
# ASR 同理惰性加载较轻，这里只预热 TTS；失败不影响服务（接口仍会按需重试）。


@app.on_event("startup")
def _warmup_tts() -> None:
    def _warm() -> None:
        try:
            if _tts.is_available():
                t0 = time.monotonic()
                # 合成两遍：第一遍加载模型，第二遍预热 CUDA kernel 缓存
                # （首次真实推理的 kernel 编译会让前几次请求慢 2-3 秒）
                _tts.synthesize("语音服务已就绪，您可以随时跟我说话。")
                _tts.synthesize("咱们先从几个方面考虑这个问题，然后再慢慢想办法。")
                logger.info("TTS 预热完成，耗时 %.1fs", time.monotonic() - t0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTS 预热失败（首次合成时将重试）：%s", exc)

    threading.Thread(target=_warm, daemon=True, name="tts-warmup").start()

# 前端多为 file:// 或本地端口，允许跨域直连（语音服务不持有任何密钥，放行安全）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _readiness(env_key: str, available: bool) -> bool:
    """能力就绪判断：默认按模型文件是否存在自动判断，环境变量可强制覆盖。"""
    raw = os.environ.get(env_key, "").strip().lower()
    if raw in ("", "auto"):
        return available
    return raw in ("1", "true", "yes", "on")


# 模型已下载即视为就绪；未下载时保持 501 回退（契约测试仍全绿）。
ASR_READY = _readiness("VOICE_ASR_READY", _asr.is_available())
TTS_READY = _readiness("VOICE_TTS_READY", _tts.is_available())


class TTSRequest(BaseModel):
    """TTS 请求体（契约字段，A 不得增删必填项）。"""

    text: str
    voice: Optional[str] = None
    format: str = "wav"
    speed: float = 0.9


def _not_implemented(what: str) -> None:
    """统一 501：模型未就绪时调用，前端据此回退到打字 / 静默。"""
    raise HTTPException(
        status_code=501,
        detail=f"{what} 模型未就绪（请先运行 python voice_service/download_models.py 下载模型）",
    )


@app.get("/health")
def health() -> dict:
    """探活 + 能力自述。永远返回 200，供安装/打包脚本与组长接线自检。"""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": __version__,
        "contract": CONTRACT_VERSION,
        "asr": ASR_READY,
        "tts": TTS_READY,
    }


@app.post("/asr")
async def asr(
    request: Request,
    language: str = Query("zh", description="识别语言，默认 zh"),
):
    """音频 -> 文本。

    入参：**原始音频字节** 直接作为 request body（Content-Type: audio/wav
          或 application/octet-stream），避免依赖 python-multipart。
    契约返回：{"text": str, "is_final": bool, "confidence": float}
    未下载模型时返回 501；识别失败时返回空结果（调用方可据此回退）。
    """
    if not ASR_READY:
        _not_implemented("ASR")
    data = await request.body()
    try:
        text, is_final, confidence = _asr.transcribe(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ASR 处理失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"ASR 处理失败：{exc}")
    return {"text": text, "is_final": is_final, "confidence": confidence}


@app.post("/tts")
def tts(req: TTSRequest):
    """文本 -> wav 音频字节流（Content-Type: audio/wav）。

    未下载模型时返回 501；合成失败返回 500 并附带清晰错误信息。
    """
    if not TTS_READY:
        _not_implemented("TTS")
    try:
        wav_bytes = _tts.synthesize(req.text, speed=req.speed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS 合成失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"TTS 合成失败：{exc}")
    return Response(content=wav_bytes, media_type="audio/wav")


@app.websocket("/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    """双向流式语音事件通道。

    事件名（冻结）：
      客户端 -> 服务端：audio_chunk(二进制 PCM16, 16kHz 单声道) / commit / interrupt / ping
      服务端 -> 客户端：speech_start / partial{text} / final{text}
                        / reply_audio(二进制) / interrupt / error{code,message}
    """
    await ws.accept()
    pcm = bytearray()
    speaking = False
    last_partial = ""
    last_partial_ts = 0.0
    try:
        while True:
            message = await ws.receive()
            mtype = message.get("type")
            if mtype == "websocket.disconnect":
                break
            if mtype != "websocket.receive":
                continue

            chunk = message.get("bytes")
            if chunk is not None:
                pcm.extend(chunk)
                if not speaking and _vad.is_speech_pcm16(bytes(chunk)):
                    speaking = True
                    await ws.send_json({"event": "speech_start"})
                now = time.monotonic()
                if (
                    ASR_READY
                    and speaking
                    and now - last_partial_ts >= 1.0
                    and len(pcm) >= _asr.TARGET_SAMPLE_RATE * 2
                ):
                    try:
                        text = await asyncio.to_thread(
                            lambda: _asr.transcribe_pcm16(bytes(pcm))[0]
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("流式 partial 识别失败：%s", exc)
                        text = ""
                    if text and text != last_partial:
                        await ws.send_json({"event": "partial", "text": text})
                        last_partial = text
                    last_partial_ts = now
                continue

            text_data = message.get("text")
            if not text_data:
                continue
            try:
                payload = json.loads(text_data)
            except (TypeError, ValueError):
                payload = {}
            name = payload.get("event")

            if name == "commit":
                if not ASR_READY:
                    pcm.clear()
                    speaking = False
                    last_partial = ""
                    await ws.send_json(
                        {
                            "event": "error",
                            "code": "asr_not_ready",
                            "message": "ASR 模型未就绪",
                        }
                    )
                    continue
                try:
                    if pcm:
                        text, _, confidence = await asyncio.to_thread(
                            _asr.transcribe_pcm16, bytes(pcm)
                        )
                    else:
                        text, confidence = "", 0.0
                    await ws.send_json(
                        {"event": "final", "text": text, "confidence": confidence}
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("流式 final 识别失败：%s", exc)
                    await ws.send_json(
                        {"event": "error", "code": "asr_failed", "message": str(exc)}
                    )
                pcm.clear()
                speaking = False
                last_partial = ""
            elif name == "interrupt":
                pcm.clear()
                speaking = False
                last_partial = ""
                await ws.send_json({"event": "interrupt"})
            # ping：no-op，仅用于保活
    except WebSocketDisconnect:
        return


if __name__ == "__main__":  # pragma: no cover - 便捷调试入口
    import uvicorn

    uvicorn.run(
        "voice_service.app:app",
        host=os.environ.get("VOICE_HOST", "127.0.0.1"),
        port=int(os.environ.get("VOICE_PORT", str(DEFAULT_PORT))),
    )
