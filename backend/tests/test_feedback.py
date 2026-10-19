"""反馈信号：重复追问判定 + 赞/踩与打断的 HTTP 接口。

自我改进真正的瓶颈是"好坏信号"而非算法——audit 表原先只有客观信号
（violations / fallback_used / 风险等级），这里补上人工信号（赞/踩）与两个
客观交互信号（重复追问、打断播报）。
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.feedback import (  # noqa: E402
    FEEDBACK_DOWN,
    FEEDBACK_UP,
    is_repeated_question,
    normalize_question,
)

REPLY = "您先别急，这几天固定时间量一量记下来，带给医生看。[RISK:R1]"


# ── 重复追问判定 ─────────────────────────────────────────────


def test_normalize_strips_punctuation_and_space():
    assert normalize_question("我 睡不着，怎么办？") == "我睡不着怎么办"


@pytest.mark.parametrize(
    "previous,current",
    [
        ("我这两天睡不着", "我这两天睡不着"),
        ("我这两天老是睡不着", "我这两天老是睡不着啊"),
        ("最近血压有点高怎么办", "最近血压有点高，怎么办呀"),
    ],
)
def test_repeat_detected(previous, current):
    assert is_repeated_question(previous, current) is True


@pytest.mark.parametrize(
    "previous,current",
    [
        ("我这两天睡不着", "我想问问血压多少算高"),
        ("我睡不着", "好"),  # 太短，不可靠
        (None, "我这两天睡不着"),  # 没有上一条
        ("我这两天睡不着", None),
        ("", "我这两天睡不着"),
    ],
)
def test_not_repeat(previous, current):
    assert is_repeated_question(previous, current) is False


# ── 接口 ─────────────────────────────────────────────────────


def test_chat_response_carries_audit_id(make_client):
    client = make_client(reply=REPLY)
    body = client.post("/api/chat", json={"message": "我血压有点高"}).json()
    assert isinstance(body["audit_id"], int)

    rows = client.get("/api/audit").json()
    assert rows[0]["id"] == body["audit_id"]
    assert rows[0]["feedback"] == 0
    assert rows[0]["repeated_question"] is False


def test_submit_feedback_updates_audit_row(make_client):
    client = make_client(reply=REPLY)
    audit_id = client.post("/api/chat", json={"message": "我血压有点高"}).json()["audit_id"]

    resp = client.post(
        f"/api/audit/{audit_id}/feedback",
        json={"rating": FEEDBACK_DOWN, "note": "答得太笼统"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"updated": True, "audit_id": audit_id, "feedback": -1}

    row = client.get("/api/audit").json()[0]
    assert row["feedback"] == FEEDBACK_DOWN
    assert row["feedback_note"] == "答得太笼统"
    assert row["feedback_at"]

    # 改赞是覆盖，不是追加
    assert (
        client.post(f"/api/audit/{audit_id}/feedback", json={"rating": FEEDBACK_UP})
        .status_code
        == 200
    )
    assert client.get("/api/audit").json()[0]["feedback"] == FEEDBACK_UP


def test_feedback_rejects_invalid_rating_and_unknown_id(make_client):
    client = make_client(reply=REPLY)
    audit_id = client.post("/api/chat", json={"message": "我血压有点高"}).json()["audit_id"]

    assert client.post(
        f"/api/audit/{audit_id}/feedback", json={"rating": 5}
    ).status_code == 422
    assert client.post(
        "/api/audit/999999/feedback", json={"rating": 1}
    ).status_code == 404


def test_interrupt_marks_audit_row(make_client):
    client = make_client(reply=REPLY)
    audit_id = client.post("/api/chat", json={"message": "我血压有点高"}).json()["audit_id"]

    resp = client.post(f"/api/audit/{audit_id}/interrupt")
    assert resp.status_code == 200
    assert client.get("/api/audit").json()[0]["interrupted"] is True
    assert client.post("/api/audit/999999/interrupt").status_code == 404


def test_repeated_question_flagged_on_second_identical_message(make_client):
    client = make_client(reply=REPLY)
    session_id = client.post("/api/chat", json={"message": "我这两天睡不着"}).json()[
        "session_id"
    ]
    client.post(
        "/api/chat", json={"message": "我这两天睡不着", "session_id": session_id}
    )

    rows = client.get("/api/audit").json()  # 按 id 倒序
    assert rows[0]["repeated_question"] is True
    assert rows[1]["repeated_question"] is False


def test_stream_done_event_carries_audit_id(make_client):
    # api_key 非空才过 /api/chat/stream 的云端 Key 门（见 main.chat_stream）
    client = make_client(reply=REPLY, api_key="sk-test")
    resp = client.post("/api/chat/stream", json={"message": "我血压有点高"})
    assert resp.status_code == 200
    done_line = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")][-1]
    done = json.loads(done_line[len("data: ") :])
    assert isinstance(done["audit_id"], int)
