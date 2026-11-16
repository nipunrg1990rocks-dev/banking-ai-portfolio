"""Seven dashboard views computed from persisted data. Every KPI carries its definition,
numerator/denominator, population and a drill-down target. Nothing here is hard-coded."""

from __future__ import annotations

import time
from collections import Counter, defaultdict, deque
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cf360 import lifecycle
from cf360 import models as m
from cf360.clock import utcnow
from cf360.rules.calc import dec, q_ccy
from cf360.rules.catalogue import RULES
from cf360.services import refcache

API_TIMINGS: deque = deque(maxlen=5000)  # (path_template, ms) recorded by middleware
FUNNEL = ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "INSTRUCTED", "PARTIALLY_SETTLED", "SETTLEMENT_FAILED",
          "SETTLED", "OPEN", "CLOSED", "EXPIRED", "EXERCISED", "MATURED", "CANCELLED"]


def kpi(id, label, value, definition, unit="", numerator=None, denominator=None, population=None, drill=None, fmt=None):
    return {"id": id, "label": label, "value": value, "unit": unit, "definition": definition, "numerator": numerator,
            "denominator": denominator, "population": population, "drill": drill, "format": fmt}


def pct(n, d):
    return round(100.0 * n / d, 2) if d else None


def _trades(s: Session, f: dict) -> list[m.Trade]:
    q = select(m.Trade).where(m.Trade.sandbox_id.is_(None))
    if f.get("product_group"):
        q = q.where(m.Trade.product_group == f["product_group"])
    if f.get("book"):
        q = q.where(m.Trade.book == f["book"])
    if f.get("currency"):
        q = q.where(m.Trade.settle_ccy == f["currency"])
    if f.get("counterparty_id"):
        q = q.where(m.Trade.counterparty_id == f["counterparty_id"])
    if f.get("status"):
        q = q.where(m.Trade.status == f["status"])
    if f.get("as_of"):
        q = q.where(m.Trade.trade_date <= f["as_of"])
    if f.get("trade_date_from"):
        q = q.where(m.Trade.trade_date >= f["trade_date_from"])
    return list(s.scalars(q))


def _usd(ref, t: m.Trade, as_of: date) -> Decimal:
    inst = ref.instruments[t.instrument_id]
    qty, px = dec(t.quantity), dec(t.price)
    if t.product_type in ("GOVT_BOND", "CORP_BOND", "REPO", "REVERSE_REPO"):
        local, ccy = qty * px / 100, t.settle_ccy
    elif t.product_type in ("FX_SPOT", "FX_FORWARD"):
        local, ccy = qty, inst.attrs["base_ccy"]
    elif t.product_type == "IRS":
        local, ccy = qty, t.settle_ccy
    elif t.product_type in ("FUTURE", "OPTION"):
        local, ccy = qty * px * dec(inst.attrs["contract_multiplier"]), t.settle_ccy
    else:
        local, ccy = qty * px, t.settle_ccy
    return q_ccy(local * ref.usd_per(ccy, as_of), "USD")


def _instr(s: Session, trade_ids: set[str] | None):
    q = select(m.SettlementInstruction).where(m.SettlementInstruction.sandbox_id.is_(None),
                                              m.SettlementInstruction.status != "CANCELLED")
    rows = list(s.scalars(q))
    return [r for r in rows if trade_ids is None or r.trade_id in trade_ids]


def _meta(s: Session, name: str, f: dict) -> dict:
    last = s.scalar(select(func.max(m.ProcessingRun.finished_at)).where(m.ProcessingRun.sandbox_id.is_(None)))
    return {"dashboard": name, "filters": {k: (str(v) if v is not None else None) for k, v in f.items()},
            "as_of": str(f["as_of"]), "refreshed_at": utcnow().isoformat(timespec="seconds") + "Z",
            "data_last_processed_at": last.isoformat(timespec="seconds") + "Z" if last else None,
            "currency_basis": f"USD equivalents use synthetic FX rates on {f['as_of']} (latest available on or before)"}


