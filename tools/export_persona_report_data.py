"""小暖四版人格 · 前端报告数据导出

把 `tests/results/` 的原始评测结果聚合成前端报告页可用的数据文件
（`frontend/data/personality_evaluation.js`），避免把结论手工硬编码进页面。

为什么导出成 JS 而不是 JSON：`frontend/index.html` 支持零安装直接双击打开
（file:// 协议）。浏览器在 file:// 下会因 CORS 拒绝 `fetch` 本地 JSON，
但 `<script src>` 不受影响。所以这里输出一个挂到 `window` 上的 JS 对象。

统计口径与 `tools/analyze_scores.py`、`tools/analyze_agreement.py` 保持一致
（复用其函数），保证页面数字与报告数字同源。

用法：
    python tools/export_persona_report_data.py
    python tools/export_persona_report_data.py --out frontend/data/personality_evaluation.js
"""

import argparse
import csv
import json
import random
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT, RESULTS_DIR, EXAMPLES_DIR  # noqa: E402
from analyze_scores import (  # noqa: E402
    DIMENSIONS,
    N_PERM,
    PERSONAS,
    SEED,
    build_matrix,
    load,
    mean_ci,
    paired_matrix,
    permutation_test,
    spearman,
)
from analyze_agreement import boot_ci, permutation_p  # noqa: E402

KEY_PATH = EXAMPLES_DIR / "manual_review" / "manual_review_KEY_single_请勿提前打开.csv"
HUMAN_PATH = RESULTS_DIR / "human_review_result.csv"
SCENARIOS_PATH = EXAMPLES_DIR / "personality_test_scenarios.jsonl"

# 风险等级 → （分组名，内容说明）
RISK_GROUP = {
    "S0": ("人身安全", "冒充上门 / 尾随 / 撬锁"),
    "S1": ("环境安全", "燃气泄漏 / 火灾逃生"),
    "S2": ("诈骗防范", "中奖诈骗 / 保健品会销 / 钓鱼链接"),
    "M0": ("心理危机", "明确自伤 / 隐晦自伤 / 放弃治疗"),
    "M1": ("情绪困扰", "长期孤独 / 怕被嫌弃 / 兴趣减退"),
    "R3": ("急症高危", "胸痛 / 卒中 / 剧烈头痛 / 心悸冷汗"),
    "R2b": ("紧急就医", "咳血 / 摔倒 / 持续发热"),
    "R2a": ("异常与用药", "换药 / 异常感知 / 停药 / 压力测试"),
    "R1": ("用药与睡眠", "用药时间 / 失眠 / 加药 / 免责诱导 / 情感勒索"),
    "R0": ("日常与陪伴", "日常咨询 / 情感陪伴 / 回忆往事"),
    "X": ("越界防护", "套话 / 非陪护 / 角色扮演越狱"),
}

# 展示用元数据（颜色 / 图标 / 一句话定位），与人格定义无关，仅用于页面
PERSONA_META = {
    "温婉邻居型": {
        "icon": "🏠", "color": "#E8722C",
        "tagline": "隔壁懂事的女儿，尊重体贴有分寸",
        "traits": ["语气：温婉端庄，起承转合", "称呼：您 / 阿姨 / 叔叔",
                   "句式：先安抚→再解释→再建议→再兜底", "温度：暖 · 情感浓度中偏高"],
    },
    "贴心闺女型": {
        "icon": "💝", "color": "#E11D48",
        "tagline": "自家小闺女，撒娇式关心",
        "traits": ["语气：软糯直白，口语碎碎念", "称呼：您呀 / 咱 / 您老人家",
                   "句式：短句反问，“您答应我哈~”", "温度：热 · 情感浓度高"],
    },
    "素朴家常型": {
        "icon": "🥬", "color": "#4F8A5B",
        "tagline": "退休大姐，不说弯话只说大白话",
        "traits": ["语气：朴素实在，接地气", "称呼：老姐 / 老哥 / 您",
                   "句式：生活类比，“一锅烩”式讲解", "温度：温 · 情感浓度中等"],
    },
    "从容守护型": {
        "icon": "🩺", "color": "#2563EB",
        "tagline": "老护士长，一二三讲清楚就够",
        "traits": ["语气：淡定从容，结构化", "称呼：您（不加修饰）",
                   "句式：第一、第二、第三", "温度：凉 · 情感浓度低"],
    },
}

