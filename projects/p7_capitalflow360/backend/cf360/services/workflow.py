"""Maker-checker approvals, amendments (versions), cancellation with reversal, and repairs."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cf360 import lifecycle
from cf360 import models as m
from cf360.clock import utcnow
from cf360.config import defect
from cf360.rules.calc import dec
from cf360.services import booking, processing, refcache
from cf360.services.core import audit, jsonable, post, raise_exception, resolve_exception, trade_snapshot

ECONOMIC_FIELDS = {"quantity", "price", "settlement_date", "trade_date", "counterparty_id", "account_id", "side", "end_date",
                   "allocations", "terms"}
NON_ECONOMIC_FIELDS = {"book", "trader_id"}


class WorkflowError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def lock_trade(s: Session, trade_id: str, expected_version: int | None) -> m.Trade:
    t = s.scalars(select(m.Trade).where(m.Trade.trade_id == trade_id).with_for_update()).first()
    if t is None:
        raise WorkflowError(404, "E_NOT_FOUND", f"Trade {trade_id} not found")
    if expected_version is not None and t.version != expected_version:
        raise WorkflowError(409, "E_STALE_VERSION", f"Trade is at version {t.version}, you sent {expected_version} (R098)")
    return t


def request_approval(s: Session, request_type: str, entity_type: str, entity_id: str, payload: dict, maker: m.User,
                     reason: str | None, sandbox_id=None) -> m.Approval:
    pending = s.scalars(select(m.Approval).where(m.Approval.entity_id == entity_id, m.Approval.status == "PENDING",
                                                 m.Approval.request_type == request_type)).first()
    if pending:
        raise WorkflowError(409, "E_APPROVAL_PENDING", f"{request_type} already pending approval {pending.approval_id}")
    a = m.Approval(approval_id="APR-" + uuid.uuid4().hex[:10].upper(), request_type=request_type, entity_type=entity_type,
                   entity_id=entity_id, payload=jsonable(payload), reason=reason, maker=maker.username, status="PENDING",
                   sandbox_id=sandbox_id)
    s.add(a)
    s.flush()
    audit(s, "APPROVAL_REQUESTED", "approval", a.approval_id, after={"type": request_type, "entity": entity_id,
                                                                    "payload": payload}, actor=(maker.username, maker.role))
    return a


def amend(s: Session, trade_id: str, version: int | None, changes: dict, reason: str, user: m.User) -> dict:
    t = lock_trade(s, trade_id, version)
    econ = {k: v for k, v in changes.items() if k in ECONOMIC_FIELDS}
    non = {k: v for k, v in changes.items() if k in NON_ECONOMIC_FIELDS}
    unknown = set(changes) - ECONOMIC_FIELDS - NON_ECONOMIC_FIELDS
    if unknown:
        raise WorkflowError(422, "E_FIELD_NOT_AMENDABLE", f"Fields cannot be amended: {sorted(unknown)}")
    if econ and (lifecycle.is_settled_state(t.status) or t.status == "CANCELLED"):
        raise WorkflowError(409, "E_AMEND_AFTER_SETTLEMENT", f"Trade is {t.status}; cancel and rebook instead (R090)")
    out = {"trade_id": trade_id, "applied": {}, "approval_id": None}
    if non:
        before = trade_snapshot(t)
        for k, v in non.items():
            setattr(t, k, v)
        t.version += 1
        s.add(m.TradeVersion(trade_id=t.trade_id, version=t.version, snapshot=trade_snapshot(t), change_type="NON_ECONOMIC_AMEND",
                             changed_by=user.username))
        audit(s, "TRADE_AMENDED", "trade", t.trade_id, before=before, after=trade_snapshot(t), details={"reason": reason})
        out["applied"] = non
    if econ:
        a = request_approval(s, "TRADE_AMEND", "trade", trade_id, {"changes": econ, "version": t.version}, user, reason, t.sandbox_id)
        out["approval_id"] = a.approval_id
        if "PENDING_AMENDMENT" not in (t.flags or []):
            t.flags = [*(t.flags or []), "PENDING_AMENDMENT"]
    out["version"] = t.version
    return out


def apply_amendment(s: Session, t: m.Trade, changes: dict, actor: tuple[str, str]) -> None:
    if lifecycle.is_settled_state(t.status) or t.status == "CANCELLED":
        raise WorkflowError(409, "E_AMEND_AFTER_SETTLEMENT", f"Trade became {t.status} before approval (R090)")
    ref = refcache.get(s)
    before = trade_snapshot(t)
    payload = {c.name: getattr(t, c.name) for c in m.Trade.__table__.columns}
    payload.update({k: v for k, v in changes.items() if k != "allocations"})
    payload["terms"] = {**(t.terms or {}), **(changes.get("terms") or {})}
    for k in ("trade_date", "settlement_date", "end_date"):
        if isinstance(payload.get(k), str):
            payload[k] = date.fromisoformat(payload[k])
    norm = booking.normalise(payload)
    from cf360.rules import engine
    issues, ctx = engine.validate(s, ref, norm | {"source_trade_ref": "AMEND-CHECK-" + t.trade_id})
    errors = [i.as_dict() for i in issues if i.severity == "ERROR" and i.code not in ("E_DUPLICATE", "E_LIMIT_BREACH")]
    if errors:
        raise WorkflowError(422, "E_VALIDATION", "; ".join(e["code"] for e in errors))
    for ins in s.scalars(select(m.SettlementInstruction).where(m.SettlementInstruction.trade_id == t.trade_id,
                                                               m.SettlementInstruction.status == "PENDING")):
        ins.status = "CANCELLED"
    for k in ("quantity", "price", "counterparty_id", "account_id", "side"):
        if k in changes:
            setattr(t, k, dec(changes[k]) if k in ("quantity", "price") else changes[k])
    for k in ("trade_date", "settlement_date", "end_date"):
        if k in changes:
            setattr(t, k, payload[k])
    t.settlement_date = ctx.get("settlement_date") or t.settlement_date
    t.terms = jsonable(payload["terms"])
    if "allocations" in changes or "quantity" in changes:
        for a in s.scalars(select(m.Allocation).where(m.Allocation.trade_id == t.trade_id, m.Allocation.status == "ACTIVE")):
            a.status = "SUPERSEDED"
        allocs = changes.get("allocations") or [{"account_id": t.account_id, "alloc_qty": t.quantity}]
        if sum(dec(a["alloc_qty"]) for a in allocs) != dec(t.quantity):
            raise WorkflowError(422, "E_ALLOC_QTY_MISMATCH", "Allocations do not sum to the amended quantity (R024)")
        for i, a in enumerate(allocs, start=1):
            s.add(m.Allocation(allocation_id=f"{t.trade_id}-V{t.version + 1}-A{i}", trade_id=t.trade_id, alloc_seq=i,
                               account_id=a["account_id"], alloc_qty=dec(a["alloc_qty"])))
        s.flush()
    t.version += 1
    t.match_status = None
    t.flags = [f for f in (t.flags or []) if f != "PENDING_AMENDMENT"]
    if t.status not in ("CAPTURED", "ENRICHED", "ALLOCATED"):
        booking.transition(s, t, "ALLOCATED", actor, "economic amendment; re-confirmation required")
    booking.enrich(s, t, actor)  # recomputes economics and allocation amounts; no status change from ALLOCATED
    s.add(m.TradeVersion(trade_id=t.trade_id, version=t.version, snapshot=trade_snapshot(t), change_type="ECONOMIC_AMEND",
                         changed_by=actor[0]))
    audit(s, "TRADE_AMENDED", "trade", t.trade_id, before=before, after=trade_snapshot(t), actor=actor)


def cancel(s: Session, trade_id: str, version: int | None, reason: str, user: m.User, rebook: dict | None = None) -> dict:
    t = lock_trade(s, trade_id, version)
    if t.status == "CANCELLED":
        raise WorkflowError(409, "E_INVALID_TRANSITION", "Trade already cancelled")
    a = request_approval(s, "TRADE_CANCEL", "trade", trade_id, {"version": t.version, "rebook": rebook}, user, reason, t.sandbox_id)
    return {"trade_id": trade_id, "approval_id": a.approval_id}


def apply_cancel(s: Session, t: m.Trade, payload: dict, actor: tuple[str, str]) -> dict:
    lifecycle.check(t.product_type, t.status, "CANCELLED")
    reversed_postings = 0
    entries = list(s.scalars(select(m.LedgerEntry).where(m.LedgerEntry.trade_id == t.trade_id, m.LedgerEntry.reversal_of.is_(None))))
    by_posting: dict[str, list[m.LedgerEntry]] = {}
    for e in entries:
        by_posting.setdefault(e.source_event_id, []).append(e)
    today = processing_business_date(s)
    for pid, legs in by_posting.items():
        legs.sort(key=lambda e: e.leg_seq)
        if post(s, f"REV:{pid}", legs[0].ledger, [(e.ledger_account, e.key, -dec(e.amount)) for e in legs],
                max(today, legs[0].value_date), "REVERSAL", t.trade_id, t.sandbox_id, reversal_of=pid):
            reversed_postings += 1
    for ins in s.scalars(select(m.SettlementInstruction).where(m.SettlementInstruction.trade_id == t.trade_id,
                                                               m.SettlementInstruction.status != "SETTLED")):
        ins.status = "CANCELLED"
    booking.transition(s, t, "CANCELLED", actor, "approved cancellation")
    for e in s.scalars(select(m.ExceptionItem).where(m.ExceptionItem.trade_id == t.trade_id, m.ExceptionItem.status == "OPEN")):
        e.status, e.resolution_note, e.resolved_business_date = "RESOLVED", "trade cancelled", today
    out = {"reversed_postings": reversed_postings}
    rb = payload.get("rebook")
    if rb:
        snap = {c.name: getattr(t, c.name) for c in m.Trade.__table__.columns}
        snap.update(rb)
        snap["trade_id"] = None
        snap["source_trade_ref"] = f"{t.source_trade_ref}-RB{t.version}"
        snap["allocations"] = rb.get("allocations")
        snap["terms"] = {**(t.terms or {}), **(rb.get("terms") or {})}
        new = booking.book(s, {**snap, **{k: snap[k] for k in ("trade_date", "settlement_date")}}, actor, t.sandbox_id)
        new.rebook_of = t.trade_id
        out["rebooked_trade_id"] = new.trade_id
    return out


def processing_business_date(s: Session) -> date:
    c = s.get(m.SystemClock, 1)
    return c.business_date if c else date.today()


def decide(s: Session, approval_id: str, approve: bool, note: str | None, user: m.User) -> dict:
    a = s.scalars(select(m.Approval).where(m.Approval.approval_id == approval_id).with_for_update()).first()
    if a is None:
        raise WorkflowError(404, "E_NOT_FOUND", "Approval not found")
    if a.status != "PENDING":
        raise WorkflowError(409, "E_APPROVAL_DECIDED", f"Approval already {a.status}")
    if a.maker == user.username and not defect("D4"):
        raise WorkflowError(403, "E_SELF_APPROVAL", "Maker cannot approve their own request (R092)")
    if user.role != "CHECKER" and not (defect("D4") and a.maker == user.username):
        raise WorkflowError(403, "E_FORBIDDEN", "Only a checker can decide approvals")
    actor = (user.username, user.role)
    result: dict = {}
    if approve:
        result = _apply(s, a, actor)
    a.status = "APPROVED" if approve else "REJECTED"
    a.checker, a.decision_note, a.decided_at = user.username, note, utcnow()
    if not approve and a.entity_type == "trade":
        t = s.get(m.Trade, a.entity_id)
        if t is not None:
            t.flags = [f for f in (t.flags or []) if f != "PENDING_AMENDMENT"]
    audit(s, "APPROVAL_" + a.status, "approval", a.approval_id, after={"result": result, "note": note}, actor=actor)
    return {"approval_id": a.approval_id, "status": a.status, "result": result}


def _apply(s: Session, a: m.Approval, actor) -> dict:
    p = a.payload
    if a.request_type == "TRADE_AMEND":
        t = lock_trade(s, a.entity_id, p.get("version"))
        apply_amendment(s, t, p["changes"], actor)
        return {"version": t.version, "status": t.status}
    if a.request_type == "TRADE_CANCEL":
        t = lock_trade(s, a.entity_id, p.get("version"))
        return apply_cancel(s, t, p, actor)
    if a.request_type == "FORCE_MATCH":
        t = lock_trade(s, a.entity_id, None)
        if t.status != "ALLOCATED":
            raise WorkflowError(409, "E_INVALID_TRANSITION", f"Trade is {t.status}, not ALLOCATED")
        t.match_status = "FORCE_MATCHED"
        booking.transition(s, t, "CONFIRMED", actor, "force-matched after review")
        for et in ("MATCH_BREAK", "MISSING_CONFIRMATION"):
            resolve_exception(s, et, t.trade_id, "force matched with approval")
        return {"status": t.status}
    if a.request_type == "SSI_CREATE":
        d = p["ssi"]
        ssi = m.SSI(ssi_id=d.get("ssi_id") or "SSI-" + uuid.uuid4().hex[:8].upper(), owner_type=d["owner_type"], owner_id=d["owner_id"],
                    currency=d["currency"], settlement_location=d["settlement_location"], agent_code=d.get("agent_code", "SIMBNEWXX"),
                    safekeeping_account=d.get("safekeeping_account"), cash_account=d.get("cash_account", f"CSH-{d['owner_id']}"),
                    effective_from=date.fromisoformat(d["effective_from"]),
                    effective_to=date.fromisoformat(d["effective_to"]) if d.get("effective_to") else None,
                    status="ACTIVE", version=1, sandbox_id=a.sandbox_id)
        s.add(ssi)
        s.flush()
        repaired = []
        for e in s.scalars(select(m.ExceptionItem).where(m.ExceptionItem.exception_type == "SSI_MISSING", m.ExceptionItem.status == "OPEN")):
            t = s.get(m.Trade, e.trade_id)
            if t is not None and t.status == "CAPTURED" and booking.enrich(s, t, actor):
                repaired.append(t.trade_id)
        return {"ssi_id": ssi.ssi_id, "repaired_trades": repaired}
    if a.request_type == "CLIENT_UPDATE":
        c = s.get(m.Client, a.entity_id)
        before = {k: getattr(c, k) for k in p["changes"]}
        for k, v in p["changes"].items():
            setattr(c, k, dec(v) if k == "exposure_limit_usd" else (date.fromisoformat(v) if k.endswith("_date") else v))
        c.version += 1
        audit(s, "CLIENT_UPDATED", "client", c.client_id, before=before, after=p["changes"], actor=actor)
        return {"client_id": c.client_id, "version": c.version}
    if a.request_type == "BREAK_WRITE_OFF":
        b = s.get(m.ReconBreak, a.entity_id)
        b.status = "WRITTEN_OFF"
        e = s.get(m.ExceptionItem, f"EXC-RECON_BREAK-{b.break_id}")
        if e:
            e.status, e.resolution_note = "WRITTEN_OFF", p.get("note") or "written off with approval"
        return {"break_id": b.break_id, "status": b.status}
    raise WorkflowError(422, "E_UNKNOWN_REQUEST", a.request_type)


def retry_instruction(s: Session, instruction_id: str, on: date, actor) -> dict:
    ins = s.get(m.SettlementInstruction, instruction_id)
    if ins is None:
        raise WorkflowError(404, "E_NOT_FOUND", "Instruction not found")
    if ins.status not in ("FAILED", "PARTIAL", "PENDING"):
        raise WorkflowError(409, "E_INVALID_TRANSITION", f"Instruction is {ins.status}")
    e = s.get(m.ExceptionItem, f"EXC-SETTLEMENT_FAIL-{instruction_id}") or s.get(m.ExceptionItem, f"EXC-PARTIAL_SETTLEMENT-{instruction_id}")
    if e:
        e.retries += 1
    stats = processing.step_settlement(s, on, ins.sandbox_id)
    audit(s, "SETTLEMENT_RETRY", "instruction", instruction_id, after={"status": ins.status, "stats": stats}, actor=actor)
    return {"instruction_id": instruction_id, "status": ins.status, "settled_on": ins.settled_on}


_ = (Decimal, raise_exception)
