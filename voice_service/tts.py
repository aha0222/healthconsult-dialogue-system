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
import logging
import re
import threading
import wave
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parent / "models"

# Kokoro torch GPU（82M，5060 实测实时率 0.03x，首选）
# v1.1-zh 是专训普通话的版本（音色 zf_001…zf_099 / zm_xxx，比 v1.0 的
# zf_xiaoxiao 更贴中文韵律）；发音修正表在两条链路上都生效，见 pronunciation.py。
KOKORO_TORCH_LANG = "z"
KOKORO_TORCH_VOICE = "zf_001"
KOKORO_TORCH_REPO = "hexgrad/Kokoro-82M-v1.1-zh"

# Kokoro（sherpa-onnx，82M int8，中英混，速度快于实时，首选）
KOKORO_DIR = MODELS_DIR / "kokoro-int8-multi-lang-v1_1"
KOKORO_MODEL = KOKORO_DIR / "model.int8.onnx"
KOKORO_VOICES = KOKORO_DIR / "voices.bin"
KOKORO_TOKENS = KOKORO_DIR / "tokens.txt"
KOKORO_DATA = KOKORO_DIR / "espeak-ng-data"
KOKORO_DICT = KOKORO_DIR / "dict"
# 中文音色（zf_001）在多语言声音表中的 sid；0 起是英文音色
KOKORO_SID_ZH = 50

# MeloTTS（sherpa-onnx vits）
MELO_DIR = MODELS_DIR / "vits-melo-tts-zh_en"
MELO_MODEL = MELO_DIR / "model.onnx"
MELO_MODEL_INT8 = MELO_DIR / "model.int8.onnx"
MELO_LEXICON = MELO_DIR / "lexicon.txt"
MELO_TOKENS = MELO_DIR / "tokens.txt"
MELO_DICT = MELO_DIR / "dict"

# piper（初版音色）
PIPER_MODEL = MODELS_DIR / "zh_CN-huayan-medium.onnx"
PIPER_CONFIG = MODELS_DIR / "zh_CN-huayan-medium.onnx.json"

_backend = None  # ("melo", handle) / ("piper", handle)

# 句间停顿（秒）——模型自带的句尾衰减之外补充的静音。
# 实测教训：kokoro 每句输出尾部自带较长衰减静音，叠加 0.22s 后停顿明显过长。
SENTENCE_PAUSE_SEC = 0.10
COMMA_PAUSE_SEC = 0.05
# 单句超过该字符数时按逗号二次切分
MAX_SUBSENT_CHARS = 45

# 长句减速（v1.1-zh 专用）：该版训练数据基本在 100 token 以内，长输入会越念越赶。
# 曲线取自官方 make_zh.py：83 音素以内不减速，之后线性放慢，0.6 倍封顶。
# app 先按 ≤45 字切句，所以只有约四分之一的合成单元会命中；整条回复的时长代价
# 实测 +0.6%（250～280 字的长回复 +0.3%～+0.9%），首句（通常很短）不受影响。
SLOW_LONG_UNIT_START = 83
SLOW_LONG_UNIT_FLOOR = 0.6
SLOW_LONG_UNIT_SPAN = 500

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
# 数字间的斜杠：血压/心率惯写 "150/95"，读作"一百五十比九十五"。
# 不处理则斜杠会变成 G2P 的未知音素 ❓（只出声不出字），
# 交给 cn2an 更糟——它按分数读成"九十五分之一百五十"，意思反了。
_DIGIT_SLASH_RE = re.compile(r"(?<=[0-9])\s*/\s*(?=[0-9])")


def normalize_for_tts(text: str, convert_numbers: bool = True) -> str:
    """LLM 回复正文 → 适合 TTS 的口语化文本。

    convert_numbers=False 关掉自写的阿拉伯数字→中文规则，留给 G2P 里的
    cn2an（misaki 中文前端的 an2cn）自己读——用于两条链路的读音对比。
    """
    out = text or ""
    for pattern, repl in _MD_PATTERNS:
        out = pattern.sub(repl, out)
    if convert_numbers:
        # 先补数字间斜杠的口语读法，再逐个数转中文
        out = _DIGIT_SLASH_RE.sub("比", out)
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


def split_sentences(text: str, convert_numbers: bool = True) -> list:
    """按句末标点切句；超长句再按逗号/顿号二次切并降级停顿。返回 [(文本, 停顿秒)]。"""
    text = normalize_for_tts(text, convert_numbers)
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


# ── 后端：Kokoro torch GPU（首选，5060 实时率 0.03x）────────────────

_kokoro_torch_pipe = None
_kokoro_en_pipe = None


