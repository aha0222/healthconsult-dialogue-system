"""本地语音识别（ASR）：sherpa-onnx 中文 Paraformer，离线 CPU 可跑。"""

from __future__ import annotations

import io
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models" / "asr"
PARAFORMER = MODELS_DIR / "model.int8.onnx"
TOKENS = MODELS_DIR / "tokens.txt"

TARGET_SAMPLE_RATE = 16000

_recognizer = None


def _load():
    global _recognizer
    if _recognizer is not None:
        return _recognizer
    import sherpa_onnx

    _recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(PARAFORMER),
        tokens=str(TOKENS),
        num_threads=2,
        sample_rate=TARGET_SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        debug=False,
    )
    return _recognizer


def is_available() -> bool:
    """模型文件是否已下载。"""
    return PARAFORMER.exists() and TOKENS.exists()


def _resample(x, src_rate: int, dst_rate: int):
    """简单的线性插值重采样（把任意采样率降到 16kHz）。"""
    import numpy as np

    if src_rate == dst_rate:
        return x
    if len(x) == 0:
        return x
    ratio = src_rate / dst_rate
    n_out = max(1, int(len(x) / ratio))
    idx = np.arange(n_out) * ratio
    i0 = np.floor(idx).astype(np.int64)
    i1 = np.minimum(i0 + 1, len(x) - 1)
    frac = idx - i0
    return (x[i0] * (1.0 - frac) + x[i1] * frac).astype(np.float32)


def _read_samples(wav_bytes: bytes):
    import numpy as np
    import soundfile as sf

    samples, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
    if samples.ndim > 1:
        samples = samples[:, 0]
    samples = np.ascontiguousarray(samples, dtype=np.float32)
    if sample_rate != TARGET_SAMPLE_RATE:
        samples = _resample(samples, sample_rate, TARGET_SAMPLE_RATE)
    return samples


def _decode(samples) -> tuple[str, bool, float]:
    import numpy as np

    samples = np.ascontiguousarray(samples, dtype=np.float32)
    if samples.size == 0:
        return "", True, 0.0

    recognizer = _load()
    stream = recognizer.create_stream()
    stream.accept_waveform(TARGET_SAMPLE_RATE, samples)
    recognizer.decode_stream(stream)

    result = getattr(stream, "result", "")
    text = result if isinstance(result, str) else getattr(result, "text", "") or ""
    text = (text or "").strip()
    # Paraformer 不直接给出逐句置信度，这里用朴素估计：有文本 1.0，无文本 0.0。
    confidence = 1.0 if text else 0.0
    return text, True, confidence


def transcribe(wav_bytes: bytes):
    """wav 字节 -> (文本, is_final, confidence)。"""
    try:
        samples = _read_samples(wav_bytes)
    except Exception:
        # 音频本身无法解码（非法/损坏的 wav）：视为「没听清」，返回空结果
        return "", True, 0.0
    return _decode(samples)


def transcribe_pcm16(pcm_bytes: bytes, sample_rate: int = TARGET_SAMPLE_RATE):
    """int16 PCM 字节（默认 16kHz 单声道）-> (文本, is_final, confidence)。"""
    import numpy as np

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if sample_rate != TARGET_SAMPLE_RATE:
        samples = _resample(samples, sample_rate, TARGET_SAMPLE_RATE)
    return _decode(samples)
