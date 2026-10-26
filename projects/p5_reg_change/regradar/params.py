"""Quantity extraction: the numbers that make a rule a rule.

Regulatory change is mostly about parameters: "30 calendar days" becomes
"15 calendar days", "25 percent" becomes "10 percent", "annually" becomes
"every six months". This module finds those quantities in a sentence,
normalises them to a common base (days, percent, US dollars) and records what
*kind* of limit each one is, because the kind decides which direction is
stricter:

* ``deadline``      "within 15 days", "no later than", "not exceed"  -> lower is stricter
* ``frequency``     "every 12 months", "annually", "quarterly"       -> lower is stricter
* ``min_duration``  "retain for at least five years"                -> higher is stricter
* ``threshold``     "25 percent or more", "losses of 5,000 or more"  -> lower is stricter (captures more)

Everything here is deterministic and explainable: an interviewer can ask why a
gap was raised and the answer is a comparison of two numbers with their source
sentences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from portfolio_core.text import tokenize

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18, "twenty": 20, "thirty": 30,
    "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "seventy-two": 72, "ninety": 90,
}
_NUM = r"(?P<num>\d[\d,]*(?:\.\d+)?|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")"
_SCALE = r"(?:\s+(?P<scale>million|thousand))?"

TIME_UNITS = {"hour": 1 / 24, "day": 1.0, "week": 7.0, "month": 30.4, "year": 365.0}
DAY_QUALIFIERS = {"business": 7 / 5, "working": 7 / 5, "calendar": 1.0}

_TIME_RE = re.compile(_NUM + r"\s+(?P<qual>calendar\s+|business\s+|working\s+)?(?P<unit>hour|day|week|month|year)s?\b",
                      re.IGNORECASE)
_PERCENT_RE = re.compile(_NUM + r"\s*(?:percent\b|per cent\b|%)", re.IGNORECASE)
_MONEY_RE = re.compile(_NUM + _SCALE + r"\s+(?:US\s+)?dollars\b", re.IGNORECASE)
_MONEY_RE2 = re.compile(r"(?:USD|US\$|\$)\s*(?P<num>\d[\d,]*(?:\.\d+)?)(?:\s+(?P<scale>million|thousand))?",
                        re.IGNORECASE)
# Frequency words with no number in them.
FREQUENCY_WORDS = {
    r"\b(?:annually|annual|yearly|once (?:a|per|each) year|each year|every year)\b": 365.0,
    r"\b(?:semi-annually|half-yearly|twice a year)\b": 182.5,
    r"\b(?:quarterly|once (?:a|per|each) quarter|every quarter)\b": 91.25,
    r"\b(?:monthly|once (?:a|per|each) month|every month)\b": 30.4,
    r"\b(?:weekly|once (?:a|per|each) week|every week)\b": 7.0,
    r"\bdaily\b": 1.0,
}
_SAME_DAY_RE = re.compile(r"\bsame business day\b", re.IGNORECASE)

KINDS = ("deadline", "frequency", "min_duration", "threshold")
LOWER_IS_STRICTER = {"deadline": True, "frequency": True, "threshold": True, "min_duration": False}


@dataclass(frozen=True)
class Quantity:
    text: str            # as written, e.g. "15 calendar days"
    value: float         # the number as written (15)
    unit: str            # "day", "percent", "usd", ...
    dimension: str       # "time", "percent" or "money"
    base: float          # normalised value: days, percent or US dollars
    kind: str            # one of KINDS
    start: int
    end: int
    context: tuple[str, ...]  # stemmed words around the number, used to pair quantities

    @property
    def label(self) -> str:
        return f"{self.text} ({self.kind.replace('_', ' ')})"


def _to_number(raw: str, scale: str | None = None) -> float:
    raw = raw.lower()
    value = float(NUMBER_WORDS[raw]) if raw in NUMBER_WORDS else float(raw.replace(",", ""))
    if scale:
        value *= 1_000_000 if scale.lower() == "million" else 1_000
    return value


def _kind(sentence: str, start: int, end: int, dimension: str) -> str:
    before = sentence[max(0, start - 60):start].lower()
    after = sentence[end:end + 40].lower()
    if dimension == "time":
        if re.search(r"\bevery\s*$|\beach\s*$|\bevery\b[^.]{0,12}$", before) or re.search(
                r"^\s*(?:cycle|basis)", after):
            return "frequency"
        if re.search(r"(?:within|no later than|not exceed|exceed|more than|before|by)\s*$", before):
            return "deadline"
        if re.search(r"(?:at least|minimum of|for|retained|retain|period of)\s*$", before):
            return "min_duration"
        if re.search(r"\b(?:retain|retention|retained|kept|stored)\b", before):
            return "min_duration"
        return "deadline"
    return "threshold"


def _window(sentence: str, start: int, end: int, before_words: int = 10, after_words: int = 6) -> tuple[str, ...]:
    pre = tokenize(sentence[:start])[-before_words:]
    post = tokenize(sentence[end:])[:after_words]
    unitish = {"day", "hour", "month", "year", "week", "calendar", "busi", "percent", "dollar", "us"}
    return tuple(t for t in pre + post if not t.isdigit() and t not in unitish and t not in NUMBER_WORDS)


def _raw_quantities(sentence: str) -> list[Quantity]:
    found: list[Quantity] = []
    taken: list[tuple[int, int]] = []

    def free(s: int, e: int) -> bool:
        return all(e <= a or s >= b for a, b in taken)

    for m in _TIME_RE.finditer(sentence):
        value = _to_number(m.group("num"))
        unit = m.group("unit").lower()
        factor = TIME_UNITS[unit]
        qual = (m.group("qual") or "").strip().lower()
        if unit == "day" and qual:
            factor *= DAY_QUALIFIERS[qual]
        kind = _kind(sentence, m.start(), m.end(), "time")
        found.append(Quantity(m.group(0), value, f"{qual + ' ' if qual else ''}{unit}", "time", value * factor, kind,
                              m.start(), m.end(), _window(sentence, m.start(), m.end())))
        taken.append((m.start(), m.end()))
    for m in _PERCENT_RE.finditer(sentence):
        if not free(m.start(), m.end()):
            continue
        value = _to_number(m.group("num"))
        found.append(Quantity(m.group(0), value, "percent", "percent", value, "threshold", m.start(), m.end(),
                              _window(sentence, m.start(), m.end())))
        taken.append((m.start(), m.end()))
    for rx in (_MONEY_RE, _MONEY_RE2):
        for m in rx.finditer(sentence):
            if not free(m.start(), m.end()):
                continue
            num = m.group("num")
            if num is None:
                continue
            value = _to_number(num, m.group("scale"))
            found.append(Quantity(m.group(0), value, "usd", "money", value, "threshold", m.start(), m.end(),
                                  _window(sentence, m.start(), m.end())))
            taken.append((m.start(), m.end()))
    for pattern, days in FREQUENCY_WORDS.items():
        for m in re.finditer(pattern, sentence, re.IGNORECASE):
            if not free(m.start(), m.end()):
                continue
            found.append(Quantity(m.group(0), days, "frequency word", "time", days, "frequency", m.start(), m.end(),
                                  _window(sentence, m.start(), m.end())))
            taken.append((m.start(), m.end()))
    for m in _SAME_DAY_RE.finditer(sentence):
        if free(m.start(), m.end()):
            found.append(Quantity(m.group(0), 1, "day", "time", 1.0, "deadline", m.start(), m.end(),
                                  _window(sentence, m.start(), m.end())))
    return sorted(found, key=lambda q: q.start)


def extract_quantities(sentence: str) -> list[Quantity]:
    """Every time, percent and money quantity in ``sentence``, left to right.

    Each quantity's context stops at its neighbours, so in "High risk every 12 months, Medium
    risk every 24 months" the 24 is described by "Medium risk", not by "High risk".
    """
    raw = _raw_quantities(sentence)
    out = []
    for i, q in enumerate(raw):
        lo = raw[i - 1].end if i else 0
        hi = raw[i + 1].start if i + 1 < len(raw) else len(sentence)
        ctx = _window(sentence[lo:hi], q.start - lo, q.end - lo)
        out.append(Quantity(q.text, q.value, q.unit, q.dimension, q.base, q.kind, q.start, q.end, ctx))
    return out


def context_similarity(a: Quantity, b: Quantity) -> float:
    """Overlap of the words around two quantities (0..1).

    Pairs "high risk ... 12 months" in one text with "High risk ... 12 months" in another.
    """
    sa, sb = set(a.context), set(b.context)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def comparable(a: Quantity, b: Quantity) -> bool:
    return a.dimension == b.dimension and a.kind == b.kind


def meets(policy: Quantity, required: Quantity, tolerance: float = 0.01) -> bool:
    """True if the policy quantity is at least as strict as the required one."""
    if LOWER_IS_STRICTER[required.kind]:
        return policy.base <= required.base * (1 + tolerance)
    return policy.base >= required.base * (1 - tolerance)


def direction(old: Quantity, new: Quantity) -> str:
    """'tightened', 'loosened' or 'unchanged' for a parameter that moved from old to new."""
    if abs(old.base - new.base) <= 1e-9 * max(1.0, abs(old.base)):
        return "unchanged"
    stricter = new.base < old.base if LOWER_IS_STRICTER[new.kind] else new.base > old.base
    return "tightened" if stricter else "loosened"


def pair_quantities(old: list[Quantity], new: list[Quantity], min_context: float = 0.0
                    ) -> list[tuple[Quantity, Quantity, float]]:
    """One-to-one pairing of comparable quantities by surrounding words, then by position."""
    scored = []
    for i, a in enumerate(old):
        for j, b in enumerate(new):
            if not comparable(a, b):
                continue
            sim = context_similarity(a, b)
            # Position breaks ties: the 2nd number in the old sentence most likely became the 2nd in the new one.
            scored.append((sim - 0.01 * abs(i - j), i, j, sim))
    scored.sort(reverse=True)
    used_a, used_b, pairs = set(), set(), []
    for _, i, j, sim in scored:
        if i in used_a or j in used_b or sim < min_context:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((old[i], new[j], sim))
    return sorted(pairs, key=lambda p: p[1].start)


def describe_value(q: Quantity) -> str:
    """Readable value for comparisons, e.g. '30 calendar days' or 'annually (365 days)'."""
    if q.unit == "frequency word":
        return f"{q.text} (about every {q.base:.0f} days)"
    return q.text


def phrase(q: Quantity) -> str:
    """The quantity as a requirement: 'every two years', 'within 4 hours', 'at least seven years', '10 percent'."""
    if q.unit == "frequency word" or q.dimension != "time":
        return q.text
    prefix = {"frequency": "every", "deadline": "within", "min_duration": "at least"}[q.kind]
    return f"{prefix} {q.text}"


def replace_quantity_text(sentence: str, old: Quantity, new_text: str) -> str:
    """Swap one quantity in its sentence; used to draft policy redlines."""
    return sentence[:old.start] + new_text + sentence[old.end:]
