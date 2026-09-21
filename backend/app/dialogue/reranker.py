"""Reranker：把 Retriever 召回的 Top-K 候选按"与当前问题的像不像"精排。

评分是纯本地的混合打分（0 token）：
    向量相似度（粗排分） + 字符 n-gram 重合 + 关键词/标签命中
重排后只保留 Top-N（默认 2~5）交给 LLM 作为少样本参考，避免把整库塞进 prompt。
"""

from .taxonomy import canonical_risk, normalize_scenes

DEFAULT_WEIGHTS = {
    "retrieval": 0.55,
    "ngram": 0.25,
    "keyword": 0.20,
}


def char_ngrams(text: str, n: int = 2) -> set:
    text = text or ""
    if len(text) < n:
        return {text} if text else set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def ngram_similarity(a: str, b: str, n: int = 2) -> float:
    """字符 n-gram Jaccard 相似度。"""
    ga, gb = char_ngrams(a, n), char_ngrams(b, n)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def keyword_score(query: str, item) -> float:
    """候选关键词命中率；无关键词时退回风险/场景代码的文本重合度。"""
    query = query or ""
    keywords = [k for k in (getattr(item, "keywords", None) or []) if k]
    if keywords:
        hits = sum(1 for k in keywords if k in query)
        return hits / len(keywords)
    label_terms = list(getattr(item, "scenes", None) or [])
    risk = getattr(item, "risk", "")
    if risk:
        label_terms.append(risk)
    if not label_terms:
        return 0.0
    hits = sum(1 for t in label_terms if t and t in query)
    return hits / len(label_terms)


def _label_boost(item, risk=None, scenes=None) -> float:
    """当前分类结果与候选标签一致时给小幅加分。"""
    boost = 0.0
    if risk and canonical_risk(getattr(item, "risk", "")) == canonical_risk(risk):
        boost += 0.5
    hint = set(normalize_scenes(scenes))
    item_scenes = set(normalize_scenes(getattr(item, "scenes", [])))
    if hint and item_scenes:
        boost += 0.5 * (len(hint & item_scenes) / len(hint))
    return boost


def rerank(query: str, candidates, top_n: int = 3, weights=None, risk=None, scenes=None) -> list:
    """对 [(CorpusItem, retrieval_score)] 精排，返回 [(CorpusItem, score)]。"""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    scored = []
    for item, retrieval in candidates or []:
        score = (
            weights["retrieval"] * float(retrieval or 0.0)
            + weights["ngram"] * ngram_similarity(query, getattr(item, "user", ""))
            + weights["keyword"] * keyword_score(query, item)
        )
        if risk or scenes:
            score += 0.05 * _label_boost(item, risk, scenes)
        scored.append((item, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[: max(0, top_n)]
