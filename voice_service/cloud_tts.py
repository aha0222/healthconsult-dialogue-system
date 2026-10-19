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


def synthesize(text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
    """文本 -> wav 字节。任何失败都抛异常，由调用方回退本地引擎。"""
    import requests

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

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
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

    audio_resp = requests.get(audio_url, timeout=60)
    if audio_resp.status_code != 200:
        raise RuntimeError(f"下载云端音频失败：HTTP {audio_resp.status_code}")
    return audio_resp.content
