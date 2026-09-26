"""语料库分布与质量验收（Phase 5 的 gate）

对合并后的语料做三项体检，任何一项不达标就以非零退出码结束，可接入 CI：

  1. 分布：每个 (场景, 风险) 格子与目标矩阵的偏差
  2. 多样性：近重复率（5-gram Jaccard）
  3. 合规：篇幅合规率、场景/风险覆盖、标签合法性

用法：
    python tools/check_distribution.py --input <merged.jsonl> --tolerance 0.15
    python tools/check_distribution.py --input <merged.jsonl> --json
"""

import argparse
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402

REPO_ROOT = cc.REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.taxonomy import strip_tags  # noqa: E402
from backend.app.safety.safety_checker import check_reply  # noqa: E402


def row_body(row: dict) -> str:
    for m in row.get("messages") or []:
        if m.get("role") == "assistant":
            return strip_tags(m.get("content", ""))
    return strip_tags(row.get("assistant", ""))


def row_user(row: dict) -> str:
    if row.get("user"):
        return row["user"]
    for m in row.get("messages") or []:
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def main():
    ap = argparse.ArgumentParser(description="语料库分布与质量验收")
    ap.add_argument("--input", required=True)
    ap.add_argument("--targets", default=str(cc.TARGETS_PATH))
    ap.add_argument("--tolerance", type=float, default=0.15,
                    help="单个格子的允许偏差")
    ap.add_argument("--dup-threshold", type=float, default=0.6,
                    help="5-gram Jaccard 近重复阈值")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = cc.load_jsonl(Path(args.input))
    targets = cc.flatten_targets(cc.load_targets(args.targets))
    if not rows:
        print("[错误] 语料为空")
        sys.exit(1)

    problems = []
    report = {"total": len(rows)}

    # ── 1. 分布 ────────────────────────────────────────────────────
    have = cc.count_matrix(rows)
    devs = []
    for cell, want in targets.items():
        got = have.get(cell, 0)
        dev = (got - want) / want if want else 0.0
        devs.append((abs(dev), cell, got, want, dev))
    devs.sort(reverse=True)
    worst = devs[0] if devs else None
    over = [d for d in devs if d[0] > args.tolerance]
    missing = [c for c in targets if have.get(c, 0) == 0]
    extra = [c for c in have if c not in targets]

    report["cells"] = {
        "target": len(targets), "covered": len(targets) - len(missing),
        "max_deviation": round(worst[0], 3) if worst else 0,
        "worst_cell": f"{worst[1][0]}/{worst[1][1]}" if worst else "",
        "over_tolerance": len(over), "missing": [f"{c[0]}/{c[1]}" for c in missing],
        "not_in_matrix": [f"{c[0]}/{c[1]}" for c in extra],
    }
    if missing:
        problems.append(f"有 {len(missing)} 个格子完全没有语料")
    if over:
        problems.append(f"有 {len(over)} 个格子偏差超过 {args.tolerance:.0%}")
    if extra:
        problems.append(f"有 {len(extra)} 个格子不在目标矩阵内")

    # ── 2. 多样性 ──────────────────────────────────────────────────
    users = [row_user(r) for r in rows]
    exact_dups, near_dups = [], []
    seen = {}
    for i, u in enumerate(users):
        k = cc.norm_text(u)
        if not k:
            continue
        if k in seen:
            exact_dups.append((seen[k], i))
            continue
        seen[k] = i
    # 近重复：只在窗口内两两比，避免 O(n²) 太慢
    WINDOW = 60
    for i in range(len(users)):
        for j in range(i + 1, min(i + WINDOW, len(users))):
            if cc.ngram_jaccard(users[i], users[j]) >= args.dup_threshold:
                near_dups.append((i, j))
    dup_rate = (len(exact_dups) + len(near_dups)) / len(rows)
    report["duplication"] = {
        "exact": len(exact_dups), "near": len(near_dups),
        "rate": round(dup_rate, 4),
    }
    if dup_rate >= 0.03:
        problems.append(f"近重复率 {dup_rate:.1%} 超过 3%")

    # ── 3. 合规 ────────────────────────────────────────────────────
    len_bad = 0
    tag_bad = []
    safety_bad = []
    for r in rows:
        risk = r.get("risk_level") or ""
        scenes = r.get("scenes") or []
        scene0 = scenes[0] if scenes else ""
        body = row_body(r)
        if not cc.length_ok(body, risk, scene0):
            len_bad += 1
        for s in scenes:
            msg = cc.check_scene_risk(s, risk)
            if msg:
                tag_bad.append(f"{r.get('sample_id')}: {msg}")
                break
        issues = check_reply(body, risk, scenes, row_user(r))
        if issues:
            safety_bad.append(f"{r.get('sample_id')}: {issues[0]}")

    st_ = cc.tag_stats(rows)
    len_rate = 1 - len_bad / len(rows)
    report["compliance"] = {
        "length_ok_rate": round(len_rate, 4),
        "length_bad": len_bad,
        "tag_conflicts": len(tag_bad),
        "safety_issues": len(safety_bad),
        "scene_coverage": st_["scene_coverage"],
        "risk_coverage": st_["risk_coverage"],
        "avg_scenes": st_["avg_scenes"],
    }
    if len_rate < 0.95:
        problems.append(f"篇幅合规率 {len_rate:.1%} 低于 95%")
    if tag_bad:
        problems.append(f"有 {len(tag_bad)} 条标签与风险矛盾")
    if safety_bad:
        problems.append(f"有 {len(safety_bad)} 条未通过安全检查")
    if st_["scene_coverage"] < len(cc.SCENES):
        problems.append(f"场景覆盖 {st_['scene_coverage']}/{len(cc.SCENES)}，有遗漏")
    if not (1.15 <= st_["avg_scenes"] <= 1.35):
        problems.append(f"平均标签数 {st_['avg_scenes']} 偏离目标区间 1.15-1.35")

    report["pass"] = not problems
    report["problems"] = problems

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print("  语料库验收")
        print("=" * 72)
        print(f"\n总量 {len(rows)} 条")
        c = report["cells"]
        print(f"\n【分布】目标 {c['target']} 格子，已覆盖 {c['covered']}")
        print(f"  最大偏差 {c['max_deviation']:.1%}  （{c['worst_cell']}）")
        if c["missing"]:
            print(f"  ⚠️ 完全空白的格子：{', '.join(c['missing'])}")
        if over:
            print(f"  超容差格子：")
            for d, cell, got, want, dev in over[:8]:
                print(f"    {cell[0]}/{cell[1]}  目标 {want}  实际 {got}  ({dev:+.0%})")
        d = report["duplication"]
        print(f"\n【多样性】完全重复 {d['exact']}  近重复 {d['near']}  合计 {d['rate']:.1%}（门槛 <3%）")
        k = report["compliance"]
        print(f"\n【合规】")
        print(f"  篇幅合规率   {k['length_ok_rate']:.1%}（门槛 ≥95%）")
        print(f"  标签矛盾     {k['tag_conflicts']}")
        print(f"  安全问题     {k['safety_issues']}")
        print(f"  场景覆盖     {k['scene_coverage']}/{len(cc.SCENES)}")
        print(f"  风险覆盖     {k['risk_coverage']}/{len(cc.RISK_LEVELS)}")
        print(f"  平均标签数   {k['avg_scenes']}（目标 1.15-1.35）")

        print("\n" + "=" * 72)
        if problems:
            print("  ❌ 未通过")
            for p in problems:
                print(f"    · {p}")
        else:
            print("  ✅ 全部达标")
        print("=" * 72)

    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
