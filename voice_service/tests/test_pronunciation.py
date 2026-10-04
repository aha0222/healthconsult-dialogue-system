# -*- coding: utf-8 -*-
"""TTS 读音修正（多音字/结构助词）回归测试。

复现 misaki legacy G2P 的真实链路：jieba 分词 → pypinyin.lazy_pinyin
（TONE3，轻声记 5）。断言语料里出现过的误读保持已修状态——改
pronunciation.py 的修正表后跑本测试防回退。

jieba 有时会多字合并（别着急/凭感觉），所以按"目标词所在 token 的
音节子序列"断言，而不是要求目标词恰好被切成整词。
"""

import pytest

pytest.importorskip("pypinyin")
pytest.importorskip("jieba")

import re

import jieba
from pypinyin import lazy_pinyin, Style

from voice_service import pronunciation

pronunciation.apply()


def g2p_tokens(text):
    """与 misaki legacy_call 一致：只对连续汉字段做 jieba+lazy_pinyin。"""
    result = []
    for run in re.findall(r"[\u4E00-\u9FFF]+|[^\u4E00-\u9FFF]+", text):
        if not re.match(r"[\u4E00-\u9FFF]", run[0]):
            continue
        for word in jieba.lcut(run, cut_all=False):
            result.append(
                (word, lazy_pinyin(word, style=Style.TONE3, neutral_tone_with_five=True))
            )
    return result


def expect(text, word, syllables):
    """断言 word 所在 token 的读音中按序出现 syllables。"""
    for w, py in g2p_tokens(text):
        if word in w:
            it = iter(py)
            for s in syllables:
                assert s in it, f"{text!r} 里 {word!r} 应读 {syllables}，实际 {py}（token={w!r}）"
            return
    raise AssertionError(f"{text!r} 里找不到 {word!r}：{g2p_tokens(text)}")


def test_sleep_context_jiao():
    # 觉：睡觉义单字/合成词应为 jiào
    expect("您去补个觉。", "觉", ["jiao4"])
    expect("睡个整觉。", "整觉", ["zheng3", "jiao4"])
    expect("睡个午觉。", "午觉", ["jiao4"])
    expect("补个长觉。", "长觉", ["chang2", "jiao4"])
    # 觉得/感觉/直觉仍为 jué
    expect("我觉得呢。", "觉得", ["jue2"])
    expect("凭感觉呢。", "感觉", ["gan3", "jue2"])
    expect("我觉着也是。", "觉着", ["jue2", "zhe5"])


def test_structural_de():
    # 结构助词得/地 → 轻声 de
    expect("胸口疼得慌。", "得", ["de5"])
    expect("慢慢地走。", "地", ["de5"])
    expect("猛地站起来。", "猛地", ["meng3", "de5"])
    # 获得义的"得"和名词义的"地"护回
    expect("得到报告。", "得到", ["de2", "dao4"])
    expect("值得做。", "值得", ["zhi2", "de5"])
    expect("地方宽敞。", "地方", ["di4"])
    expect("下地走走。", "下地", ["xia4", "di4"])
    expect("各地都有。", "各地", ["ge4", "di4"])


def test_zhe_zhao():
    # V+着 → zhe；睡不着/找不着 → zháo
    expect("您坐着。", "着", ["zhe5"])
    expect("夜里睡不着。", "睡不着", ["zhao2"])
    expect("东西找不着。", "找不着", ["zhao2"])
    expect("睡着了。", "睡着", ["zhao2"])
    expect("别着急。", "着急", ["zhao2"])


def test_jiao_gan_chang_defaults():
    # 教：动词 jiāo；教育义护回
    expect("我教您。", "教", ["jiao1"])
    expect("教室很亮。", "教室", ["jiao4"])
    # 干：口干/干香菇 gān；干活 gàn
    expect("口干。", "干", ["gan1"])
    expect("干活别太累。", "干活", ["gan4"])
    expect("饭前洗手，干净。", "干净", ["gan1"])
    # 长：长出 zhǎng；长时间 cháng
    expect("长出了水泡。", "长出", ["zhang3"])
    expect("走长时间不行。", "长时间", ["chang2"])
    expect("长期熬夜。", "长期", ["chang2"])