def settlement_populations(ins: list[m.SettlementInstruction], as_of: date) -> dict:
    eligible = [i for i in ins if i.settle_date <= as_of]
    pending = [i for i in ins if i.settle_date > as_of]
    settled = [i for i in eligible if i.status == "SETTLED"]
    on_time = [i for i in settled if i.settled_on and i.settled_on <= i.settle_date]
    return {"eligible": eligible, "pending": pending, "settled": settled, "on_time": on_time,
            "failed": [i for i in eligible if i.status == "FAILED"], "partial": [i for i in eligible if i.status == "PARTIAL"]}


def executive(s: Session, f: dict) -> dict:
    ref = refcache.get(s)
    trades = _trades(s, f)
    ids = {t.trade_id for t in trades}
    ins = _instr(s, ids)
    pop = settlement_populations(ins, f["as_of"])
    exc = list(s.scalars(select(m.ExceptionItem).where(m.ExceptionItem.sandbox_id.is_(None), m.ExceptionItem.status == "OPEN")))
    exc = [e for e in exc if e.trade_id is None or e.trade_id in ids]
    need_conf = [t for t in trades if t.product_type not in ("FUTURE", "OPTION")]
    auto = [t for t in need_conf if t.match_status in ("MATCHED", "MATCHED_WITHIN_TOLERANCE")]
    notional = sum((_usd(ref, t, f["as_of"]) for t in trades), Decimal(0))
    pending_appr = s.scalar(select(func.count()).select_from(m.Approval).where(m.Approval.status == "PENDING",
                                                                               m.Approval.sandbox_id.is_(None)))
    breaks = s.scalar(select(func.count()).select_from(m.ReconBreak).where(m.ReconBreak.status == "OPEN"))
    by_day = Counter(t.trade_date.isoformat() for t in trades)
    runs = list(s.scalars(select(m.ProcessingRun).where(m.ProcessingRun.sandbox_id.is_(None)).order_by(m.ProcessingRun.business_date)))
    daily = [{"date": r.business_date.isoformat(), "settled": r.stats.get("settled", 0), "failed": r.stats.get("failed", 0),
              "partial": r.stats.get("partial", 0)} for r in runs if sum(r.stats.get(k, 0) for k in ("settled", "failed", "partial"))]
    return {**_meta(s, "executive", f), "kpis": [
        kpi("trades", "Trades booked", len(trades), "Count of non-sandbox trades with trade date <= as-of, after filters",
            drill={"kind": "trades", "filters": {}}),
        kpi("notional", "Gross notional (USD)", float(notional), "Sum of |quantity x price x multiplier| (bonds and repos /100) converted to USD",
            unit="USD", fmt="money"),
        kpi("settle_rate", "Settlement success (eligible)", pct(len(pop["settled"]), len(pop["eligible"])),
            "Settled instructions / instructions due on or before the as-of date (pending instructions excluded)", unit="%",
            numerator=len(pop["settled"]), denominator=len(pop["eligible"]), population="eligible (due)",
            drill={"kind": "settlements", "filters": {"status": "FAILED"}}),
        kpi("on_time", "Settled on contractual date", pct(len(pop["on_time"]), len(pop["settled"])),
            "Instructions settled on or before their settlement date / all settled instructions", unit="%",
            numerator=len(pop["on_time"]), denominator=len(pop["settled"]), population="completed"),
        kpi("auto_match", "Auto-matched confirmations", pct(len(auto), len(need_conf)),
            "Trades matched automatically (exact or within tolerance) / trades that require a counterparty confirmation", unit="%",
            numerator=len(auto), denominator=len(need_conf), drill={"kind": "trades", "filters": {"status": "ALLOCATED"}}),
        kpi("open_exceptions", "Open exceptions", len(exc), "Exceptions with status OPEN",
            drill={"kind": "exceptions", "filters": {"status": "OPEN"}}),
        kpi("pending_settlement", "Not yet due", len(pop["pending"]), "Instructions with settlement date after the as-of date",
            population="pending", drill={"kind": "settlements", "filters": {"status": "PENDING"}}),
        kpi("approvals", "Approvals awaiting checker", pending_appr, "Maker-checker requests with status PENDING",
            drill={"kind": "approvals", "filters": {"status": "PENDING"}}),
        kpi("breaks", "Open reconciliation breaks", breaks, "Breaks against custodian statements not yet resolved or written off",
            drill={"kind": "recon", "filters": {}}),
    ], "charts": [
        {"id": "by_group", "type": "bar", "title": "Trades by product group",
         "data": [{"name": g, "value": c} for g, c in sorted(Counter(t.product_group for t in trades).items())],
         "drill": {"kind": "trades", "param": "product_group"}},
        {"id": "by_day", "type": "line", "title": "Trades by trade date", "data": [{"name": d, "value": c} for d, c in sorted(by_day.items())]},
        {"id": "daily_settlement", "type": "stacked", "title": "Daily settlement outcomes (processing runs)", "data": daily},
        {"id": "exceptions_by_type", "type": "bar", "title": "Open exceptions by type",
         "data": [{"name": k, "value": v} for k, v in sorted(Counter(e.exception_type for e in exc).items())],
         "drill": {"kind": "exceptions", "param": "exception_type"}},
    ]}


