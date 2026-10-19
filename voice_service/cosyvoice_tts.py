"""本地语音合成（CosyVoice2-0.5B，中文多音字消歧，离线 GPU）。

这是小暖 TTS 的“治本”版本：CosyVoice2 使用 BERT 前端做中文多音字/语义
消歧，自然度与读音准确率明显高于 Piper / Kokoro。默认使用零样本音色复刻，
以随包附带的 ``zero_shot_prompt.wav`` 作为参考音色（温暖女声）。
"""

from __future__ import annotations

import io
import logging
import os
import sys
import threading
import wave
from pathlib import Path

import numpy as np
import torch

# 提速：flow 匹配推理步数默认从 10 降到 6（README 建议值，音质略降、速度明显变快）。
# 仍需用户自行覆盖时可用环境变量 COSYVOICE_FLOW_STEPS 设回（建议范围 4~10）。
# 必须放在首次导入 cosyvoice（内部读取该变量）之前，setdefault 不会覆盖用户已设的值。
os.environ.setdefault("COSYVOICE_FLOW_STEPS", "6")

VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

MODELS_DIR = Path(__file__).resolve().parent / "models"
MODEL_DIR = Path(
    os.environ.get(
        "COSYVOICE_MODEL_DIR",
        str(MODELS_DIR / "cosyvoice2"),
    )
)
PROMPT_WAV = VENDOR_DIR / "zero_shot_prompt.wav"
# 与官方示例保持一致：参考音频对应的原文，用于零样本复刻。
PROMPT_TEXT = "希望你以后能够做的比我还好呦。"
_PROMPT_SPK_ID = "xiaonuan_default"

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000

_model = None
_PROMPT_CACHED = False
_load_lock = threading.Lock()


def _load():
    global _model, _PROMPT_CACHED
    if _model is not None:
        return _model
    with _load_lock:
        if _model is not None:
            return _model
        from cosyvoice.cli.cosyvoice import CosyVoice2

        device = "cuda" if torch.cuda.is_available() else "cpu"
        fp16 = device == "cuda"
        dtype = "fp16" if fp16 else "fp32"
        # 若已一次性导出 JIT / TensorRT 加速文件，则自动启用（更大提速）。
        # 文件名与 CosyVoice2 的 load_jit / load_trt 约定保持一致，缺文件时不启用。
        use_jit = (MODEL_DIR / f"flow.encoder.{dtype}.zip").exists()
        use_trt = (
            (MODEL_DIR / f"flow.decoder.estimator.{dtype}.mygpu.plan").exists()
            and (MODEL_DIR / "flow.decoder.estimator.fp32.onnx").exists()
        )
        _model = CosyVoice2(
            str(MODEL_DIR),
            load_jit=use_jit,
            load_trt=use_trt,
            fp16=fp16,
        )
        # 参考音色是固定的：把“参考音频 -> 说话人向量 + 语音 token”只算一次并缓存。
        # 否则每次 /tts 都要重新跑一遍 onnx 语音 tokenizer（CPU 上约 5-6 秒）。
        try:
            _model.add_zero_shot_spk(PROMPT_TEXT, str(PROMPT_WAV), _PROMPT_SPK_ID)
            _PROMPT_CACHED = True
        except Exception as exc:  # noqa: BLE001 - 缓存失败则回退到每次现算
            _PROMPT_CACHED = False
            logger.warning("预缓存参考音色失败，将每次现算：%s", exc)
        # 预热：跑一次极短合成，触发 CUDA kernel 编译与显卡升频，
        # 避免用户点第一句时出现 5-8 秒的“假延迟”。
        try:
            _kwargs = (
                {"zero_shot_spk_id": _PROMPT_SPK_ID}
                if _PROMPT_CACHED
                else {}
            )
            list(
                _model.inference_zero_shot(
                    "嗯",
                    PROMPT_TEXT,
                    str(PROMPT_WAV),
                    stream=False,
                    speed=1.0,
                    **_kwargs,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 预热失败不影响正式使用
            logger.warning("TTS 预热失败：%s", exc)
    return _model


def is_available() -> bool:
    """模型必需文件是否齐备。"""
    required = [
        MODEL_DIR / "cosyvoice2.yaml",
        MODEL_DIR / "llm.pt",
        MODEL_DIR / "flow.pt",
        MODEL_DIR / "hift.pt",
        MODEL_DIR / "campplus.onnx",
        MODEL_DIR / "speech_tokenizer_v2.onnx",
        MODEL_DIR / "CosyVoice-BlankEN" / "model.safetensors",
    ]
    return all(p.exists() for p in required)


def _empty_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
    return buf.getvalue()


def synthesize(text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
    """文本 -> wav 字节。voice 参数保留兼容，CosyVoice2 使用零样本参考音色。"""
    del voice  # 当前固定使用附带参考音色；后续可扩展为自定义参考音频。
    model = _load()
    _kwargs = (
        {"zero_shot_spk_id": _PROMPT_SPK_ID}
        if _PROMPT_CACHED
        else {}
    )
    chunks = list(
        model.inference_zero_shot(
            text,
            PROMPT_TEXT,
            str(PROMPT_WAV),
            stream=False,
            speed=speed,
            **_kwargs,
        )
    )
    audios = [chunk["tts_speech"] for chunk in chunks if chunk.get("tts_speech") is not None]
    if not audios:
        return _empty_wav()
    audio = torch.cat(audios, dim=1).squeeze(0).cpu().numpy()
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(model.sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def get_sample_rate() -> int:
    """返回实际输出采样率（首次调用会确保模型已加载）。"""
    return _load().sample_rate


def synthesize_stream(text: str, speed: float = 1.0):
    """文本 -> 逐段 PCM16 字节流（int16, mono, little-endian）。

    与 synthesize() 使用相同的零样本参考音色，但走 CosyVoice2 流式推理，
    前端可以边收边播，显著降低「点击到出声」的首音延迟。
    """
    model = _load()
    _kwargs = (
        {"zero_shot_spk_id": _PROMPT_SPK_ID}
        if _PROMPT_CACHED
        else {}
    )
    for chunk in model.inference_zero_shot(
        text,
        PROMPT_TEXT,
        str(PROMPT_WAV),
        stream=True,
        speed=speed,
        **_kwargs,
    ):
        speech = chunk.get("tts_speech")
        if speech is None:
            continue
        audio = speech.squeeze(0).cpu().numpy()
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
        yield pcm.tobytes()
