"""能量 VAD：轻量、无模型，用于断句与「打断」检测。"""

from __future__ import annotations

import numpy as np


def pcm16_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """int16 PCM 字节 -> float32 归一化到 [-1, 1]。"""
    return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0


def rms(samples: np.ndarray) -> float:
    """样本均方根，衡量响度。"""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))


def is_speech(samples: np.ndarray, threshold: float = 0.01) -> bool:
    """float32 样本是否含语音（响度超过阈值）。"""
    return rms(samples) > threshold


def is_speech_pcm16(pcm_bytes: bytes, threshold: float = 0.01) -> bool:
    """int16 PCM 字节是否含语音。"""
    return is_speech(pcm16_to_float32(pcm_bytes), threshold)
