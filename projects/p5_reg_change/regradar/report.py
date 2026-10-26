"""The Regulatory Change Impact Assessment pack (Markdown), as exported from the app."""

from __future__ import annotations

from datetime import date

from .agents import DEMO_AS_OF, Assessment, run_assessment
from .corpus import load_circular_pairs
from .gaps import STATUS_LABEL
from .signoff import Workflow


def assess_all(as_of: date = DEMO_AS_OF, llm=None) -> dict[str, Assessment]:
    """One assessment per circular in the library, keyed by folder name."""
    return {p.key: run_assessment(p, as_of, llm=llm) for p in load_circular_pairs()}


def build_pack(wf: Workflow) -> str:
    a = wf.assessment
    c = a.circular
    out = [
        f"# Regulatory Change Impact Assessment: {c.circular_id}",
        "",
        f"**{c.title}**  ",
        f"Regulator: {c.regulator}. Published {c.published or 'n/a'}. Effective {c.effective_date or 'n/a'}.  ",
        f"Assessment run {a.run_id}, as of {a.as_of.isoformat()}. Engine: "
        f"{'offline rules engine' if not a.llm_label else 'rules engine + ' + a.llm_label}.  ",
        "Status: **" + (f"SIGNED OFF by {wf.signed_off_by} at {wf.signed_off_at}" if wf.signed_off_by
                        else "DRAFT, not signed off") + "**",
        "",
        "> Synthetic demonstration. Ledgerline Bank and the NPCA are fictional. Independent AI-assisted portfolio "
        "project.",
        "",
        "## Executive summary",
        "",
        a.summary,
        "",
        f"_Summary source: {a.summary_source}._",
        "",
        "## Gap register",
        "",
        "| Finding | Provision | Change | Status | Rating | Policy | Confidence | Human review |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in a.findings:
        review = ("cleared by " + wf.reviewed_flags[f.fid]) if f.fid in wf.reviewed_flags else (
            "required" if f.needs_review else "-")
        out.append(f"| {f.fid} | {f.provision.pid} | {f.change.headline} | {STATUS_LABEL[f.status]} | {f.rating} | "
                   f"{', '.join(f.policy_refs) or '-'} | {f.confidence} | {review} |")
    out += ["", "## Findings in detail", ""]
    for f in a.findings:
        if f.status == "OPPORTUNITY":
            continue
        out += [f"### {f.fid}: {f.provision.pid} ({STATUS_LABEL[f.status]}, {f.rating})", "",
                f"**Requirement:** {f.provision.text}", ""]
        if f.change.previous and f.change.change_type == "AMENDED":
            out += [f"**Previously:** {f.change.previous.text}", ""]
        if f.best_sentence and f.primary_ref:
            out += [f"**Closest policy text ({f.primary_ref}):** {f.best_sentence}", ""]
        out += ["**Why:**", *[f"- {r}" for r in f.rationale], ""]
        if f.risk:
            out += ["**Risk score:**", *[f"- {r}" for r in f.risk.factors], ""]
        if f.review_reasons:
            out += ["**Challenger:**", *[f"- {r}" for r in f.review_reasons], ""]
    out += ["## Remediation plan", "",
            "| Action | Type | Title | Owner | Due | Priority | Decision | By |", "|---|---|---|---|---|---|---|---|"]
    for x in a.actions:
        out.append(f"| {x.aid} | {x.kind} | {x.title} | {x.owner} | {x.due} | {x.priority} | {x.decision} | "
                   f"{x.decided_by or '-'} |")
    out += ["", "### Proposed wording", ""]
    for x in a.actions:
        if x.after:
            out += [f"**{x.aid} {x.title}**", ""]
            if x.before:
                out += [f"- Current: {x.before}", f"- Proposed: {x.after}", ""]
            else:
                out += [f"- New text: {x.after}", ""]
    ok, broken = wf.audit.verify_chain()
    out += ["## Audit trail", "",
            f"{wf.audit.count()} events; hash chain {'intact' if ok else f'BROKEN at event {broken}'}.", ""]
    for e in reversed(wf.audit.events(correlation_id=a.run_id)):
        out.append(f"- {e.timestamp} {e.actor}: {e.event_type} (hash {e.hash[:12]})")
    return "\n".join(out) + "\n"
