"""retriever.py 单元测试：嵌入后端、语料加载、检索与向量缓存。"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.retriever import (
    CorpusItem,
    HashEmbedder,
    Retriever,
    cosine,
    load_corpus,
)


def _write_corpus(path):
    rows = [
        {"id": "C001", "user": "血压这两天有点高", "risk": "R1", "scenes": ["S3"]},
        {"id": "C002", "user": "胸口闷得慌喘不上气", "risk": "R3", "scenes": ["E1"]},
        {"id": "C003", "user": "有人敲门说查水表", "risk": "R3", "scenes": ["N1"]},
    ]
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_hash_embedder_deterministic_and_normalized():
    embedder = HashEmbedder(dim=64)
    v1 = embedder.encode(["血压有点高"])[0]
    v2 = embedder.encode(["血压有点高"])[0]
    assert v1 == v2
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_cosine_bounds():
    embedder = HashEmbedder(dim=64)
    a = embedder.encode(["血压高"])[0]
    b = embedder.encode(["血压高"])[0]
    assert abs(cosine(a, b) - 1.0) < 1e-6
    assert cosine(a, []) == 0.0


def test_load_corpus_missing_and_blank(tmp_path):
    assert load_corpus(tmp_path / "none.jsonl") == []
    blank = tmp_path / "blank.jsonl"
    blank.write_text("\n\n", encoding="utf-8")
    assert load_corpus(blank) == []


def test_load_corpus_normalizes(tmp_path):
    path = tmp_path / "c.jsonl"
    _write_corpus(path)
    items = load_corpus(path)
    assert [item.id for item in items] == ["C001", "C002", "C003"]
    assert items[0].scenes == ["S3"]


def test_retrieve_ranks_relevant_first(tmp_path):
    path = tmp_path / "c.jsonl"
    _write_corpus(path)
    retriever = Retriever(load_corpus(path), HashEmbedder())
    results = retriever.retrieve("我血压有点高，怎么办", top_k=3)
    assert results
    assert results[0][0].id == "C001"


def test_retrieve_empty_corpus():
    retriever = Retriever([], HashEmbedder())
    assert retriever.retrieve("随便问问") == []


def test_retrieve_uses_cache(tmp_path):
    path = tmp_path / "c.jsonl"
    _write_corpus(path)
    cache_dir = tmp_path / "cache"
    retriever = Retriever(load_corpus(path), HashEmbedder(), cache_dir)
    retriever.ensure_vectors()
    cached_files = list(cache_dir.glob("*.json"))
    assert len(cached_files) == 1

    reused = Retriever(load_corpus(path), HashEmbedder(), cache_dir)
    reused._digest = retriever._digest
    assert reused._load_cached() is not None
