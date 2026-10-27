"""Change comparison: what exactly did the regulator change?

1. Every provision of the new circular is aligned with at most one provision of
   the version it replaces. Similarity is TF-IDF cosine on the text with numbers
   masked (so "30 days" -> "15 days" still aligns), plus a bonus when both sit in
   a section with the same title. Pairs are taken greedily, best first.
2. Each provision is then classed:
   * UNCHANGED  same wording
   * AMENDED    aligned but reworded; parameter moves are listed with their direction
   * NEW        no counterpart in the old version
   * REMOVED    an old provision with no counterpart in the new version
3. AMENDED provisions get a word-level redline and the list of *inserted* words,
   which is what the policy must now cover.
"""

from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass, field

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from portfolio_core.text import tokenize

from .corpus import CircularPair, Provision
from .params import NUMBER_WORDS, Quantity, direction, extract_quantities, pair_quantities, phrase

NEW, AMENDED, UNCHANGED, REMOVED = "NEW", "AMENDED", "UNCHANGED", "REMOVED"
CHANGE_TYPES = (NEW, AMENDED, UNCHANGED, REMOVED)
ALIGN_THRESHOLD = 0.45

_NUMERIC = re.compile(r"\b(?:\d[\d,]*(?:\.\d+)?|" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)


def mask_numbers(text: str) -> str:
    return _NUMERIC.sub(" NUM ", text)


@dataclass
class ParameterChange:
    old: Quantity
    new: Quantity
    direction: str  # tightened | loosened | unchanged

    @property
    def summary(self) -> str:
        arrow = {"tightened": "▼ tightened", "loosened": "▲ loosened"}.get(self.direction, "unchanged")
        if self.new.kind == "frequency":
            return f"{phrase(self.old)} → {phrase(self.new)} ({arrow})"
        return f"{self.old.text} → {self.new.text} ({arrow})"


@dataclass
class Change:
    change_type: str
    current: Provision | None
    previous: Provision | None
    similarity: float = 0.0
    parameter_changes: list[ParameterChange] = field(default_factory=list)
    new_parameters: list[Quantity] = field(default_factory=list)   # quantities with no old counterpart
    inserted_words: list[str] = field(default_factory=list)
    deleted_words: list[str] = field(default_factory=list)

    @property
    def provision(self) -> Provision:
        return self.current or self.previous  # type: ignore[return-value]

    @property
    def pid(self) -> str:
        return self.provision.pid

    @property
    def materiality(self) -> str:
        """How much the change matters before looking at the bank's policies."""
        if self.change_type == REMOVED:
            return "Low"
        if self.change_type == UNCHANGED:
            return "None"
        if self.change_type == NEW:
            return "High" if self.provision.modality in ("obligation", "prohibition") else "Medium"
        if any(p.direction == "tightened" for p in self.parameter_changes):
            return "High"
        if self.new_parameters or len(self.inserted_words) >= 2:
            return "Medium"
        return "Low"

    @property
    def headline(self) -> str:
        if self.change_type == AMENDED and self.parameter_changes:
            moved = [p for p in self.parameter_changes if p.direction != "unchanged"]
            if moved:
                return "; ".join(p.summary for p in moved)
        if self.change_type == AMENDED and (self.inserted_words or self.new_parameters):
            added = [phrase(q) for q in self.new_parameters] or self.inserted_words
            return "adds: " + ", ".join(added[:6])
        return {NEW: "new requirement", REMOVED: "requirement withdrawn", UNCHANGED: "no change"}.get(
            self.change_type, "reworded")


def _word_diff(old: str, new: str) -> tuple[list[str], list[str], str]:
    """Inserted words, deleted words and an HTML redline (deleted struck through, inserted highlighted)."""
    a, b = old.split(), new.split()
    sm = difflib.SequenceMatcher(a=[w.lower().strip(".,;:") for w in a], b=[w.lower().strip(".,;:") for w in b],
                                 autojunk=False)
    inserted, deleted, out = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.append(html.escape(" ".join(b[j1:j2])))
            continue
        if op in ("replace", "delete"):
            deleted += a[i1:i2]
            out.append(f"<del>{html.escape(' '.join(a[i1:i2]))}</del>")
        if op in ("replace", "insert"):
            inserted += b[j1:j2]
            out.append(f"<ins>{html.escape(' '.join(b[j1:j2]))}</ins>")
    return inserted, deleted, " ".join(out)


