"""Retriever：把可能相关的标注语料快速召回（粗排阶段）。

职责边界：
    Retriever 只负责"多召回"——用嵌入向量做粗排，返回 Top-K 候选；
    精排交给 reranker.py，最终只把 Top-N 交给 LLM。

嵌入后端可插拔，保证项目可移植：
    local  sentence-transformers 本地模型（默认，首次联网下载权重）
    api    OpenAI 兼容 /embeddings 接口（零下载）
    hash   纯 Python 字符 n-gram 哈希向量（零依赖零下载，离线/测试兜底）

语料为空或嵌入后端不可用时，retrieve 返回空列表，分类器自动退回
"关键词快路径 + LLM 兜底"，功能不受影响。
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings, get_settings
from .taxonomy import canonical_risk, normalize_scenes, strip_tags

logger = logging.getLogger("xiaonuan.retriever")


@dataclass
class CorpusItem:
    id: str
    user: str
    risk: str = ""
    scenes: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    source: str = ""
    assistant: str = ""

    @property
    def text(self) -> str:
        return self.user


def load_corpus(path) -> list:
    """载入 JSONL 语料；文件不存在或为空时返回空列表。"""
    path = Path(path)
    if not path.is_file():
        return []
    items = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("语料行解析失败，已跳过: %s", line[:60])
                continue
            user = str(row.get("user") or "").strip()
            assistant = str(row.get("assistant") or "").strip()
            if (not user or not assistant) and isinstance(row.get("messages"), list):
                for message in row["messages"]:
                    role = message.get("role")
                    content = str(message.get("content") or "").strip()
                    if role == "user" and not user:
                        user = content
                    elif role == "assistant" and not assistant:
                        assistant = content
            if not user:
                continue
            risk = row.get("risk") or row.get("risk_level")
            scenes = row.get("scenes") or row.get("expected_scenes")
            items.append(
                CorpusItem(
                    id=str(
                        row.get("id") or row.get("sample_id") or f"C{len(items) + 1:03d}"
                    ),
                    user=user,
                    risk=canonical_risk(risk),
                    scenes=normalize_scenes(scenes),
                    keywords=[str(k) for k in (row.get("keywords") or [])],
                    source=str(row.get("source") or ""),
                    assistant=strip_tags(assistant) if assistant else "",
                )
            )
    return items


def _l2_normalize(vec):
    norm = sum(v * v for v in vec) ** 0.5
    if norm <= 0:
        return vec
    return [v / norm for v in vec]


def cosine(a, b) -> float:
    """余弦相似度；零向量返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


class BaseEmbedder:
    name = "base"
    signature = "base"

    def encode(self, texts) -> list:
        raise NotImplementedError


class HashEmbedder(BaseEmbedder):
    """纯 Python 字符 n-gram 哈希向量：确定性、零依赖、零下载。"""

    name = "hash"

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.signature = f"hash:{dim}"

    def _vector(self, text: str) -> list:
        vec = [0.0] * self.dim
        text = text or ""
        for n in (1, 2, 3):
            for i in range(len(text) - n + 1):
                gram = text[i : i + n]
                digest = hashlib.md5(gram.encode("utf-8")).hexdigest()
                idx = int(digest, 16) % self.dim
                vec[idx] += 1.0
        return _l2_normalize(vec)

    def encode(self, texts) -> list:
        return [self._vector(t) for t in texts]


class LocalEmbedder(BaseEmbedder):
    """sentence-transformers 本地模型；权重在用户缓存目录，不进仓库。"""

    name = "local"

    def __init__(self, model: str, model_path: str = ""):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise RuntimeError(
                "未安装 sentence-transformers，请 pip install sentence-transformers，"
                "或改用 EMBEDDING_BACKEND=api / hash。"
            ) from exc

        target = model_path or model
        self._model = SentenceTransformer(target)
        self.signature = f"local:{target}"

    def encode(self, texts) -> list:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        return [list(map(float, v)) for v in vectors]


class APIEmbedder(BaseEmbedder):
    """OpenAI 兼容 /embeddings 接口，无需本地模型。"""

    name = "api"

    def __init__(self, base_url: str, api_key: str, model: str):
        if not base_url or not api_key:
            raise RuntimeError(
                "EMBEDDING_BACKEND=api 需要同时配置 EMBEDDING_BASE_URL 与 EMBEDDING_API_KEY。"
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise RuntimeError("需要安装 openai 库：pip install openai") from exc
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self.signature = f"api:{base_url}:{model}"

    def encode(self, texts) -> list:
        resp = self._client.embeddings.create(model=self._model, input=list(texts))
        return [list(map(float, d.embedding)) for d in resp.data]


def build_embedder(settings: Settings | None = None) -> BaseEmbedder:
    """按配置构造嵌入后端；local/api 不可用时回退 HashEmbedder，保证可移植。"""
    settings = settings or get_settings()
    backend = (settings.embedding_backend or "local").lower()
    try:
        if backend == "api":
            return APIEmbedder(
                settings.embedding_base_url,
                settings.embedding_api_key,
                settings.embedding_model,
            )
        if backend == "local":
            return LocalEmbedder(
                settings.embedding_model, settings.embedding_model_path
            )
        if backend == "hash":
            return HashEmbedder()
    except Exception as exc:
        logger.warning("嵌入后端 %s 不可用（%s），回退 hash 后端。", backend, exc)
    return HashEmbedder()


def _corpus_digest(items) -> str:
    h = hashlib.md5()
    for item in items:
        h.update(item.id.encode("utf-8"))
        h.update(b"\x00")
        h.update(item.text.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class Retriever:
    """基于嵌入向量的粗排检索器。"""

    def __init__(self, corpus=None, embedder=None, cache_dir=None):
        self.corpus = list(corpus or [])
        self.embedder = embedder or HashEmbedder()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._vectors = None
        self._digest = None

    def _cache_path(self):
        if not self.cache_dir or self._digest is None:
            return None
        safe = self.embedder.signature.replace("/", "_").replace(":", "_")
        return self.cache_dir / f"{safe}_{self._digest}.json"

    def _load_cached(self):
        path = self._cache_path()
        if not path or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            vectors = [data[item.id] for item in self.corpus]
            return vectors
        except (KeyError, ValueError, OSError):
            return None

    def _save_cache(self, vectors):
        path = self._cache_path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {item.id: vec for item, vec in zip(self.corpus, vectors)}
            path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:  # 缓存失败不影响主流程
            logger.warning("写入向量缓存失败: %s", exc)

    def ensure_vectors(self):
        """惰性计算语料向量，优先读缓存。"""
        if self._vectors is not None:
            return self._vectors
        if not self.corpus:
            self._vectors = []
            return self._vectors

        self._digest = _corpus_digest(self.corpus)
        cached = self._load_cached()
        if cached is not None and len(cached) == len(self.corpus):
            self._vectors = cached
            return self._vectors

        self._vectors = self.embedder.encode([item.text for item in self.corpus])
        self._save_cache(self._vectors)
        return self._vectors

    def retrieve(self, query: str, top_k: int = 10) -> list:
        """返回 [(CorpusItem, score)]，按相似度降序；语料为空时返回 []。"""
        if not self.corpus:
            return []
        query = (query or "").strip()
        if not query:
            return []
        vectors = self.ensure_vectors()
        query_vec = self.embedder.encode([query])[0]
        scored = [
            (item, cosine(query_vec, vec))
            for item, vec in zip(self.corpus, vectors)
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[: max(0, top_k)]
