"""Hybrid retrieval over policy chunks.

Two complementary rankers are fused with Reciprocal Rank Fusion (RRF):

1. **BM25** (keyword relevance): good for exact terms such as "CTR" or "85 percent".
2. **Dense semantic vectors**: TF-IDF features projected with truncated SVD
   (Latent Semantic Analysis) into a low-dimensional embedding space and
   compared with cosine similarity. This captures related wording such as
   "beneficial owner" vs "ownership" without any external embedding service.

Banking acronyms in the question are expanded first ("PEP" -> "politically
exposed person") so short questions still match the full policy wording.

The embedder is deliberately local: the app runs offline, free and
deterministically. A hosted embedding model (e.g. Databricks Vector Search)
can replace ``DenseIndex`` behind the same interface.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from portfolio_core.text import tokenize

from .documents import Chunk

ACRONYMS: dict[str, str] = {
    "kyc": "know your customer customer due diligence",
    "cdd": "customer due diligence",
    "edd": "enhanced due diligence",
    "sdd": "simplified due diligence",
    "pep": "politically exposed person",
    "peps": "politically exposed persons",
    "ubo": "beneficial owner ownership",
    "aml": "anti-money laundering",
    "sar": "suspicious activity report",
    "sars": "suspicious activity reports",
    "ctr": "currency transaction report cash",
    "cip": "customer identification program",
    "cet1": "common equity tier 1 capital ratio",
    "lcr": "liquidity coverage ratio",
    "npl": "non-performing loans",
    "rcsa": "risk and control self-assessment",
    "kri": "key risk indicator",
    "kris": "key risk indicators",
    "cro": "chief risk officer",
    "ofac": "sanctions list screening",
    "ai": "artificial intelligence model",
    "llm": "large language model generative ai",
    "genai": "generative ai",
    "ras": "risk appetite statement",
    "tprm": "third-party risk management vendor",
}


# Everyday words employees use, mapped to the wording policies use.
SYNONYMS: dict[str, str] = {
    "tell": "disclose", "inform": "disclose", "notify": "disclose report",
    "deadline": "no later than days", "timeline": "within days", "quickly": "within hours",
    "vendor": "third party", "vendors": "third parties", "supplier": "third party",
    "owner": "ownership", "owners": "ownership", "blocked": "block frozen",
    "keep": "retain retention", "stored": "retain retention", "retained": "retention",
}


def expand_query(query: str) -> str:
    words = tokenize(query, drop_stopwords=False, stemmed=False)
    extra = [ACRONYMS[t] for t in words if t in ACRONYMS] + [SYNONYMS[t] for t in words if t in SYNONYMS]
    return query if not extra else f"{query} {' '.join(extra)}"


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float          # fused RRF score (rank-based)
    bm25: float           # raw BM25 score
    dense: float          # cosine similarity in [-1, 1]
    rank: int


class DenseIndex:
    """LSA embeddings: TF-IDF -> truncated SVD -> L2-normalised vectors."""

    def __init__(self, texts: list[str], dims: int = 128, seed: int = 7) -> None:
        self.vectorizer = TfidfVectorizer(
            tokenizer=tokenize, lowercase=False, token_pattern=None,
            ngram_range=(1, 2), sublinear_tf=True, min_df=1,
        )
        tfidf = self.vectorizer.fit_transform(texts)
        n_components = max(2, min(dims, tfidf.shape[0] - 1, tfidf.shape[1] - 1))
        self.svd = TruncatedSVD(n_components=n_components, random_state=seed)
        self.matrix = normalize(self.svd.fit_transform(tfidf))

    def embed(self, text: str) -> np.ndarray:
        return normalize(self.svd.transform(self.vectorizer.transform([text])))[0]

    def similarities(self, text: str) -> np.ndarray:
        return self.matrix @ self.embed(text)


class HybridRetriever:
    def __init__(self, chunks: list[Chunk], rrf_k: int = 60) -> None:
        if not chunks:
            raise ValueError("cannot build an index with no chunks")
        self.chunks = list(chunks)
        self.rrf_k = rrf_k
        corpus = [c.search_text for c in self.chunks]
        self.bm25 = BM25Okapi([tokenize(t) for t in corpus])
        self.dense = DenseIndex(corpus)

    def search(self, query: str, k: int = 5, domains: list[str] | None = None) -> list[RetrievedChunk]:
        expanded = expand_query(query)
        bm25_scores = np.asarray(self.bm25.get_scores(tokenize(expanded)))
        dense_scores = self.dense.similarities(expanded)

        allowed = np.array([not domains or c.domain in domains for c in self.chunks])
        bm25_order = [i for i in np.argsort(-bm25_scores) if allowed[i]]
        dense_order = [i for i in np.argsort(-dense_scores) if allowed[i]]

        fused: dict[int, float] = {}
        for order in (bm25_order, dense_order):
            for rank, idx in enumerate(order):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (self.rrf_k + rank + 1)

        best = sorted(fused, key=lambda i: fused[i], reverse=True)[:k]
        return [
            RetrievedChunk(self.chunks[i], fused[i], float(bm25_scores[i]), float(dense_scores[i]), r + 1)
            for r, i in enumerate(best)
        ]
