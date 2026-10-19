"""在线档的网络兜底：云端不可用时切到本地档，并把"已降级"这件事说出来。

为什么需要：`get_llm()` 是按 `OFFLINE_MODE` **启动时静态选档**的，没有运行时降级。
在线档为主路径之后，拔网线/云端抖动只会让每次请求 502——演示时第一句话就翻车。

三条设计约束（都不是可选的）：
  1. **默认关闭**。`LLM_FALLBACK_ENABLED=1` 才启用，与其余能力开关一致；
     关掉时行为与从前完全一样（云端报错即 502，不静默换档）。
  2. **不静默**。切档写入结构化日志，并经 `/api/health` 与聊天响应的 notice 字段
     告知前端，让老人看到"网络不通，先用本地"而不是莫名变快变笨。
  3. **流式中途不换档**。已经吐过 delta 再换本地重试会把两段话拼在一起；
     只有"一个字都还没吐"才允许换。

状态是**进程级共享**的：`get_llm()` 会被 MemoryManager 每个请求调用一次，
若把降级状态挂在实例上，每个请求都会各自重新探测一遍云端。
"""

from __future__ import annotations

import logging
import os
import threading
import time

from ..dialogue.llm_client import LLMError
from ..logging_config import log_event

logger = logging.getLogger("xiaonuan.llm.fallback")

BACKEND_CLOUD = "cloud"
BACKEND_LOCAL = "local"


def _local_endpoint() -> str:
    """本地端点根地址。与 local_llm 一致，直接读环境变量（Settings 没有这个字段）。"""
    return (os.environ.get("OFFLINE_LLM_ENDPOINT") or "").strip() or "http://127.0.0.1:8080"

# 进程级降级状态（按配置指纹区分，避免测试之间互相干扰）
_STATE: dict = {}
_LOCK = threading.Lock()


def _state_for(key: str) -> dict:
    with _LOCK:
        state = _STATE.get(key)
        if state is None:
            state = {"degraded": False, "since": 0.0, "probe_after": 0.0, "reason": ""}
            _STATE[key] = state
        return state


def reset_state(key: str | None = None) -> None:
    """清空降级状态（测试用；key=None 清全部）。"""
    with _LOCK:
        if key is None:
            _STATE.clear()
        else:
            _STATE.pop(key, None)


def backend_status(key: str) -> dict:
    """给 /api/health 用的只读快照。"""
    state = _state_for(key)
    return {
        "llm_backend": BACKEND_LOCAL if state["degraded"] else BACKEND_CLOUD,
        "llm_degraded": bool(state["degraded"]),
        "llm_degraded_reason": state["reason"] or "",
    }


def mark_degraded(settings, reason: str) -> None:
    """把某个配置标成"已降级"（启动探测用，早于第一个请求）。"""
    state = _state_for(FallbackLLM.state_key(settings))
    state["degraded"] = True
    state["since"] = time.monotonic()
    state["probe_after"] = time.monotonic() + float(
        getattr(settings, "llm_fallback_probe_seconds", 60) or 0
    )
    state["reason"] = reason
    log_event(logger, "llm_degraded", reason=reason, to=BACKEND_LOCAL,
              endpoint=_local_endpoint())


def probe_primary(settings, timeout: float = 3.0) -> bool:
    """启动时探一次云端可达性。

    只有"明确连不上"才返回 False；401/403 表示网络是通的、只是 Key 有问题，
    那种情况不该当成断网去切本地档（会把配置错误伪装成网络问题）。
    """
    import httpx

    base = (getattr(settings, "base_url", "") or "").rstrip("/")
    if not base:
        return True
    key = getattr(settings, "api_key", "") or ""
    try:
        httpx.get(
            base + "/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
    except Exception:
        return False
    return True


def _cause_is_connection_error(exc: BaseException) -> bool:
    """异常链里是否有"连不上云端"这一环。

    `LLMClient` 把所有异常包成 `LLMError`（原文挂在 `__cause__`），所以要沿链找
    `openai.APIConnectionError`（含其子类 `APITimeoutError`：都不算"云端可用"）。
    """
    try:
        from openai import APIConnectionError
    except ImportError:  # pragma: no cover - 依赖缺失时保守不降级
        return False
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, APIConnectionError):
            return True
        current = current.__cause__ or current.__context__
    return False


