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
import re
from typing import Optional

import asyncio
import json
import logging
import threading
from contextlib import contextmanager
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
from .tone import instruct_for, tts_params
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
# 排队上限：真卡住时宁可让前端回退浏览器语音，也不要让请求无限等下去
# （演示时"点了没反应"比"换了个音色"糟得多）。
_TTS_LOCK_TIMEOUT = 30.0


@contextmanager
def _tts_guard():
    if not _TTS_LOCK.acquire(timeout=_TTS_LOCK_TIMEOUT):
        logger.warning("TTS 推理锁等待超时（%.0f 秒），返回 503", _TTS_LOCK_TIMEOUT)
        raise HTTPException(
            status_code=503,
            detail="语音合成繁忙，请稍后重试（前端会自动回退浏览器语音）",
        )
    try:
        yield
    finally:
        _TTS_LOCK.release()

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
    # 云端语音：后台预热一次（建 TLS 连接 + 远端模型热起来）。
    # 实测第一次 /tts 会多等半秒到一秒，表现就是"开场有段空白"。
    if _cloud_tts.is_available():
        threading.Thread(
            target=_cloud_tts.warmup, daemon=True, name="cloud-tts-warmup"
        ).start()


_preload_models()


class TTSRequest(BaseModel):
    """TTS 请求体（契约字段，A 不得增删必填项）。"""

    text: str
    voice: Optional[str] = None
    format: str = "wav"
    speed: float = 0.9
    engine: Optional[str] = None
    # 这条回复的风险等级（R0/R1/R2a/R2b/R3）。仅用于选播报语气，缺省按普通情况。
    risk: Optional[str] = None


# ── 引擎无关的文本清洗 ────────────────────────────────────────────────
# 只做「模型不该念出来的东西」：风险标记、markdown 残留、代码围栏、链接。
# 刻意不动数字与读音——那是各引擎自己的文本规范化（云端与 CosyVoice2 都自带），
# 早先为 Kokoro 写的 normalize_for_tts / 数字转中文 / 长句减速只属于本地弱模型路径。
_MARKER_RE = re.compile(r"\[(?:RISK|SCENE)\s*:[^\]]*\]")
# 整块代码围栏直接丢掉：里面的内容念出来对老人没有意义
_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.S)
_FENCE_RE = re.compile(r"```[a-zA-Z]*|```")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
_LIST_RE = re.compile(r"^[ \t]*(?:[-*•]|\d+[.)、])[ \t]+", re.M)
_JUNK_RE = re.compile(r"[|~>\\]")
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")
# 数字间的斜杠：血压惯写 150/95。直接送引擎会被读成"除以"（云端实测）或
# "九十五分之一百五十"（本地 CosyVoice2 实测，方向还反了），所以换成"比"。
# 只处理两侧 1~3 位数字、且不连着别的数字或斜杠的情况——日期 2024/10/09 不会被改。
_DIGIT_SLASH_RE = re.compile(r"(?<![\d/])(\d{1,3})\s*/\s*(\d{1,3})(?![\d/])")


def clean_for_tts(text: str) -> str:
    """把模型回复整理成「适合念出来」的纯文本（与具体引擎无关）。"""
    out = text or ""
    out = _MARKER_RE.sub("", out)
    out = _FENCE_BLOCK_RE.sub("", out)
    out = _FENCE_RE.sub("", out)
    out = _LINK_RE.sub(r"\1", out)
    out = _BOLD_RE.sub(r"\1", out)
    out = _ITALIC_RE.sub(r"\1", out)
    out = _INLINE_CODE_RE.sub(r"\1", out)
    out = _HEADING_RE.sub("", out)
    out = _LIST_RE.sub("", out)
    out = _EMOJI_RE.sub("", out)
    out = _DIGIT_SLASH_RE.sub(r"\1比\2", out)
    out = _JUNK_RE.sub("", out)
    out = re.sub(r"[ \t]+", " ", out)
    # 换行在语音里没有意义：空行当句号、单行当逗号，保持停顿感
    out = re.sub(r"\n{2,}", "。", out)
    out = re.sub(r"\n", "，", out)
    # 去掉标记后常留下「。，」这种悬垂标点，收敛一下
    out = re.sub(r"([。！？；])[，、；：]+", r"\1", out)
    out = re.sub(r"^[，、；：]+", "", out)
    out = re.sub(r"\s*([，。！？；：、])\s*", r"\1", out)
    return out.strip()


def trim_wav_silence(wav_bytes: bytes, head_ms: int = 30, tail_ms: int = 80,
                     threshold: float = 0.004) -> bytes:
    """裁掉合成音频首尾的静音。

    实测云端与本地都会在开头留 0.35–0.45 秒空白，听起来像"开场顿一下"。
    只处理 16 位 PCM；解析失败或整段无声时原样返回，不影响合成主流程。
    """
    import io as _io
    import wave as _wave

    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy 已是运行依赖
        return wav_bytes
    try:
        with _wave.open(_io.BytesIO(wav_bytes)) as r:
            params = r.getparams()
            frames = r.readframes(r.getnframes())
    except Exception:  # noqa: BLE001
        return wav_bytes
    if params.sampwidth != 2 or not frames:
        return wav_bytes

    audio = np.frombuffer(frames, dtype="<i2")
    loud = np.where(np.abs(audio) > int(threshold * 32767))[0]
    if loud.size == 0:
        return wav_bytes

    ch = params.nchannels
    sr = params.framerate
    start = max(0, int(loud[0]) - int(sr * head_ms / 1000) * ch)
    end = min(audio.size, int(loud[-1]) + int(sr * tail_ms / 1000) * ch)
    start -= start % ch          # 对齐到帧边界，多声道不会错位
    end -= end % ch
    if start == 0 and end == audio.size:
        return wav_bytes

    out = _io.BytesIO()
    with _wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(audio[start:end].tobytes())
    return out.getvalue()


