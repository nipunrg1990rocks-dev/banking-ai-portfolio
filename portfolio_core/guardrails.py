"""Input guardrails shared by the portfolio's GenAI projects.

These are deterministic, explainable checks that run before any text is sent
to retrieval or to a language model:

* PII detection and redaction (so personal data never reaches a model or a log)
* Prompt-injection pattern detection
* Basic input validation (empty / too long)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_QUESTION_CHARS = 1000

# Order matters: more specific patterns first so a card number is not also
# reported as an account number.
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("CARD_NUMBER", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("UK_NINO", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b", re.IGNORECASE)),
    ("PHONE", re.compile(r"(?<!\w)\+?\d{1,3}[ -]?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b")),
    ("ACCOUNT_NUMBER", re.compile(r"\b\d{8,12}\b")),
]

INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)",
        r"disregard (all |the )?(previous|prior|above|system)",
        r"you are now",
        r"act as (an? )?(unfiltered|jailbroken|dan)",
        r"reveal (your|the) (system )?prompt",
        r"system prompt",
        r"developer mode",
        r"bypass (the )?(guardrails|controls|policy)",
    ]
]


@dataclass
class GuardrailResult:
    original: str
    sanitized: str
    pii_found: list[str] = field(default_factory=list)
    injection_detected: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.errors) or self.injection_detected


def redact_pii(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    redacted = text
    for label, pattern in PII_PATTERNS:
        if pattern.search(redacted):
            found.append(label)
            redacted = pattern.sub(f"[REDACTED_{label}]", redacted)
    return redacted, found


def detect_injection(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS)


def check_input(text: str, max_chars: int = MAX_QUESTION_CHARS) -> GuardrailResult:
    text = (text or "").strip()
    result = GuardrailResult(original=text, sanitized=text)
    if not text:
        result.errors.append("Please enter a question.")
        return result
    if len(text) > max_chars:
        result.errors.append(f"Question is too long ({len(text)} characters; limit {max_chars}).")
        return result
    result.sanitized, result.pii_found = redact_pii(text)
    result.injection_detected = detect_injection(text)
    return result
