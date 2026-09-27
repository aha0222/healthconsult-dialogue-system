"""pytest 公共夹具：供多个测试文件复用 TestClient + 假 LLM + 临时 SQLite。"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import main as main_module
from backend.app.config import Settings
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
