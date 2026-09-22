"""reranker.py 单元测试：字符 n-gram 与混合精排。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.dialogue.reranker import (
    char_ngrams,
    keyword_score,
    ngram_similarity,
    rerank,
)
from backend.app.dialogue.retriever import CorpusItem


def test_char_ngrams_and_similarity():
    assert char_ngrams("血压") == {"血压"}
    assert ngram_similarity("血压高", "血压高") == 1.0
    assert ngram_similarity("血压高", "胸口疼") < 0.2


def test_keyword_score_uses_keywords_then_labels():
    item = CorpusItem(id="C1", user="u", risk="R1", scenes=["S3"], keywords=["血压", "加药"])
    assert keyword_score("血压有点高，能加药吗", item) == 1.0
    assert keyword_score("胸口疼", item) == 0.0

    no_kw = CorpusItem(id="C2", user="u", risk="R3", scenes=["E1"])
    assert keyword_score("场景E1", no_kw) == 0.5


def test_rerank_prefers_hybrid_match():
    query = "血压这两天有点高"
    close = CorpusItem(id="C1", user="血压这两天有点高", risk="R1", scenes=["S3"], keywords=["血压"])
    far = CorpusItem(id="C2", user="有人敲门说查水表", risk="R3", scenes=["N1"])
    candidates = [(close, 0.5), (far, 0.9)]
    ranked = rerank(query, candidates, top_n=2)
    assert ranked[0][0].id == "C1"


def test_rerank_respects_top_n():
    items = [
        (CorpusItem(id=f"C{i}", user="血压高", risk="R1", scenes=["S3"]), 0.5)
        for i in range(5)
    ]
    assert len(rerank("血压高", items, top_n=2)) == 2