def operations(s: Session, f: dict) -> dict:
    trades = _trades(s, f)
    ids = {t.trade_id for t in trades}
    ins = _instr(s, ids)
    pop = settlement_populations(ins, f["as_of"])
    ref = refcache.get(s)
    status_counts = Counter(t.status for t in trades)
    exc = [e for e in s.scalars(select(m.ExceptionItem).where(m.ExceptionItem.sandbox_id.is_(None)))
           if e.trade_id is None or e.trade_id in ids]
    open_exc = [e for e in exc if e.status == "OPEN"]
    buckets = Counter()
    for i in pop["failed"] + pop["partial"]:
        age = ref.cals.count_between(i.first_fail_date or i.settle_date, f["as_of"], "USNY") if (i.first_fail_date or i.settle_date) <= f["as_of"] else 0
        buckets["0-1" if age <= 1 else "2-5" if age <= 5 else "6-10" if age <= 10 else ">10"] += 1
    exc_age = Counter()
    for e in open_exc:
        age = ref.cals.count_between(e.opened_business_date, f["as_of"], "USNY") if e.opened_business_date <= f["as_of"] else 0
        exc_age["0-1" if age <= 1 else "2-5" if age <= 5 else "6-10" if age <= 10 else ">10"] += 1
    match = Counter(t.match_status or "AWAITING" for t in trades if t.product_type not in ("FUTURE", "OPTION"))
    fails = Counter(i.fail_reason for i in pop["failed"] + pop["partial"])
    return {**_meta(s, "operations", f), "kpis": [
        kpi("unmatched", "Matching queue", len([t for t in trades if t.status == "ALLOCATED"]),
            "Trades allocated but not yet confirmed (missing or mismatched confirmations)",
            drill={"kind": "trades", "filters": {"status": "ALLOCATED"}}),
        kpi("to_clear", "Clearing queue", status_counts.get("CONFIRMED", 0) + status_counts.get("CLEARED", 0),
            "Trades confirmed or cleared awaiting the next step", drill={"kind": "trades", "filters": {"status": "CLEARED"}}),
        kpi("failing", "Failing instructions", len(pop["failed"]), "Eligible instructions in FAILED status",
            numerator=len(pop["failed"]), denominator=len(pop["eligible"]), drill={"kind": "settlements", "filters": {"status": "FAILED"}}),
        kpi("partial", "Partially settled", len(pop["partial"]), "Eligible instructions with residual quantity",
            drill={"kind": "settlements", "filters": {"status": "PARTIAL"}}),
        kpi("exc_open", "Open exceptions", len(open_exc), "All open exceptions", drill={"kind": "exceptions", "filters": {"status": "OPEN"}}),
        kpi("exc_resolved", "Resolved exceptions", len([e for e in exc if e.status == "RESOLVED"]), "Exceptions resolved (incl. auto-resolved on settlement)"),
    ], "charts": [
        {"id": "funnel", "type": "funnel", "title": "Lifecycle status (current)",
         "data": [{"name": st, "value": status_counts.get(st, 0)} for st in FUNNEL if status_counts.get(st)],
         "drill": {"kind": "trades", "param": "status"}},
        {"id": "matching", "type": "pie", "title": "Matching outcomes", "data": [{"name": k, "value": v} for k, v in sorted(match.items())]},
        {"id": "fail_ageing", "type": "bar", "title": "Settlement fail ageing (business days, R086)",
         "data": [{"name": b, "value": buckets.get(b, 0)} for b in ("0-1", "2-5", "6-10", ">10")]},
        {"id": "exc_ageing", "type": "bar", "title": "Open exception ageing (business days)",
         "data": [{"name": b, "value": exc_age.get(b, 0)} for b in ("0-1", "2-5", "6-10", ">10")]},
        {"id": "fail_reasons", "type": "bar", "title": "Fail and partial reasons", "data": [{"name": k or "-", "value": v} for k, v in fails.items()]},
        {"id": "exc_owner", "type": "bar", "title": "Open exceptions by owner",
         "data": [{"name": k, "value": v} for k, v in sorted(Counter(e.owner_role for e in open_exc).items())],
         "drill": {"kind": "exceptions", "param": "owner_role"}},
    ]}


