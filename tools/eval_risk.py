"""风险分级评测：在固定评测集上给分级器打分。

用法：
    # 本地关键词分级器（确定性，无需 API）
    python tools/eval_risk.py --mode local

    # 真实 LLM（走完整编排链路，需要 API Key）
    python tools/eval_risk.py --mode llm --api-key sk-xxx --model deepseek-chat

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

from backend.app.dialogue.markers import infer_risk_local
from backend.app.evaluation import evaluate_risk, load_cases

DEFAULT_CASES = REPO_ROOT / "backend" / "tests" / "eval" / "risk_cases.jsonl"


def local_predictor(text):
    return infer_risk_local(text)


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

    return predict


def main():
    parser = argparse.ArgumentParser(description="风险分级评测（数据闭环）")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="评测集 JSONL 路径")
    parser.add_argument("--mode", choices=["local", "llm"], default="local")
    parser.add_argument("--min-accuracy", type=float, default=0.0, help="低于该准确率则退出码 1")
    parser.add_argument("--api-key", help="LLM 模式的 API Key")
    parser.add_argument("--base-url", help="LLM 接口地址")
    parser.add_argument("--model", help="模型名")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.mode == "llm":
        if not args.api_key:
            print("[错误] LLM 模式需要 --api-key")
            sys.exit(1)
        predictor = build_llm_predictor(args.api_key, args.base_url, args.model)
    else:
        predictor = local_predictor

    metrics = evaluate_risk(predictor, cases)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    if metrics["accuracy"] < args.min_accuracy:
        print(f"[FAIL] 准确率 {metrics['accuracy']:.2%} 低于阈值 {args.min_accuracy:.0%}")
        raise SystemExit(1)
    print(f"[PASS] 准确率 {metrics['accuracy']:.2%}")


if __name__ == "__main__":
    main()
