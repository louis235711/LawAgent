"""BM25 sparse retrieval for legal knowledge base with jieba tokenization."""
from collections import defaultdict
import math
import jieba


class BM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: list[str] = []
        self._tokenized: list[list[str]] = []
        self._doc_len: list[int] = []
        self._avgdl: float = 0.0
        self._df: dict[str, int] = defaultdict(int)  # document frequency
        self._idf: dict[str, float] = {}
        self._meta: list[dict] = []  # per-document metadata (law_name, chapter, etc.)
        self._built = False

    def index(self, documents: list[str], metadata: list[dict] | None = None):
        """Build BM25 index from documents with optional metadata."""
        self.documents = documents
        self._meta = metadata or [{}] * len(documents)
        self._tokenized = []
        self._doc_len = []
        total_len = 0
        self._df.clear()

        for doc in documents:
            tokens = list(jieba.cut(doc))
            self._tokenized.append(tokens)
            self._doc_len.append(len(tokens))
            total_len += len(tokens)
            for token in set(tokens):
                self._df[token] += 1

        self._avgdl = total_len / max(len(documents), 1)

        # Precompute IDF
        n = len(documents)
        self._idf = {
            term: math.log((n - freq + 0.5) / (freq + 0.5) + 1.0)
            for term, freq in self._df.items()
        }

        self._built = True

    def get_meta(self, doc_idx: int) -> dict:
        """Return metadata for a document by index."""
        if 0 <= doc_idx < len(self._meta):
            return self._meta[doc_idx]
        return {}

    def search(self, query: str, top_k: int = 15) -> list[tuple[int, float]]:
        """Search and return [(doc_index, score), ...] sorted descending."""
        if not self._built:
            return []

        query_tokens = list(jieba.cut(query))
        scores = []

        for idx in range(len(self.documents)):
            score = self._score(query_tokens, idx)
            if score > 0:
                scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _score(self, query_tokens: list[str], doc_idx: int) -> float:
        doc_tokens = self._tokenized[doc_idx]
        doc_len = self._doc_len[doc_idx]
        tf = defaultdict(int)
        for t in doc_tokens:
            tf[t] += 1

        score = 0.0
        for term in query_tokens:
            if term not in self._idf:
                continue
            idf = self._idf[term]
            term_freq = tf.get(term, 0)
            numerator = term_freq * (self.k1 + 1.0)
            denominator = term_freq + self.k1 * (1.0 - self.b + self.b * doc_len / max(self._avgdl, 1.0))
            score += idf * numerator / denominator
        return score


# Singleton for legal knowledge
_legal_bm25: BM25 | None = None


def get_legal_bm25() -> BM25:
    global _legal_bm25
    if _legal_bm25 is None:
        _legal_bm25 = BM25()
    return _legal_bm25


def build_legal_bm25(documents: list[str], metadata: list[dict] | None = None):
    bm25 = get_legal_bm25()
    bm25.index(documents, metadata)


def reset_legal_bm25():
    global _legal_bm25
    _legal_bm25 = None
