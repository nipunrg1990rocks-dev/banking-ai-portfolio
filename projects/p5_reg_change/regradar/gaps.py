"""Gap assessment and risk scoring.

For every NEW or AMENDED provision the assessor decides whether Ledgerline's
policy already meets it:

* CONFLICT  the policy sets a number of the same kind that is weaker
            ("policy: SAR within 30 days; regulation: within 15 days")
* PARTIAL   the policy covers the topic but misses part of it: a number the
            regulator now sets, or distinctive scope words ("crypto-asset")
* MISSING   nothing in the policy library speaks to the provision
* COVERED   the policy already meets it (equal or stricter numbers, scope covered)

Each finding carries its evidence (the regulatory sentence, the policy
sentence, the two numbers), a confidence level and a risk score
(impact x likelihood, 1-25) with a written rationale for every factor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .corpus import DOMAIN_POLICY, Circular
from .diff import AMENDED, REMOVED, Change
from .mapping import ParamMatch, PolicyMatcher, SectionHit, controls_for
from .params import Quantity, describe_value, meets, phrase

CONFLICT, PARTIAL, MISSING, COVERED = "CONFLICT", "PARTIAL", "MISSING", "COVERED"
OPPORTUNITY = "OPPORTUNITY"  # a removed requirement: the bank may be able to simplify
STATUS_LABEL = {CONFLICT: "Gap: policy weaker than rule", PARTIAL: "Gap: partly covered",
                MISSING: "Gap: not covered", COVERED: "Covered", OPPORTUNITY: "Requirement withdrawn"}
STATUS_ORDER = {CONFLICT: 0, MISSING: 1, PARTIAL: 2, OPPORTUNITY: 3, COVERED: 4}

# Thresholds, tuned on the four demo circulars and documented in docs/architecture.md.
PARAM_MIN_SCORE = 0.30     # minimum match score for a policy number to count as "the same rule"
SECTION_MIN_SIM = 0.12     # below this, the best policy sentence is not about the provision
COVERAGE_COVERED = 0.62    # share of distinctive words the section must contain to count as covered
COVERAGE_PARTIAL = 0.30    # below this (with weak sentence match) the provision is not covered at all
MISSING_SIM = 0.25         # with low coverage, a best sentence below this means "not covered"
SCOPE_WITH_NUMBER = 0.45   # a number can be met while the scope is not ("2 hours internally" vs "notify the Authority")

DOMAIN_IMPACT = {"AML": 4, "Sanctions": 4, "KYC": 4, "Model Risk & AI": 3, "Operational Risk": 3,
                 "Third-Party Risk": 3, "Data & Privacy": 3, "Enterprise Risk": 3}
LIKELIHOOD = {CONFLICT: 4, MISSING: 4, PARTIAL: 2, COVERED: 1, OPPORTUNITY: 1}
RATINGS = ((20, "Critical"), (12, "High"), (6, "Medium"), (0, "Low"))
RATING_ORDER = ["Low", "Medium", "High", "Critical"]
RECIPIENTS = {
    "the regulator": re.compile(r"\b(?:the authority|regulator|supervisor)\b", re.IGNORECASE),
    "the board": re.compile(r"\bboard\b", re.IGNORECASE),
    "customers": re.compile(r"\bcustomers?\b", re.IGNORECASE),
}
_REPORTING = re.compile(r"\b(?:notify|report|tell|inform|disclose|submit)\w*\b", re.IGNORECASE)
_EXTERNAL = re.compile(r"\b(?:notify the authority|the authority|file a|report\b[^.]*\bto the board|to the board)\b",
                       re.IGNORECASE)


@dataclass
class ParamCheck:
    required: Quantity
    match: ParamMatch | None
    ok: bool | None  # None = the policy sets no comparable number

    @property
    def text(self) -> str:
        if self.match is None:
            return f"Regulation: {phrase(self.required)}. No policy figure clearly governs this."
        verdict = "meets it" if self.ok else "is weaker"
        return (f"Regulation: {phrase(self.required)}; {self.match.sentence.ref}: "
                f"{describe_value(self.match.policy)}, which {verdict}.")


@dataclass
class ControlImpact:
    control_id: str
    name: str
    owner: str
    last_test_result: str
    action: str  # Reconfigure | Review | Design new control
    reason: str


@dataclass
class Risk:
    impact: int
    likelihood: int
    score: int
    rating: str
    factors: list[str]
    days_to_effective: int | None


@dataclass
class Finding:
    fid: str
    circular_id: str
    change: Change
    status: str
    primary_ref: str | None
    policy_refs: list[str]
    checks: list[ParamCheck] = field(default_factory=list)
    coverage: float = 1.0
    missing_terms: list[str] = field(default_factory=list)
    hits: list[SectionHit] = field(default_factory=list)
    best_sentence: str = ""
    best_sentence_sim: float = 0.0
    confidence: str = "Medium"
    rationale: list[str] = field(default_factory=list)
    controls: list[ControlImpact] = field(default_factory=list)
    risk: Risk | None = None
    trace: list[str] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    llm_opinion: dict | None = None

    @property
    def provision(self):
        return self.change.provision

    @property
    def is_gap(self) -> bool:
        return self.status in (CONFLICT, PARTIAL, MISSING)

    @property
    def rating(self) -> str:
        return self.risk.rating if self.risk else "Low"


def _required_parameters(change: Change) -> list[Quantity]:
    if change.change_type == AMENDED:
        return [p.new for p in change.parameter_changes if p.direction != "unchanged"] + change.new_parameters
    return list(change.new_parameters)


def _terms_for(change: Change, matcher: PolicyMatcher) -> list[str]:
    if change.change_type == AMENDED:
        return matcher.salient_terms(change.inserted_words)
    return matcher.salient_terms(change.provision.text.split())


def recipient_gap(provision_text: str, policy_sentence: str) -> str | None:
    """A reporting duty is only met if the policy reports to the same audience ("the Authority", not the CRO)."""
    if not _REPORTING.search(provision_text):
        return None
    for name, rx in RECIPIENTS.items():
        if rx.search(provision_text) and not rx.search(policy_sentence):
            return name
    return None


def assess(change: Change, matcher: PolicyMatcher, circular: Circular, as_of: date, n: int) -> Finding:
    prov = change.provision
    fid = f"F-{circular.circular_id.split('-', 1)[-1]}-{n:02d}"
    if change.change_type == REMOVED:
        f = Finding(fid, circular.circular_id, change, OPPORTUNITY, None, [], confidence="High",
                    rationale=["The regulator withdrew this provision. No new obligation; the bank may simplify "
                               "any policy or process that only existed to meet it."])
        f.risk = Risk(1, 1, 1, "Low", ["Withdrawn requirement: no compliance exposure."], None)
        return f

    trace = []
    home = DOMAIN_POLICY.get(circular.domain)
    query = f"{prov.section_title}. {prov.text}"
    hits = matcher.rank_sections(prov.text, matcher.search(query, k=5), home)
    trace.append("Searched the policy library: " + ", ".join(f"{h.section.ref} ({h.sentence_sim:.2f})" for h in hits))
    terms = _terms_for(change, matcher)
    if hits and hits[0].sentence_sim < 0.2 and terms:
        # Agentic retry: a weak first match triggers a second search on the distinctive words alone.
        extra = matcher.rank_sections(prov.text, matcher.search(" ".join(terms), k=4))
        known = {h.section.ref for h in hits}
        added = [h for h in extra if h.section.ref not in known]
        if added:
            hits = matcher.rank_sections(prov.text, hits + added, home)
            for i, h in enumerate(hits, 1):
                h.rank = i
            trace.append("Weak first match, so searched again on the distinctive words ("
                         + ", ".join(terms[:6]) + "): added " + ", ".join(h.section.ref for h in added))

    required = _required_parameters(change)
    checks: list[ParamCheck] = []
    conflict_refs: list[str] = []
    for q in required:
        matches = matcher.match_parameter(q, prov.text, hits, PARAM_MIN_SCORE, home)
        if not matches:
            checks.append(ParamCheck(q, None, None))
            trace.append(f"No policy figure clearly governs '{phrase(q)}'.")
            continue
        best = matches[0]
        ok = meets(best.policy, q)
        checks.append(ParamCheck(q, best, ok))
        trace.append(f"Compared '{phrase(q)}' with {best.sentence.ref} '{best.policy.text}' "
                     f"(match {best.score:.2f}): {'meets' if ok else 'weaker'}.")
        if not ok:
            conflict_refs.append(best.sentence.ref)
            # The same weaker figure can sit in more than one policy (e.g. a retention period).
            for other in matches[1:]:
                if (other.sentence.ref not in conflict_refs and other.context >= 0.3
                        and abs(other.policy.base - best.policy.base) < 1e-6 and not meets(other.policy, q)):
                    conflict_refs.append(other.sentence.ref)
                    checks.append(ParamCheck(q, other, False))
                    trace.append(f"The same figure also appears in {other.sentence.ref}.")

    top = hits[0] if hits else None
    matched_refs = [c.match.sentence.ref for c in checks if c.match]
    primary = (conflict_refs[0] if conflict_refs else matched_refs[0] if matched_refs
               else top.section.ref if top else None)
    primary_hit = next((h for h in hits if h.section.ref == primary), top)
    best_sim = primary_hit.sentence_sim if primary_hit else 0.0
    coverage, missing = matcher.coverage(terms, [primary] if primary else [])
    rationale: list[str] = []

    if conflict_refs:
        status = CONFLICT
        for c in checks:
            if c.ok is False:
                rationale.append(c.text)
    elif any(c.match is None for c in checks):
        if best_sim < MISSING_SIM and coverage < COVERAGE_PARTIAL:
            status = MISSING
            rationale.append("No policy section addresses this requirement "
                             f"(best match {primary}, {coverage:.0%} of distinctive terms).")
        else:
            status = PARTIAL
            rationale += [c.text for c in checks if c.match is None]
            rationale.append(f"Closest policy text is {primary}, which covers the topic but not this figure.")
    elif checks:
        audience = recipient_gap(prov.text, checks[0].match.sentence.text) if checks[0].match else None
        if audience:
            status = PARTIAL
            rationale += [c.text for c in checks]
            rationale.append(f"The timing is met, but {primary} reports internally, not to {audience}.")
        elif coverage < SCOPE_WITH_NUMBER:
            status = PARTIAL
            rationale += [c.text for c in checks]
            rationale.append(f"The figure is met, but {primary} does not cover: {', '.join(missing[:6])}.")
        else:
            status = COVERED
            rationale += [c.text for c in checks]
    else:
        if coverage >= COVERAGE_COVERED and best_sim >= SECTION_MIN_SIM:
            status = COVERED
            rationale.append(f"{primary} already covers this requirement ({coverage:.0%} of its distinctive terms).")
        elif coverage >= COVERAGE_PARTIAL or best_sim >= 0.3:
            status = PARTIAL
            rationale.append(f"{primary} covers the topic but not: {', '.join(missing[:6]) or 'some of the wording'}.")
        else:
            status = MISSING
            rationale.append("No policy section addresses this requirement "
                             f"(best match {primary}, {coverage:.0%} of distinctive terms).")

    policy_refs = list(dict.fromkeys(conflict_refs + matched_refs + ([primary] if primary and status != MISSING
                                                                       else [])))
    # Confidence: how clear-cut the evidence is.
    if status == CONFLICT and all(c.match and c.match.score >= 0.5 for c in checks if c.ok is False):
        confidence = "High"
    elif status == COVERED and coverage >= 0.8:
        confidence = "High"
    elif status == MISSING and best_sim < 0.1:
        confidence = "High"
    elif best_sim < 0.15 and status != MISSING:
        confidence = "Low"
    else:
        confidence = "Medium"
    return Finding(fid, circular.circular_id, change, status, primary if status != MISSING else None, policy_refs,
                   checks, coverage, missing, hits, primary_hit.best_sentence.text if primary_hit and
                   primary_hit.best_sentence else "", best_sim, confidence, rationale, trace=trace)


def map_controls(f: Finding) -> None:
    """Controls that implement the affected policy sections, and what each needs."""
    if f.status == MISSING:
        f.controls = [ControlImpact("NEW", "New control required", "To be assigned", "n/a", "Design new control",
                                    "No existing control implements this requirement.")]
        return
    if not f.is_gap:
        return
    df = controls_for(f.policy_refs)
    old_values = {str(int(c.match.policy.value)) for c in f.checks if c.match and c.ok is False}
    for _, row in df.iterrows():
        desc = str(row["description"])
        hard_coded = sorted(v for v in old_values if re.search(rf"\b{v}\b", desc))
        if hard_coded:
            action, reason = "Reconfigure", f"Its configuration uses the old figure ({', '.join(hard_coded)})."
        else:
            action, reason = "Review", "Implements the affected policy section; confirm it still meets the rule."
        f.controls.append(ControlImpact(row["control_id"], row["name"], row["owner"], row["last_test_result"],
                                        action, reason))


def score_risk(f: Finding, circular: Circular, as_of: date) -> None:
    if f.status == OPPORTUNITY:
        return
    factors = []
    impact = DOMAIN_IMPACT.get(circular.domain, 3)
    factors.append(f"Impact {impact}: {circular.domain} requirements carry "
                   f"{'high (financial crime)' if impact >= 4 else 'moderate'} regulatory and customer impact.")
    if _EXTERNAL.search(f.provision.text) and impact < 5:
        impact += 1
        factors.append("Impact +1: the rule involves reporting to the regulator or the board.")
    likelihood = LIKELIHOOD[f.status]
    factors.append(f"Likelihood {likelihood}: {STATUS_LABEL[f.status].lower()}.")
    weak = [c for c in f.controls if c.last_test_result in ("Ineffective", "Partially effective")]
    if weak and f.is_gap and likelihood < 5:
        likelihood += 1
        factors.append(f"Likelihood +1: {weak[0].control_id} was last tested '{weak[0].last_test_result}'.")
    score = impact * likelihood
    rating = next(r for floor, r in RATINGS if score >= floor)
    days = None
    if circular.effective_date:
        try:
            days = (date.fromisoformat(circular.effective_date) - as_of).days
        except ValueError:
            days = None
    if days is not None and f.is_gap:
        if days <= 90 and rating != "Critical":
            rating = RATING_ORDER[RATING_ORDER.index(rating) + 1]
            factors.append(f"Raised one level: the rule takes effect in {days} days ({circular.effective_date}).")
        else:
            factors.append(f"Takes effect in {days} days ({circular.effective_date}).")
    factors.append(f"Score {impact} x {likelihood} = {score} → {rating}.")
    f.risk = Risk(impact, likelihood, score, rating, factors, days)
