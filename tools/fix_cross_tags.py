"""修正交叉场景标签：只保留 user 文本真的有该场景关键词的附加标签

── 为什么需要这一步 ──────────────────────────────────────────────
扩展阶段派发交叉标签时，`_pick_cross` 只要发现预算里有以该场景开头的组合，
就给这个场景的**每一条**都加上交叉标签，完全不看内容讲的是什么。
结果出现了「我腿没劲儿」被标上 `L3 作息睡眠` 这种明显错误的标签。

这类错误标签会带来两个后果：
  · SFT 侧：模型学到「S1 场景总是伴随睡眠问题」这种虚假相关
  · Retriever 侧：条目被索引到无关场景下，检索质量下降

本脚本用 SCENE_KEYWORDS 做一次确定性校验，把没有关键词支撑的附加标签去掉，
并重写 assistant 末尾的标签块。

用法：
    python tools/fix_cross_tags.py --input merged.jsonl --out merged_fixed.jsonl
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402

REPO_ROOT = cc.REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.taxonomy import format_tags, strip_tags  # noqa: E402
from backend.app.safety.safety_checker import (  # noqa: E402
    SCENE_KEYWORDS,
    _S1_KEYWORDS,
)

KEYWORDS = {**SCENE_KEYWORDS, "S1": _S1_KEYWORDS}


def scene_supported(scene: str, text: str) -> bool:
    """文本里是否有该场景的关键词支撑。无关键词定义的场景保守放行。"""
    kws = KEYWORDS.get(scene)
    if not kws:
        return True
    return any(k in text for k in kws)


def main():
    ap = argparse.ArgumentParser(description="修正交叉场景标签")
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = cc.load_jsonl(Path(args.input))
    print("=" * 72)
    print(f"  交叉标签修正   输入 {len(rows)} 条")
    print("=" * 72)

    fixed = 0
    dropped_tags = Counter()
    changed_examples = []

    for r in rows:
        scenes = list(r.get("scenes") or [])
        if len(scenes) < 2:
            continue
        user = cc.row_user_text(r)
        keep = [scenes[0]] + [s for s in scenes[1:] if scene_supported(s, user)]
        if len(keep) == len(scenes):
            continue
        for s in scenes[1:]:
            if s not in keep:
                dropped_tags[f"{scenes[0]}+{s}"] += 1
        if len(changed_examples) < 6:
            changed_examples.append((r.get("sample_id"), scenes, keep, user))
        r["scenes"] = keep
        # 重写 assistant 末尾标签块
        for m in r.get("messages") or []:
            if m.get("role") == "assistant":
                m["content"] = strip_tags(m["content"]) + format_tags(
                    r.get("risk_level", ""), keep)
        r["cross_tags_fixed"] = True
        fixed += 1

    cc.write_jsonl(Path(args.out), rows)

    n_tags = Counter(len(r.get("scenes") or []) for r in rows)
    avg = sum(k * v for k, v in n_tags.items()) / len(rows) if rows else 0
    print(f"\n修正 {fixed} 条（{fixed/len(rows)*100:.0f}%）")
    print(f"标签数分布: {dict(sorted(n_tags.items()))}")
    print(f"平均标签数: {avg:.3f}")

    if dropped_tags:
        print(f"\n被去掉的附加标签：")
        for k, v in dropped_tags.most_common(12):
            print(f"  {k}  ×{v}")

    if changed_examples:
        print(f"\n修改示例：")
        for sid, old, new, user in changed_examples:
            print(f"  {sid}")
            print(f"    {'/'.join(old)} → {'/'.join(new)}")
            print(f"    「{user[:50]}」")

    print(f"\n输出 → {args.out}")


if __name__ == "__main__":
    main()