def product(s: Session, f: dict) -> dict:
    ref = refcache.get(s)
    trades = _trades(s, f)
    groups = defaultdict(list)
    for t in trades:
        groups[t.product_group].append(t)
    rows = []
    for g, ts in sorted(groups.items()):
        ins = _instr(s, {t.trade_id for t in ts})
        pop = settlement_populations(ins, f["as_of"])
        rows.append({"product_group": g, "trades": len(ts), "notional_usd": float(sum(_usd(ref, t, f["as_of"]) for t in ts)),
                     "eligible": len(pop["eligible"]), "settled": len(pop["settled"]),
                     "settle_rate": pct(len(pop["settled"]), len(pop["eligible"])), "pending": len(pop["pending"]),
                     "open_or_live": len([t for t in ts if t.status == "OPEN"])})
    eq = [t for t in trades if t.product_group == "EQUITY"]
    comm = defaultdict(Decimal)
    for t in eq:
        comm[t.market] += dec((t.economics or {}).get("commission") or 0) * refcache.get(s).usd_per(t.settle_ccy, f["as_of"])
    bonds = [t for t in trades if t.product_group == "BOND"]
    accrued = sum((dec((t.economics or {}).get("accrued") or 0) * ref.usd_per(t.settle_ccy, f["as_of"]) for t in bonds), Decimal(0))
    fx = Counter()
    for t in trades:
        if t.product_group == "FX":
            fx[t.instrument_id[2:]] += float(_usd(ref, t, f["as_of"]))
    vm = s.execute(select(m.LifecycleEvent.currency, func.sum(m.LifecycleEvent.amount)).where(
        m.LifecycleEvent.event_type == "VARIATION_MARGIN", m.LifecycleEvent.sandbox_id.is_(None),
        m.LifecycleEvent.event_date <= f["as_of"]).group_by(m.LifecycleEvent.currency)).all()
    irs = Counter()
    for t in trades:
        if t.product_group == "IRS":
            irs[f"{t.settle_ccy} {t.terms.get('irs_tenor_years')}Y"] += float(_usd(ref, t, f["as_of"]))
    fin = Counter()
    for t in trades:
        if t.product_group == "FINANCING":
            fin[t.product_type] += float(dec((t.economics or {}).get("settle_amount") or 0) * ref.usd_per(t.settle_ccy, f["as_of"]))
    return {**_meta(s, "product", f), "kpis": [
        kpi("eq_comm", "Equity commission (USD)", float(sum(comm.values())), "Sum of commissions converted at as-of FX", unit="USD", fmt="money",
            drill={"kind": "trades", "filters": {"product_group": "EQUITY"}}),
        kpi("bond_ai", "Bond accrued interest traded (USD)", float(accrued), "Sum of accrued interest amounts on bond trades", unit="USD", fmt="money",
            drill={"kind": "trades", "filters": {"product_group": "BOND"}}),
        kpi("vm", "Futures variation margin (net, USD)", float(sum((dec(v) * ref.usd_per(c, f["as_of"]) for c, v in vm), Decimal(0))),
            "Net daily variation margin paid(-)/received(+) by client accounts", unit="USD", fmt="money",
            drill={"kind": "events", "filters": {"event_type": "VARIATION_MARGIN"}}),
        kpi("live_swaps", "Live swaps", len([t for t in trades if t.product_type == "IRS" and t.status == "OPEN"]),
            "Swaps in OPEN status", drill={"kind": "trades", "filters": {"product_group": "IRS"}}),
    ], "table": rows, "charts": [
        {"id": "eq_comm", "type": "bar", "title": "Equity commission by market (USD)", "data": [{"name": k, "value": float(v)} for k, v in sorted(comm.items())]},
        {"id": "fx_pairs", "type": "bar", "title": "FX volume by pair (USD)", "data": [{"name": k, "value": v} for k, v in sorted(fx.items())]},
        {"id": "irs", "type": "bar", "title": "Swap notional by currency and tenor (USD)", "data": [{"name": k, "value": v} for k, v in sorted(irs.items())]},
        {"id": "financing", "type": "bar", "title": "Financing cash/collateral by type (USD)", "data": [{"name": k, "value": v} for k, v in sorted(fin.items())]},
        {"id": "by_group_rate", "type": "bar", "title": "Settlement success by product group (%)",
         "data": [{"name": r["product_group"], "value": r["settle_rate"] or 0} for r in rows], "drill": {"kind": "trades", "param": "product_group"}},
    ]}


