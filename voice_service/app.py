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
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import CONTRACT_VERSION, DEFAULT_PORT, SERVICE_NAME, __version__
from . import asr as _asr
from . import cloud_tts as _cloud_tts
from . import vad as _vad

logger = logging.getLogger(__name__)

# TTS 引擎切换：
#   cosyvoice  -> CosyVoice2-0.5B（推荐，中文多音字消歧、自然度最好）
#   kokoro     -> Kokoro v1.1（备选，GPU 加速）
#   piper      -> Piper（CPU 兜底）
TTS_ENGINE = os.environ.get("TTS_ENGINE", "").strip().lower()
if not TTS_ENGINE:
    # 有 CosyVoice2 主权重就优先用它；还没下载则先用随包的 Kokoro，避免 /tts 直接 501。
    _cosy_llm = os.path.join(
        os.path.dirname(__file__), "models", "cosyvoice2", "llm.pt"
    )
    TTS_ENGINE = "cosyvoice" if os.path.exists(_cosy_llm) else "kokoro"
if TTS_ENGINE == "cosyvoice":
    from . import cosyvoice_tts as _tts

    _TTS_DEFAULT_VOICE = ""
elif TTS_ENGINE == "kokoro":
    from . import kokoro_tts as _tts

    _TTS_DEFAULT_VOICE = "zf_001"
else:
    from . import tts as _tts

    _TTS_DEFAULT_VOICE = None

# 推理锁：TTS 引擎（GPU/CPU 模型）不是线程安全的，多路并发合成会抢用同一模型，
# 导致显存溢出或音频错乱。这里把「真正的推理」串行化，让 /tts 可以被并行调用、内部排队。
_TTS_LOCK = threading.Lock()

app = FastAPI(title="小暖语音服务", version=__version__)

# 前端多为 file:// 或本地端口，允许跨域直连（语音服务不持有任何密钥，放行安全）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Sample-Rate", "X-Channels", "X-Dtype"],
)

# 模型「文件是否齐备」：仅决定 501 提示是“未下载”口径。
_ASR_AVAILABLE = _asr.is_available()
_TTS_AVAILABLE = _tts.is_available()

# 运行时就绪位：只有真正加载并预热成功后才置为 True。
# 修复「预热就绪位撒谎」：/health 不再用“文件存在”冒充“已就绪”。
_ASR_LOADED = False
_TTS_LOADED = False


def _asr_ready() -> bool:
    """ASR 是否真正就绪。环境变量可强制覆盖（供测试 / 运维排障）。"""
    raw = os.environ.get("VOICE_ASR_READY", "").strip().lower()
    if raw in ("", "auto"):
        return _ASR_AVAILABLE and _ASR_LOADED
    return raw in ("1", "true", "yes", "on")


def _tts_ready() -> bool:
    """TTS 是否真正就绪。环境变量可强制覆盖（供测试 / 运维排障）。"""
    raw = os.environ.get("VOICE_TTS_READY", "").strip().lower()
    if raw in ("", "auto"):
        return _TTS_AVAILABLE and _TTS_LOADED
    return raw in ("1", "true", "yes", "on")


def _preload_enabled(env_key: str) -> bool:
    """该能力是否应当被预加载（环境变量明确置 0/false/off 时不加载）。"""
    raw = os.environ.get(env_key, "").strip().lower()
    return raw in ("", "auto", "1", "true", "yes", "on")


def _preload_models() -> None:
    """启动时预加载并预热模型；只有成功后才把运行时就绪位置为 True。"""
    global _ASR_LOADED, _TTS_LOADED
    if _ASR_AVAILABLE and _preload_enabled("VOICE_ASR_READY"):
        try:
            _asr._load()
            _ASR_LOADED = True
        except Exception as exc:  # noqa: BLE001
            _ASR_LOADED = False
            logger.warning("预加载 ASR 模型失败，/health 将报告 asr=false：%s", exc)
    if _TTS_AVAILABLE and _preload_enabled("VOICE_TTS_READY"):
        try:
            _tts._load()
            # CosyVoice 在 _load() 内部已经预热；Kokoro/Piper 需要真正合成一次，
            # 否则 /health 先报就绪、第一句仍要等十几秒，前端会误退回浏览器语音。
            if TTS_ENGINE != "cosyvoice":
                _tts.synthesize("语音服务已就绪。")
            _TTS_LOADED = True
        except Exception as exc:  # noqa: BLE001
            _TTS_LOADED = False
            logger.warning("预加载 TTS 模型失败，/health 将报告 tts=false：%s", exc)


