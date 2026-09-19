"""人工抽检 vs 大模型评分 · 一致性分析

这是整条结论链上最关键的一步：前面所有"哪版人格更好"的判断都建立在大模型
评委身上，而这个脚本回答的是——**大模型评委的分数可信吗？**

── 看什么 ──────────────────────────────────────────────────────
1. 偏差（bias）：人工和大模型的均分差多少。大模型系统性偏松是常见现象，
   只要偏差是**跨人格一致**的，人格之间的相对排序可能依然成立。
2. 相关（correlation）：两边对"哪条更好"的判断是否一致。这比均分更关键——
   均分可以差很多而排序完全一致。
3. 逐条吻合率：完全一致、相差 1 分以内的比例。
4. 人格排序稳健性：用人工分重排一次，看是否和用大模型分排出来的一样。
   如果排序变了，前面的结论就要推翻。

用法：
    python tools/analyze_agreement.py
    python tools/analyze_agreement.py --human tests/results/human_review_result.csv
"""

import argparse
import csv
import json
import math
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import RESULTS_DIR, EXAMPLES_DIR  # noqa: E402
from _rubric import DIMENSION_NAMES as DIMENSIONS  # noqa: E402

PERSONAS = ["温婉邻居型", "贴心闺女型", "素朴家常型", "从容守护型"]
DEFAULT_KEY = EXAMPLES_DIR / "manual_review" / "manual_review_KEY_single_请勿提前打开.csv"

SEED = 42
N_PERM = 10000
N_BOOT = 1000


def read_csv(path: Path) -> list:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def spearman(xs, ys):
    if len(xs) < 3:
        return 0.0

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else 0.0


