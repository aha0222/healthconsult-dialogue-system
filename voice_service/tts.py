"""本地语音合成（TTS）：piper 中文音色，离线 CPU 可跑。

听感优化（针对「断句随意、人机感强」的反馈）：
  1. 合成前文本规范化——剥掉 LLM 回复里的 Markdown 残留（**、#、```、列表
     序号）、emoji 与不可读符号；阿拉伯数字转中文读法（"150" → "一百五"，
     piper 对裸数字的读法不稳定且怪）。
  2. 按标点切句逐句合成，句间插入停顿——整段合成时 piper 的韵律/停顿不可控，
     逐句合成后停顿严格跟标点走，长回复的呼吸感明显改善。
  3. 单句过长（> MAX_SUBSENT_CHARS）再按逗号二次切，避免长句内部气息紊乱。

对外契约不变：synthesize(text, speed) -> 完整 wav 字节。
"""

from __future__ import annotations

import io
import re
import wave
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / "models" / "tts"
MODEL = MODELS_DIR / "zh_CN-huayan-medium.onnx"
CONFIG = MODELS_DIR / "zh_CN-huayan-medium.onnx.json"

_voice = None

# 句间停顿（秒）——piper 输出 22050Hz，按采样数插入静音
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
    rough = re.split(r"(?<=[。！？；])", text)
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


def _synthesize_one(text: str, speed: float) -> tuple:
    """单句合成，返回 (采样率, samples bytes, 声宽, 声道)。"""
    from piper.config import SynthesisConfig

    voice = _load()
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


def synthesize(text: str, speed: float = 0.9) -> bytes:
    """文本 -> wav 字节：切句逐段合成，按标点插入停顿后拼接。"""
    pieces = split_sentences(text)
    if not pieces:
        return b""

    rate = width = channels = None
    body = io.BytesIO()
    for sentence, pause in pieces:
        seg_rate, frames, seg_width, seg_channels = _synthesize_one(sentence, speed)
        if rate is None:
            rate, width, channels = seg_rate, seg_width, seg_channels
        body.write(frames)
        if pause > 0:
            body.write(b"\x00" * int(seg_rate * pause) * seg_width * seg_channels)

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setframerate(rate)
        w.setsampwidth(width)
        w.setnchannels(channels)
        w.writeframes(body.getvalue())
    return out.getvalue()