def risk(s: Session, f: dict) -> dict:
    clients = {c.client_id: c for c in s.scalars(select(m.Client).where(m.Client.sandbox_id.is_(None)))}
    used = dict(s.execute(select(m.Trade.client_id, func.sum(m.Trade.usd_equivalent)).where(
        m.Trade.sandbox_id.is_(None), m.Trade.status.not_in(("CANCELLED", "CLOSED", "MATURED", "EXPIRED", "EXERCISED")))
        .group_by(m.Trade.client_id)).all())
    util = sorted(({"client_id": cid, "name": clients[cid].short_name, "used_usd": float(u or 0),
                    "limit_usd": float(clients[cid].exposure_limit_usd),
                    "utilisation_pct": round(100 * float(u or 0) / float(clients[cid].exposure_limit_usd), 2)}
                   for cid, u in used.items() if cid in clients), key=lambda r: -r["utilisation_pct"])
    rej = list(s.scalars(select(m.BookingRejection).where(m.BookingRejection.sandbox_id.is_(None))))
    breaches = [r for r in rej if any(e["code"] == "E_LIMIT_BREACH" for e in r.errors)]
    exc = list(s.scalars(select(m.ExceptionItem).where(m.ExceptionItem.sandbox_id.is_(None))))
    calls = [e for e in exc if e.exception_type == "MARGIN_CALL"]
    warns = [e for e in exc if e.exception_type == "LIMIT_WARNING"]
    appr = Counter(a.status for a in s.scalars(select(m.Approval).where(m.Approval.sandbox_id.is_(None))))
    blocked = s.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "APPROVAL_BLOCKED_SELF")) or 0
    im = s.execute(select(m.Trade.settle_ccy, func.count()).where(m.Trade.product_type == "FUTURE", m.Trade.status == "OPEN",
                                                                  m.Trade.sandbox_id.is_(None)).group_by(m.Trade.settle_ccy)).all()
    return {**_meta(s, "risk", f), "kpis": [
        kpi("max_util", "Highest limit utilisation", util[0]["utilisation_pct"] if util else 0,
            "Max over clients of weighted USD exposure / limit (R006 exposure proxy, not a risk model)", unit="%",
            drill={"kind": "trades", "filters": {"client_id": util[0]["client_id"]}} if util else None),
        kpi("breaches", "Limit breaches blocked", len(breaches), "Bookings rejected with E_LIMIT_BREACH", drill={"kind": "rejections", "filters": {"code": "E_LIMIT_BREACH"}}),
        kpi("warnings", "Limit warnings (>=90%)", len(warns), "Bookings accepted with W_LIMIT_WARNING",
            drill={"kind": "exceptions", "filters": {"exception_type": "LIMIT_WARNING"}}),
        kpi("margin_calls", "Repo margin calls", len(calls), "Daily repo revaluations where deficit exceeded the MTA (R072)",
            drill={"kind": "exceptions", "filters": {"exception_type": "MARGIN_CALL"}}),
        kpi("approvals_pending", "Approvals pending", appr.get("PENDING", 0), "Maker-checker requests awaiting a checker",
            drill={"kind": "approvals", "filters": {"status": "PENDING"}}),
        kpi("self_blocked", "Self-approvals blocked", blocked, "Attempts by a maker to approve their own request (R092)"),
    ], "table": util[:15], "charts": [
        {"id": "util", "type": "bar", "title": "Exposure utilisation by client (%)", "data": [{"name": r["name"], "value": r["utilisation_pct"]} for r in util[:12]]},
        {"id": "approvals", "type": "pie", "title": "Approval decisions", "data": [{"name": k, "value": v} for k, v in sorted(appr.items())]},
        {"id": "futures_open", "type": "bar", "title": "Open futures trades by currency", "data": [{"name": c, "value": n} for c, n in im]},
    ]}


