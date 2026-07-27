"""
生成人工抽查清单

按资料包建议的抽查比例：
- 急症、用药：100%
- 异常感知：50%
- 普通慢病/其他：20%

用法：
    python scripts/generate_manual_review_list.py \
        --input examples/v0.2.3_health_safety_repair.jsonl \
        --output examples/manual_review_list.csv
"""

import argparse
import csv
import json
import random
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent


def should_review(category, risk):
    """根据类别和风险决定是否需要人工抽查"""
    low_risk = risk in ("R0", "X", "M1")
    if category in ("急症高危", "用药边界"):
        return True
    if category in ("异常认知", "异常感知"):
        return random.random() < 0.5
    if low_risk:
        return random.random() < 0.2
    # 其他 R1/R2 类按 30% 抽查
    return random.random() < 0.3


def main():
    parser = argparse.ArgumentParser(description="生成人工抽查清单")
    parser.add_argument(
        "--input",
        default=str(PROJECT_DIR / "examples" / "v0.2.3_health_safety_repair.jsonl"),
        help="输入已清洗的候选数据",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_DIR / "examples" / "manual_review_list.csv"),
        help="输出抽查清单 CSV",
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子，保证可复现")
    args = parser.parse_args()

    random.seed(args.seed)

    in_path = Path(args.input)
    out_path = Path(args.output)

    rows = []
    with open(in_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            messages = row.get("messages", [])
            user = ""
            assistant = ""
            for m in messages:
                if m.get("role") == "user":
                    user = m.get("content", "")
                elif m.get("role") == "assistant":
                    assistant = m.get("content", "")

            risk = row.get("llm_risk", "")
            category = row.get("category", "")
            if should_review(category, risk):
                rows.append({
                    "sample_id": row.get("sample_id", ""),
                    "category": category,
                    "risk_level": risk,
                    "speaker_type": row.get("speaker_type", ""),
                    "user": user,
                    "assistant": assistant,
                    "review_result": "",
                    "reviewer_notes": "",
                })

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sample_id", "category", "risk_level", "speaker_type",
                "user", "assistant", "review_result", "reviewer_notes"
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"人工抽查清单已生成：{out_path}")
    print(f"共 {rows.__len__()} 条需要抽查（占总样本比例见输出）")


if __name__ == "__main__":
    main()
