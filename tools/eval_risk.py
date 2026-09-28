"""风险分级评测：在固定评测集上给分级器打分。

用法：
    # 本地关键词分级器（确定性，无需 API）
    python tools/eval_risk.py --mode local

    # 真实 LLM（走完整编排链路，需要 API Key）
    python tools/eval_risk.py --mode llm --api-key sk-xxx --model deepseek-flash

    # 低于阈值时退出码 1，可用于 CI
    python tools/eval_risk.py --mode local --min-accuracy 0.8
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.markers import infer_risk_local, infer_tags_local
from backend.app.evaluation import (
    check_thresholds,
    evaluate_risk,
    evaluate_tags,
    load_cases,
)

DEFAULT_CASES = REPO_ROOT / "backend" / "tests" / "eval" / "risk_cases.jsonl"
# 默认重点关注的高风险等级（分层门禁）
DEFAULT_RISK_THRESHOLDS = "R3:0.9,R2b:0.9"


def parse_risk_thresholds(raw):
    """解析 "R3:0.9,R2b:0.8" -> {"R3": 0.9, "R2b": 0.8}。"""
    result = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        risk, _, value = item.partition(":")
        result[risk.strip()] = float(value)
    return result


def local_predictor(text):
    return infer_risk_local(text)


def local_tags_predictor(text):
    return infer_tags_local(text)


def build_llm_predictor(api_key, base_url, model):
    from backend.app.config import Settings
    from backend.app.dialogue.orchestrator import DialogueOrchestrator

    overrides = {"api_key": api_key}
    if base_url:
        overrides["base_url"] = base_url
    if model:
        overrides["model"] = model
    orchestrator = DialogueOrchestrator(settings=Settings(**overrides))

    def predict(text):
        return orchestrator.respond(text)["risk"]

    def predict_tags(text):
        result = orchestrator.respond(text)
        return result["risk"], result.get("scenes", [])

    return predict, predict_tags


def main():
    parser = argparse.ArgumentParser(description="风险分级 + 场景类别评测（数据闭环）")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="评测集 JSONL 路径")
    parser.add_argument("--mode", choices=["local", "llm"], default="local")
    parser.add_argument("--min-accuracy", type=float, default=0.0, help="低于该准确率则退出码 1")
    parser.add_argument(
        "--min-risk-accuracy",
        default="",
        help='按等级的最小准确率，如 "R3:0.9,R2b:0.9"（缺样本也判失败）',
    )
    parser.add_argument("--min-scene-f1", type=float, default=0.0, help="场景多标签 F1 下限")
    parser.add_argument("--api-key", help="LLM 模式的 API Key")
    parser.add_argument("--base-url", help="LLM 接口地址")
    parser.add_argument("--model", help="模型名")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.mode == "llm":
        if not args.api_key:
            print("[错误] LLM 模式需要 --api-key")
            sys.exit(1)
        predictor, tags_predictor = build_llm_predictor(args.api_key, args.base_url, args.model)
    else:
        predictor, tags_predictor = local_predictor, local_tags_predictor

    metrics = evaluate_risk(predictor, cases)
    tag_metrics = evaluate_tags(tags_predictor, cases)
    output = {"risk": metrics, "tags": tag_metrics}
    print(json.dumps(output, ensure_ascii=False, indent=2))

    risk_thresholds = parse_risk_thresholds(args.min_risk_accuracy)
    failures = check_thresholds(
        metrics, args.min_accuracy, risk_thresholds
    )
    if tag_metrics["scene_f1"] < args.min_scene_f1:
        failures.append(
            f"场景 F1 {tag_metrics['scene_f1']:.2%} < {args.min_scene_f1:.0%}"
        )

    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        raise SystemExit(1)
    print(
        f"[PASS] 风险准确率 {metrics['accuracy']:.2%}；"
        f"场景 F1 {tag_metrics['scene_f1']:.2%}"
    )


if __name__ == "__main__":
    main()