def data_quality(s: Session, f: dict) -> dict:
    dq = list(s.scalars(select(m.DQResult)))
    rej = list(s.scalars(select(m.BookingRejection).where(m.BookingRejection.sandbox_id.is_(None))))
    trades = _trades(s, f)
    missing_econ = [t for t in trades if not t.economics]
    dup = [t for t in trades if "W_POTENTIAL_DUPLICATE" in (t.flags or [])]
    by_dim = defaultdict(lambda: [0, 0])
    for r in dq:
        by_dim[r.dimension][0] += r.checked
        by_dim[r.dimension][1] += r.failed
    codes = Counter(e["code"] for r in rej for e in r.errors)
    lake_batches = sorted({r.batch_id for r in dq})
    quarantined = sum(r.failed for r in dq if r.dimension != "uniqueness")
    return {**_meta(s, "data_quality", f), "kpis": [
        kpi("completeness", "Platform completeness", pct(len(trades) - len(missing_econ), len(trades)),
            "Trades with computed economics / trades", unit="%", numerator=len(trades) - len(missing_econ), denominator=len(trades)),
        kpi("dups", "Potential duplicates flagged", len(dup), "Trades with W_POTENTIAL_DUPLICATE (R029 soft check)",
            drill={"kind": "exceptions", "filters": {"exception_type": "POTENTIAL_DUPLICATE"}}),
        kpi("rejections", "Booking rejections", len(rej), "API/loader bookings rejected by validation rules",
            drill={"kind": "rejections", "filters": {}}),
        kpi("lake_quarantine", "Lakehouse quarantined rows", quarantined,
            "Rows quarantined by Silver data-quality rules across imported lakehouse batches (local Spark run unless stated)",
            population=", ".join(lake_batches) or "no lakehouse run imported"),
    ], "charts": [
        {"id": "dims", "type": "bar", "title": "Lakehouse checks by dimension (pass %)",
         "data": [{"name": k, "value": pct(v[0] - v[1], v[0]) or 0, "checked": v[0], "failed": v[1]} for k, v in sorted(by_dim.items())]},
        {"id": "rej_codes", "type": "bar", "title": "Rejections by error code", "data": [{"name": k, "value": v} for k, v in codes.most_common()],
         "drill": {"kind": "rejections", "param": "code"}},
    ], "table": [{"batch_id": r.batch_id, "table": r.table_name, "rule_id": r.rule_id, "dimension": r.dimension, "checked": r.checked,
                  "failed": r.failed, "environment": r.environment} for r in dq]}


