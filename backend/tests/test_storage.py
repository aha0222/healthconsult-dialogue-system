"""storage.py 单元测试：SQLite 会话与消息。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.storage import SCHEMA_VERSION, Database


def make_db(tmp_path):
    return Database(tmp_path / "sessions.db")


def test_schema_version_is_current(tmp_path):
    db = make_db(tmp_path)
    assert db.get_schema_version() == SCHEMA_VERSION


def test_migration_from_v2_adds_memory_columns(tmp_path):
    import sqlite3

    from backend.app.storage import AUDIT_SCHEMA, SESSIONS_SCHEMA

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(SESSIONS_SCHEMA)
    conn.executescript(AUDIT_SCHEMA)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    conn.commit()
    conn.close()

    db = Database(path)
    assert db.get_schema_version() == SCHEMA_VERSION

    session_id = db.create_session("温婉邻居型")
    db.update_memory(session_id, summary="s", profile="{}", summary_upto=1)
    assert db.get_session(session_id)["summary"] == "s"


def test_create_and_get_session(tmp_path):
    db = make_db(tmp_path)
    session_id = db.create_session("温婉邻居型")
    session = db.get_session(session_id)
    assert session["id"] == session_id
    assert session["personality"] == "温婉邻居型"
    assert db.get_session("missing") is None


def test_add_and_get_messages(tmp_path):
    db = make_db(tmp_path)
    session_id = db.create_session("温婉邻居型")
    db.add_message(session_id, "user", "我血压有点高")
    db.add_message(session_id, "assistant", "您记下来带给医生看。", "R1")

    messages = db.get_messages(session_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["risk"] == "R1"


def test_get_messages_limit_returns_latest_in_order(tmp_path):
    db = make_db(tmp_path)
    session_id = db.create_session("温婉邻居型")
    for i in range(5):
        db.add_message(session_id, "user", f"消息{i}")

    recent = db.get_messages(session_id, limit=2)
    assert [m["content"] for m in recent] == ["消息3", "消息4"]


def test_list_sessions_with_counts(tmp_path):
    db = make_db(tmp_path)
    s1 = db.create_session("温婉邻居型")
    db.add_message(s1, "user", "你好")
    db.create_session("贴心闺女型")

    sessions = db.list_sessions()
    assert len(sessions) == 2
    by_id = {s["id"]: s for s in sessions}
    assert by_id[s1]["message_count"] == 1


def test_delete_session(tmp_path):
    db = make_db(tmp_path)
    session_id = db.create_session("温婉邻居型")
    db.add_message(session_id, "user", "你好")

    assert db.delete_session(session_id) is True
    assert db.get_session(session_id) is None
    assert db.get_messages(session_id) == []
    assert db.delete_session(session_id) is False


def test_update_memory(tmp_path):
    db = make_db(tmp_path)
    session_id = db.create_session("温婉邻居型")
    db.add_message(session_id, "user", "你好")

    db.update_memory(
        session_id,
        summary="老人血压偏高。",
        profile='{"conditions": ["高血压"]}',
        summary_upto=1,
    )
    session = db.get_session(session_id)
    assert session["summary"] == "老人血压偏高。"
    assert session["profile"] == '{"conditions": ["高血压"]}'
    assert session["summary_upto"] == 1


def test_add_and_list_audit(tmp_path):
    db = make_db(tmp_path)
    session_id = db.create_session("温婉邻居型")
    db.add_audit(
        session_id=session_id,
        personality="温婉邻居型",
        user_message="我血压有点高",
        reply="您记下来带给医生看。",
        risk="R1",
        violations=["missing_scene_marker"],
        fallback_used=False,
        model="deepseek-chat",
        latency_ms=123,
    )

    records = db.list_audit()
    assert len(records) == 1
    record = records[0]
    assert record["risk"] == "R1"
    assert record["violations"] == ["missing_scene_marker"]
    assert record["fallback_used"] is False
    assert record["latency_ms"] == 123

    assert len(db.list_audit(session_id=session_id)) == 1
    assert db.list_audit(session_id="other") == []
