"""访问控制：API Key 鉴权 + 内存滑动窗口限流。

- 鉴权：配置了 BACKEND_API_KEY 时，请求需带 `X-API-Key` 头；未配置则关闭（本地开发）。
- 限流：按客户端 IP 做滑动窗口计数，RATE_LIMIT_PER_MINUTE<=0 时关闭。
  单进程内存实现，多副本部署时应替换为 Redis 等共享存储。
"""

import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

from .config import get_settings


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def require_api_key(x_api_key: str | None = Header(default=None)):
    """校验 X-API-Key；未配置后端密钥时不校验。"""
    settings = get_settings()
    if not settings.auth_enabled:
        return
    if x_api_key != settings.backend_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失的 API Key",
            headers={"WWW-Authenticate": "X-API-Key"},
        )


class SlidingWindowLimiter:
    """按 key 记录时间戳的滑动窗口限流器。"""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        now = time.monotonic()
        queue = self._hits[key]
        while queue and now - queue[0] > window_seconds:
            queue.popleft()
        if len(queue) >= limit:
            return False
        queue.append(now)
        return True

    def reset(self):
        self._hits.clear()


limiter = SlidingWindowLimiter()


async def rate_limit(request: Request):
    """按 IP 限流；未开启时直接放行。"""
    settings = get_settings()
    limit = settings.rate_limit_per_minute
    if limit <= 0:
        return
    if not limiter.allow(get_client_ip(request), limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后再试",
            headers={"Retry-After": "60"},
        )
