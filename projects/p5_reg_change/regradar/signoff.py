"""Human sign-off: the only step that can make an assessment final.

Controls enforced here (and tested):

* every decision names a person and is written to the hash-chained audit log
  shared with the other portfolio projects (``portfolio_core.audit``);
* rejecting or editing an action needs a written comment;
* four-eyes: the approver who signs off the assessment cannot be the analyst
  who prepared it (accepted its actions);
* sign-off is blocked while any Critical or High action is undecided or any
  finding the Challenger flagged has not been reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from portfolio_core.audit import AuditLog

from .agents import Assessment

DECISIONS = ("Accepted", "Rejected", "Edited")
BLOCKING = ("Critical", "High")


class SignOffError(ValueError):
    pass


@dataclass
class Workflow:
    assessment: Assessment
    audit: AuditLog
    preparers: set[str] = field(default_factory=set)
    reviewed_flags: dict[str, str] = field(default_factory=dict)   # finding id -> reviewer
    signed_off_by: str | None = None
    signed_off_at: str | None = None

    def __post_init__(self) -> None:
        a = self.assessment
        self.audit.record("assessment_created", {
            "circular": a.circular.circular_id, "findings": len(a.findings), "actions": len(a.actions),
            "counts": a.counts(), "mode": a.llm_label or "offline"}, actor="agents", correlation_id=a.run_id)

    @property
    def run_id(self) -> str:
        return self.assessment.run_id

    def decide(self, aid: str, decision: str, person: str, comment: str = "", new_text: str | None = None) -> None:
        if self.signed_off_by:
            raise SignOffError("The assessment is already signed off; reopen it to change decisions.")
        person = (person or "").strip()
        if not person:
            raise SignOffError("Enter your name before recording a decision.")
        if decision not in DECISIONS:
            raise SignOffError(f"Unknown decision {decision!r}.")
        if decision in ("Rejected", "Edited") and not comment.strip():
            raise SignOffError(f"A comment is required to record '{decision}'.")
        action = next((x for x in self.assessment.actions if x.aid == aid), None)
        if action is None:
            raise SignOffError(f"No action {aid}.")
        before = action.after
        if decision == "Edited":
            if not new_text or not new_text.strip():
                raise SignOffError("Provide the edited wording.")
            action.after = new_text.strip()
        action.decision, action.decided_by, action.comment = decision, person, comment.strip()
        action.history.append({"decision": decision, "by": person, "comment": comment.strip(),
                               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.preparers.add(person)
        self.audit.record("action_decided", {"action": aid, "finding": action.finding_id, "decision": decision,
                                             "comment": comment.strip(), "text_before": before,
                                             "text_after": action.after}, actor=person, correlation_id=self.run_id)

    def review_flag(self, fid: str, person: str, note: str) -> None:
        person = (person or "").strip()
        if not person or not note.strip():
            raise SignOffError("A name and a note are required to clear a review flag.")
        f = self.assessment.finding(fid)
        if f is None or not f.needs_review:
            raise SignOffError(f"{fid} is not flagged for review.")
        self.reviewed_flags[fid] = person
        self.preparers.add(person)
        self.audit.record("flag_reviewed", {"finding": fid, "note": note.strip(), "reasons": f.review_reasons},
                          actor=person, correlation_id=self.run_id)

    def blockers(self) -> list[str]:
        out = []
        for x in self.assessment.actions:
            if x.priority in BLOCKING and x.decision == "Proposed":
                out.append(f"{x.aid} ({x.priority}) has no decision.")
        for f in self.assessment.needs_review():
            if f.fid not in self.reviewed_flags:
                out.append(f"{f.fid} was flagged by the Challenger and has not been reviewed.")
        return out

    def sign_off(self, approver: str, role: str = "Head of Regulatory Compliance") -> None:
        approver = (approver or "").strip()
        if not approver:
            raise SignOffError("Enter the approver's name.")
        if approver in self.preparers:
            raise SignOffError("Four-eyes rule: the approver cannot be a person who prepared or decided actions.")
        blockers = self.blockers()
        if blockers:
            raise SignOffError("Sign-off is blocked: " + " ".join(blockers[:3])
                               + (f" (+{len(blockers) - 3} more)" if len(blockers) > 3 else ""))
        self.signed_off_by = approver
        self.signed_off_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        decided = {d: sum(1 for x in self.assessment.actions if x.decision == d) for d in ("Accepted", "Rejected",
                                                                                         "Edited", "Proposed")}
        self.audit.record("assessment_signed_off", {"approver": approver, "role": role, "decisions": decided,
                                                    "preparers": sorted(self.preparers)},
                          actor=approver, correlation_id=self.run_id)
