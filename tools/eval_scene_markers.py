"""SCENE 标记评测：统一"场景标得准不准"的口径。

对应任务：《小暖项目分工》成员 B 第 6 项 —— 出场景标记（SCENE）的评测口径，
配合组长第 2 项（提升在线档场景标记准确率）。

为什么需要一个统一口径
--------------------
"准确率 74%" 这句话只有在说清**怎么算**之后才有意义。场景是**多标签**的
（1816 条语料里 847 条标 1 个、969 条标 2 个），"对"至少有四种算法，
数值能差十几个点。组里必须用同一套，否则"从 74% 提到 78%"没法比较。

本工具给出 5 个指标，并**把安全场景单独拎出来**——普通场景标错是体验问题，
安全场景漏标是安全问题。

用法
----
    # 1) 评测一份已有的预测结果（最常用）
    python tools\\eval_scene_markers.py --pred preds.jsonl

      preds.jsonl 每行：{"id": "C001", "scenes": ["L2"]}
      或直接放模型原始输出：{"id": "C001", "raw": "...[RISK:R0]\\n[SCENE:L2]"}

    # 2) 用关键词兜底生成预测（快，但只覆盖 5 个安全场景，仅作对照）
    python tools\\eval_scene_markers.py --keyword

    # 3) 跑本地模型生成预测（慢，CPU 上约 4 条/分钟）
    python tools\\eval_scene_markers.py --llm --limit 50

指标说明（全部针对"标注有 ground truth 的语料"）
------------------------------------------------
    严格命中 exact   : 预测集合 == 标注集合
    任一命中 any     : 预测集合 ∩ 标注集合 ≠ ∅
    主场景命中 first : 预测的第一个场景 ∈ 标注集合
    微平均 F1 micro  : 把 (样本, 场景) 展平成二分类算 P/R/F1
    逐场景 recall    : 每个场景被认出的比例（**安全场景看这个**）
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CORPUS = REPO / "backend/app/dialogue/corpus/scene_risk_corpus.jsonl"

# 安全相关场景：标错代价与普通场景不同，单独统计
SAFETY_SCENES = {"E1", "M2", "N1", "N2", "N3", "N4", "M3"}


def load_corpus():
    return [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_preds(path: Path):
    """读预测文件。支持 {"id","scenes"} 或 {"id","raw"}。"""
    from backend.app.dialogue.markers import parse_marker

    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if "scenes" in rec and rec["scenes"] is not None:
            out[rec["id"]] = list(rec["scenes"])
        elif "raw" in rec:
            _, _, scenes = parse_marker(rec["raw"])
            out[rec["id"]] = list(scenes or [])
        else:
            raise SystemExit(f"预测行缺少 scenes / raw 字段：{rec}")
    return out


def preds_from_keyword(rows):
    from backend.app.dialogue.markers import infer_tags_local
    return {r["id"]: list(infer_tags_local(r["user"])[1]) for r in rows}


def preds_from_llm(rows, endpoint):
    import os
    os.environ["OFFLINE_MODE"] = "1"
    os.environ["OFFLINE_LLM_BACKEND"] = "endpoint"
    os.environ["OFFLINE_LLM_ENDPOINT"] = endpoint
    os.environ["OFFLINE_LLM_STRICT"] = "1"
    os.environ.setdefault("DEEPSEEK_API_KEY", "sk-local")
    os.environ.setdefault("EMBEDDING_BACKEND", "hash")
    os.environ.setdefault("EMBEDDING_STRICT", "1")

    from backend.app.config import get_settings
    from backend.app.dialogue.markers import parse_marker
    from backend.app.dialogue.orchestrator import DialogueOrchestrator

    orch = DialogueOrchestrator(settings=get_settings())
    out = {}
    for i, r in enumerate(rows, 1):
        cls = orch._runtime_classification(r["user"])
        sp = orch._compose_system_prompt(r["user"], "warm", cls, speaker_block=None)
        msgs = orch._build_messages(r["user"], "warm", None, None, sp)
        raw = orch.llm.chat(msgs)
        _, _, scenes = parse_marker(raw)
        out[r["id"]] = list(scenes or [])
        if i % 10 == 0 or i == len(rows):
            print(f"    [{i}/{len(rows)}]", flush=True)
    return out


def evaluate(rows, preds):
    tp = fp = fn = 0
    exact = anyhit = firsthit = 0
    per_scene = defaultdict(lambda: defaultdict(int))
    errors = []
    n = 0

    for r in rows:
        if r["id"] not in preds:
            continue
        n += 1
        gold = set(r.get("scenes") or [])
        pred = set(preds[r["id"]])
        first = preds[r["id"]][0] if preds[r["id"]] else None

        if pred == gold:
            exact += 1
        if pred & gold:
            anyhit += 1
        if first in gold:
            firsthit += 1

        tp += len(pred & gold)
        fp += len(pred - gold)
        fn += len(gold - pred)

        for s in gold:
            per_scene[s]["tp"] += 1 if s in pred else 0
            per_scene[s]["fn"] += 0 if s in pred else 1
        for s in pred - gold:
            per_scene[s]["fp"] += 1

        if not (pred & gold) and len(errors) < 12:
            errors.append((r["id"], sorted(gold), sorted(pred), r["user"][:44]))

    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * rc / (p + rc) if p + rc else 0.0

    return dict(n=n, exact=exact, anyhit=anyhit, firsthit=firsthit,
                tp=tp, fp=fp, fn=fn, p=p, r=rc, f1=f1,
                per_scene=per_scene, errors=errors)


def main() -> int:
    ap = argparse.ArgumentParser(description="SCENE 标记评测")
    ap.add_argument("--pred", help="预测文件（jsonl）")
    ap.add_argument("--keyword", action="store_true", help="用关键词兜底生成预测")
    ap.add_argument("--llm", action="store_true", help="跑本地模型生成预测")
    ap.add_argument("--endpoint", default="http://127.0.0.1:8080")
    ap.add_argument("--limit", type=int, default=0, help="只评前 N 条")
    ap.add_argument("--json", default="", help="把结果写成 JSON")
    args = ap.parse_args()

    rows = load_corpus()
    if args.limit:
        rows = rows[:args.limit]

    if args.pred:
        preds = load_preds(Path(args.pred))
        source = f"预测文件 {args.pred}"
    elif args.keyword:
        preds = preds_from_keyword(rows)
        source = "关键词兜底 infer_tags_local"
    elif args.llm:
        preds = preds_from_llm(rows, args.endpoint)
        source = f"本地模型 @{args.endpoint}"
    else:
        ap.error("必须指定 --pred / --keyword / --llm 之一")

    res = evaluate(rows, preds)
    n = res["n"] or 1

    print("=" * 80)
    print("  SCENE 标记评测")
    print("=" * 80)
    print(f"  预测来源 : {source}")
    print(f"  评测样本 : {res['n']} 条（有预测且有标注）")
    print()
    print(f"  {'严格命中 exact':<22}{res['exact']:>5}/{res['n']:<5}= {res['exact']/n*100:>5.1f}%")
    print(f"  {'任一命中 any':<22}{res['anyhit']:>5}/{res['n']:<5}= {res['anyhit']/n*100:>5.1f}%")
    print(f"  {'主场景命中 first':<22}{res['firsthit']:>5}/{res['n']:<5}= {res['firsthit']/n*100:>5.1f}%")
    print(f"  {'微平均 P / R / F1':<22}{res['p']*100:>5.1f} / {res['r']*100:.1f} / {res['f1']*100:.1f}")
    print(f"  {'(tp/fp/fn)':<22}{res['tp']}/{res['fp']}/{res['fn']}")
    print()

    print("-" * 80)
    print("  逐场景（安全场景在前）")
    print("-" * 80)
    print(f"  {'场景':<6}{'召回':>7}{'精确':>7}{'标注数':>8}"
          f"{'tp':>6}{'fn':>6}{'fp':>6}   备注")
    gold_total = Counter()
    for r in rows:
        for s in (r.get("scenes") or []):
            gold_total[s] += 1

    order = sorted(res["per_scene"],
                   key=lambda s: (s not in SAFETY_SCENES, -gold_total.get(s, 0)))
    for s in order:
        d = res["per_scene"][s]
        tp, fn, fp = d["tp"], d["fn"], d["fp"]
        rec = tp / (tp + fn) * 100 if tp + fn else 0
        pre = tp / (tp + fp) * 100 if tp + fp else 0
        note = "★ 安全场景" if s in SAFETY_SCENES else ""
        print(f"  {s:<6}{rec:>6.0f}%{pre:>6.0f}%{gold_total.get(s, 0):>8}"
              f"{tp:>6}{fn:>6}{fp:>6}   {note}")

    if res["errors"]:
        print()
        print("-" * 80)
        print("  完全没命中的样例（前 12 条）")
        print("-" * 80)
        for i, gold, pred, text in res["errors"]:
            print(f"  {i}  标注={gold}  预测={pred}")
            print(f"      {text}")

    if args.json:
        out = {k: v for k, v in res.items() if k not in ("per_scene", "errors")}
        out["per_scene"] = {k: dict(v) for k, v in res["per_scene"].items()}
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\n  已写 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
