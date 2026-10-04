# -*- coding: utf-8 -*-
"""Kokoro 中文 G2P 的读音修正表（多音字 / 结构助词）。

链路：misaki legacy G2P = jieba 分词 → pypinyin.lazy_pinyin 查音。
pypinyin 词库没收录的词会退回**单字默认音**，产生误读：

- 觉：睡个觉/补个觉/好觉/整觉 → 误读 jue2（应 jiào）
- 得/地：结构助词（疼得慌/慢慢地）→ 误读 de2/di4（应轻声 de）
- 教：教您做操 → 误读 jiao4（应动词 jiāo）
- 睡不着/找不着 → 误读 zhe5（应 zháo）
- 干：口干/干香菇/干等着 → 误读 gan4（应 gān）
- 长时间/太长/长椅/长觉 → 误读 zhang3（应 cháng）

修法：pypinyin.load_single_dict 改单字默认音，load_phrases_dict
注入/覆盖词组读音（词组优先级高于单字，所以觉得/感觉、地方/地址、
干活/能干、医保卡等词库词不受单字改动影响）；用 jieba.add_word
保证词组表里的词被整词切出。文本本身不改，只影响 torch 路径读音。

改完跑 `tools/tts_polyphone_probe.py`（基于真实 jieba+pypinyin 链路）
复核全语料读音。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 单字默认音：域内该字**孤立成词**时的主导读音（组词读音交给词组表/内置词库）
SINGLE_CHAR_DEFAULTS = {
    "觉": "jiao4",  # 睡个觉/补个觉/好觉/整觉；觉得、感觉、直觉等在词库里
    "得": "de",     # 结构助词轻声（疼得/睡得/跑得快）；得到、值得、记得等在词库里
    "地": "de",     # 结构助词轻声（慢慢地/平稳地）；地方、地址等在词库里
    "教": "jiao1",  # 动词（教您/教你/教了一辈子书）；教育、请教等在词库里
    "干": "gan1",   # 口干/干香菇/干等着/擦干；干活、能干等在词库里
}

# 词组读音：补齐或覆盖 pypinyin 内置（TONE3 音节；轻声不带声调数字）。
# 含两类：单字默认音改动后需要"护回来"的词（得到/各地/教室/地高辛…），
# 以及 pypinyin 内置就读错的词（睡不着/长时间/太长…）。
PHRASE_OVERRIDES = {
    # 睡不着/找不着：内置词库把"着"读 zhe，应为 zháo
    "睡不着": [["shui4"], ["bu"], ["zhao2"]],
    "找不着": [["zhao3"], ["bu"], ["zhao2"]],
    "犯不着": [["fan4"], ["bu"], ["zhao2"]],
    "摸不着": [["mo1"], ["bu"], ["zhao2"]],
    "够不着": [["gou4"], ["bu"], ["zhao2"]],
    "见不着": [["jian4"], ["bu"], ["zhao2"]],
    "着火": [["zhao2"], ["huo3"]],
    "点着": [["dian3"], ["zhao2"]],
    "着地": [["zhao2"], ["di4"]],
    "就地": [["jiu4"], ["di4"]],
    "下地": [["xia4"], ["di4"]],
    # "长" cháng：pypinyin 单字默认 zhǎng，以下合成词不在内置词库
    "长时间": [["chang2"], ["shi2"], ["jian1"]],
    "好长时间": [["hao3"], ["chang2"], ["shi2"], ["jian1"]],
    "多长时间": [["duo1"], ["chang2"], ["shi2"], ["jian1"]],
    "太长": [["tai4"], ["chang2"]],
    "长觉": [["chang2"], ["jiao4"]],
    "补长觉": [["bu3"], ["chang2"], ["jiao4"]],
    "长椅": [["chang2"], ["yi3"]],
    "长效": [["chang2"], ["xiao4"]],
    # "得" dé：改单字默认后需要护回的"获得"义词
    "得到": [["de2"], ["dao4"]],
    "感觉得到": [["gan3"], ["jue2"], ["de2"], ["dao4"]],
    "得出": [["de2"], ["chu1"]],
    "得分": [["de2"], ["fen1"]],
    # "地" dì：改单字默认后需要护回的名词义
    "各地": [["ge4"], ["di4"]],
    "户籍地": [["hu4"], ["ji2"], ["di4"]],
    "地高辛": [["di4"], ["gao1"], ["xin1"]],
    # "觉着" = jué zhe（觉得的口语形式），区别于"睡觉"义
    "觉着": [["jue2"], ["zhe5"]],
    # "教" jiào：教育义合成词不在内置词库
    "教室": [["jiao4"], ["shi4"]],
    "教师": [["jiao4"], ["shi1"]],
    # "干" gàn：干活义（区别于口干/干香菇的 gān）
    "干不干": [["gan4"], ["bu4"], ["gan4"]],
    "干重": [["gan4"], ["zhong4"]],
    # 单字默认改后需要护回的杂项
    "不得劲": [["bu4"], ["de2"], ["jin4"]],
}

# 保证词组被 jieba 整词切出，读音才能按词组命中
JIEBA_USER_WORDS = sorted(PHRASE_OVERRIDES)

_applied = False


def apply() -> None:
    """注入读音修正（幂等）。缺 pypinyin/jieba 时静默跳过（如 CI）。"""
    global _applied
    if _applied:
        return
    try:
        import jieba
        from pypinyin import load_phrases_dict, load_single_dict
    except ImportError:
        logger.info("pypinyin/jieba 不可用，跳过 TTS 读音修正注入")
        return
    load_single_dict({ord(ch): py for ch, py in SINGLE_CHAR_DEFAULTS.items()})
    load_phrases_dict(dict(PHRASE_OVERRIDES))
    for word in JIEBA_USER_WORDS:
        jieba.add_word(word, freq=1000)
    _applied = True