_preload_models()


class TTSRequest(BaseModel):
    """TTS 请求体（契约字段，A 不得增删必填项）。"""

    text: str
    voice: Optional[str] = None
    format: str = "wav"
    speed: float = 0.9
    engine: Optional[str] = None


def _not_implemented(what: str, hint: str = "download_models.py") -> None:
    """统一 501：模型未就绪时调用，前端据此回退到打字 / 静默。"""
    raise HTTPException(
        status_code=501,
        detail=f"{what} 模型未就绪（请先运行 python voice_service/{hint} 下载模型）",
    )


@app.get("/health")
def health() -> dict:
    """探活 + 能力自述。永远返回 200，供安装/打包脚本与组长接线自检。"""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": __version__,
        "contract": CONTRACT_VERSION,
        "asr": _asr_ready(),
        "tts": _tts_ready(),
        "cloud_tts": _cloud_tts.is_available(),
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
    if not _asr_ready():
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

    engine=cloud 时走阿里百炼云端合成，失败自动回退本地；否则走本地引擎。
    未下载模型时返回 501；合成失败返回 500 并附带清晰错误信息。
    """
    if req.engine == "cloud":
        if not _cloud_tts.is_available():
            logger.warning("请求云端语音但未配置 DASHSCOPE_API_KEY，回退本地引擎")
        else:
            try:
                wav_bytes = _cloud_tts.synthesize(
                    req.text, voice=req.voice, speed=req.speed
                )
                return Response(content=wav_bytes, media_type="audio/wav")
            except Exception as exc:  # noqa: BLE001
                logger.warning("云端合成失败，回退本地引擎：%s", exc)
    if not _tts_ready():
        if TTS_ENGINE == "cosyvoice":
            hint = "download_cosyvoice2.py"
        elif TTS_ENGINE == "kokoro":
            hint = "download_kokoro.py"
        else:
            hint = "download_models.py"
        _not_implemented("TTS", hint)
    try:
        with _TTS_LOCK:
            if TTS_ENGINE == "kokoro":
                wav_bytes = _tts.synthesize(
                    req.text, voice=req.voice or _TTS_DEFAULT_VOICE, speed=req.speed
                )
            elif TTS_ENGINE == "cosyvoice":
                wav_bytes = _tts.synthesize(
                    req.text, voice=req.voice or _TTS_DEFAULT_VOICE, speed=req.speed
                )
            else:
                wav_bytes = _tts.synthesize(req.text, speed=req.speed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS 合成失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"TTS 合成失败：{exc}")
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/tts/stream")
def tts_stream(req: TTSRequest):
    """文本 -> 流式 PCM16（application/octet-stream），供前端边合成边播放。

    采样率 / 声道 / 位深通过响应头返回：X-Sample-Rate / X-Channels / X-Dtype。
    仅 cosyvoice 引擎支持流式；其它引擎返回 501，前端应回退到 /tts。
    """
    if req.engine == "cloud":
        raise HTTPException(
            status_code=501,
            detail="云端语音暂不支持流式，请使用 /tts（非流式）",
        )
    if not _tts_ready():
        if TTS_ENGINE == "cosyvoice":
            hint = "download_cosyvoice2.py"
        elif TTS_ENGINE == "kokoro":
            hint = "download_kokoro.py"
        else:
            hint = "download_models.py"
        _not_implemented("TTS", hint)
    if not hasattr(_tts, "synthesize_stream"):
        raise HTTPException(
            status_code=501,
            detail="当前 TTS 引擎不支持流式合成（请使用 cosyvoice）",
        )
    try:
        sample_rate = _tts.get_sample_rate()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取采样率失败：%s", exc)
        sample_rate = 24000

    def _gen():
        try:
            with _TTS_LOCK:
                for pcm in _tts.synthesize_stream(req.text, speed=req.speed):
                    yield pcm
        except Exception as exc:  # noqa: BLE001
            logger.warning("TTS 流式合成失败：%s", exc)

    return StreamingResponse(
        _gen(),
        media_type="application/octet-stream",
        headers={
            "X-Sample-Rate": str(sample_rate),
            "X-Channels": "1",
            "X-Dtype": "int16",
        },
    )


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
                    _asr_ready()
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
                if not _asr_ready():
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
