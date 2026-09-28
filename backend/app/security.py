"""访问控制：API Key 鉴权 + 内存滑动窗口限流。

- 鉴权：配置了 BACKEND_API_KEY（可逗号分隔多个密钥）时，请求需带 `X-API-Key` 头；
  未配置则关闭（本地开发）。比较使用常量时间函数，避免时序侧信道。
- 限流：按客户端 IP 做滑动窗口计数，RATE_LIMIT_PER_MINUTE<=0 时关闭。
  仅当请求来自 TRUSTED_PROXIES 时才信任 X-Forwarded-For，否则回退直连 IP，
  防止伪造 XFF 绕过限流。单进程内存实现，多副本部署应替换为 Redis 等共享存储。
"""

import hmac
import ipaddress
import logging
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status

from .config import Settings, get_settings

security_logger = logging.getLogger("xiaonuan.security")


def _is_trusted_proxy(host: str, trusted) -> bool:
    """host 是否属于受信代理（支持精确 IP 与 CIDR）。"""
    if not host or not trusted:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host in trusted
    for item in trusted:
        try:
            if addr in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            if host == item:
                return True
    return False


def get_client_ip(request: Request) -> str:
    """取客户端 IP；仅受信代理的 X-Forwarded-For 才被采信。"""
    settings = get_settings()
    direct = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and _is_trusted_proxy(direct, settings.trusted_proxies):
        return forwarded.split(",")[0].strip()
    return direct


async def require_api_key(x_api_key: str | None = Header(default=None)):
    """校验 X-API-Key；未配置后端密钥时不校验。"""
    settings = get_settings()
    accepted = settings.all_backend_api_keys
    if not accepted:
        return
    provided = x_api_key or ""
    if not any(hmac.compare_digest(provided, key) for key in accepted):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失的 API Key",
            headers={"WWW-Authenticate": "X-API-Key"},
        )


def check_startup_security(settings: Settings | None = None) -> list:
    """校验安全相关配置。

    返回告警列表（供启动日志与 /api/health 暴露）；生产环境在缺少鉴权或
    CORS 通配时直接抛错拒绝启动。
    """
    settings = settings or get_settings()
    warnings = list(settings.config_warnings)

    wildcard = "*" in settings.cors_origins
    if not settings.auth_enabled:
        warnings.append("未配置 BACKEND_API_KEY，接口处于无鉴权状态")
    if wildcard:
        warnings.append("CORS_ORIGINS=* 允许任意来源，建议改为显式来源列表")

    if settings.environment in ("production", "prod"):
        problems = []
        if not settings.auth_enabled:
            problems.append("生产环境必须配置 BACKEND_API_KEY")
        if wildcard:
            problems.append("生产环境必须显式配置 CORS_ORIGINS，不能为 *")
        if problems:
            raise RuntimeError("；".join(problems))

    return warnings


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
