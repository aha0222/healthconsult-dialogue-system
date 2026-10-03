"""嵌入后端严格构造测试（成员 B 交付）。

核心不变量：**缺依赖必须抛清晰错误，不得静默回退 hash**。
仓库原有的 `retriever.build_embedder()` 会静默回退，这里验证严格版本不会。
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.dialogue.retriever import HashEmbedder  # noqa: E402
from backend.app.providers.embedding import (  # noqa: E402
    EmbedderUnavailable, build_embedder_strict, describe_embedder,
)


def _s(**kw):
    base = {"embedding_backend": "hash"}
    base.update(kw)
    return Settings(**base)


def test_hash_backend_is_always_available():
    """hash 零依赖零下载，是离线档的推荐后端。"""
    emb = build_embedder_strict(_s(embedding_backend="hash"))
    assert isinstance(emb, HashEmbedder)
    assert describe_embedder(emb)["backend"] == "hash"


def test_hash_embedder_encode_shape():
    emb = build_embedder_strict(_s(embedding_backend="hash"))
    vecs = emb.encode(["我大便发黑", "睡不好觉"])
    assert len(vecs) == 2
    assert len(vecs[0]) == len(vecs[1])
    assert all(isinstance(x, float) for x in vecs[0][:5])


def test_local_backend_without_dependency_raises_not_falls_back(monkeypatch):
    """缺 sentence-transformers 时必须抛错，而不是悄悄变成 hash。"""
    monkeypatch.setenv("EMBEDDING_STRICT", "1")
    with pytest.raises(EmbedderUnavailable) as exc:
        build_embedder_strict(_s(embedding_backend="local"))
    msg = str(exc.value)
    assert "local" in msg
    assert "hash" in msg          # 提示里应给出可行出路
    assert "不回退" in msg


def test_api_backend_without_credentials_raises(monkeypatch):
    monkeypatch.setenv("EMBEDDING_STRICT", "1")
    with pytest.raises(EmbedderUnavailable) as exc:
        build_embedder_strict(_s(embedding_backend="api",
                                 embedding_base_url="", embedding_api_key=""))
    assert "api" in str(exc.value)


def test_unknown_backend_raises(monkeypatch):
    """未知值不再默默给个 hash。"""
    monkeypatch.setenv("EMBEDDING_STRICT", "1")
    with pytest.raises(EmbedderUnavailable) as exc:
        build_embedder_strict(_s(embedding_backend="magic"))
    assert "magic" in str(exc.value)


def test_non_strict_mode_restores_legacy_fallback(monkeypatch):
    """EMBEDDING_STRICT=0 时退回仓库原有的宽松行为，便于渐进接线。"""
    monkeypatch.setenv("EMBEDDING_STRICT", "0")
    emb = build_embedder_strict(_s(embedding_backend="local"))
    assert isinstance(emb, HashEmbedder)


def test_strict_defaults_on(monkeypatch):
    """默认必须是严格：否则离线档的硬约束形同虚设。"""
    monkeypatch.delenv("EMBEDDING_STRICT", raising=False)
    with pytest.raises(EmbedderUnavailable):
        build_embedder_strict(_s(embedding_backend="local"))
