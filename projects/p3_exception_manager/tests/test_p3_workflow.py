"""End-to-end tests of the LangGraph workflow through the ExceptionManager."""

import pytest
from conftest import find

from opsagent.manager import ClaudeNarrator, ExceptionManager
from portfolio_core.llm import ClaudeClient


def test_graph_has_the_designed_nodes(manager):
    nodes = set(manager.graph.get_graph().nodes)
    assert {"intake", "triage", "human_triage", "investigate", "assess_risk", "propose_action", "control_gate",
            "human_approval", "execute", "verify", "escalate", "close"} <= nodes


def test_every_seed_case_takes_its_expected_route(processed):
    for v in processed.all_cases():
        exp = processed.exceptions[v.exception_id]["expected"]
        route = "TRIAGE" if v.status == "AWAITING_TRIAGE" else v.state["gate"]["route"]
        assert route == exp["route"], v.exception_id
        if route == "AUTO":
            assert v.status == "AUTO_RESOLVED" and v.state["verification"]["ok"]
        if exp["regulated"]:
            assert v.status == "AWAITING_APPROVAL" and not v.state.get("execution")


def test_maker_checker_with_segregation_of_duties(manager):
    exc_id = find(manager, lambda e: e["expected"]["category"] == "PAYMENT_REPAIR" and e["amount_usd"] > 50_000
                  and e["amount_usd"] < 1_000_000 and e["reason_code"] == "AC01")
    v = manager.start(exc_id)
    assert v.pending["step"]["label"] == "Maker review" and v.pending["of"] == 2
    v = manager.approve(exc_id, "a.mensah", "SSI checked")
    assert v.pending["step"]["label"] == "Four-eyes check"
    v = manager.approve(exc_id, "a.mensah")  # same person again
    assert "different person" in v.pending["last_error"] or "cannot sign" in v.pending["last_error"]
    v = manager.approve(exc_id, "j.ortiz")  # analyst lacks checker authority
    assert "cannot sign" in v.pending["last_error"] and not v.state.get("execution")
    v = manager.approve(exc_id, "p.nair", "ok")
    assert v.status == "RESOLVED" and v.state["verification"]["ok"]
    pid = manager.exceptions[exc_id]["refs"]["payment_id"]
    assert manager.bank.payments[pid]["status"] == "RESUBMITTED"
    denied = [e for e in manager.audit.events(correlation_id=exc_id) if e.event_type == "approval.denied"]
    assert len(denied) == 2


def test_reject_closes_without_touching_records(manager):
    exc_id = find(manager, lambda e: e["expected"].get("is_duplicate") is True and e["amount_usd"] < 50_000)
    manager.start(exc_id)
    pid = manager.exceptions[exc_id]["refs"]["payment_id"]
    v = manager.resume(exc_id, {"decision": "REJECT", "approver": "j.ortiz", "comment": "customer confirms both"})
    assert v.status == "REJECTED" and not v.state.get("execution")
    assert manager.bank.payments[pid]["status"] == "HELD"


def test_sanctions_case_needs_compliance_and_escalates_to_mlro(manager):
    exc_id = find(manager, lambda e: e["expected"].get("agent_view") == "strong match"
                  and not e["expected"].get("injection"))
    v = manager.start(exc_id)
    assert v.state["plan"]["action"] == "keep_payment_blocked"
    v = manager.resume(exc_id, {"decision": "APPROVE", "approver": "r.walsh"})  # Head of Ops is not Compliance
    assert "cannot sign" in v.pending["last_error"]
    v = manager.resume(exc_id, {"decision": "ESCALATE", "approver": "s.okafor", "comment": "to MLRO"})
    assert v.status == "ESCALATED" and v.state["execution"]["result"]["queue"] == "MLRO"


def test_prompt_injection_in_ticket_is_contained(manager):
    exc_id = find(manager, lambda e: e["expected"].get("injection"))
    v = manager.start(exc_id)
    assert v.state["guardrails"]["flags"] == ["PROMPT_INJECTION"]
    assert v.status == "AWAITING_APPROVAL" and v.state["plan"]["action"] == "keep_payment_blocked"
    pid = manager.exceptions[exc_id]["refs"]["payment_id"]
    assert manager.bank.payments[pid]["status"] == "HELD_SCREENING"


