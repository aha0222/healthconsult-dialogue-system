"""小暖语音服务 · 契约空壳（Day0 冻结版）。

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

当前状态：**空壳**。
  - /health        永远可用（便于 Day0 接线与探活）。
  - /asr /tts /voice/stream  返回 501 Not Implemented，调用方据此回退到打字/静默。
  - A 的交付标准：把下面三个 501 换成真实实现，**签名与事件名一个字不许改**。

依赖：fastapi / uvicorn（见 requirements.txt；空壳期不依赖 python-multipart）。
启动：python -m voice_service   （等价于 uvicorn voice_service.app:app --port 8100）
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import CONTRACT_VERSION, DEFAULT_PORT, SERVICE_NAME, __version__

app = FastAPI(title="小暖语音服务", version=__version__)

# 前端多为 file:// 或本地端口，允许跨域直连（语音服务不持有任何密钥，放行安全）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 能力就绪开关：A 实现后置为 "1"（或直接恒为 True）。空壳期为 False。
ASR_READY = os.environ.get("VOICE_ASR_READY", "0") == "1"
TTS_READY = os.environ.get("VOICE_TTS_READY", "0") == "1"


class TTSRequest(BaseModel):
    """TTS 请求体（契约字段，A 不得增删必填项）。"""

    text: str
    voice: Optional[str] = None
    format: str = "wav"
    speed: float = 0.9


def _not_implemented(what: str) -> None:
    """统一 501。前端收到 501 即回退到打字输入 / 静默。"""
    raise HTTPException(
        status_code=501,
        detail=f"{what} 尚未实现（voice_service 契约空壳，等待 A 交付）",
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
    A 实现时保持字段不变；空壳期返回 501。
    """
    _ = await request.body()  # 空壳期丢弃；A 实现时在此调用本地 ASR
    _not_implemented("ASR")


@app.post("/tts")
def tts(req: TTSRequest):
    """文本 -> wav 音频字节流（Content-Type: audio/wav）。

    A 实现时返回 fastapi.responses.Response(content=wav_bytes,
    media_type="audio/wav")；空壳期返回 501。
    """
    _not_implemented("TTS")


@app.websocket("/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    """双向流式语音事件通道（A 实现）。

    事件名（冻结）：
      客户端 -> 服务端：audio_chunk(二进制) / commit / interrupt / ping
      服务端 -> 客户端：speech_start / partial{text} / final{text}
                        / reply_audio(二进制) / interrupt / error{code,message}
    空壳期：接受连接后发一条 error 再关闭，避免前端卡死。
    """
    await ws.accept()
    try:
        await ws.send_json(
            {
                "event": "error",
                "code": "not_implemented",
                "message": "语音流式接口尚未实现（voice_service 契约空壳）",
            }
        )
    except WebSocketDisconnect:
        return
    await ws.close()


if __name__ == "__main__":  # pragma: no cover - 便捷调试入口
    import uvicorn

    uvicorn.run(
        "voice_service.app:app",
        host=os.environ.get("VOICE_HOST", "127.0.0.1"),
        port=int(os.environ.get("VOICE_PORT", str(DEFAULT_PORT))),
    )
