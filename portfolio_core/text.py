"""Small text utilities shared by retrieval code."""

from __future__ import annotations

import re
from functools import lru_cache

from nltk.stem.snowball import SnowballStemmer

_STEMMER = SnowballStemmer("english")

STOPWORDS = frozenset(
    """a an and are as at be by can do does for from has have how i if in is it its
    me must my of on or our should that the their them then there these they this to
    under was we what when where which who why will with within would you your any all
    may be been being into per than also not no were had did done""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")


@lru_cache(maxsize=50_000)
def stem(token: str) -> str:
    return _STEMMER.stem(token)


def tokenize(text: str, drop_stopwords: bool = True, stemmed: bool = True) -> list[str]:
    """Lower-case word tokens; stemmed by default so "filed", "filing" and "file" match."""
    tokens = _TOKEN_RE.findall(text.lower())
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [stem(t) for t in tokens] if stemmed else tokens


def split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        parts.extend(s.strip() for s in _SENT_RE.split(line) if s.strip())
    return parts
