"""本地语音合成（Kokoro v1.1 中文，GPU 加速，离线）。"""

from __future__ import annotations

import io
import threading
import wave
from pathlib import Path

import numpy as np
import torch

MODELS_DIR = Path(__file__).resolve().parent / "models" / "kokoro"
MODEL = MODELS_DIR / "kokoro-v1_1-zh.pth"
CONFIG = MODELS_DIR / "config.json"
VOICES_DIR = MODELS_DIR / "voices"

REPO = "hexgrad/Kokoro-82M-v1.1-zh"
SAMPLE_RATE = 24000

_pipeline = None
_load_lock = threading.Lock()

# "得"的必须义（děi）常见组合：只覆盖这些"得+动词"，不影响"得到/觉得/记得"等
_DEI_PHRASES = {
    "得去": [["dei3"], ["qu4"]],
    "得做": [["dei3"], ["zuo4"]],
    "得看": [["dei3"], ["kan4"]],
    "得买": [["dei3"], ["mai3"]],
    "得改": [["dei3"], ["gai3"]],
    "得注意": [["dei3"], ["zhu4"], ["yi4"]],
    "得小心": [["dei3"], ["xiao3"], ["xin1"]],
    "得抓紧": [["dei3"], ["zhua1"], ["jin3"]],
    "得吃饭": [["dei3"], ["chi1"], ["fan4"]],
    "得做饭": [["dei3"], ["zuo4"], ["fan4"]],
    "得睡觉": [["dei3"], ["shui4"], ["jiao4"]],
    "得喝水": [["dei3"], ["he1"], ["shui3"]],
    "得吃药": [["dei3"], ["chi1"], ["yao4"]],
    "得看病": [["dei3"], ["kan4"], ["bing4"]],
    "得复查": [["dei3"], ["fu4"], ["cha2"]],
    "得锻炼": [["dei3"], ["duan4"], ["lian4"]],
    "得坚持": [["dei3"], ["jian1"], ["chi2"]],
    "得记住": [["dei3"], ["ji4"], ["zhu4"]],
    "得保证": [["dei3"], ["bao3"], ["zheng4"]],
    "得先": [["dei3"], ["xian1"]],
    "得让": [["dei3"], ["rang4"]],
    "得学": [["dei3"], ["xue2"]],
    "得休息": [["dei3"], ["xiu1"], ["xi1"]],
    "得按时": [["dei3"], ["an4"], ["shi2"]],
    "得量": [["dei3"], ["liang2"]],
    "得测": [["dei3"], ["ce4"]],
    "得打针": [["dei3"], ["da3"], ["zhen1"]],
    "得住院": [["dei3"], ["zhu4"], ["yuan4"]],
}

# "得"读 de/dé 的常用词：jieba 分词时优先级必须高于上面的"得+动词"，
# 否则「值得做」会被切成「值/得做」读成 zhí děi zuò，「记得吃药」同理。
_DE_PROTECT = (
    "值得", "记得", "觉得", "舍得", "舍不得", "免得", "省得", "懂得",
    "显得", "晓得", "使得", "懒得", "难得", "怪不得", "恨不得",
    "巴不得", "由不得", "要不得", "不见得", "认不得", "得到", "获得",
)


def _load():
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    with _load_lock:
        if _pipeline is not None:
            return _pipeline
        from kokoro import KModel, KPipeline
        from misaki import en
        from pypinyin import load_phrases_dict
        import jieba

        from . import pronunciation

        # 多音字/结构助词修正（得/地/觉/着…）。必须在首次合成前注入 pypinyin，
        # 否则这条引擎路径会绕过 pronunciation.py 的全部修正。
        pronunciation.apply()

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = KModel(repo_id=REPO, config=str(CONFIG), model=str(MODEL)).to(device).eval()
        _en_g2p = en.G2P(trf=False)
        # misaki.zh 期望 en_callable 返回音素字符串，而 en.G2P 返回 (音素, tokens) 元组
        en_callable = lambda text: _en_g2p(text)[0]
        _pipeline = KPipeline(lang_code="z", repo_id=REPO, model=model, en_callable=en_callable)
        # 让 jieba 把"得+动词"当成一个词，再用 pypinyin 词表映射到 děi（避免分词拆开）。
        # 频率要低于 _DE_PROTECT：否则「值得做/记得吃药」会被错误合并。
        for phrase in _DEI_PHRASES:
            jieba.add_word(phrase, freq=1000)
        for word in _DE_PROTECT:
            jieba.add_word(word, freq=1_000_000)
        load_phrases_dict(_DEI_PHRASES)
    return _pipeline


def is_available() -> bool:
    return MODEL.exists() and CONFIG.exists()


def _empty_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
    return buf.getvalue()


def synthesize(text: str, voice: str = "zf_001", speed: float = 1.0) -> bytes:
    """文本 -> wav 字节。voice 可传 'zf_001' 或本地 .pt 路径。"""
    pipeline = _load()
    voice_path = voice if voice.endswith(".pt") else str(VOICES_DIR / f"{voice}.pt")
    if not Path(voice_path).exists():
        raise ValueError(f"音色不存在：{voice}")
    results = list(pipeline(text, voice=voice_path, speed=speed))
    audios = [r.output.audio for r in results if r.output is not None]
    if not audios:
        return _empty_wav()
    audio = torch.cat(audios).cpu().numpy()
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