def _kokoro_en_callable():
    """英文/拉丁片段的音素化回调（v1.1-zh 官方做法）。

    v1.1 的中文前端把非中文片段一律换成未知音素 ❓，正文里偶尔出现的
    "维生素D""做个CT" 就没了声音。官方 make_zh.py 接一条**无模型**的英文
    管线把英文转成音素交给同一个声学模型。v1.0 走 legacy 路径，不读这个回调。
    """
    global _kokoro_en_pipe
    try:
        # 英文管线内部用 spaCy，缺 en_core_web_sm 时 misaki 会 **当场 pip 下载**。
        # 离线机器上那是必失败的网络调用，所以先自查，缺了就跳过（不触发下载）。
        import spacy

        if not spacy.util.is_package("en_core_web_sm"):
            logger.warning(
                "缺 spaCy 英文模型 en_core_web_sm，正文里的英文将没有声音。"
                "补：python -m spacy download en_core_web_sm（离线机器需预装）"
            )
            return None

        from kokoro import KPipeline

        if _kokoro_en_pipe is None:
            _kokoro_en_pipe = KPipeline(lang_code="a", repo_id=KOKORO_TORCH_REPO, model=False)
        return lambda text: next(_kokoro_en_pipe(text)).phonemes
    except Exception as exc:  # 缺 espeak/misaki[en] 时退回未知音素
        logger.warning("英文片段音素化不可用（%s），正文里的英文将没有声音", exc)
        return None


def _kokoro_torch_available() -> bool:
    try:
        import torch  # noqa: F401
        from kokoro import KPipeline  # noqa: F401
        return True
    except ImportError:
        return False


def _load_kokoro_torch():
    global _kokoro_torch_pipe
    if _kokoro_torch_pipe is not None:
        return _kokoro_torch_pipe
    import os

    # 模型缓存在本机 HF cache；镜像用于首次下载
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    from . import pronunciation

    pronunciation.apply()  # 多音字/助词读音修正（须在首次合成前注入 pypinyin）
    from kokoro import KPipeline

    import torch

    # 不传 device 时 KPipeline 默认 CPU（RT~0.9x）；显式上 GPU（RT~0.02x）
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipe = KPipeline(lang_code=KOKORO_TORCH_LANG, repo_id=KOKORO_TORCH_REPO,
                     device=device, en_callable=_kokoro_en_callable())
    _kokoro_torch_pipe = pipe
    return pipe


def _trim_silence(samples, sample_rate: int, threshold: float = 0.004,
                  keep_head: float = 0.03, keep_tail: float = 0.09):
    """裁剪音频首尾的静音（模型句尾自带拖尾衰减，逐句拼接前必须裁掉）。"""
    import numpy as np

    if len(samples) == 0:
        return samples
    loud = np.where(np.abs(samples) > threshold)[0]
    if len(loud) == 0:
        return samples[: int(sample_rate * keep_head)]
    start = max(0, int(loud[0] - sample_rate * keep_head))
    end = min(len(samples), int(loud[-1] + sample_rate * keep_tail))
    return samples[start:end]


def _slow_speed(speed: float):
    """给 KPipeline 的 speed 回调：音素数越多念得越慢（治长句末尾赶）。

    KPipeline 接受 float 或 Callable[[int], float]；回调收到的是音素数。
    乘在调用方给的 speed 上，所以 /tts 的 speed 参数语义不变，只是长句再慢一点。
    """

    def fn(len_ps: int) -> float:
        factor = 1.0
        if len_ps > SLOW_LONG_UNIT_START:
            factor = max(
                SLOW_LONG_UNIT_FLOOR,
                1 - (len_ps - SLOW_LONG_UNIT_START) / SLOW_LONG_UNIT_SPAN,
            )
        return speed * factor

    return fn


