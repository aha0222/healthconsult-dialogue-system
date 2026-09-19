"""小暖四版人格 · 评分统计与结论

读取大模型逐条评分结果，输出各人格的得分对比、显著性检验、安全性统计，
并给出默认人格的选型建议。

── 统计方法说明 ────────────────────────────────────────────────
只用标准库，不引入 numpy/scipy：
  · 置信区间用 bootstrap（1000 次重采样），不依赖正态假设
  · 显著性用置换检验（permutation test），以「场景」为区组单元——
    同一场景下的四版人格互为对照，消除了场景难度带来的干扰
不装额外依赖，是为了不让 CI 和环境因为一个分析脚本变重。

── 分析单元 ────────────────────────────────────────────────────
每条回复先按 (场景, 人格) 对两次生成取平均，得到 43 场景 × 4 人格 的矩阵，
再做配对比较。这样"人格差异"不会被"同一次生成的随机波动"稀释。

用法：
    python tools/analyze_scores.py
    python tools/analyze_scores.py --input tests/results/personality_scores_xxx.jsonl
"""

import argparse
import json
import math
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import RESULTS_DIR  # noqa: E402

PERSONAS = ["温婉邻居型", "贴心闺女型", "素朴家常型", "从容守护型"]
DIMENSIONS = ["可理解性", "情感温度", "实用可执行", "分寸感", "简洁度"]

HIGH_RISK = {"S0", "S1", "S2", "M0", "M1"}
MID_RISK = {"R3", "R2b"}

N_PERM = 10000
N_BOOT = 1000
SEED = 42


def load(path: Path) -> list:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def build_matrix(rows: list, key_field: str):
    """(场景) -> {人格: 平均分}"""
    buckets = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if key_field == "mean_score":
            v = r.get("mean_score")
        else:
            v = (r.get("scores") or {}).get(key_field)
        if v is None:
            continue
        buckets[r["scenario_id"]][r["personality"]].append(v)
    return {
        sid: {p: st.mean(vs) for p, vs in per.items() if vs}
        for sid, per in buckets.items()
    }


def paired_matrix(matrix: dict, personas=None):
    """只保留四版人格齐全的场景，返回 {场景: {人格: 分}} 与场景列表。"""
    personas = personas or PERSONAS
    out = {}
    for sid, per in matrix.items():
        if all(p in per for p in personas):
            out[sid] = {p: per[p] for p in personas}
    return out, sorted(out)


def mean_ci(values, n_boot=N_BOOT, rng=None):
    rng = rng or random.Random(SEED)
    if not values:
        return None, None, None
    m = st.mean(values)
    if len(values) < 2:
        return m, m, m
    boots = []
    n = len(values)
    for _ in range(n_boot):
        boots.append(st.mean(rng.choices(values, k=n)))
    boots.sort()
    return m, boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot)]


