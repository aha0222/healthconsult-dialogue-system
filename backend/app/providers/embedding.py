"""嵌入后端的严格构造（成员 B 交付）。

┌─ 为什么需要这个 ─────────────────────────────────────────────────────┐
│ `dialogue/retriever.py` 的 `build_embedder()` 把构造整个 try 包住，   │
│ **任何异常都 warning 后回退 HashEmbedder()**：                        │
│                                                                       │
│     try: ... except Exception as exc:                                │
│         logger.warning("嵌入后端 %s 不可用（%s），回退 hash 后端。")   │
│     return HashEmbedder()                                            │
│                                                                       │
│ 缺 sentence-transformers、模型权重下载失败、api 缺 key —— 全都静默    │
│ 变成 hash。检索照样"能跑"，只是召回的语义质量悄悄退化了，没有任何     │
│ 地方会失败或报警。                                                    │
│                                                                       │
│ 分工方案的硬约束是「缺依赖必须抛清晰错误，不得静默降级」，所以离线档  │
│ 需要一个不吞异常的版本。                                              │
└──────────────────────────────────────────────────────────────────────┘

用法（接线方式见交付报告第 5 节）：

    from ..providers.embedding import build_embedder_strict

行为差异：
    · `EMBEDDING_BACKEND=hash`  —— 零依赖零下载，直接放行（离线档推荐）
    · `EMBEDDING_BACKEND=local` —— 缺 sentence-transformers 或权重时**抛错**
    · `EMBEDDING_BACKEND=api`   —— 缺 base_url/api_key 时**抛错**
    · 未知 backend 值           —— **抛错**（不再默默给个 hash）

想恢复旧的宽松行为（比如为了兼容尚未改造的调用点），设 `EMBEDDING_STRICT=0`。
"""

from __future__ import annotations

import logging
import os

from ..config import Settings, get_settings
from ..dialogue.retriever import APIEmbedder, BaseEmbedder, HashEmbedder, LocalEmbedder

logger = logging.getLogger("xiaonuan.embedding")

KNOWN_BACKENDS = ("hash", "local", "api")


class EmbedderUnavailable(RuntimeError):
    """嵌入后端不可用且严格模式禁止回退。"""


def _strict_default() -> bool:
    raw = (os.environ.get("EMBEDDING_STRICT") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def build_embedder_strict(
    settings: Settings | None = None, strict: bool | None = None
) -> BaseEmbedder:
    """按配置构造嵌入后端；不可用时抛 EmbedderUnavailable，不回退 hash。

    `strict=None` 时读环境变量 EMBEDDING_STRICT（默认严格）。
    非严格模式下退回仓库原有的「失败即 hash」行为，便于渐进接线。
    """
    settings = settings or get_settings()
    strict = _strict_default() if strict is None else strict
    backend = (settings.embedding_backend or "local").strip().lower()

    try:
        if backend == "hash":
            return HashEmbedder()
        if backend == "local":
            return LocalEmbedder(settings.embedding_model, settings.embedding_model_path)
        if backend == "api":
            return APIEmbedder(
                settings.embedding_base_url,
                settings.embedding_api_key,
                settings.embedding_model,
            )
        raise EmbedderUnavailable(
            f"未知的嵌入后端 {backend!r}，合法取值：{' / '.join(KNOWN_BACKENDS)}"
        )
    except EmbedderUnavailable:
        raise
    except Exception as exc:
        if strict:
            raise EmbedderUnavailable(
                f"嵌入后端 {backend!r} 不可用：{exc}。"
                "严格模式下不回退 hash（回退会让召回质量静默退化）。"
                "离线部署请设 EMBEDDING_BACKEND=hash；"
                "要用本地权重请先 pypi 安装 sentence-transformers 并把权重放到 "
                "EMBEDDING_MODEL_PATH。"
            ) from exc
        logger.warning(
            "嵌入后端 %s 不可用（%s），按 EMBEDDING_STRICT=0 回退 hash 后端。", backend, exc
        )
        return HashEmbedder()


def describe_embedder(embedder: BaseEmbedder) -> dict:
    """给自检脚本用的可观测信息。"""
    return {
        "backend": getattr(embedder, "name", "unknown"),
        "signature": getattr(embedder, "signature", "unknown"),
    }


__all__ = ["EmbedderUnavailable", "build_embedder_strict", "describe_embedder"]
