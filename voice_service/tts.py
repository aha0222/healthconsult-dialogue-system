"""本地语音合成（TTS）：piper 中文音色，离线 CPU 可跑。"""

from __future__ import annotations

import io
import wave
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models" / "tts"
MODEL = MODELS_DIR / "zh_CN-huayan-medium.onnx"
CONFIG = MODELS_DIR / "zh_CN-huayan-medium.onnx.json"

_voice = None


def _load():
    global _voice
    if _voice is not None:
        return _voice
    from piper import PiperVoice

    _voice = PiperVoice.load(str(MODEL), config_path=str(CONFIG))
    return _voice


def is_available() -> bool:
    """模型文件是否已下载。"""
    return MODEL.exists() and CONFIG.exists()


def synthesize(text: str, speed: float = 0.9) -> bytes:
    """文本 -> wav 字节。speed 越小语速越慢（映射为 piper 的 length_scale）。"""
    from piper.config import SynthesisConfig

    voice = _load()
    length_scale = 1.0 / max(0.5, min(2.0, speed))
    syn_config = SynthesisConfig(length_scale=length_scale)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file, syn_config=syn_config)
    return buf.getvalue()