def test_low_confidence_goes_to_human_triage_then_continues(manager):
    exc_id = find(manager, lambda e: e["expected"]["route"] == "TRIAGE")
    v = manager.start(exc_id)
    assert v.pending["type"] == "TRIAGE" and "investigate" not in [t["node"] for t in v.state["trace"]]
    v = manager.resume(exc_id, {"category": "DUPLICATE_PAYMENT", "user": "a.mensah"})
    assert v.state["triage"]["decided_by"] == "a.mensah"
    assert v.state["gate"]["route"] == "HUMAN"  # human-triaged cases never go straight through
    assert v.state["plan"]["action"] == "create_work_item"  # no payment linked, so manual investigation


def test_submitter_cannot_approve_their_own_exception(manager):
    exc_id = manager.submit(title="Customer chasing refund", description="Please check the refund for Mrs Hart",
                            source_system="Branch Ticket", amount=120, currency="GBP", submitted_by="a.mensah")
    manager.start(exc_id)
    v = manager.resume(exc_id, {"category": "PAYMENT_REPAIR", "user": "j.ortiz"})
    v = manager.approve(exc_id, "a.mensah")
    assert "submitted" in v.pending["last_error"]


def test_resume_requires_a_pending_decision(processed):
    auto = next(v for v in processed.all_cases() if v.status == "AUTO_RESOLVED")
    with pytest.raises(ValueError):
        processed.resume(auto.exception_id, {"decision": "APPROVE", "approver": "p.nair"})


def test_restarting_a_case_does_not_rerun_it(manager):
    exc_id = find(manager, lambda e: e["expected"]["route"] == "AUTO")
    manager.start(exc_id)
    n = len(manager.toolbox.calls)
    manager.start(exc_id)
    assert len(manager.toolbox.calls) == n


def test_audit_chain_covers_the_case_and_detects_tampering(processed):
    exc_id = next(v.exception_id for v in processed.all_cases() if v.status == "AUTO_RESOLVED")
    types = [e.event_type for e in reversed(processed.audit.events(correlation_id=exc_id))]
    assert types[:2] == ["exception.received", "agent.triaged"]
    assert {"control.gate", "action.executed", "action.verified", "exception.closed"} <= set(types)
    assert processed.audit.verify_chain() == (True, None)


def test_tampering_breaks_the_chain(manager):
    manager.run_queue()
    manager.audit._conn.execute("UPDATE audit_events SET payload = '{}' WHERE seq = 5")
    ok, broken_at = manager.audit.verify_chain()
    assert not ok and broken_at == 5


def test_narrator_failure_never_blocks_the_case():
    def broken(_state):
        raise RuntimeError("model down")

    m = ExceptionManager(narrator=broken)
    v = m.start(find(m, lambda e: e["expected"]["route"] == "AUTO"))
    assert v.status == "AUTO_RESOLVED" and "error" in v.state["narrative"]


class _FakeMessages:
    def __init__(self):
        self.seen = []

    def create(self, **kwargs):
        self.seen.append(kwargs)

        class Block:
            type, text = "text", "Case note drafted from the evidence."

        class Usage:
            input_tokens, output_tokens = 300, 40

        class Resp:
            content, usage, stop_reason = [Block()], Usage(), "end_turn"

        return Resp()


def test_claude_narrator_only_writes_the_note_and_sees_redacted_text():
    fake = _FakeMessages()
    client = ClaudeClient(client=type("C", (), {"messages": fake})(), model="test-model")
    m = ExceptionManager(narrator=ClaudeNarrator(client))
    exc_id = find(m, lambda e: e["expected"]["category"] == "LIMIT_BREACH")
    v = m.start(exc_id)
    assert v.state["narrative"]["source"] == "claude" and v.state["narrative"]["text"].startswith("Case note")
    assert v.state["gate"]["route"] == "HUMAN"  # the note cannot change the route
    prompt = fake.seen[0]["messages"][0]["content"]
    acct = m.exceptions[exc_id]["refs"]["account_id"]
    assert acct not in prompt and "[REDACTED_ACCOUNT_NUMBER]" in prompt
