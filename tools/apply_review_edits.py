"""把人工审核中的改写应用回最终语料

人工在评审页里填的「修改后回复」是正式产出——它们会被回灌成最终语料。
这个脚本按 sample_id 匹配，用改写版替换 assistant 正文，并补回末尾标签
（人工改写时经常漏掉标签，漏了会导致该条通不过 A3 检查）。

用法：
    python tools/apply_review_edits.py --corpus v0.3.0_corpus500.jsonl \
        --review <导出的csv> --out v0.3.0_corpus500.jsonl
    python tools/apply_review_edits.py ... --dry-run
"""

import argparse
import csv
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402

REPO_ROOT = cc.REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.taxonomy import (  # noqa: E402
    extract_tags, format_tags, strip_tags,
)


def main():
    ap = argparse.ArgumentParser(description="应用人工改写")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = cc.load_jsonl(Path(args.corpus))
    with open(args.review, encoding="utf-8-sig") as f:
        review = {r.get("review_id", ""): r for r in csv.DictReader(f)}

    by_id = {r.get("sample_id"): r for r in rows}
    applied, tag_fixed, skipped, invalid = [], [], [], []

    for sid, rev in review.items():
        text = (rev.get("edited_assistant") or "").strip()
        if not text:
            continue
        row = by_id.get(sid)
        if not row:
            skipped.append(sid)
            continue

        # 防呆：误敲键盘、粘贴失败会产生明显不像完整回复的「改写」。
        # 之前出现过把一条 217 字的 R3 急症回复改成 3 个字符（"cdv"）的情况，
        # 直接应用会毁掉这条数据。
        orig = next((strip_tags(m["content"]) for m in row.get("messages") or []
                     if m.get("role") == "assistant"), "")
        if len(text) < 20 or len(text) < len(orig) * 0.3:
            invalid.append((sid, text, len(orig)))
            continue

        risk, scenes = extract_tags(text)
        if not risk:
            text = strip_tags(text) + format_tags(
                row.get("risk_level", ""), row.get("scenes") or [])
            tag_fixed.append(sid)
        for m in row.get("messages") or []:
            if m.get("role") == "assistant":
                before = strip_tags(m["content"])
                m["content"] = text
                applied.append((sid, len(before), len(strip_tags(text))))
        row["human_edited"] = True

    print("=" * 70)
    print(f"  应用人工改写   语料 {len(rows)} 条，审核记录 {len(review)} 条")
    print("=" * 70)
    print(f"\n应用改写 {len(applied)} 条：")
    for sid, a, b in applied:
        print(f"  {sid}   原 {a} 字 → 改 {b} 字")
    if invalid:
        print(f"\n⚠️ 跳过 {len(invalid)} 条明显无效的改写（疑似误输入）：")
        for sid, text, n in invalid:
            print(f"  {sid}   原文 {n} 字，改写只有 {len(text)} 字：「{text[:20]}」")
        print("  如确属有意修改，请重新在评审页填写完整正文后重跑。")
    if tag_fixed:
        print(f"\n其中 {len(tag_fixed)} 条改写漏了末尾标签，已自动补回：")
        for sid in tag_fixed:
            print(f"  {sid}")
    if skipped:
        print(f"\n审核记录里有但语料中找不到（可能被去重剔除）{len(skipped)} 条：")
        for sid in skipped[:6]:
            print(f"  {sid}")

    if args.dry_run:
        print("\n[dry-run] 未写入")
        return

    cc.write_jsonl(Path(args.out), rows)
    print(f"\n输出 {len(rows)} 条 → {args.out}")


if __name__ == "__main__":
    main()
