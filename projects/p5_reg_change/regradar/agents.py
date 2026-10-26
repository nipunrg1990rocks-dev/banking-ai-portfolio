"""The agent crew and the orchestrator that runs them.

Seven agents work in sequence on one circular, each with its own tools, and
every step they take is recorded as a trace event the app replays live:

1. Intake Agent        document intelligence: sections, provisions, modality, figures
2. Change Analyst      aligns old and new versions; classifies and redlines every change
3. Policy Mapper       hybrid RAG over the policy library; re-searches when the first match is weak
4. Gap Assessor        compares figures, scope and audience; scores risk with a rationale
5. Remediation Planner drafts policy redlines, new clauses and control changes with owners and dates
6. Challenger          checks every finding (citations, numbers, confidence) and, when a model is
                       connected, asks it for an independent verdict; disagreements go to a human
7. Reporter            writes the executive summary (model draft only if every number is grounded)

The human is the eighth step: nothing is final until a named approver signs off
(see ``signoff.py``). The orchestrator is plain Python, so the same run works
offline, in the app, in tests and in a Databricks notebook.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from .corpus import DOMAIN_POLICY, CircularPair, policy_sections
from .diff import AMENDED, NEW, REMOVED, UNCHANGED, Change, compare
from .gaps import (
    CONFLICT,
    COVERED,
    MISSING,
    OPPORTUNITY,
    PARTIAL,
    RATING_ORDER,
    STATUS_ORDER,
    Finding,
    assess,
    map_controls,
    score_risk,
)
from .llm import draft_summary, review_finding
from .mapping import PolicyMatcher, default_matcher
from .params import extract_quantities
from .remediation import Action, plan

DEMO_AS_OF = date(2026, 10, 26)


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    icon: str
    role: str
    tools: tuple[str, ...]


CREW = (
    AgentSpec("intake", "Intake Agent", "📥", "Reads the circular: sections, provisions, duties and figures.",
              ("parse_circular", "classify_modality", "extract_quantities")),
    AgentSpec("change", "Change Analyst", "🔀", "Compares the new circular with the version it replaces.",
              ("align_provisions", "word_redline", "pair_parameters")),
    AgentSpec("mapper", "Policy Mapper", "🧭", "Finds the policy sections and controls each change lands on.",
              ("hybrid_policy_search", "sentence_match", "control_lookup")),
    AgentSpec("assessor", "Gap Assessor", "⚖️", "Decides covered / partial / conflict / missing and scores risk.",
              ("compare_figures", "check_scope", "check_audience", "score_risk")),
    AgentSpec("planner", "Remediation Planner", "🛠️", "Drafts redlines, new clauses and control changes.",
              ("draft_redline", "draft_clause", "assign_owner")),
    AgentSpec("challenger", "Challenger", "🧐", "Tests every finding before a human sees it.",
              ("verify_citations", "grounding_check", "llm_second_opinion")),
    AgentSpec("reporter", "Reporter", "📝", "Writes the executive summary for the committee.",
              ("summarise", "grounding_check")),
)
CREW_BY_KEY = {a.key: a for a in CREW}


@dataclass
class TraceEvent:
    agent: str
    kind: str      # start | step | warn | done
    text: str
    t_ms: float


@dataclass
class Assessment:
    run_id: str
    pair: CircularPair
    as_of: date
    changes: list[Change]
    findings: list[Finding]
    actions: list[Action]
    summary: str
    summary_source: str
    events: list[TraceEvent] = field(default_factory=list)
    llm_label: str | None = None
    llm_usage: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def circular(self):
        return self.pair.current

    @property
    def gaps(self) -> list[Finding]:
        return [f for f in self.findings if f.is_gap]

    def counts(self) -> dict[str, int]:
        c = Counter(f.status for f in self.findings)
        return {s: c.get(s, 0) for s in (CONFLICT, MISSING, PARTIAL, COVERED, OPPORTUNITY)}

    def change_counts(self) -> dict[str, int]:
        c = Counter(ch.change_type for ch in self.changes)
        return {s: c.get(s, 0) for s in (NEW, AMENDED, REMOVED, UNCHANGED)}

    def rating_counts(self) -> dict[str, int]:
        c = Counter(f.rating for f in self.gaps)
        return {r: c.get(r, 0) for r in reversed(RATING_ORDER)}

    def finding(self, fid: str) -> Finding | None:
        return next((f for f in self.findings if f.fid == fid), None)

    def impacted_policies(self) -> list[str]:
        return sorted({r for f in self.gaps for r in f.policy_refs})

    def impacted_controls(self) -> list[str]:
        return sorted({c.control_id for f in self.gaps for c in f.controls if c.control_id != "NEW"})

    def needs_review(self) -> list[Finding]:
        return [f for f in self.findings if f.needs_review]


def _sort_key(f: Finding):
    return (-RATING_ORDER.index(f.rating) if f.is_gap else 1, STATUS_ORDER[f.status], f.provision.pid)


def template_summary(a: Assessment) -> str:
    circ = a.circular
    counts = a.counts()
    gaps = sorted(a.gaps, key=_sort_key)
    lines = [f"{circ.circular_id} ({circ.title}) takes effect on {circ.effective_date}. The agents assessed "
             f"{len(a.findings)} changed provisions against Ledgerline's policies: {counts[CONFLICT]} where a policy "
             f"is weaker than the new rule, {counts[MISSING]} not covered, {counts[PARTIAL]} partly covered and "
             f"{counts[COVERED]} already covered."]
    for f in gaps[:3]:
        lines.append(f"- [{f.rating}] {f.provision.pid}: {f.change.headline}. {f.rationale[0] if f.rationale else ''}")
    if gaps:
        lines.append(f"{len(a.actions)} draft actions cover {len(a.impacted_policies())} policy sections and "
                     f"{len(a.impacted_controls())} controls. All actions are drafts until signed off.")
    else:
        lines.append("No gaps were found. The assessment still needs sign-off to be recorded.")
    return "\n".join(lines)


def summary_facts(a: Assessment) -> str:
    circ = a.circular
    rows = [f"Circular {circ.circular_id}: {circ.title}. Effective {circ.effective_date}. Regulator: {circ.regulator}.",
            f"Counts: {a.counts()}. Ratings of gaps: {a.rating_counts()}. Draft actions: {len(a.actions)}."]
    for f in sorted(a.gaps, key=_sort_key)[:6]:
        rows.append(f"- {f.fid} [{f.rating}] {f.status} {f.provision.pid}: {f.provision.text} "
                    f"Policy: {', '.join(f.policy_refs) or 'none'}. Why: {' '.join(f.rationale)}")
    return "\n".join(rows)


def run_assessment(pair: CircularPair, as_of: date = DEMO_AS_OF, llm=None,
                   on_event: Callable[[TraceEvent], None] | None = None,
                   matcher: PolicyMatcher | None = None, review_with_llm: bool = True) -> Assessment:
    """Run the crew over one circular. ``llm`` is optional; without it every step is offline and deterministic."""
    t0 = time.perf_counter()
    events: list[TraceEvent] = []
    circ = pair.current

    def emit(agent: str, kind: str, text: str) -> None:
        ev = TraceEvent(agent, kind, text, (time.perf_counter() - t0) * 1000)
        events.append(ev)
        if on_event:
            on_event(ev)

    # 1. Intake
    emit("intake", "start", f"Reading {circ.circular_id}: {circ.title}")
    duties = Counter(p.modality for p in circ.provisions)
    figures = sum(len(extract_quantities(p.text)) for p in circ.provisions)
    emit("intake", "step", f"{len(circ.sections())} sections, {len(circ.provisions)} provisions: "
         + ", ".join(f"{v} {k}" for k, v in duties.most_common()))
    emit("intake", "step", f"Extracted {figures} figures (deadlines, frequencies, durations, thresholds).")
    emit("intake", "done", f"Effective {circ.effective_date or 'date not stated'}; "
         f"{'replaces ' + pair.previous.circular_id if pair.previous else 'new circular, no previous version'}.")

    # 2. Change analysis
    emit("change", "start", "Aligning the new text with the version it replaces.")
    changes = compare(pair)
    cc = Counter(c.change_type for c in changes)
    emit("change", "step", ", ".join(f"{cc.get(k, 0)} {k.lower()}" for k in (NEW, AMENDED, REMOVED, UNCHANGED)))
    for c in changes:
        if c.change_type == AMENDED and c.parameter_changes:
            emit("change", "step", f"{c.pid}: {c.headline}")
    emit("change", "done", f"{sum(1 for c in changes if c.materiality == 'High')} high-materiality changes.")

    # 3-4. Mapping and assessment
    matcher = matcher or default_matcher()
    emit("mapper", "start", f"Searching {len(policy_sections())} policy sections (BM25 + embeddings, fused).")
    findings: list[Finding] = []
    for n, c in enumerate([c for c in changes if c.change_type != UNCHANGED], 1):
        f = assess(c, matcher, circ, as_of, n)
        for line in f.trace:
            if line.startswith("Weak first match"):
                emit("mapper", "warn", f"{c.pid}: {line}")
        map_controls(f)
        score_risk(f, circ, as_of)
        findings.append(f)
        target = ", ".join(f.policy_refs) or "no policy section"
        emit("mapper", "step", f"{c.pid} → {target}")
    emit("mapper", "done", f"{len({r for f in findings for r in f.policy_refs})} policy sections and "
         f"{len({ci.control_id for f in findings for ci in f.controls if ci.control_id != 'NEW'})} controls in scope.")
    emit("assessor", "start", "Comparing figures, scope and audience for every change.")
    for f in findings:
        if f.is_gap:
            emit("assessor", "step", f"{f.provision.pid}: {f.status} · {f.rating} · {f.rationale[0][:140]}")
    counts = Counter(f.status for f in findings)
    emit("assessor", "done", ", ".join(f"{counts.get(s, 0)} {s.lower()}"
                                       for s in (CONFLICT, MISSING, PARTIAL, COVERED)))

    # 5. Remediation
    emit("planner", "start", "Drafting remediation for every gap.")
    actions = plan(findings, circ, as_of)
    kinds = Counter(a.kind for a in actions)
    emit("planner", "done", f"{len(actions)} draft actions: " + ", ".join(f"{v} {k.lower()}" for k, v in kinds.items()))

    # 6. Challenger
    emit("challenger", "start", "Checking every finding before it reaches a human.")
    for f in findings:
        if f.status == OPPORTUNITY:
            continue
        if f.is_gap and f.status != MISSING and not f.policy_refs:
            f.needs_review, f.review_reasons = True, f.review_reasons + ["No policy citation."]
        if f.confidence == "Low":
            f.needs_review = True
            f.review_reasons.append("Low confidence: the best policy match is weak.")
        top = f.hits[0].section if f.hits else None
        same_domain = top is not None and top.doc_id == DOMAIN_POLICY.get(circ.domain)
        if f.status == MISSING and (same_domain or f.best_sentence_sim >= 0.12):
            f.needs_review = True
            f.review_reasons.append(f"Verify 'not covered': {top.ref if top else 'a related section'} is related "
                                    "and may partly cover it.")
    if llm is not None and review_with_llm:
        emit("challenger", "step", f"Asking {llm.label} for an independent verdict on each gap.")
        for f in findings:
            if f.status == OPPORTUNITY:
                continue
            op = review_finding(llm, f)
            f.llm_opinion = op.__dict__
            if op.error:
                emit("challenger", "warn", f"{f.provision.pid}: model review failed ({op.error}); rules verdict kept.")
            elif not op.agrees:
                f.needs_review = True
                f.review_reasons.append(f"The model's verdict is {op.status}, the rules engine's is {f.status}.")
                emit("challenger", "warn", f"{f.provision.pid}: model says {op.status}, rules say {f.status} → human.")
            elif not op.grounded:
                f.review_reasons.append("Model rationale had figures not in the sources; ignored.")
    flagged = sum(1 for f in findings if f.needs_review)
    emit("challenger", "done", f"{flagged} finding(s) routed to human review; every finding cites its provision "
         "and, where one exists, a policy section.")

    findings.sort(key=_sort_key)
    a = Assessment(uuid.uuid4().hex[:8], pair, as_of, changes, findings, actions, "", "template", events,
                   llm.label if llm is not None else None)

    # 7. Reporter
    emit("reporter", "start", "Writing the executive summary.")
    a.summary = template_summary(a)
    if llm is not None:
        text, bad = draft_summary(llm, summary_facts(a))
        if text:
            a.summary, a.summary_source = text, llm.label
            emit("reporter", "done", f"Summary drafted by {llm.label}; every figure checked against the findings.")
        else:
            emit("reporter", "warn", "Model summary rejected (unsupported figures: " + ", ".join(bad[:4])
                 + "); template summary used.")
    else:
        emit("reporter", "done", "Template summary written (offline).")
    if llm is not None:
        a.llm_usage = dict(llm.usage.__dict__)
    a.seconds = time.perf_counter() - t0
    return a