def boot_ci(vals, rng, n=N_BOOT):
    if len(vals) < 2:
        return (vals[0] if vals else 0, vals[0] if vals else 0)
    ms = sorted(st.mean(rng.choices(vals, k=len(vals))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def permutation_p(groups: dict, rng, n=N_PERM):
    """组间均值极差的置换检验（不配对版本，用于人工分的人格比较）。"""
    keys = list(groups)
    pooled = [v for k in keys for v in groups[k]]
    obs = max(st.mean(groups[k]) for k in keys) - min(st.mean(groups[k]) for k in keys)
    cnt = 0
    for _ in range(n):
        rng.shuffle(pooled)
        i = 0
        means = []
        for k in keys:
            means.append(st.mean(pooled[i:i + len(groups[k])]))
            i += len(groups[k])
        if max(means) - min(means) >= obs - 1e-12:
            cnt += 1
    return obs, cnt / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--human", default=str(RESULTS_DIR / "human_review_result.csv"))
    ap.add_argument("--key", default=str(DEFAULT_KEY))
    args = ap.parse_args()

    human_path, key_path = Path(args.human), Path(args.key)
    for p in (human_path, key_path):
        if not p.exists():
            print(f"[错误] 找不到文件：{p}")
            sys.exit(1)

    human = {r["review_id"]: r for r in read_csv(human_path)}
    key = {r["review_id"]: r for r in read_csv(key_path)}

    joined = [rid for rid in human if rid in key]
    print("=" * 78)
    print("  人工抽检 vs 大模型评分 · 一致性分析")
    print(f"  人工: {human_path.name}（{len(human)} 条）")
    print(f"  对照: {key_path.name}")
    print(f"  成功比对: {len(joined)} 条")
    print("=" * 78)

    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    rng = random.Random(SEED)

    # ── 1. 分维度一致性 ────────────────────────────────────────────
    out("\n## 一、分维度一致性\n")
    out("| 维度 | 人工均分 | 大模型均分 | 偏差(人−模) | Spearman ρ | 完全一致 | 相差≤1 |")
    out("|------|---------|-----------|------------|-----------|---------|--------|")
    dim_stats = {}
    for d in DIMENSIONS:
        hs = [int(human[r][d]) for r in joined if human[r].get(d)]
        ls = [int(key[r]["大模型_" + d]) for r in joined if key[r].get("大模型_" + d)]
        if not hs or not ls:
            continue
        n = min(len(hs), len(ls))
        hs, ls = hs[:n], ls[:n]
        diff = [h - l for h, l in zip(hs, ls)]
        exact = sum(1 for x in diff if x == 0) / n
        within1 = sum(1 for x in diff if abs(x) <= 1) / n
        rho = spearman(hs, ls)
        dim_stats[d] = {"human": st.mean(hs), "llm": st.mean(ls), "bias": st.mean(diff), "rho": rho}
        out(f"| {d} | {st.mean(hs):.2f} | {st.mean(ls):.2f} | {st.mean(diff):+.2f} "
            f"| {rho:+.3f} | {exact*100:.0f}% | {within1*100:.0f}% |")

    # 综合
    hm = [st.mean(int(human[r][d]) for d in DIMENSIONS if human[r].get(d)) for r in joined]
    lm = [st.mean(int(key[r]["大模型_" + d]) for d in DIMENSIONS if key[r].get("大模型_" + d))
          for r in joined]
    n = min(len(hm), len(lm))
    hm, lm = hm[:n], lm[:n]
    diff = [h - l for h, l in zip(hm, lm)]
    exact = sum(1 for x in diff if abs(x) < 1e-9) / n
    within1 = sum(1 for x in diff if abs(x) <= 1) / n
    out(f"| **综合** | **{st.mean(hm):.2f}** | **{st.mean(lm):.2f}** | **{st.mean(diff):+.2f}** "
        f"| **{spearman(hm, lm):+.3f}** | {exact*100:.0f}% | {within1*100:.0f}% |")

    lo, hi = boot_ci(diff, rng)
    out(f"\n综合偏差的 95% 置信区间：[{lo:+.2f}, {hi:+.2f}]"
        f"{'  ← 不含 0，偏差真实存在' if lo > 0 or hi < 0 else '  ← 含 0，偏差不明确'}")
    if st.mean(diff) < -0.05:
        out("→ 人工**严于**大模型：大模型评委偏松。")
    elif st.mean(diff) > 0.05:
        out("→ 人工**宽于**大模型。")
    else:
        out("→ 两边尺度基本对齐。")

    # ── 2. 分歧最大的条目 ─────────────────────────────────────────
    out("\n## 二、分歧最大的条目（人工比大模型低 1 分以上）\n")
    gaps = []
    for r in joined:
        h = st.mean(int(human[r][d]) for d in DIMENSIONS if human[r].get(d))
        l = st.mean(int(key[r]["大模型_" + d]) for d in DIMENSIONS if key[r].get("大模型_" + d))
        gaps.append((l - h, r, h, l, key[r]["人格"], key[r]["scenario_id"], human[r].get("note", "")))
    gaps.sort(reverse=True)
    out("| review_id | 场景 | 人格 | 人工 | 大模型 | 差 | 人工备注 |")
    out("|-----------|------|------|------|--------|----|---------|")
    shown = 0
    for g, r, h, l, p, sid, note in gaps:
        if g < 1.0 or shown >= 12:
            continue
        note_short = (note[:52] + "…") if len(note) > 52 else (note or "—")
        out(f"| {r} | {sid} | {p} | {h:.1f} | {l:.1f} | {g:+.1f} | {note_short} |")
        shown += 1
    if shown == 0:
        out("（无）")

    # ── 3. 人格排序稳健性 ─────────────────────────────────────────
    out("\n## 三、人格排序稳健性（用人工分重排一遍）\n")
    by_persona_h = defaultdict(list)
    by_persona_l = defaultdict(list)
    for r in joined:
        p = key[r]["人格"]
        by_persona_h[p].append(st.mean(int(human[r][d]) for d in DIMENSIONS if human[r].get(d)))
        by_persona_l[p].append(
            st.mean(int(key[r]["大模型_" + d]) for d in DIMENSIONS if key[r].get("大模型_" + d)))

    out("| 人格 | 人工均分 | 95% CI | n | 大模型均分 | 人工排名 | 模型排名 |")
    out("|------|---------|--------|---|-----------|---------|---------|")
    h_means = {p: st.mean(v) for p, v in by_persona_h.items()}
    l_means = {p: st.mean(v) for p, v in by_persona_l.items()}
    h_rank = {p: i + 1 for i, p in enumerate(sorted(h_means, key=h_means.get, reverse=True))}
    l_rank = {p: i + 1 for i, p in enumerate(sorted(l_means, key=l_means.get, reverse=True))}
    for p in PERSONAS:
        if p not in h_means:
            continue
        lo, hi = boot_ci(by_persona_h[p], rng)
        out(f"| {p} | **{h_means[p]:.2f}** | [{lo:.2f}, {hi:.2f}] | {len(by_persona_h[p])} "
            f"| {l_means[p]:.2f} | {h_rank[p]} | {l_rank[p]} |")

    rank_rho = spearman([h_means[p] for p in PERSONAS if p in h_means],
                        [l_means[p] for p in PERSONAS if p in h_means])
    out(f"\n两种排名的 Spearman 相关：**{rank_rho:+.3f}**")
    h_top = min(h_means, key=lambda p: h_rank[p])
    l_top = min(l_means, key=lambda p: l_rank[p])
    if h_top == l_top:
        out(f"→ 人工与大模型给出的**第一名一致**：{h_top}")
    else:
        out(f"→ ⚠️ **排名发生变化**：人工第一 = {h_top}，大模型第一 = {l_top}")

    obs, pv = permutation_p({p: v for p, v in by_persona_h.items() if v}, rng)
    out(f"\n人工分的人格间差异（置换检验）：极差 {obs:.3f}，p = {pv:.4f}"
        f"{'  ← 显著' if pv < 0.05 else '  ← 不显著'}")
    out(f"（注意：抽检样本仅 {len(joined)} 条，每个人格约 {len(joined)//4} 条，"
        f"统计效力有限，此处结论应视为方向性参考）")

    # ── 4. 结论 ───────────────────────────────────────────────────
    out("\n## 四、结论\n")
    overall_rho = spearman(hm, lm)
    if overall_rho >= 0.5:
        out(f"- 人工与大模型的逐条相关 ρ = {overall_rho:+.3f}，属**可接受**水平，"
            f"大模型评分在相对排序上可信。")
    elif overall_rho >= 0.3:
        out(f"- 人工与大模型的逐条相关 ρ = {overall_rho:+.3f}，**偏弱**。"
            f"大模型评分可用于粗略排序，但不宜作为唯一依据。")
    else:
        out(f"- 人工与大模型的逐条相关 ρ = {overall_rho:+.3f}，**很弱**。"
            f"大模型评分的区分能力存疑，人格结论需谨慎表述。")
    if abs(st.mean(diff)) > 0.5:
        out(f"- 存在 **{st.mean(diff):+.2f} 分的系统性偏差**，人工明显更严。"
            f"报告里应写明「大模型评分为宽松尺度，仅用于相对比较」。")
    if h_top == l_top:
        out(f"- 两种口径下第一名一致（{h_top}），核心结论**稳健**。")
    else:
        out(f"- 两种口径下第一名不同，核心结论**不稳健**，需要扩大人工样本或改用配对比较。")

    report = RESULTS_DIR / "agreement_analysis.md"
    report.write_text("# 人工抽检 vs 大模型评分 · 一致性分析\n\n" + "\n".join(lines) + "\n",
                      encoding="utf-8")
    print(f"\n{'=' * 78}")
    print(f"  报告已保存：{report}")
    print(f"{'=' * 78}")


if __name__ == "__main__":
    main()
