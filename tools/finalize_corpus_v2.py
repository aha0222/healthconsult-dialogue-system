"""语料入库：合并去重 → 剔标签矛盾 → 按目标矩阵裁格 → 输出 v0.4.0。

用途（第三阶段收尾）：
    python tools/finalize_corpus_v2.py            # 执行合并与裁剪，写入 examples/corpus/
    python tools/finalize_corpus_v2.py --dry-run  # 只打印统计，不写文件

规则：
1. 来源顺序固定：v0.3.0_corpus500（已人工审核）→ v0.4.0_seed_anchors（组长手工种子）
   → 第一轮 expand 清洗结果 → 补跑清洗结果。靠 user 文本去重。
2. 任一场景标签与风险等级矛盾（check_scene_risk，含交叉标签）的条目直接剔除。
3. 每个格子裁剪到目标矩阵条数：超出的条目**优先剔除带交叉标签的**（压平均标签数），
   同优先级内按 sample_id 降序裁（确定性，可重跑）。
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402

CORPUS_DIR = cc.CORPUS_DIR
SOURCES = [
    CORPUS_DIR / "v0.3.0_corpus500.jsonl",
    CORPUS_DIR / "v0.4.0_seed_anchors.jsonl",
    cc.REPO_ROOT / ".cache" / "expand_cleaned_v2.jsonl",
    cc.REPO_ROOT / ".cache" / "expand_topup_cleaned.jsonl",
    cc.REPO_ROOT / ".cache" / "expand_fix_cleaned.jsonl",
    cc.REPO_ROOT / ".cache" / "expand_L5R1_cleaned.jsonl",
]

OUT_PATH = CORPUS_DIR / "v0.4.0_corpus_expanded.jsonl"


def row_risk(row) -> str:
    return row.get("risk_level") or row.get("risk") or ""


def conflicts(row) -> bool:
    """任一场景标签（含交叉）与风险矛盾即剔除。"""
    risk = row_risk(row)
    for scene in row.get("scenes") or []:
        if cc.check_scene_risk(scene, risk):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="语料 v0.4.0 入库裁剪")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = []
    for path in SOURCES:
        part = cc.load_jsonl(path)
        rows.extend(part)
        print(f"  来源 {path.name}: {len(part)} 条")
    merged, dup_dropped = cc.dedup_rows(rows)
    print(f"合并 {len(rows)} → 去重 {len(merged)}（丢 {len(dup_dropped)}）")

    conflicted = [r for r in merged if conflicts(r)]
    merged = [r for r in merged if not conflicts(r)]
    print(f"剔除标签矛盾 {len(conflicted)} 条 → 剩 {len(merged)}")

    targets = cc.flatten_targets(cc.load_targets())
    by_cell = defaultdict(list)
    for row in merged:
        by_cell[cc.cell_key(row)].append(row)

    kept, cut = [], []
    for cell, items in by_cell.items():
        want = targets.get(cell, 0)
        items = sorted(
            items,
            key=lambda r: (
                # 优先保留：无交叉标签的（压平均标签数）；同优先级按 id 降序裁
                len(r.get("scenes") or []),
                r.get("sample_id", ""),
            ),
        )
        kept.extend(items[:want])
        cut.extend(items[want:])
    # 目标之外的格子（不在矩阵里）整格舍弃
    kept = [r for r in kept if cc.cell_key(r) in targets]

    total = len(kept)
    stats = cc.tag_stats(kept)
    print(
        f"按格子裁剪：留 {total} / 裁 {len(cut)}  "
        f"场景覆盖 {stats['scene_coverage']}/24  平均标签数 {stats['avg_scenes']}"
    )
    missing = [c for c in targets if not any(cc.cell_key(r) == c for r in kept)]
    if missing:
        print(f"仍空缺格子: {missing}")

    if args.dry_run:
        print("[DRY-RUN] 未写文件")
        return
    cc.write_jsonl(OUT_PATH, kept)
    print(f"已写入 {OUT_PATH.relative_to(cc.REPO_ROOT)}")


if __name__ == "__main__":
    main()