def permutation_test(matrix: dict, scenarios: list, personas: list,
                     n_perm=N_PERM, seed=SEED):
    """区组置换检验：在每个场景内打乱人格标签，看观测到的组间差异是否偶然。

    统计量 = 各人格均值的极差（max - min）。单边 p 值。
    """
    rng = random.Random(seed)

    def stat(labels_by_scenario):
        sums = {p: [] for p in personas}
        for sid in scenarios:
            for p, v in zip(personas, labels_by_scenario[sid]):
                sums[p].append(v)
        means = [st.mean(sums[p]) for p in personas if sums[p]]
        return max(means) - min(means) if len(means) > 1 else 0.0

    observed_vals = {sid: [matrix[sid][p] for p in personas] for sid in scenarios}
    observed = stat(observed_vals)

    count = 0
    for _ in range(n_perm):
        perm = {sid: rng.sample(vals, len(vals)) for sid, vals in observed_vals.items()}
        if stat(perm) >= observed - 1e-12:
            count += 1
    return observed, count / n_perm


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.input:
        in_path = Path(args.input)
    else:
        cands = sorted(p for p in RESULTS_DIR.glob("personality_scores_*.jsonl")
                       if not p.name.startswith("_"))
        if not cands:
            print("[错误] 未找到评分结果")
            sys.exit(1)
        in_path = cands[-1]

    rows = load(in_path)
    print("=" * 78)
    print(f"  小暖四版人格 · 评分分析")
    print(f"  输入: {in_path.name}（{len(rows)} 条评分）")
    print("=" * 78)

    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    # ── 1. 各人格综合得分 ──────────────────────────────────────────
    mtx_all = build_matrix(rows, "mean_score")
    paired, scenarios = paired_matrix(mtx_all)
    out(f"\n有效场景: {len(scenarios)}（四版人格齐全）")

    out("\n## 一、综合得分（五维度均分）\n")
    out("| 人格 | 均分 | 95% CI | 标准差 | 场景数 |")
    out("|------|------|--------|--------|--------|")
    rng = random.Random(SEED)
    persona_scores = {}
    for p in PERSONAS:
        vals = [paired[s][p] for s in scenarios]
        persona_scores[p] = vals
        m, lo, hi = mean_ci(vals, rng=rng)
        out(f"| {p} | **{m:.3f}** | [{lo:.3f}, {hi:.3f}] | {st.pstdev(vals):.3f} | {len(vals)} |")

    obs, pval = permutation_test(paired, scenarios, PERSONAS)
    out(f"\n整体差异（区组置换检验，{N_PERM} 次）：极差 {obs:.3f}，p = {pval:.4f}"
        f"{'  ← 显著' if pval < 0.05 else '  ← 不显著'}")

    # ── 2. 分维度 ──────────────────────────────────────────────────
    out("\n## 二、分维度得分\n")
    out("| 维度 | " + " | ".join(PERSONAS) + " | 区分度 |")
    out("|------|" + "------|" * (len(PERSONAS) + 1))
    dim_matrix = {}
    for d in DIMENSIONS:
        mtx = build_matrix(rows, d)
        pm, sc = paired_matrix(mtx)
        dim_matrix[d] = pm
        means = []
        for p in PERSONAS:
            vals = [pm[s][p] for s in sc if p in pm.get(s, {})]
            means.append(st.mean(vals) if vals else float("nan"))
        spread = max(means) - min(means)
        out(f"| {d} | " + " | ".join(f"{m:.2f}" for m in means) + f" | {spread:.3f} |")

    # ── 3. 分层：高风险场景 ────────────────────────────────────────
    risk_of = {}
    for r in rows:
        risk_of[r["scenario_id"]] = r["risk"]

    out("\n## 三、按风险分层（这才是关键——安全场景才见真章）\n")
    strata = [
        ("高危（人身安全 / 心理危机）", HIGH_RISK),
        ("中危（急症 / 紧急就医）", MID_RISK),
        ("常规（其余）", None),
    ]
    for label, risks in strata:
        if risks is None:
            subs = [s for s in scenarios if risk_of.get(s) not in HIGH_RISK | MID_RISK]
        else:
            subs = [s for s in scenarios if risk_of.get(s) in risks]
        if not subs:
            continue
        out(f"\n**{label}** —— {len(subs)} 个场景\n")
        out("| 人格 | 均分 | 95% CI |")
        out("|------|------|--------|")
        for p in PERSONAS:
            vals = [paired[s][p] for s in subs]
            m, lo, hi = mean_ci(vals, rng=rng)
            out(f"| {p} | **{m:.3f}** | [{lo:.3f}, {hi:.3f}] |")
        if len(subs) >= 3:
            o, pv = permutation_test(paired, subs, PERSONAS, n_perm=N_PERM)
            out(f"\n组间差异：极差 {o:.3f}，p = {pv:.4f}"
                f"{'  ← 显著' if pv < 0.05 else '  ← 不显著'}")

    # ── 4. 两两对比 ────────────────────────────────────────────────
    out("\n## 四、两两对比（区组置换检验）\n")
    from itertools import combinations
    out("| 对比 | 均分差 | p 值 | 结论 |")
    out("|------|--------|------|------|")
    pairs = list(combinations(PERSONAS, 2))
    raw = []
    for a, b in pairs:
        o, pv = permutation_test(paired, scenarios, [a, b], n_perm=N_PERM)
        diff = st.mean(persona_scores[a]) - st.mean(persona_scores[b])
        raw.append((a, b, diff, pv, o))
    # Bonferroni 校正
    alpha = 0.05 / len(pairs)
    for a, b, diff, pv, o in sorted(raw, key=lambda x: x[2], reverse=True):
        verdict = "显著" if pv < alpha else ("边缘" if pv < 0.05 else "不显著")
        out(f"| {a} vs {b} | {diff:+.3f} | {pv:.4f} | {verdict} |")
    out(f"\n（Bonferroni 校正后阈值 α = {alpha:.4f}，共 {len(pairs)} 组对比）")

    # ── 5. 安全性 ──────────────────────────────────────────────────
    out("\n## 五、安全性统计\n")
    out("| 人格 | 评委判红线 | 本地检查器命中 | 两者不一致 |")
    out("|------|-----------|---------------|-----------|")
    for p in PERSONAS:
        sub = [r for r in rows if r["personality"] == p]
        n = len(sub)
        llm = sum(1 for r in sub if r.get("llm_safety_redline"))
        loc = sum(1 for r in sub if r.get("local_check_violations"))
        dis = sum(1 for r in sub if r.get("local_check_disagrees"))
        out(f"| {p} | {llm}/{n} | {loc}/{n} | {dis}/{n} |")

    # ── 6. 效度检查 ────────────────────────────────────────────────
    out("\n## 六、量表效度检查\n")
    lens = [r["reply_chars"] for r in rows]
    means = [r["mean_score"] for r in rows if r["mean_score"] is not None]
    if len(lens) == len(means):
        rho = spearman(lens, means)
        out(f"- 字数 vs 综合得分 的 Spearman 相关：**{rho:+.3f}**"
            f"{'（|ρ|>0.5，得分很可能主要被篇幅驱动）' if abs(rho) > 0.5 else ''}")
    sat = []
    for d in DIMENSIONS:
        vals = [r["scores"][d] for r in rows if (r.get("scores") or {}).get(d) is not None]
        c = Counter(vals)
        top = c.most_common(1)[0]
        sat.append((d, top[0], top[1] / len(vals)))
    out("- 各维度众数占比（越高说明该维度越没有区分力）：")
    for d, mode, share in sorted(sat, key=lambda x: -x[2]):
        out(f"  - {d}：{mode} 分占 {share * 100:.0f}%")

    # ── 7. 结论 ────────────────────────────────────────────────────
    out("\n## 七、结论\n")
    ranking = sorted(PERSONAS, key=lambda p: st.mean(persona_scores[p]), reverse=True)
    out(f"按综合得分排名：{' > '.join(ranking)}")
    if pval < 0.05:
        out(f"\n整体差异显著（p = {pval:.4f}），可以判定存在真实的人格间差异。")
    else:
        out(f"\n整体差异**不显著**（p = {pval:.4f}）——四版人格在这些维度上得分相当，")
        out("无法仅凭综合得分判定优劣。此时应结合分维度得分与安全性统计综合判断，")
        out("并考虑补充强制排序（见 tools/ 下的相关说明）来获得更明确的区分。")

    report = RESULTS_DIR / f"personality_analysis_{in_path.stem.replace('personality_scores_', '')}.md"
    report.write_text("# 小暖四版人格 · 评分分析报告\n\n```\n" + "\n".join(lines) + "\n```\n",
                      encoding="utf-8")
    print(f"\n{'=' * 78}")
    print(f"  报告已保存：{report}")
    print(f"{'=' * 78}")


if __name__ == "__main__":
    main()
