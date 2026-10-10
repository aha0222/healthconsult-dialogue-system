"""本地 LLM 实现（成员 B 交付）。由 `providers.get_llm()` 在 OFFLINE_MODE 下返回。

┌─ 设计要点 ───────────────────────────────────────────────────────────┐
│ 1. 签名与 LLMClient 完全一致（chat / chat_stream），调用点零改动。    │
│ 2. **构造惰性**：__init__ 不连网、不加载模型。                        │
│    主链路里 main.py 一次启动构造两个实例，MemoryManager 更是每次请求  │
│    都构造一次；构造函数若去连端点或加载 GGUF，离线模式会每请求重载    │
│    2GB 模型。后端解析推迟到首次调用。                                 │
│ 3. 两种后端，端点优先：                                               │
│      endpoint  —— OpenAI 兼容本地端点（llama-server / Ollama /        │
│                   LM Studio / vLLM），零编译、零 Python 重依赖        │
│      llama_cpp —— 进程内 llama-cpp-python，需另装且占内存            │
│ 4. **绝不回退云端**。两个本地后端都不可用时抛 LLMError，消息点名       │
│    每一条失败原因，便于定位。                                         │
└──────────────────────────────────────────────────────────────────────┘

配置全部从环境变量读（backend/app/config.py 由组长独占，B 不能加字段）：

    OFFLINE_LLM_BACKEND     auto | endpoint | llama_cpp    默认 auto
    OFFLINE_LLM_ENDPOINT    http://127.0.0.1:8080          端点根地址（自动补 /v1）
    OFFLINE_LLM_MODEL       传给端点的 model 名            默认 "local"
    OFFLINE_LLM_MODEL_PATH  进程内后端的 GGUF 路径         默认空
    OFFLINE_LLM_API_KEY     端点占位 key（SDK 要求非空）    默认 sk-local
    OFFLINE_LLM_TIMEOUT     单次调用超时秒数               默认 120
    OFFLINE_LLM_PROBE_TIMEOUT 端点探活超时秒数             默认 3
    OFFLINE_LLM_STRICT      1 = 锁定后端、禁用回退          默认 0
    OFFLINE_LLM_THREADS     进程内后端线程数               默认 8
    OFFLINE_LLM_CTX         进程内后端上下文长度           默认 16384
"""

from __future__ import annotations

import logging
import os
import threading
import time

from ..config import Settings, get_settings
from ..dialogue.llm_client import LLMError
from ..logging_config import log_event

logger = logging.getLogger("xiaonuan.llm.offline")

DEFAULT_ENDPOINT = "http://127.0.0.1:8080"
DEFAULT_PROBE_TIMEOUT = 3.0
DEFAULT_TIMEOUT = 120.0
DEFAULT_PLACEHOLDER_KEY = "sk-local"
DEFAULT_RESOLVE_TTL = 30.0

