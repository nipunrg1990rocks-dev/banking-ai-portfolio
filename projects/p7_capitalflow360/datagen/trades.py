"""Orders, executions (fills), the 500 baseline trades and their allocations.

The baseline is *valid by construction*: every trade passes the platform's booking rules
(eligibility, calendars, lot sizes, collars, SSIs). Realistic operational outcomes (confirmation
mismatches, settlement fails, reconciliation breaks) come from the simulated external files,
not from bad input. Negative variants are generated separately (``datagen/negative.py``).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from datagen.calendars import CCY_CALENDAR
from datagen.market import STALE_FROM, fx_pair_rate
from datagen.refdata import BOOKS, MARKET
from testkit.oracle import reference as ref

PERIOD_START = date(2026, 9, 14)
PERIOD_END = date(2026, 10, 30)
COUNTS = {"EQUITY": 140, "BOND": 120, "FX": 80, "LISTED_DERIV": 70, "IRS": 50, "FINANCING": 40}
TRADERS = {"EQUITY": ["trader1", "trader2"], "BOND": ["trader2"], "FX": ["trader1"], "LISTED_DERIV": ["trader1"],
           "IRS": ["trader2"], "FINANCING": ["trader2"]}
BROKERS = {"US": ["CP01", "CP06", "CP10"], "GB": ["CP02", "CP09"], "EU": ["CP05", "CP03"], "JP": ["CP04"],
           "FX": ["CP02", "CP03", "CP07", "CP10", "CP05"], "RATES": ["CP02", "CP03", "CP05", "CP09", "CP10"],
           "FIN": ["CP09", "CP02", "CP10", "CP03"]}
MARKET_REGION = {"XNYS": "US", "XLON": "GB", "XETR": "EU", "XTKS": "JP", "XCME": "US", "XCBO": "US",
                 "XEUR": "EU", "OTC-US": "US", "OTC-UK": "GB", "OTC-EU": "EU", "OTC-JP": "JP"}


class Ctx:
    def __init__(self, rng, inst, hol, prices, fx_table, fixings, fwd_points, clients, accounts):
        self.rng = rng
        self.inst = {r["instrument_id"]: r for r in inst["Instruments"]}
        self.eq = {r["instrument_id"]: r for r in inst["Equity_Details"]}
        self.bond = {r["instrument_id"]: r for r in inst["Bond_Details"]}
        self.fxd = {r["instrument_id"]: r for r in inst["FX_Details"]}
        self.der = {r["instrument_id"]: r for r in inst["Derivative_Details"]}
        self.swap = {r["instrument_id"]: r for r in inst["Swap_Templates"]}
        self.hol = hol
        self.px = {(r["instrument_id"], r["price_date"]): r["close_price"] for r in prices}
        self.fx = fx_table
        self.fix = fixings
        self.fwd = {(r["pair"], r["rate_date"], r["tenor_months"]): r["forward_points"] for r in fwd_points}
        self.clients = {c["client_id"]: c for c in clients}
        self.accounts = accounts
        self.acc_by_client: dict[str, list[dict]] = {}
        for a in accounts:
            self.acc_by_client.setdefault(a["client_id"], []).append(a)
        self.n_trade = 0
        self.n_order = 0
        self.n_exec = 0
        self.n_alloc = 0
        self.orders, self.execs, self.trades, self.allocs = [], [], [], []

    def bdays(self, cals: list[str]) -> list[date]:
        out = []
        d = PERIOD_START
        while d <= PERIOD_END:
            if all(ref.is_business_day(d, self.hol[c]) for c in cals):
                out.append(d)
            d += timedelta(days=1)
        return out

    def eligible_clients(self, group: str, on: date) -> list[dict]:
        out = []
        for c in self.clients.values():
            if c["status"] != "ACTIVE" or c["kyc_expiry_date"] < on:
                continue
            if group in ("LISTED_DERIV", "IRS", "FINANCING") and c["client_category"] == "RETAIL":
                continue
            blocks = [a for a in self.acc_by_client[c["client_id"]] if group in a["permitted_products"].split(",")]
            if blocks:
                out.append(c)
        return out


def _utc(d: date, hh: int, mm: int, tz: str) -> datetime:
    return datetime.combine(d, time(hh, mm), ZoneInfo(tz)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _split(rng, total: int, parts: int, unit: int) -> list[int]:
    units = total // unit
    if parts <= 1 or units < parts:
        return [total]
    cuts = sorted(rng.sample(range(1, units), parts - 1))
    sizes = [b - a for a, b in zip([0] + cuts, cuts + [units], strict=True)]
    return [s * unit for s in sizes]


def _new_order(ctx: Ctx, c, acct, inst_id, ptype, side, qty, limit, ts, trader, ccy):
    ctx.n_order += 1
    oid = f"ORD{ctx.n_order:05d}"
    ctx.orders.append({"order_id": oid, "client_id": c["client_id"], "account_id": acct["account_id"],
                       "instrument_id": inst_id, "product_type": ptype, "side": side,
                       "order_type": "LIMIT" if limit else "MARKET", "limit_price": limit, "order_qty": qty,
                       "order_ccy": ccy, "trader_id": trader, "order_ts_utc": ts, "status": "FILLED",
                       "channel": ctx.rng.choice(["FIX", "FIX", "VOICE", "PORTAL"]), "source_system": "OMS-SIM",
                       "source_record_id": f"OMS-{100000 + ctx.n_order}"})
    return ctx.orders[-1]


def _fills(ctx: Ctx, order, qty: int, unit: int, px: float, market: str, cpty: str, ts: datetime, dp: int,
           max_fills: int = 4):
    n = ctx.rng.randint(1, max_fills)
    sizes = _split(ctx.rng, qty, n, unit)
    fills = []
    for k, s in enumerate(sizes):
        ctx.n_exec += 1
        p = round(px * (1 + ctx.rng.uniform(-0.002, 0.002)), dp) if k else round(px, dp)
        ctx.execs.append({"exec_id": f"EX{ctx.n_exec:06d}", "order_id": order["order_id"], "execution_venue": market,
                          "counterparty_id": cpty, "exec_qty": s, "exec_price": p,
                          "exec_ts_utc": ts + timedelta(minutes=3 * k), "liquidity_flag": ctx.rng.choice(["ADD", "REMOVE"]),
                          "source_system": "EMS-SIM", "source_record_id": f"EMS-{900000 + ctx.n_exec}"})
        fills.append((s, p))
    return fills


def _trade(ctx: Ctx, **kw) -> dict:
    ctx.n_trade += 1
    tid = f"TRD{ctx.n_trade:05d}"
    base = {k: None for k in TRADE_COLUMNS}
    base.update({"trade_id": tid, "source_trade_ref": f"FO-{ctx.rng.randint(10**7, 10**8 - 1)}-{ctx.n_trade}",
                 "source_system": "FO-SIM", "source_record_id": f"FO-{tid}", "ingest_batch": "BASELINE-001"})
    base.update(kw)
    ctx.trades.append(base)
    return base


def _allocate(ctx: Ctx, trade: dict, client: dict, qty: int, unit: int, group: str, max_split=3):
    funds = [a for a in ctx.acc_by_client[client["client_id"]]
             if a["account_type"] == "FUND" and group in a["permitted_products"].split(",")]
    if not funds or ctx.rng.random() < 0.3:
        targets = [trade["account_id"]]
    else:
        targets = ctx.rng.sample(funds, min(len(funds), ctx.rng.randint(1, max_split)))
    sizes = _split(ctx.rng, qty, len(targets), unit)
    if len(sizes) != len(targets):
        targets = targets[:1]
        sizes = [qty]
    for seq, (a, s) in enumerate(zip(targets, sizes, strict=True), start=1):
        ctx.n_alloc += 1
        acct_id = a if isinstance(a, str) else a["account_id"]
        ctx.allocs.append({"allocation_id": f"AL{ctx.n_alloc:06d}", "trade_id": trade["trade_id"], "alloc_seq": seq,
                           "account_id": acct_id, "alloc_qty": s, "source_system": "ALLOC-SIM",
                           "source_record_id": f"ALC-{trade['trade_id']}-{seq}"})


TRADE_COLUMNS = [
    "trade_id", "source_trade_ref", "order_id", "product_group", "product_type", "instrument_id", "client_id",
    "account_id", "book", "trader_id", "counterparty_id", "side", "quantity", "price", "price_type", "trade_ccy",
    "settle_ccy", "trade_date", "trade_time_utc", "market", "market_timezone", "settlement_date", "end_date",
    "fx_counter_ccy", "fx_counter_amount", "fx_spot_rate", "fx_forward_points", "fx_tenor_months",
    "repo_rate_pct", "haircut_pct", "collateral_ccy", "sl_fee_bps", "irs_fixed_rate_pct", "irs_float_index",
    "irs_spread_bps", "irs_tenor_years", "execution_venue", "source_system", "source_record_id", "ingest_batch",
]


def settle_cals(ctx: Ctx, market: str, ccy: str) -> list[set]:
    return [ctx.hol[MARKET[market][1]], ctx.hol[CCY_CALENDAR[ccy]]]


def eq_settle_date(ctx: Ctx, market: str, ccy: str, td: date) -> date:
    lag = {"XNYS": 1, "XLON": 2, "XETR": 2, "XTKS": 2}[market]
    return ref.add_business_days(td, lag, settle_cals(ctx, market, ccy))


def gen_equities(ctx: Ctx, n: int):
    rng = ctx.rng
    ids = list(ctx.eq)
    weights = {"XNYS": 0.43, "XLON": 0.21, "XETR": 0.18, "XTKS": 0.18}
    made = 0
    while made < n:
        market = rng.choices(list(weights), list(weights.values()))[0]
        cands = [i for i in ids if ctx.inst[i]["market"] == market]
        iid = rng.choice(cands)
        ccy = ctx.inst[iid]["currency"]
        td = rng.choice(ctx.bdays([MARKET[market][1], CCY_CALENDAR[ccy]]))
        if iid in STALE_FROM and td >= STALE_FROM[iid] - timedelta(days=1):
            continue
        if (iid, td) not in ctx.px:
            continue
        clients = ctx.eligible_clients("EQUITY", td)
        c = rng.choice(clients)
        block = [a for a in ctx.acc_by_client[c["client_id"]] if "EQUITY" in a["permitted_products"]][0]
        lot = ctx.eq[iid]["lot_size"]
        close = ctx.px[(iid, td)]
        qty = rng.choice([100, 200, 500, 1000, 2500, 5000, 10000, 20000]) if lot == 1 else rng.choice([1, 2, 5, 10, 30, 50]) * 100
        if close * qty > 15_000_000 and ccy != "JPY":
            qty = max(lot, int(2_000_000 / close) // lot * lot)
        side = rng.choice(["BUY", "SELL"])
        tz = MARKET[market][2]
        hh, mm = rng.randint(9, 15), rng.randint(0, 59)
        if market == "XTKS":
            hh = rng.choice([9, 10, 13, 14])
        if hh == 9 and mm < 30 and market != "XTKS":
            mm += 30
        ts = _utc(td, hh, mm, tz)
        cpty = rng.choice(BROKERS[MARKET_REGION[market]])
        trader = rng.choice(TRADERS["EQUITY"])
        order = _new_order(ctx, c, block, iid, ctx.inst[iid]["product_type"], side, qty, None, ts - timedelta(minutes=2),
                           trader, ccy)
        dp = 0 if ccy == "JPY" else 2
        fills = _fills(ctx, order, qty, lot, close, market, cpty, ts, dp)
        if rng.random() < 0.08:  # order not completely filled: done for the day
            order["order_qty"] = qty + lot * rng.randint(1, 5) * (100 if lot == 1 else 1)
            order["status"] = "PARTIALLY_FILLED_DONE"
        avg = ref.round_dp(ref.vwap(fills), 6)
        t = _trade(ctx, order_id=order["order_id"], product_group="EQUITY", product_type=ctx.inst[iid]["product_type"],
                   instrument_id=iid, client_id=c["client_id"], account_id=block["account_id"],
                   book={"XNYS": "EQ-CASH-NY", "XTKS": "EQ-CASH-TKY"}.get(market, "EQ-CASH-LDN"), trader_id=trader,
                   counterparty_id=cpty, side=side, quantity=qty, price=float(avg), price_type="PER_SHARE",
                   trade_ccy=ccy, settle_ccy=ccy, trade_date=td, trade_time_utc=ts, market=market, market_timezone=tz,
                   settlement_date=eq_settle_date(ctx, market, ccy, td), execution_venue=market)
        _allocate(ctx, t, c, qty, lot if lot > 1 else 100 if qty >= 300 else 1, "EQUITY")
        made += 1


def gen_bonds(ctx: Ctx, n: int):
    rng = ctx.rng
    govt = [i for i in ctx.bond if ctx.inst[i]["product_type"] == "GOVT_BOND"]
    corp = [i for i in ctx.bond if ctx.inst[i]["product_type"] == "CORP_BOND"]
    lag = {"OTC-US": 1, "OTC-UK": 1, "OTC-EU": 2, "OTC-JP": 1}
    made = 0
    while made < n:
        iid = rng.choice(govt if made < 60 else corp)
        b, inst = ctx.bond[iid], ctx.inst[iid]
        market, ccy = inst["market"], inst["currency"]
        td = rng.choice(ctx.bdays([CCY_CALENDAR[ccy]]))
        if (iid, td) not in ctx.px or (iid in STALE_FROM and td >= STALE_FROM[iid] - timedelta(days=1)):
            continue
        sd = ref.add_business_days(td, lag[market], [ctx.hol[CCY_CALENDAR[ccy]]])
        c = rng.choice(ctx.eligible_clients("BOND", td))
        block = [a for a in ctx.acc_by_client[c["client_id"]] if "BOND" in a["permitted_products"]][0]
        if ccy == "JPY":
            nominal = rng.choice([50, 100, 200, 500, 1000]) * 1_000_000
        else:
            nominal = rng.choice([250, 500, 1000, 2000, 3000, 5000]) * 1000
        side = rng.choice(["BUY", "SELL"])
        tz = {"OTC-US": "America/New_York", "OTC-UK": "Europe/London", "OTC-EU": "Europe/Berlin", "OTC-JP": "Asia/Tokyo"}[market]
        hh = rng.randint(8, 16)
        if market == "OTC-JP" and rng.random() < 0.5:
            hh = 8  # 08:xx JST is the previous day in UTC (timezone rule test)
        ts = _utc(td, hh, rng.randint(0, 59), tz)
        cpty = rng.choice(BROKERS[MARKET_REGION[market]])
        trader = rng.choice(TRADERS["BOND"])
        price = round(ctx.px[(iid, td)] + rng.uniform(-0.05, 0.05), 4)
        order = _new_order(ctx, c, block, iid, inst["product_type"], side, nominal, None, ts - timedelta(minutes=1),
                           trader, ccy)
        _fills(ctx, order, nominal, nominal, price, market, cpty, ts, 4, max_fills=1)
        t = _trade(ctx, order_id=order["order_id"], product_group="BOND", product_type=inst["product_type"],
                   instrument_id=iid, client_id=c["client_id"], account_id=block["account_id"],
                   book="RATES-GOVT" if inst["product_type"] == "GOVT_BOND" else "CREDIT-CORP", trader_id=trader,
                   counterparty_id=cpty, side=side, quantity=nominal, price=price, price_type="PCT_OF_PAR",
                   trade_ccy=ccy, settle_ccy=ccy, trade_date=td, trade_time_utc=ts, market=market, market_timezone=tz,
                   settlement_date=sd, execution_venue="OTC")
        _allocate(ctx, t, c, nominal, max(b["min_increment"], 1000 if ccy != "JPY" else 50_000_000), "BOND", max_split=2)
        made += 1


def gen_fx(ctx: Ctx, n: int):
    rng = ctx.rng
    pairs = list(ctx.fxd)
    cal = {c: ctx.hol[CCY_CALENDAR[c]] for c in CCY_CALENDAR}
    made = 0
    while made < n:
        iid = rng.choice(pairs)
        d = ctx.fxd[iid]
        b, q = d["base_ccy"], d["quote_ccy"]
        td = rng.choice(ctx.bdays([CCY_CALENDAR[b], CCY_CALENDAR[q]]))
        fwd = made >= 50
        c = rng.choice(ctx.eligible_clients("FX", td))
        block = [a for a in ctx.acc_by_client[c["client_id"]] if "FX" in a["permitted_products"]][0]
        amount = rng.choice([1, 2, 3, 5, 10, 15, 25]) * 1_000_000
        spot = fx_pair_rate(ctx.fx, b, q, td, d["rate_precision_dp"])
        spot = round(spot * (1 + rng.uniform(-0.0004, 0.0004)), d["rate_precision_dp"])
        side = rng.choice(["BUY", "SELL"])
        ts = _utc(td, rng.randint(8, 16), rng.randint(0, 59), "Europe/London")
        if fwd:
            m = rng.choice([1, 2, 3, 6])
            pts = ctx.fwd[(b + q, td, m)]
            rate = float(ref.round_dp(ref.fx_forward_rate(spot, pts, q), d["rate_precision_dp"] + 2))
            vd = ref.fx_forward_date(td, b, q, m, cal)
        else:
            m, pts, rate = None, None, spot
            vd = ref.fx_spot_date(td, b, q, cal)
        counter = ref.fx_counter_amount(amount, rate, q)
        cpty = rng.choice(BROKERS["FX"])
        trader = rng.choice(TRADERS["FX"])
        order = _new_order(ctx, c, block, iid, "FX_FORWARD" if fwd else "FX_SPOT", side, amount, None,
                           ts - timedelta(seconds=30), trader, b)
        _fills(ctx, order, amount, amount, rate, "OTC-FX", cpty, ts, 7, max_fills=1)
        t = _trade(ctx, order_id=order["order_id"], product_group="FX", product_type="FX_FORWARD" if fwd else "FX_SPOT",
                   instrument_id=iid, client_id=c["client_id"], account_id=block["account_id"], book="FX-SPOT-FWD",
                   trader_id=trader, counterparty_id=cpty, side=side, quantity=amount, price=rate, price_type="FX_RATE",
                   trade_ccy=b, settle_ccy=b, trade_date=td, trade_time_utc=ts, market="OTC-FX",
                   market_timezone="Europe/London", settlement_date=vd, fx_counter_ccy=q,
                   fx_counter_amount=float(counter), fx_spot_rate=spot if fwd else None,
                   fx_forward_points=pts, fx_tenor_months=m, execution_venue="OTC")
        _allocate(ctx, t, c, amount, amount, "FX", max_split=1)
        made += 1


def gen_listed(ctx: Ctx, n: int):
    rng = ctx.rng
    futs = [i for i, d in ctx.der.items() if d["derivative_type"] == "FUTURE"]
    opts = [i for i, d in ctx.der.items() if d["derivative_type"] == "OPTION"]
    made = 0
    while made < n:
        is_fut = made < 40
        iid = rng.choice(futs if is_fut else opts)
        d, inst = ctx.der[iid], ctx.inst[iid]
        market, ccy = inst["market"], inst["currency"]
        days_ok = [x for x in ctx.bdays([MARKET[market][1]]) if x < d["last_trading_day"] and (iid, x) in ctx.px]
        if not days_ok:
            continue
        td = rng.choice(days_ok)
        c = rng.choice(ctx.eligible_clients("LISTED_DERIV", td))
        block = [a for a in ctx.acc_by_client[c["client_id"]] if "LISTED_DERIV" in a["permitted_products"]][0]
        qty = rng.choice([1, 2, 5, 10, 20, 25, 50, 100])
        side = rng.choice(["BUY", "SELL"])
        tz = MARKET[market][2]
        ts = _utc(td, rng.randint(9, 14), rng.randint(0, 59), tz)
        cpty = rng.choice(BROKERS[MARKET_REGION[market]])
        trader = rng.choice(TRADERS["LISTED_DERIV"])
        px = ctx.px[(iid, td)]
        order = _new_order(ctx, c, block, iid, d["derivative_type"], side, qty, None, ts - timedelta(minutes=1), trader, ccy)
        fills = _fills(ctx, order, qty, 1, px, market, cpty, ts, 2, max_fills=3)
        avg = ref.round_dp(ref.vwap(fills), 6)
        sd = td if is_fut else ref.add_business_days(td, 1, [ctx.hol[MARKET[market][1]], ctx.hol[CCY_CALENDAR[ccy]]])
        t = _trade(ctx, order_id=order["order_id"], product_group="LISTED_DERIV", product_type=d["derivative_type"],
                   instrument_id=iid, client_id=c["client_id"], account_id=block["account_id"], book="DERIV-LISTED",
                   trader_id=trader, counterparty_id=cpty, side=side, quantity=qty, price=float(avg),
                   price_type="INDEX_POINTS" if is_fut else "PREMIUM", trade_ccy=ccy, settle_ccy=ccy, trade_date=td,
                   trade_time_utc=ts, market=market, market_timezone=tz, settlement_date=sd, execution_venue=market)
        _allocate(ctx, t, c, qty, 1, "LISTED_DERIV", max_split=2)
        made += 1


def gen_irs(ctx: Ctx, n: int):
    rng = ctx.rng
    templates = list(ctx.swap)
    for _ in range(n):
        iid = rng.choices(templates, [0.45, 0.3, 0.15, 0.10])[0]
        s = ctx.swap[iid]
        ccy = s["currency"]
        td = rng.choice(ctx.bdays([s["payment_calendar"]]))
        c = rng.choice(ctx.eligible_clients("IRS", td))
        block = [a for a in ctx.acc_by_client[c["client_id"]] if "IRS" in a["permitted_products"]][0]
        notional = rng.choice([10, 25, 50, 75, 100, 150]) * 1_000_000 * (100 if ccy == "JPY" else 1)
        tenor = rng.choice([2, 3, 5, 7, 10])
        fixing = ctx.fix[(s["float_index"], td)]
        fixed = round(fixing + rng.uniform(-0.35, 0.25) + 0.03 * tenor, 4)
        eff = ref.add_business_days(td, 2, [ctx.hol[s["payment_calendar"]]])
        mat = ref.add_months_raw(eff, 12 * tenor)
        side = rng.choice(["PAY_FIXED", "RECEIVE_FIXED"])
        ts = _utc(td, rng.randint(8, 16), rng.randint(0, 59), "Europe/London")
        cpty = rng.choice(BROKERS["RATES"])
        trader = rng.choice(TRADERS["IRS"])
        order = _new_order(ctx, c, block, iid, "IRS", side, notional, None, ts - timedelta(minutes=5), trader, ccy)
        _fills(ctx, order, notional, notional, fixed, "OTC-RATES", cpty, ts, 4, max_fills=1)
        t = _trade(ctx, order_id=order["order_id"], product_group="IRS", product_type="IRS", instrument_id=iid,
                   client_id=c["client_id"], account_id=block["account_id"], book="RATES-SWAPS", trader_id=trader,
                   counterparty_id=cpty, side=side, quantity=notional, price=fixed, price_type="RATE_PCT",
                   trade_ccy=ccy, settle_ccy=ccy, trade_date=td, trade_time_utc=ts, market="OTC-RATES",
                   market_timezone="Europe/London", settlement_date=eff, end_date=mat,
                   irs_fixed_rate_pct=fixed, irs_float_index=s["float_index"], irs_spread_bps=rng.choice([0, 0, 0, 5, 10]),
                   irs_tenor_years=tenor, execution_venue="OTC")
        _allocate(ctx, t, c, notional, notional, "IRS", max_split=1)


def gen_financing(ctx: Ctx, n: int):
    rng = ctx.rng
    govt = [i for i in ctx.bond if ctx.inst[i]["product_type"] == "GOVT_BOND" and i not in STALE_FROM]
    lend_eq = [i for i in ctx.eq if ctx.inst[i]["market"] in ("XNYS", "XLON", "XETR") and i not in STALE_FROM
               and ctx.inst[i]["product_type"] == "EQUITY"]
    kinds = ["REPO"] * 15 + ["REVERSE_REPO"] * 10 + ["SEC_LOAN"] * 8 + ["SEC_BORROW"] * 7
    for kind in kinds[:n]:
        if kind in ("REPO", "REVERSE_REPO"):
            iid = rng.choice(govt)
            inst, b = ctx.inst[iid], ctx.bond[iid]
            ccy = inst["currency"]
            cals = [ctx.hol[CCY_CALENDAR[ccy]]]
            while True:
                td = rng.choice(ctx.bdays([CCY_CALENDAR[ccy]]))
                if (iid, td) in ctx.px:
                    break
            start = td if rng.random() < 0.5 else ref.add_business_days(td, 1, cals)
            term = rng.choice(["ON", "1W", "1M", "3M"])
            end = {"ON": ref.add_business_days(start, 1, cals), "1W": ref.roll_following(start + timedelta(days=7), cals),
                   "1M": ref.roll_modified_following(ref.add_months_raw(start, 1), cals),
                   "3M": ref.roll_modified_following(ref.add_months_raw(start, 3), cals)}[term]
            nominal = rng.choice([5, 10, 20, 25, 50]) * 1_000_000 * (100 if ccy == "JPY" else 1)
            ai = ref.accrued_interest_per_100(start, b["coupon_rate_pct"], b["coupon_frequency"], b["day_count"],
                                              b["maturity_date"], b["issue_date"], b["ex_dividend_bdays"],
                                              ctx.hol[CCY_CALENDAR[ccy]])
            dirty = float(ref.round_dp(ref.to_frac(ctx.px[(iid, td)]) + ai, 6))
            rate = round(ctx.fix[({"USD": "SOFR-SIM", "EUR": "ESTR-SIM", "GBP": "SONIA-SIM", "JPY": "TONA-SIM"}[ccy], td)]
                         + rng.uniform(-0.05, 0.15), 4)
            extra = dict(price=dirty, price_type="DIRTY_PCT", settlement_date=start, end_date=end, repo_rate_pct=rate,
                         haircut_pct=2.0, collateral_ccy=ccy, quantity=nominal)
            market = inst["market"]
            cpty = rng.choice(BROKERS["FIN"])
        else:
            iid = rng.choice(lend_eq)
            inst = ctx.inst[iid]
            ccy, market = inst["currency"], inst["market"]
            while True:
                td = rng.choice(ctx.bdays([MARKET[market][1], CCY_CALENDAR[ccy]]))
                if (iid, td) in ctx.px:
                    break
            start = eq_settle_date(ctx, market, ccy, td)
            cals = settle_cals(ctx, market, ccy)
            end = ref.roll_modified_following(ref.add_months_raw(start, rng.choice([1, 2])), cals)
            qty = rng.choice([10, 20, 50, 100, 200]) * 1000
            coll_ccy = ccy if rng.random() < 0.6 else "USD"
            extra = dict(price=ctx.px[(iid, td)], price_type="PER_SHARE", settlement_date=start, end_date=end,
                         sl_fee_bps=rng.choice([25, 40, 60, 90, 150]), collateral_ccy=coll_ccy, quantity=qty)
            cpty = rng.choice(BROKERS["FIN"])
        c = rng.choice(ctx.eligible_clients("FINANCING", td))
        block = [a for a in ctx.acc_by_client[c["client_id"]] if "FINANCING" in a["permitted_products"]][0]
        tz = MARKET[market][2]
        # same-day repo starts are traded early so instructions beat the London cut-off (R013)
        ts = _utc(td, rng.randint(8, 9) if extra["settlement_date"] == td else rng.randint(8, 12), rng.randint(0, 59), tz)
        trader = rng.choice(TRADERS["FINANCING"])
        order = _new_order(ctx, c, block, iid, kind, kind, extra["quantity"], None, ts - timedelta(minutes=2), trader, ccy)
        _fills(ctx, order, extra["quantity"], extra["quantity"], extra["price"], market, cpty, ts, 6, max_fills=1)
        t = _trade(ctx, order_id=order["order_id"], product_group="FINANCING", product_type=kind, instrument_id=iid,
                   client_id=c["client_id"], account_id=block["account_id"], book=BOOKS["FINANCING"][0],
                   trader_id=trader, counterparty_id=cpty, side=kind, trade_ccy=ccy, settle_ccy=ccy, trade_date=td,
                   trade_time_utc=ts, market=market, market_timezone=tz, execution_venue="OTC", **extra)
        _allocate(ctx, t, c, extra["quantity"], extra["quantity"], "FINANCING", max_split=1)


def gen_unfilled_orders(ctx: Ctx, k: int):
    """Orders that never became trades (cancelled or rejected before execution)."""
    rng = ctx.rng
    for _ in range(k):
        src = rng.choice(ctx.orders[:400])
        o = dict(src)
        ctx.n_order += 1
        o["order_id"] = f"ORD{ctx.n_order:05d}"
        o["status"] = rng.choice(["CANCELLED", "REJECTED"])
        o["order_ts_utc"] = src["order_ts_utc"] + timedelta(minutes=rng.randint(5, 90))
        o["source_record_id"] = f"OMS-{100000 + ctx.n_order}"
        ctx.orders.append(o)


def build_all(ctx: Ctx):
    gen_equities(ctx, COUNTS["EQUITY"])
    gen_bonds(ctx, COUNTS["BOND"])
    gen_fx(ctx, COUNTS["FX"])
    gen_listed(ctx, COUNTS["LISTED_DERIV"])
    gen_irs(ctx, COUNTS["IRS"])
    gen_financing(ctx, COUNTS["FINANCING"])
    # Legitimate look-alike trades (same economics, different source ref): duplicate-review cases
    for src_idx in (5, 200):
        src = ctx.trades[src_idx]
        o = dict(next(x for x in ctx.orders if x["order_id"] == src["order_id"]))
        ctx.n_order += 1
        o["order_id"] = f"ORD{ctx.n_order:05d}"
        o["order_ts_utc"] = src["trade_time_utc"] + timedelta(minutes=41)
        o["source_record_id"] = f"OMS-{100000 + ctx.n_order}"
        ctx.orders.append(o)
        ctx.n_exec += 1
        ctx.execs.append({"exec_id": f"EX{ctx.n_exec:06d}", "order_id": o["order_id"],
                          "execution_venue": src["execution_venue"], "counterparty_id": src["counterparty_id"],
                          "exec_qty": src["quantity"], "exec_price": src["price"],
                          "exec_ts_utc": src["trade_time_utc"] + timedelta(minutes=42), "liquidity_flag": "ADD",
                          "source_system": "EMS-SIM", "source_record_id": f"EMS-{900000 + ctx.n_exec}"})
        # replace the last trade of the same group so the group count stays fixed
        victim = max((i for i, t in enumerate(ctx.trades) if t["product_group"] == src["product_group"]))
        vid = ctx.trades[victim]["trade_id"]
        old_order = ctx.trades[victim]["order_id"]
        ctx.execs = [e for e in ctx.execs if e["order_id"] != old_order]
        for od in ctx.orders:
            if od["order_id"] == old_order:
                od["status"] = "CANCELLED"
        ctx.allocs = [a for a in ctx.allocs if a["trade_id"] != vid]
        ctx.trades[victim] = dict(src, trade_id=vid, order_id=o["order_id"],
                                  source_trade_ref=src["source_trade_ref"] + "-B",
                                  trade_time_utc=src["trade_time_utc"] + timedelta(minutes=42),
                                  source_record_id=f"FO-{vid}")
        for a in [a for a in ctx.allocs if a["trade_id"] == src["trade_id"]]:
            ctx.n_alloc += 1
            ctx.allocs.append(dict(a, allocation_id=f"AL{ctx.n_alloc:06d}", trade_id=vid,
                                   source_record_id=f"ALC-{vid}-{a['alloc_seq']}"))
    gen_unfilled_orders(ctx, 36)
