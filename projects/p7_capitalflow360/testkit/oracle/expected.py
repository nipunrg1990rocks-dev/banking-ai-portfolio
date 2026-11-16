"""Independent expected-outcome projection for the baseline dataset.

Given the input tables (as lists of dicts), compute:

* per-trade and per-allocation expected economics;
* the expected matching outcome of each trade against the counterparty confirmation;
* every dated cash and securities movement that should have settled by the statement date;
* expected positions and cash balances per account at the statement date.

The convention table below is written out by hand from the cited sources rather than read from
the platform's configuration tables, so a wrong configuration row is caught.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

from testkit.oracle import reference as ref

CCY_CAL = {"USD": "USNY", "EUR": "EUTA", "GBP": "GBLO", "JPY": "JPTO", "CAD": "CATO", "AUD": "AUSY", "CHF": "CHZU"}
MARKET_CAL = {"XNYS": "XNYS", "XLON": "XLON", "XETR": "XETR", "XTKS": "XTKS", "XCME": "XCME", "XCBO": "XCBO",
              "XEUR": "XEUR"}
# Reviewed convention table (business-day lags), see docs/rule_catalogue.md R011.
EQUITY_LAG = {"XNYS": 1, "XLON": 2, "XETR": 2, "XTKS": 2}
BOND_LAG = {"USD": 1, "GBP": 1, "EUR": 2, "JPY": 1}
EXCHANGE_FEE_BPS = {"XNYS": Fraction(2, 10), "XLON": Fraction(1, 10), "XETR": Fraction(15, 100), "XTKS": Fraction(1, 10)}
UK_STAMP_DUTY_BPS = Fraction(50)
AUTO_EXERCISE_THRESHOLD = "0.01"
STATEMENT_DATE = date(2026, 11, 6)


@dataclass
class Movement:
    on: date
    account_id: str
    kind: str  # CASH or SEC
    key: str  # currency or instrument id
    amount: Fraction
    trade_id: str
    reason: str


@dataclass
class Expected:
    trades: dict = field(default_factory=dict)
    allocations: dict = field(default_factory=dict)
    matching: dict = field(default_factory=dict)
    movements: list = field(default_factory=list)
    settlements: dict = field(default_factory=dict)
    positions: dict = field(default_factory=dict)
    cash: dict = field(default_factory=dict)
    lifecycle: list = field(default_factory=list)


class Data:
    def __init__(self, t: dict[str, list[dict]]):
        self.t = t
        self.hol: dict[str, set] = defaultdict(set)
        for h in t["Holidays"]:
            self.hol[h["calendar_code"]].add(h["holiday_date"])
        self.inst = {r["instrument_id"]: r for r in t["Instruments"]}
        self.eq = {r["instrument_id"]: r for r in t["Equity_Details"]}
        self.bond = {r["instrument_id"]: r for r in t["Bond_Details"]}
        self.fxd = {r["instrument_id"]: r for r in t["FX_Details"]}
        self.der = {r["instrument_id"]: r for r in t["Derivative_Details"]}
        self.swap = {r["instrument_id"]: r for r in t["Swap_Templates"]}
        self.acc = {r["account_id"]: r for r in t["Accounts"]}
        self.px = {(r["instrument_id"], r["price_date"]): r["close_price"] for r in t["Prices"]}
        self.fx = {(r["currency"], r["rate_date"]): r["usd_per_unit"] for r in t["FX_Rates"]}
        self.fix = {(r["rate_index"], r["fixing_date"]): r["rate_pct"] for r in t["Rate_Fixings"]}
        self.allocs = defaultdict(list)
        for a in t["Allocations"]:
            self.allocs[a["trade_id"]].append(a)
        for v in self.allocs.values():
            v.sort(key=lambda a: a["alloc_seq"])
        self.sched = defaultdict(list)
        for s in t["Commission_Schedules"]:
            self.sched[(s["account_id"], s["product_group"])].append(s)

    def cals(self, *codes):
        return [self.hol[c] for c in codes]

    def usd_per(self, ccy: str, d: date):
        cur = d
        for _ in range(10):
            if (ccy, cur) in self.fx:
                return self.fx[(ccy, cur)]
            cur -= timedelta(days=1)
        raise KeyError((ccy, d))

    def schedule(self, account_id, group, on: date):
        for s in self.sched[(account_id, group)]:
            if s["effective_from"] <= on and (s["effective_to"] is None or on <= s["effective_to"]):
                return s
        raise KeyError((account_id, group, on))


def expected_settlement_date(D: Data, tr: dict) -> date:
    ptype, td, ccy = tr["product_type"], tr["trade_date"], tr["settle_ccy"]
    inst = D.inst[tr["instrument_id"]]
    if ptype in ("EQUITY", "ETF"):
        m = inst["market"]
        return ref.add_business_days(td, EQUITY_LAG[m], D.cals(MARKET_CAL[m], CCY_CAL[ccy]))
    if ptype in ("GOVT_BOND", "CORP_BOND"):
        return ref.add_business_days(td, BOND_LAG[ccy], D.cals(CCY_CAL[ccy]))
    if ptype == "FX_SPOT":
        f = D.fxd[tr["instrument_id"]]
        return ref.fx_spot_date(td, f["base_ccy"], f["quote_ccy"], {c: D.hol[k] for c, k in CCY_CAL.items()})
    if ptype == "FX_FORWARD":
        f = D.fxd[tr["instrument_id"]]
        return ref.fx_forward_date(td, f["base_ccy"], f["quote_ccy"], int(tr["fx_tenor_months"]),
                                   {c: D.hol[k] for c, k in CCY_CAL.items()})
    if ptype == "FUTURE":
        return td
    if ptype == "OPTION":
        return ref.add_business_days(td, 1, D.cals(MARKET_CAL[inst["market"]], CCY_CAL[ccy]))
    if ptype == "IRS":
        return ref.add_business_days(td, 2, D.cals(D.swap[tr["instrument_id"]]["payment_calendar"]))
    if ptype in ("REPO", "REVERSE_REPO"):
        return tr["settlement_date"]  # as agreed; validated as a business day elsewhere
    if ptype in ("SEC_LOAN", "SEC_BORROW"):
        m = inst["market"]
        return ref.add_business_days(td, EQUITY_LAG[m], D.cals(MARKET_CAL[m], CCY_CAL[ccy]))
    raise ValueError(ptype)


def trade_economics(D: Data, tr: dict) -> dict:
    ptype, ccy = tr["product_type"], tr["settle_ccy"]
    sd = expected_settlement_date(D, tr)
    out = {"trade_id": tr["trade_id"], "settlement_date": sd, "settle_ccy": ccy}
    if ptype in ("EQUITY", "ETF"):
        inst = D.inst[tr["instrument_id"]]
        s = D.schedule(tr["account_id"], "EQUITY", tr["trade_date"])
        min_ccy = ref.to_frac(s["min_commission_usd"]) / ref.to_frac(D.usd_per(ccy, tr["trade_date"]))
        stamp = UK_STAMP_DUTY_BPS if inst["market"] == "XLON" and D.eq[tr["instrument_id"]]["stamp_duty_exempt"] == "N" else 0
        ch = ref.equity_charges(tr["side"], tr["quantity"], tr["price"], 1, ccy, s["commission_bps"], min_ccy,
                                EXCHANGE_FEE_BPS[inst["market"]], stamp)
        out.update(ch)
        out["settle_amount"] = ch["net"]
    elif ptype in ("GOVT_BOND", "CORP_BOND"):
        b = D.bond[tr["instrument_id"]]
        ai = ref.accrued_interest_per_100(sd, b["coupon_rate_pct"], int(b["coupon_frequency"]), b["day_count"],
                                          b["maturity_date"], b["issue_date"], int(b["ex_dividend_bdays"] or 0),
                                          D.hol[CCY_CAL[ccy]])
        c = ref.bond_consideration(tr["quantity"], tr["price"], ai, ccy)
        out.update({"principal": c["principal"], "accrued": c["accrued"], "gross": c["gross"],
                    "dirty_price": c["dirty_price"], "accrued_per_100": ref.round_dp(ai, 8), "net": c["gross"],
                    "settle_amount": c["gross"]})
    elif ptype in ("FX_SPOT", "FX_FORWARD"):
        f = D.fxd[tr["instrument_id"]]
        counter = ref.fx_counter_amount(tr["quantity"], tr["price"], f["quote_ccy"])
        out.update({"base_amount": ref.round_ccy(tr["quantity"], f["base_ccy"]), "counter_amount": counter,
                    "counter_ccy": f["quote_ccy"], "settle_amount": ref.round_ccy(tr["quantity"], f["base_ccy"]),
                    "net": ref.round_ccy(tr["quantity"], f["base_ccy"])})
        if ptype == "FX_FORWARD":
            out["forward_rate_check"] = ref.fx_forward_rate(tr["fx_spot_rate"], tr["fx_forward_points"], f["quote_ccy"])
    elif ptype == "FUTURE":
        d = D.der[tr["instrument_id"]]
        out.update({"notional": ref.round_ccy(ref.to_frac(tr["quantity"]) * ref.to_frac(tr["price"]) *
                                              d["contract_multiplier"], ccy),
                    "initial_margin": ref.round_ccy(ref.to_frac(tr["quantity"]) * d["initial_margin_per_contract"], ccy),
                    "settle_amount": Decimal(0), "net": Decimal(0)})
    elif ptype == "OPTION":
        d = D.der[tr["instrument_id"]]
        prem = ref.option_premium(tr["quantity"], tr["price"], d["contract_multiplier"], ccy)
        out.update({"premium": prem, "settle_amount": prem, "net": prem})
    elif ptype == "IRS":
        s = D.swap[tr["instrument_id"]]
        eff = sd
        mat = ref.add_months_raw(eff, 12 * int(tr["irs_tenor_years"]))
        hs = D.cals(s["payment_calendar"])
        fixed = ref.swap_schedule(eff, mat, int(s["fixed_freq_months"]), hs)
        flt = ref.swap_schedule(eff, mat, int(s["float_freq_months"]), hs)
        fixing = D.fix[(s["float_index"], tr["trade_date"])]
        out.update({"effective_date": eff, "maturity_date": mat, "fixed_periods": len(fixed),
                    "float_periods": len(flt),
                    "first_fixed_amount": ref.swap_fixed_amount(tr["quantity"], tr["price"], fixed[0][0], fixed[0][1], ccy),
                    "first_float_amount": ref.swap_float_amount(tr["quantity"], fixing, tr["irs_spread_bps"] or 0,
                                                                flt[0][0], flt[0][1], ccy),
                    "first_float_fixing_pct": fixing, "first_fixed_payment_date": fixed[0][2],
                    "first_float_payment_date": flt[0][2], "settle_amount": Decimal(0), "net": Decimal(0)})
    elif ptype in ("REPO", "REVERSE_REPO"):
        cash = ref.repo_cash_amount(tr["quantity"], tr["price"], tr["haircut_pct"], ccy)
        interest = ref.repo_interest(cash, tr["repo_rate_pct"], tr["settlement_date"], tr["end_date"], ccy)
        out.update({"cash_amount": cash, "repo_interest": interest, "repurchase_amount": cash + interest,
                    "settle_amount": cash, "net": cash})
    elif ptype in ("SEC_LOAN", "SEC_BORROW"):
        coll_ccy = tr["collateral_ccy"]
        same = coll_ccy == ccy
        if same:
            coll = ref.secloan_collateral_required(tr["quantity"], tr["price"], True, ccy)
        else:
            px_in_coll = ref.to_frac(tr["price"]) * ref.to_frac(D.usd_per(ccy, tr["trade_date"])) / ref.to_frac(
                D.usd_per(coll_ccy, tr["trade_date"]))
            coll = ref.secloan_collateral_required(tr["quantity"], px_in_coll, False, coll_ccy)
        fee = ref.secloan_fee(tr["quantity"], tr["price"], tr["sl_fee_bps"], sd, tr["end_date"], ccy)
        out.update({"collateral_amount": coll, "collateral_ccy": coll_ccy, "loan_fee": fee,
                    "loan_value": ref.round_ccy(ref.to_frac(tr["quantity"]) * ref.to_frac(tr["price"]), ccy),
                    "settle_amount": coll, "net": coll})
    return out


def allocation_amounts(D: Data, tr: dict, econ: dict) -> list[dict]:
    allocs = D.allocs[tr["trade_id"]]
    qtys = [a["alloc_qty"] for a in allocs]
    amounts = ref.allocate(econ["settle_amount"], qtys, tr["quantity"], econ["settle_ccy"]) if econ["settle_amount"] else [Decimal(0)] * len(qtys)
    out = []
    for a, amt in zip(allocs, amounts, strict=True):
        out.append({"allocation_id": a["allocation_id"], "trade_id": tr["trade_id"], "account_id": a["account_id"],
                    "alloc_qty": a["alloc_qty"], "settle_amount": amt})
    return out


def match_confirmation(econ: dict, tr: dict, conf: dict | None) -> str:
    """MATCHED, MATCHED_WITHIN_TOLERANCE, UNMATCHED_MISSING, MISMATCH_<FIELD>."""
    if tr["product_type"] in ("FUTURE", "OPTION"):
        return "MATCHED"  # exchange-matched, confirmed by the CCP feed
    if conf is None:
        return "UNMATCHED_MISSING"
    if Fraction(str(conf["quantity"])) != Fraction(str(tr["quantity"])):
        return "MISMATCH_QUANTITY"
    if Fraction(str(conf["price"])) != Fraction(str(tr["price"])):
        return "MISMATCH_PRICE"
    if conf["settle_date"] != econ["settlement_date"]:
        return "MISMATCH_SETTLE_DATE"
    if econ["settle_amount"] and conf["settle_amount"] is not None:
        if not ref.within_amount_tolerance(econ["settle_amount"], conf["settle_amount"], econ["settle_ccy"]):
            return "MISMATCH_AMOUNT"
        if Fraction(str(conf["settle_amount"])) != ref.to_frac(econ["settle_amount"]):
            return "MATCHED_WITHIN_TOLERANCE"
    return "MATCHED"


def project(tables: dict[str, list[dict]], statement_date: date = STATEMENT_DATE) -> Expected:
    D = Data(tables)
    E = Expected()
    confs = {c["trade_id"]: c for c in tables.get("Confirmations", [])}
    outcomes = {s["allocation_id"]: s for s in tables.get("Settlement_Status", [])}
    pending: list[dict] = []  # settlement obligations (DvP / FoP / cash)

    for tr in tables["Trades"]:
        econ = trade_economics(D, tr)
        E.trades[tr["trade_id"]] = econ
        allocs = allocation_amounts(D, tr, econ)
        for a in allocs:
            E.allocations[a["allocation_id"]] = a
        m = match_confirmation(econ, tr, confs.get(tr["trade_id"]))
        E.matching[tr["trade_id"]] = m
        if not m.startswith("MATCHED"):
            continue  # unmatched trades are not instructed and do not settle
        _obligations(D, tr, econ, allocs, pending, E, statement_date)

    _settle(D, pending, outcomes, E, statement_date)
    _income(D, tables, E, statement_date)
    pos, cash = defaultdict(Fraction), defaultdict(Fraction)
    for p in tables["Opening_Positions"]:
        pos[(p["account_id"], p["instrument_id"])] += ref.to_frac(p["quantity"])
    for c in tables["Opening_Cash"]:
        cash[(c["account_id"], c["currency"])] += ref.to_frac(c["balance"])
    for mv in E.movements:
        if mv.on > statement_date:
            continue
        if mv.kind == "SEC":
            pos[(mv.account_id, mv.key)] += mv.amount
        else:
            cash[(mv.account_id, mv.key)] += mv.amount
    E.positions = {k: v for k, v in pos.items()}
    E.cash = {k: v for k, v in cash.items()}
    return E


def _obligations(D: Data, tr, econ, allocs, pending, E: Expected, stmt: date):
    ptype, ccy, sd = tr["product_type"], econ["settle_ccy"], econ["settlement_date"]
    buy = tr["side"] in ("BUY",)
    for a in allocs:
        acct, q, amt = a["account_id"], ref.to_frac(a["alloc_qty"]), ref.to_frac(a["settle_amount"])
        base = {"allocation_id": a["allocation_id"], "trade_id": tr["trade_id"], "account_id": acct, "on": sd,
                "ccy": ccy, "instrument_id": tr["instrument_id"], "product_type": ptype}
        if ptype in ("EQUITY", "ETF", "GOVT_BOND", "CORP_BOND"):
            pending.append(dict(base, sec_qty=q if buy else -q, cash=-amt if buy else amt, leg="DVP", partial_ok=True))
        elif ptype in ("FX_SPOT", "FX_FORWARD"):
            f = D.fxd[tr["instrument_id"]]
            counter = ref.to_frac(econ["counter_amount"])
            sign = 1 if buy else -1
            pending.append(dict(base, sec_qty=0, cash=0, leg="FX", fx=[(f["base_ccy"], sign * q),
                                                                     (f["quote_ccy"], -sign * counter)]))
        elif ptype == "OPTION":
            pending.append(dict(base, sec_qty=0, cash=-amt if buy else amt, leg="PREMIUM", instrument_id=None))
            _option_expiry(D, tr, a, E, stmt)
        elif ptype == "FUTURE":
            _futures_vm(D, tr, a, E, stmt)
        elif ptype in ("REPO", "REVERSE_REPO"):
            repo = ptype == "REPO"
            cash = ref.to_frac(econ["cash_amount"])
            rep = ref.to_frac(econ["repurchase_amount"])
            pending.append(dict(base, sec_qty=-q if repo else q, cash=cash if repo else -cash, leg="REPO_START"))
            pending.append(dict(base, on=tr["end_date"], sec_qty=q if repo else -q, cash=-rep if repo else rep,
                                leg="REPO_END", allocation_id=a["allocation_id"] + "-E"))
        elif ptype in ("SEC_LOAN", "SEC_BORROW"):
            loan = ptype == "SEC_LOAN"
            coll = ref.to_frac(econ["collateral_amount"])
            fee = ref.to_frac(econ["loan_fee"])
            cc = econ["collateral_ccy"]
            pending.append(dict(base, ccy=cc, sec_qty=-q if loan else q, cash=coll if loan else -coll, leg="SL_START"))
            pending.append(dict(base, ccy=cc, on=tr["end_date"], sec_qty=q if loan else -q, cash=-coll if loan else coll,
                                leg="SL_END", allocation_id=a["allocation_id"] + "-E"))
            pending.append(dict(base, on=tr["end_date"], sec_qty=0, cash=fee if loan else -fee, leg="SL_FEE",
                                instrument_id=None, allocation_id=a["allocation_id"] + "-F"))


def _futures_vm(D: Data, tr, a, E: Expected, stmt: date):
    d = D.der[tr["instrument_id"]]
    ccy = tr["settle_ccy"]
    cal = D.hol[MARKET_CAL[D.inst[tr["instrument_id"]]["market"]]]
    prev = tr["price"]
    day = tr["trade_date"]
    last = min(d["last_trading_day"], stmt)
    E.movements.append(Movement(tr["trade_date"], a["account_id"], "SEC", tr["instrument_id"],
                                ref.to_frac(a["alloc_qty"]) * (1 if tr["side"] == "BUY" else -1), tr["trade_id"],
                                "FUTURE_OPEN"))
    while day <= last:
        if ref.is_business_day(day, cal) and (tr["instrument_id"], day) in D.px:
            sp = D.px[(tr["instrument_id"], day)]
            vm = ref.futures_variation_margin(tr["side"], a["alloc_qty"], prev, sp, d["contract_multiplier"], ccy)
            if vm:
                E.movements.append(Movement(day, a["account_id"], "CASH", ccy, ref.to_frac(vm), tr["trade_id"], "VM"))
            E.lifecycle.append({"trade_id": tr["trade_id"], "allocation_id": a["allocation_id"], "event": "VM",
                                "date": day, "amount": vm, "ccy": ccy})
            prev = sp
        day += timedelta(days=1)
    if d["last_trading_day"] <= stmt:
        E.movements.append(Movement(d["last_trading_day"], a["account_id"], "SEC", tr["instrument_id"],
                                    -ref.to_frac(a["alloc_qty"]) * (1 if tr["side"] == "BUY" else -1), tr["trade_id"],
                                    "FUTURE_EXPIRY"))
        E.lifecycle.append({"trade_id": tr["trade_id"], "allocation_id": a["allocation_id"], "event": "EXPIRY",
                            "date": d["last_trading_day"], "amount": Decimal(0), "ccy": ccy})


def _option_expiry(D: Data, tr, a, E: Expected, stmt: date):
    d = D.der[tr["instrument_id"]]
    ccy = tr["settle_ccy"]
    exp = d["last_trading_day"]
    sign = 1 if tr["side"] == "BUY" else -1
    q = ref.to_frac(a["alloc_qty"])
    if tr["settlement_date"] <= stmt:
        E.movements.append(Movement(tr["settlement_date"], a["account_id"], "SEC", tr["instrument_id"], sign * q,
                                    tr["trade_id"], "OPTION_OPEN"))
    if exp > stmt:
        return
    und_px = D.px[(d["underlying_id"], exp)]
    exercised = ref.option_exercise(d["option_type"], d["strike"], und_px, AUTO_EXERCISE_THRESHOLD)
    E.movements.append(Movement(exp, a["account_id"], "SEC", tr["instrument_id"], -sign * q, tr["trade_id"],
                                "OPTION_EXPIRY"))
    stl_day = ref.add_business_days(exp, 1, D.cals(MARKET_CAL[D.inst[tr["instrument_id"]]["market"]], CCY_CAL[ccy]))
    ev = {"trade_id": tr["trade_id"], "allocation_id": a["allocation_id"], "date": exp, "ccy": ccy,
          "underlying_price": und_px, "exercised": exercised}
    if not exercised:
        E.lifecycle.append(dict(ev, event="OPTION_LAPSED", amount=Decimal(0)))
        return
    mult = d["contract_multiplier"]
    if d["settlement_type"] == "CASH":
        amt = ref.option_cash_settlement(d["option_type"], d["strike"], und_px, a["alloc_qty"], mult, ccy)
        if stl_day <= stmt:
            E.movements.append(Movement(stl_day, a["account_id"], "CASH", ccy, sign * ref.to_frac(amt), tr["trade_id"],
                                        "OPTION_CASH_SETTLEMENT"))
        E.lifecycle.append(dict(ev, event="EXERCISE_CASH", amount=sign * amt))
    else:
        shares = q * mult
        strike_amt = ref.round_ccy(shares * ref.to_frac(d["strike"]), ccy)
        # long call / short put receive shares and pay strike; long put / short call the reverse
        receive = (d["option_type"] == "CALL") == (sign == 1)
        if stl_day <= stmt:
            E.movements.append(Movement(stl_day, a["account_id"], "SEC", d["underlying_id"],
                                        shares if receive else -shares, tr["trade_id"], "OPTION_PHYSICAL"))
            E.movements.append(Movement(stl_day, a["account_id"], "CASH", ccy,
                                        -ref.to_frac(strike_amt) if receive else ref.to_frac(strike_amt),
                                        tr["trade_id"], "OPTION_STRIKE"))
        E.lifecycle.append(dict(ev, event="EXERCISE_PHYSICAL", amount=strike_amt, shares=shares, receive=receive))


def _settle(D: Data, pending: list[dict], outcomes: dict, E: Expected, stmt: date):
    """Day-by-day settlement engine: receipts before deliveries, then by allocation id.
    Securities deliveries need available settled position; partial settlement only where the
    account allows it. Controlled custodian outcomes (FAILED until a date) are honoured."""
    avail = defaultdict(Fraction)
    for p in D.t["Opening_Positions"]:
        avail[(p["account_id"], p["instrument_id"])] += ref.to_frac(p["quantity"])
    # movements from futures/options already projected affect securities only for derivative ids
    open_items = sorted(pending, key=lambda x: (x["on"], x["allocation_id"]))
    day = min(x["on"] for x in open_items) if open_items else stmt
    state = {x["allocation_id"]: {"remaining": x["sec_qty"], "cash_remaining": x["cash"], "status": "PENDING",
                                  "settled_qty": Fraction(0), "settled_cash": Fraction(0), "obl": x}
             for x in open_items}
    # pre-existing positions movements from option/physical delivery can fund deliveries too
    sec_mv = sorted([m for m in E.movements if m.kind == "SEC"], key=lambda m: m.on)
    mv_i = 0
    while day <= stmt:
        while mv_i < len(sec_mv) and sec_mv[mv_i].on <= day:
            m = sec_mv[mv_i]
            avail[(m.account_id, m.key)] += m.amount
            mv_i += 1
        due = [s for s in state.values() if s["status"] in ("PENDING", "PARTIAL", "FAILED") and s["obl"]["on"] <= day]
        due.sort(key=lambda s: (0 if s["obl"]["sec_qty"] >= 0 else 1, s["obl"]["allocation_id"]))
        for s in due:
            o = s["obl"]
            oc = outcomes.get(o["allocation_id"])
            if oc and oc["status"] == "FAILED" and (oc["resolved_date"] is None or day < oc["resolved_date"]):
                s["status"] = "FAILED"
                s["fail_reason"] = oc["reason_code"]
                continue
            if o["leg"] == "FX":
                for c, amt in o["fx"]:
                    E.movements.append(Movement(day, o["account_id"], "CASH", c, amt, o["trade_id"], "FX_SETTLEMENT"))
                s.update(status="SETTLED", settled_on=day, remaining=0)
                continue
            q = s["remaining"]
            key = (o["account_id"], o["instrument_id"])
            if q < 0 and o["instrument_id"]:
                have = avail[key]
                if have < -q:
                    acc = D.acc[o["account_id"]]
                    lot = _lot(D, o["instrument_id"])
                    part = (have // lot) * lot if have > 0 else Fraction(0)
                    if acc["partial_settlement_allowed"] == "Y" and part > 0 and s["status"] != "PARTIAL":
                        frac = part / (-o["sec_qty"])
                        cash_part = ref.to_frac(ref.round_ccy(o["cash"] * frac, o["ccy"]))
                        avail[key] -= part
                        _post(E, day, o, -part, cash_part)
                        s["remaining"] = q + part
                        s["cash_remaining"] = o["cash"] - cash_part
                        s["settled_qty"] += part
                        s["settled_cash"] += cash_part
                        s["status"] = "PARTIAL"
                    elif s["status"] != "PARTIAL":
                        s["status"] = "FAILED"
                        s["fail_reason"] = "INSUFFICIENT_SECURITIES"
                    continue
                avail[key] += q
            elif q > 0 and o["instrument_id"]:
                avail[key] += q
            _post(E, day, o, q, s["cash_remaining"])
            s["settled_qty"] += q
            s["settled_cash"] += s["cash_remaining"]
            s.update(status="SETTLED", settled_on=day, remaining=0, cash_remaining=0)
        day += timedelta(days=1)
    for k, s in state.items():
        o = s["obl"]
        E.settlements[k] = {"allocation_id": k, "trade_id": o["trade_id"], "leg": o["leg"], "due": o["on"],
                            "status": s["status"] if o["on"] <= stmt else "NOT_DUE",
                            "settled_on": s.get("settled_on"), "settled_qty": s["settled_qty"],
                            "settled_cash": s["settled_cash"], "fail_reason": s.get("fail_reason")}


def _lot(D: Data, iid: str) -> Fraction:
    if iid in D.eq:
        return Fraction(int(D.eq[iid]["lot_size"]))
    if iid in D.bond:
        return Fraction(int(D.bond[iid]["min_increment"]))
    return Fraction(1)


def _post(E: Expected, day, o, sec_qty, cash):
    if sec_qty and o["instrument_id"]:
        E.movements.append(Movement(day, o["account_id"], "SEC", o["instrument_id"], sec_qty, o["trade_id"], o["leg"]))
    if cash:
        E.movements.append(Movement(day, o["account_id"], "CASH", o["ccy"], cash, o["trade_id"], o["leg"]))


def _income(D: Data, tables, E: Expected, stmt: date):
    """Dividends and coupons paid on settled positions at the record date (end of day)."""
    def position_on(acct, iid, d):
        q = Fraction(0)
        for p in tables["Opening_Positions"]:
            if p["account_id"] == acct and p["instrument_id"] == iid:
                q += ref.to_frac(p["quantity"])
        for m in E.movements:
            if m.kind == "SEC" and m.account_id == acct and m.key == iid and m.on <= d:
                q += m.amount
        return q

    holders = defaultdict(set)
    for p in tables["Opening_Positions"]:
        holders[p["instrument_id"]].add(p["account_id"])
    for m in E.movements:
        if m.kind == "SEC":
            holders[m.key].add(m.account_id)
    for dv in tables.get("Dividends", []):
        for acct in sorted(holders[dv["instrument_id"]]):
            q = position_on(acct, dv["instrument_id"], dv["record_date"])
            if q > 0:
                amt = ref.round_ccy(q * ref.to_frac(dv["gross_rate_per_share"]), dv["currency"])
                E.lifecycle.append({"event": "DIVIDEND", "ca_id": dv["ca_id"], "account_id": acct,
                                    "date": dv["pay_date"], "amount": amt, "ccy": dv["currency"], "qty": q})
                if dv["pay_date"] <= stmt:
                    E.movements.append(Movement(dv["pay_date"], acct, "CASH", dv["currency"], ref.to_frac(amt),
                                                dv["ca_id"], "DIVIDEND"))
    for cp in tables.get("Coupons", []):
        for acct in sorted(holders[cp["instrument_id"]]):
            q = position_on(acct, cp["instrument_id"], cp["record_date"])
            if q > 0:
                amt = ref.round_ccy(q * ref.to_frac(cp["amount_per_100"]) / 100, cp["currency"])
                E.lifecycle.append({"event": "COUPON", "ca_id": cp["ca_id"], "account_id": acct,
                                    "date": cp["pay_date"], "amount": amt, "ccy": cp["currency"], "qty": q})
                if cp["pay_date"] <= stmt:
                    E.movements.append(Movement(cp["pay_date"], acct, "CASH", cp["currency"], ref.to_frac(amt),
                                                cp["ca_id"], "COUPON"))