def redline_html(change: Change) -> str:
    if change.change_type == AMENDED and change.previous and change.current:
        return _word_diff(change.previous.text, change.current.text)[2]
    if change.change_type == NEW:
        return f"<ins>{html.escape(change.provision.text)}</ins>"
    if change.change_type == REMOVED:
        return f"<del>{html.escape(change.provision.text)}</del>"
    return html.escape(change.provision.text)


GENERIC = {"day", "hour", "month", "year", "week", "calendar", "busi", "percent", "dollar", "us", "num", "least",
           "everi", "each", "within", "than", "more", "after", "later", "firm", "must", "shall", "requir"}


def content_words(words: list[str]) -> list[str]:
    """Meaningful words (as written, lower case): no stop words, numbers, units or rule boilerplate."""
    out: list[str] = []
    for w in words:
        clean = w.lower().strip(".,;:()'\"")
        stems = tokenize(clean)
        if not stems or clean in out or clean in NUMBER_WORDS:
            continue
        if all(t.isdigit() or t in GENERIC for t in stems):
            continue
        out.append(clean)
    return out


def _align(prev: list[Provision], curr: list[Provision]) -> list[tuple[int, int, float]]:
    if not prev or not curr:
        return []
    texts = [mask_numbers(p.text) for p in prev + curr]
    vec = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, ngram_range=(1, 2),
                          sublinear_tf=True)
    m = vec.fit_transform(texts)
    sims = cosine_similarity(m[len(prev):], m[:len(prev)])
    scored = []
    for j, c in enumerate(curr):
        for i, p in enumerate(prev):
            s = float(sims[j, i]) + (0.1 if p.section_title.lower() == c.section_title.lower() else 0.0)
            scored.append((s, i, j))
    scored.sort(reverse=True)
    used_p, used_c, pairs = set(), set(), []
    for s, i, j in scored:
        if s < ALIGN_THRESHOLD or i in used_p or j in used_c:
            continue
        used_p.add(i)
        used_c.add(j)
        pairs.append((i, j, min(s, 1.0)))
    return pairs


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def compare(pair: CircularPair) -> list[Change]:
    """All changes from ``pair.previous`` to ``pair.current``, in the order of the new circular (removed last)."""
    curr = [p for p in pair.current.provisions]
    prev = [p for p in pair.previous.provisions] if pair.previous else []
    # The boilerplate disclaimer under the title is not a provision; sections only.
    matched = {j: (i, s) for i, j, s in _align(prev, curr)}
    changes: list[Change] = []
    for j, c in enumerate(curr):
        if j not in matched:
            changes.append(Change(NEW, c, None, 0.0, new_parameters=extract_quantities(c.text),
                                  inserted_words=content_words(c.text.split())))
            continue
        i, sim = matched[j]
        p = prev[i]
        if _norm(p.text) == _norm(c.text):
            changes.append(Change(UNCHANGED, c, p, sim))
            continue
        inserted, deleted, _ = _word_diff(p.text, c.text)
        old_q, new_q = extract_quantities(p.text), extract_quantities(c.text)
        pairs = pair_quantities(old_q, new_q)
        paired_new = {id(b) for _, b, _ in pairs}
        param_changes = [ParameterChange(a, b, direction(a, b)) for a, b, _ in pairs]
        new_params = [q for q in new_q if id(q) not in paired_new]
        changes.append(Change(AMENDED, c, p, sim, param_changes, new_params, content_words(inserted),
                              content_words(deleted)))
    used_prev = {i for i, _ in matched.values()}
    for i, p in enumerate(prev):
        if i not in used_prev:
            changes.append(Change(REMOVED, None, p, 0.0, deleted_words=content_words(p.text.split())))
    return changes
