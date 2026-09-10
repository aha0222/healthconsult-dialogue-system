"""回复缓存：TTL + LRU。

安全优先，默认关闭。只缓存"低风险 + 无上下文"的首轮问答：
- 请求无 session_id、无 history；
- 本地预判风险属于 R0/R1；
- 最终结果风险属于 R0/R1 且未触发兜底。

任何高风险/带上下文的请求都不走缓存，避免答非所问或放大安全问题。
"""

import hashlib
import threading
import time
from collections import OrderedDict

from .config import Settings
from .dialogue.markers import infer_risk_local

CACHEABLE_RISKS = {"R0", "R1"}


class TTLCache:
    def __init__(self, max_size: int = 256, ttl: int = 300):
        self.max_size = max(1, max_size)
        self.ttl = max(1, ttl)
        self._data: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(*parts) -> str:
        raw = "|".join(str(p) for p in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key):
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            value, expires_at = item
            if expires_at < now:
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key, value):
        expires_at = time.time() + self.ttl
        with self._lock:
            self._data[key] = (value, expires_at)
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def clear(self):
        with self._lock:
            self._data.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "max_size": self.max_size,
                "ttl": self.ttl,
            }


def is_cacheable_request(message, history, session_id, settings: Settings) -> bool:
    """判断请求是否允许走缓存。"""
    if not settings.cache_enabled:
        return False
    if session_id or history:
        return False
    return infer_risk_local(message) in CACHEABLE_RISKS


def is_cacheable_result(result: dict) -> bool:
    """判断结果是否值得写入缓存。"""
    return (result.get("risk") in CACHEABLE_RISKS) and not result.get("fallback_used")
