"""场景 + 风险分级评测：快路径覆盖率、兜底率、准确率与 token 估算。

用法：
    # 只跑关键词快路径（确定性、可离线、可进 CI）
    python tools/eval_classifier.py --mode keyword

    # 完整链路（歧义时 LLM 兜底，需要 API Key）
    python tools/eval_classifier.py --mode auto

    # 强制 LLM 分类（对照基线）
    python tools/eval_classifier.py --mode llm

    # 低于阈值退出码 1
    python tools/eval_classifier.py --mode keyword --min-accuracy 0.8
"""

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.dialogue.classifier import (
    SceneRiskClassifier,
    build_classifier_prompt,
)
from backend.app.dialogue.taxonomy import canonical_risk, normalize_scenes
from backend.app.evaluation import load_cases
from backend.app.paths import SKILL_MD

DEFAULT_CASES = REPO_ROOT / "backend" / "tests" / "eval" / "risk_cases.jsonl"


def estimate_tokens(text: str) -> int:
    """粗略估算 token：中文约 1.5 字符/token。"""
    return int(len(text or "") / 1.5) + 1


def scene_scores(pairs):
    tp = fp = fn = 0
    for expected, predicted in pairs:
        expected, predicted = set(expected), set(predicted)
        tp += len(expected & predicted)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def run(args):
    settings = Settings.from_env()
    if args.corpus:
        settings.corpus_path = args.corpus
    if args.embedding_backend:
        settings.embedding_backend = args.embedding_backend
    classifier = SceneRiskClassifier(settings=settings)

    cases = load_cases(args.cases)
    allow_llm = args.mode != "keyword"
    force_llm = args.mode == "llm"

    total = len(cases)
    risk_correct = 0
    keyword_decided = 0
    fallback_used = 0
    mismatches = []
    scene_pairs = []
    fallback_prompt_tokens = 0
    latency_ms = 0.0

    for case in cases:
        user = case["user"]
        start = time.perf_counter()
        result = classifier.classify(user, allow_llm=allow_llm, force_llm=force_llm)
        latency_ms += (time.perf_counter() - start) * 1000

        expected_risk = canonical_risk(case.get("expected_risk", ""))
        expected_scenes = normalize_scenes(case.get("expected_scenes") or [])

        if result.source == "keyword" and not result.ambiguous:
            keyword_decided += 1
        if result.source == "llm":
            fallback_used += 1
            fallback_prompt_tokens += estimate_tokens(
                build_classifier_prompt(user, result.candidates)
            )

        if result.risk == expected_risk:
            risk_correct += 1
        else:
            mismatches.append(
                {
                    "id": case.get("id"),
                    "user": user,
                    "expected": expected_risk,
                    "predicted": result.risk,
                    "source": result.source,
                }
            )
        if expected_scenes:
            scene_pairs.append((expected_scenes, result.scenes))

    precision, recall, f1 = scene_scores(scene_pairs)
    skill_tokens = estimate_tokens(SKILL_MD.read_text(encoding="utf-8"))
    avg_latency = latency_ms / total if total else 0.0

    metrics = {
        "mode": args.mode,
        "total": total,
        "risk_accuracy": (risk_correct / total) if total else 0.0,
        "scene_precision": precision,
        "scene_recall": recall,
        "scene_f1": f1,
        "keyword_coverage": (keyword_decided / total) if total else 0.0,
        "fallback_rate": (fallback_used / total) if total else 0.0,
        "avg_latency_ms": round(avg_latency, 2),
        "est_fallback_prompt_tokens": fallback_prompt_tokens,
        "est_skill_md_tokens": skill_tokens,
        "mismatches": mismatches,
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    if metrics["risk_accuracy"] < args.min_accuracy:
        print(
            f"[FAIL] 风险准确率 {metrics['risk_accuracy']:.2%} 低于阈值 {args.min_accuracy:.0%}"
        )
        raise SystemExit(1)
    print(
        f"[PASS] 风险准确率 {metrics['risk_accuracy']:.2%}；"
        f"场景 F1 {f1:.2%}；快路径覆盖 {metrics['keyword_coverage']:.2%}；"
        f"兜底率 {metrics['fallback_rate']:.2%}"
    )


def main():
    parser = argparse.ArgumentParser(description="场景 + 风险分级评测")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="评测集 JSONL")
    parser.add_argument("--mode", choices=["keyword", "auto", "llm"], default="keyword")
    parser.add_argument("--min-accuracy", type=float, default=0.0)
    parser.add_argument("--corpus", help="语料 JSONL 路径")
    parser.add_argument("--embedding-backend", choices=["local", "api", "hash"])
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
