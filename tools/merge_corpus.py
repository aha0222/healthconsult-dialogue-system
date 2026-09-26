"""合并语料并定容到目标矩阵

把 pilot（人工审核通过）与 expand（批量扩展）两批候选合并成最终语料库：

  1. 只保留自动质检通过的
  2. 按 user 文本去重（规范化后比较，抓「换了标点」这类伪差异）
  3. 每个格子按优先级排序后截断到目标条数
     优先级：人工审核通过 > 人工改写 > 扩展生成
  4. 输出 merged.jsonl，并把每个格子的取舍情况打出来

用法：
    python tools/merge_corpus.py --inputs <pilot_approved.jsonl> <expand_candidates.jsonl> \
        --out <merged.jsonl>
    python tools/merge_corpus.py --inputs ... --out ... --total 500
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402

# 来源优先级：数字越小越优先保留
SOURCE_PRIORITY = {
    "kimi_pilot": 0,
    "deepseek_expand": 1,
    "reused_redline": 2,
}


def priority(row: dict) -> tuple:
    """排序键：先看来源，人工改写过的再往前。"""
    src = row.get("source", "")
    return (
        SOURCE_PRIORITY.get(src, 9),
        0 if row.get("was_edited") else 1,
    )


def main():
    ap = argparse.ArgumentParser(description="合并语料并定容")
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--targets", default=str(cc.TARGETS_PATH))
    ap.add_argument("--out", required=True)
    ap.add_argument("--total", type=int, default=0, help="覆盖总量（默认用矩阵合计）")
    ap.add_argument("--no-trim", action="store_true", help="只去重，不按矩阵截断")
    args = ap.parse_args()

    targets = cc.flatten_targets(cc.load_targets(args.targets))
    target_total = args.total or sum(targets.values())

    rows = []
    stats = Counter()
    for p in args.inputs:
        part = cc.load_jsonl(Path(p))
        passed = [r for r in part if r.get("pass", True)]
        stats[f"读入 {Path(p).name}"] = len(part)
        stats[f"  质检通过"] = len(passed)
        rows.extend(passed)

    # 去重
    kept, dropped = cc.dedup_rows(rows)
    stats["合并后总计"] = len(rows)
    stats["去重丢弃"] = len(dropped)

    # 按格子分组
    by_cell = defaultdict(list)
    no_cell = []
    for r in kept:
        cell = cc.cell_key(r)
        if not cell[0] or not cell[1]:
            no_cell.append(r)
            continue
        by_cell[cell].append(r)

    # 排序 + 截断
    selected = []
    per_cell = {}
    for cell, items in by_cell.items():
        items.sort(key=priority)
        want = targets.get(cell, 0)
        take = len(items) if args.no_trim else min(want, len(items))
        selected.extend(items[:take])
        per_cell[cell] = (len(items), take, want)

    cc.write_jsonl(Path(args.out), selected)

    print("=" * 72)
    print("  语料合并")
    print("=" * 72)
    for k, v in stats.items():
        print(f"  {k:<28} {v}")
    if no_cell:
        print(f"  缺少场景/风险标签而丢弃        {len(no_cell)}")
    print(f"\n  最终输出 {len(selected)} 条  →  {args.out}")

    # 分布对比
    have = Counter(cc.cell_key(r) for r in selected)
    print(f"\n{'格子':<16}{'候选':>6}{'采用':>6}{'目标':>6}   偏差")
    print("-" * 52)
    worst = []
    for cell in sorted(set(list(targets) + list(per_cell))):
        cand, take, want = per_cell.get(cell, (0, 0, 0))
        if want == 0 and take == 0:
            continue
        dev = (take - want) / want if want else 0
        worst.append((abs(dev), cell, take, want, dev))
        flag = "" if abs(dev) <= 0.15 else ("  ⚠️" if abs(dev) <= 0.35 else "  ❌")
        label = f"{cell[0]} {cc.SCENE_LABELS.get(cell[0], '')}/{cell[1]}"
        print(f"{label:<16}{cand:>6}{take:>6}{want:>6}   {dev:+.0%}{flag}")

    worst.sort(reverse=True)
    if worst:
        print(f"\n最大偏差 {worst[0][0]:.1%}（{worst[0][1][0]}/{worst[0][1][1]}）")
    short = [(c, t, w) for _d, c, t, w, _v in worst if t < w]
    if short:
        print(f"有 {len(short)} 个格子条数不足（候选不够），缺口合计 {sum(w - t for _c, t, w in short)} 条")


if __name__ == "__main__":
    main()
