"""Trade blotter, trade detail, booking, amendment, cancellation and transitions."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from openpyxl import Workbook
from sqlalchemy import Text, cast, or_, select
from sqlalchemy.orm import Session

from cf360 import lifecycle
from cf360 import models as m
from cf360.api.deps import ApiError, Idempotency, current_user, require
from cf360.api.schemas import AmendIn, CancelIn, ReasonIn, TradeIn, TransitionIn
from cf360.api.util import page, row, rows, translate
from cf360.config import defect
from cf360.db import get_session
from cf360.services import booking, workflow
from cf360.services.booking import BookingError
from cf360.services.core import jsonable

router = APIRouter(prefix="/api/trades", tags=["trades"])

BLOTTER_COLUMNS = ["trade_id", "version", "status", "product_group", "product_type", "instrument_id", "client_id", "account_id",
                   "book", "trader_id", "counterparty_id", "side", "quantity", "price", "trade_ccy", "settle_ccy", "trade_date",
                   "settlement_date", "net_amount", "usd_equivalent", "match_status", "flags", "source_trade_ref", "market"]
SORTABLE = set(BLOTTER_COLUMNS) - {"flags"}


def _filtered(q: str | None, status: list[str] | None, product_group: str | None, product_type: str | None,
              currency: str | None, book: str | None, counterparty_id: str | None, client_id: str | None,
              trade_date_from: date | None, trade_date_to: date | None, settle_from: date | None, settle_to: date | None,
              flag: str | None, sandbox_id: str | None, sort: str):
    stmt = select(m.Trade)
    stmt = stmt.where(m.Trade.sandbox_id == sandbox_id) if sandbox_id else stmt.where(m.Trade.sandbox_id.is_(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(m.Trade.trade_id.ilike(like), m.Trade.source_trade_ref.ilike(like), m.Trade.instrument_id.ilike(like),
                              m.Trade.client_id.ilike(like), m.Trade.account_id.ilike(like), m.Trade.order_id.ilike(like)))
    if status:
        stmt = stmt.where(m.Trade.status.in_(status))
    if product_group:
        stmt = stmt.where(m.Trade.product_group == product_group)
    if product_type:
        stmt = stmt.where(m.Trade.product_type == product_type)
    if currency and not defect("D6"):  # injected defect D6: the currency filter is silently ignored
        stmt = stmt.where(m.Trade.settle_ccy == currency.upper())
    if book:
        stmt = stmt.where(m.Trade.book == book)
    if counterparty_id:
        stmt = stmt.where(m.Trade.counterparty_id == counterparty_id)
    if client_id:
        stmt = stmt.where(m.Trade.client_id == client_id)
    if trade_date_from:
        stmt = stmt.where(m.Trade.trade_date >= trade_date_from)
    if trade_date_to:
        stmt = stmt.where(m.Trade.trade_date <= trade_date_to)
    if settle_from:
        stmt = stmt.where(m.Trade.settlement_date >= settle_from)
    if settle_to:
        stmt = stmt.where(m.Trade.settlement_date <= settle_to)
    if flag:
        stmt = stmt.where(cast(m.Trade.flags, Text).ilike(f'%"{flag}"%'))
    desc = sort.startswith("-")
    col = sort.lstrip("-")
    if col not in SORTABLE:
        raise ApiError(400, "E_BAD_SORT", f"Cannot sort by {col}", {"sortable": sorted(SORTABLE)})
    c = getattr(m.Trade, col)
    return stmt.order_by(c.desc() if desc else c.asc(), m.Trade.trade_id.asc())


def filters(q: str | None = None, status: list[str] | None = Query(default=None), product_group: str | None = None,
            product_type: str | None = None, currency: str | None = None, book: str | None = None,
            counterparty_id: str | None = None, client_id: str | None = None, trade_date_from: date | None = None,
            trade_date_to: date | None = None, settle_from: date | None = None, settle_to: date | None = None,
            flag: str | None = None, sandbox_id: str | None = None, sort: str = "-trade_date"):
    return _filtered(q, status, product_group, product_type, currency, book, counterparty_id, client_id, trade_date_from,
                     trade_date_to, settle_from, settle_to, flag, sandbox_id, sort)


@router.get("", summary="Trade blotter with search, filters, sort and pagination")
def list_trades(stmt=Depends(filters), page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(50, ge=1, le=500),
                columns: str | None = None, s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    out = page(s, stmt, page_no, page_size)
    if columns:
        keep = [c for c in columns.split(",") if c in BLOTTER_COLUMNS] or BLOTTER_COLUMNS
        out["items"] = [{k: it[k] for k in keep} for it in out["items"]]
        out["columns"] = keep
    else:
        out["columns"] = BLOTTER_COLUMNS
    return out


@router.get("/export", summary="Export the filtered blotter as CSV or XLSX")
def export_trades(stmt=Depends(filters), format: str = Query("csv", pattern="^(csv|xlsx)$"), columns: str | None = None,
                  s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    keep = [c for c in (columns or "").split(",") if c in BLOTTER_COLUMNS] or BLOTTER_COLUMNS
    data = [row(t) for t in s.scalars(stmt.limit(20000))]
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(keep)
        for d in data:
            w.writerow([",".join(d[k]) if isinstance(d[k], list) else d[k] for k in keep])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=cf360_blotter.csv"})
    wb = Workbook()
    ws = wb.active
    ws.title = "Blotter"
    ws.append(keep)
    for d in data:
        ws.append([",".join(d[k]) if isinstance(d[k], list) else d[k] for k in keep])
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=cf360_blotter.xlsx"})


@router.get("/{trade_id}", summary="Trade detail with versions, allocations, instructions, events and audit")
def get_trade(trade_id: str, s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    t = s.get(m.Trade, trade_id)
    if t is None:
        raise ApiError(404, "E_NOT_FOUND", f"Trade {trade_id} not found")
    def q(model, *conds, order=None):
        stmt = select(model).where(*conds)
        return rows(s.scalars(stmt.order_by(order) if order is not None else stmt))
    audit = q(m.AuditEvent, m.AuditEvent.entity_id == trade_id, order=m.AuditEvent.audit_id)
    return {
        "trade": row(t),
        "allowed_transitions": sorted(lifecycle.allowed(t.product_type, t.status)),
        "lifecycle_path": lifecycle.PATHS[t.product_type],
        "not_applicable": lifecycle.NOT_APPLICABLE.get(t.product_type, {}),
        "versions": q(m.TradeVersion, m.TradeVersion.trade_id == trade_id, order=m.TradeVersion.version),
        "allocations": q(m.Allocation, m.Allocation.trade_id == trade_id, order=m.Allocation.allocation_id),
        "confirmations": q(m.Confirmation, m.Confirmation.trade_id == trade_id),
        "clearing": q(m.ClearingRecord, m.ClearingRecord.trade_id == trade_id),
        "instructions": q(m.SettlementInstruction, m.SettlementInstruction.trade_id == trade_id,
                          order=m.SettlementInstruction.instruction_id),
        "events": q(m.LifecycleEvent, m.LifecycleEvent.trade_id == trade_id, order=m.LifecycleEvent.event_date),
        "ledger": q(m.LedgerEntry, m.LedgerEntry.trade_id == trade_id, order=m.LedgerEntry.entry_id),
        "exceptions": q(m.ExceptionItem, m.ExceptionItem.trade_id == trade_id),
        "approvals": q(m.Approval, m.Approval.entity_id == trade_id, order=m.Approval.created_at),
        "order": row(s.get(m.Order, t.order_id)) if t.order_id else None,
        "audit": audit,
    }


@router.post("", status_code=201, summary="Book a trade (Idempotency-Key supported, R093)")
def book_trade(body: TradeIn, response: Response, idempotency_key: str | None = Header(default=None),
               s: Session = Depends(get_session), user: m.User = Depends(require("trade.book"))):
    payload = body.model_dump(mode="python")
    idem = Idempotency(s, idempotency_key, user, payload)
    if idem.replay:
        status, body_ = idem.replay
        return JSONResponse(body_, status_code=status, headers={"Idempotent-Replay": "true"})
    if payload.get("allocations"):
        payload["allocations"] = [dict(a) for a in payload["allocations"]]
    try:
        with s.begin_nested():
            t = booking.book(s, payload, (user.username, user.role), None, reject_log=False)
    except BookingError as e:
        s.add(m.BookingRejection(source_trade_ref=payload.get("source_trade_ref"), ingest_batch=payload.get("ingest_batch"),
                                 errors=e.issues, payload=jsonable(payload)))
        err = {"error": {"code": "E_VALIDATION", "message": "Trade failed validation", "details": {"issues": e.issues}}}
        idem.store(422, err)
        s.commit()
        return JSONResponse(err, status_code=422)
    out = {"trade": row(t), "warnings": t.flags}
    idem.store(201, out)
    s.commit()
    return out


def _book_sandbox(s: Session, sandbox_id: str, payload: dict, user: m.User):
    """Used by the test-support router: book into an isolated sandbox."""
    return booking.book(s, payload, (user.username, user.role), sandbox_id, reject_log=False)


@router.post("/validate", summary="Dry-run: validate and compute economics without booking")
def validate_trade(body: TradeIn, s: Session = Depends(get_session), user: m.User = Depends(require("trade.view"))):
    payload = body.model_dump(mode="python")
    if payload.get("allocations"):
        payload["allocations"] = [dict(a) for a in payload["allocations"]]
    payload["source_trade_ref"] = payload.get("source_trade_ref") or "DRYRUN"
    sp = s.begin_nested()
    try:
        t = booking.book(s, payload, (user.username, user.role), "DRYRUN", reject_log=False)
        out = {"valid": True, "trade": row(t),
               "allocations": rows(s.scalars(select(m.Allocation).where(m.Allocation.trade_id == t.trade_id))),
               "warnings": t.flags}
    except BookingError as e:
        out = {"valid": False, "issues": e.issues}
    finally:
        sp.rollback()
        s.rollback()
    return out


@router.patch("/{trade_id}", summary="Amend a trade; economic changes need checker approval (R090/R091/R098)")
def amend_trade(trade_id: str, body: AmendIn, if_match: str | None = Header(default=None),
                s: Session = Depends(get_session), user: m.User = Depends(require("trade.amend"))):
    if if_match is None:
        raise ApiError(428, "E_PRECONDITION_REQUIRED", "Send If-Match with the trade version you read (R098)")
    with translate(s):
        out = workflow.amend(s, trade_id, int(if_match.strip('"W/')), body.changes, body.reason, user)
        s.commit()
    return out


@router.post("/{trade_id}/cancel", status_code=202, summary="Request cancellation (and optional rebook); needs approval")
def cancel_trade(trade_id: str, body: CancelIn, if_match: str | None = Header(default=None), s: Session = Depends(get_session),
                 user: m.User = Depends(require("trade.cancel"))):
    with translate(s):
        out = workflow.cancel(s, trade_id, int(if_match.strip('"W/')) if if_match else None, body.reason, user, body.rebook)
        s.commit()
    return out


@router.post("/{trade_id}/transition", summary="Move a trade to another status (server-side state machine, R099)")
def transition_trade(trade_id: str, body: TransitionIn, s: Session = Depends(get_session),
                     user: m.User = Depends(require("trade.transition"))):
    with translate(s):
        t = workflow.lock_trade(s, trade_id, None)
        booking.transition(s, t, body.target, (user.username, user.role), body.reason)
        s.commit()
    return {"trade_id": trade_id, "status": t.status}


@router.post("/{trade_id}/force-match", status_code=202, summary="Request a force match of a confirmation break (approval)")
def force_match(trade_id: str, body: ReasonIn, s: Session = Depends(get_session), user: m.User = Depends(require("match.manage"))):
    with translate(s):
        t = workflow.lock_trade(s, trade_id, None)
        if t.status != "ALLOCATED":
            raise ApiError(409, "E_INVALID_TRANSITION", f"Trade is {t.status}; only ALLOCATED trades can be force-matched")
        a = workflow.request_approval(s, "FORCE_MATCH", "trade", trade_id, {"version": t.version}, user, body.reason, t.sandbox_id)
        s.commit()
    return {"trade_id": trade_id, "approval_id": a.approval_id}


_ = current_user
