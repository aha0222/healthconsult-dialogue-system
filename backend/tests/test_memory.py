"""memory.py 单元测试：滚动摘要与长期画像。"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings
from backend.app.dialogue.memory import (
    MemoryManager,
    build_memory_block,
    load_profile,
    parse_memory_result,
)
from backend.app.storage import Database


class FakeMemoryLLM:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = 0
        self.last_messages = None

    def chat(self, messages, **kwargs):
        self.calls += 1
        self.last_messages = messages
        if self.error:
            raise RuntimeError(self.error)
        return self.reply


def make_settings(**overrides):
    base = dict(
        summary_enabled=True,
        summary_threshold=4,
        summary_keep_recent=2,
        max_history=10,
    )
    base.update(overrides)
    return Settings(**base)


def seed(db, session_id, count):
    for i in range(count):
        db.add_message(session_id, "user", f"消息{i}")


def test_parse_memory_result_valid():
    raw = '{"summary": "血压偏高，已建议记录", "profile": {"conditions": ["高血压"], "medications": []}}'
    parsed = parse_memory_result(raw)
    assert parsed["summary"] == "血压偏高，已建议记录"
    assert parsed["profile"]["conditions"] == ["高血压"]
    assert parsed["profile"]["medications"] == []


def test_parse_memory_result_embedded_and_invalid():
    assert parse_memory_result('前文 {"summary": "x", "profile": {}} 后文')["summary"] == "x"
    assert parse_memory_result("不是 JSON") is None


def test_load_profile():
    assert load_profile(None) == {}
    assert load_profile("bad json") == {}
    assert load_profile('{"conditions": ["糖尿病"]}') == {"conditions": ["糖尿病"]}


def test_build_memory_block():
    block = build_memory_block("老人最近血压偏高。", {"conditions": ["高血压"], "medications": ["降压药"]})
    assert "【历史摘要】" in block
    assert "高血压" in block
    assert "用药" in block
    assert build_memory_block(None, {}) == ""


def test_prepare_below_threshold_skips_llm(tmp_path):
    db = Database(tmp_path / "m.db")
    sid = db.create_session("温婉邻居型")
    seed(db, sid, 3)
    llm = FakeMemoryLLM(reply="{}")
    manager = MemoryManager(db, llm=llm, settings=make_settings())

    ctx = manager.prepare(sid)
    assert llm.calls == 0
    assert ctx["memory_block"] == ""
    assert len(ctx["history"]) == 3


def test_prepare_triggers_summary_and_keeps_recent(tmp_path):
    db = Database(tmp_path / "m.db")
    sid = db.create_session("温婉邻居型")
    seed(db, sid, 6)

    reply = json.dumps(
        {"summary": "老人血压偏高。", "profile": {"conditions": ["高血压"]}},
        ensure_ascii=False,
    )
    llm = FakeMemoryLLM(reply=reply)
    manager = MemoryManager(db, llm=llm, settings=make_settings())

    ctx = manager.prepare(sid)
    assert llm.calls == 1
    assert "高血压" in ctx["memory_block"]
    assert "【历史摘要】" in ctx["memory_block"]

    session = db.get_session(sid)
    # 最近 2 条不参与摘要：第 4 条（id=4）是最后被摘要的
    assert session["summary_upto"] == 4
    assert session["summary"] == "老人血压偏高。"


def test_prepare_does_not_resummarize(tmp_path):
    db = Database(tmp_path / "m.db")
    sid = db.create_session("温婉邻居型")
    seed(db, sid, 6)
    llm = FakeMemoryLLM(reply='{"summary": "s", "profile": {}}')
    manager = MemoryManager(db, llm=llm, settings=make_settings())

    manager.prepare(sid)
    assert llm.calls == 1
    manager.prepare(sid)  # 没有新的可摘要消息
    assert llm.calls == 1


def test_prepare_survives_llm_error(tmp_path):
    db = Database(tmp_path / "m.db")
    sid = db.create_session("温婉邻居型")
    seed(db, sid, 6)
    llm = FakeMemoryLLM(error="boom")
    manager = MemoryManager(db, llm=llm, settings=make_settings())

    ctx = manager.prepare(sid)
    assert ctx["memory_block"] == ""
    assert len(ctx["history"]) == 6


def test_disabled_summary_skips_llm(tmp_path):
    db = Database(tmp_path / "m.db")
    sid = db.create_session("温婉邻居型")
    seed(db, sid, 6)
    llm = FakeMemoryLLM(reply='{"summary": "s", "profile": {}}')
    manager = MemoryManager(db, llm=llm, settings=make_settings(summary_enabled=False))

    manager.prepare(sid)
    assert llm.calls == 0
