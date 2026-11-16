"""Trade capture, enrichment and allocation."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cf360 import lifecycle
from cf360 import models as m
from cf360.clock import utcnow
from cf360.rules import calc, engine
from cf360.rules.calc import dec
from cf360.services import refcache
from cf360.services.core import audit, jsonable, raise_exception, resolve_exception, trade_snapshot

TERM_FIELDS = ["fx_counter_ccy", "fx_counter_amount", "fx_spot_rate", "fx_forward_points", "fx_tenor_months",
               "repo_rate_pct", "haircut_pct", "collateral_ccy", "sl_fee_bps", "irs_fixed_rate_pct", "irs_float_index",
               "irs_spread_bps", "irs_tenor_years", "market_timezone", "execution_venue"]
SETTLEMENT_PRODUCTS = {"EQUITY", "ETF", "GOVT_BOND", "CORP_BOND", "FX_SPOT", "FX_FORWARD", "OPTION", "REPO",
                       "REVERSE_REPO", "SEC_LOAN", "SEC_BORROW"}
CLEARED_PRODUCTS = {"EQUITY", "ETF", "FUTURE", "OPTION", "IRS"}


class BookingError(Exception):
    def __init__(self, issues):
        super().__init__("; ".join(i["code"] for i in issues))
        self.issues = issues


def _date(v):
    if v is None or isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return date.fromisoformat(str(v)[:10])


def _ts(v):
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", ""))


def normalise(p: dict) -> dict:
    t = {k: p.get(k) for k in ("trade_id", "source_system", "source_trade_ref", "order_id", "product_type", "instrument_id",
                               "client_id", "account_id", "book", "trader_id", "counterparty_id", "side", "quantity",
                               "price", "trade_ccy", "settle_ccy", "market", "ingest_batch")}
    t["trade_date"] = _date(p.get("trade_date"))
    t["settlement_date"] = _date(p.get("settlement_date"))
    t["end_date"] = _date(p.get("end_date"))
    t["trade_time_utc"] = _ts(p.get("trade_time_utc"))
    t["source_system"] = t["source_system"] or "API"
    t["source_trade_ref"] = t["source_trade_ref"] or p.get("idempotency_ref") or None
    terms = dict(p.get("terms") or {})
    for f in TERM_FIELDS:
        if p.get(f) is not None:
            terms[f] = p[f]
    t["terms"] = jsonable(terms)
    t["allocations"] = p.get("allocations")
    return t


def next_trade_id(s: Session, prefix: str = "TRD") -> str:
    n = s.scalar(select(func.count()).select_from(m.Trade).where(m.Trade.trade_id.like(f"{prefix}%"))) or 0
    while True:
        n += 1
        tid = f"{prefix}{n:05d}" if prefix == "TRD" else f"{prefix}-{n:05d}"
        if s.get(m.Trade, tid) is None:
            return tid


def book(s: Session, payload: dict, actor: tuple[str, str], sandbox_id: str | None = None, reject_log: bool = True) -> m.Trade:
    ref = refcache.get(s)
    t = normalise(payload)
    if not t.get("source_trade_ref"):
        t["source_trade_ref"] = f"AUTO-{utcnow():%Y%m%d%H%M%S%f}"
    issues, ctx = engine.validate(s, ref, t, sandbox_id)
    # R026 fills consistency
    if t.get("order_id") and not [i for i in issues if i.severity == "ERROR"]:
        fills = s.execute(select(m.Execution.exec_qty, m.Execution.exec_price).where(m.Execution.order_id == t["order_id"])).all()
        if fills:
            fq = sum(dec(f[0]) for f in fills)
            vw = calc.q_dp(sum(dec(f[0]) * dec(f[1]) for f in fills) / fq, 6)
            if fq != dec(t["quantity"]):
                issues.append(engine.Issue("R026", "E_FILL_QTY_MISMATCH", f"Fills {fq} != quantity {t['quantity']}", "quantity"))
            elif vw != calc.q_dp(dec(t["price"]), 6) and t["product_type"] in ("EQUITY", "ETF", "FUTURE", "OPTION"):
                issues.append(engine.Issue("R026", "E_AVG_PRICE_MISMATCH", f"VWAP {vw} != price {t['price']}", "price"))
    allocs = t["allocations"] or [{"account_id": t["account_id"], "alloc_qty": t["quantity"]}]
    if not [i for i in issues if i.severity == "ERROR"]:
        total = sum(dec(a["alloc_qty"]) for a in allocs)
        if total != dec(t["quantity"]):
            issues.append(engine.Issue("R024", "E_ALLOC_QTY_MISMATCH", f"Allocations {total} != quantity {t['quantity']}", "allocations"))
        for a in allocs:
            acct = s.get(m.Account, a["account_id"])
            if acct is None or acct.client_id != t["client_id"]:
                issues.append(engine.Issue("R003", "E_ALLOC_ACCOUNT", f"Allocation account {a['account_id']} invalid", "allocations"))
            elif ctx.get("group") not in acct.permitted_products.split(","):
                issues.append(engine.Issue("R003", "E_PRODUCT_NOT_PERMITTED", f"{ctx.get('group')} not permitted for {acct.account_id}", "allocations"))
    errors = [i.as_dict() for i in issues if i.severity == "ERROR"]
    warnings = [i.as_dict() for i in issues if i.severity == "WARNING"]
    if errors:
        if reject_log:
            s.add(m.BookingRejection(source_trade_ref=t.get("source_trade_ref"), ingest_batch=t.get("ingest_batch"),
                                     errors=errors, payload=jsonable({k: v for k, v in t.items()}), sandbox_id=sandbox_id))
            audit(s, "TRADE_REJECTED", "trade", t.get("source_trade_ref") or "?", details={"errors": errors})
        raise BookingError(errors)
    inst = ctx["inst"]
    tid = t.get("trade_id") or next_trade_id(s, "TRD" if not sandbox_id else "SBX")
    flags = [w["code"] for w in warnings]
    biz = s.get(m.SystemClock, 1)
    if biz and t["trade_date"] < biz.business_date and t.get("ingest_batch") and "LATE" in str(t.get("ingest_batch")):
        flags.append("LATE")  # R095
    trade = m.Trade(trade_id=tid, version=1, status="CAPTURED", source_system=t["source_system"],
                    source_trade_ref=t["source_trade_ref"], order_id=t.get("order_id"), product_group=ctx["group"],
                    product_type=t["product_type"], instrument_id=inst.instrument_id, client_id=t["client_id"],
                    account_id=t["account_id"], book=t.get("book") or "UNASSIGNED", trader_id=t.get("trader_id") or actor[0],
                    counterparty_id=t["counterparty_id"], side=t["side"], quantity=dec(t["quantity"]), price=dec(t["price"]),
                    trade_ccy=t.get("trade_ccy") or t["settle_ccy"], settle_ccy=t["settle_ccy"], trade_date=t["trade_date"],
                    trade_time_utc=t.get("trade_time_utc") or datetime.combine(t["trade_date"], time(12, 0)), market=inst.market,
                    settlement_date=ctx["settlement_date"], end_date=t.get("end_date"), terms=t["terms"], economics={},
                    flags=flags, ingest_batch=t.get("ingest_batch"), sandbox_id=sandbox_id,
                    usd_equivalent=ctx.get("usd_notional", Decimal(0)) * engine.EXPOSURE_WEIGHT[ctx["group"]])
    s.add(trade)
    s.flush()
    s.add(m.TradeVersion(trade_id=tid, version=1, snapshot=trade_snapshot(trade), change_type="NEW", changed_by=actor[0]))
    for i, a in enumerate(allocs, start=1):
        s.add(m.Allocation(allocation_id=a.get("allocation_id") or f"{tid}-A{i}", trade_id=tid, alloc_seq=a.get("alloc_seq") or i,
                           account_id=a["account_id"], alloc_qty=dec(a["alloc_qty"])))
    s.flush()
    audit(s, "TRADE_CAPTURED", "trade", tid, after=trade_snapshot(trade), details={"warnings": warnings}, actor=actor)
    if "W_POTENTIAL_DUPLICATE" in flags:
        raise_exception(s, "POTENTIAL_DUPLICATE", "MEDIUM", "MIDDLE_OFFICE", "Same economics booked twice with different references; review",
                        trade_id=tid, ref_id=tid, rule_id="R029", error_code="W_POTENTIAL_DUPLICATE", sandbox_id=sandbox_id,
                        on=t["trade_date"])
    if "W_LIMIT_WARNING" in flags:
        raise_exception(s, "LIMIT_WARNING", "LOW", "RISK", "Client exposure at or above 90% of limit", trade_id=tid, ref_id=tid,
                        rule_id="R006", error_code="W_LIMIT_WARNING", sandbox_id=sandbox_id, on=t["trade_date"])
    enrich(s, trade, actor)
    return trade


def transition(s: Session, trade: m.Trade, target: str, actor: tuple[str, str], reason: str = "") -> None:
    lifecycle.check(trade.product_type, trade.status, target)
    before = trade.status
    trade.status = target
    audit(s, "TRADE_STATUS", "trade", trade.trade_id, before={"status": before}, after={"status": target},
          details={"reason": reason}, actor=actor)


def settlement_location(ref, trade: m.Trade, inst: m.Instrument) -> str | None:
    if trade.product_type in ("FX_SPOT", "FX_FORWARD"):
        return "SIM-PAY"
    if trade.product_type in ("FUTURE", "IRS"):
        return None
    if trade.product_type == "OPTION":
        return "SIM-PAY"
    return ref.markets[inst.market].settlement_location


def ssi_for(s: Session, owner_id: str, ccy: str, location: str, on: date) -> m.SSI | None:
    return s.scalars(select(m.SSI).where(m.SSI.owner_id == owner_id, m.SSI.currency == ccy, m.SSI.settlement_location == location,
                                         m.SSI.effective_from <= on, (m.SSI.effective_to.is_(None)) | (m.SSI.effective_to >= on),
                                         m.SSI.status != "EXPIRED").order_by(m.SSI.version.desc())).first()


def required_ssis(ref, trade: m.Trade, inst: m.Instrument) -> list[tuple[str, str]]:
    """(currency, location) pairs that need SSIs on both sides."""
    loc = settlement_location(ref, trade, inst)
    if loc is None:
        return []
    if trade.product_type in ("FX_SPOT", "FX_FORWARD"):
        return [(inst.attrs["base_ccy"], "SIM-PAY"), (inst.attrs["quote_ccy"], "SIM-PAY")]
    if trade.product_type in ("SEC_LOAN", "SEC_BORROW"):
        cc = trade.terms.get("collateral_ccy") or trade.settle_ccy
        pairs = [(trade.settle_ccy, loc)]
        if cc != trade.settle_ccy:
            pairs.append((cc, "SIM-PAY"))
        return pairs
    return [(trade.settle_ccy, loc)]


def enrich(s: Session, trade: m.Trade, actor: tuple[str, str]) -> bool:
    """CAPTURED -> ENRICHED -> ALLOCATED. Missing SSIs keep the trade CAPTURED with an exception (R080/R081)."""
    ref = refcache.get(s)
    inst = ref.instruments[trade.instrument_id]
    allocs = list(s.scalars(select(m.Allocation).where(m.Allocation.trade_id == trade.trade_id,
                                                       m.Allocation.status == "ACTIVE").order_by(m.Allocation.alloc_seq)))
    missing = []
    sd = trade.settlement_date
    for ccy, loc in required_ssis(ref, trade, inst):
        for owner in {a.account_id for a in allocs} | {trade.counterparty_id}:
            if ssi_for(s, owner, ccy, loc, sd) is None:
                missing.append(f"{owner}/{ccy}/{loc}")
    if missing:
        raise_exception(s, "SSI_MISSING", "HIGH", "SETTLEMENTS", f"No effective SSI on {sd} for {', '.join(sorted(missing))}",
                        trade_id=trade.trade_id, ref_id=trade.trade_id, rule_id="R080", error_code="E_SSI_MISSING",
                        sandbox_id=trade.sandbox_id, on=trade.trade_date)
        return False
    resolve_exception(s, "SSI_MISSING", trade.trade_id, "SSI available")
    econ = engine.economics(s, ref, trade, inst)
    trade.economics = jsonable(econ)
    trade.gross_amount = econ.get("gross") or econ.get("notional") or econ.get("premium") or econ.get("cash_amount") or econ.get("base_amount")
    trade.net_amount = econ.get("settle_amount")
    if trade.status == "CAPTURED":
        transition(s, trade, "ENRICHED", actor, "economics computed, SSIs resolved")
    qtys = [dec(a.alloc_qty) for a in allocs]
    amt = dec(econ.get("settle_amount") or 0)
    parts = calc.split_amount(amt, qtys, trade.settle_ccy if trade.product_type not in ("SEC_LOAN", "SEC_BORROW")
                              else (trade.terms.get("collateral_ccy") or trade.settle_ccy)) if amt else [Decimal(0)] * len(qtys)
    for a, p in zip(allocs, parts, strict=True):
        a.settle_amount = p
    if trade.status == "ENRICHED":
        transition(s, trade, "ALLOCATED", actor, f"{len(allocs)} allocation(s)")
    return True
