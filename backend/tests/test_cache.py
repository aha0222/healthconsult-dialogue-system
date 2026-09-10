"""cache.py 单元测试：TTL + LRU 与缓存资格判定。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import backend.app.cache as cache_module
from backend.app.cache import (
    TTLCache,
    is_cacheable_request,
    is_cacheable_result,
)
from backend.app.config import Settings


def test_set_and_get():
    cache = TTLCache(max_size=4, ttl=100)
    cache.set("k", {"reply": "hi"})
    assert cache.get("k") == {"reply": "hi"}


def test_lru_eviction():
    cache = TTLCache(max_size=2, ttl=100)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # a 变最近
    cache.set("c", 3)  # 淘汰 b
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3


def test_ttl_expiry(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(cache_module.time, "time", lambda: now["t"])
    cache = TTLCache(max_size=4, ttl=10)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    now["t"] = 1011.0
    assert cache.get("k") is None


def test_stats_and_clear():
    cache = TTLCache(max_size=4, ttl=100)
    cache.set("k", "v")
    cache.get("k")
    cache.get("missing")
    stats = cache.stats()
    assert stats["size"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    cache.clear()
    assert cache.stats()["size"] == 0
    assert cache.stats()["hits"] == 0


def test_make_key_deterministic():
    assert TTLCache.make_key("a", "b") == TTLCache.make_key("a", "b")
    assert TTLCache.make_key("a", "b") != TTLCache.make_key("a", "c")


def test_is_cacheable_request():
    enabled = Settings(cache_enabled=True)
    assert is_cacheable_request("今天天气不错", [], None, enabled) is True
    assert is_cacheable_request("血压有点高", [], None, enabled) is True
    # 高风险不缓存
    assert is_cacheable_request("胸口疼喘不上气", [], None, enabled) is False
    # 带上下文不缓存
    assert is_cacheable_request("今天天气不错", [], "s1", enabled) is False
    assert is_cacheable_request("今天天气不错", [{"role": "user", "content": "x"}], None, enabled) is False
    # 关闭时不缓存
    assert is_cacheable_request("今天天气不错", [], None, Settings(cache_enabled=False)) is False


def test_is_cacheable_result():
    assert is_cacheable_result({"risk": "R1", "fallback_used": False}) is True
    assert is_cacheable_result({"risk": "R3", "fallback_used": False}) is False
    assert is_cacheable_result({"risk": "R1", "fallback_used": True}) is False
