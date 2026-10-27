"""Remediation planner: turn each gap into concrete, owned, dated actions.

* CONFLICT  a policy redline: the weaker figure in the policy sentence is replaced
            with the regulator's figure, word for word, plus a "Reconfigure" action
            for every control whose configuration still uses the old figure.
* PARTIAL   a sentence added to the matched policy section, adapted from the
            regulator's wording, and a review of the section's controls.
* MISSING   a new policy clause in the policy that owns the domain, and a new
            control to design.

Every action cites the regulatory provision and the policy section, has an
owner (the policy or control owner) and a due date that leaves 30 days of
testing before the rule takes effect. These are drafts: nothing is final until
a named approver accepts it on the sign-off screen.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .corpus import DOMAIN_POLICY, Circular, section_by_ref
from .diff import _word_diff
from .gaps import CONFLICT, MISSING, PARTIAL, Finding
from .params import replace_quantity_text

TEST_BUFFER_DAYS = 30


@dataclass
class Action:
    aid: str
    finding_id: str
    kind: str          # Policy redline | Policy addition | New policy clause | Control reconfiguration | ...
    title: str
    detail: str
    owner: str
    due: str
    priority: str
    citations: list[str]
    before: str = ""
    after: str = ""
    drafted_by: str = "Offline drafter (rules, no LLM)"
    decision: str = "Proposed"     # Proposed | Accepted | Rejected | Edited
    decided_by: str = ""
    comment: str = ""
    history: list[dict] = field(default_factory=list)

    @property
    def redline_html(self) -> str:
        if not self.before:
            return f"<ins>{html.escape(self.after)}</ins>" if self.after else ""
        return _word_diff(self.before, self.after)[2]


def adapt_to_bank(text: str, bank: str = "Ledgerline Bank") -> str:
    """Rewrite a regulator's sentence as a bank policy sentence."""
    m = re.match(r"^Firms that (\w+) (.*?) must (.*)$", text)
    if m:
        verb, rest, tail = m.groups()
        return f"Where {bank} {verb}s {rest}, it must {tail}"
    out = re.sub(r"^Firms\b", bank, text)
    out = re.sub(r"\bthe firm's\b", "the bank's", out)
    out = re.sub(r"\bfirms\b", "the bank", out)
    out = re.sub(r"\bthe Authority\b", "the Northbridge Prudential and Conduct Authority (NPCA)", out)
    return out


def due_date(circular: Circular, as_of: date) -> str:
    try:
        target = date.fromisoformat(circular.effective_date) - timedelta(days=TEST_BUFFER_DAYS)
    except ValueError:
        target = as_of + timedelta(days=90)
    return max(target, as_of + timedelta(days=14)).isoformat()


def plan(findings: list[Finding], circular: Circular, as_of: date) -> list[Action]:
    actions: list[Action] = []
    due = due_date(circular, as_of)

    def add(f: Finding, kind: str, title: str, detail: str, owner: str, cites: list[str], before: str = "",
            after: str = "") -> None:
        actions.append(Action(f"A-{len(actions) + 1:03d}", f.fid, kind, title, detail, owner, due, f.rating,
                              cites, before, after))

    for f in findings:
        if not f.is_gap:
            continue
        reg_ref = f.provision.pid
        if f.status == CONFLICT:
            done_refs = set()
            for c in f.checks:
                if c.ok is not False or c.match is None:
                    continue
                ref = c.match.sentence.ref
                section = section_by_ref(ref)
                before = c.match.sentence.text
                # Keep the policy's own unit wording where it can, e.g. "annually" -> "every six months".
                new_text = c.required.text
                if c.match.policy.unit == "frequency word" and c.required.unit != "frequency word":
                    new_text = f"every {c.required.text}"
                after = replace_quantity_text(before, c.match.policy, new_text)
                key = (ref, before)
                if key in done_refs:
                    continue
                done_refs.add(key)
                add(f, "Policy redline", f"Update {ref}: {c.match.policy.text} → {new_text}",
                    f"Align {ref} with {reg_ref}. The policy currently allows {c.match.policy.text}; the regulation "
                    f"requires {c.required.text}.", section.owner if section else "Policy owner", [reg_ref, ref],
                    before, after)
        elif f.status == PARTIAL and f.primary_ref:
            section = section_by_ref(f.primary_ref)
            missing = [c.required.text for c in f.checks if c.match is None]
            gap_words = ", ".join(missing or f.missing_terms[:5]) or "the new wording"
            add(f, "Policy addition", f"Extend {f.primary_ref} to cover {gap_words}",
                f"{f.primary_ref} covers the topic of {reg_ref} but not {gap_words}. Add the sentence below.",
                section.owner if section else "Policy owner", [reg_ref, f.primary_ref], "",
                adapt_to_bank(f.provision.text))
        elif f.status == MISSING:
            doc = DOMAIN_POLICY.get(circular.domain, "MRM-006")
            owner = next((s.owner for s in _sections_of(doc)), "Policy owner")
            add(f, "New policy clause", f"Add a new clause to {doc}: {f.provision.section_title}",
                f"No Ledgerline policy addresses {reg_ref}. Add a new clause to {doc} ({circular.domain}).",
                owner, [reg_ref, doc], "", adapt_to_bank(f.provision.text))
        for ci in f.controls:
            if ci.action == "Reconfigure":
                new_values = [c.required for c in f.checks if c.ok is False and c.match]
                old_values = [c.match.policy for c in f.checks if c.ok is False and c.match]
                before = _control_description(ci.control_id)
                after = before
                for old, new in zip(old_values, new_values, strict=False):
                    after = re.sub(rf"\b{int(old.value)}\b", f"{int(new.value)}", after)
                add(f, "Control reconfiguration", f"Reconfigure {ci.control_id} {ci.name}",
                    f"{ci.reason} Re-test the control after the change and re-set any early-warning trigger "
                    "so it fires before the new limit.", ci.owner, [reg_ref, ci.control_id], before, after)
            elif ci.action == "Design new control":
                add(f, "New control", f"Design a control for {reg_ref}",
                    f"Design, document and test a preventive or detective control for: {f.provision.text}",
                    "Second line: Compliance Testing", [reg_ref])
            elif ci.action == "Review" and f.status != PARTIAL:
                continue
            elif ci.action == "Review":
                add(f, "Control review", f"Review {ci.control_id} {ci.name}",
                    f"{ci.reason} Last tested: {ci.last_test_result}.", ci.owner, [reg_ref, ci.control_id])
    return actions


def _sections_of(doc_id: str):
    from .corpus import policy_sections

    return [s for s in policy_sections() if s.doc_id == doc_id]


def _control_description(control_id: str) -> str:
    from .corpus import load_controls

    df = load_controls()
    row = df[df["control_id"] == control_id]
    return str(row.iloc[0]["description"]) if len(row) else ""