def testing(s: Session, f: dict) -> dict:
    runs = list(s.scalars(select(m.TestRun).order_by(m.TestRun.started_at)))
    run_id = f.get("test_run") or (runs[-1].run_id if runs else None)
    res = list(s.scalars(select(m.TestResult).where(m.TestResult.run_id == run_id))) if run_id else []
    status = Counter(r.status for r in res)
    by_project = defaultdict(Counter)
    for r in res:
        by_project[r.project][r.status] += 1
    rules_hit = {rid for r in res if r.status in ("passed", "flaky") for rid in (r.rule_ids or [])}
    all_rules = [r["id"] for r in RULES]
    scen = {r.scenario_id for r in res if r.scenario_id}
    defects = Counter(r.defect_id for r in res if r.defect_id and r.status == "failed")
    trend = [{"name": r.label, "passed": r.summary.get("passed", 0), "failed": r.summary.get("failed", 0),
              "flaky": r.summary.get("flaky", 0), "skipped": r.summary.get("skipped", 0)} for r in runs]
    executed = sum(v for k, v in status.items() if k != "skipped")
    return {**_meta(s, "testing", f), "test_run": run_id, "runs": [{"run_id": r.run_id, "label": r.label, "defects": r.defects_enabled}
                                                                  for r in runs], "kpis": [
        kpi("executed", "Executed cases", executed, "Test results with status passed/failed/flaky in the selected run",
            drill={"kind": "tests", "filters": {"run_id": run_id}}),
        kpi("pass_rate", "Pass rate", pct(status.get("passed", 0) + status.get("flaky", 0), executed), "(passed + flaky) / executed", unit="%",
            numerator=status.get("passed", 0) + status.get("flaky", 0), denominator=executed),
        kpi("failed", "Failed", status.get("failed", 0), "Failed results (each linked to evidence)",
            drill={"kind": "tests", "filters": {"run_id": run_id, "status": "failed"}}),
        kpi("flaky", "Flaky", status.get("flaky", 0), "Passed only after a retry"),
        kpi("skipped", "Skipped", status.get("skipped", 0), "Skipped results, listed with reason"),
        kpi("rule_cov", "Rule coverage", pct(len(rules_hit & set(all_rules)), len(all_rules)),
            "Catalogue rules with at least one passing executed case / catalogue rules", unit="%",
            numerator=len(rules_hit & set(all_rules)), denominator=len(all_rules)),
        kpi("scenarios", "Distinct scenarios executed", len(scen), "Unique scenario ids among results"),
    ], "charts": [
        {"id": "by_project", "type": "stacked", "title": "Results by project / browser",
         "data": [{"name": p, **dict(c)} for p, c in sorted(by_project.items())]},
        {"id": "trend", "type": "stacked", "title": "Run history", "data": trend},
        {"id": "defects", "type": "bar", "title": "Failures by injected defect", "data": [{"name": k, "value": v} for k, v in sorted(defects.items())]},
    ]}