def _kokoro_torch_synthesize(pipe, text: str, speed: float) -> tuple:
    """整段交给 KPipeline（内部按句切分），逐句裁剪拖尾静音后拼接停顿。

    返回 (24000, int16 bytes, 2, 1)。
    """
    import numpy as np
    import torch

    with torch.inference_mode():
        chunks = list(
            pipe(text, voice=KOKORO_TORCH_VOICE,
                 speed=_slow_speed(max(0.5, min(2.0, speed))))
        )
    audios = [
        _trim_silence(c.audio.numpy(), 24000)
        for c in chunks
        if c.audio is not None
    ]
    audios = [a for a in audios if len(a)]
    if not audios:
        return 24000, b"", 2, 1
    pause = np.zeros(int(24000 * SENTENCE_PAUSE_SEC), dtype=audios[0].dtype)
    merged = []
    for i, a in enumerate(audios):
        merged.append(a)
        if i < len(audios) - 1:
            merged.append(pause)
    pcm = (np.clip(np.concatenate(merged), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    return 24000, pcm, 2, 1


# ── 后端：Kokoro（sherpa-onnx，82M int8，首选）──────────────────────

def _kokoro_available() -> bool:
    return KOKORO_MODEL.exists() and KOKORO_VOICES.exists() and KOKORO_TOKENS.exists()


_kokoro_tts = None


def _load_kokoro():
    global _kokoro_tts
    if _kokoro_tts is not None:
        return _kokoro_tts
    import sherpa_onnx

    lexicons = ",".join(
        str(p) for p in (
            KOKORO_DIR / "lexicon-zh.txt",
            KOKORO_DIR / "lexicon-us-en.txt",
            KOKORO_DIR / "lexicon-gb-en.txt",
        ) if p.exists()
    )
    kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
        model=str(KOKORO_MODEL),
        voices=str(KOKORO_VOICES),
        tokens=str(KOKORO_TOKENS),
        lexicon=lexicons,
        data_dir=str(KOKORO_DATA) if KOKORO_DATA.is_dir() else "",
        dict_dir=str(KOKORO_DICT) if KOKORO_DICT.is_dir() else "",
    )
    model_config = sherpa_onnx.OfflineTtsModelConfig(kokoro=kokoro)
    config = sherpa_onnx.OfflineTtsConfig(model=model_config)
    _kokoro_tts = sherpa_onnx.OfflineTts(config)
    return _kokoro_tts


def _kokoro_synthesize_one(tts, text: str, speed: float) -> tuple:
    import numpy as np

    audio = tts.generate(text, sid=KOKORO_SID_ZH, speed=max(0.5, min(2.0, speed)))
    pcm = (np.clip(np.asarray(audio.samples), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    return audio.sample_rate, pcm, 2, 1


# ── 后端：MeloTTS（sherpa-onnx）────────────────────────────────────

def _melo_available() -> bool:
    return MELO_MODEL.exists() and MELO_TOKENS.exists() and MELO_LEXICON.exists()


_melo_tts = None


def _load_melo():
    global _melo_tts
    if _melo_tts is not None:
        return _melo_tts
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
    _melo_tts = sherpa_onnx.OfflineTts(config)
    return _melo_tts


def _melo_synthesize_one(tts, text: str, speed: float) -> tuple:
    """单句合成，返回 (采样率, int16 samples bytes, 声宽=2, 声道=1)。"""
    import numpy as np

    audio = tts.generate(text, sid=0, speed=max(0.5, min(2.0, speed)))
    pcm = (np.clip(np.asarray(audio.samples), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    return audio.sample_rate, pcm, 2, 1


# ── 后端：piper（初版音色，回退用）──────────────────────────────────

def _piper_available() -> bool:
    return PIPER_MODEL.exists() and PIPER_CONFIG.exists()


_piper_voice = None


def _load_piper():
    global _piper_voice
    if _piper_voice is not None:
        return _piper_voice
    from piper import PiperVoice

    _piper_voice = PiperVoice.load(str(PIPER_MODEL), config_path=str(PIPER_CONFIG))
    return _piper_voice


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
    if _kokoro_torch_available():
        _backend = ("kokoro-gpu", lambda text, speed: _kokoro_torch_synthesize(
            _load_kokoro_torch(), text, speed))
    elif _kokoro_available():
        _backend = ("kokoro", lambda text, speed: _kokoro_synthesize_one(_load_kokoro(), text, speed))
    elif _melo_available():
        _backend = ("melo", lambda text, speed: _melo_synthesize_one(_load_melo(), text, speed))
    elif _piper_available():
        _backend = ("piper", lambda text, speed: _piper_synthesize_one(_load_piper(), text, speed))
    else:
        raise RuntimeError("没有任何可用的 TTS 模型（kokoro / MeloTTS / piper 均未下载）")
    return _backend


def is_available() -> bool:
    """是否有任一 TTS 后端可用。"""
    return _kokoro_available() or _melo_available() or _piper_available()


_SYNTH_LOCK = threading.Lock()


@lru_cache(maxsize=64)
def _synthesize_cached(text: str, speed: float) -> bytes:
    """带 LRU 缓存的合成：重听/重复播报零延迟。"""
    _, synth_one = get_backend()
    pieces = split_sentences(text)
    if not pieces:
        return b""

    rate = width = channels = None
    body = io.BytesIO()
    with _SYNTH_LOCK:  # 合成器非线程安全：并发请求（预取+重听）串行执行
        for k, (sentence, pause) in enumerate(pieces):
            seg_rate, frames, seg_width, seg_channels = synth_one(sentence, speed)
            if rate is None:
                rate, width, channels = seg_rate, seg_width, seg_channels
            body.write(frames)
            # 末片不补停顿：句尾死空气只会推迟 onended；句间停顿由播放端队列控制
            if pause > 0 and k < len(pieces) - 1:
                body.write(b"\x00" * (int(seg_rate * pause) * seg_width * seg_channels))

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setframerate(rate)
        w.setsampwidth(width)
        w.setnchannels(channels)
        w.writeframes(body.getvalue())
    return out.getvalue()


def synthesize(text: str, speed: float = 0.9) -> bytes:
    """文本 -> wav 字节（带 LRU 缓存，重听秒出）。"""
    return _synthesize_cached(text or "", speed)
