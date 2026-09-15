"""evaluation.py 单元测试：评测集加载与风险分级打分。"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.markers import infer_risk_local, infer_tags_local
from backend.app.evaluation import evaluate_risk, evaluate_tags, load_cases

EVAL_CASES = Path(__file__).resolve().parent / "eval" / "risk_cases.jsonl"


def test_load_cases(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"id": "a", "user": "u", "expected_risk": "R0"}\n\n'
        '{"id": "b", "user": "v", "expected_risk": "R1"}\n',
        encoding="utf-8",
    )
    cases = load_cases(path)
    assert len(cases) == 2
    assert cases[1]["expected_risk"] == "R1"


def test_evaluate_risk_with_fake_predictor():
    cases = [
        {"id": "a", "user": "x", "expected_risk": "R0"},
        {"id": "b", "user": "y", "expected_risk": "R1"},
    ]
    predictor = lambda text: "R0"  # noqa: E731
    metrics = evaluate_risk(predictor, cases)
    assert metrics["total"] == 2
    assert metrics["correct"] == 1
    assert metrics["accuracy"] == 0.5
    assert len(metrics["mismatches"]) == 1
    assert metrics["confusion"]["R1->R0"] == 1


def test_local_predictor_meets_baseline():
    """本地兜底分级器在评测集上的准确率不应明显退化。"""
    cases = load_cases(EVAL_CASES)
    metrics = evaluate_risk(infer_risk_local, cases)
    assert metrics["total"] >= 15
    assert metrics["accuracy"] >= 0.8, metrics["mismatches"]


def test_evaluate_tags_metrics():
    cases = [
        {"id": "a", "user": "x", "expected_risk": "R1", "expected_scenes": ["S3"]},
        {"id": "b", "user": "y", "expected_risk": "R3", "expected_scenes": ["E1", "S1"]},
    ]

    def predictor(text):
        if text == "x":
            return "R1", ["S3"]
        return "R2b", ["E1"]

    metrics = evaluate_tags(predictor, cases)
    assert metrics["total"] == 2
    assert metrics["risk_accuracy"] == 0.5
    assert metrics["scene_cases"] == 2
    assert metrics["scene_precision"] == 1.0
    assert metrics["scene_recall"] == 2 / 3
    assert metrics["scene_exact_match"] == 0.5


def test_local_tags_predictor_scene_baseline():
    cases = load_cases(EVAL_CASES)
    metrics = evaluate_tags(infer_tags_local, cases)
    assert metrics["scene_cases"] >= 15
    assert metrics["scene_f1"] >= 0.5, metrics["scene_mismatches"]
