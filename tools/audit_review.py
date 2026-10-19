# -*- coding: utf-8 -*-
"""审计对话复盘：把测试/试用对话变成可审的改进清单。

对应“测试对话 → 改进 SKILL / 补语料”的日常闭环。边界是刻意的：

    collect  只读 audit_log，按信号排优先级，产出待审队列（不修改任何东西）
    apply    只处理人工填过 decision 的队列；输出语料候选 / 负例 / SKILL 建议
             **不修改 SKILL.md、safety_checker.py、正式语料库**

为什么需要人工这一步：赞/踩、打断、重复追问都只是线索，不是“正确答案”。
自动把线索写进红线或语料，会把误判固化成规则。所有吸收都要过回归门禁。

用法
----
    # 1) 从审计库生成复盘队列（默认输出到 review/audit_review/<时间>/）
    python tools\\audit_review.py collect
    python tools\\audit_review.py collect --db backend/data/sessions.db --limit 50

    # 2) 人工编辑 queue.jsonl：把 decision 改成 accept/edit/reject，
    #    填 destination（skill / negative / corpus，可多选逗号分隔）和 notes。
    #    必要时补 expected_risk / expected_scenes / corrected_reply。

    # 3) 吸收已审核条目（默认只写 review/audit_review/ 下的候选文件）
    python tools\\audit_review.py apply --queue review/audit_review/<时间>/queue.jsonl

产出
----
    queue.jsonl             待审队列（含信号、优先级、建议去向）
    report.md               人读版汇总
    summary.json            机器可读统计
    corpus_candidates.jsonl  apply 后的语料候选（追加到候选文件，不动正式库）
    negative_cases.jsonl    apply 后的负例候选
    skill_suggestions.md    apply 后的 SKILL 建议（只写建议，不改 SKILL.md）
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.app.dialogue.markers import infer_tags_local  # noqa: E402
from backend.app.dialogue.speaker import (  # noqa: E402
    SPEAKER_FAMILY,
    detect_speaker_role,
)
from backend.app.dialogue.taxonomy import RISK_ORDER, SCENE_LABELS  # noqa: E402

DEFAULT_DB = REPO / "backend" / "data" / "sessions.db"
DEFAULT_OUT = REPO / "review" / "audit_review"
PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2}

# (code, weight, 人读说明)
SIGNAL_LABELS = {
    "negative_feedback": "用户点踩",
    "positive_feedback": "用户点赞",
    "feedback_note": "用户留了反馈备注",
    "repeated_question": "用户重复追问同一件事",
    "interrupted": "播报被打断",
    "violation": "安全层报违规",
    "fallback": "回复被兜底话术替换",
    "risk_under": "模型定级低于关键词兜底",
    "risk_over": "模型定级高于关键词兜底",
    "no_scenes": "没有场景标签",
    "empty_reply": "回复为空或过短",
    "latency_outlier": "延迟显著偏高",
}


def _rank(value) -> int:
    return RISK_ORDER.get(str(value), -1)


def _load_json(value, default):
    try:
        parsed = json.loads(value) if isinstance(value, str) and value.strip() else value
        return parsed if parsed is not None else default
    except (TypeError, ValueError):
        return default


def load_audit_rows(db_path: Path) -> list[dict]:
    """读 audit_log；只取复盘需要的列，缺列时给出可诊断的错误。"""
    if not db_path.is_file():
        raise SystemExit(f"审计库不存在：{db_path}")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM audit_log ORDER BY id DESC")]
    except sqlite3.Error as exc:
        raise SystemExit(f"读取 audit_log 失败：{exc}") from exc
    finally:
        con.close()
    return rows


def _normalize_row(row: dict, median_latency: float) -> dict:
    scenes = _load_json(row.get("scenes"), [])
    violations = _load_json(row.get("violations"), [])
    if not isinstance(scenes, list):
        scenes = []
    if not isinstance(violations, list):
        violations = [str(violations)] if violations else []
    return {
        **row,
        "scenes": [str(s) for s in scenes],
        "violations": violations,
        "feedback": int(row.get("feedback") or 0),
        "interrupted": bool(row.get("interrupted")),
        "repeated_question": bool(row.get("repeated_question")),
        "fallback_used": bool(row.get("fallback_used")),
        "median_latency": median_latency,
    }


def build_review_rows(rows: list[dict], include_positive: bool = True) -> list[dict]:
    """按信号打分并排序。返回待审记录（不改数据库）。"""
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms")]
    median_latency = statistics.median(latencies) if latencies else 0
    normalized = [_normalize_row(r, median_latency) for r in rows]

    out = []
    for row in normalized:
        reasons = []

        def add(code: str, weight: int, detail: str = ""):
            reasons.append({"code": code, "weight": weight, "detail": detail})

        if row["feedback"] == -1:
            add("negative_feedback", 5, row.get("feedback_note") or "")
        elif row["feedback"] == 1:
            if not include_positive:
                continue
            add("positive_feedback", 1, row.get("feedback_note") or "")
        if row.get("feedback_note") and row["feedback"] == 0:
            add("feedback_note", 2, str(row["feedback_note"]))
        if row["repeated_question"]:
            add("repeated_question", 3)
        if row["interrupted"]:
            add("interrupted", 2)
        if row["violations"]:
            add("violation", 5, json.dumps(row["violations"], ensure_ascii=False)[:160])
        if row["fallback_used"]:
            add("fallback", 3)
        if not row["scenes"]:
            add("no_scenes", 2)
        if not (row.get("reply") or "").strip() or len((row.get("reply") or "").strip()) < 10:
            add("empty_reply", 3)

        user = row.get("user_message") or ""
        kw_risk, kw_scenes = infer_tags_local(user)
        model_risk = row.get("risk")
        if _rank(model_risk) < _rank(kw_risk):
            add("risk_under", 4, f"记录={model_risk} 兜底={kw_risk}")
        elif _rank(model_risk) > _rank(kw_risk):
            add("risk_over", 1, f"记录={model_risk} 兜底={kw_risk}")

        if median_latency and row.get("latency_ms") and row["latency_ms"] > median_latency * 3:
            add("latency_outlier", 1, f"{row['latency_ms']}ms > 3×中位")

        if not reasons:
            continue

        score = sum(r["weight"] for r in reasons)
        priority = "high" if score >= 7 else ("medium" if score >= 4 else "low")
        codes = {r["code"] for r in reasons}
        suggested = []
        if codes & {"negative_feedback", "repeated_question", "interrupted", "fallback", "empty_reply", "no_scenes"}:
            suggested.append("skill")
        if codes & {"negative_feedback", "violation", "risk_under", "fallback", "empty_reply"}:
            suggested.append("negative")
        if "positive_feedback" in codes or not (codes & {"violation", "risk_under", "negative_feedback"}):
            suggested.append("corpus")

        out.append({
            "audit_id": row["id"],
            "priority": priority,
            "score": score,
            "reasons": reasons,
            "suggested": suggested,
            "decision": "pending",
            "destination": "",
            "reviewer": "",
            "notes": "",
            "expected_risk": None,
            "expected_scenes": [],
            "corrected_reply": None,
            "user": user,
            "reply": row.get("reply") or "",
            "risk": model_risk,
            "scenes": row["scenes"],
            "keyword_risk": kw_risk,
            "keyword_scenes": kw_scenes,
            "feedback": row["feedback"],
            "feedback_note": row.get("feedback_note"),
            "interrupted": row["interrupted"],
            "repeated_question": row["repeated_question"],
            "fallback_used": row["fallback_used"],
            "llm_backend": row.get("llm_backend"),
            "created_at": row.get("created_at"),
        })

    out.sort(key=lambda r: (-PRIORITY_RANK[r["priority"]], -r["score"], -int(r["audit_id"])))
    return out


def _write_report(rows: list[dict], out_dir: Path, source: str) -> dict:
    by_priority = Counter(r["priority"] for r in rows)
    by_reason = Counter(reason["code"] for r in rows for reason in r["reasons"])
    suggested = Counter(dest for r in rows for dest in r["suggested"])

    lines = [
        "# 审计对话复盘队列",
        "",
        f"- 数据源：`{source}`",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 待审条数：**{len(rows)}**",
        f"- 优先级：high {by_priority.get('high', 0)} / medium {by_priority.get('medium', 0)} / low {by_priority.get('low', 0)}",
        "",
        "## 怎么审",
        "",
        "编辑同目录的 `queue.jsonl`：",
        "",
        "- `decision`：`accept` / `edit` / `reject` / `pending`",
        "- `destination`：`skill` / `negative` / `corpus`，可逗号组合",
        "- `notes`：为什么这样改",
        "- 需要时补 `expected_risk`、`expected_scenes`、`corrected_reply`",
        "",
        "然后运行：",
        "",
        "```powershell",
        f"python tools\\audit_review.py apply --queue {out_dir / 'queue.jsonl'}",
        "```",
        "",
        "`apply` 只写候选文件，不改 SKILL.md、安全规则或正式语料库。",
        "",
        "## 信号分布",
        "",
        "| 信号 | 条数 |",
        "|---|---:|",
    ]
    for code, count in by_reason.most_common():
        lines.append(f"| {SIGNAL_LABELS.get(code, code)} | {count} |")
    lines += ["", "## 建议去向", "", "| 去向 | 条数 |", "|---|---:|"]
    for dest, count in suggested.most_common():
        lines.append(f"| {dest} | {count} |")

    lines += [
        "",
        "## 队列（按优先级）",
        "",
        "| id | 优先级 | 分数 | 信号 | 用户原话 |",
        "|---:|---|---:|---|---|",
    ]
    for r in rows:
        text = (r["user"] or "").replace("|", "／").replace("\n", " ")[:72]
        codes = "、".join(SIGNAL_LABELS.get(x["code"], x["code"]) for x in r["reasons"])
        lines.append(f"| {r['audit_id']} | {r['priority']} | {r['score']} | {codes} | {text} |")

    lines += [
        "",
        "> 隐私：审计对话可能含真实用户信息。`review/audit_review/` 已加入 .gitignore，",
        "> 不要直接提交或外发；需要共享时先脱敏。",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    summary = {
        "source": source,
        "total": len(rows),
        "by_priority": dict(by_priority),
        "by_reason": dict(by_reason),
        "suggested": dict(suggested),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def collect(db_path: Path, out_dir: Path, include_positive: bool, limit: int, min_priority: str) -> list[dict]:
    rows = build_review_rows(load_audit_rows(db_path), include_positive=include_positive)
    threshold = PRIORITY_RANK[min_priority]
    rows = [r for r in rows if PRIORITY_RANK[r["priority"]] >= threshold]
    if limit:
        rows = rows[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "queue.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = _write_report(rows, out_dir, str(db_path))
    print("=" * 78)
    print("  审计对话复盘队列")
    print("=" * 78)
    print(f"  数据源 : {db_path}")
    print(f"  待审   : {len(rows)} 条")
    print(f"  优先级 : {summary['by_priority']}")
    print(f"  输出   : {out_dir}")
    print(f"      queue.jsonl / report.md / summary.json")
    print()
    print("  下一步：编辑 queue.jsonl 的 decision/destination，再运行 apply。")
    return rows


def _append_jsonl(path: Path, records: list[dict], key: str) -> int:
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    existing.add(json.loads(line).get(key))
                except ValueError:
                    pass
    added = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for record in records:
            if record.get(key) in existing:
                continue
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing.add(record.get(key))
            added += 1
    return added


def _corpus_record(row: dict) -> dict:
    user = row.get("corrected_user") or row.get("user") or ""
    reply = row.get("corrected_reply") or row.get("reply") or ""
    risk = row.get("expected_risk") or row.get("risk") or ""
    scenes = row.get("expected_scenes") or row.get("scenes") or []
    role = detect_speaker_role(user)
    speaker_type = "family_caregiver" if role == SPEAKER_FAMILY else "elder_self"
    category = SCENE_LABELS.get(scenes[0], "复盘补充") if scenes else "复盘补充"
    return {
        "sample_id": f"review-{row['audit_id']}",
        "category": category,
        "messages": [
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ],
        "risk_level": risk,
        "scenes": scenes,
        "speaker_type": speaker_type,
        "source": "audit_review",
        "audit_id": row["audit_id"],
        "feedback": row.get("feedback"),
        "review_notes": row.get("notes") or "",
    }


def _negative_record(row: dict) -> dict:
    return {
        "text": row.get("corrected_user") or row.get("user") or "",
        "reply": row.get("corrected_reply") or row.get("reply") or "",
        "expected_risk": row.get("expected_risk") or row.get("risk"),
        "expected_scenes": row.get("expected_scenes") or row.get("scenes") or [],
        "observed_risk": row.get("risk"),
        "observed_scenes": row.get("scenes") or [],
        "source": "audit_review",
        "audit_id": row["audit_id"],
        "feedback": row.get("feedback"),
        "reason_codes": [r["code"] for r in row.get("reasons") or []],
        "review_notes": row.get("notes") or "",
        "needs_human_label": not bool(row.get("expected_risk")),
    }


def apply_review(queue_path: Path, out_dir: Path, dry_run: bool = False) -> dict:
    rows = [json.loads(line) for line in queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    accepted = [r for r in rows if r.get("decision") in ("accept", "edit")]
    rejected = [r for r in rows if r.get("decision") == "reject"]
    pending = [r for r in rows if r.get("decision") not in ("accept", "edit", "reject")]

    corpus, negative, skill = [], [], []
    for row in accepted:
        dest = {d.strip() for d in str(row.get("destination") or "").split(",") if d.strip()}
        if not dest:
            pending.append(row)
            continue
        if "corpus" in dest:
            corpus.append(_corpus_record(row))
        if "negative" in dest:
            negative.append(_negative_record(row))
        if "skill" in dest:
            skill.append(row)

    outputs = {
        "corpus_candidates": out_dir / "corpus_candidates.jsonl",
        "negative_cases": out_dir / "negative_cases.jsonl",
        "skill_suggestions": out_dir / "skill_suggestions.md",
    }
    counts = {"accepted": len(accepted), "rejected": len(rejected), "pending": len(pending),
              "corpus": len(corpus), "negative": len(negative), "skill": len(skill)}

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        counts["corpus_added"] = _append_jsonl(outputs["corpus_candidates"], corpus, "sample_id")
        counts["negative_added"] = _append_jsonl(outputs["negative_cases"], negative, "audit_id")
        if skill:
            lines = ["# SKILL.md 修改建议（人工吸收）", "",
                     "> 本文件不修改 SKILL.md。请把确认后的规则写进规范，并跑回归门禁。", ""]
            for row in skill:
                lines += [
                    f"## audit #{row['audit_id']}",
                    "",
                    f"- 用户：{row.get('user', '')}",
                    f"- 回复：{row.get('reply', '')}",
                    f"- 信号：{', '.join(r['code'] for r in row.get('reasons') or [])}",
                    f"- 审核意见：{row.get('notes') or '（未填）'}",
                    "",
                ]
            outputs["skill_suggestions"].write_text("\n".join(lines), encoding="utf-8")

    print("=" * 78)
    print("  复盘吸收" + ("（dry-run）" if dry_run else ""))
    print("=" * 78)
    print(f"  accept/edit: {counts['accepted']}  reject: {counts['rejected']}  pending: {counts['pending']}")
    print(f"  语料候选   : {counts['corpus']}" + (f"（新增 {counts.get('corpus_added', 0)}）" if not dry_run else ""))
    print(f"  负例候选   : {counts['negative']}" + (f"（新增 {counts.get('negative_added', 0)}）" if not dry_run else ""))
    print(f"  SKILL 建议 : {counts['skill']}")
    if not dry_run:
        for label, path in outputs.items():
            if path.exists():
                print(f"  {label:<16}: {path}")
    print()
    print("  ⚠️ 未修改 SKILL.md、安全规则或正式语料库；候选文件需人工合并并过门禁。")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="审计对话复盘：collect 生成待审队列，apply 吸收人工决策")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="从审计库生成复盘队列")
    c.add_argument("--db", default=str(DEFAULT_DB))
    c.add_argument("--out", default="")
    c.add_argument("--limit", type=int, default=0)
    c.add_argument("--min-priority", choices=["low", "medium", "high"], default="low")
    c.add_argument("--no-positive", action="store_true", help="不把点赞记录放进语料候选")

    a = sub.add_parser("apply", help="吸收 queue.jsonl 中的人工决策")
    a.add_argument("--queue", required=True)
    a.add_argument("--out-dir", default="")
    a.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    if args.cmd == "collect":
        out = Path(args.out) if args.out else DEFAULT_OUT / datetime.now().strftime("%Y%m%d-%H%M%S")
        collect(Path(args.db), out, include_positive=not args.no_positive,
                limit=args.limit, min_priority=args.min_priority)
        return 0

    queue_path = Path(args.queue)
    if not queue_path.is_file():
        raise SystemExit(f"找不到队列：{queue_path}")
    out_dir = Path(args.out_dir) if args.out_dir else queue_path.parent
    apply_review(queue_path, out_dir, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
