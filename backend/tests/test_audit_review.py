"""审计复盘工具：信号打分、待审队列、人工决策后的候选输出。"""

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from audit_review import (  # noqa: E402
    apply_review,
    build_review_rows,
    load_audit_rows,
)

AUDIT_DDL = """
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, personality TEXT, user_message TEXT NOT NULL,
    reply TEXT NOT NULL, risk TEXT, violations TEXT,
    fallback_used INTEGER DEFAULT 0, model TEXT, latency_ms INTEGER,
    created_at TEXT NOT NULL, scenes TEXT, feedback INTEGER DEFAULT 0,
    feedback_note TEXT, feedback_at TEXT, interrupted INTEGER DEFAULT 0,
    repeated_question INTEGER DEFAULT 0, llm_backend TEXT
);
"""


def make_db(path: Path):
    con = sqlite3.connect(path)
    con.executescript(AUDIT_DDL)
    rows = [
        # id=1 点踩 + 兜底 + 无场景
        ("我这两天睡不着", "您试试睡前少喝茶。[RISK:R1]", "R1", "[]", 1,
         "2026-10-20T10:00:00+00:00", "[]", -1, "答得太笼统", 0, 0, "cloud"),
        # id=2 点赞
        ("我今天心情不错", "真好，听着就让人高兴。今天有什么开心事？", "R0", "[]", 0,
         "2026-10-20T10:01:00+00:00", "[]", 1, None, 0, 0, "cloud"),
        # id=3 重复追问 + 打断
        ("我这两天睡不着", "您试试睡前少喝茶。", "R1", "[]", 0,
         "2026-10-20T10:02:00+00:00", "[]", 0, None, 1, 1, "cloud"),
    ]
    for row in rows:
        con.execute(
            """INSERT INTO audit_log
               (session_id, personality, user_message, reply, risk, violations,
                fallback_used, model, latency_ms, created_at, scenes, feedback,
                feedback_note, feedback_at, interrupted, repeated_question, llm_backend)
               VALUES ('s','温婉邻居型',?,?,?,?,?, 'deepseek-flash', 1000, ?, ?, ?, ?, NULL, ?, ?, ?)""",
            (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
             row[8], row[9], row[10], row[11]),
        )
    con.commit()
    con.close()


def test_build_review_rows_prioritizes_negative_feedback(tmp_path):
    db = tmp_path / "audit.db"
    make_db(db)
    rows = build_review_rows(load_audit_rows(db))
    by_id = {r["audit_id"]: r for r in rows}
    assert by_id[1]["priority"] == "high"
    assert any(x["code"] == "negative_feedback" for x in by_id[1]["reasons"])
    assert "skill" in by_id[1]["suggested"]
    assert by_id[2]["decision"] == "pending"
    assert any(x["code"] == "positive_feedback" for x in by_id[2]["reasons"])
    assert by_id[3]["repeated_question"] is True
    assert by_id[3]["interrupted"] is True


def test_apply_writes_corpus_and_negative_candidates_only(tmp_path):
    queue = tmp_path / "queue.jsonl"
    item = {
        "audit_id": 1,
        "user": "我这两天睡不着",
        "reply": "您试试睡前少喝茶。",
        "risk": "R1",
        "scenes": [],
        "feedback": -1,
        "reasons": [{"code": "negative_feedback", "weight": 5, "detail": ""}],
        "decision": "edit",
        "destination": "corpus,negative",
        "notes": "补充具体睡眠建议",
        "expected_risk": "R1",
        "expected_scenes": ["L3"],
        "corrected_reply": "您先别急，睡前少喝浓茶，固定时间上床，观察一周。",
    }
    queue.write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")

    counts = apply_review(queue, tmp_path)
    assert counts["corpus"] == 1 and counts["negative"] == 1
    corpus = [json.loads(x) for x in (tmp_path / "corpus_candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert corpus[0]["sample_id"] == "review-1"
    assert corpus[0]["risk_level"] == "R1"
    assert corpus[0]["scenes"] == ["L3"]
    assert corpus[0]["messages"][1]["content"].startswith("您先别急")
    assert corpus[0]["speaker_type"] == "elder_self"

    neg = [json.loads(x) for x in (tmp_path / "negative_cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert neg[0]["needs_human_label"] is False
    assert neg[0]["expected_scenes"] == ["L3"]
    # 明确不碰 SKILL.md
    assert not (tmp_path / "skill_suggestions.md").exists()


def test_apply_dry_run_writes_nothing(tmp_path):
    queue = tmp_path / "queue.jsonl"
    queue.write_text(json.dumps({
        "audit_id": 9, "user": "x", "reply": "y", "decision": "accept",
        "destination": "corpus",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = apply_review(queue, tmp_path, dry_run=True)
    assert counts["corpus"] == 1
    assert not (tmp_path / "corpus_candidates.jsonl").exists()