def engineering(s: Session, f: dict) -> dict:
    runs = list(s.scalars(select(m.ProcessingRun).where(m.ProcessingRun.sandbox_id.is_(None)).order_by(m.ProcessingRun.business_date)))
    durs = [(r.finished_at - r.started_at).total_seconds() * 1000 for r in runs if r.finished_at]
    jobs = list(s.scalars(select(m.JobRun).order_by(m.JobRun.started_at)))
    timings = list(API_TIMINGS)
    per_path = defaultdict(list)
    for p, ms in timings:
        per_path[p].append(ms)

    def p95(v):
        v = sorted(v)
        return round(v[int(0.95 * (len(v) - 1))], 1) if v else None
    latest = max((j.finished_at for j in jobs if j.finished_at), default=None)
    return {**_meta(s, "engineering", f), "kpis": [
        kpi("eod_runs", "EOD processing runs", len(runs), "Completed daily processing runs (main book)"),
        kpi("eod_p95", "EOD run p95 (ms)", p95(durs), "95th percentile wall time of daily processing runs", unit="ms"),
        kpi("jobs", "Lakehouse task runs imported", len(jobs), "Job task runs imported from lakehouse evidence"),
        kpi("freshness", "Lakehouse freshness", latest.isoformat(timespec="seconds") + "Z" if latest else "no run imported",
            "Finish time of the latest imported lakehouse task"),
        kpi("api_p95", "API p95 latency (ms)", p95([ms for _, ms in timings]), f"p95 over the last {len(timings)} API requests since start", unit="ms"),
    ], "charts": [
        {"id": "eod", "type": "line", "title": "EOD run duration (ms)", "data": [{"name": r.business_date.isoformat(), "value": round(d, 1)}
                                                                             for r, d in zip(runs, durs, strict=False)]},
        {"id": "jobs", "type": "bar", "title": "Lakehouse tasks: rows out", "data": [{"name": f"{j.task}", "value": j.rows_out,
                                                                                       "quarantined": j.rows_quarantined, "status": j.status,
                                                                                       "env": j.environment} for j in jobs[-12:]]},
        {"id": "api", "type": "bar", "title": "API p95 by endpoint (ms)", "data": [{"name": p, "value": p95(v)} for p, v in sorted(per_path.items())][:12]},
    ], "lineage": LINEAGE, "table": [{"job_run_id": j.job_run_id, "environment": j.environment, "task": j.task, "batch": j.batch_id,
                                      "status": j.status, "rows_in": j.rows_in, "rows_out": j.rows_out,
                                      "quarantined": j.rows_quarantined} for j in jobs]}


LINEAGE = [
    {"from": "Excel workbooks (data/excel)", "to": "Parquet landing (validated conversion)"},
    {"from": "Parquet landing (validated conversion)", "to": "bronze_* (Delta, immutable snapshots)"},
    {"from": "bronze_* (Delta, immutable snapshots)", "to": "silver_trades / silver_reference (typed, deduped)"},
    {"from": "bronze_* (Delta, immutable snapshots)", "to": "silver_quarantine"},
    {"from": "silver_trades / silver_reference (typed, deduped)", "to": "gold_trade_lifecycle"},
    {"from": "silver_trades / silver_reference (typed, deduped)", "to": "gold_cash_position_projection"},
    {"from": "gold_trade_lifecycle", "to": "gold_test_cases (1,500+ cases)"},
    {"from": "gold_test_cases (1,500+ cases)", "to": "Playwright API/UI suites"},
    {"from": "Excel workbooks (data/excel)", "to": "Platform loader (PostgreSQL)"},
    {"from": "Platform loader (PostgreSQL)", "to": "Dashboards and reports"},
    {"from": "Playwright API/UI suites", "to": "Testing dashboard"},
]

VIEWS = {"executive": executive, "operations": operations, "product": product, "risk": risk, "data-quality": data_quality,
         "testing": testing, "engineering": engineering}

_ = (lifecycle, time)
