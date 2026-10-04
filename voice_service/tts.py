"""本地语音合成（TTS）：MeloTTS（sherpa-onnx）优先，piper 回退，离线 CPU/GPU 可跑。

听感优化管线（针对「断句随意、人机感强」的反馈）：
  1. 合成前文本规范化——剥掉 LLM 回复里的 Markdown 残留（**、#、```、列表
     序号）、emoji 与不可读符号；阿拉伯数字转中文读法（"150" → "一百五"）。
  2. 按标点切句逐句合成，句间插入停顿——停顿严格跟标点走，恢复呼吸感。
  3. 单句过长（> MAX_SUBSENT_CHARS）再按逗号二次切。

后端选择（模型都在 voice_service/models/，由下载脚本获取，不入库）：
  - vits-melo-tts-zh_en/ 目录存在 → sherpa-onnx MeloTTS（中英混，韵律自然，首选）
  - 否则 zh_CN-huayan-medium.onnx 存在 → piper（初版音色，兜底）
两者都没有 → is_available() 为 False，/tts 走 501 回退分支。

对外契约不变：synthesize(text, speed) -> 完整 wav 字节。
"""

from __future__ import annotations

import io
import re
import wave
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models"

# MeloTTS（sherpa-onnx vits）
MELO_DIR = MODELS_DIR / "vits-melo-tts-zh_en"
MELO_MODEL = MELO_DIR / "model.onnx"
MELO_LEXICON = MELO_DIR / "lexicon.txt"
MELO_TOKENS = MELO_DIR / "tokens.txt"
MELO_DICT = MELO_DIR / "dict"

# piper（初版音色）
PIPER_MODEL = MODELS_DIR / "zh_CN-huayan-medium.onnx"
PIPER_CONFIG = MODELS_DIR / "zh_CN-huayan-medium.onnx.json"

_backend = None  # ("melo", handle) / ("piper", handle)

# 句间停顿（秒）——按各后端采样率换算成静音帧
SENTENCE_PAUSE_SEC = 0.22
COMMA_PAUSE_SEC = 0.10
# 单句超过该字符数时按逗号二次切分
MAX_SUBSENT_CHARS = 45

_MD_PATTERNS = [
    (re.compile(r"```[a-zA-Z]*\n?"), ""),          # 代码围栏
    (re.compile(r"\*\*([^*]*)\*\*"), r"\1"),        # 粗体
    (re.compile(r"\*([^*\n]+)\*"), r"\1"),          # 斜体
    (re.compile(r"^#{1,6}\s*", re.M), ""),          # 标题井号
    (re.compile(r"^\s*[-*•]\s+", re.M), ""),        # 无序列表符
    (re.compile(r"^\s*\d+[.、)]\s*", re.M), ""),    # 有序列表序号
    (re.compile(r"\[(.*?)\]\(.*?\)"), r"\1"),       # 链接保留文字
    (re.compile(r"`([^`]*)`"), r"\1"),              # 行内代码
    (re.compile(r"[|#*`~>\\]"), ""),                # 残余符号
    (re.compile(
        r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]"), ""),  # emoji/符号
    (re.compile(r"\n{2,}"), "。"),                  # 段落边界给一个句停
    (re.compile(r"\n"), "，"),
]

_CN_DIGITS = "零一二三四五六七八九"


def _number_to_cn(m: "re.Match") -> str:
    """把 1~9999 的整数读法转成中文（"150" → "一百五"，"120" → "一百二"）。

    采用口语读法：非末尾的零省略（120 读"一百二"而不是"一百二十零"）。
    """
    s = m.group(0)
    if len(s) > 4:
        return s
    digits = [int(c) for c in s]
    units = ["", "十", "百", "千"]
    parts = []
    n = len(digits)
    for i, d in enumerate(digits):
        pos = n - i - 1  # 位权索引：0=个位
        if d == 0:
            if parts and not parts[-1].endswith("零") and pos > 0:
                parts.append("零")
            continue
        if d == 1 and pos == 1:
            parts.append("十")  # 口语：10~19 读"十X"而不是"一十X"
        else:
            parts.append(_CN_DIGITS[d] + units[pos])
    out = "".join(parts).rstrip("零")
    return out or s


_NUMBER_RE = re.compile(r"(?<![0-9.])[1-9][0-9]{0,3}(?![0-9.])")


def normalize_for_tts(text: str) -> str:
    """LLM 回复正文 → 适合 TTS 的口语化文本。"""
    out = text or ""
    for pattern, repl in _MD_PATTERNS:
        out = pattern.sub(repl, out)
    out = _NUMBER_RE.sub(_number_to_cn, out)
    # 连续标点收敛（含跨标点类型：冒号+逗号 → 单逗号），去句首悬垂标点
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"([，。！？；：、])\s*(?=[，。！？；：、])", r"\1", out)
    out = re.sub(r"([，。！？；])\s*，", r"\1", out)
    out = re.sub(r"：\s*，", "：", out)
    # 中文之间的空格对 TTS 无意义，删除（保留英文单词间空格）
    out = re.sub(r"(?<=[\u4e00-\u9fff，。！？；：、])\s+(?=[\u4e00-\u9fff])", "", out)
    out = re.sub(r"\s+([，。！？；：、])", r"\1", out)
    return out.strip()


