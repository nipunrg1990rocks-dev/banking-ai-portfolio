import pytest

from opsagent.policy import (
    AuthorisationError,
    authorise,
    check_approver,
    control_gate,
    priority_score,
    sla_status,
)

MAKER = {"label": "Maker review", "roles": ["Ops Analyst", "Ops Supervisor", "Head of Operations"]}
CHECKER = {"label": "Four-eyes check", "roles": ["Ops Supervisor", "Head of Operations"]}
STP_PLAN = {"action": "match_nostro_items", "auto_eligible": True, "auto_reason": "timing difference"}


def gate(category="NOSTRO_BREAK", plan=STP_PLAN, amount=5_000, conf=0.95, flags=()):
    return control_gate(category=category, plan=plan, amount_usd=amount, triage_confidence=conf,
                        guardrail_flags=list(flags))


def test_low_value_reversible_case_goes_straight_through():
    assert gate()["route"] == "AUTO"


@pytest.mark.parametrize("category", ["SANCTIONS_HIT", "KYC_EXPIRED_BLOCK", "LIMIT_BREACH"])
def test_regulated_decisions_are_never_automated(category):
    g = gate(category=category, amount=1)
    assert g["route"] == "HUMAN" and g["regulated"]
    assert g["required_approvals"][0]["roles"] in (["Compliance Officer"], ["Credit Officer"])


@pytest.mark.parametrize("kwargs", [{"amount": 10_001}, {"conf": 0.6}, {"flags": ["PROMPT_INJECTION"]},
                                    {"plan": {**STP_PLAN, "auto_eligible": False}},
                                    {"category": "PAYMENT_REPAIR"}])
def test_any_single_failed_condition_blocks_straight_through(kwargs):
    assert gate(**kwargs)["route"] == "HUMAN"


def test_four_eyes_above_threshold_and_authority_scales_with_amount():
    plan = {"action": "release_payment", "auto_eligible": False}
    assert [s["label"] for s in gate("DUPLICATE_PAYMENT", plan, 40_000)["required_approvals"]] == ["Maker review"]
    big = gate("DUPLICATE_PAYMENT", plan, 400_000)["required_approvals"]
    assert [s["label"] for s in big] == ["Maker review", "Four-eyes check"]
    assert gate("DUPLICATE_PAYMENT", plan, 2_000_000)["required_approvals"][1]["roles"] == ["Head of Operations"]


def test_work_items_need_one_signature_whatever_the_amount():
    plan = {"action": "create_work_item", "auto_eligible": False}
    assert len(gate("SETTLEMENT_FAIL", plan, 5_000_000)["required_approvals"]) == 1


def test_approver_checks():
    check_approver(MAKER, "a.mensah", [], None)
    with pytest.raises(AuthorisationError, match="cannot sign"):
        check_approver(CHECKER, "a.mensah", [], None)
    with pytest.raises(AuthorisationError, match="different person"):
        check_approver(CHECKER, "p.nair", [{"approver": "p.nair"}], None)
    with pytest.raises(AuthorisationError, match="submitted"):
        check_approver(MAKER, "a.mensah", [], "a.mensah")
    with pytest.raises(AuthorisationError, match="Unknown"):
        check_approver(MAKER, "intruder", [], None)


def _state(route, required, approvals, category="DUPLICATE_PAYMENT", amount=100_000.0, flags=()):
    return {"gate": {"route": route, "required_approvals": required}, "triage": {"category": category},
            "exception": {"amount_usd": amount}, "guardrails": {"flags": list(flags)}, "approvals": approvals}


def test_final_authorisation_rechecks_everything():
    with pytest.raises(AuthorisationError):
        authorise(_state("HUMAN", [MAKER, CHECKER], [{"decision": "APPROVE", "approver": "a.mensah"}]))
    authorise(_state("HUMAN", [MAKER, CHECKER], [{"decision": "APPROVE", "approver": "a.mensah"},
                                                 {"decision": "APPROVE", "approver": "p.nair"}]))
    with pytest.raises(AuthorisationError):  # an AUTO route that should never have been granted
        authorise(_state("AUTO", [], [], category="SANCTIONS_HIT", amount=10))


def test_sla_and_priority():
    exc = {"created_at": "2026-10-26T08:00:00Z", "sla_hours": 4, "amount_usd": 2_000_000}
    sla = sla_status(exc)
    assert sla["state"] == "ON TRACK" and sla["hours_left"] == 3
    breached = sla_status({**exc, "created_at": "2026-10-25T08:00:00Z"})
    assert breached["state"] == "BREACHED"
    hi = priority_score(exc, "SANCTIONS_HIT", breached, {}, customer_tier="Platinum", guardrail_flags=[])
    lo = priority_score({**exc, "amount_usd": 50}, "GL_SUSPENSE", sla, {}, customer_tier=None, guardrail_flags=[])
    assert hi["band"] == "P1" and lo["band"] in ("P3", "P4") and hi["score"] > lo["score"]
    assert sum(hi["components"].values()) == pytest.approx(hi["score"])
