"""Controls: priority scoring, the control gate and the delegated-authority matrix.

This module is deliberately plain Python with no model in it. The agent proposes;
these rules decide who must agree before anything happens:

* **Regulated decisions are never automated.** Sanctions dispositions, KYC
  decisions and credit decisions always need the named specialist role.
* **Straight-through processing** is allowed only for low-value, reversible,
  non-regulated actions whose playbook marked them eligible, with a confident
  triage and clean guardrails.
* **Four-eyes** (maker-checker) above a value threshold, with **segregation of
  duties**: every approver must be a different person, and nobody approves an
  exception they submitted.
* ``authorise`` re-checks all of this immediately before execution, so a bug
  elsewhere in the graph cannot slip an action past the gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .data import NOW, STAFF

REGULATED = {
    "SANCTIONS_HIT": "Compliance Officer",
    "KYC_EXPIRED_BLOCK": "Compliance Officer",
    "LIMIT_BREACH": "Credit Officer",
}
STP_CATEGORIES = {"NOSTRO_BREAK", "GL_SUSPENSE"}
STP_LIMIT_USD = 10_000
FOUR_EYES_USD = 50_000
MIN_TRIAGE_CONFIDENCE_FOR_STP = 0.80

# Delegated authority (USD equivalent) for operational, non-regulated actions.
ROLE_LIMITS_USD = {
    "Ops Analyst": 50_000,
    "Ops Supervisor": 1_000_000,
    "Head of Operations": math.inf,
    "Compliance Officer": 0,  # specialist roles approve their own domain only
    "Credit Officer": 0,
}
STAFF_BY_USER = {s["user"]: s for s in STAFF}


# ----------------------------------------------------------------------- SLA
def sla_status(exc: dict, now: datetime = NOW) -> dict:
    created = datetime.fromisoformat(exc["created_at"].replace("Z", "+00:00"))
    due = created + timedelta(hours=exc["sla_hours"])
    left = (due - now).total_seconds() / 3600
    state = "BREACHED" if left < 0 else "AT RISK" if left <= max(1.0, exc["sla_hours"] * 0.25) else "ON TRACK"
    return {"due": due.isoformat().replace("+00:00", "Z"), "hours_left": round(left, 2), "state": state,
            "age_hours": round((now - created).total_seconds() / 3600, 2)}


# ------------------------------------------------------------------ priority
def priority_score(exc: dict, category: str, sla: dict, facts: dict, *, customer_tier: str | None,
                   guardrail_flags: list[str]) -> dict:
    amount = exc["amount_usd"]
    exposure = round(min(35.0, 35.0 * math.log10(amount + 1) / 7.0), 1)  # USD 10m -> full marks
    h = sla["hours_left"]
    urgency = 25 if h < 0 else 22 if h <= 1 else 16 if h <= 4 else 10 if h <= 12 else 4
    regulatory = 20 if category in REGULATED else 0
    tier = {"Platinum": 10, "Gold": 6}.get(customer_tier or "", 3)
    aux = 0
    reasons = []
    if facts.get("penalty", {}).get("days_failed", 0) >= 1:
        aux, reasons = 8, ["settlement penalties accruing"]
    if facts.get("suspense", {}).get("age_days", 0) > 30:
        aux, reasons = 10, ["suspense item aged over 30 days"]
    if guardrail_flags:
        aux = max(aux, 6)
        reasons.append("guardrail flag on the ticket text")
    total = round(min(100.0, exposure + urgency + regulatory + tier + aux), 1)
    band = "P1" if total >= 70 else "P2" if total >= 50 else "P3" if total >= 30 else "P4"
    return {"score": total, "band": band, "components": {
        "financial exposure (0-35)": exposure, "SLA urgency (0-25)": urgency, "regulatory (0-20)": regulatory,
        "customer tier (0-10)": tier, "ageing / penalties / flags (0-10)": aux}, "notes": reasons}


# --------------------------------------------------------------- control gate
@dataclass(frozen=True)
class ApprovalStep:
    label: str
    roles: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"label": self.label, "roles": list(self.roles)}


def checker_roles(amount_usd: float) -> tuple[str, ...]:
    return tuple(r for r in ("Ops Supervisor", "Head of Operations") if ROLE_LIMITS_USD[r] >= amount_usd)


def control_gate(*, category: str, plan: dict, amount_usd: float, triage_confidence: float,
                 guardrail_flags: list[str]) -> dict:
    """Decide AUTO (straight-through) or HUMAN, and which approvals are needed."""
    reasons: list[str] = []
    regulated = category in REGULATED
    if regulated:
        reasons.append(f"{category} is a regulated decision: a {REGULATED[category]} must decide.")
    if not plan.get("auto_eligible"):
        reasons.append(plan.get("auto_reason") or "The playbook does not allow straight-through processing here.")
    if category not in STP_CATEGORIES:
        reasons.append(f"{category} is not on the straight-through list.")
    if amount_usd > STP_LIMIT_USD:
        reasons.append(f"USD {amount_usd:,.0f} is above the straight-through limit of USD {STP_LIMIT_USD:,}.")
    if triage_confidence < MIN_TRIAGE_CONFIDENCE_FOR_STP:
        reasons.append(f"Triage confidence {triage_confidence:.2f} is below {MIN_TRIAGE_CONFIDENCE_FOR_STP}.")
    if guardrail_flags:
        reasons.append("Guardrails flagged the ticket text (" + ", ".join(guardrail_flags) + ").")

    if not reasons:
        return {"route": "AUTO", "regulated": False, "required_approvals": [],
                "reasons": [f"Eligible for straight-through processing: {plan.get('auto_reason')}",
                            f"Below USD {STP_LIMIT_USD:,}, reversible, non-regulated, triage confident."]}

    steps: list[ApprovalStep] = []
    if regulated:
        steps.append(ApprovalStep(f"{REGULATED[category]} decision", (REGULATED[category],)))
        if amount_usd > FOUR_EYES_USD:
            steps.append(ApprovalStep("Four-eyes check", checker_roles(amount_usd)))
    else:
        steps.append(ApprovalStep("Maker review", ("Ops Analyst", "Ops Supervisor", "Head of Operations")))
        # Opening a work item changes no records, so it needs one pair of eyes whatever the amount.
        if amount_usd > FOUR_EYES_USD and plan.get("action") != "create_work_item":
            steps.append(ApprovalStep("Four-eyes check", checker_roles(amount_usd)))
            reasons.append(f"Above USD {FOUR_EYES_USD:,}: maker-checker with a second, different approver "
                           f"holding authority for USD {amount_usd:,.0f}.")
    return {"route": "HUMAN", "regulated": regulated, "required_approvals": [s.as_dict() for s in steps],
            "reasons": reasons}


# ------------------------------------------------------------ authorisation
class AuthorisationError(PermissionError):
    pass


def check_approver(step: dict, approver: str, prior: list[dict], submitted_by: str | None) -> None:
    staff = STAFF_BY_USER.get(approver)
    if not staff:
        raise AuthorisationError(f"Unknown approver '{approver}'.")
    if staff["role"] not in step["roles"]:
        raise AuthorisationError(f"{staff['name']} ({staff['role']}) cannot sign '{step['label']}'; needs one of: "
                                 + ", ".join(step["roles"]) + ".")
    if approver == submitted_by:
        raise AuthorisationError("Segregation of duties: you submitted this exception, so you cannot approve it.")
    if any(a["approver"] == approver for a in prior):
        raise AuthorisationError("Segregation of duties: each approval must come from a different person.")


def authorise(state: dict) -> None:
    """Final check before any action tool runs. Raises if the action is not fully authorised."""
    gate = state["gate"]
    category = state["triage"]["category"]
    if gate["route"] == "AUTO":
        if category in REGULATED or state["exception"]["amount_usd"] > STP_LIMIT_USD or state["guardrails"]["flags"]:
            raise AuthorisationError("Straight-through route is not permitted for this exception.")
        return
    approvals = [a for a in state.get("approvals", []) if a["decision"] == "APPROVE"]
    required = gate["required_approvals"]
    if len(approvals) < len(required):
        raise AuthorisationError(f"{len(approvals)} of {len(required)} required approvals recorded.")
    seen: list[dict] = []
    for step, a in zip(required, approvals, strict=False):
        check_approver(step, a["approver"], seen, state["exception"].get("submitted_by"))
        seen.append(a)
