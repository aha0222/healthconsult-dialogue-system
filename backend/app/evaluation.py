"""离线评测：风险分级 + 场景类别评测集与打分。

评测集每行：
    {"id": "...", "user": "...", "expected_risk": "R1", "expected_scenes": ["S3"]}

predictor 是一个可调用对象：
    - evaluate_risk 期望 predictor(user_text) -> risk 字符串
    - evaluate_tags 期望 predictor(user_text) -> (risk, [scenes])
这样既能评测本地关键词分级器（确定性、可进 CI），也能评测真实 LLM。
"""

import json
from collections import Counter

from .dialogue.taxonomy import RISK_LEVELS


def load_cases(path):
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def evaluate_risk(predictor, cases):
    """对评测集打分，返回指标字典。"""
    total = len(cases)
    correct = 0
    mismatches = []
    confusion = Counter()
    per_risk_total = Counter()
    per_risk_correct = Counter()

    for case in cases:
        expected = case["expected_risk"]
        predicted = predictor(case["user"])
        confusion[(expected, predicted)] += 1
        per_risk_total[expected] += 1
        if predicted == expected:
            correct += 1
            per_risk_correct[expected] += 1
        else:
            mismatches.append(
                {
                    "id": case.get("id"),
                    "user": case["user"],
                    "expected": expected,
                    "predicted": predicted,
                }
            )

    per_risk = {}
    for risk in RISK_LEVELS:
        count = per_risk_total.get(risk, 0)
        hits = per_risk_correct.get(risk, 0)
        per_risk[risk] = {
            "total": count,
            "correct": hits,
            "accuracy": (hits / count) if count else 0.0,
        }

    return {
        "total": total,
        "correct": correct,
        "accuracy": (correct / total) if total else 0.0,
        "mismatches": mismatches,
        "confusion": {
            f"{expected}->{predicted}": count
            for (expected, predicted), count in sorted(confusion.items())
        },
        "per_risk": per_risk,
    }


def check_thresholds(metrics, min_accuracy=0.0, min_risk_accuracy=None):
    """分层门禁：整体准确率 + 指定风险等级准确率。

    min_risk_accuracy: {"R3": 0.9, "R2b": 0.9}，缺样本也会判为不达标。
    返回不达标说明列表；空列表表示通过。
    """
    failures = []
    if metrics.get("accuracy", 0.0) < min_accuracy:
        failures.append(
            f"整体准确率 {metrics.get('accuracy', 0.0):.2%} < {min_accuracy:.0%}"
        )

    per_risk = metrics.get("per_risk", {})
    for risk, threshold in (min_risk_accuracy or {}).items():
        stat = per_risk.get(risk) or {}
        count = stat.get("total", 0)
        if count == 0:
            failures.append(f"高风险等级 {risk} 无评测样本")
            continue
        accuracy = stat.get("accuracy", 0.0)
        if accuracy < threshold:
            failures.append(
                f"{risk} 准确率 {accuracy:.2%} < {threshold:.0%}（样本 {count}）"
            )
    return failures


def evaluate_tags(predictor, cases):
    """双维度评测：风险准确率 + 场景类别多标签 micro-F1。

    predictor(user_text) -> (risk, scenes)
    仅统计带 expected_scenes 的样本的场景指标。
    """
    total = len(cases)
    risk_correct = 0
    risk_mismatches = []

    tp = fp = fn = 0
    exact = 0
    scene_cases = 0
    scene_mismatches = []

    for case in cases:
        expected_risk = case.get("expected_risk", "")
        expected_scenes = set(case.get("expected_scenes") or [])
        predicted_risk, predicted_scenes = predictor(case["user"])
        predicted_scenes = set(predicted_scenes or [])

        if predicted_risk == expected_risk:
            risk_correct += 1
        else:
            risk_mismatches.append(
                {
                    "id": case.get("id"),
                    "expected": expected_risk,
                    "predicted": predicted_risk,
                }
            )

        if not expected_scenes:
            continue
        scene_cases += 1
        case_tp = len(predicted_scenes & expected_scenes)
        case_fp = len(predicted_scenes - expected_scenes)
        case_fn = len(expected_scenes - predicted_scenes)
        tp += case_tp
        fp += case_fp
        fn += case_fn
        if not case_fp and not case_fn:
            exact += 1
        else:
            scene_mismatches.append(
                {
                    "id": case.get("id"),
                    "expected": sorted(expected_scenes),
                    "predicted": sorted(predicted_scenes),
                }
            )

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "total": total,
        "risk_accuracy": (risk_correct / total) if total else 0.0,
        "risk_mismatches": risk_mismatches,
        "scene_cases": scene_cases,
        "scene_exact_match": (exact / scene_cases) if scene_cases else 0.0,
        "scene_precision": precision,
        "scene_recall": recall,
        "scene_f1": f1,
        "scene_mismatches": scene_mismatches,
    }
