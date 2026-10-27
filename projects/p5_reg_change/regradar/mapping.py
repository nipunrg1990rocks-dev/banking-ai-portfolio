"""Policy and control mapping: where in Ledgerline's rulebook does a regulatory provision land?

Retrieval reuses Project 1's hybrid retriever (BM25 + LSA embeddings fused with
Reciprocal Rank Fusion) over the same eight policies, so the two projects cite
sections identically. On top of section retrieval this module adds:

* sentence-level similarity (TF-IDF cosine with numbers masked), to find the
  exact policy sentence that speaks to a provision;
* parameter matching: for every number the regulator sets, the policy number
  of the same kind (deadline, frequency, minimum duration, threshold) whose
  surrounding words and sentence best match;
* term coverage: the share of the provision's distinctive words (weighted by
  rarity across the policy library) that the matched section contains.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from copilot.retriever import HybridRetriever  # noqa: E402,I001  (Project 1 package, path set by corpus)
from portfolio_core.text import split_sentences, tokenize

from .corpus import PolicySection, load_controls, policy_chunks, policy_sections
from .diff import GENERIC, mask_numbers
from .params import Quantity, comparable, context_similarity, extract_quantities


def _content_tokens(text: str) -> list[str]:
    """Tokens for sentence matching: rule boilerplate ("must", "every", "at least") and masked numbers removed,
    so two sentences match on what they are about, not on sharing "every NUM years"."""
    return [t for t in tokenize(text) if t not in GENERIC and t != "num"]


DOMAIN_PRIOR = 0.08  # a sanctions rule is more likely to land in the sanctions policy than in the AML policy


@dataclass(frozen=True)
class PolicySentence:
    ref: str
    index: int
    text: str
    quantities: tuple[Quantity, ...]


@dataclass
class SectionHit:
    section: PolicySection
    rrf: float
    dense: float
    rank: int
    best_sentence: PolicySentence | None = None
    sentence_sim: float = 0.0


@dataclass
class ParamMatch:
    required: Quantity
    policy: Quantity
    sentence: PolicySentence
    score: float
    context: float
    sentence_sim: float


class PolicyMatcher:
    def __init__(self) -> None:
        self.sections = {s.ref: s for s in policy_sections()}
        self.retriever = HybridRetriever(policy_chunks())
        self.sentences: dict[str, list[PolicySentence]] = {}
        for ref, s in self.sections.items():
            self.sentences[ref] = [PolicySentence(ref, i, t, tuple(extract_quantities(t)))
                                   for i, t in enumerate(split_sentences(s.text), 1)]
        all_sentences = [x.text for xs in self.sentences.values() for x in xs]
        self.vectorizer = TfidfVectorizer(tokenizer=_content_tokens, lowercase=False, token_pattern=None,
                                          ngram_range=(1, 2), sublinear_tf=True)
        self.vectorizer.fit([mask_numbers(t) for t in all_sentences])
        # Rarity of each word across policy sections: rare words carry the meaning of a requirement.
        doc_freq: Counter[str] = Counter()
        self.section_terms: dict[str, set[str]] = {}
        for ref, s in self.sections.items():
            terms = set(tokenize(f"{s.doc_title} {s.heading} {s.text}"))
            self.section_terms[ref] = terms
            doc_freq.update(terms)
        n = len(self.sections)
        self.idf = {t: math.log((1 + n) / (1 + df)) + 1 for t, df in doc_freq.items()}
        self.max_idf = math.log(1 + n) + 1

    # ---------------------------------------------------------------- retrieval
    @staticmethod
    def _ref_of(chunk) -> str:
        number = chunk.section.split(".", 1)[0].strip()
        return f"{chunk.doc_id} §{number}"

    def search(self, query: str, k: int = 5) -> list[SectionHit]:
        hits: dict[str, SectionHit] = {}
        for r in self.retriever.search(query, k=k * 2):
            ref = self._ref_of(r.chunk)
            if ref not in hits and ref in self.sections:
                hits[ref] = SectionHit(self.sections[ref], r.score, r.dense, len(hits) + 1)
            if len(hits) == k:
                break
        return list(hits.values())

    def sentence_similarities(self, text: str, sentences: list[PolicySentence]) -> np.ndarray:
        if not sentences:
            return np.zeros(0)
        q = self.vectorizer.transform([mask_numbers(text)])
        m = self.vectorizer.transform([mask_numbers(s.text) for s in sentences])
        return cosine_similarity(q, m)[0]

    def rank_sections(self, text: str, hits: list[SectionHit], home_doc: str | None = None) -> list[SectionHit]:
        """Attach each section's best-matching sentence, then order by sentence match blended with retrieval rank.

        ``home_doc`` is the policy that owns the circular's domain; its sections get a small prior.
        """
        for h in hits:
            sents = self.sentences[h.section.ref]
            sims = self.sentence_similarities(text, sents)
            if len(sims):
                i = int(np.argmax(sims))
                h.best_sentence, h.sentence_sim = sents[i], float(sims[i])
        return sorted(hits, key=lambda h: h.sentence_sim + 0.15 / h.rank
                      + (DOMAIN_PRIOR if h.section.doc_id == home_doc else 0.0), reverse=True)

    # ---------------------------------------------------------------- parameters
    def match_parameter(self, required: Quantity, text: str, hits: list[SectionHit],
                        min_score: float = 0.3, home_doc: str | None = None) -> list[ParamMatch]:
        """Policy quantities comparable with ``required``, best first, scored by context and sentence similarity."""
        out = []
        for h in hits:
            sents = self.sentences[h.section.ref]
            sims = self.sentence_similarities(text, sents)
            for sent, sim in zip(sents, sims, strict=True):
                for q in sent.quantities:
                    if not comparable(required, q):
                        continue
                    ctx = context_similarity(required, q)
                    score = (0.5 * ctx + 0.5 * float(sim) + 0.05 / h.rank
                             + (DOMAIN_PRIOR if h.section.doc_id == home_doc else 0.0))
                    if score >= min_score:
                        out.append(ParamMatch(required, q, sent, score, ctx, float(sim)))
        return sorted(out, key=lambda m: m.score, reverse=True)

    # ---------------------------------------------------------------- coverage
    def salient_terms(self, words: list[str]) -> list[str]:
        terms: list[str] = []
        for w in words:
            for t in tokenize(w):
                if t.isdigit() or t in GENERIC or t in terms:
                    continue
                terms.append(t)
        return terms

    def coverage(self, terms: list[str], refs: list[str]) -> tuple[float, list[str]]:
        """IDF-weighted share of ``terms`` found in the given sections, and the terms that are missing."""
        if not terms:
            return 1.0, []
        present = set().union(*(self.section_terms.get(r, set()) for r in refs)) if refs else set()
        total = got = 0.0
        missing = []
        for t in terms:
            w = self.idf.get(t, self.max_idf)
            total += w
            if t in present:
                got += w
            else:
                missing.append(t)
        return (got / total if total else 1.0), missing


@lru_cache(maxsize=1)
def default_matcher() -> PolicyMatcher:
    return PolicyMatcher()


def controls_for(refs: list[str]) -> pd.DataFrame:
    df = load_controls()
    return df[df["policy_ref"].isin(refs)].copy()
