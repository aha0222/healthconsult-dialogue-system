"""隐私出网口径的回归：① 注入模型的东西不许含号码；② 对话原文要正则脱敏；③ 审计留存旋钮。

为什么要有这组测试：出网路径（云端 LLM）看不到、审计库又是全文——
这类"悄悄多带了点数据"的问题，只有断言才能钉住。
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.dialogue.orchestrator import DialogueOrchestrator  # noqa: E402
from backend.app.main import app, get_db, get_orchestrator  # noqa: E402
from backend.app.storage import Database  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PHONE = "13812345678"
ID_CARD = "11010119900307123X"
BANK = "6222021234567890123"


class RecordingLLM:
    """记下真正发给模型的 messages —— 出网内容只能靠这个证明。"""

    def __init__(self, reply="知道了。[RISK:R1]"):
        self.reply = reply
        self.sent = []

    def chat(self, messages, **kwargs):
        self.sent.append(messages)
        return self.reply

    def chat_stream(self, messages, **kwargs):
        self.sent.append(messages)
        for i in range(0, len(self.reply), 4):
            yield self.reply[i : i + 4]


@pytest.fixture
def recording_client(tmp_path):
    llm = RecordingLLM()
    orch = DialogueOrchestrator(llm=llm, settings=Settings())
    db = Database(tmp_path / "privacy.db")
    app.dependency_overrides[get_orchestrator] = lambda: orch
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), llm, db
    app.dependency_overrides.clear()


# ── ① 采集字段：称谓留、号码不留 ─────────────────────────────


def test_collected_emergency_contact_keeps_name_masks_number(recording_client):
    """紧急联系人只留称谓，手机号/身份证不能落库、更不能进 prompt。"""
    client, llm, db = recording_client
    resp = client.post(
        "/api/profile",
        json={
            "name": "张阿姨",
            "emergencyContact": f"儿子 王先生 {PHONE}",
            "medications": "氨氯地平",
        },
    )
    user_id = resp.json()["id"]

    stored = json.loads(db.get_user(user_id)["collected"])
    assert "王先生" in stored["emergencyContact"]   # 称谓留着，对话里能用
    assert PHONE not in stored["emergencyContact"]  # 号码不落库
    assert "[手机号]" in stored["emergencyContact"]

    # 带着这份档案问一句，注入模型的【已知信息】里也不能有号码
    client.post("/api/chat", json={"message": "我血压有点高", "user_id": user_id})
    injected = "\n".join(m["content"] for m in llm.sent[-1] if m["role"] == "system")
    assert "王先生" in injected
    assert PHONE not in injected


# ── ② 对话原文脱敏 ───────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,masked",
    [
        (f"我儿子电话{PHONE}打不通", "[手机号]"),
        (f"身份证号是{ID_CARD}", "[身份证]"),
        (f"银行卡{BANK}丢了", "[银行卡]"),
    ],
)
def test_chat_redacts_private_numbers_everywhere(recording_client, raw, masked):
    """老人在对话里报出的号码：既不发给云端，也不进审计库与历史表。"""
    client, llm, db = recording_client
    body = client.post("/api/chat", json={"message": raw}).json()

    sent = "\n".join(m["content"] for m in llm.sent[-1])
    assert masked in sent
    for digits in (PHONE, ID_CARD, BANK):
        assert digits not in sent                       # 没出网
        assert digits not in str(client.get("/api/audit").json())  # 没进审计

    session_id = body["session_id"]
    history = db.get_messages(session_id)
    assert all(digits not in m["content"] for m in history for digits in (PHONE, ID_CARD, BANK))
    assert masked in history[0]["content"]


def test_client_supplied_history_is_redacted_too(recording_client):
    """新建会话时前端会带上历史，那里是原始输入，也要过一遍。"""
    client, llm, db = recording_client
    client.post(
        "/api/chat",
        json={
            "message": "刚才说的事",
            "history": [{"role": "user", "content": f"我女儿号是{PHONE}"}],
        },
    )
    sent = "\n".join(m["content"] for m in llm.sent[-1])
    assert PHONE not in sent
    assert "[手机号]" in sent


def test_redaction_does_not_break_risk_detection(recording_client):
    """脱敏只动长号码串，不能把症状关键词也洗掉——否则是安全倒退。"""
    client, llm, db = recording_client
    body = client.post(
        "/api/chat",
        json={"message": f"我儿子电话{PHONE}打不通，我胸口闷得慌喘不上气"},
    ).json()
    assert body["risk"] == "R3"


# ── ③ 审计留存旋钮（默认关） ─────────────────────────────────


def test_prune_audit_disabled_by_default(tmp_path):
    db = Database(tmp_path / "keep.db")
    session_id = db.create_session("温婉邻居型")
    db.add_audit(session_id, "温婉邻居型", "a", "b[R1]")

    assert db.prune_audit(0) == 0
    assert db.prune_audit(-1) == 0
    assert len(db.list_audit()) == 1  # 一条都没少


def test_prune_audit_removes_only_old_rows(tmp_path):
    import sqlite3

    db = Database(tmp_path / "prune.db")
    session_id = db.create_session("温婉邻居型")
    db.add_audit(session_id, "温婉邻居型", "旧", "旧[R1]")
    db.add_audit(session_id, "温婉邻居型", "新", "新[R1]")

    # 把第一条改成 200 天前
    old_ts = "2020-01-01T00:00:00+00:00"
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE audit_log SET created_at = ? WHERE id = 1", (old_ts,))

    assert db.prune_audit(180) == 1
    rows = db.list_audit()
    assert [r["user_message"] for r in rows] == ["新"]
    # 会话与消息不归它管
    assert db.get_session(session_id) is not None


def test_retention_default_is_forever(monkeypatch):
    # 只测"未配置时"的默认值；本机 .env 可能显式配了保留天数，不能拿应用实例断言。
    monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
    assert Settings.from_env().audit_retention_days == 0


def test_retention_days_read_from_env(monkeypatch):
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "180")
    assert Settings.from_env().audit_retention_days == 180
