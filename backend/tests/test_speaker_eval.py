"""说话人识别评测：数据在 tests/eval/speaker_cases.jsonl（163 例角色类 + 12 例安全类）。

本文件是**基线护栏**，与 test_evaluation.py 的 ``test_local_predictor_meets_baseline``
同构：断言不劣于 2026-10-07 实测的基线，并按「误切 / 漏判」两个方向分层盯，
避免被 67 例舒适区样本（占题量 41%、当前 100% 全对）把退化掩盖掉。

**验收目标比这里高**：docs/speaker_eval_design.md 第 7 节要求 163 例 ≥85%
且**误切 family 率为 0**。当前基线未达后者（7 例误切，全在类 3 / 类 5），
第 3 天修复后须**在同一提交里把下面的阈值一起提高**——否则这道护栏会一直
停在「修好之前」的标准上。
"""

import json
from pathlib import Path

from backend.app.dialogue import speaker

CASES = Path(__file__).resolve().parent / "eval" / "speaker_cases.jsonl"

# 2026-10-07 基线（tools/speaker_eval_probe.py 输出）。只许变好，不许变差。
BASELINE_WRONG_MAX = {
    "0基线": 0,
    "1引用转述": 5,
    "2a无称呼可判": 0,
    "2b无称呼模糊": 0,
    "3双人对话": 5,
    "4方言口语": 11,
    "5历史延续": 2,
}
BASELINE_OVER_FAMILY_MAX = 7  # 误切 family：唯一会伤害老人体验的方向，验收目标是 0
BASELINE_ACCURACY_MIN = 0.85  # 总体 85.9%

EXPECTED_CATEGORY_COUNTS = {
    "0基线": 67,
    "1引用转述": 24,
    "2a无称呼可判": 12,
    "2b无称呼模糊": 12,
    "3双人对话": 16,
    "4方言口语": 20,
    "5历史延续": 12,
}


def load_rows():
    with open(CASES, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_role_cases():
    """角色类样例；安全类行（expected 为空）不在此测。"""
    return [row for row in load_rows() if row.get("expected")]


def score(cases):
    """返回 (准确率, 分类别错判数, 误切 family 数, 错判明细)。"""
    by_cat, wrong, over, detail = {}, 0, 0, []
    for item in cases:
        got = speaker.detect_speaker_role(item["text"], history=item.get("history"))
        expected = item["expected"]
        if got == expected:
            continue
        wrong += 1
        by_cat[item["category"]] = by_cat.get(item["category"], 0) + 1
        detail.append((item["category"], item["text"], expected, got))
        if expected == speaker.SPEAKER_ELDER and got == speaker.SPEAKER_FAMILY:
            over += 1
    return (len(cases) - wrong) / len(cases), by_cat, over, detail


def test_dataset_shape_matches_design():
    """题量与分布锁在设计文档第 2 节：改样例就得同步改文档。"""
    rows = load_rows()
    ids = [row["id"] for row in rows]
    assert len(set(ids)) == len(ids), "id 重复（曾因 2a/2b 共用前缀撞过）"
    cases = [row for row in rows if row.get("expected")]
    by_cat = {}
    for item in cases:
        by_cat[item["category"]] = by_cat.get(item["category"], 0) + 1
    assert by_cat == EXPECTED_CATEGORY_COUNTS
    assert all(item["expected"] in speaker.VALID_SPEAKER_ROLES for item in cases)


def test_accuracy_does_not_regress():
    """总体准确率不低于 85% 门槛。"""
    accuracy, _, _, detail = score(load_role_cases())
    assert accuracy >= BASELINE_ACCURACY_MIN, detail


def test_no_category_regresses():
    """分层门禁：每一类都不许比基线更差。

    舒适区样本占题量 41% 且全对，只看总分会被掩盖——这正是旧评测集
    「67 例 85% 达标」却测不出问题的原因（见设计文档第 1 节）。
    """
    _, by_cat, _, _ = score(load_role_cases())
    worse = {
        cat: (by_cat.get(cat, 0), cap)
        for cat, cap in BASELINE_WRONG_MAX.items()
        if by_cat.get(cat, 0) > cap
    }
    assert not worse, f"以下类别比基线更差（错判数, 基线上限）：{worse}"


def test_over_family_direction_does_not_regress():
    """方向门禁：误切 family（老人被当家属）的条数不许增加。

    这个方向会改称呼与内容侧重（对老人说「您可以怎么照顾老人」），
    是唯一伤害体验的方向；漏判只是退回 SPEAKER_DETECT_ENABLED 关闭时的
    行为，不劣于现状。详见设计文档第 3 节。验收目标是 0 例。
    """
    _, _, over, detail = score(load_role_cases())
    over_detail = [
        d for d in detail
        if d[2] == speaker.SPEAKER_ELDER and d[3] == speaker.SPEAKER_FAMILY
    ]
    assert over <= BASELINE_OVER_FAMILY_MAX, f"误切 family {over} 例：{over_detail}"


def test_safety_cases_recorded_not_scored_here():
    """类 6 只记数据、不在此判分。

    它断言的是「角色不改变红线」，必须把同一文本分别以 speaker_role=elder /
    family 走 /api/chat 比对 risk_level 与兜底话术，函数层测不到。
    """
    safety = [row for row in load_rows() if not row.get("expected")]
    assert len(safety) == 12
    assert all(row.get("assert") for row in safety)
