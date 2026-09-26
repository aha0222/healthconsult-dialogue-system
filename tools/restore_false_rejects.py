"""恢复被 validate_sample 误剔除的语料

── 问题 ────────────────────────────────────────────────────────
`validate_sample` 用的是「声明场景 ∪ **检测**场景」逻辑：检测出的场景同样会触发
该场景的必需动作检查。而 `detect_scenes` 是纯关键词匹配，于是：

  · 「前阵子摔了一跤，现在腿脚不利索，想问问怎么吃」（L1 饮食营养）
    → 检测到「摔」→ 判为 E1 急症 → 要求回复里必须有 120 → 误剔
  · 「我妈独居，接到电话说买了假药要被罚款」（N3 诈骗财产）
    → 检测到「药」→ 判为 S2 用药 → 要求提医生/药师 → 误剔

这类误判每次语料生成都会稳定丢掉约 2% 的合格数据。

── 本脚本的判定标准 ─────────────────────────────────────────────
只有当「必需动作缺失」针对的**场景不在该条声明的 scenes 里**时，才认定为误判并恢复：
  必需动作原因 → 对应场景
  emergency_scene_missing_escalation              → E1
  environment_scene_missing_emergency_response    → N2
  safety_scene_missing_emergency_response         → N1
  fraud_scene_missing_response                    → N3
  mental_crisis_scene_missing_professional_guidance → M2
  medication_missing_doctor_or_pharmacist_confirmation → S2

如果场景**在**声明里，说明标签本身就要求了那个动作，属于真实缺失，不予恢复。

用法：
    python tools/restore_false_rejects.py \
        --candidates merged_fixed.jsonl \
        --cleaned v0.3.0_corpus500.jsonl \
        --out v0.3.0_corpus500.jsonl
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import corpus_common as cc  # noqa: E402

sys.path.insert(0, str(cc.REPO_ROOT))

from backend.app.safety.safety_checker import validate_sample  # noqa: E402
from tools.clean_candidates import clean_row  # noqa: E402  (复用同一套定型逻辑)

# 必需动作原因 → 触发它的场景
REASON_TO_SCENE = {
    "emergency_scene_missing_escalation": "E1",
    "environment_scene_missing_emergency_response": "N2",
    "safety_scene_missing_emergency_response": "N1",
    "fraud_scene_missing_response": "N3",
    "mental_crisis_scene_missing_professional_guidance": "M2",
    "medication_missing_doctor_or_pharmacist_confirmation": "S2",
}

# 非「必需动作」类的 fatal，即使场景不匹配也不恢复（这些是真问题）
NON_ACTION_REASONS = {
    "duplicate_id", "empty_content", "missing_id", "json_parse",
    "missing_scene_marker", "stray_scene_marker", "too_many_scene_tags",
    "invalid_risk_tag", "missing_scene_tag", "placeholder_hit",
    "prompt_leak", "reasoning_leak", "assistant_role_mismatch",
}


def main():
    ap = argparse.ArgumentParser(description="恢复被误剔除的语料")
    ap.add_argument("--candidates", required=True, help="原始候选（已定型前的）")
    ap.add_argument("--cleaned", required=True, help="clean_candidates 的输出")
    ap.add_argument("--out", required=True, help="输出路径（可与 --cleaned 相同）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = cc.load_jsonl(Path(args.candidates))
    kept = cc.load_jsonl(Path(args.cleaned))
    kept_ids = {r.get("sample_id") for r in kept}
    dropped = [r for r in src if r.get("sample_id") not in kept_ids]

    print("=" * 74)
    print(f"  恢复误剔除语料")
    print(f"  候选 {len(src)}  已保留 {len(kept)}  待检 {len(dropped)}")
    print("=" * 74)

    restored, refused = [], []
    for r in dropped:
        new_row = clean_row(r)
        _sid, violations = validate_sample(new_row, mode="generated_sft")
        fatal = [v for sev, v in violations if sev == "fatal"]
        declared = set(new_row.get("scenes") or [])

        reasons_bad = [v for v in fatal if v in NON_ACTION_REASONS]
        action_reasons = [v for v in fatal if v in REASON_TO_SCENE]

        if reasons_bad or not action_reasons:
            refused.append((new_row["sample_id"], fatal, "含非必需动作类问题"))
            continue

        # 全部必需动作原因都指向未声明的场景 → 误判
        false_pos = all(REASON_TO_SCENE[v] not in declared for v in action_reasons)
        if false_pos:
            restored.append((new_row, action_reasons, sorted(declared)))
        else:
            refused.append((new_row["sample_id"], fatal,
                            f"场景已声明 {'/'.join(sorted(declared))}，属真实缺失"))

    print(f"\n判定为误判可恢复：{len(restored)} 条")
    for row, reasons, declared in restored:
        trigger = "、".join(f"{r}→{REASON_TO_SCENE[r]}" for r in reasons)
        print(f"  {row['sample_id']}")
        print(f"    声明 {declared}，触发原因 {trigger}")

    if refused:
        print(f"\n不予恢复：{len(refused)} 条（确有真实问题）")
        for sid, fatal, why in refused:
            print(f"  {sid}: {fatal}  —— {why}")

    if args.dry_run:
        print("\n[dry-run] 未写入")
        return

    merged = kept + [row for row, _r, _d in restored]
    merged.sort(key=lambda r: r.get("sample_id", ""))
    cc.write_jsonl(Path(args.out), merged)
    print(f"\n输出 {len(merged)} 条 → {args.out}")


if __name__ == "__main__":
    main()
