"""Operations: reference data, work queues, settlements, positions/cash, reconciliation, exceptions,
lifecycle events, approvals and simulated interface messages."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.api.deps import ApiError, as_of_date, current_user, require
from cf360.api.schemas import ClientChangeIn, DecisionIn, ExceptionActionIn, InterfaceMessageIn, SSIIn, WriteOffIn
from cf360.api.util import page, row, rows, translate
from cf360.db import get_session
from cf360.lifecycle import InvalidTransition
from cf360.rules.calc import CCY_CALENDAR, dec
from cf360.services import recon, refcache, workflow
from cf360.services.booking import BookingError
from cf360.services.core import audit, business_date, jsonable

router = APIRouter(prefix="/api", tags=["operations"])

REFDATA = {
    "clients": (m.Client, m.Client.client_id), "accounts": (m.Account, m.Account.account_id),
    "counterparties": (m.Counterparty, m.Counterparty.counterparty_id), "instruments": (m.Instrument, m.Instrument.instrument_id),
    "markets": (m.Market, m.Market.market), "calendars": (m.CalendarDef, m.CalendarDef.calendar_code),
    "holidays": (m.Holiday, m.Holiday.holiday_date), "conventions": (m.SettlementConvention, m.SettlementConvention.convention_id),
    "ssis": (m.SSI, m.SSI.ssi_id), "prices": (m.Price, m.Price.price_date), "fx-rates": (m.FxRate, m.FxRate.rate_date),
    "rate-fixings": (m.RateFixing, m.RateFixing.fixing_date), "config": (m.ConfigParam, m.ConfigParam.param),
    "commission-schedules": (m.CommissionSchedule, m.CommissionSchedule.schedule_id),
    "corporate-actions": (m.CorporateAction, m.CorporateAction.pay_date),
}
SEARCH_COL = {"clients": "legal_name", "accounts": "account_name", "counterparties": "legal_name", "instruments": "name",
              "ssis": "owner_id", "prices": "instrument_id", "fx-rates": "currency", "holidays": "calendar_code",
              "commission-schedules": "account_id", "corporate-actions": "instrument_id", "rate-fixings": "rate_index",
              "conventions": "product_types", "config": "param", "markets": "market", "calendars": "calendar_code"}


# ------------------------------------------------------------------------------------------------ reference data
@router.get("/refdata", tags=["refdata"], summary="List reference-data entities")
def refdata_entities(_=Depends(current_user)):
    return {"entities": sorted(REFDATA)}


@router.get("/refdata/{entity}", tags=["refdata"], summary="Browse a reference-data table (effective-dated where relevant)")
def refdata(entity: str, q: str | None = None, page_no: int = Query(1, alias="page", ge=1),
            page_size: int = Query(50, ge=1, le=500), s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    if entity not in REFDATA:
        raise ApiError(404, "E_NOT_FOUND", f"Unknown entity {entity}", {"entities": sorted(REFDATA)})
    model, order = REFDATA[entity]
    stmt = select(model)
    if q:
        pk = list(model.__table__.primary_key.columns)[0]
        stmt = stmt.where(getattr(model, SEARCH_COL[entity]).ilike(f"%{q}%") | pk.cast(String).ilike(f"%{q}%"))
    return page(s, stmt.order_by(order), page_no, page_size)


@router.post("/refdata/ssis", status_code=202, tags=["refdata"], summary="Request a new SSI (maker-checker); repairs SSI_MISSING trades")
def request_ssi(body: SSIIn, s: Session = Depends(get_session), user: m.User = Depends(require("refdata.request"))):
    d = body.model_dump(mode="json")
    reason = d.pop("reason")
    with translate(s):
        a = workflow.request_approval(s, "SSI_CREATE", "ssi", f"{d['owner_id']}:{d['currency']}:{d['settlement_location']}",
                                      {"ssi": d}, user, reason)
        s.commit()
    return {"approval_id": a.approval_id}


@router.post("/refdata/clients/{client_id}/changes", status_code=202, tags=["refdata"], summary="Request a client change (maker-checker)")
def request_client_change(client_id: str, body: ClientChangeIn, s: Session = Depends(get_session),
                          user: m.User = Depends(require("refdata.request"))):
    c = s.get(m.Client, client_id)
    if c is None:
        raise ApiError(404, "E_NOT_FOUND", "Client not found")
    allowed = {"status", "kyc_expiry_date", "kyc_review_date", "exposure_limit_usd", "risk_rating", "client_category"}
    bad = set(body.changes) - allowed
    if bad:
        raise ApiError(422, "E_FIELD_NOT_AMENDABLE", f"Client fields not changeable: {sorted(bad)}", {"allowed": sorted(allowed)})
    with translate(s):
        a = workflow.request_approval(s, "CLIENT_UPDATE", "client", client_id, {"changes": jsonable(body.changes)}, user, body.reason)
        s.commit()
    return {"approval_id": a.approval_id}


# ------------------------------------------------------------------------------------------------ queues
def _bday_age(ref, start: date | None, end: date, ccy: str) -> int | None:
    if start is None:
        return None
    return ref.cals.count_between(start, end, CCY_CALENDAR.get(ccy, "USNY"))


@router.get("/queues/{queue}", tags=["queues"], summary="Work queues: matching, clearing, settlement, failed, pending-approval")
def queue(queue: str, page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(50, ge=1, le=500),
          product_group: str | None = None, s: Session = Depends(get_session), _=Depends(require("trade.view")),
          as_of=Depends(as_of_date)):
    T, SI = m.Trade, m.SettlementInstruction
    if queue == "matching":
        stmt = select(T).where(T.sandbox_id.is_(None), T.status == "ALLOCATED")
    elif queue == "clearing":
        stmt = select(T).where(T.sandbox_id.is_(None), T.status == "CONFIRMED", T.product_type.in_(
            ["EQUITY", "ETF", "FUTURE", "OPTION", "IRS"]))
    elif queue == "enrichment":
        stmt = select(T).where(T.sandbox_id.is_(None), T.status.in_(["CAPTURED", "ENRICHED"]))
    elif queue in ("settlement", "failed"):
        st = ["PENDING", "PARTIAL", "FAILED"] if queue == "settlement" else ["FAILED", "PARTIAL"]
        stmt = select(SI).where(SI.sandbox_id.is_(None), SI.status.in_(st)).order_by(SI.settle_date, SI.instruction_id)
        out = page(s, stmt, page_no, page_size)
        ref = refcache.get(s)
        for it in out["items"]:
            ff = date.fromisoformat(it["first_fail_date"]) if it["first_fail_date"] else None
            it["age_bdays"] = _bday_age(ref, ff, as_of, it["currency"]) if ff else None
            it["due"] = date.fromisoformat(it["settle_date"]) <= as_of
        return out
    else:
        raise ApiError(404, "E_NOT_FOUND", f"Unknown queue {queue}")
    if product_group:
        stmt = stmt.where(T.product_group == product_group)
    return page(s, stmt.order_by(T.trade_date, T.trade_id), page_no, page_size)


@router.post("/settlements/{instruction_id}/retry", tags=["settlements"], summary="Retry a failed or partial instruction")
def retry_settlement(instruction_id: str, s: Session = Depends(get_session), user: m.User = Depends(require("settlement.manage")),
                     as_of=Depends(as_of_date)):
    with translate(s):
        out = workflow.retry_instruction(s, instruction_id, as_of, (user.username, user.role))
        s.commit()
    return jsonable(out)


@router.get("/settlements", tags=["settlements"], summary="Settlement instructions")
def settlements(status: str | None = None, trade_id: str | None = None, account_id: str | None = None,
                settle_from: date | None = None, settle_to: date | None = None, page_no: int = Query(1, alias="page", ge=1),
                page_size: int = Query(50, ge=1, le=500), s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    SI = m.SettlementInstruction
    stmt = select(SI).where(SI.sandbox_id.is_(None))
    for col, v in ((SI.status, status), (SI.trade_id, trade_id), (SI.account_id, account_id)):
        if v:
            stmt = stmt.where(col == v)
    if settle_from:
        stmt = stmt.where(SI.settle_date >= settle_from)
    if settle_to:
        stmt = stmt.where(SI.settle_date <= settle_to)
    return page(s, stmt.order_by(SI.settle_date.desc(), SI.instruction_id), page_no, page_size)


# ------------------------------------------------------------------------------------------------ positions and cash
def _balances(s: Session, ledger: str, as_of: date | None, account_id: str | None, key: str | None, projected: bool):
    L = m.LedgerEntry
    stmt = select(L.ledger_account, L.key, func.sum(L.amount)).where(L.ledger == ledger, L.sandbox_id.is_(None),
                                                                     ~L.ledger_account.contains(":"))
    if as_of and not projected:
        stmt = stmt.where(L.value_date <= as_of)
    if account_id:
        stmt = stmt.where(L.ledger_account == account_id)
    if key:
        stmt = stmt.where(L.key == key)
    return [{"account_id": a, "key": k, "amount": str(dec(v))} for a, k, v in
            s.execute(stmt.group_by(L.ledger_account, L.key).order_by(L.ledger_account, L.key)) if dec(v) != 0]


@router.get("/positions", tags=["positions"], summary="Settled securities positions by account (ledger), or projected")
def positions(account_id: str | None = None, instrument_id: str | None = None, projected: bool = False,
              s: Session = Depends(get_session), _=Depends(require("trade.view")), as_of=Depends(as_of_date)):
    data = _balances(s, "SEC", as_of, account_id, instrument_id, projected)
    names = {i.instrument_id: i.name for i in refcache.get(s).instruments.values()}
    for d in data:
        d["instrument_id"] = d.pop("key")
        d["quantity"] = d.pop("amount")
        d["name"] = names.get(d["instrument_id"])
    return {"as_of": as_of, "basis": "projected (all value dates)" if projected else "settled (value date <= as_of)", "items": data}


@router.get("/cash", tags=["positions"], summary="Cash balances by account and currency")
def cash(account_id: str | None = None, currency: str | None = None, projected: bool = False,
         s: Session = Depends(get_session), _=Depends(require("trade.view")), as_of=Depends(as_of_date)):
    data = _balances(s, "CASH", as_of, account_id, currency, projected)
    for d in data:
        d["currency"] = d.pop("key")
    return {"as_of": as_of, "basis": "projected (all value dates)" if projected else "settled (value date <= as_of)", "items": data}


@router.get("/cash/ladder", tags=["positions"], summary="Projected cash ladder by value date (settled + pending postings)")
def cash_ladder(currency: str = "USD", account_id: str | None = None, s: Session = Depends(get_session),
                _=Depends(require("trade.view")), as_of=Depends(as_of_date)):
    L, SI = m.LedgerEntry, m.SettlementInstruction
    stmt = select(L.value_date, func.sum(L.amount)).where(L.ledger == "CASH", L.key == currency, L.sandbox_id.is_(None),
                                                          ~L.ledger_account.contains(":"))
    if account_id:
        stmt = stmt.where(L.ledger_account == account_id)
    posted = {d: dec(v) for d, v in s.execute(stmt.group_by(L.value_date))}
    pq = select(SI.settle_date, func.sum(SI.cash_amount - SI.settled_cash)).where(
        SI.currency == currency, SI.status.in_(["PENDING", "PARTIAL", "FAILED"]), SI.sandbox_id.is_(None))
    if account_id:
        pq = pq.where(SI.account_id == account_id)
    pending = {d: dec(v) for d, v in s.execute(pq.group_by(SI.settle_date))}
    opening = sum((v for d, v in posted.items() if d <= as_of), dec(0))
    ladder, running = [], opening
    for d in sorted({*[d for d in posted if d > as_of], *pending}):
        flow = posted.get(d, dec(0)) + pending.get(d, dec(0))
        running += flow
        ladder.append({"date": d, "posted": posted.get(d, dec(0)), "pending": pending.get(d, dec(0)), "balance": running})
    return jsonable({"currency": currency, "as_of": as_of, "opening": opening, "ladder": ladder})


# ------------------------------------------------------------------------------------------------ reconciliation
@router.get("/recon/runs", tags=["recon"])
def recon_runs(s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    return {"items": rows(s.scalars(select(m.ReconRun).order_by(m.ReconRun.statement_date.desc(), m.ReconRun.recon_type)))}


@router.get("/recon/breaks", tags=["recon"])
def recon_breaks(status: str | None = None, recon_type: str | None = None, s: Session = Depends(get_session),
                 _=Depends(require("trade.view"))):
    stmt = select(m.ReconBreak)
    if status:
        stmt = stmt.where(m.ReconBreak.status == status)
    if recon_type:
        stmt = stmt.where(m.ReconBreak.recon_type == recon_type)
    return {"items": rows(s.scalars(stmt.order_by(m.ReconBreak.break_id)))}


@router.post("/recon/run", tags=["recon"], summary="Run position and cash reconciliation against custodian statements (R096)")
def run_recon(statement_date: date | None = None, s: Session = Depends(get_session), user: m.User = Depends(require("recon.manage"))):
    d = statement_date or s.scalar(select(func.max(m.ExternalStatement.statement_date)))
    if d is None:
        raise ApiError(409, "E_NO_STATEMENT", "No custodian statement loaded")
    out = recon.run(s, d)
    s.commit()
    return {"statement_date": d, "summary": out}


@router.post("/recon/breaks/{break_id}/write-off", status_code=202, tags=["recon"], summary="Request a break write-off (approval)")
def write_off(break_id: str, body: WriteOffIn, s: Session = Depends(get_session), user: m.User = Depends(require("recon.manage"))):
    b = s.get(m.ReconBreak, break_id)
    if b is None:
        raise ApiError(404, "E_NOT_FOUND", "Break not found")
    with translate(s):
        a = workflow.request_approval(s, "BREAK_WRITE_OFF", "recon_break", break_id, {"note": body.note}, user, body.note)
        s.commit()
    return {"approval_id": a.approval_id}


# ------------------------------------------------------------------------------------------------ exceptions
@router.get("/exceptions", tags=["exceptions"], summary="Exception queue with ownership and ageing")
def exceptions(status: str | None = None, exception_type: str | None = None, owner_role: str | None = None,
               severity: str | None = None, assigned_to: str | None = None, trade_id: str | None = None,
               page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(50, ge=1, le=500),
               s: Session = Depends(get_session), _=Depends(require("trade.view")), as_of=Depends(as_of_date)):
    E = m.ExceptionItem
    stmt = select(E).where(E.sandbox_id.is_(None))
    for col, v in ((E.status, status), (E.exception_type, exception_type), (E.owner_role, owner_role), (E.severity, severity),
                   (E.assigned_to, assigned_to), (E.trade_id, trade_id)):
        if v:
            stmt = stmt.where(col == v)
    out = page(s, stmt.order_by(E.opened_business_date, E.exception_id), page_no, page_size)
    ref = refcache.get(s)
    for it in out["items"]:
        end = date.fromisoformat(it["resolved_business_date"]) if it["resolved_business_date"] else as_of
        it["age_bdays"] = ref.cals.count_between(date.fromisoformat(it["opened_business_date"]), end, "USNY")
        it["age_bucket"] = "0-1" if it["age_bdays"] <= 1 else "2-5" if it["age_bdays"] <= 5 else "6-10" if it["age_bdays"] <= 10 else ">10"
    counts = dict(s.execute(select(E.status, func.count()).where(E.sandbox_id.is_(None)).group_by(E.status)).all())
    out["status_counts"] = counts
    return out


@router.post("/exceptions/{exception_id}/actions", tags=["exceptions"], summary="Assign, start, retry, resolve, escalate or comment")
def exception_action(exception_id: str, body: ExceptionActionIn, s: Session = Depends(get_session),
                     user: m.User = Depends(require("exception.manage")), as_of=Depends(as_of_date)):
    e = s.scalars(select(m.ExceptionItem).where(m.ExceptionItem.exception_id == exception_id).with_for_update()).first()
    if e is None:
        raise ApiError(404, "E_NOT_FOUND", "Exception not found")
    if e.status in ("RESOLVED", "WRITTEN_OFF") and body.action != "COMMENT":
        raise ApiError(409, "E_EXCEPTION_CLOSED", f"Exception is {e.status}")
    if user.role not in (e.owner_role, "RISK") and body.action in ("RESOLVE", "RETRY", "START"):
        raise ApiError(403, "E_NOT_OWNER", f"Owned by {e.owner_role}; {user.role} can only assign, escalate or comment")
    before = {"status": e.status, "assigned_to": e.assigned_to}
    result = None
    with translate(s):
        if body.action == "ASSIGN":
            if not body.assignee or s.get(m.User, body.assignee) is None:
                raise ApiError(422, "E_ASSIGNEE", "Assignee must be an existing user")
            e.assigned_to = body.assignee
        elif body.action == "START":
            e.status, e.assigned_to = "IN_PROGRESS", e.assigned_to or user.username
        elif body.action == "ESCALATE":
            e.severity, e.owner_role = "HIGH", "RISK"
        elif body.action == "RESOLVE":
            if not body.note:
                raise ApiError(422, "E_NOTE_REQUIRED", "A resolution note is required")
            e.status, e.resolution_note, e.resolved_business_date = "RESOLVED", body.note, as_of
        elif body.action == "RETRY":
            e.retries += 1
            if e.exception_type in ("SETTLEMENT_FAIL", "PARTIAL_SETTLEMENT") and e.ref_id:
                result = workflow.retry_instruction(s, e.ref_id, as_of, (user.username, user.role))
            elif e.exception_type == "SSI_MISSING" and e.trade_id:
                from cf360.services import booking
                t = s.get(m.Trade, e.trade_id)
                result = {"repaired": bool(t and t.status == "CAPTURED" and booking.enrich(s, t, (user.username, user.role)))}
            else:
                raise ApiError(409, "E_NOT_RETRYABLE", f"{e.exception_type} cannot be retried automatically")
        audit(s, f"EXCEPTION_{body.action}", "exception", exception_id, before=before,
              after={"status": e.status, "assigned_to": e.assigned_to}, details={"note": body.note, "result": result})
        s.commit()
    return {"exception": row(e), "result": jsonable(result)}


# ------------------------------------------------------------------------------------------------ lifecycle events
@router.get("/events", tags=["events"], summary="Lifecycle events: coupons, dividends, VM, expiries, resets, repo/loan legs")
def events(event_type: str | None = None, trade_id: str | None = None, date_from: date | None = None, date_to: date | None = None,
           page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(50, ge=1, le=500),
           s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    E = m.LifecycleEvent
    stmt = select(E).where(E.sandbox_id.is_(None))
    if event_type:
        stmt = stmt.where(E.event_type == event_type)
    if trade_id:
        stmt = stmt.where(E.trade_id == trade_id)
    if date_from:
        stmt = stmt.where(E.event_date >= date_from)
    if date_to:
        stmt = stmt.where(E.event_date <= date_to)
    out = page(s, stmt.order_by(E.event_date.desc(), E.event_id), page_no, page_size)
    out["type_counts"] = dict(s.execute(select(E.event_type, func.count()).where(E.sandbox_id.is_(None)).group_by(E.event_type)).all())
    return out


# ------------------------------------------------------------------------------------------------ approvals
@router.get("/approvals", tags=["approvals"])
def approvals(status: str | None = "PENDING", s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    stmt = select(m.Approval)
    if status:
        stmt = stmt.where(m.Approval.status == status)
    return {"items": rows(s.scalars(stmt.order_by(m.Approval.created_at.desc())))}


@router.post("/approvals/{approval_id}/decision", tags=["approvals"], summary="Approve or reject (checker; never the maker, R092)")
def decide(approval_id: str, body: DecisionIn, s: Session = Depends(get_session), user: m.User = Depends(current_user)):
    try:
        out = workflow.decide(s, approval_id, body.approve, body.note, user)
        s.commit()
    except BookingError as e:
        s.rollback()
        raise ApiError(422, "E_VALIDATION", "Approved change failed validation", {"issues": e.issues}) from e
    except InvalidTransition as e:
        s.rollback()
        raise ApiError(409, "E_INVALID_TRANSITION", str(e), {"rule_id": "R099"}) from e
    except workflow.WorkflowError as e:
        s.rollback()
        if e.code in ("E_SELF_APPROVAL", "E_FORBIDDEN"):
            audit(s, "APPROVAL_BLOCKED_SELF" if e.code == "E_SELF_APPROVAL" else "APPROVAL_BLOCKED_ROLE", "approval", approval_id,
                  details={"attempted_by": user.username, "rule_id": "R092"}, actor=(user.username, user.role))
            s.commit()
        raise ApiError(e.status, e.code, e.message) from e
    except Exception:
        s.rollback()
        raise
    refcache.invalidate()
    return out


# ------------------------------------------------------------------------------------------------ interfaces (R094)
@router.get("/interfaces/messages", tags=["interfaces"])
def interface_messages(system: str | None = None, status: str | None = None, page_no: int = Query(1, alias="page", ge=1),
                       page_size: int = Query(50, ge=1, le=500), s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    IM = m.InterfaceMessage
    stmt = select(IM)
    if system:
        stmt = stmt.where(IM.system == system)
    if status:
        stmt = stmt.where(IM.status == status)
    out = page(s, stmt.order_by(IM.created_at.desc(), IM.message_id), page_no, page_size)
    out["summary"] = [{"system": a, "status": b, "count": c} for a, b, c in
                      s.execute(select(IM.system, IM.status, func.count()).group_by(IM.system, IM.status))]
    return out


def _apply_message(s: Session, msg: m.InterfaceMessage) -> None:
    if msg.message_type == "CONFIRMATION":
        p = msg.payload
        t = s.get(m.Trade, msg.ref_id) if msg.ref_id else None
        if t is None:
            raise ApiError(422, "E_UNKNOWN_TRADE", f"Confirmation references unknown trade {msg.ref_id}")
        s.add(m.Confirmation(confirmation_id="CNF-" + msg.message_id[-20:], trade_id=t.trade_id, counterparty_id=t.counterparty_id,
                             cp_trade_ref=p.get("cp_trade_ref", msg.message_id), quantity=dec(p.get("quantity", t.quantity)),
                             price=dec(p.get("price", t.price)),
                             settle_amount=dec(p["settle_amount"]) if p.get("settle_amount") is not None else None,
                             settle_ccy=p.get("settle_ccy", t.settle_ccy),
                             settle_date=date.fromisoformat(p["settle_date"]) if p.get("settle_date") else t.settlement_date,
                             received_ts_utc=msg.created_at, sandbox_id=t.sandbox_id))
    msg.status = "PROCESSED"


@router.post("/interfaces/{system}/messages", tags=["interfaces"],
             summary="Receive a simulated inbound message; duplicates ignored, gaps parked until filled (R094)")
def receive_message(system: str, body: InterfaceMessageIn, s: Session = Depends(get_session),
                    user: m.User = Depends(require("trade.transition"))):
    system = system.upper()
    if system not in ("BROKER", "CCP", "CUSTODIAN", "PAYMENT", "EXCHANGE"):
        raise ApiError(404, "E_UNKNOWN_SYSTEM", f"Unknown interface {system}")
    IM = m.InterfaceMessage
    existing = s.get(IM, body.message_id)
    if existing is not None:
        audit(s, "MESSAGE_DUPLICATE_IGNORED", "message", body.message_id, details={"system": system})
        s.commit()
        return {"message_id": body.message_id, "status": "IGNORED_DUPLICATE", "original_status": existing.status}
    last = s.scalar(select(func.max(IM.sequence_no)).where(IM.system == system, IM.direction == "IN", IM.status == "PROCESSED")) or 0
    msg = IM(message_id=body.message_id, system=system, direction="IN", message_type=body.message_type, sequence_no=body.sequence_no,
            correlation_id=body.ref_id, ref_id=body.ref_id, payload=jsonable(body.payload), status="PENDING",
            business_date=body.business_date or business_date(s))
    s.add(msg)
    s.flush()
    processed = []
    with translate(s):
        if body.sequence_no <= last:
            msg.status = "IGNORED_DUPLICATE"
        elif body.sequence_no > last + 1:
            msg.status = "PARKED"
        else:
            _apply_message(s, msg)
            processed.append(msg.message_id)
            nxt = body.sequence_no + 1
            while True:  # release parked messages now in sequence
                parked = s.scalars(select(IM).where(IM.system == system, IM.status == "PARKED", IM.sequence_no == nxt)).first()
                if parked is None:
                    break
                _apply_message(s, parked)
                processed.append(parked.message_id)
                nxt += 1
        audit(s, "MESSAGE_RECEIVED", "message", body.message_id, after={"status": msg.status, "sequence_no": body.sequence_no,
                                                                         "expected": last + 1, "released": processed})
        s.commit()
    nxt = (s.scalar(select(func.max(IM.sequence_no)).where(IM.system == system, IM.direction == "IN", IM.status == "PROCESSED")) or 0) + 1
    return {"message_id": body.message_id, "status": msg.status, "expected_sequence": last + 1, "next_expected": nxt,
            "processed": processed}


_ = uuid
