"""Audit trail, ledger postings, exceptions and the business clock."""

from __future__ import annotations

import json
import uuid
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.config import defect

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
current_actor: ContextVar[tuple[str, str]] = ContextVar("current_actor", default=("system", "SYSTEM"))
processing_date: ContextVar[date | None] = ContextVar("processing_date", default=None)


def new_correlation() -> str:
    return "corr-" + uuid.uuid4().hex[:16]


def jsonable(v):
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def business_date(s: Session) -> date:
    c = s.get(m.SystemClock, 1)
    return c.business_date if c else date(2026, 11, 6)


def audit(s: Session, action: str, entity_type: str, entity_id: str, before=None, after=None, details=None,
          actor: tuple[str, str] | None = None) -> None:
    who, role = actor or current_actor.get()
    s.add(m.AuditEvent(business_date=processing_date.get() or business_date(s), actor=who, role=role, action=action, entity_type=entity_type,
                       entity_id=entity_id, correlation_id=correlation_id.get() or new_correlation(),
                       before=jsonable(before), after=jsonable(after), details=jsonable(details)))


def trade_snapshot(t: m.Trade) -> dict:
    return jsonable({c.name: getattr(t, c.name) for c in m.Trade.__table__.columns if c.name not in ("created_at", "updated_at")})


def post(s: Session, source_event_id: str, ledger: str, legs: list[tuple[str, str, Decimal]], value_date: date,
         entry_type: str, trade_id: str | None = None, sandbox_id: str | None = None, reversal_of: str | None = None) -> bool:
    """Post a balanced set of legs. Returns False if this event was already posted (idempotent replay)."""
    total: dict[str, Decimal] = {}
    for _, key, amt in legs:
        total[key] = total.get(key, Decimal(0)) + amt
    if any(v != 0 for v in total.values()):
        raise ValueError(f"Unbalanced posting {source_event_id}: {total}")
    rows = [{"posting_id": source_event_id, "source_event_id": source_event_id, "leg_seq": i, "ledger": ledger,
             "ledger_account": acct, "key": key, "amount": amt, "value_date": value_date, "entry_type": entry_type,
             "trade_id": trade_id, "sandbox_id": sandbox_id, "reversal_of": reversal_of}
            for i, (acct, key, amt) in enumerate(legs)]
    if defect("D5") and entry_type in ("DVP", "FX_SETTLEMENT", "PREMIUM", "REPO_START", "REPO_END", "SL_START", "SL_END", "SL_FEE"):
        # injected defect D5: a unique suffix bypasses the idempotency key
        for r in rows:
            r["source_event_id"] = r["source_event_id"] + "#" + uuid.uuid4().hex[:6]
    stmt = (pg_insert(m.LedgerEntry).values(rows).on_conflict_do_nothing(constraint="uq_ledger_event_leg")
            .returning(m.LedgerEntry.entry_id))
    return len(s.execute(stmt).all()) > 0


def raise_exception(s: Session, etype: str, severity: str, owner_role: str, message: str, trade_id: str | None = None,
                    ref_id: str | None = None, rule_id: str | None = None, error_code: str | None = None,
                    sandbox_id: str | None = None, on: date | None = None) -> m.ExceptionItem:
    """Open an exception unless an open one with the same type and reference exists."""
    eid = f"EXC-{etype}-{ref_id or trade_id}"
    existing = s.get(m.ExceptionItem, eid)
    if existing is not None:
        if existing.status in ("RESOLVED", "WRITTEN_OFF"):
            existing.status = "OPEN"
            existing.retries += 1
            existing.message = message
        return existing
    e = m.ExceptionItem(exception_id=eid, exception_type=etype, severity=severity, status="OPEN", owner_role=owner_role,
                        trade_id=trade_id, ref_id=ref_id, rule_id=rule_id, error_code=error_code, message=message,
                        opened_business_date=on or processing_date.get() or business_date(s), sandbox_id=sandbox_id)
    s.add(e)
    s.flush()
    audit(s, "EXCEPTION_OPENED", "exception", eid, after={"type": etype, "message": message})
    return e


def resolve_exception(s: Session, etype: str, ref: str, note: str, on: date | None = None) -> None:
    e = s.get(m.ExceptionItem, f"EXC-{etype}-{ref}")
    if e is not None and e.status not in ("RESOLVED", "WRITTEN_OFF"):
        e.status = "RESOLVED"
        e.resolution_note = note
        e.resolved_business_date = on or business_date(s)
        audit(s, "EXCEPTION_RESOLVED", "exception", e.exception_id, details={"note": note})


def dumps(v) -> str:
    return json.dumps(jsonable(v), sort_keys=True)


_ = select