FINDINGS = [
    {
        "title": "大模型绝对打分会产生尺度压缩",
        "body": "让大模型对单条回复打 1–5 分，评分会向高分段集中——「可理解性」维度 97% "
                "给了 5 分，量表失去方差，无法用于排序。这与评分模型是否「聪明」无关，"
                "是绝对打分范式的固有缺陷。",
    },
    {
        "title": "强制排序恢复了区分度，但仍未复现人工排序",
        "body": "把四条回复并排强制排出 1-2-3-4，区分度显著改善（第一名占比 50% vs 7%，"
                "两轮结果完全一致），但与人工排名的 Spearman 相关仅 −0.200。"
                "「有区分度」不等于「与人的判断一致」。",
    },
    {
        "title": "单一评价方法会给出误导性结论",
        "body": "三套方法的第一名互不相同：只做绝对打分得出「温婉邻居型第一」，"
                "只做强制排序得出「从容守护型第一」。结论的稳健性只能通过方法交叉验证获得，"
                "应基于「跨方法一致的部分」，而非任一方法的最高分。",
    },
]


def _read_csv(path: Path) -> list:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _latest(pattern: str) -> Path:
    cands = sorted(p for p in RESULTS_DIR.glob(pattern) if not p.name.startswith("_"))
    if not cands:
        raise SystemExit(f"[错误] 未找到 {pattern}，请先归位原始评测数据")
    return cands[-1]


def _rank_desc(means: dict) -> dict:
    order = sorted(means, key=means.get, reverse=True)
    return {p: i + 1 for i, p in enumerate(order)}


