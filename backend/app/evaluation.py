"""离线评测：风险分级评测集与打分。

评测集每行：{"id": "...", "user": "...", "expected_risk": "R1"}

predictor 是一个可调用对象：predictor(user_text) -> risk 字符串。
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
