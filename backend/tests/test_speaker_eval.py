"""说话人识别评测：数据在 tests/eval/speaker_cases.jsonl（163 例角色类 + 12 例安全类）。

本文件是**验收门禁**，与 test_evaluation.py 的 ``test_local_predictor_meets_baseline``
同构：断言 163 例全部判对，并按「误切 / 漏判」两个方向分层盯，而不是只看总体
准确率——舒适区样本占题量 41% 且一贯全对，只看总分会被掩盖（旧评测集 67 例
"satisfies 85%" 却测不出任何问题，见 docs/speaker_eval_design.md 第 1 节）。

这些阈值在 2026-10-07 第 3 天随 D1/D2/D3 的修复一起提到验收目标
（同一提交，见设计文档第 8 节末）。修复前的基线是 23 例错判 / 85.9%，其中
**7 例是误切 family**——那 7 例正是本门禁存在的理由。
"""

import json
from pathlib import Path

from backend.app.dialogue import speaker

CASES = Path(__file__).resolve().parent / "eval" / "speaker_cases.jsonl"

# 验收目标：每一类都不许有错判。修复前的基线备查：
#   1引用转述 5、3双人对话 5、4方言口语 11、5历史延续 2（其余为 0）
ACCEPTANCE_WRONG_MAX = {
    "0基线": 0,
    "1引用转述": 0,
    "2a无称呼可判": 0,
    "2b无称呼模糊": 0,
    "3双人对话": 0,
    "4方言口语": 0,
    "5历史延续": 0,
}
ACCEPTANCE_OVER_FAMILY_MAX = 0  # 误切 family：唯一会伤害老人体验的方向，必须为 0
ACCEPTANCE_ACCURACY_MIN = 0.85  # 设计文档第 7 节写在纸上的那条整体门槛

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


def test_accuracy_meets_floor():
    """设计文档第 7 节写在纸上的整体门槛：163 例 ≥85%。"""
    accuracy, _, _, detail = score(load_role_cases())
    assert accuracy >= ACCEPTANCE_ACCURACY_MIN, detail


def test_no_category_has_wrong_cases():
    """分层门禁（严格的那条）：每一类都必须零错判。

    舒适区样本占题量 41% 且一贯全对，只看总分会被掩盖——这正是旧评测集
    「67 例 85% 达标」却测不出任何问题的原因（见设计文档第 1 节）。
    """
    _, by_cat, _, _ = score(load_role_cases())
    worse = {
        cat: (by_cat.get(cat, 0), cap)
        for cat, cap in ACCEPTANCE_WRONG_MAX.items()
        if by_cat.get(cat, 0) > cap
    }
    assert not worse, f"以下类别出现错判（错判数, 上限）：{worse}"


def test_over_family_direction_is_zero():
    """方向门禁：误切 family（老人被当家属）必须为 0。

    这个方向会改称呼与内容侧重（对老人说「您可以怎么照顾老人」），是唯一
    伤害体验的方向；漏判只是退回 SPEAKER_DETECT_ENABLED 关闭时的行为，不劣
    于现状。详见设计文档第 3 节。
    """
    _, _, over, detail = score(load_role_cases())
    over_detail = [
        d for d in detail
        if d[2] == speaker.SPEAKER_ELDER and d[3] == speaker.SPEAKER_FAMILY
    ]
    assert over <= ACCEPTANCE_OVER_FAMILY_MAX, f"误切 family {over} 例：{over_detail}"


def test_safety_cases_recorded_not_scored_here():
    """类 6 只记数据、不在此判分。

    它断言的是「角色不改变红线」，必须把同一文本分别以 speaker_role=elder /
    family 走 /api/chat 比对 risk_level 与兜底话术，函数层测不到。
    """
    safety = [row for row in load_rows() if not row.get("expected")]
    assert len(safety) == 12
    assert all(row.get("assert") for row in safety)