class FallbackLLM:
    """包装主档（云端），在连接级失败时切到本地档并保持可见。

    与 `LLMClient` / `LocalLLM` 签名完全一致，调用点零改动。
    """

    def __init__(self, primary, fallback_factory, settings, enabled: bool = True):
        self._primary = primary
        self._fallback_factory = fallback_factory
        self._settings = settings
        self._enabled = bool(enabled)
        self._local = None
        # 配置指纹：不同 base_url / 端点的实例互不干扰
        self._key = self.state_key(settings)
        self._probe_seconds = float(
            getattr(settings, "llm_fallback_probe_seconds", 60) or 0
        )

    @staticmethod
    def state_key(settings) -> str:
        """进程级降级状态的键（同一份配置共享一个状态）。"""
        return f"{getattr(settings, 'base_url', '')}|{_local_endpoint()}"

    @property
    def degraded(self) -> bool:
        return _state_for(self._key)["degraded"]

    def _local_llm(self):
        if self._local is None:
            self._local = self._fallback_factory()
        return self._local

    def _switch(self, reason: str) -> None:
        state = _state_for(self._key)
        if not state["degraded"]:
            log_event(
                logger,
                "llm_degraded",
                reason=reason,
                to=BACKEND_LOCAL,
                endpoint=_local_endpoint(),
            )
        state["degraded"] = True
        state["since"] = time.monotonic()
        state["probe_after"] = time.monotonic() + self._probe_seconds
        state["reason"] = reason

    def _recover(self) -> None:
        state = _state_for(self._key)
        state["degraded"] = False
        state["since"] = 0.0
        state["probe_after"] = 0.0
        state["reason"] = ""
        log_event(logger, "llm_recovered", to=BACKEND_CLOUD)

    def _should_try_cloud(self) -> bool:
        """降级后仍定期回探云端：网络恢复了就不该一直用慢档。"""
        state = _state_for(self._key)
        if not state["degraded"]:
            return True
        if self._probe_seconds <= 0:
            return False
        return time.monotonic() >= state["probe_after"]

    def _use_cloud(self) -> bool:
        return not _state_for(self._key)["degraded"] or self._should_try_cloud()

    def _failover(self, exc: BaseException, consumed_output: bool):
        """返回 None 表示已切本地可重试；否则抛原异常。"""
        if not self._enabled or consumed_output or not _cause_is_connection_error(exc):
            raise exc
        self._switch(reason=f"{type(exc).__name__}: {exc}")
        return self._local_llm()

    def chat(self, messages, temperature=None, max_tokens=None, model=None) -> str:
        if not self._use_cloud():
            return self._local_llm().chat(
                messages, temperature=temperature, max_tokens=max_tokens, model=model
            )
        try:
            reply = self._primary.chat(
                messages, temperature=temperature, max_tokens=max_tokens, model=model
            )
        except LLMError as exc:
            if self.degraded and self._enabled and _cause_is_connection_error(exc):
                # 回探云端又失败：继续用本地，不把异常抛给老人
                self._switch(reason=f"回探失败: {exc}")
                return self._local_llm().chat(
                    messages, temperature=temperature, max_tokens=max_tokens, model=model
                )
            local = self._failover(exc, consumed_output=False)
            return local.chat(
                messages, temperature=temperature, max_tokens=max_tokens, model=model
            )
        if self.degraded:
            self._recover()
        return reply

    def chat_stream(self, messages, temperature=None, max_tokens=None, model=None):
        if not self._use_cloud():
            yield from self._local_llm().chat_stream(
                messages, temperature=temperature, max_tokens=max_tokens, model=model
            )
            return

        consumed = False
        try:
            for chunk in self._primary.chat_stream(
                messages, temperature=temperature, max_tokens=max_tokens, model=model
            ):
                consumed = True
                yield chunk
        except LLMError as exc:
            if self.degraded and self._enabled and _cause_is_connection_error(exc):
                self._switch(reason=f"回探失败: {exc}")
                if not consumed:
                    yield from self._local_llm().chat_stream(
                        messages, temperature=temperature, max_tokens=max_tokens, model=model
                    )
                    return
                raise
            # 一个字都没吐出去才允许换档；吐过就保持现状（宁缺勿串）
            local = self._failover(exc, consumed_output=consumed)
            yield from local.chat_stream(
                messages, temperature=temperature, max_tokens=max_tokens, model=model
            )
            return
        if self.degraded:
            self._recover()