def apply_pitch(wav_bytes: bytes, pitch: float) -> bytes:
    """音高参数（代理实现）：按 pitch 倍变速，再重采样回原采样率。

    变速不变调做不到"只改音高"，但急症场景要的就是整体沉下来、慢下来，
    这个近似听感是"低声、放缓"，足够；speed 已同步调低，不会叠出"变尖"。
    """
    if not pitch or abs(pitch - 1.0) < 0.01:
        return wav_bytes

    import io as _io
    import wave as _wave

    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy 已是运行依赖
        return wav_bytes
    try:
        with _wave.open(_io.BytesIO(wav_bytes)) as r:
            params = r.getparams()
            frames = r.readframes(r.getnframes())
    except Exception:  # noqa: BLE001
        return wav_bytes
    if params.sampwidth != 2 or not frames:
        return wav_bytes

    audio = np.frombuffer(frames, dtype="<i2")
    ch = params.nchannels
    n = audio.size // ch
    if n < 2:
        return wav_bytes
    src = np.arange(n, dtype=np.float64) * float(pitch)   # 双线性插值取样点
    lo = np.floor(src).astype(np.int64)
    hi = np.minimum(lo + 1, n - 1)
    frac = (src - lo)[:, None]
    x = audio.reshape(n, ch).astype(np.float64)
    mixed = x[lo] * (1.0 - frac) + x[hi] * frac
    out_audio = np.clip(np.rint(mixed), -32768, 32767).astype("<i2")

    out = _io.BytesIO()
    with _wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(out_audio.reshape(-1).tobytes())
    return out.getvalue()


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
    text = clean_for_tts(req.text)
    if not text:
        raise HTTPException(status_code=400, detail="文本为空（清洗后无可朗读内容）")

    if req.engine == "cloud":
        # 云端 v3 是自回归大模型，语气跟着文本内容走，实测已正常，不额外干预。
        if not _cloud_tts.is_available():
            logger.warning("请求云端语音但未配置 DASHSCOPE_API_KEY，回退本地引擎")
        else:
            try:
                wav_bytes = _cloud_tts.synthesize(text, voice=req.voice, speed=req.speed)
                return Response(
                    content=trim_wav_silence(wav_bytes),
                    media_type="audio/wav",
                )
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
    # 本地引擎：CosyVoice2 用语气指令（音色不变、只改语气），
    # Kokoro/Piper 没有指令能力，退回"放慢 + 压低音高"。
    speed, pitch = tts_params(req.speed, req.risk)
    instruct = instruct_for(req.risk) if TTS_ENGINE == "cosyvoice" else None
    if instruct:
        pitch = 1.0
    try:
        with _tts_guard():
            if TTS_ENGINE == "cosyvoice":
                wav_bytes = _tts.synthesize(
                    text, voice=req.voice, speed=speed, instruct=instruct
                )
            elif TTS_ENGINE == "kokoro":
                wav_bytes = _tts.synthesize(
                    text, voice=req.voice or _TTS_DEFAULT_VOICE, speed=speed
                )
            else:
                wav_bytes = _tts.synthesize(text, speed=speed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS 合成失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"TTS 合成失败：{exc}")
    return Response(content=apply_pitch(trim_wav_silence(wav_bytes), pitch), media_type="audio/wav")


@app.post("/tts/stream")
def tts_stream(req: TTSRequest):
    """文本 -> 流式 PCM16（application/octet-stream），供前端边合成边播放。

    采样率 / 声道 / 位深通过响应头返回：X-Sample-Rate / X-Channels / X-Dtype。
    只有云端引擎走流式；本地引擎一律 501，前端回退到 /tts 分块播放。
    """
    text = clean_for_tts(req.text)
    if not text:
        raise HTTPException(status_code=400, detail="文本为空（清洗后无可朗读内容）")
    if req.engine == "cloud":
        # 云端 v3 是自回归大模型，语气跟着文本走，实测已正常，不额外干预。
        if not _cloud_tts.is_available():
            logger.warning("请求云端流式语音但未配置 DASHSCOPE_API_KEY，回退本地引擎")
        else:
            # 先取到第一块再返回响应头：连接/鉴权失败能干净地回退到 /tts，
            # 而不是先回 200 再断流。
            try:
                gen = _cloud_tts.synthesize_stream_pcm(text, voice=req.voice, speed=req.speed)
                first = next(gen)
            except Exception as exc:  # noqa: BLE001
                logger.warning("云端流式合成失败，回退本地引擎：%s", exc)
            else:
                def _cloud_gen():
                    yield first
                    yield from gen

                return StreamingResponse(
                    _cloud_gen(),
                    media_type="application/octet-stream",
                    headers={
                        "X-Sample-Rate": "24000",
                        "X-Channels": "1",
                        "X-Dtype": "int16",
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",
                    },
                )
    # 本地引擎不做流式：实测 CosyVoice2 流式 1 秒音频要 1.2–2.2 秒才合成完
    # （RTF > 1，边收边播必然断粮），整段合成的 RTF 只有 0.7–1.0，前端分块
    # + 预取反而更顺。另外同步生成器会被客户端中断打断，而它同时持有模型锁，
    # 中途断开会把锁永久卡住——后续所有合成都排队等死。所以这里直接 501，
    # 让前端走 /tts 的分块路径。
    raise HTTPException(
        status_code=501,
        detail="本地引擎不提供流式合成，请使用 /tts（前端会自动回退到分块播放）",
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
