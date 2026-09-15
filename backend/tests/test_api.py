"""HTTP API 测试：使用 TestClient + 假 LLM + 临时 SQLite。"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import main as main_module
from backend.app.cache import TTLCache
from backend.app.config import Settings
from backend.app.dialogue.llm_client import LLMError
from backend.app.dialogue.orchestrator import DialogueOrchestrator
from backend.app.main import app, get_cache, get_db, get_memory, get_orchestrator
from backend.app.security import limiter
from backend.app.storage import Database


class FakeLLM:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error

    def chat(self, messages, **kwargs):
        if self.error:
            raise self.error
        return self.reply

    def chat_stream(self, messages, **kwargs):
        if self.error:
            raise self.error
        text = self.reply or ""
        for i in range(0, len(text), 4):
            yield text[i : i + 4]


class SpyOrchestrator:
    def __init__(self):
        self.memory_blocks = []

    def respond(self, message, personality, history, memory_block=None):
        self.memory_blocks.append(memory_block)
        return {
            "reply": "好的。",
            "risk": "R0",
            "risk_label": "日常",
            "violations": [],
            "fallback_used": False,
            "semantic_checked": False,
            "personality": personality,
            "model": "test",
        }


class StubMemory:
    def prepare(self, session_id):
        return {"history": [], "memory_block": "【历史摘要】\n老人有高血压。"}


@pytest.fixture
def make_client(tmp_path):
    def _make(
        reply=None,
        error=None,
        api_key="",
        backend_api_key="",
        rate_limit=0,
        cache_enabled=False,
    ):
        orch = DialogueOrchestrator(llm=FakeLLM(reply, error), settings=Settings())
        db = Database(tmp_path / "test.db")
        app.dependency_overrides[get_orchestrator] = lambda: orch
        app.dependency_overrides[get_db] = lambda: db
        main_module.settings.api_key = api_key
        main_module.settings.backend_api_key = backend_api_key
        main_module.settings.rate_limit_per_minute = rate_limit
        main_module.settings.cache_enabled = cache_enabled
        limiter.reset()
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()
    main_module.settings.api_key = ""
    main_module.settings.backend_api_key = ""
    main_module.settings.rate_limit_per_minute = 0
    main_module.settings.cache_enabled = False
    limiter.reset()


def test_health(make_client):
    client = make_client(reply="x")
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "model" in body


def test_personalities(make_client):
    client = make_client(reply="x")
    resp = client.get("/api/personalities")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 4
    assert any(item["default"] for item in items)


def test_taxonomy_endpoint(make_client):
    client = make_client(reply="x")
    resp = client.get("/api/taxonomy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_levels"] == ["R3", "R2b", "R2a", "R1", "R0"]
    assert "S3" in body["scenes"]
    assert body["scene_labels"]["S3"] == "慢病管理"


def test_chat_creates_session_and_persists(make_client):
    client = make_client(reply="您记下来带给医生看。[RISK:R1]")
    resp = client.post("/api/chat", json={"message": "我血压有点高"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "您记下来带给医生看。"
    assert body["risk"] == "R1"
    assert body["fallback_used"] is False
    session_id = body["session_id"]
    assert session_id

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "我血压有点高"


def test_chat_continues_existing_session(make_client):
    client = make_client(reply="好的。[RISK:R0]")
    first = client.post("/api/chat", json={"message": "第一句"}).json()
    session_id = first["session_id"]

    second = client.post(
        "/api/chat", json={"message": "第二句", "session_id": session_id}
    ).json()
    assert second["session_id"] == session_id

    detail = client.get(f"/api/sessions/{session_id}").json()
    assert len(detail["messages"]) == 4


def test_chat_unknown_session_404(make_client):
    client = make_client(reply="x")
    resp = client.post("/api/chat", json={"message": "你好", "session_id": "nope"})
    assert resp.status_code == 404


def test_chat_redline_fallback(make_client):
    client = make_client(reply="药量你自己调。[RISK:R2a]")
    resp = client.post("/api/chat", json={"message": "我能自己加药吗"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["fallback_used"] is True
    assert body["violations"]


def test_chat_empty_message_rejected(make_client):
    client = make_client(reply="x")
    resp = client.post("/api/chat", json={"message": ""})
    assert resp.status_code == 422


def test_chat_llm_error_returns_502(make_client):
    client = make_client(error=LLMError("没有配置 Key"))
    resp = client.post("/api/chat", json={"message": "你好"})
    assert resp.status_code == 502
    assert "没有配置 Key" in resp.json()["detail"]


def test_chat_stream_requires_api_key(make_client):
    client = make_client(reply="你好。[RISK:R0]")
    resp = client.post("/api/chat/stream", json={"message": "你好"})
    assert resp.status_code == 502


def test_chat_stream_emits_deltas_and_done(make_client):
    client = make_client(reply="您记下来带给医生看。[RISK:R1]", api_key="test-key")
    resp = client.post("/api/chat/stream", json={"message": "我血压有点高"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    text = resp.text
    assert "event: delta" in text
    assert "event: done" in text

    done_line = [ln for ln in text.splitlines() if ln.startswith("data: ")][-1]
    done = json.loads(done_line[len("data: ") :])
    assert done["risk"] == "R1"
    assert done["reply"] == "您记下来带给医生看。"
    assert done["session_id"]

    detail = client.get(f"/api/sessions/{done['session_id']}").json()
    assert len(detail["messages"]) == 2


def test_session_list_and_delete(make_client):
    client = make_client(reply="好的。[RISK:R0]")
    session_id = client.post("/api/chat", json={"message": "你好"}).json()["session_id"]

    listed = client.get("/api/sessions").json()
    assert any(s["id"] == session_id for s in listed)

    assert client.delete(f"/api/sessions/{session_id}").json()["deleted"] is True
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


def test_chat_writes_audit(make_client):
    client = make_client(reply="药量你自己调。[RISK:R2a]")
    client.post("/api/chat", json={"message": "我能自己加药吗"})

    records = client.get("/api/audit").json()
    assert len(records) == 1
    assert records[0]["risk"] == "R2a"
    assert records[0]["fallback_used"] is True
    assert records[0]["latency_ms"] is not None


def test_audit_filter_by_session(make_client):
    client = make_client(reply="好的。[RISK:R0]")
    session_id = client.post("/api/chat", json={"message": "你好"}).json()["session_id"]

    assert len(client.get(f"/api/audit?session_id={session_id}").json()) == 1
    assert client.get("/api/audit?session_id=nope").json() == []


def test_auth_disabled_allows_request(make_client):
    client = make_client(reply="好的。[RISK:R0]")
    assert client.post("/api/chat", json={"message": "你好"}).status_code == 200


def test_auth_requires_api_key_when_configured(make_client):
    client = make_client(reply="好的。[RISK:R0]", backend_api_key="secret")

    no_key = client.post("/api/chat", json={"message": "你好"})
    assert no_key.status_code == 401

    wrong = client.post(
        "/api/chat", json={"message": "你好"}, headers={"X-API-Key": "bad"}
    )
    assert wrong.status_code == 401

    ok = client.post(
        "/api/chat", json={"message": "你好"}, headers={"X-API-Key": "secret"}
    )
    assert ok.status_code == 200


def test_health_open_even_with_auth(make_client):
    client = make_client(reply="x", backend_api_key="secret")
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["auth_enabled"] is True


def test_rate_limit_returns_429(make_client):
    client = make_client(reply="好的。[RISK:R0]", rate_limit=2)
    assert client.post("/api/chat", json={"message": "1"}).status_code == 200
    assert client.post("/api/chat", json={"message": "2"}).status_code == 200
    limited = client.post("/api/chat", json={"message": "3"})
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_chat_passes_memory_block(make_client):
    client = make_client(reply="x")
    spy = SpyOrchestrator()
    app.dependency_overrides[get_orchestrator] = lambda: spy
    app.dependency_overrides[get_memory] = lambda: StubMemory()

    first = client.post("/api/chat", json={"message": "你好"}).json()
    assert spy.memory_blocks[0] == ""  # 新会话无记忆

    client.post(
        "/api/chat", json={"message": "再说一次", "session_id": first["session_id"]}
    )
    assert "历史摘要" in spy.memory_blocks[1]  # 续接时注入记忆块


def test_message_too_long_rejected(make_client):
    client = make_client(reply="x")
    resp = client.post("/api/chat", json={"message": "啊" * 2001})
    assert resp.status_code == 422


def test_cache_hit_on_repeat_low_risk(make_client):
    client = make_client(reply="您记下来带给医生看。[RISK:R1]", cache_enabled=True)
    cache = TTLCache(max_size=8, ttl=300)
    app.dependency_overrides[get_cache] = lambda: cache

    first = client.post("/api/chat", json={"message": "血压有点高"}).json()
    assert first["cached"] is False

    second = client.post("/api/chat", json={"message": "血压有点高"}).json()
    assert second["cached"] is True
    assert second["reply"] == first["reply"]
    assert cache.stats()["hits"] == 1


def test_cache_skips_high_risk(make_client):
    client = make_client(reply="请马上打120。[RISK:R3]", cache_enabled=True)
    cache = TTLCache(max_size=8, ttl=300)
    app.dependency_overrides[get_cache] = lambda: cache

    client.post("/api/chat", json={"message": "胸口疼喘不上气"})
    client.post("/api/chat", json={"message": "胸口疼喘不上气"})
    assert cache.stats()["size"] == 0


def test_cache_stats_endpoint(make_client):
    client = make_client(reply="x")
    resp = client.get("/api/cache/stats")
    assert resp.status_code == 200
    assert "size" in resp.json()
