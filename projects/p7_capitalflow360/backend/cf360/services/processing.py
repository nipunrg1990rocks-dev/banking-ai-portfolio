"""Restartable daily processing: matching, clearing, instructions, settlement and lifecycle events.

Every step is idempotent: re-running a business date changes nothing that was already done
(ledger postings carry unique event keys, event rows have deterministic ids, and each step only
selects trades/instructions still in the state it acts on).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.rules import calc, engine
from cf360.rules.calc import CCY_CALENDAR, dec, q_ccy
from cf360.services import booking, refcache
from cf360.services.core import audit, post, processing_date, raise_exception, resolve_exception

SYSTEM = ("system", "SYSTEM")
MATCHED = ("MATCHED", "MATCHED_WITHIN_TOLERANCE")


def _scope(q, model, sandbox_id):
    col = model.sandbox_id
    return q.where(col == sandbox_id) if sandbox_id else q.where(col.is_(None))


# ------------------------------------------------------------------------------------- matching
def match(trade: m.Trade, conf: m.Confirmation | None) -> tuple[str, dict]:
    if conf is None:
        return "UNMATCHED_MISSING", {}
    econ = trade.economics or {}
    detail = {"quantity": [str(trade.quantity), str(conf.quantity)], "price": [str(trade.price), str(conf.price)],
              "settle_date": [str(trade.settlement_date), str(conf.settle_date)],
              "settle_amount": [econ.get("settle_amount"), str(conf.settle_amount)]}
    if dec(conf.quantity) != dec(trade.quantity):
        return "MISMATCH_QUANTITY", detail
    if dec(conf.price) != dec(trade.price):
        return "MISMATCH_PRICE", detail
    if conf.settle_date != trade.settlement_date:
        return "MISMATCH_SETTLE_DATE", detail
    ours = dec(econ.get("settle_amount") or 0)
    if ours and conf.settle_amount is not None:
        diff = abs(ours - dec(conf.settle_amount))
        detail["difference"] = str(diff)
        if diff > calc.amount_tolerance(trade.settle_ccy):
            return "MISMATCH_AMOUNT", detail
        if diff:
            return "MATCHED_WITHIN_TOLERANCE", detail
    return "MATCHED", detail


def step_matching(s: Session, d: date, sandbox_id=None) -> int:
    n = 0
    trades = s.scalars(_scope(select(m.Trade).where(m.Trade.status == "ALLOCATED", m.Trade.trade_date <= d), m.Trade, sandbox_id)
                       .order_by(m.Trade.trade_id)).all()
    ref = refcache.get(s)
    for t in trades:
        if t.product_type in ("FUTURE", "OPTION"):
            result, detail = "MATCHED", {"source": "CCP trade feed"}
        else:
            conf = s.scalars(select(m.Confirmation).where(m.Confirmation.trade_id == t.trade_id,
                                                          func.date(m.Confirmation.received_ts_utc) <= d)
                             .order_by(m.Confirmation.received_ts_utc.desc())).first()
            if conf is None:
                if ref.cals.count_between(t.trade_date, d, CCY_CALENDAR[t.settle_ccy]) >= 1:
                    raise_exception(s, "MISSING_CONFIRMATION", "MEDIUM", "MIDDLE_OFFICE",
                                    f"No counterparty confirmation received for {t.trade_id}; chase {t.counterparty_id}",
                                    trade_id=t.trade_id, ref_id=t.trade_id, rule_id="R082", error_code="E_CONFIRMATION_MISSING",
                                    sandbox_id=sandbox_id, on=d)
                continue
            result, detail = match(t, conf)
            conf.match_result, conf.match_detail = result, detail
        if t.match_status == result and result not in MATCHED:
            continue
        t.match_status = result
        if result in MATCHED:
            resolve_exception(s, "MISSING_CONFIRMATION", t.trade_id, "confirmation received", d)
            resolve_exception(s, "MATCH_BREAK", t.trade_id, "matched", d)
            booking.transition(s, t, "CONFIRMED", SYSTEM, result)
            n += 1
        else:
            resolve_exception(s, "MISSING_CONFIRMATION", t.trade_id, "confirmation received", d)
            raise_exception(s, "MATCH_BREAK", "HIGH", "MIDDLE_OFFICE", f"{result}: ours vs counterparty {detail}",
                            trade_id=t.trade_id, ref_id=t.trade_id, rule_id="R082", error_code="E_MATCH_MISMATCH",
                            sandbox_id=sandbox_id, on=d)
    return n


# ------------------------------------------------------------------------------------- clearing
def ccp_for(t: m.Trade, inst: m.Instrument) -> str:
    if t.product_type in ("FUTURE", "OPTION"):
        return inst.attrs["ccp_id"]
    if t.product_type == "IRS":
        return "CCP-RATES"
    return {"XNYS": "CCP-US", "XLON": "CCP-EU", "XETR": "CCP-EU"}.get(t.market, "SIM-CCP-JP")


def step_clearing(s: Session, d: date, sandbox_id=None) -> int:
    ref = refcache.get(s)
    n = 0
    trades = s.scalars(_scope(select(m.Trade).where(m.Trade.status == "CONFIRMED", m.Trade.trade_date <= d,
                                                    m.Trade.product_type.in_(booking.CLEARED_PRODUCTS)), m.Trade, sandbox_id)
                       .order_by(m.Trade.trade_id)).all()
    for t in trades:
        inst = ref.instruments[t.instrument_id]
        ccp = ccp_for(t, inst)
        cid = f"CLR-{t.trade_id}-v{t.version}"
        if s.get(m.ClearingRecord, cid) is None:
            s.add(m.ClearingRecord(clearing_id=cid, trade_id=t.trade_id, ccp_id=ccp, status="ACCEPTED",
                                   novation_ref=f"NOV-{ccp}-{t.trade_id}", business_date=d))
            s.add(m.InterfaceMessage(message_id=f"MSG-CCP-{cid}", system="CCP", direction="IN", message_type="CLEARING_ACK",
                                     correlation_id=t.trade_id, ref_id=t.trade_id, payload={"ccp": ccp, "status": "ACCEPTED"},
                                     status="PROCESSED", business_date=d, sandbox_id=sandbox_id))
        booking.transition(s, t, "CLEARED", SYSTEM, f"novated to {ccp}")
        if t.product_type == "FUTURE":
            booking.transition(s, t, "OPEN", SYSTEM, "position open; daily variation margin")
            for a in _allocs(s, t):
                sign = 1 if t.side == "BUY" else -1
                post(s, f"FUTOPEN:{a.allocation_id}", "SEC", [(a.account_id, t.instrument_id, sign * dec(a.alloc_qty)),
                                                              (f"CCP:{ccp}", t.instrument_id, -sign * dec(a.alloc_qty))],
                     t.trade_date, "FUTURE_OPEN", t.trade_id, sandbox_id)
        elif t.product_type == "IRS":
            booking.transition(s, t, "OPEN", SYSTEM, "swap live")
        n += 1
    return n


def _allocs(s: Session, t: m.Trade) -> list[m.Allocation]:
    return list(s.scalars(select(m.Allocation).where(m.Allocation.trade_id == t.trade_id, m.Allocation.status == "ACTIVE")
                          .order_by(m.Allocation.alloc_seq)))


# ------------------------------------------------------------------------------------- instructions
def step_instruct(s: Session, d: date, sandbox_id=None) -> int:
    ref = refcache.get(s)
    n = 0
    cond = or_(and_(m.Trade.status == "CONFIRMED", m.Trade.product_type.not_in(booking.CLEARED_PRODUCTS)),
               and_(m.Trade.status == "CLEARED", m.Trade.product_type.in_(("EQUITY", "ETF", "OPTION"))))
    trades = s.scalars(_scope(select(m.Trade).where(cond, m.Trade.trade_date <= d), m.Trade, sandbox_id).order_by(m.Trade.trade_id)).all()
    for t in trades:
        if t.product_type not in booking.SETTLEMENT_PRODUCTS:
            continue
        inst = ref.instruments[t.instrument_id]
        create_instructions(s, ref, t, inst, d)
        booking.transition(s, t, "INSTRUCTED", SYSTEM, "settlement instructions released")
        n += 1
    return n


def create_instructions(s: Session, ref, t: m.Trade, inst: m.Instrument, d: date, release_utc: datetime | None = None):
    econ = t.economics
    loc = booking.settlement_location(ref, t, inst)
    allocs = _allocs(s, t)
    qtys = [dec(a.alloc_qty) for a in allocs]
    buy = t.side == "BUY"
    sd = t.settlement_date
    conv = engine.expected_settlement_date(ref, inst, {"product_type": t.product_type, "trade_date": t.trade_date,
                                                       "settle_ccy": t.settle_ccy, "terms": t.terms,
                                                       "settlement_date": t.settlement_date})[1]
    release = release_utc or t.trade_time_utc
    rolled = engine.cutoff_missed(ref, conv, release, sd)
    if rolled:
        sd = engine.next_bday(ref, sd, t.settle_ccy)
    rows = []

    def add(alloc, suffix, leg, direction, sec, cash, ccy, on, fx=None, instrument=True, location=loc):
        key = alloc.allocation_id + suffix
        our = booking.ssi_for(s, alloc.account_id, ccy, location, on) if location else None
        cp = booking.ssi_for(s, t.counterparty_id, ccy, location, on) if location else None
        rows.append(m.SettlementInstruction(
            instruction_id=f"SI-{key}-v{t.version}", trade_id=t.trade_id, allocation_id=key, leg=leg, account_id=alloc.account_id,
            instrument_id=t.instrument_id if instrument else None, direction=direction, sec_qty=sec, cash_amount=cash,
            currency=ccy, fx_legs=fx, settle_date=on, settlement_location=location or "-", our_ssi_id=our.ssi_id if our else None,
            cpty_ssi_id=cp.ssi_id if cp else None, status="PENDING", cutoff_missed=rolled and leg not in ("REPO_END", "SL_END", "SL_FEE"),
            sandbox_id=t.sandbox_id))

    if t.product_type in ("EQUITY", "ETF", "GOVT_BOND", "CORP_BOND"):
        for a in allocs:
            q, c = dec(a.alloc_qty), dec(a.settle_amount)
            add(a, "", "DVP", "RECEIVE" if buy else "DELIVER", q if buy else -q, -c if buy else c, t.settle_ccy, sd)
    elif t.product_type in ("FX_SPOT", "FX_FORWARD"):
        base, quote = inst.attrs["base_ccy"], inst.attrs["quote_ccy"]
        counters = calc.split_amount(dec(econ["counter_amount"]), qtys, quote)
        for a, cnt in zip(allocs, counters, strict=True):
            sign = 1 if buy else -1
            add(a, "", "FX", "FX", Decimal(0), Decimal(0), base, sd,
                fx=[[base, str(sign * dec(a.alloc_qty))], [quote, str(-sign * cnt)]], instrument=False)
    elif t.product_type == "OPTION":
        for a in allocs:
            c = dec(a.settle_amount)
            add(a, "", "PREMIUM", "PAY" if buy else "COLLECT", Decimal(0), -c if buy else c, t.settle_ccy, sd, instrument=False)
    elif t.product_type in ("REPO", "REVERSE_REPO"):
        repo = t.product_type == "REPO"
        cash_parts = calc.split_amount(dec(econ["cash_amount"]), qtys, t.settle_ccy)
        rep_parts = calc.split_amount(dec(econ["repurchase_amount"]), qtys, t.settle_ccy)
        for a, cash, rep in zip(allocs, cash_parts, rep_parts, strict=True):
            q = dec(a.alloc_qty)
            add(a, "", "REPO_START", "DELIVER" if repo else "RECEIVE", -q if repo else q, cash if repo else -cash, t.settle_ccy, sd)
            add(a, "-E", "REPO_END", "RECEIVE" if repo else "DELIVER", q if repo else -q, -rep if repo else rep, t.settle_ccy, t.end_date)
    elif t.product_type in ("SEC_LOAN", "SEC_BORROW"):
        loan = t.product_type == "SEC_LOAN"
        cc = econ["collateral_ccy"]
        colls = calc.split_amount(dec(econ["collateral_amount"]), qtys, cc)
        fees = calc.split_amount(dec(econ["loan_fee"]), qtys, t.settle_ccy)
        for a, coll, fee in zip(allocs, colls, fees, strict=True):
            q = dec(a.alloc_qty)
            add(a, "", "SL_START", "DELIVER" if loan else "RECEIVE", -q if loan else q, coll if loan else -coll, cc, sd)
            add(a, "-E", "SL_END", "RECEIVE" if loan else "DELIVER", q if loan else -q, -coll if loan else coll, cc, t.end_date)
            add(a, "-F", "SL_FEE", "COLLECT" if loan else "PAY", Decimal(0), fee if loan else -fee, t.settle_ccy, t.end_date,
                instrument=False, location="SIM-PAY")
    for r in rows:
        if s.get(m.SettlementInstruction, r.instruction_id) is None:
            s.add(r)
            s.add(m.InterfaceMessage(message_id=f"MSG-OUT-{r.instruction_id}", system="CUSTODIAN", direction="OUT",
                                     message_type="SETTLEMENT_INSTRUCTION", correlation_id=t.trade_id, ref_id=r.instruction_id,
                                     payload={"leg": r.leg, "qty": str(r.sec_qty), "cash": str(r.cash_amount), "ccy": r.currency,
                                              "settle_date": r.settle_date.isoformat()},
                                     status="PROCESSED", business_date=d, sandbox_id=t.sandbox_id))
    if rolled:
        raise_exception(s, "CUTOFF_MISSED", "LOW", "SETTLEMENTS", f"Instruction released after cut-off; settles {sd}",
                        trade_id=t.trade_id, ref_id=t.trade_id, rule_id="R013", error_code="W_CUTOFF_MISSED",
                        sandbox_id=t.sandbox_id, on=d)
    s.flush()


# ------------------------------------------------------------------------------------- settlement
def _lot(inst: m.Instrument | None) -> Decimal:
    if inst is None:
        return Decimal(1)
    if inst.product_type in ("EQUITY", "ETF"):
        return Decimal(int(inst.attrs.get("lot_size", 1)))
    if inst.product_type in ("GOVT_BOND", "CORP_BOND"):
        return Decimal(int(inst.attrs.get("min_increment", 1)))
    return Decimal(1)


def step_settlement(s: Session, d: date, sandbox_id=None) -> dict:
    ref = refcache.get(s)
    stats = {"settled": 0, "partial": 0, "failed": 0}
    due = s.scalars(_scope(select(m.SettlementInstruction).where(
        m.SettlementInstruction.settle_date <= d, m.SettlementInstruction.status.in_(("PENDING", "FAILED", "PARTIAL"))),
        m.SettlementInstruction, sandbox_id)).all()
    due.sort(key=lambda i: (0 if i.sec_qty >= 0 else 1, i.allocation_id))
    touched = set()
    for ins in due:
        t = s.get(m.Trade, ins.trade_id)
        if t.status == "CANCELLED":
            continue
        touched.add(t.trade_id)
        ins.attempts += 1
        oc = s.get(m.ControlledOutcome, ins.allocation_id)
        if oc and oc.status == "FAILED" and (oc.resolved_date is None or d < oc.resolved_date):
            _fail(s, ins, t, d, oc.reason_code, "Custodian reported a fail")
            stats["failed"] += 1
            continue
        acct = s.get(m.Account, ins.account_id)
        if ins.leg == "FX":
            legs = [(c, dec(a)) for c, a in ins.fx_legs]
            short = _cash_short(s, ref, acct, legs, d)
            if short:
                _fail(s, ins, t, d, "INSUFFICIENT_CASH", short)
                stats["failed"] += 1
                continue
            for i, (c, a) in enumerate(legs):
                post(s, f"SETTLE:{ins.instruction_id}:FX{i}", "CASH", [(ins.account_id, c, a), (f"CPTY:{t.counterparty_id}", c, -a)],
                     d, "FX_SETTLEMENT", t.trade_id, t.sandbox_id)
            _settled(s, ins, t, d, Decimal(0), Decimal(0))
            stats["settled"] += 1
            continue
        remaining = dec(ins.sec_qty) - dec(ins.settled_qty)
        cash_remaining = dec(ins.cash_amount) - dec(ins.settled_cash)
        inst = ref.instruments.get(ins.instrument_id) if ins.instrument_id else None
        if cash_remaining < 0:
            short = _cash_short(s, ref, acct, [(ins.currency, cash_remaining)], d)
            if short:
                _fail(s, ins, t, d, "INSUFFICIENT_CASH", short)
                stats["failed"] += 1
                continue
        if remaining < 0 and ins.instrument_id:
            have = engine.sec_balance(s, ins.account_id, ins.instrument_id, d)
            if have < -remaining:
                lot = _lot(inst)
                part = (have // lot) * lot if have > 0 else Decimal(0)
                if acct.partial_settlement_allowed and part > 0 and ins.status != "PARTIAL":
                    cash_part = q_ccy(dec(ins.cash_amount) * part / -dec(ins.sec_qty), ins.currency)
                    _post_dvp(s, ins, t, d, -part, cash_part, "P1")
                    ins.settled_qty = dec(ins.settled_qty) - part
                    ins.settled_cash = dec(ins.settled_cash) + cash_part
                    ins.status, ins.partial_count = "PARTIAL", ins.partial_count + 1
                    ins.fail_reason = "INSUFFICIENT_SECURITIES"
                    ins.first_fail_date = ins.first_fail_date or d
                    raise_exception(s, "PARTIAL_SETTLEMENT", "MEDIUM", "SETTLEMENTS",
                                    f"Partially settled {part} of {-dec(ins.sec_qty)}; residual pending", trade_id=t.trade_id,
                                    ref_id=ins.instruction_id, rule_id="R085", error_code="E_INSUFFICIENT_SECURITIES",
                                    sandbox_id=t.sandbox_id, on=d)
                    audit(s, "SETTLEMENT_PARTIAL", "instruction", ins.instruction_id, after={"qty": str(part), "cash": str(cash_part)},
                          actor=SYSTEM)
                    stats["partial"] += 1
                elif ins.status != "PARTIAL":
                    _fail(s, ins, t, d, "INSUFFICIENT_SECURITIES", f"available {have} < required {-remaining}")
                    stats["failed"] += 1
                continue
        _post_dvp(s, ins, t, d, remaining, cash_remaining, "FINAL")
        _settled(s, ins, t, d, remaining, cash_remaining)
        stats["settled"] += 1
    for tid in sorted(touched):
        rollup(s, s.get(m.Trade, tid), d)
    return stats


def _cash_short(s, ref, acct: m.Account, legs, d) -> str | None:
    for ccy, amt in legs:
        if amt >= 0:
            continue
        bal = engine.cash_balance(s, acct.account_id, ccy, d)
        credit = dec(acct.credit_line_usd) / ref.usd_per(ccy, d)
        if bal + credit < -amt:
            return f"cash {bal:.2f} + credit {credit:.2f} < {-amt:.2f} {ccy}"
    return None


def _post_dvp(s, ins: m.SettlementInstruction, t: m.Trade, d: date, qty: Decimal, cash: Decimal, tag: str):
    if qty and ins.instrument_id:
        post(s, f"SETTLE:{ins.instruction_id}:{tag}:SEC", "SEC", [(ins.account_id, ins.instrument_id, qty),
                                                                (f"CSD:{ins.settlement_location}", ins.instrument_id, -qty)],
             d, ins.leg, t.trade_id, t.sandbox_id)
    if cash:
        post(s, f"SETTLE:{ins.instruction_id}:{tag}:CASH", "CASH", [(ins.account_id, ins.currency, cash),
                                                                  (f"CPTY:{t.counterparty_id}", ins.currency, -cash)],
             d, ins.leg, t.trade_id, t.sandbox_id)


def _settled(s, ins, t, d, qty, cash):
    ins.settled_qty = dec(ins.settled_qty) + qty
    ins.settled_cash = dec(ins.settled_cash) + cash
    ins.status, ins.settled_on = "SETTLED", d
    for et in ("SETTLEMENT_FAIL", "PARTIAL_SETTLEMENT"):
        resolve_exception(s, et, ins.instruction_id, f"settled {d}", d)
    s.add(m.InterfaceMessage(message_id=f"MSG-IN-{ins.instruction_id}-{d}", system="CUSTODIAN" if ins.leg != "FX" else "PAYMENT",
                             direction="IN", message_type="SETTLEMENT_CONFIRMATION", correlation_id=t.trade_id,
                             ref_id=ins.instruction_id, payload={"status": "SETTLED", "date": d.isoformat()}, status="PROCESSED",
                             business_date=d, sandbox_id=t.sandbox_id))
    audit(s, "SETTLEMENT_SETTLED", "instruction", ins.instruction_id, after={"date": d.isoformat()}, actor=SYSTEM)
    s.flush()


def _fail(s, ins, t, d, reason, message):
    first = ins.status != "FAILED"
    ins.status, ins.fail_reason = "FAILED", reason
    ins.first_fail_date = ins.first_fail_date or d
    if first:
        raise_exception(s, "SETTLEMENT_FAIL", "HIGH", "SETTLEMENTS", f"{reason}: {message}", trade_id=t.trade_id,
                        ref_id=ins.instruction_id, rule_id="R083" if "SECURITIES" in reason else "R084",
                        error_code="E_" + reason, sandbox_id=t.sandbox_id, on=d)
        audit(s, "SETTLEMENT_FAILED", "instruction", ins.instruction_id, after={"reason": reason}, actor=SYSTEM)


def rollup(s: Session, t: m.Trade, d: date):
    ins = list(s.scalars(select(m.SettlementInstruction).where(m.SettlementInstruction.trade_id == t.trade_id,
                                                                m.SettlementInstruction.status != "CANCELLED")))
    if t.product_type in ("REPO", "REVERSE_REPO", "SEC_LOAN", "SEC_BORROW"):
        start = [i for i in ins if i.leg in ("REPO_START", "SL_START")]
        end = [i for i in ins if i.leg in ("REPO_END", "SL_END", "SL_FEE")]
        if t.status in ("INSTRUCTED", "SETTLEMENT_FAILED", "PARTIALLY_SETTLED"):
            if all(i.status == "SETTLED" for i in start):
                booking.transition(s, t, "OPEN", SYSTEM, "start leg settled")
            elif any(i.status == "FAILED" for i in start) and t.status != "SETTLEMENT_FAILED":
                booking.transition(s, t, "SETTLEMENT_FAILED", SYSTEM, "start leg failed")
        if t.status == "OPEN" and end and all(i.status == "SETTLED" for i in end):
            booking.transition(s, t, "CLOSED", SYSTEM, "end leg settled")
        return
    if t.product_type == "OPTION":
        if t.status in ("INSTRUCTED", "SETTLEMENT_FAILED") and all(i.status == "SETTLED" for i in ins):
            booking.transition(s, t, "OPEN", SYSTEM, "premium settled")
            ccp = ccp_for(t, refcache.get(s).instruments[t.instrument_id])
            for a in _allocs(s, t):
                sign = 1 if t.side == "BUY" else -1
                post(s, f"OPTOPEN:{a.allocation_id}", "SEC", [(a.account_id, t.instrument_id, sign * dec(a.alloc_qty)),
                                                              (f"CCP:{ccp}", t.instrument_id, -sign * dec(a.alloc_qty))],
                     t.settlement_date, "OPTION_OPEN", t.trade_id, t.sandbox_id)
        return
    states = {i.status for i in ins}
    target = None
    if states == {"SETTLED"}:
        target = "SETTLED"
    elif "PARTIAL" in states or ("SETTLED" in states and states - {"SETTLED"}):
        target = "PARTIALLY_SETTLED"
    elif "FAILED" in states:
        target = "SETTLEMENT_FAILED"
    if target and target != t.status:
        booking.transition(s, t, target, SYSTEM, f"settlement roll-up {d}")


# ------------------------------------------------------------------------------------- lifecycle events
def _event(s, eid, **kw) -> bool:
    if s.get(m.LifecycleEvent, eid) is not None:
        return False
    s.add(m.LifecycleEvent(event_id=eid, **kw))
    return True


def step_derivatives(s: Session, d: date, sandbox_id=None):
    ref = refcache.get(s)
    futs = s.scalars(_scope(select(m.Trade).where(m.Trade.product_type == "FUTURE", m.Trade.status == "OPEN",
                                                  m.Trade.trade_date <= d), m.Trade, sandbox_id)).all()
    for t in futs:
        inst = ref.instruments[t.instrument_id]
        ltd = engine._d(inst.attrs["last_trading_day"])
        if d > ltd:
            continue
        cal = ref.markets[inst.market].calendar_code
        px = ref.price_on(t.instrument_id, d)
        mult = inst.attrs["contract_multiplier"]
        if ref.cals.is_open(d, cal) and px is not None:
            prev_p = ref.price_before(t.instrument_id, d)
            prev = prev_p[1] if prev_p and prev_p[0] >= t.trade_date else t.price
            for a in _allocs(s, t):
                vm = calc.futures_vm(t.side, a.alloc_qty, prev, px, mult, t.settle_ccy)
                if _event(s, f"VM:{a.allocation_id}:{d}", trade_id=t.trade_id, allocation_id=a.allocation_id,
                          account_id=a.account_id, event_type="VARIATION_MARGIN", event_date=d, amount=vm, currency=t.settle_ccy,
                          status="SETTLED", details={"settle_price": str(px), "previous": str(prev)}, sandbox_id=t.sandbox_id) and vm:
                    post(s, f"VM:{a.allocation_id}:{d}", "CASH", [(a.account_id, t.settle_ccy, vm),
                                                                   (f"CCP:{inst.attrs['ccp_id']}", t.settle_ccy, -vm)],
                         d, "VARIATION_MARGIN", t.trade_id, t.sandbox_id)
        if d == ltd:
            for a in _allocs(s, t):
                sign = 1 if t.side == "BUY" else -1
                post(s, f"FUTEXP:{a.allocation_id}", "SEC", [(a.account_id, t.instrument_id, -sign * dec(a.alloc_qty)),
                                                             (f"CCP:{inst.attrs['ccp_id']}", t.instrument_id, sign * dec(a.alloc_qty))],
                     d, "FUTURE_EXPIRY", t.trade_id, t.sandbox_id)
                _event(s, f"EXPIRY:{a.allocation_id}", trade_id=t.trade_id, allocation_id=a.allocation_id, account_id=a.account_id,
                       event_type="FUTURE_EXPIRY", event_date=d, amount=Decimal(0), currency=t.settle_ccy, status="DONE",
                       details={}, sandbox_id=t.sandbox_id)
            booking.transition(s, t, "EXPIRED", SYSTEM, "last trading day; final variation margin paid")
    opts = s.scalars(_scope(select(m.Trade).where(m.Trade.product_type == "OPTION", m.Trade.status == "OPEN"), m.Trade, sandbox_id)).all()
    for t in opts:
        inst = ref.instruments[t.instrument_id]
        exp = engine._d(inst.attrs["last_trading_day"])
        if d != exp:
            continue
        a_ = inst.attrs
        und_px = ref.price_on(a_["underlying_id"], exp)
        itm, intrinsic = calc.option_in_the_money(a_["option_type"], a_["strike"], und_px)
        stl = ref.cals.add(exp, 1, ref.markets[inst.market].calendar_code, CCY_CALENDAR[t.settle_ccy])
        mult = dec(a_["contract_multiplier"])
        sign = 1 if t.side == "BUY" else -1
        for a in _allocs(s, t):
            post(s, f"OPTEXP:{a.allocation_id}", "SEC", [(a.account_id, t.instrument_id, -sign * dec(a.alloc_qty)),
                                                         (f"CCP:{a_['ccp_id']}", t.instrument_id, sign * dec(a.alloc_qty))],
                 exp, "OPTION_EXPIRY", t.trade_id, t.sandbox_id)
            if not itm:
                _event(s, f"OPTX:{a.allocation_id}", trade_id=t.trade_id, allocation_id=a.allocation_id, account_id=a.account_id,
                       event_type="OPTION_LAPSED", event_date=exp, amount=Decimal(0), currency=t.settle_ccy, status="DONE",
                       details={"underlying_price": str(und_px)}, sandbox_id=t.sandbox_id)
                continue
            if a_["settlement_type"] == "CASH":
                amt = q_ccy(intrinsic * dec(a.alloc_qty) * mult, t.settle_ccy)
                post(s, f"OPTCASH:{a.allocation_id}", "CASH", [(a.account_id, t.settle_ccy, sign * amt),
                                                               (f"CCP:{a_['ccp_id']}", t.settle_ccy, -sign * amt)],
                     stl, "OPTION_CASH_SETTLEMENT", t.trade_id, t.sandbox_id)
                _event(s, f"OPTX:{a.allocation_id}", trade_id=t.trade_id, allocation_id=a.allocation_id, account_id=a.account_id,
                       event_type="EXERCISE_CASH", event_date=exp, amount=sign * amt, currency=t.settle_ccy, status="SCHEDULED",
                       details={"underlying_price": str(und_px), "settles": stl.isoformat()}, sandbox_id=t.sandbox_id)
            else:
                shares = dec(a.alloc_qty) * mult
                strike_amt = q_ccy(shares * dec(a_["strike"]), t.settle_ccy)
                receive = (a_["option_type"] == "CALL") == (sign == 1)
                und = a_["underlying_id"]
                post(s, f"OPTPHYS:{a.allocation_id}:SEC", "SEC", [(a.account_id, und, shares if receive else -shares),
                                                                  (f"CCP:{a_['ccp_id']}", und, -shares if receive else shares)],
                     stl, "OPTION_PHYSICAL", t.trade_id, t.sandbox_id)
                post(s, f"OPTPHYS:{a.allocation_id}:CASH", "CASH", [(a.account_id, t.settle_ccy, -strike_amt if receive else strike_amt),
                                                                    (f"CCP:{a_['ccp_id']}", t.settle_ccy, strike_amt if receive else -strike_amt)],
                     stl, "OPTION_STRIKE", t.trade_id, t.sandbox_id)
                _event(s, f"OPTX:{a.allocation_id}", trade_id=t.trade_id, allocation_id=a.allocation_id, account_id=a.account_id,
                       event_type="EXERCISE_PHYSICAL", event_date=exp, amount=strike_amt, currency=t.settle_ccy, status="SCHEDULED",
                       details={"underlying_price": str(und_px), "shares": str(shares), "receive": receive, "settles": stl.isoformat()},
                       sandbox_id=t.sandbox_id)
        booking.transition(s, t, "EXERCISED" if itm else "EXPIRED", SYSTEM, f"expiry: underlying {und_px}")


def step_swaps(s: Session, d: date, sandbox_id=None):
    ref = refcache.get(s)
    swaps = s.scalars(_scope(select(m.Trade).where(m.Trade.product_type == "IRS", m.Trade.status == "OPEN"), m.Trade, sandbox_id)).all()
    for t in swaps:
        inst = ref.instruments[t.instrument_id]
        cal = inst.attrs["payment_calendar"]
        sched = t.economics.get("schedule", {})
        for start, end, pay in sched.get("float", []):
            start, end, pay = date.fromisoformat(start), date.fromisoformat(end), date.fromisoformat(pay)
            fix_date = t.trade_date if start == t.settlement_date else ref.cals.add(start - timedelta(days=1), 0, cal)
            if start != t.settlement_date:
                fix_date = ref.cals.preceding(start - timedelta(days=1), cal)
                fix_date = ref.cals.preceding(fix_date - timedelta(days=1), cal)
            if fix_date == d:
                fixing = ref.fixings.get((inst.attrs["float_index"], d))
                if fixing is None:
                    raise_exception(s, "FIXING_MISSING", "HIGH", "MIDDLE_OFFICE", f"No fixing for {inst.attrs['float_index']} on {d}",
                                    trade_id=t.trade_id, ref_id=f"{t.trade_id}:{start}", rule_id="R061", error_code="E_FIXING_MISSING",
                                    sandbox_id=sandbox_id, on=d)
                    continue
                amt = calc.swap_float(t.quantity, fixing, t.terms.get("irs_spread_bps"), start, end, t.settle_ccy)
                _event(s, f"RESET:{t.trade_id}:{start}", trade_id=t.trade_id, event_type="RATE_RESET", event_date=d, amount=amt,
                       currency=t.settle_ccy, status="DONE", details={"fixing_pct": str(fixing), "period": [str(start), str(end)],
                                                                      "pay": str(pay)}, sandbox_id=t.sandbox_id)
        if d >= t.end_date:
            booking.transition(s, t, "MATURED", SYSTEM, "final payment date passed")


def step_income(s: Session, d: date, sandbox_id=None):
    """Dividends and coupons on pay date for positions held at record date (main book only)."""
    if sandbox_id:
        return
    cas = s.scalars(select(m.CorporateAction).where(m.CorporateAction.pay_date == d)).all()
    for ca in cas:
        rows = s.execute(select(m.LedgerEntry.ledger_account, func.sum(m.LedgerEntry.amount)).where(
            m.LedgerEntry.ledger == "SEC", m.LedgerEntry.key == ca.instrument_id, m.LedgerEntry.value_date <= ca.record_date,
            m.LedgerEntry.sandbox_id.is_(None), ~m.LedgerEntry.ledger_account.contains(":"))
            .group_by(m.LedgerEntry.ledger_account).order_by(m.LedgerEntry.ledger_account)).all()
        for acct, qty in rows:
            qty = dec(qty)
            if qty <= 0:
                continue
            per = dec(ca.rate) if ca.event_type == "CASH_DIVIDEND" else dec(ca.rate) / 100
            amt = q_ccy(qty * per, ca.currency)
            if _event(s, f"{ca.ca_id}:{acct}", trade_id=None, account_id=acct,
                      event_type="DIVIDEND" if ca.event_type == "CASH_DIVIDEND" else "COUPON", event_date=d, amount=amt,
                      currency=ca.currency, status="SETTLED", details={"ca_id": ca.ca_id, "qty": str(qty),
                                                                       "record_date": ca.record_date.isoformat()}):
                post(s, f"INCOME:{ca.ca_id}:{acct}", "CASH", [(acct, ca.currency, amt), (f"PAYAGENT:{ca.currency}", ca.currency, -amt)],
                     d, ca.event_type, None, None)
        ca.processed = True


def step_repo_margin(s: Session, d: date, sandbox_id=None):
    ref = refcache.get(s)
    mta = dec(ref.param("REPO_MTA", d, default="250000"))
    repos = s.scalars(_scope(select(m.Trade).where(m.Trade.product_type.in_(("REPO", "REVERSE_REPO")), m.Trade.status == "OPEN"),
                             m.Trade, sandbox_id)).all()
    for t in repos:
        inst = ref.instruments[t.instrument_id]
        if not ref.cals.is_open(d, CCY_CALENDAR[t.settle_ccy]):
            continue
        p = ref.price_on_or_before(t.instrument_id, d)
        if p is None:
            continue
        ai = calc.accrued_per_100(inst.attrs | {"issue_date": engine._d(inst.attrs["issue_date"]),
                                                "maturity_date": engine._d(inst.attrs["maturity_date"])}, d, ref.cals, t.settle_ccy)
        mv = dec(t.quantity) * (p[1] + ai) / 100
        call = calc.repo_margin_call(t.economics["cash_amount"], mv, t.terms.get("haircut_pct", 0), mta, t.settle_ccy)
        if call > 0:
            raise_exception(s, "MARGIN_CALL", "MEDIUM", "RISK", f"Collateral deficit {call} {t.settle_ccy} exceeds MTA {mta}",
                            trade_id=t.trade_id, ref_id=f"{t.trade_id}:{d}", rule_id="R072", error_code="MARGIN_CALL",
                            sandbox_id=sandbox_id, on=d)


# ------------------------------------------------------------------------------------- orchestration
def process_day(s: Session, d: date, sandbox_id=None) -> dict:
    started = datetime.utcnow()
    token = processing_date.set(d)
    try:
        stats = {"matched": step_matching(s, d, sandbox_id)}
        s.flush()
        stats["cleared"] = step_clearing(s, d, sandbox_id)
        s.flush()
        stats["instructed"] = step_instruct(s, d, sandbox_id)
        s.flush()
        stats.update(step_settlement(s, d, sandbox_id))
        s.flush()
        for step in (step_derivatives, step_swaps, step_income, step_repo_margin):
            step(s, d, sandbox_id)
            s.flush()
    finally:
        processing_date.reset(token)
    run_id = f"EOD-{sandbox_id or 'MAIN'}-{d}"
    pr = s.get(m.ProcessingRun, run_id)
    if pr is None:
        pr = m.ProcessingRun(run_id=run_id, run_type="EOD", business_date=d, started_at=started, status="RUNNING",
                             sandbox_id=sandbox_id)
        s.add(pr)
    pr.finished_at = datetime.utcnow()
    pr.status = "COMPLETED"
    pr.stats = {k: int(v) for k, v in stats.items()}
    s.flush()
    return stats


def run_through(s: Session, target: date, start: date | None = None, sandbox_id=None, commit_each_day=True) -> list[dict]:
    clock = s.get(m.SystemClock, 1)
    if start is None:
        if sandbox_id is None and clock and clock.last_processed:
            start = clock.last_processed + timedelta(days=1)
        else:
            q = _scope(select(func.min(m.Trade.trade_date)), m.Trade, sandbox_id)
            start = s.scalar(q) or target
    out = []
    d = start
    while d <= target:
        out.append({"date": d.isoformat(), **process_day(s, d, sandbox_id)})
        if sandbox_id is None and clock:
            clock.last_processed = d
        if commit_each_day:
            s.commit()
        d += timedelta(days=1)
    return out
