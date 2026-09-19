"""人格选型评测 · 跨方法不变量回归

读取已入库的原始评测数据（tests/results/ 与 examples/manual_review/），
断言 `docs/personality_evaluation_report.md` 的核心结论仍然成立：

  1. 温婉邻居型在三种方法下都位于前二；
  2. 贴心闺女型在三种方法下都位于后二。

这不是在重新做评测，而是把「结论」固化成回归基线：将来若替换原始数据、
改人格定义或重跑生成，一旦结论被推翻，CI 会立刻报错，而不是悄悄漂移。

纯标准库实现，不依赖网络与大模型，可在 CI 中确定性运行。
"""

import csv
import json
import statistics as st
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "tests" / "results"
EXAMPLES_DIR = REPO_ROOT / "skills" / "healthconsult-assistant-skill" / "examples"
KEY_PATH = EXAMPLES_DIR / "manual_review" / "manual_review_KEY_single_请勿提前打开.csv"
HUMAN_PATH = RESULTS_DIR / "human_review_result.csv"

PERSONAS = ["温婉邻居型", "贴心闺女型", "素朴家常型", "从容守护型"]
DIMENSIONS = ["可理解性", "情感温度", "实用可执行", "分寸感", "简洁度"]

ROBUST_TOP = "温婉邻居型"
ROBUST_BOTTOM = "贴心闺女型"


def _latest(pattern: str) -> Path:
    cands = sorted(RESULTS_DIR.glob(pattern))
    if not cands:
        pytest.skip(f"未找到原始数据 {pattern}，跳过（需先归位评测数据）")
    return cands[-1]


def _load_jsonl(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _rank_desc(means: dict) -> dict:
    """均分越高名次越靠前（1 = 最好）。"""
    order = sorted(means, key=means.get, reverse=True)
    return {p: i + 1 for i, p in enumerate(order)}


def _absolute_score_ranks() -> dict:
    rows = _load_jsonl(_latest("personality_scores_*.jsonl"))
    buckets = {p: [] for p in PERSONAS}
    for r in rows:
        if r.get("mean_score") is not None and r.get("personality") in buckets:
            buckets[r["personality"]].append(r["mean_score"])
    means = {p: st.mean(v) for p, v in buckets.items() if v}
    return _rank_desc(means)


def _forced_ranking_ranks() -> dict:
    rows = _load_jsonl(_latest("personality_ranking_*.jsonl"))
    ranks = {p: [] for p in PERSONAS}
    for r in rows:
        label_to_persona = r["label_to_persona"]
        for i, label in enumerate(r["ranking"], 1):
            p = label_to_persona.get(label)
            if p in ranks:
                ranks[p].append(i)
    means = {p: st.mean(v) for p, v in ranks.items() if v}
    # 平均名次越小越好，转成「越大越好」的分数再排
    return _rank_desc({p: -m for p, m in means.items()})


def _human_score_ranks() -> dict:
    if not HUMAN_PATH.exists() or not KEY_PATH.exists():
        pytest.skip("人工抽检数据缺失，跳过人工维度")
    with open(HUMAN_PATH, encoding="utf-8-sig") as f:
        human = {r["review_id"]: r for r in csv.DictReader(f)}
    with open(KEY_PATH, encoding="utf-8-sig") as f:
        key = {r["review_id"]: r for r in csv.DictReader(f)}

    buckets = {p: [] for p in PERSONAS}
    for rid, h in human.items():
        k = key.get(rid)
        if not k:
            continue
        vals = [int(h[d]) for d in DIMENSIONS if h.get(d) not in (None, "")]
        if vals and k.get("人格") in buckets:
            buckets[k["人格"]].append(st.mean(vals))
    means = {p: st.mean(v) for p, v in buckets.items() if v}
    return _rank_desc(means)


def _methods() -> dict:
    return {
        "绝对打分": _absolute_score_ranks(),
        "人工抽检": _human_score_ranks(),
        "强制排序": _forced_ranking_ranks(),
    }


def test_cross_method_invariants_hold():
    """温婉邻居型三法均前二；贴心闺女型三法均后二。"""
    methods = _methods()
    for name, ranks in methods.items():
        assert ranks, f"{name}：未计算出任何人格名次"
        assert ranks.get(ROBUST_TOP, 99) <= 2, (
            f"{name} 下 {ROBUST_TOP} 名次为 {ranks.get(ROBUST_TOP)}，不再位于前二"
        )
        assert ranks.get(ROBUST_BOTTOM, 0) >= 3, (
            f"{name} 下 {ROBUST_BOTTOM} 名次为 {ranks.get(ROBUST_BOTTOM)}，不再位于后二"
        )


def test_all_personas_ranked_in_every_method():
    """三种方法都必须覆盖四版人格，避免数据不完整导致结论失真。"""
    for name, ranks in _methods().items():
        assert set(ranks) == set(PERSONAS), f"{name} 未覆盖全部人格：{sorted(ranks)}"