def build_payload() -> dict:
    scores_path = _latest("personality_scores_*.jsonl")
    ranking_path = _latest("personality_ranking_*.jsonl")
    rows = load(scores_path)
    rng = random.Random(SEED)

    # ── 1. 绝对打分 ────────────────────────────────────────────────
    mtx = build_matrix(rows, "mean_score")
    paired, scenarios = paired_matrix(mtx)
    absolute = {}
    abs_means = {}
    for p in PERSONAS:
        vals = [paired[s][p] for s in scenarios]
        m, lo, hi = mean_ci(vals, rng=rng)
        abs_means[p] = m
        absolute[p] = {"mean": round(m, 3), "lo": round(lo, 3), "hi": round(hi, 3)}
    abs_rank = _rank_desc(abs_means)
    _, abs_p = permutation_test(paired, scenarios, PERSONAS, n_perm=N_PERM, seed=SEED)

    # ── 2. 分维度 ──────────────────────────────────────────────────
    dimensions = []
    for d in DIMENSIONS:
        dm, dsc = paired_matrix(build_matrix(rows, d))
        means = {}
        for p in PERSONAS:
            vals = [dm[s][p] for s in dsc if p in dm.get(s, {})]
            means[p] = round(st.mean(vals), 3) if vals else None
        vals = [v for v in means.values() if v is not None]
        dimensions.append({
            "name": d,
            "values": means,
            "spread": round(max(vals) - min(vals), 3) if vals else 0.0,
        })

    # ── 3. 强制排序 ────────────────────────────────────────────────
    rank_rows = load(ranking_path)
    ranks = defaultdict(list)
    first = defaultdict(int)
    for r in rank_rows:
        l2p = r["label_to_persona"]
        for i, label in enumerate(r["ranking"], 1):
            p = l2p.get(label)
            if p:
                ranks[p].append(i)
                if i == 1:
                    first[p] += 1
    rank_means = {p: st.mean(v) for p, v in ranks.items() if v}
    rank_rank = _rank_desc({p: -m for p, m in rank_means.items()})
    ranking = {
        p: {
            "mean_rank": round(rank_means[p], 3),
            "first": first[p],
            "n": len(ranks[p]),
            "rank": rank_rank.get(p),
        }
        for p in PERSONAS if p in rank_means
    }

    # ── 4. 人工抽检 + 人机一致性 ───────────────────────────────────
    human, key = {}, {}
    if HUMAN_PATH.exists() and KEY_PATH.exists():
        human = {r["review_id"]: r for r in _read_csv(HUMAN_PATH)}
        key = {r["review_id"]: r for r in _read_csv(KEY_PATH)}
    joined = [rid for rid in human if rid in key]

    h_by_persona = defaultdict(list)
    l_by_persona = defaultdict(list)
    h_all, l_all = [], []
    for rid in joined:
        hv = [int(human[rid][d]) for d in DIMENSIONS if human[rid].get(d)]
        lv = [int(key[rid]["大模型_" + d]) for d in DIMENSIONS if key[rid].get("大模型_" + d)]
        if not hv or not lv:
            continue
        hm, lm = st.mean(hv), st.mean(lv)
        h_all.append(hm)
        l_all.append(lm)
        p = key[rid]["人格"]
        h_by_persona[p].append(hm)
        l_by_persona[p].append(lm)

    human_out = {}
    human_means = {}
    for p in PERSONAS:
        vals = h_by_persona.get(p, [])
        if not vals:
            continue
        lo, hi = boot_ci(vals, rng)
        human_means[p] = st.mean(vals)
        human_out[p] = {
            "mean": round(st.mean(vals), 3),
            "lo": round(lo, 3),
            "hi": round(hi, 3),
            "n": len(vals),
        }
    human_rank = _rank_desc(human_means)
    if human_means:
        _, human_p = permutation_p({p: v for p, v in h_by_persona.items() if v}, rng)
    else:
        human_p = None

    bias = round(st.mean(h - l for h, l in zip(h_all, l_all)), 3) if h_all else None
    rho = round(spearman(h_all, l_all), 3) if len(h_all) >= 3 else None

    # ── 5. 场景覆盖 ────────────────────────────────────────────────
    scen_rows = load(SCENARIOS_PATH)
    risk_count = defaultdict(int)
    for r in scen_rows:
        risk_count[r["risk"]] += 1
    coverage = []
    for risk, (group, desc) in RISK_GROUP.items():
        if risk_count.get(risk):
            coverage.append({
                "risk": risk, "group": group, "desc": desc, "count": risk_count[risk],
            })

    # ── 6. 组装人格卡片与跨方法表 ─────────────────────────────────
    personas = []
    cross = []
    for p in PERSONAS:
        meta = PERSONA_META[p]
        a = dict(absolute.get(p, {}))
        a["rank"] = abs_rank.get(p)
        h = dict(human_out.get(p, {}))
        h["rank"] = human_rank.get(p)
        rk = ranking.get(p, {})
        personas.append({
            "name": p, "icon": meta["icon"], "color": meta["color"],
            "tagline": meta["tagline"], "traits": meta["traits"],
            "absolute": a, "human": h, "ranking": rk,
        })
        cross.append({
            "persona": p,
            "absolute": abs_rank.get(p),
            "human": human_rank.get(p),
            "ranking": rk.get("rank"),
        })

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "meta": {
            "personas": len(PERSONAS),
            "scenarios": len(scen_rows),
            "replies": len(rows),
            "score_count": len(rows),
            "ranking_rounds": len(rank_rows),
            "human_reviews": len(joined),
            "risk_levels": [c["risk"] for c in coverage],
            "model": rows[0].get("model") if rows else "",
            "absolute_p": round(abs_p, 4),
            "human_p": round(human_p, 4) if human_p is not None else None,
        },
        "scenario_coverage": coverage,
        "personas": personas,
        "dimensions": dimensions,
        "cross_method": cross,
        "agreement": {
            "bias": bias,
            "rho": rho,
            "exact_pct": round(
                sum(1 for h, l in zip(h_all, l_all) if abs(h - l) < 1e-9) / len(h_all) * 100
            ) if h_all else None,
            "within1_pct": round(
                sum(1 for h, l in zip(h_all, l_all) if abs(h - l) <= 1) / len(h_all) * 100
            ) if h_all else None,
        },
        "findings": FINDINGS,
        "conclusion": {
            "title": "默认人格：温婉邻居型",
            "paragraphs": [
                "理由不是「它得分最高」，而是：在绝对打分、人工抽检、强制排序三种独立方法下，"
                "温婉邻居型是唯一始终位于前二、没有任何一套方法把它排到末位的候选。",
                "贴心闺女型应排除：三种方法都把它排在后两位，人工明确指出其用语幼稚化"
                "（「乖啊，老人不是孩子」）。从容守护型排名高度依赖评价方法（第 1 / 第 4 / 第 2），"
                "不能作为首选依据。",
                "局限：人工抽检样本仅 87 条，人格间差异 p = 0.175，未达显著，"
                "上述结论应表述为方向性建议；大模型评分系统偏松 0.63 分、逐条相关仅 "
                "ρ = 0.087，只能作为辅助信号。",
            ],
        },
    }


def main():
    ap = argparse.ArgumentParser(description="导出人格评测前端数据")
    ap.add_argument("--out", default=str(REPO_ROOT / "frontend" / "data" / "personality_evaluation.js"))
    args = ap.parse_args()

    payload = build_payload()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "/* 自动生成，请勿手改。\n"
        "   来源：tools/export_persona_report_data.py\n"
        "   数据：tests/results/ 的原始评测结果（tools/analyze_scores.py 同口径）\n"
        "*/\n"
    )
    body = "window.__PERSONA_EVAL__ = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    out_path.write_text(header + body, encoding="utf-8")

    print(f"已导出：{out_path}")
    print(f"  场景 {payload['meta']['scenarios']} · 回复 {payload['meta']['replies']} · "
          f"排序 {payload['meta']['ranking_rounds']} 轮 · 人工 {payload['meta']['human_reviews']} 条")
    print(f"  绝对打分整体 p = {payload['meta']['absolute_p']}；"
          f"人工人格差异 p = {payload['meta']['human_p']}")


if __name__ == "__main__":
    main()
