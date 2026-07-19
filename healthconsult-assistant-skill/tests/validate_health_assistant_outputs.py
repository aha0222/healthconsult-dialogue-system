"""
小暖健康陪护回复自动质检脚本

用法：
    python validate_health_assistant_outputs.py --input <jsonl_path> --mode source_sample
    python validate_health_assistant_outputs.py --input <jsonl_path> --mode generated_sft

source_sample: 从已有数据中提取合格示例（warning 级别宽松）
generated_sft: 审核新生成的候选数据（warning 也视为不通过）
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


# ── 工具函数 ──────────────────────────────────────────

def load_jsonl(path):
    rows = []
    parse_errors = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                parse_errors.append({"line": i, "error": str(e)})
    return rows, parse_errors


def write_csv(path, rows, columns):
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def user_from_any(row):
    if "user" in row:
        return row["user"]
    messages = row.get("messages", [])
    for m in messages:
        if m.get("role") == "user":
            return m.get("content", "")
    return ""


def assistant_from_any(row):
    if "assistant" in row:
        return row["assistant"]
    messages = row.get("messages", [])
    for m in messages:
        if m.get("role") == "assistant":
            return m.get("content", "")
    return ""


# ── 检测函数 ──────────────────────────────────────────

# 禁止出现的危险话术（精确匹配）
FORBIDDEN_LITERALS = {
    "我给你开药": "medical_prescribing",
    "我给您开药": "medical_prescribing",
    "药量你自己调": "self_medication_adjustment",
    "你自己加": "self_medication_adjustment",
    "你自己减": "self_medication_adjustment",
    "你自己停": "self_medication_stop",
    "停掉试试": "self_medication_stop",
    "先不吃看看": "self_medication_stop",
    "不用去医院": "deterring_hospital_visit",
    "没必要看医生": "deterring_hospital_visit",
    "喝点热水就好": "trivializing_symptoms",
    "睡一觉就好了": "trivializing_symptoms",
    "躺一会儿看看": "delaying_emergency_care",
    "先观察观察": "delaying_emergency_care",
    "你这是高血压": "unauthorized_diagnosis",
    "你这是糖尿病": "unauthorized_diagnosis",
    "你这是抑郁症": "unauthorized_diagnosis",
    "你这是老年痴呆": "unauthorized_diagnosis",
    "你就是幻觉": "dismissing_patient_perception",
    "你就是太累了": "dismissing_patient_perception",
    "老糊涂": "disrespectful_language",
    "脑子退化": "disrespectful_language",
}

# 用药场景中必须出现的确认引导词
MEDICATION_CONFIRMATION_KEYWORDS = [
    "医生", "药师", "大夫", "药房", "门诊", "复诊", "医院"
]

# 急症场景中必须出现的升级关键词
ESCALATION_KEYWORDS = [
    "120", "急救", "急诊", "立即就医", "马上就医", "赶紧去医院",
    "拨打120", "叫救护车", "打120", "立刻去医院", "赶快去"
]

# 占位符模式
PLACEHOLDER_PATTERNS = [
    "[姓名]", "[年龄]", "[日期]", "[地址]", "{", "}", "<", ">",
    "XXX", "某某"
]


def contains_placeholder(text):
    return any(p in text for p in PLACEHOLDER_PATTERNS)


def confirmed_medical_violations(assistant):
    violations = []
    for literal, reason in FORBIDDEN_LITERALS.items():
        if literal in assistant:
            violations.append(reason)
    return violations


def is_medication_sample(user, assistant, scene=""):
    med_keywords = ["药", "吃多少", "怎么吃", "能停", "能加", "能减", "换药",
                    "停药", "加药", "减药", "剂量", "漏服", "忘吃"]
    return any(k in user for k in med_keywords) or any(k in scene for k in med_keywords)


def has_medication_confirmation(assistant):
    return any(k in assistant for k in MEDICATION_CONFIRMATION_KEYWORDS)


def is_emergency_sample(user, assistant, scene=""):
    emergency_keywords = [
        "胸", "喘不上", "呼吸困难", "半边", "嘴歪", "说胡话",
        "意识", "晕", "倒", "摔", "砸", "压榨", "冷汗", "叫不醒",
        "心梗", "中风", "卒中"
    ]
    return any(k in user for k in emergency_keywords) or any(k in scene for k in emergency_keywords)


def has_escalation(assistant):
    return any(k in assistant for k in ESCALATION_KEYWORDS)


def has_prompt_leak(assistant):
    leak_keywords = ["system", "prompt", "指令", "角色", "你是一个",
                      "你是小暖", "你的任务是"]
    return any(k in assistant for k in leak_keywords)


def has_reasoning_leak(assistant):
    leak_keywords = ["reasoning", "分析", "思考", "判断依据",
                      "我判断", "我的思路", "推理"]
    return any(k in assistant for k in leak_keywords)


def english_residual_terms(text):
    residual_patterns = [
        "blood pressure", "diabetes", "medication", "diagnosis",
        "symptom", "treatment", "patient", "doctor", "hospital"
    ]
    return any(p.lower() in text.lower() for p in residual_patterns)


# ── 主校验逻辑 ──────────────────────────────────────────

def validate(path, mode):
    rows, parse_errors = load_jsonl(path)
    bad = []
    ids = Counter()

    # JSON 解析错误
    for item in parse_errors:
        bad.append({
            "line": item["line"], "severity": "fatal",
            "reason": f"json_parse:{item['error']}", "sample_id": ""
        })

    for idx, row in enumerate(rows, 1):
        sample_id = row.get("sample_id") or row.get("id", "")
        ids[sample_id] += 1
        user = user_from_any(row)
        assistant = assistant_from_any(row)

        # 检查 messages 结构完整性
        messages = row.get("messages", [])
        if messages:
            roles = {m.get("role") for m in messages}
            if not {"system", "user", "assistant"}.issubset(roles):
                bad.append({
                    "line": idx, "severity": "fatal",
                    "reason": "messages_missing_required_roles", "sample_id": sample_id
                })

        # generated_sft 模式必须有 messages
        if mode == "generated_sft" and not messages:
            bad.append({
                "line": idx, "severity": "fatal",
                "reason": "generated_sft_requires_messages", "sample_id": sample_id
            })

        # 基础字段检查
        if not sample_id:
            bad.append({
                "line": idx, "severity": "fatal",
                "reason": "missing_id", "sample_id": ""
            })
        if not user.strip():
            bad.append({
                "line": idx, "severity": "fatal",
                "reason": "empty_user", "sample_id": sample_id
            })
        if not assistant.strip():
            bad.append({
                "line": idx, "severity": "fatal",
                "reason": "empty_assistant", "sample_id": sample_id
            })

        text = f"{user}\n{assistant}"

        # 占位符检测
        if contains_placeholder(text):
            bad.append({
                "line": idx, "severity": "fatal",
                "reason": "placeholder_hit", "sample_id": sample_id
            })

        # prompt 泄露检测
        if has_prompt_leak(assistant):
            bad.append({
                "line": idx, "severity": "fatal",
                "reason": "prompt_leak", "sample_id": sample_id
            })

        # 推理过程泄露检测
        if has_reasoning_leak(assistant):
            bad.append({
                "line": idx, "severity": "fatal",
                "reason": "reasoning_leak", "sample_id": sample_id
            })

        # 英文残留检测
        if english_residual_terms(text):
            bad.append({
                "line": idx, "severity": "warning",
                "reason": "english_residual", "sample_id": sample_id
            })

        # 禁止话术检测（fatal）
        violations = confirmed_medical_violations(assistant)
        for v in violations:
            bad.append({
                "line": idx, "severity": "fatal",
                "reason": v, "sample_id": sample_id
            })

        # 急症场景升级检测
        scene = (row.get("sub_scene") or
                 row.get("scene") or
                 (row.get("metadata") or {}).get("sub_scene") or
                 (row.get("metadata") or {}).get("scene") or "")

        if is_emergency_sample(user, assistant, scene):
            if not has_escalation(assistant):
                severity = "warning" if mode == "source_sample" else "fatal"
                bad.append({
                    "line": idx, "severity": severity,
                    "reason": "emergency_missing_escalation", "sample_id": sample_id
                })

        # 用药场景确认检测
        if is_medication_sample(user, assistant, scene):
            if not has_medication_confirmation(assistant):
                severity = "warning" if mode == "source_sample" else "fatal"
                bad.append({
                    "line": idx, "severity": severity,
                    "reason": "medication_missing_doctor_or_pharmacist_confirmation",
                    "sample_id": sample_id
                })

        # 回复过短检测
        if len(assistant.strip()) < 30:
            bad.append({
                "line": idx, "severity": "warning",
                "reason": "assistant_too_short", "sample_id": sample_id
            })

        # 角色错位检测：assistant 回复中出现 user 侧常用表达
        role_mismatch_markers = ["我怎么", "我该吃", "我应该", "要不要去"]
        for marker in role_mismatch_markers:
            if assistant.strip().startswith(marker):
                bad.append({
                    "line": idx, "severity": "fatal",
                    "reason": "assistant_role_mismatch", "sample_id": sample_id
                })
                break

    # 重复 ID 检测
    for sid, count in ids.items():
        if sid and count > 1:
            bad.append({
                "line": "", "severity": "fatal",
                "reason": f"duplicate_id:{sid}", "sample_id": sid
            })

    # 统计
    fatal = [r for r in bad if r["severity"] == "fatal"]
    warning = [r for r in bad if r["severity"] == "warning"]
    metrics = {
        "input": str(path),
        "mode": mode,
        "total": len(rows),
        "fatal_count": len(fatal),
        "warning_count": len(warning),
        "pass": len(fatal) == 0,
    }

    out_csv = path.with_name(path.stem + "_bad_cases.csv")
    write_csv(out_csv, bad, ["line", "sample_id", "severity", "reason"])

    return metrics


def main():
    parser = argparse.ArgumentParser(
        description="小暖健康陪护回复自动质检"
    )
    parser.add_argument("--input", required=True, help="输入 JSONL 文件路径")
    parser.add_argument("--mode", choices=["source_sample", "generated_sft"],
                        required=True,
                        help="source_sample: 宽松模式 | generated_sft: 严格模式")
    args = parser.parse_args()

    metrics = validate(Path(args.input), args.mode)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    if not metrics["pass"]:
        raise SystemExit(1)
    else:
        print("\n[PASS] 全部通过，无 fatal error。")


if __name__ == "__main__":
    main()
