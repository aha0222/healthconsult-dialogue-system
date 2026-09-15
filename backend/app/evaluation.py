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

    for case in cases:
        expected = case["expected_risk"]
        predicted = predictor(case["user"])
        confusion[(expected, predicted)] += 1
        if predicted == expected:
            correct += 1
        else:
            mismatches.append(
                {
                    "id": case.get("id"),
                    "user": case["user"],
                    "expected": expected,
                    "predicted": predicted,
                }
            )

    return {
        "total": total,
        "correct": correct,
        "accuracy": (correct / total) if total else 0.0,
        "mismatches": mismatches,
        "confusion": {
            f"{expected}->{predicted}": count
            for (expected, predicted), count in sorted(confusion.items())
        },
    }


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
