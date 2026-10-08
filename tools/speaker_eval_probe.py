# -*- coding: utf-8 -*-
"""说话人识别评测探针：分类别统计准确率，并按错误方向拆分。

默认跑内置的 40 例种子样例（设计文档 docs/speaker_eval_design.md 第 4 节的清单）。
传入 JSONL 路径则改跑该文件，每行形如
    {"text": "...", "expected": "family", "category": "3双人对话", "history": ["..."]}

用法:
    .venv/Scripts/python.exe tools/speaker_eval_probe.py
    .venv/Scripts/python.exe tools/speaker_eval_probe.py backend/tests/eval/speaker_cases.jsonl

为什么按方向拆：误判成 family（老人被当家属）会改称呼和内容侧重，是本模块唯一
伤害体验的方向；漏判 family 只是退回开关关闭时的行为。两者代价不对称，
只看总体准确率会把这个差别抹平。详见设计文档第 3 节。
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.dialogue import speaker  # noqa: E402

ELDER = speaker.SPEAKER_ELDER
FAMILY = speaker.SPEAKER_FAMILY

# (类别, 期望角色, 文本, 历史)
SEED_CASES = [
    # ── 类 1 引用转述 ────────────────────────────────────────────────
    ("1引用转述", FAMILY, "我妈说\u201c我不吃药\u201d，怎么劝她", None),
    ("1引用转述", FAMILY, "我妈跟我说她不头晕了，可我看着她走路还是晃", None),
    ("1引用转述", FAMILY, "我爸说他胸口疼，但问他他又说没事", None),
    ("1引用转述", FAMILY, "我奶奶一直说\u201c我没病\u201d，不肯去医院", None),
    ("1引用转述", FAMILY, "我父亲说他浑身没劲，饭也不想吃", None),
    ("1引用转述", FAMILY, "替我母亲问一下，她说她自己晚上睡不着", None),
    ("1引用转述", FAMILY, "我岳父总念叨\u201c我活够了\u201d，我很害怕", None),
    ("1引用转述", ELDER, "大夫说我血压高，让我加药", None),
    ("1引用转述", ELDER, "我闺女让我别吃那个药", None),
    ("1引用转述", ELDER, "我儿子说我这血压得盯着点", None),
    ("1引用转述", ELDER, "我老伴总说我记性差", None),
    ("1引用转述", ELDER, "隔壁老王说我气色不好", None),
    ("1引用转述", ELDER, "我闺女给我买了个血压计，我不会用", None),
    # ── 类 2a 无称呼但可判（靠历史）────────────────────────────────
    ("2a无称呼可判", FAMILY, "他这个样子还能治好吗", ["我爸中风了"]),
    ("2a无称呼可判", FAMILY, "这个要不要送医院", ["我奶奶摔了一跤"]),
    ("2a无称呼可判", FAMILY, "她最近老忘事，我该注意什么", ["我母亲80岁了"]),
    ("2a无称呼可判", FAMILY, "晚上老是折腾，我快撑不住了", ["我家老人糊涂得厉害"]),
    ("2a无称呼可判", FAMILY, "他最近总忘事", ["我爸今年80了"]),
    ("2a无称呼可判", FAMILY, "那该怎么办", ["我妈不肯吃药"]),
    # ── 类 2b 无称呼且本质模糊（应回退 elder）──────────────────────
    ("2b无称呼模糊", ELDER, "你好", None),
    ("2b无称呼模糊", ELDER, "你们这都能聊什么", None),
    ("2b无称呼模糊", ELDER, "他打呼噜很响", None),
    ("2b无称呼模糊", ELDER, "这个情况严重吗", None),
    ("2b无称呼模糊", ELDER, "那该怎么办", None),
    # ── 类 3 双人对话（历史混合 / 角色切换）──────────────────────
    ("3双人对话", FAMILY, "那该怎么办", ["我头晕", "我爸血压高"]),
    ("3双人对话", ELDER, "那该怎么办", ["我爸血压高", "我头晕"]),
    ("3双人对话", ELDER, "我自己也头晕", ["我爸今年80了"]),
    ("3双人对话", ELDER, "那怎么办呢", ["我爸不肯吃药", "他不肯去医院",
                                          "我头晕", "我还心慌", "还是不吃"]),
    # ── 类 4 方言口语 ───────────────────────────────────────────────
    ("4方言口语", FAMILY, "俺妈血压高怎么办", None),
    ("4方言口语", FAMILY, "我爹不肯吃药", None),
    ("4方言口语", FAMILY, "我老娘最近糊涂得厉害", None),
    ("4方言口语", FAMILY, "我姥爷摔了一跤", None),
    ("4方言口语", FAMILY, "俺娘晚上睡不踏实", None),
    ("4方言口语", ELDER, "我浑身不得劲", None),
    ("4方言口语", ELDER, "我脑袋瓜子嗡嗡的", None),
    ("4方言口语", ELDER, "我心口窝疼得慌", None),
    # ── 类 5 历史延续 ───────────────────────────────────────────────
    ("5历史延续", ELDER, "那该怎么办", ["我妈不肯吃药", "他不肯去医院",
                                          "我头疼", "我还心慌", "他还是不吃",
                                          "我也没办法了"]),
    ("5历史延续", FAMILY, "他最近总忘事", ["我爸今年80了"]),
    ("5历史延续", ELDER, "我头晕得厉害", ["我爸今年80了"]),
    ("5历史延续", FAMILY, "那该怎么办", [None, 123, "我妈吃药了"]),
]


def load_jsonl(path: str):
    """读取评测集，返回 (角色类样例, 安全类样例)。

    角色类行带 ``"expected": "elder"/"family"``，由本脚本直接判定。
    安全类行 ``"expected"`` 为 null，断言的是"角色不改变红线"——那必须走
    /api/chat 才能测，本脚本只列出、不判分。
    """
    role_cases, safety_cases = [], []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                item = json.loads(line)
                text = item["text"]
                category = item.get("category", "未分类")
            except (json.JSONDecodeError, KeyError) as exc:
                raise SystemExit(f"{path}:{lineno} 解析失败：{exc}") from exc
            if item.get("expected"):
                role_cases.append((category, item["expected"], text,
                                   item.get("history")))
            else:
                safety_cases.append((category, text, item.get("assert", "")))
    return role_cases, safety_cases


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        cases, safety_cases = load_jsonl(args[0])
        source = args[0]
    else:
        cases, safety_cases = SEED_CASES, []
        source = "内置种子样例（40 例）"

    by_class = defaultdict(lambda: [0, 0, []])
    miss_family = []   # 漏判：家属被当老人
    over_family = []   # 误切：老人被当家属（代价最高的方向）
    for cls, expected, text, history in cases:
        got = speaker.detect_speaker_role(text, history=history)
        ok = got == expected
        entry = by_class[cls]
        entry[0] += 1
        if not ok:
            entry[1] += 1
            entry[2].append((text, expected, got))
        if expected == FAMILY and got == ELDER:
            miss_family.append((cls, text))
        elif expected == ELDER and got == FAMILY:
            over_family.append((cls, text))
        mark = "OK  " if ok else "MISS"
        print(f"{mark} [{cls}] 期望={expected:6s} 实得={got:6s} {text}"
              + (f"   历史={history}" if history else ""))

    total = len(cases)
    wrong = sum(v[1] for v in by_class.values())
    print("\n" + "=" * 72)
    print(f"样例来源：{source}")
    print(f"总体：{total} 例，错判 {wrong} 例，准确率 {(total - wrong) / total:.1%}")
    print("=" * 72)
    print(f"{'类别':16s} {'例数':>4s} {'错判':>4s} {'准确率':>7s}")
    for cls in sorted(by_class):
        n, bad, _ = by_class[cls]
        print(f"{cls:16s} {n:4d} {bad:4d} {(n - bad) / n:7.0%}")
        for text, expected, got in by_class[cls][2]:
            print(f"      {text}  （期望 {expected}，实得 {got}）")

    print("\n" + "=" * 72)
    print("按方向拆分（主指标，见设计文档第 3 节）")
    print("=" * 72)
    print(f"误切 family（老人被当家属，代价最高）：{len(over_family)} 例")
    for cls, text in over_family:
        print(f"      [{cls}] {text}")
    print(f"漏判 family（家属被当老人，退回现状）：{len(miss_family)} 例")
    for cls, text in miss_family:
        print(f"      [{cls}] {text}")

    if safety_cases:
        print("\n" + "=" * 72)
        print(f"类 6 安全不变式交叉：{len(safety_cases)} 例（不判分，需走 /api/chat）")
        print("=" * 72)
        print("断言：同一文本分别以 speaker_role=elder / family 请求，risk_level")
        print("      与兜底话术必须一致——即角色不改变红线。")
        for _, text, _ in safety_cases:
            print(f"      {text}")


if __name__ == "__main__":
    main()