# 进程级共享句柄：端点 client 与进程内 Llama 都只建一次。
# 注意不要缓存 get_llm() 的返回值——那会让测试的 monkeypatch 失效。
_ENDPOINT_CLIENTS: dict = {}
_LLAMA_HANDLES: dict = {}
_RESOLVED: dict = {}          # 配置指纹 -> (backend, 解析时刻)
_LOCK = threading.Lock()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        logger.warning("环境变量 %s 不是合法数字，回退默认值 %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except ValueError:
        logger.warning("环境变量 %s 不是合法整数，回退默认值 %s", name, default)
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


def _with_v1(base_url: str) -> str:
    """把端点根地址规范成以 /v1 结尾。

    OpenAI SDK 不会自动补 /v1，缺了会拼出 /chat/completions 而 404。
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        url = DEFAULT_ENDPOINT
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return url


def _is_connection_error(exc: BaseException) -> bool:
    """是否为连接级失败（含超时）。

    `APITimeoutError` 是 `APIConnectionError` 的子类，所以这一个判断同时覆盖
    "连不上"与"连上了但超时"；而 `BadRequestError` 等**不是**它的子类，
    内容类错误不会被误当成连接问题。
    """
    try:
        from openai import APIConnectionError
    except ImportError:  # pragma: no cover - 依赖缺失时由调用方另行报错
        return False
    return isinstance(exc, APIConnectionError)


def _is_retryable_connection_error(exc: BaseException) -> bool:
    """"连不上"（而非"等太久"）——换一条新连接重试一次是安全且值得的。

    刻意排除 `APITimeoutError`：它也是 `APIConnectionError` 的子类，但它意味着
    请求可能已经在端点侧跑起来了，重试等于让 CPU 档的一次超时（默认 600s）翻倍，
    且可能重复计费/重复推理。超时不重试，交给调用方按原有超时失败。
    """
    try:
        from openai import APITimeoutError
    except ImportError:  # pragma: no cover - 依赖缺失时由调用方另行报错
        return False
    return _is_connection_error(exc) and not isinstance(exc, APITimeoutError)


def _drop_endpoint_clients() -> None:
    """丢弃所有缓存的端点 client，让下次请求重建连接。

    不按 base_url 精确匹配：实际部署只有一个端点，全清更简单也够用。
    为什么必须丢：httpx 连接池里的坏连接不会自愈，而这些 client 是进程级缓存，
    不丢的话一次瞬时失败会让此后每次请求都复用坏连接（详见
    docs/local_llm_endpoint_wedge_report.md）。
    """
    with _LOCK:
        _ENDPOINT_CLIENTS.clear()


class _EndpointBackend:
    """OpenAI 兼容本地端点。"""

    name = "endpoint"

    def __init__(self, base_url: str, api_key: str, timeout: float):
        self.base_url = _with_v1(base_url)
        self.api_key = api_key or DEFAULT_PLACEHOLDER_KEY
        self.timeout = timeout

    def client(self, fresh: bool = False):
        key = (self.base_url, self.timeout)
        with _LOCK:
            client = _ENDPOINT_CLIENTS.get(key)
            if client is None or fresh:
                try:
                    from openai import OpenAI
                except ImportError as exc:  # pragma: no cover - 依赖缺失
                    raise RuntimeError(
                        "本地端点后端需要 openai 库：pip install openai"
                    ) from exc
                client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    timeout=self.timeout,
                    # CPU 推理很慢，SDK 默认重试 2 次会把一次失败放大成三次
                    max_retries=0,
                )
                _ENDPOINT_CLIENTS[key] = client
            return client

    def probe(self, timeout: float) -> None:
        """探活。用 /v1/models —— llama-server / Ollama / LM Studio / vLLM 都支持。

        不要拿 /v1/chat/completions 探活：那会真的跑一次推理。
        llama-server 另有 /health（不在 /v1 下），但通用性不如 /models。

        连接级失败会**丢弃池里的 client、换一条新连接重试一次**。这是必须的：
        client 是进程级缓存的，而 httpx 池里的坏连接不会自愈，不换掉它的话
        一次瞬时失败会让此后每次探活都复用坏连接——表现为"整页无法对话、
        只有重启后端才能恢复"（见 docs/local_llm_endpoint_wedge_report.md）。
        重试只针对连接错误，且只重试一次，`max_retries=0` 仍然保留（CPU 档
        一次超时被放大成三次的顾虑不变）。
        """
        client = self.client()
        try:
            client.with_options(timeout=timeout).models.list()
        except Exception as exc:
            if _is_connection_error(exc):
                logger.warning(
                    "端点探活连接失败（%s），丢弃缓存的 client 后换新连接重试一次", exc
                )
                fresh = self.client(fresh=True)
                fresh.with_options(timeout=timeout).models.list()
                return
            # 端口上蹲着别的服务（代理、Web 服务器等）时返回的是 HTML，
            # 报错原文很难看懂，这里补一句可操作的提示。
            text = str(exc)
            if "<!DOCTYPE" in text or "<html" in text.lower():
                raise RuntimeError(
                    f"{self.base_url} 上不是 llama-server —— 该端口返回了 HTML 页面，"
                    "多半被别的服务（代理/Web 服务器）占用了。"
                    "请换一个端口（scripts/offline/start.ps1 会自动挑空闲口），"
                    "或先停掉占用该端口的进程。原始错误：" + text.splitlines()[0][:120]
                ) from exc
            raise

    def create(self, model, messages, temperature, max_tokens, stream):
        # 不发 extra_body：`thinking` 是 DeepSeek 专有字段，
        # 严格 OpenAI 兼容的端点（vLLM/TGI 等）会直接 400。
        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )
        try:
            return self.client().chat.completions.create(**kwargs)
        except Exception as exc:
            if not _is_retryable_connection_error(exc):
                raise
            # 池里那条 keep-alive 连接已经死了（端点重启、空闲被回收）。client 一直是
            # `max_retries=0`（防 CPU 档超时被放大成三次），代价是 SDK 不再帮我们
            # 透明重连——于是表现为"空闲一会儿后**第一个请求必失败**，第二个才好"，
            # 在演示/验收时就是"开口第一句就 502"。这里自己换新连接重试一次。
            logger.warning("本地端点连接失败（%s），丢弃缓存连接后换新重试一次", exc)
            return self.client(fresh=True).chat.completions.create(**kwargs)


class _LlamaCppBackend:
    """进程内 llama-cpp-python。"""

    name = "llama_cpp"

    def __init__(self, model_path: str, n_ctx: int, n_threads: int):
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads

    def handle(self):
        if not self.model_path:
            raise RuntimeError(
                "未配置 OFFLINE_LLM_MODEL_PATH，无法使用进程内后端"
            )
        if not os.path.isfile(self.model_path):
            raise RuntimeError(f"GGUF 模型文件不存在：{self.model_path}")

        key = (os.path.abspath(self.model_path), self.n_ctx, self.n_threads)
        with _LOCK:
            llama = _LLAMA_HANDLES.get(key)
            if llama is None:
                try:
                    from llama_cpp import Llama
                except ImportError as exc:
                    raise RuntimeError(
                        "进程内后端需要 llama-cpp-python：pip install llama-cpp-python"
                    ) from exc
                logger.info(
                    "加载本地 GGUF 模型（首次较慢）：%s  n_ctx=%s  threads=%s",
                    self.model_path, self.n_ctx, self.n_threads,
                )
                llama = Llama(
                    model_path=self.model_path,
                    n_ctx=self.n_ctx,
                    n_threads=self.n_threads,
                    verbose=False,
                )
                _LLAMA_HANDLES[key] = llama
            return llama

    def probe(self, timeout: float) -> None:
        """进程内后端没有端点可探，构造句柄即验证（会加载模型）。"""
        self.handle()

    def create(self, model, messages, temperature, max_tokens, stream):
        llama = self.handle()
        return llama.create_chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
        )


def _delta_content(chunk):
    """从 OpenAI SDK 对象或 llama_cpp 的 dict 里取增量文本。"""
    choices = chunk.get("choices") if isinstance(chunk, dict) else getattr(chunk, "choices", None)
    if not choices:
        return None
    first = choices[0]
    delta = first.get("delta") if isinstance(first, dict) else getattr(first, "delta", None)
    if delta is None:
        return None
    content = delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
    return content or None


def _choice_message_content(response):
    choices = getattr(response, "choices", None)
    if not choices and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return ""
    first = choices[0]
    if isinstance(first, dict):
        return (first.get("message") or {}).get("content") or ""
    message = getattr(first, "message", None)
    return (getattr(message, "content", None) or "") if message else ""


class LocalLLM:
    """本地 LLM。接口与 `dialogue.llm_client.LLMClient` 完全一致。

    构造不产生任何副作用；后端解析与连接推迟到首次 `chat` / `chat_stream`。
    """

    def __init__(self, settings: Settings | None = None, client=None):
        self.settings = settings or get_settings()
        self._client = client          # 测试注入用
        self._backend = None
        self._backend_name = ""

    # ── 后端解析 ──────────────────────────────────────────────────
    @property
    def backend_name(self) -> str:
        return self._backend_name

    def _candidates(self):
        """按 OFFLINE_LLM_BACKEND 产出 (名称, 工厂) 列表，按尝试顺序排列。"""
        mode = _env("OFFLINE_LLM_BACKEND", "auto").lower()
        strict = _env_flag("OFFLINE_LLM_STRICT", False)
        timeout = _env_float("OFFLINE_LLM_TIMEOUT", DEFAULT_TIMEOUT)

        def endpoint():
            return _EndpointBackend(
                _env("OFFLINE_LLM_ENDPOINT", DEFAULT_ENDPOINT),
                _env("OFFLINE_LLM_API_KEY", DEFAULT_PLACEHOLDER_KEY),
                timeout,
            )

        def llama_cpp():
            return _LlamaCppBackend(
                _env("OFFLINE_LLM_MODEL_PATH", ""),
                _env_int("OFFLINE_LLM_CTX", 16384),
                _env_int("OFFLINE_LLM_THREADS", 8),
            )

        if mode == "endpoint":
            return [("endpoint", endpoint)]
        if mode == "llama_cpp":
            return [("llama_cpp", llama_cpp)]
        # auto：端点优先，失败回退进程内；strict 时只试端点
        order = [("endpoint", endpoint)]
        if not strict:
            order.append(("llama_cpp", llama_cpp))
        return order

    def _fingerprint(self) -> tuple:
        """当前离线配置的指纹，用于共享解析结果。"""
        return (
            _env("OFFLINE_LLM_BACKEND", "auto").lower(),
            _env("OFFLINE_LLM_ENDPOINT", DEFAULT_ENDPOINT),
            _env("OFFLINE_LLM_MODEL_PATH", ""),
            _env("OFFLINE_LLM_STRICT", "0"),
        )

    def _resolve(self):
        """解析并探活后端。全部不可用则抛 LLMError，点名每条失败原因。

        解析结果在模块级按配置指纹缓存 TTL 秒。这是必要的：
        `main.py` 的 `get_memory()` 没有单例缓存，**每个请求**都会构造一次
        MemoryManager；若接上 get_llm() 后每次都探活，健康时每请求多一次
        网络往返，端点挂掉时每请求多等 2–3 秒。
        """
        if self._backend is not None:
            return self._backend

        fp = self._fingerprint()
        ttl = _env_float("OFFLINE_LLM_RESOLVE_TTL", DEFAULT_RESOLVE_TTL)
        now = time.monotonic()
        with _LOCK:
            hit = _RESOLVED.get(fp)
        if hit and ttl > 0 and (now - hit[1]) < ttl:
            self._backend, self._backend_name = hit[0], hit[0].name
            return self._backend

        probe_timeout = _env_float("OFFLINE_LLM_PROBE_TIMEOUT", DEFAULT_PROBE_TIMEOUT)
        reasons = []
        for name, factory in self._candidates():
            try:
                backend = factory()
                backend.probe(probe_timeout)
            except Exception as exc:
                reason = f"{name}（{type(exc).__name__}: {exc}）"
                reasons.append(reason)
                logger.warning("离线后端 %s 不可用：%s", name, exc)
                continue

            self._backend = backend
            self._backend_name = name
            with _LOCK:
                _RESOLVED[fp] = (backend, now)
            if reasons:
                # 有回退发生过，必须留下明确记录，不能静默
                logger.warning(
                    "离线后端已从失败项回退到 %s。失败原因：%s", name, "；".join(reasons)
                )
            log_event(logger, "offline_backend_selected", backend=name)
            return backend

        # 失败不缓存，下次请求会重新探活（端点可能是后来才起来的）
        detail = "；".join(reasons) if reasons else "未配置任何可用后端"
        raise LLMError(
            "离线模式下所有本地 LLM 后端都不可用，已中止（不会回退到云端）。"
            f"失败原因 —— {detail}。"
            "请检查：endpoint 后端是否已启动 llama-server（见 scripts/offline/start.ps1）、"
            "或改用 llama_cpp 后端并配置 OFFLINE_LLM_MODEL_PATH 指向 GGUF 文件。"
        )

    def _invalidate(self, exc: BaseException | None = None):
        """调用失败时丢弃缓存，让下次请求重新解析（端点可能已重启）。

        连接级失败还要**丢弃进程级缓存的端点 client**：只清解析结果的话，下次
        请求拿回来的还是同一个坏 client（它连同 httpx 连接池一起被缓存），
        于是永久卡死。详见 docs/local_llm_endpoint_wedge_report.md。
        """
        fp = self._fingerprint()
        with _LOCK:
            _RESOLVED.pop(fp, None)
        self._backend = None
        if exc is None or _is_connection_error(exc):
            _drop_endpoint_clients()

    # ── 对 LLMClient 的接口 ───────────────────────────────────────
    def _target_model(self, model):
        # 端点的 model 名优先取离线配置；llama-server 会忽略它
        return model or _env("OFFLINE_LLM_MODEL", "") or self.settings.model or "local"

    def _log_usage(self, model, usage):
        if not usage:
            return
        log_event(
            logger,
            "llm_usage",
            model=model,
            backend=self._backend_name or "unknown",
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )

    def chat(self, messages, temperature=None, max_tokens=None, model=None) -> str:
        """调用本地后端，返回回复正文。签名与 LLMClient.chat 一致。"""
        model = self._target_model(model)
        backend = self._resolve()
        if self._client is not None:      # 测试注入
            backend = None
        try:
            if self._client is not None:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=(
                        self.settings.temperature if temperature is None else temperature
                    ),
                    max_tokens=(
                        self.settings.max_tokens if max_tokens is None else max_tokens
                    ),
                )
            else:
                response = backend.create(
                    model,
                    messages,
                    self.settings.temperature if temperature is None else temperature,
                    self.settings.max_tokens if max_tokens is None else max_tokens,
                    False,
                )
        except LLMError:
            raise
        except Exception as exc:
            self._invalidate(exc)
            raise LLMError(
                f"本地模型调用失败（后端 {self._backend_name or '未解析'}）：{exc}"
            ) from exc

        self._log_usage(model, getattr(response, "usage", None))
        return (_choice_message_content(response) or "").strip()

    def chat_stream(self, messages, temperature=None, max_tokens=None, model=None):
        """流式调用，逐段 yield 文本。签名与 LLMClient.chat_stream 一致。"""
        model = self._target_model(model)
        backend = self._resolve() if self._client is None else None
        try:
            if self._client is not None:
                stream = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=(
                        self.settings.temperature if temperature is None else temperature
                    ),
                    max_tokens=(
                        self.settings.max_tokens if max_tokens is None else max_tokens
                    ),
                    stream=True,
                )
            else:
                stream = backend.create(
                    model,
                    messages,
                    self.settings.temperature if temperature is None else temperature,
                    self.settings.max_tokens if max_tokens is None else max_tokens,
                    True,
                )
        except LLMError:
            raise
        except Exception as exc:
            self._invalidate(exc)
            raise LLMError(
                f"本地模型流式调用失败（后端 {self._backend_name or '未解析'}）：{exc}"
            ) from exc

        try:
            for chunk in stream:
                content = _delta_content(chunk)
                if content:
                    yield content
        except Exception as exc:
            self._invalidate(exc)
            raise LLMError(f"本地模型流式生成中断：{exc}") from exc


__all__ = ["LocalLLM"]
