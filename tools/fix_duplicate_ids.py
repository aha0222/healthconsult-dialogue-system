"""修复重复的 sample_id

── 成因 ────────────────────────────────────────────────────────
生成时 sample_id 的格式是 `exp_{场景}_{风险}_b{批次}_{序号}`。
当某个格子分两次补跑时（例如第一批候选不够，事后再补），
两次都会从 b0 开始编号，于是产生完全相同的 id。

重复 id 会让 batch_validate 报 fatal，也会让「按 id 溯源」失效
（审核记录、锚点引用都靠 id 匹配，撞了就对不上）。

── 修法 ────────────────────────────────────────────────────────
保留首次出现的 id，后续重复的追加 `_r2`、`_r3` 后缀。
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402


def fix_duplicates(rows):
    seen = Counter()
    renamed = []
    for r in rows:
        sid = r.get("sample_id", "")
        if not sid:
            continue
        seen[sid] += 1
        if seen[sid] > 1:
            new_id = f"{sid}_r{seen[sid]}"
            # 极端情况下加后缀还撞，就继续往后找
            while new_id in seen:
                seen[new_id] += 1
                new_id = f"{sid}_r{seen[sid]}"
            renamed.append((sid, new_id))
            r["sample_id"] = new_id
            r["renamed_from"] = sid
    return rows, renamed


def main():
    ap = argparse.ArgumentParser(description="修复重复 sample_id")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = cc.load_jsonl(Path(args.input))
    before = Counter(r.get("sample_id") for r in rows)
    dup_before = [k for k, v in before.items() if v > 1]

    rows, renamed = fix_duplicates(rows)

    after = Counter(r.get("sample_id") for r in rows)
    dup_after = [k for k, v in after.items() if v > 1]

    print("=" * 66)
    print(f"  修复重复 sample_id   共 {len(rows)} 条")
    print("=" * 66)
    print(f"\n修复前重复 id: {len(dup_before)} 个")
    for k in sorted(dup_before):
        print(f"  {k}  ×{before[k]}")
    print(f"\n重命名 {len(renamed)} 条：")
    for old, new in renamed:
        print(f"  {old} → {new}")
    print(f"\n修复后重复 id: {len(dup_after)}")

    cc.write_jsonl(Path(args.out), rows)
    print(f"\n输出 → {args.out}")


if __name__ == "__main__":
    main()
