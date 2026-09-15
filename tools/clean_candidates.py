"""
候选数据清洗脚本

把生成器产出的 candidates JSONL 转换为最终训练候选集：
1. 将风险/场景标签统一移到 assistant 回复末尾
2. 将 system prompt 替换为完整 SKILL.md
3. 用 generated_sft 严格模式重新校验
4. 仅保留无 fatal 的样本

用法：
    python tools/clean_candidates.py \
        --input skills/healthconsult-assistant-skill/examples/generated_candidates_20260719_174148.jsonl \
        --output skills/healthconsult-assistant-skill/examples/v0.2.3_health_safety_repair.jsonl
"""

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _paths import REPO_ROOT, SKILL_MD, EXAMPLES_DIR

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.taxonomy import (
    extract_tags,
    format_tags,
    strip_tags,
)
from backend.app.safety.safety_checker import validate_sample

SKILL_TEXT = SKILL_MD.read_text(encoding="utf-8")

SPEAKER_HINTS = {
    "family_caregiver": (
        "\n[注意：当前说话者是家属/照护者，在描述老人的情况。"
        "请称呼说话者为'您'，称呼老人为'老人家'。]"
    ),
    "family_member": (
        "\n[注意：当前说话者是家属，在描述老人的情况。"
        "请称呼说话者为'您'，称呼老人为'老人家'。]"
    ),
    "elder_self": (
        "\n[注意：当前说话者是老人本人。可根据语境酌情使用'您'或'阿姨/叔叔'。]"
    ),
}


def normalize_assistant(assistant_text, fallback_risk="?", fallback_scenes=None):
    """提取双维度标签并移到末尾；正文无标签时用 fallback 补，返回 (正文, 风险, 场景)。"""
    risk, scenes = extract_tags(assistant_text)
    reply = strip_tags(assistant_text)
    if not risk:
        risk = fallback_risk
    if not scenes:
        scenes = list(fallback_scenes or [])
    if not risk or risk == "?":
        return assistant_text, risk, scenes
    return reply + format_tags(risk, scenes), risk, scenes


def clean_row(row):
    messages = row.get("messages", [])
    user_msg = ""
    assistant_msg = ""
    for m in messages:
        if m.get("role") == "user":
            user_msg = m.get("content", "")
        elif m.get("role") == "assistant":
            assistant_msg = m.get("content", "")

    fallback_risk = row.get("risk_level") or row.get("llm_risk") or "?"
    fallback_scenes = row.get("scenes") or []
    assistant_clean, risk, scenes = normalize_assistant(
        assistant_msg, fallback_risk, fallback_scenes
    )
    speaker = row.get("speaker_type", "elder_self")
    system_content = SKILL_TEXT + SPEAKER_HINTS.get(speaker, "")

    new_row = {
        "sample_id": row.get("sample_id", ""),
        "category": row.get("category", ""),
        "speaker_type": speaker,
        "risk_level": risk,
        "scenes": scenes,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_clean},
        ],
    }
    return new_row


def main():
    parser = argparse.ArgumentParser(description="清洗小暖候选训练数据")
    parser.add_argument("--input", required=True, help="输入 candidates JSONL 路径")
    parser.add_argument(
        "--output",
        default=str(EXAMPLES_DIR / "v0.2.3_health_safety_repair.jsonl"),
        help="输出清洗后 JSONL 路径",
    )
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)

    total = 0
    kept = 0
    dropped = 0
    drop_reasons = []

    with open(in_path, "r", encoding="utf-8") as fin, open(
        out_path, "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            total += 1

            new_row = clean_row(row)
            _, violations = validate_sample(new_row, mode="generated_sft")
            fatal = [v for sev, v in violations if sev == "fatal"]

            if fatal:
                dropped += 1
                drop_reasons.append(
                    {"sample_id": new_row["sample_id"], "reasons": fatal}
                )
                continue

            fout.write(json.dumps(new_row, ensure_ascii=False) + "\n")
            kept += 1

    print(f"清洗完成：")
    print(f"  输入：{in_path}")
    print(f"  总数：{total}")
    print(f"  保留：{kept}")
    print(f"  剔除：{dropped}")
    print(f"  输出：{out_path}")

    if dropped:
        print("\n剔除样本及原因：")
        for item in drop_reasons:
            print(f"  {item['sample_id']}: {item['reasons']}")


if __name__ == "__main__":
    main()