def split_sentences(text: str) -> list:
    """按句末标点切句；超长句再按逗号/顿号二次切并降级停顿。返回 [(文本, 停顿秒)]。"""
    text = normalize_for_tts(text)
    if not text:
        return []
    rough = re.split(r"(?<=[。！？；：])", text)
    result = []
    for chunk in rough:
        chunk = chunk.strip().lstrip("，、；：")
        if not chunk:
            continue
        if len(chunk) <= MAX_SUBSENT_CHARS:
            result.append((chunk, SENTENCE_PAUSE_SEC))
            continue
        pieces = [p for p in re.split(r"(?<=[，、])", chunk) if p.strip()]
        for j, piece in enumerate(pieces):
            piece = piece.strip().lstrip("，、；：")
            if not piece:
                continue
            pause = SENTENCE_PAUSE_SEC if j == len(pieces) - 1 else COMMA_PAUSE_SEC
            result.append((piece, pause))
    return result


# ── 后端：MeloTTS（sherpa-onnx）────────────────────────────────────

def _melo_available() -> bool:
    return MELO_MODEL.exists() and MELO_TOKENS.exists() and MELO_LEXICON.exists()


def _load_melo():
    import sherpa_onnx

    # int8 量化版优先（CPU 上速度约 2 倍，质量损失可忽略）
    model = MELO_DIR / "model.int8.onnx"
    if not model.exists():
        model = MELO_MODEL
    # rule_fsts：数字/日期/电话/多音字的文本规范化规则（模型目录自带）
    rule_fsts = ",".join(
        str(p) for p in (
            MELO_DIR / "number.fst",
            MELO_DIR / "date.fst",
            MELO_DIR / "new_heteronym.fst",
            MELO_DIR / "phone.fst",
        ) if p.exists()
    )
    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(model),
        lexicon=str(MELO_LEXICON),
        tokens=str(MELO_TOKENS),
        dict_dir=str(MELO_DICT) if MELO_DICT.is_dir() else "",
    )
    model_config = sherpa_onnx.OfflineTtsModelConfig(vits=vits)
    config = sherpa_onnx.OfflineTtsConfig(model=model_config, rule_fsts=rule_fsts)
    return sherpa_onnx.OfflineTts(config)


def _melo_synthesize_one(tts, text: str, speed: float) -> tuple:
    """单句合成，返回 (采样率, int16 samples bytes, 声宽=2, 声道=1)。"""
    import numpy as np

    audio = tts.generate(text, sid=0, speed=max(0.5, min(2.0, speed)))
    pcm = (np.clip(np.asarray(audio.samples), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    return audio.sample_rate, pcm, 2, 1


# ── 后端：piper（初版音色，回退用）──────────────────────────────────

def _piper_available() -> bool:
    return PIPER_MODEL.exists() and PIPER_CONFIG.exists()


def _load_piper():
    from piper import PiperVoice

    return PiperVoice.load(str(PIPER_MODEL), config_path=str(PIPER_CONFIG))


def _piper_synthesize_one(voice, text: str, speed: float) -> tuple:
    from piper.config import SynthesisConfig

    length_scale = 1.0 / max(0.5, min(2.0, speed))
    syn_config = SynthesisConfig(length_scale=length_scale)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file, syn_config=syn_config)
    wav_bytes = buf.getvalue()
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        return (
            r.getframerate(),
            r.readframes(r.getnframes()),
            r.getsampwidth(),
            r.getnchannels(),
        )


# ── 对外接口 ───────────────────────────────────────────────────────

def get_backend() -> tuple:
    """惰性选择并缓存合成后端，返回 (名称, 句级合成函数)。"""
    global _backend
    if _backend is not None:
        return _backend
    if _melo_available():
        _backend = ("melo", lambda text, speed: _melo_synthesize_one(_load_melo(), text, speed))
    elif _piper_available():
        _backend = ("piper", lambda text, speed: _piper_synthesize_one(_load_piper(), text, speed))
    else:
        raise RuntimeError("没有任何可用的 TTS 模型（MeloTTS / piper 均未下载）")
    return _backend


def is_available() -> bool:
    """是否有任一 TTS 后端可用。"""
    return _melo_available() or _piper_available()


def synthesize(text: str, speed: float = 0.9) -> bytes:
    """文本 -> wav 字节：切句逐段合成，按标点插入停顿后拼接。"""
    _, synth_one = get_backend()
    pieces = split_sentences(text)
    if not pieces:
        return b""

    rate = width = channels = None
    body = io.BytesIO()
    for sentence, pause in pieces:
        seg_rate, frames, seg_width, seg_channels = synth_one(sentence, speed)
        if rate is None:
            rate, width, channels = seg_rate, seg_width, seg_channels
        body.write(frames)
        if pause > 0:
            body.write(b"\x00" * (int(seg_rate * pause) * seg_width * seg_channels))

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setframerate(rate)
        w.setsampwidth(width)
        w.setnchannels(channels)
        w.writeframes(body.getvalue())
    return out.getvalue()
