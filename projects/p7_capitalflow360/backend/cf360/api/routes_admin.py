"""Auth, metadata, admin (clock, processing, replay), dashboards, reports, audit, test runs and
test-support sandboxes."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cf360 import lifecycle
from cf360 import models as m
from cf360.api.deps import PERMISSIONS, ROLES, ApiError, as_of_date, current_user, make_token, require
from cf360.api.schemas import ClockIn, JobRunsIn, LoginIn, ProcessIn, SandboxIn, TestRunIn, TradeIn
from cf360.api.util import page, row, rows, translate
from cf360.clock import utcnow
from cf360.config import KNOWN_DEFECTS, settings
from cf360.db import get_session
from cf360.rules.calc import dec
from cf360.rules.catalogue import BY_ID, RULES
from cf360.services import booking, dashboards, processing, recon, refcache
from cf360.services.core import audit, jsonable, post
from cf360.services.loader import ROLES as DEMO_USERS
from cf360.services.loader import verify_password

router = APIRouter(prefix="/api", tags=["platform"])


# ------------------------------------------------------------------------------------------------ auth
@router.post("/auth/login", tags=["auth"], summary="Log in as a demo user; returns a bearer token")
def login(body: LoginIn, s: Session = Depends(get_session)):
    u = s.get(m.User, body.username)
    if u is None or not u.active or not verify_password(body.password, u.password_hash):
        audit(s, "LOGIN_FAILED", "user", body.username, actor=(body.username, "UNKNOWN"))
        s.commit()
        raise ApiError(401, "E_BAD_CREDENTIALS", "Unknown user or wrong password")
    audit(s, "LOGIN", "user", u.username, actor=(u.username, u.role))
    s.commit()
    return {"access_token": make_token(u), "token_type": "bearer",
            "user": {"username": u.username, "display_name": u.display_name, "role": u.role}}


@router.get("/auth/me", tags=["auth"])
def me(user: m.User = Depends(current_user)):
    return {"username": user.username, "display_name": user.display_name, "role": user.role,
            "permissions": sorted(p for p, r in PERMISSIONS.items() if user.role in r)}


@router.get("/auth/demo-users", tags=["auth"], summary="Demo users for the login screen (no secrets)")
def demo_users():
    return {"users": [{"username": u, "display_name": n, "role": r} for u, n, r in DEMO_USERS],
            "note": "Synthetic demo users. The shared demo password is set by CF360_DEMO_PASSWORD (see .env.example)."}


# ------------------------------------------------------------------------------------------------ metadata
@router.get("/health", tags=["meta"])
def health(s: Session = Depends(get_session)):
    c = s.get(m.SystemClock, 1)
    return {"status": "ok", "business_date": c.business_date if c else None, "test_support": settings.test_support,
            "defects_enabled": sorted(settings.defects)}


@router.get("/meta/rules", tags=["meta"], summary="Business rule catalogue")
def rules(family: str | None = None):
    return {"items": [r for r in RULES if not family or r["family"] == family], "count": len(RULES)}


@router.get("/meta/rules/{rule_id}", tags=["meta"])
def rule(rule_id: str):
    if rule_id not in BY_ID:
        raise ApiError(404, "E_NOT_FOUND", "Unknown rule")
    return BY_ID[rule_id]


@router.get("/meta/lifecycle", tags=["meta"], summary="Lifecycle state machines and not-applicable stages per product")
def lifecycle_meta():
    return {"paths": lifecycle.PATHS, "not_applicable": lifecycle.NOT_APPLICABLE, "matrix": lifecycle.matrix(),
            "graphs": {p: {k: sorted(v) for k, v in lifecycle.graph(p).items()} for p in lifecycle.PATHS}}


@router.get("/meta/permissions", tags=["meta"])
def permissions():
    return {"roles": ROLES, "permissions": {k: sorted(v) for k, v in PERMISSIONS.items()}}


# ------------------------------------------------------------------------------------------------ admin
@router.get("/admin/clock", tags=["admin"])
def get_clock(s: Session = Depends(get_session), _=Depends(current_user)):
    return row(s.get(m.SystemClock, 1))


@router.put("/admin/clock", tags=["admin"], summary="Set the business date (no processing)")
def set_clock(body: ClockIn, s: Session = Depends(get_session), _=Depends(require("admin"))):
    c = s.get(m.SystemClock, 1)
    before = c.business_date
    c.business_date = body.business_date
    audit(s, "CLOCK_SET", "system_clock", "1", before={"business_date": before}, after={"business_date": body.business_date})
    s.commit()
    return row(c)


@router.post("/admin/process", tags=["admin"], summary="Run end-of-day processing up to a business date (restartable)")
def run_processing(body: ProcessIn, s: Session = Depends(get_session), _=Depends(require("process.run"))):
    c = s.get(m.SystemClock, 1)
    target = body.business_date or c.business_date
    days = processing.run_through(s, target)
    c.business_date = max(c.business_date, target)
    audit(s, "EOD_PROCESSED", "system_clock", "1", after={"through": target, "days": len(days)})
    s.commit()
    return {"through": target, "days": days, "last_processed": c.last_processed}


@router.post("/admin/replay", tags=["admin"], summary="Re-run one processed day to prove idempotency (R093/R097)")
def replay(body: ProcessIn, s: Session = Depends(get_session), _=Depends(require("process.run"))):
    d = body.business_date or s.get(m.SystemClock, 1).last_processed
    L = m.LedgerEntry

    def snapshot():
        n = s.scalar(select(func.count()).select_from(L).where(L.sandbox_id.is_(None)))
        cash = s.scalar(select(func.coalesce(func.sum(func.abs(L.amount)), 0)).where(L.ledger == "CASH", L.sandbox_id.is_(None)))
        return n, dec(cash)
    before = snapshot()
    stats = processing.process_day(s, d)
    s.flush()
    after = snapshot()
    audit(s, "EOD_REPLAYED", "processing_run", f"EOD-MAIN-{d}",
          details={"ledger_rows_before": before[0], "ledger_rows_after": after[0]})
    s.commit()
    return jsonable({"business_date": d, "stats": stats, "ledger_rows_before": before[0], "ledger_rows_after": after[0],
                     "new_postings": after[0] - before[0], "idempotent": after == before})


@router.get("/admin/defects", tags=["admin"], summary="Injected-defect switches (read-only; set via CF360_DEFECTS at start-up)")
def defects(_=Depends(current_user)):
    return {"known": KNOWN_DEFECTS, "enabled": sorted(settings.defects),
            "note": "Defects are off by default and can only be enabled with the CF360_DEFECTS environment variable."}


@router.get("/admin/users", tags=["admin"])
def users(s: Session = Depends(get_session), _=Depends(require("admin"))):
    return {"items": rows(s.scalars(select(m.User).order_by(m.User.username)), exclude=("password_hash",))}


@router.get("/admin/processing-runs", tags=["admin"])
def processing_runs(s: Session = Depends(get_session), _=Depends(current_user)):
    return {"items": rows(s.scalars(select(m.ProcessingRun).where(m.ProcessingRun.sandbox_id.is_(None))
                                    .order_by(m.ProcessingRun.business_date.desc())))}


@router.get("/admin/rejections", tags=["admin"], summary="Booking rejections with rule ids and error codes")
def rejections(page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(50, ge=1, le=500),
               s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    return page(s, select(m.BookingRejection).where(m.BookingRejection.sandbox_id.is_(None)).order_by(m.BookingRejection.id.desc()),
                page_no, page_size)


@router.post("/admin/job-runs/import", tags=["admin"], summary="Import lakehouse job runs and data-quality results")
def import_job_runs(body: JobRunsIn, s: Session = Depends(get_session), _=Depends(require("testrun.import"))):
    for j in body.job_runs:
        s.merge(m.JobRun(**{k: (datetime.fromisoformat(v) if k in ("started_at", "finished_at") and isinstance(v, str) else v)
                            for k, v in j.items()}))
    envs = {d.get("environment") for d in body.dq_results}
    batches = {d.get("batch_id") for d in body.dq_results}
    if body.dq_results:
        s.execute(delete(m.DQResult).where(m.DQResult.environment.in_(envs), m.DQResult.batch_id.in_(batches)))
    for d in body.dq_results:
        s.add(m.DQResult(**d))
    s.commit()
    return {"job_runs": len(body.job_runs), "dq_results": len(body.dq_results)}


# ------------------------------------------------------------------------------------------------ audit
@router.get("/audit", tags=["audit"], summary="Append-only audit trail with correlation ids")
def audit_events(entity_id: str | None = None, actor: str | None = None, action: str | None = None,
                 correlation_id: str | None = None, page_no: int = Query(1, alias="page", ge=1),
                 page_size: int = Query(50, ge=1, le=500), s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    A = m.AuditEvent
    stmt = select(A)
    for col, v in ((A.entity_id, entity_id), (A.actor, actor), (A.action, action), (A.correlation_id, correlation_id)):
        if v:
            stmt = stmt.where(col == v)
    return page(s, stmt.order_by(A.audit_id.desc()), page_no, page_size)


# ------------------------------------------------------------------------------------------------ dashboards and reports
def dash_filters(as_of=Depends(as_of_date), product_group: str | None = None, book: str | None = None, currency: str | None = None,
                 counterparty_id: str | None = None, status: str | None = None, trade_date_from: date | None = None,
                 as_of_override: date | None = Query(default=None, alias="as_of")) -> dict:
    return {"as_of": as_of_override or as_of, "product_group": product_group, "book": book, "currency": currency,
            "counterparty_id": counterparty_id, "status": status, "trade_date_from": trade_date_from}


@router.get("/dashboards", tags=["dashboards"])
def dashboard_list(_=Depends(current_user)):
    return {"dashboards": list(dashboards.VIEWS)}


@router.get("/dashboards/{name}", tags=["dashboards"], summary="Dashboard KPIs with definitions, populations and drill targets")
def dashboard(name: str, f: dict = Depends(dash_filters), s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    if name not in dashboards.VIEWS:
        raise ApiError(404, "E_NOT_FOUND", f"Unknown dashboard {name}", {"dashboards": list(dashboards.VIEWS)})
    return jsonable(dashboards.VIEWS[name](s, f))


@router.get("/reports/snapshot", tags=["reports"], summary="One metrics snapshot used by every report deck")
def snapshot(f: dict = Depends(dash_filters), s: Session = Depends(get_session), _=Depends(require("trade.view"))):
    out = {"generated_at": utcnow().isoformat(timespec="seconds") + "Z", "filters": jsonable(f),
           "dashboards": {n: jsonable(v(s, f)) for n, v in dashboards.VIEWS.items()}}
    return out


# ------------------------------------------------------------------------------------------------ test runs
@router.post("/test-runs/import", tags=["test-runs"], summary="Import executed test results (Playwright JSON converted)")
def import_test_run(body: TestRunIn, s: Session = Depends(get_session), _=Depends(require("testrun.import"))):
    if body.replace:
        s.execute(delete(m.TestResult).where(m.TestResult.run_id == body.run_id))
    s.merge(m.TestRun(run_id=body.run_id, label=body.label, environment=body.environment, started_at=body.started_at,
                      defects_enabled=body.defects_enabled, summary=jsonable(body.summary)))
    fields = {c.name for c in m.TestResult.__table__.columns} - {"id"}
    s.add_all([m.TestResult(run_id=body.run_id, **{k: v for k, v in r.items() if k in fields and k != "run_id"})
               for r in body.results])
    s.commit()
    return {"run_id": body.run_id, "results": len(body.results)}


@router.get("/test-runs", tags=["test-runs"])
def test_runs(s: Session = Depends(get_session), _=Depends(current_user)):
    return {"items": rows(s.scalars(select(m.TestRun).order_by(m.TestRun.imported_at.desc())))}


@router.get("/test-runs/{run_id}/results", tags=["test-runs"])
def test_results(run_id: str, status: str | None = None, project: str | None = None, q: str | None = None,
                 page_no: int = Query(1, alias="page", ge=1), page_size: int = Query(100, ge=1, le=500),
                 s: Session = Depends(get_session), _=Depends(current_user)):
    R = m.TestResult
    stmt = select(R).where(R.run_id == run_id)
    if status:
        stmt = stmt.where(R.status == status)
    if project:
        stmt = stmt.where(R.project == project)
    if q:
        stmt = stmt.where(R.title.ilike(f"%{q}%") | R.case_id.ilike(f"%{q}%") | R.scenario_id.ilike(f"%{q}%"))
    return page(s, stmt.order_by(R.id), page_no, page_size)


# ------------------------------------------------------------------------------------------------ test support (sandboxes)
def _test_support():
    if not settings.test_support:
        raise ApiError(404, "E_NOT_FOUND", "Test support is disabled (CF360_TEST_SUPPORT=false)")


SANDBOX_MODELS = [m.LedgerEntry, m.SettlementInstruction, m.LifecycleEvent, m.ExceptionItem, m.Approval, m.Confirmation,
                  m.InterfaceMessage, m.BookingRejection, m.ProcessingRun, m.Execution, m.Order, m.SSI]


@router.post("/test-support/sandboxes", tags=["test-support"], dependencies=[Depends(_test_support)],
             summary="Create an isolated sandbox for one test case")
def create_sandbox(body: SandboxIn, _=Depends(current_user)):
    return {"sandbox_id": body.sandbox_id or "SBX-" + uuid.uuid4().hex[:12]}


@router.post("/test-support/sandboxes/{sandbox_id}/trades", status_code=201, tags=["test-support"],
             dependencies=[Depends(_test_support)], summary="Book a trade inside a sandbox (all rules apply)")
def sandbox_book(sandbox_id: str, body: TradeIn, s: Session = Depends(get_session), user: m.User = Depends(require("trade.book"))):
    payload = body.model_dump(mode="python")
    if payload.get("allocations"):
        payload["allocations"] = [dict(a) for a in payload["allocations"]]
    payload["source_trade_ref"] = f"{sandbox_id}:{payload.get('source_trade_ref') or uuid.uuid4().hex[:8]}"
    with translate(s):
        t = booking.book(s, payload, (user.username, user.role), sandbox_id, reject_log=False)
        s.commit()
    return {"trade": row(t), "allocations": rows(s.scalars(select(m.Allocation).where(m.Allocation.trade_id == t.trade_id)))}


@router.post("/test-support/sandboxes/{sandbox_id}/ledger", tags=["test-support"], dependencies=[Depends(_test_support)],
             summary="Seed an opening cash or securities balance inside a sandbox")
def sandbox_ledger(sandbox_id: str, body: dict, s: Session = Depends(get_session), _=Depends(require("process.run"))):
    led = body.get("ledger", "CASH")
    amt = dec(body["amount"])
    eid = f"SBXOPEN:{sandbox_id}:{body['account_id']}:{body['key']}:{uuid.uuid4().hex[:6]}"
    post(s, eid, led, [(body["account_id"], body["key"], amt), (f"OPENING:{led}", body["key"], -amt)],
         date.fromisoformat(body.get("value_date", "2026-09-11")), "OPEN", None, sandbox_id)
    s.commit()
    return {"posting_id": eid}


@router.post("/test-support/sandboxes/{sandbox_id}/confirmations", tags=["test-support"], dependencies=[Depends(_test_support)],
             summary="Simulate a counterparty confirmation for a sandbox trade (defaults to our economics; override fields to break it)")
def sandbox_confirm(sandbox_id: str, body: dict, s: Session = Depends(get_session), _=Depends(require("match.manage"))):
    t = s.get(m.Trade, body["trade_id"])
    if t is None or t.sandbox_id != sandbox_id:
        raise ApiError(404, "E_NOT_FOUND", "Trade not in this sandbox")
    o = body.get("overrides") or {}
    amt = o.get("settle_amount", (t.economics or {}).get("settle_amount"))
    received = datetime.fromisoformat(o["received_ts_utc"]) if o.get("received_ts_utc") else datetime.combine(t.trade_date, datetime.min.time()).replace(hour=20)
    c = m.Confirmation(confirmation_id=f"CNF-{t.trade_id}-{uuid.uuid4().hex[:6]}", trade_id=t.trade_id, counterparty_id=t.counterparty_id,
                       cp_trade_ref=f"CP-{t.trade_id}", quantity=dec(o.get("quantity", t.quantity)), price=dec(o.get("price", t.price)),
                       settle_amount=dec(amt) if amt is not None else None, settle_ccy=o.get("settle_ccy", t.settle_ccy),
                       settle_date=date.fromisoformat(o["settle_date"]) if o.get("settle_date") else t.settlement_date,
                       received_ts_utc=received, sandbox_id=sandbox_id)
    s.add(c)
    s.commit()
    return row(c)


@router.post("/test-support/sandboxes/{sandbox_id}/process", tags=["test-support"], dependencies=[Depends(_test_support)],
             summary="Run end-of-day processing for the sandbox only")
def sandbox_process(sandbox_id: str, through: date, start: date | None = None, s: Session = Depends(get_session),
                    _=Depends(require("process.run"))):
    days = processing.run_through(s, through, start, sandbox_id)
    s.commit()
    return {"days": len(days), "stats": days[-1] if days else None}


@router.get("/test-support/sandboxes/{sandbox_id}", tags=["test-support"], dependencies=[Depends(_test_support)],
            summary="Everything the sandbox produced: trades, allocations, instructions, ledger balances, events, exceptions")
def sandbox_state(sandbox_id: str, as_of: date | None = None, s: Session = Depends(get_session), _=Depends(current_user)):
    trades = list(s.scalars(select(m.Trade).where(m.Trade.sandbox_id == sandbox_id).order_by(m.Trade.trade_id)))
    tids = [t.trade_id for t in trades]
    L = m.LedgerEntry
    bal = s.execute(select(L.ledger, L.ledger_account, L.key, func.sum(L.amount)).where(L.sandbox_id == sandbox_id,
                    *( [L.value_date <= as_of] if as_of else [])).group_by(L.ledger, L.ledger_account, L.key)).all()
    return {
        "trades": rows(trades),
        "allocations": rows(s.scalars(select(m.Allocation).where(m.Allocation.trade_id.in_(tids)).order_by(m.Allocation.allocation_id))),
        "instructions": rows(s.scalars(select(m.SettlementInstruction).where(m.SettlementInstruction.sandbox_id == sandbox_id)
                                       .order_by(m.SettlementInstruction.instruction_id))),
        "events": rows(s.scalars(select(m.LifecycleEvent).where(m.LifecycleEvent.sandbox_id == sandbox_id).order_by(m.LifecycleEvent.event_id))),
        "exceptions": rows(s.scalars(select(m.ExceptionItem).where(m.ExceptionItem.sandbox_id == sandbox_id))),
        "approvals": rows(s.scalars(select(m.Approval).where(m.Approval.sandbox_id == sandbox_id))),
        "balances": [{"ledger": a, "account": b, "key": c, "amount": str(dec(d))} for a, b, c, d in bal],
        "ledger_rows": s.scalar(select(func.count()).select_from(L).where(L.sandbox_id == sandbox_id)),
    }


@router.delete("/test-support/sandboxes/{sandbox_id}", tags=["test-support"], dependencies=[Depends(_test_support)],
               summary="Delete everything a sandbox created (audit events are kept)")
def delete_sandbox(sandbox_id: str, s: Session = Depends(get_session), _=Depends(current_user)):
    tids = list(s.scalars(select(m.Trade.trade_id).where(m.Trade.sandbox_id == sandbox_id)))
    with translate(s):
        for model in SANDBOX_MODELS:
            s.execute(delete(model).where(model.sandbox_id == sandbox_id))
        if tids:
            for model in (m.Allocation, m.TradeVersion, m.ClearingRecord):
                s.execute(delete(model).where(model.trade_id.in_(tids)))
            s.execute(delete(m.Trade).where(m.Trade.trade_id.in_(tids)))
        s.commit()
    return {"deleted_trades": len(tids)}


@router.post("/test-support/sandboxes/{sandbox_id}/recon", tags=["test-support"], dependencies=[Depends(_test_support)])
def sandbox_positions(sandbox_id: str, as_of: date, s: Session = Depends(get_session), _=Depends(current_user)):
    return jsonable({"positions": {f"{a}|{k}": v for (a, k), v in recon.internal_positions(s, as_of, sandbox_id).items()},
                     "cash": {f"{a}|{k}": v for (a, k), v in recon.internal_cash(s, as_of, sandbox_id).items()}})


_ = (Header, refcache)
