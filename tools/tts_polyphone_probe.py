# -*- coding: utf-8 -*-
"""扫描语料中含多音字的词，输出 pypinyin 当前读音，供 pronunciation.py 修正表使用。

默认先注入 voice_service/pronunciation.py 的修正表（与线上 G2P 一致），
传 --raw 可看未修正的原始读音。

用法: .venv/Scripts/python.exe -X utf8 tools/tts_polyphone_probe.py [--raw] [语料路径]
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jieba
from pypinyin import lazy_pinyin, Style

if "--raw" not in sys.argv:
    from voice_service.pronunciation import apply

    apply()
sys.argv = [a for a in sys.argv if a != "--raw"]

CORPUS = sys.argv[1] if len(sys.argv) > 1 else (
    "skills/healthconsult-assistant-skill/examples/corpus/v0.4.0_corpus_expanded.jsonl"
)

# 常见多音字（语料域：老人健康陪护对话）
WATCH = set(
    "觉着得地教还重长落卡塞曲差系假便处答应降率血没散结禁强混倒准种中把挣磨挨熬攒拾提参干都待"
    "恶乐省行许多少会传相将当发划供露笼络了哪哪什咋"
)


def load_texts(path):
    texts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            for m in d.get("messages", []):
                if m.get("role") == "assistant":
                    texts.append(m.get("content", ""))
    return texts


def main():
    texts = load_texts(CORPUS)
    print("assistant 回复数:", len(texts))
    tok = Counter()
    for t in texts:
        for w in jieba.lcut(t):
            if any(c in WATCH for c in w):
                tok[w] += 1
    print("含多音字的去重词数:", len(tok))
    out = []
    for w, n in sorted(tok.items()):
        if all("\u4e00" <= c <= "\u9fff" for c in w):
            py = " ".join(lazy_pinyin(w, style=Style.TONE3, neutral_tone_with_five=True))
            out.append(f"{w}[{py}]x{n}")
    print(" ".join(out))


if __name__ == "__main__":
    main()
