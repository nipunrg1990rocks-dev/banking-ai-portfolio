"""Triage model: which kind of exception is this, and how sure are we?

A small, explainable text classifier (TF-IDF features + multinomial logistic
regression) trained on a synthetic history of closed exceptions. The history is
written with different phrasing from the open queue, so the evaluation measures
generalisation rather than memorised sentences.

If the top probability is below ``CONFIDENCE_THRESHOLD`` or the model says
``OTHER``, the agent does not guess: the exception goes to a human triage queue.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

CATEGORIES = {
    "PAYMENT_REPAIR": "Payment repair (invalid account / bank identifier)",
    "NOSTRO_BREAK": "Nostro reconciliation break",
    "DUPLICATE_PAYMENT": "Suspected duplicate payment",
    "SANCTIONS_HIT": "Sanctions screening alert",
    "KYC_EXPIRED_BLOCK": "Payment blocked: KYC review overdue",
    "SETTLEMENT_FAIL": "Securities settlement fail",
    "GL_SUSPENSE": "Aged GL suspense item",
    "LIMIT_BREACH": "Credit / overdraft limit excess",
    "OTHER": "Other / needs human triage",
}
CONFIDENCE_THRESHOLD = 0.55

_HISTORY_TEMPLATES: dict[str, list[str]] = {
    "PAYMENT_REPAIR": [
        "Payments Hub | {code} | wire to {ben} rejected, beneficiary account invalid, repair needed",
        "Payments Hub | {code} | SEPA transfer returned {code} incorrect account number, please correct IBAN",
        "Payments Hub | {code} | MT103 rejected bank identifier unknown, check BIC and resubmit",
        "Payments Hub | | returned payment wrong creditor account details, customer chasing for resend",
        "Payments Hub | {code} | faster payment bounced account not found, repair beneficiary details",
    ],
    "NOSTRO_BREAK": [
        "Nostro Recon | | unmatched item on correspondent account, statement vs ledger difference",
        "Nostro Recon | | reconciliation exception open item USD nostro not paired with statement",
        "Nostro Recon | | break between our books and correspondent statement, investigate open entry",
        "Nostro Recon | | recon break ageing, ledger entry with no statement match on nostro",
    ],
    "DUPLICATE_PAYMENT": [
        "Payments Hub | AM05 | possible duplicate same amount same beneficiary within the day, held",
        "Payments Hub | AM05 | duplicate payment check triggered, second instruction identical to earlier one",
        "Payments Hub | | customer may have sent payment twice, same reference and value, hold for review",
    ],
    "SANCTIONS_HIT": [
        "Screening | SANCTIONS_HIT | name screening potential match against watchlist, payment held",
        "Screening | SANCTIONS_HIT | real-time filter alert beneficiary name similar to listed party",
        "Screening | | watchlist hit on counterparty, disposition required before release",
    ],
    "KYC_EXPIRED_BLOCK": [
        "Payments Hub | AG01 | payment stopped, customer due diligence refresh overdue",
        "Payments Hub | AG01 | KYC periodic review expired, transaction forbidden until review complete",
        "Onboarding | | client documents out of date, KYC lapsed, outgoing payments blocked",
    ],
    "SETTLEMENT_FAIL": [
        "Settlements | {fail} | trade failed to settle at depository, counterparty short",
        "Settlements | {fail} | settlement fail on bond trade, CSD status unmatched instructions",
        "Settlements | | failed trade, delivery versus payment not completed, penalty risk",
    ],
    "GL_SUSPENSE": [
        "General Ledger | | suspense account balance unapplied credit needs allocation",
        "General Ledger | | aged item in suspense GL, unidentified receipt to clear",
        "General Ledger | | unallocated funds sitting in suspense, find owner and post",
    ],
    "LIMIT_BREACH": [
        "Credit Monitoring | LIMIT_EXCESS | account overdrawn beyond agreed facility, pay or return",
        "Credit Monitoring | LIMIT_EXCESS | excess over overdraft limit, credit decision required",
        "Credit Monitoring | | customer exceeded credit line, debits pending decision",
    ],
    "OTHER": [
        "Branch Ticket | | customer requests duplicate statement for last year",
        "Contact Centre | | card not received, customer wants status of new debit card",
        "Branch Ticket | | change of address request, documents attached",
        "Contact Centre | | customer unhappy with service, wants to speak to manager",
        "Branch Ticket | | query about interest rate on savings account",
        "Contact Centre | | online banking password reset not working",
    ],
}
_FILL = {
    "code": ["AC01", "AC04", "RC01", "BE01"],
    "ben": ["supplier", "landlord", "subsidiary", "payroll provider", "law firm"],
    "fail": ["INSUFFICIENT_SECURITIES", "INSUFFICIENT_CASH", "SSI_MISMATCH", "LATE_MATCHING"],
}
_NOISE = ["urgent", "please action", "per RM", "today", "EOD", "client escalated", "ref attached", "", "", ""]


def synthetic_history(n_per_class: int = 70, seed: int = 7) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    texts, labels = [], []
    for label, templates in _HISTORY_TEMPLATES.items():
        for _ in range(n_per_class):
            t = rng.choice(templates)
            for slot, options in _FILL.items():
                t = t.replace("{" + slot + "}", rng.choice(options))
            words = t.split()
            if rng.random() < 0.3 and len(words) > 8:  # drop a word so the model cannot memorise exact strings
                words.pop(rng.randrange(4, len(words)))
            texts.append(" ".join(words) + " " + rng.choice(_NOISE))
            labels.append(label)
    return texts, labels


def exception_text(exc: dict) -> str:
    return " | ".join([exc.get("source_system", ""), exc.get("reason_code") or "", exc.get("title", ""),
                       exc.get("description", "")])


@dataclass
class TriageResult:
    category: str
    confidence: float
    needs_human_triage: bool
    top3: list[tuple[str, float]]
    top_terms: list[str]


class TriageModel:
    def __init__(self, seed: int = 7) -> None:
        texts, labels = synthetic_history(seed=seed)
        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=2, lowercase=True,
                                      token_pattern=r"(?u)\b[A-Za-z][A-Za-z0-9_]+\b")),
            ("clf", LogisticRegression(max_iter=2000, C=4.0)),
        ])
        self.pipeline.fit(texts, labels)
        self.training_size = len(texts)

    @property
    def classes(self) -> list[str]:
        return list(self.pipeline.named_steps["clf"].classes_)

    def predict(self, exc: dict) -> TriageResult:
        text = exception_text(exc)
        proba = self.pipeline.predict_proba([text])[0]
        order = np.argsort(proba)[::-1]
        classes = self.classes
        best, conf = classes[order[0]], float(proba[order[0]])
        return TriageResult(
            category=best, confidence=round(conf, 3),
            needs_human_triage=best == "OTHER" or conf < CONFIDENCE_THRESHOLD,
            top3=[(classes[i], round(float(proba[i]), 3)) for i in order[:3]],
            top_terms=self._explain(text, order[0]),
        )

    def _explain(self, text: str, class_idx: int, k: int = 5) -> list[str]:
        """Terms in this text that pushed hardest towards the chosen class (feature weight x tf-idf)."""
        tfidf = self.pipeline.named_steps["tfidf"]
        clf = self.pipeline.named_steps["clf"]
        vec = tfidf.transform([text])
        names = tfidf.get_feature_names_out()
        contrib = vec.multiply(clf.coef_[class_idx]).toarray()[0]
        idx = [i for i in np.argsort(contrib)[::-1][:k] if contrib[i] > 0]
        return [str(names[i]) for i in idx]
