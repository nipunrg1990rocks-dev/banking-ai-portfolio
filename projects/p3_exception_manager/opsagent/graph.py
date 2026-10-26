"""The LangGraph workflow: one exception in, one controlled outcome out.

    intake -> triage -> [human_triage] -> investigate -> assess_risk -> propose_action -> control_gate
        control_gate --AUTO--> execute -> verify -> close
        control_gate --HUMAN--> human_approval (pauses; loops once per required signature)
            approve (all signatures) -> execute -> verify -> close
            reject -> close
            escalate -> escalate -> close

Human steps use LangGraph's ``interrupt()``: the graph saves its state to the
checkpointer and stops. ``Command(resume=...)`` continues it later, possibly
in another request, with the human's decision. Nodes that pause have no side
effects before the pause, because LangGraph re-runs them on resume.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from portfolio_core.audit import AuditLog
from portfolio_core.guardrails import detect_injection, redact_pii

from .playbooks import PLAYBOOKS
from .policy import AuthorisationError, authorise, check_approver, control_gate, priority_score, sla_status
from .tools import Toolbox, ToolError
from .triage import CATEGORIES, TriageModel

NODE_TITLES = {
    "intake": "Intake & guardrails",
    "triage": "Triage agent",
    "human_triage": "Human triage",
    "investigate": "Investigator agent",
    "assess_risk": "Risk & priority",
    "propose_action": "Resolution planner",
    "control_gate": "Control gate",
    "human_approval": "Human approval",
    "execute": "Executor",
    "verify": "Verifier",
    "escalate": "Escalation",
    "close": "Close & audit",
}


class CaseState(TypedDict, total=False):
    exception: dict
    redacted_text: str
    guardrails: dict
    sla: dict
    triage: dict
    facts: dict
    findings: list[dict]
    tool_calls: Annotated[list[dict], operator.add]
    priority: dict
    plan: dict
    narrative: dict
    gate: dict
    approvals: list[dict]
    approval_errors: list[str]
    execution: dict
    verification: dict
    outcome: str
    trace: Annotated[list[dict], operator.add]


class Narrator(Protocol):
    def __call__(self, state: CaseState) -> dict: ...


def _step(node: str, detail: str, status: str = "done", **extra: Any) -> list[dict]:
    return [{"node": node, "title": NODE_TITLES[node], "detail": detail, "status": status, **extra}]


def template_narrative(state: CaseState) -> dict:
    exc, plan, pr = state["exception"], state["plan"], state["priority"]
    lines = [f"{exc['exception_id']} ({CATEGORIES.get(state['triage']['category'], '')}), priority {pr['band']} "
             f"(score {pr['score']}), SLA {state['sla']['state'].lower()}.",
             "Evidence: " + " ".join(f["text"] for f in state.get("findings", [])[:3]),
             f"Proposed: {plan['summary']}"]
    return {"text": "\n\n".join(lines), "source": "template", "model": None}


VERIFY = {
    "repair_and_resubmit_payment": ("get_payment", "payment_id", lambda r: r["status"] == "RESUBMITTED"),
    "cancel_payment": ("get_payment", "payment_id", lambda r: r["status"] == "CANCELLED"),
    "release_payment": ("get_payment", "payment_id", lambda r: r["status"] == "RELEASED"),
    "keep_payment_blocked": ("get_payment", "payment_id", lambda r: r["status"] == "BLOCKED"),
    "match_nostro_items": ("get_nostro_item", "ledger_item_id", lambda r: r["status"] == "MATCHED"),
    "clear_suspense_to_account": ("get_suspense_item", "suspense_id", lambda r: r["status"] == "CLEARED"),
    "reinstruct_settlement": ("get_trade", "trade_id", lambda r: r["status"] == "REINSTRUCTED"),
    "record_credit_decision": ("get_account", "account_id", lambda r: bool(r.get("credit_decision"))),
    "request_kyc_refresh": ("get_customer", "customer_id", lambda r: r["kyc_status"] == "Review in progress"),
}
_ARG_TO_TOOL_ARG = {"ledger_item_id": "item_id"}
ESCALATION_QUEUES = {"SANCTIONS_HIT": "MLRO", "KYC_EXPIRED_BLOCK": "MLRO", "LIMIT_BREACH": "Credit Risk"}


def build_graph(tb: Toolbox, audit: AuditLog, model: TriageModel, narrator: Narrator | None = None,
                checkpointer: Any | None = None):
    narrate = narrator or template_narrative

    def log(event: str, state: CaseState, payload: dict, actor: str = "agent") -> None:
        audit.record(event, payload, actor=actor, correlation_id=state["exception"]["exception_id"])

    def new_calls(before: int) -> list[dict]:
        return [dict(c) for c in tb.calls[before:]]

    # ------------------------------------------------------------ nodes
    def intake(state: CaseState) -> dict:
        exc = state["exception"]
        raw = f"{exc['title']}. {exc['description']}"
        redacted, pii = redact_pii(raw)
        flags = ["PROMPT_INJECTION"] if detect_injection(raw) else []
        sla = sla_status(exc)
        log("exception.received", state, {"source": exc["source_system"], "amount_usd": exc["amount_usd"],
                                           "pii_redacted": pii, "flags": flags, "sla_state": sla["state"]})
        if sla["state"] == "BREACHED":
            log("sla.breach_notified", state, {"notified": "Ops Supervisor", "hours_overdue": -sla["hours_left"]})
        detail = f"SLA {sla['state'].lower()} ({sla['hours_left']:+.1f}h)."
        if pii:
            detail += f" Redacted before any model sees it: {', '.join(pii)}."
        if flags:
            detail += " Instruction-like text detected in the ticket: treated as data, case forced to human review."
        return {"redacted_text": redacted, "guardrails": {"pii": pii, "flags": flags}, "sla": sla,
                "approvals": [], "approval_errors": [], "trace": _step("intake", detail,
                                                                      "warn" if flags else "done")}

    def triage(state: CaseState) -> dict:
        exc = dict(state["exception"])
        exc["title"], exc["description"] = "", state["redacted_text"]
        r = model.predict(exc)
        result = {"category": r.category, "confidence": r.confidence, "top3": r.top3, "top_terms": r.top_terms,
                  "needs_human_triage": r.needs_human_triage, "decided_by": "model"}
        log("agent.triaged", state, {k: result[k] for k in ("category", "confidence", "needs_human_triage")})
        detail = (f"{CATEGORIES[r.category]} (confidence {r.confidence:.0%}); key terms: {', '.join(r.top_terms)}."
                  + (" Below threshold: sending to a human." if r.needs_human_triage else ""))
        return {"triage": result, "trace": _step("triage", detail, "warn" if r.needs_human_triage else "done")}

    def human_triage(state: CaseState) -> dict:
        answer = interrupt({"type": "TRIAGE", "exception_id": state["exception"]["exception_id"],
                            "model_view": state["triage"]["top3"],
                            "options": [c for c in CATEGORIES if c != "OTHER"]})
        category = answer.get("category")
        if category not in PLAYBOOKS:
            raise ValueError(f"Unknown category {category!r}")
        triage = {**state["triage"], "category": category, "decided_by": answer.get("user", "unknown"),
                  "needs_human_triage": False}
        log("human.triaged", state, {"category": category}, actor=answer.get("user", "unknown"))
        return {"triage": triage, "trace": _step("human_triage", f"{answer.get('user')} classified it as "
                                                                 f"{CATEGORIES[category]}.")}

    def investigate(state: CaseState) -> dict:
        category = state["triage"]["category"]
        before = len(tb.calls)
        try:
            facts, findings = PLAYBOOKS[category][0](state["exception"], tb)
        except (KeyError, ToolError) as exc:
            facts, findings = {"missing": str(exc)}, [{"tool": "-", "severity": "high",
                                                      "text": f"Could not gather evidence automatically: {exc}."}]
        calls = new_calls(before)
        log("agent.investigated", state, {"tools": [c["tool"] for c in calls], "findings": len(findings)})
        return {"facts": facts, "findings": findings, "tool_calls": calls,
                "trace": _step("investigate", f"Called {len(calls)} tool(s): "
                               + ", ".join(dict.fromkeys(c["tool"] for c in calls)) + ".")}

    def assess_risk(state: CaseState) -> dict:
        facts, refs = state["facts"], state["exception"]["refs"]
        before = len(tb.calls)
        customer = facts.get("customer")
        if customer is None and refs.get("customer_id"):
            try:
                customer = tb.call("get_customer", customer_id=refs["customer_id"])
            except ToolError:
                customer = None
        pr = priority_score(state["exception"], state["triage"]["category"], state["sla"], facts,
                            customer_tier=(customer or {}).get("tier"), guardrail_flags=state["guardrails"]["flags"])
        log("agent.prioritised", state, {"band": pr["band"], "score": pr["score"]})
        return {"priority": pr, "tool_calls": new_calls(before),
                "trace": _step("assess_risk", f"{pr['band']} (score {pr['score']}/100): "
                               + ", ".join(f"{k.split(' (')[0]} {v}" for k, v in pr["components"].items()) + ".")}

    def propose_action(state: CaseState) -> dict:
        category = state["triage"]["category"]
        if "missing" in state["facts"]:
            exc_id = state["exception"]["exception_id"]
            plan = {"action": "create_work_item", "action_args": {
                "queue": "Ops Investigations", "summary": f"Manual investigation of {exc_id}"},
                "summary": "Not enough linked records to act: open a manual investigation.",
                "rationale": [state["facts"]["missing"]], "steps": ["Analyst investigates and re-triages."],
                "auto_eligible": False, "auto_reason": "", "confidence": "Low", "moves_money": False}
        else:
            plan = PLAYBOOKS[category][1](state["exception"], state["facts"])
        draft = {**state, "plan": plan}
        try:
            narrative = narrate(draft)
        except Exception as exc:  # a narrator failure must never block the workflow
            narrative = {**template_narrative(draft), "error": f"LLM narrator failed, used template ({exc})"}
        log("agent.proposed", state, {"action": plan["action"], "args": plan["action_args"],
                                      "narrative_source": narrative["source"]})
        return {"plan": plan, "narrative": narrative,
                "trace": _step("propose_action", f"{plan['summary']} (confidence {plan['confidence'].lower()})")}

    def gate(state: CaseState) -> dict:
        g = control_gate(category=state["triage"]["category"], plan=state["plan"],
                         amount_usd=state["exception"]["amount_usd"],
                         triage_confidence=1.0 if state["triage"]["decided_by"] != "model"
                         else state["triage"]["confidence"], guardrail_flags=state["guardrails"]["flags"])
        if state["triage"]["decided_by"] != "model" and g["route"] == "AUTO":
            g = {**g, "route": "HUMAN", "required_approvals": [
                {"label": "Maker review", "roles": ["Ops Analyst", "Ops Supervisor", "Head of Operations"]}],
                 "reasons": ["Human-triaged cases are never processed straight through."]}
        log("control.gate", state, {"route": g["route"], "regulated": g["regulated"],
                                    "required": [s["label"] for s in g["required_approvals"]]}, actor="control")
        detail = ("Straight-through: " + g["reasons"][0]) if g["route"] == "AUTO" else (
            "Human approval required: " + " then ".join(
                f"{s['label']} ({'/'.join(s['roles'])})" for s in g["required_approvals"]) + ".")
        return {"gate": g, "trace": _step("control_gate", detail)}

    def human_approval(state: CaseState) -> dict:
        required = state["gate"]["required_approvals"]
        done = [a for a in state["approvals"] if a["decision"] == "APPROVE"]
        step = required[len(done)]
        answer = interrupt({"type": "APPROVAL", "exception_id": state["exception"]["exception_id"],
                            "step": step, "step_no": len(done) + 1, "of": len(required),
                            "proposal": state["plan"]["summary"], "prior_approvers": [a["approver"] for a in done],
                            "last_error": (state["approval_errors"] or [None])[-1]})
        decision = str(answer.get("decision", "")).upper()
        approver, comment = answer.get("approver", ""), answer.get("comment", "")
        if decision not in ("APPROVE", "REJECT", "ESCALATE"):
            decision = "INVALID"
        try:
            if decision == "INVALID":
                raise AuthorisationError("Decision must be APPROVE, REJECT or ESCALATE.")
            check_approver(step, approver, done, state["exception"].get("submitted_by"))
        except AuthorisationError as err:
            log("approval.denied", state, {"approver": approver, "step": step["label"], "reason": str(err)},
                actor=approver or "unknown")
            return {"approval_errors": [*state["approval_errors"], str(err)],
                    "trace": _step("human_approval", f"Refused: {err}", "fail")}
        record = {"step": step["label"], "approver": approver, "decision": decision, "comment": comment}
        log(f"approval.{decision.lower()}", state, record, actor=approver)
        update: dict = {"approvals": [*state["approvals"], record],
                        "trace": _step("human_approval", f"{step['label']}: {decision.lower()} by {approver}"
                                       + (f' ("{comment}")' if comment else "") + ".",
                                       "done" if decision == "APPROVE" else "warn")}
        if decision == "REJECT":
            update["outcome"] = "REJECTED"
        elif decision == "ESCALATE":
            update["outcome"] = "ESCALATED"
        return update

    def execute(state: CaseState) -> dict:
        plan, exc = state["plan"], state["exception"]
        try:
            authorise(state)
        except AuthorisationError as err:
            log("control.blocked_execution", state, {"reason": str(err)}, actor="control")
            return {"outcome": "BLOCKED_BY_CONTROL", "execution": {"error": str(err)},
                    "trace": _step("execute", f"Blocked by the final authorisation check: {err}", "fail")}
        before = len(tb.calls)
        result = tb.act(plan["action"], idempotency_key=f"{exc['exception_id']}:{plan['action']}",
                        **plan["action_args"])
        mode = "straight-through" if state["gate"]["route"] == "AUTO" else "after approval"
        log("action.executed", state, {"action": plan["action"], "args": plan["action_args"], "result": result,
                                       "mode": mode}, actor="agent:executor")
        return {"execution": {"action": plan["action"], "result": result, "mode": mode},
                "tool_calls": new_calls(before),
                "trace": _step("execute", f"Ran {plan['action']} ({mode}).")}

    def verify(state: CaseState) -> dict:
        if state.get("outcome") == "BLOCKED_BY_CONTROL":
            return {}
        action, args = state["plan"]["action"], state["plan"]["action_args"]
        before = len(tb.calls)
        if action in VERIFY:
            tool, arg, check = VERIFY[action]
            record = tb.call(tool, **{_ARG_TO_TOOL_ARG.get(arg, arg): args[arg]})
            ok = "error" not in state["execution"]["result"] and check(record)
        else:
            ok = "error" not in state["execution"]["result"]
        outcome = "AUTO_RESOLVED" if state["gate"]["route"] == "AUTO" else "RESOLVED"
        if not ok:
            outcome = "EXECUTION_UNVERIFIED"
        if action == "create_work_item" and ok:
            outcome = "ROUTED_TO_QUEUE"
        log("action.verified", state, {"ok": ok, "outcome": outcome}, actor="agent:verifier")
        return {"verification": {"ok": ok}, "outcome": outcome, "tool_calls": new_calls(before),
                "trace": _step("verify", "Read the record back: the change is in place." if ok
                               else "Read-back did not show the expected change.", "done" if ok else "fail")}

    def escalate(state: CaseState) -> dict:
        exc = state["exception"]
        approver = state["approvals"][-1]["approver"] if state["approvals"] else "agent"
        queue = ESCALATION_QUEUES.get(state["triage"]["category"], "Head of Operations")
        result = tb.act("create_work_item", idempotency_key=f"{exc['exception_id']}:escalation",
                        queue=queue, summary=f"Escalated {exc['exception_id']} by {approver}")
        log("exception.escalated", state, result, actor=approver)
        return {"execution": {"action": "create_work_item", "result": result, "mode": "escalation"},
                "trace": _step("escalate", f"Escalated to the {queue} queue ({result['work_item_id']}).")}

    def close(state: CaseState) -> dict:
        outcome = state.get("outcome") or "CLOSED"
        log("exception.closed", state, {"outcome": outcome, "approvals": len(state.get("approvals", []))})
        return {"outcome": outcome, "trace": _step("close", f"Outcome {outcome}; audit trail sealed.")}

    # ---------------------------------------------------------- routing
    def after_triage(state: CaseState) -> str:
        return "human_triage" if state["triage"]["needs_human_triage"] else "investigate"

    def after_gate(state: CaseState) -> str:
        return "execute" if state["gate"]["route"] == "AUTO" else "human_approval"

    def after_approval(state: CaseState) -> str:
        if state.get("outcome") == "REJECTED":
            return "close"
        if state.get("outcome") == "ESCALATED":
            return "escalate"
        approved = sum(1 for a in state["approvals"] if a["decision"] == "APPROVE")
        return "execute" if approved >= len(state["gate"]["required_approvals"]) else "human_approval"

    g = StateGraph(CaseState)
    for name, fn in [("intake", intake), ("triage", triage), ("human_triage", human_triage),
                     ("investigate", investigate), ("assess_risk", assess_risk), ("propose_action", propose_action),
                     ("control_gate", gate), ("human_approval", human_approval), ("execute", execute),
                     ("verify", verify), ("escalate", escalate), ("close", close)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_edge("intake", "triage")
    g.add_conditional_edges("triage", after_triage, ["human_triage", "investigate"])
    g.add_edge("human_triage", "investigate")
    g.add_edge("investigate", "assess_risk")
    g.add_edge("assess_risk", "propose_action")
    g.add_edge("propose_action", "control_gate")
    g.add_conditional_edges("control_gate", after_gate, ["execute", "human_approval"])
    g.add_conditional_edges("human_approval", after_approval, ["human_approval", "execute", "escalate", "close"])
    g.add_edge("execute", "verify")
    g.add_edge("verify", "close")
    g.add_edge("escalate", "close")
    g.add_edge("close", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())

