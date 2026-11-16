"""Booking validation and enrichment (production rule implementations)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.rules import calc
from cf360.rules.calc import CCY_CALENDAR, dec, q_ccy

COMPLEX_GROUPS = {"LISTED_DERIV", "IRS", "FINANCING"}
OPEN_STATES_EXCLUDED = {"CANCELLED", "REJECTED", "CLOSED", "MATURED", "EXPIRED", "EXERCISED"}
EXPOSURE_WEIGHT = {"EQUITY": Decimal("1"), "BOND": Decimal("1"), "FX": Decimal("0.1"), "LISTED_DERIV": Decimal("0.1"),
                   "IRS": Decimal("0.05"), "FINANCING": Decimal("0.1")}
COLLAR = {"EQUITY": ("PCT", Decimal("0.10")), "ETF": ("PCT", Decimal("0.10")), "FUTURE": ("PCT", Decimal("0.10")),
          "GOVT_BOND": ("PTS", Decimal("5")), "CORP_BOND": ("PTS", Decimal("5"))}
STALE_BDAYS = 3


@dataclass
class Issue:
    rule_id: str
    code: str
    message: str
    severity: str = "ERROR"
    field: str | None = None

    def as_dict(self):
        return {"rule_id": self.rule_id, "code": self.code, "message": self.message, "severity": self.severity,
                "field": self.field}


def market_calendars(ref, inst: m.Instrument, ccy: str, basis: str) -> tuple[str, ...]:
    mk = ref.markets.get(inst.market)
    if basis == "MARKET":
        return (mk.calendar_code,)
    if basis == "CCY":
        return (CCY_CALENDAR[ccy],)
    if basis == "BOTH_CCY":
        return (CCY_CALENDAR[inst.attrs["base_ccy"]], CCY_CALENDAR[inst.attrs["quote_ccy"]])
    return (mk.calendar_code, CCY_CALENDAR[ccy])


def trade_calendars(ref, inst: m.Instrument, ptype: str) -> tuple[str, ...]:
    """Calendar(s) on which the trade date must be a business day (R010)."""
    if ptype in ("FX_SPOT", "FX_FORWARD"):
        return (CCY_CALENDAR[inst.attrs["base_ccy"]], CCY_CALENDAR[inst.attrs["quote_ccy"]])
    if ptype == "IRS":
        return (inst.attrs["payment_calendar"],)
    if ptype in ("GOVT_BOND", "CORP_BOND", "REPO", "REVERSE_REPO"):
        return (CCY_CALENDAR[inst.currency],)
    return (ref.markets[inst.market].calendar_code,)


def expected_settlement_date(ref, inst: m.Instrument, t: dict) -> tuple[date | None, str | None]:
    ptype = t["product_type"]
    td = t["trade_date"]
    if ptype in ("REPO", "REVERSE_REPO"):
        return t["settlement_date"], "SC-REPO"
    if ptype in ("FX_SPOT", "FX_FORWARD"):
        a = inst.attrs
        return calc.fx_value_date(ref.cals, td, a["base_ccy"], a["quote_ccy"],
                                  int(t["terms"].get("fx_tenor_months") or 0) if ptype == "FX_FORWARD" else None), "SC-FX"
    conv = ref.convention(ptype, inst.market, td)
    if conv is None:
        return None, None
    if conv.offset_bdays == 0:
        return td, conv.convention_id
    codes = market_calendars(ref, inst, t["settle_ccy"], conv.calendar_basis)
    if ptype == "IRS":
        codes = (inst.attrs["payment_calendar"],)
    return ref.cals.add(td, conv.offset_bdays, *codes), conv.convention_id


def validate(s: Session, ref, t: dict, sandbox_id: str | None = None) -> tuple[list[Issue], dict]:
    """Return (issues, context). ``t`` is a normalised booking dict (see services.booking)."""
    issues: list[Issue] = []
    ctx: dict = {}

    def err(rule, code, msg, field=None, severity="ERROR"):
        issues.append(Issue(rule, code, msg, severity, field))

    for f in ("instrument_id", "client_id", "account_id", "counterparty_id", "trade_date", "side", "quantity", "price",
              "settle_ccy", "product_type"):
        if t.get(f) in (None, ""):
            err("DQ01", "E_REQUIRED_FIELD", f"{f} is required", f)
    if issues:
        return issues, ctx
    inst = ref.instruments.get(t["instrument_id"])
    client = s.get(m.Client, t["client_id"])
    acct = s.get(m.Account, t["account_id"])
    if inst is None:
        err("DQ02", "E_UNKNOWN_INSTRUMENT", f"Unknown instrument {t['instrument_id']}", "instrument_id")
    if client is None:
        err("DQ02", "E_UNKNOWN_CLIENT", f"Unknown client {t['client_id']}", "client_id")
    if acct is None:
        err("DQ02", "E_UNKNOWN_ACCOUNT", f"Unknown account {t['account_id']}", "account_id")
    if t["counterparty_id"] not in ref.counterparties:
        err("DQ02", "E_UNKNOWN_COUNTERPARTY", f"Unknown counterparty {t['counterparty_id']}", "counterparty_id")
    if issues:
        return issues, ctx
    ctx.update(inst=inst, client=client, acct=acct)
    ptype, group, td = t["product_type"], inst.product_group, t["trade_date"]
    qty, price = dec(t["quantity"]), dec(t["price"])
    expected_type = {"FX_PAIR": ("FX_SPOT", "FX_FORWARD"), "IRS_TEMPLATE": ("IRS",),
                     "GOVT_BOND": ("GOVT_BOND", "REPO", "REVERSE_REPO"),
                     "EQUITY": ("EQUITY", "SEC_LOAN", "SEC_BORROW")}.get(inst.product_type, (inst.product_type,))
    if ptype not in expected_type:
        err("DQ04", "E_DOMAIN", f"Product type {ptype} does not fit instrument type {inst.product_type}", "product_type")
        return issues, ctx
    if ptype in ("REPO", "REVERSE_REPO", "SEC_LOAN", "SEC_BORROW"):
        group = "FINANCING"
    ctx["group"] = group
    if acct.client_id != client.client_id:
        err("R003", "E_ACCOUNT_CLIENT_MISMATCH", "Account does not belong to client", "account_id")
    # R001 / R002 / R003 / R004 / R005
    if client.status != "ACTIVE":
        err("R001", "E_CLIENT_INACTIVE", f"Client status is {client.status}", "client_id")
    if td > client.kyc_expiry_date:
        err("R002", "E_KYC_EXPIRED", f"KYC expired on {client.kyc_expiry_date}", "client_id")
    if group not in acct.permitted_products.split(","):
        err("R003", "E_PRODUCT_NOT_PERMITTED", f"{group} not permitted for account {acct.account_id}", "account_id")
    if group in COMPLEX_GROUPS and client.client_category not in ("PROFESSIONAL", "ELIGIBLE_COUNTERPARTY"):
        err("R004", "E_CLIENT_CATEGORY", f"{client.client_category} client cannot trade {group}", "client_id")
    if inst.status != "ACTIVE" or (inst.maturity_date and ptype in ("GOVT_BOND", "CORP_BOND") and td >= inst.maturity_date):
        err("R005", "E_INSTRUMENT_EXPIRED", "Instrument is not active", "instrument_id")
    if ptype in ("FUTURE", "OPTION") and td > date.fromisoformat(str(inst.attrs["last_trading_day"])):
        err("R052", "E_CONTRACT_EXPIRED", f"Last trading day was {inst.attrs['last_trading_day']}", "trade_date")
    # R007 / R034 / R028
    if qty <= 0:
        err("R007", "E_QTY_INVALID", "Quantity must be positive", "quantity")
    elif ptype in ("EQUITY", "ETF") and qty % int(inst.attrs.get("lot_size", 1)) != 0:
        err("R007", "E_LOT_SIZE", f"Quantity must be a multiple of {inst.attrs.get('lot_size')}", "quantity")
    elif ptype in ("FUTURE", "OPTION") and qty != qty.to_integral_value():
        err("R007", "E_LOT_SIZE", "Contracts must be whole", "quantity")
    if ptype in ("GOVT_BOND", "CORP_BOND") and qty > 0:
        a = inst.attrs
        if qty < a["min_denomination"] or qty % a["min_increment"] != 0:
            err("R034", "E_DENOMINATION", f"Nominal must be >= {a['min_denomination']} in steps of {a['min_increment']}", "quantity")
    if ptype in ("FX_SPOT", "FX_FORWARD"):
        a = inst.attrs
        counter = t["terms"].get("fx_counter_amount")
        if counter is not None:
            counter = dec(counter)
            if not calc.is_ccy_precise(counter, a["quote_ccy"]):
                err("R028", "E_CCY_PRECISION", f"{a['quote_ccy']} amount has too many decimals", "fx_counter_amount")
            else:
                exp = calc.fx_counter(qty, price, a["quote_ccy"])
                if abs(exp - counter) > Decimal(1).scaleb(-calc.MINOR[a["quote_ccy"]]):
                    err("R042", "E_FX_COUNTER_MISMATCH", f"Counter amount {counter} != {exp}", "fx_counter_amount")
        if ptype == "FX_FORWARD" and t["terms"].get("fx_spot_rate") is not None:
            out = calc.fx_outright(t["terms"]["fx_spot_rate"], t["terms"].get("fx_forward_points") or 0, a["quote_ccy"])
            if abs(out - price) > Decimal("0.000001"):
                err("R043", "E_FX_POINTS_MISMATCH", f"Outright {price} != spot + points {out}", "price")
    elif not calc.is_ccy_precise(qty, t["settle_ccy"]) and ptype in ("FX_SPOT", "FX_FORWARD", "IRS"):
        err("R028", "E_CCY_PRECISION", "Amount has too many decimals", "quantity")
    # R014 trade date in market local time
    ts = t.get("trade_time_utc")
    tz = ref.markets[inst.market].timezone if inst.market in ref.markets else "UTC"
    if ptype in ("FX_SPOT", "FX_FORWARD", "IRS"):
        tz = t.get("market_timezone") or t["terms"].get("market_timezone") or "Europe/London"
    if ts is not None:
        local = ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz)).date()
        if local != td:
            err("R014", "E_TRADE_DATE_TZ", f"Executed {ts:%Y-%m-%d %H:%M} UTC = {local} in {tz}, not {td}", "trade_date")
    # R010 trade date
    codes = trade_calendars(ref, inst, ptype)
    if not ref.cals.is_open(td, *codes):
        err("R010", "E_TRADE_DATE_HOLIDAY", f"{td} is not a business day on {'/'.join(codes)}", "trade_date")
        return issues, ctx
    # R015 / R011 / R012 settlement date
    exp_sd, conv_id = expected_settlement_date(ref, inst, t)
    ctx["convention_id"] = conv_id
    if exp_sd is None:
        err("R015", "E_NO_CONVENTION", f"No settlement convention for {ptype} on {inst.market} at {td}", "settlement_date")
    elif t.get("settlement_date") and t["settlement_date"] != exp_sd:
        err("R011", "E_SETTLE_DATE_MISMATCH", f"Settlement date {t['settlement_date']} != expected {exp_sd} ({conv_id})",
            "settlement_date")
    ctx["settlement_date"] = exp_sd or t.get("settlement_date")
    sd = ctx["settlement_date"]
    if sd and ptype in ("REPO", "REVERSE_REPO") and not ref.cals.is_open(sd, CCY_CALENDAR[t["settle_ccy"]]):
        err("R012", "E_SETTLE_DATE_HOLIDAY", f"{sd} is not a settlement business day", "settlement_date")
    if ptype == "IRS":
        end = t.get("end_date")
        if end is None or (sd and end <= sd):
            err("R063", "E_SWAP_DATES", "Swap maturity must be after the effective date", "end_date")
        fix = ref.fixings.get((inst.attrs["float_index"], td))
        if fix is None:
            err("R061", "E_FIXING_MISSING", f"No {inst.attrs['float_index']} fixing on {td}", "trade_date")
    if ptype in ("REPO", "REVERSE_REPO", "SEC_LOAN", "SEC_BORROW") and (t.get("end_date") is None or (sd and t["end_date"] <= sd)):
        err("R012", "E_SETTLE_DATE_HOLIDAY", "End date must be after the start date", "end_date")
    # R009 stale price and R008 collar
    if ptype in ("EQUITY", "ETF", "GOVT_BOND", "CORP_BOND", "FUTURE", "OPTION"):
        p = ref.price_on_or_before(inst.instrument_id, td)
        if p is None or ref.cals.count_between(p[0], td, *codes) > STALE_BDAYS:
            err("R009", "E_STALE_PRICE", f"Reference price is stale (last {p[0] if p else 'none'})", "price")
        elif ptype in COLLAR:
            kind, band = COLLAR[ptype]
            refp = p[1]
            breach = abs(price / refp - 1) > band if kind == "PCT" else abs(price - refp) > band
            if breach:
                err("R008", "E_PRICE_COLLAR", f"Price {price} outside collar around {refp}", "price")
    if ptype in ("FX_SPOT",):
        a = inst.attrs
        try:
            refrate = ref.usd_per(a["base_ccy"], td) / ref.usd_per(a["quote_ccy"], td)
            if abs(price / refrate - 1) > Decimal("0.02"):
                err("R008", "E_PRICE_COLLAR", f"Rate {price} outside 2% collar around {refrate:.6f}", "price")
        except LookupError as e:
            err("R027", "E_FX_RATE_MISSING", str(e), "trade_date")
    # R029 duplicates
    dup = s.scalar(select(func.count()).select_from(m.Trade).where(
        m.Trade.source_system == t["source_system"], m.Trade.source_trade_ref == t["source_trade_ref"],
        m.Trade.status != "CANCELLED"))
    if dup:
        err("R029", "E_DUPLICATE", f"Source reference {t['source_trade_ref']} already booked", "source_trade_ref")
    else:
        soft = s.scalar(select(func.count()).select_from(m.Trade).where(
            m.Trade.client_id == t["client_id"], m.Trade.instrument_id == t["instrument_id"], m.Trade.side == t["side"],
            m.Trade.trade_date == td, m.Trade.quantity == qty, m.Trade.price == price, m.Trade.status != "CANCELLED"))
        if soft:
            err("R029", "W_POTENTIAL_DUPLICATE", "Same economics already booked today with another reference",
                "source_trade_ref", "WARNING")
    # R006 exposure (USD proxy)
    try:
        usd = usd_notional(ref, inst, t, ptype)
        ctx["usd_notional"] = usd
        used = s.scalar(select(func.coalesce(func.sum(m.Trade.usd_equivalent), 0)).where(
            m.Trade.client_id == client.client_id, m.Trade.status.not_in(OPEN_STATES_EXCLUDED))) or Decimal(0)
        new_exposure = used + usd * EXPOSURE_WEIGHT[group]
        ctx["exposure_used"] = new_exposure
        if new_exposure > client.exposure_limit_usd:
            err("R006", "E_LIMIT_BREACH", f"Exposure {new_exposure:,.0f} USD exceeds limit {client.exposure_limit_usd:,.0f}", "quantity")
        elif new_exposure >= client.exposure_limit_usd * Decimal("0.9"):
            err("R006", "W_LIMIT_WARNING", f"Exposure at {new_exposure / client.exposure_limit_usd:.0%} of limit", "quantity", "WARNING")
    except LookupError as e:
        err("R027", "E_FX_RATE_MISSING", str(e), "trade_date")
    # R050 initial margin
    if ptype == "FUTURE":
        im = qty * dec(inst.attrs["initial_margin_per_contract"])
        avail = cash_balance(s, acct.account_id, inst.currency, None, sandbox_id) + dec(acct.credit_line_usd) / ref.usd_per(inst.currency, td)
        ctx["initial_margin"] = q_ccy(im, inst.currency)
        if im > avail:
            err("R050", "E_INSUFFICIENT_MARGIN", f"Initial margin {im} exceeds available {avail:.2f}", "quantity")
    return issues, ctx


def usd_notional(ref, inst, t, ptype) -> Decimal:
    qty, price, td = dec(t["quantity"]), dec(t["price"]), t["trade_date"]
    ccy = inst.currency if ptype not in ("FX_SPOT", "FX_FORWARD") else inst.attrs["base_ccy"]
    if ptype in ("GOVT_BOND", "CORP_BOND", "REPO", "REVERSE_REPO"):
        local = qty * price / 100
    elif ptype in ("FX_SPOT", "FX_FORWARD", "IRS"):
        local = qty
    elif ptype in ("FUTURE", "OPTION"):
        local = qty * price * dec(inst.attrs["contract_multiplier"])
    else:
        local = qty * price
    return q_ccy(local * ref.usd_per(ccy, td), "USD")


def cash_balance(s: Session, account_id: str, ccy: str, as_of: date | None = None, sandbox_id: str | None = None) -> Decimal:
    """Main book sees only baseline postings; a sandbox sees baseline plus its own postings."""
    q = select(func.coalesce(func.sum(m.LedgerEntry.amount), 0)).where(
        m.LedgerEntry.ledger == "CASH", m.LedgerEntry.ledger_account == account_id, m.LedgerEntry.key == ccy)
    if as_of:
        q = q.where(m.LedgerEntry.value_date <= as_of)
    sb = m.LedgerEntry.sandbox_id
    q = q.where(sb.is_(None) | (sb == sandbox_id)) if sandbox_id else q.where(sb.is_(None))
    return dec(s.scalar(q) or 0)


def sec_balance(s: Session, account_id: str, iid: str, as_of: date | None = None, sandbox_id: str | None = None) -> Decimal:
    """Main book sees only baseline postings; a sandbox sees baseline plus its own postings."""
    q = select(func.coalesce(func.sum(m.LedgerEntry.amount), 0)).where(
        m.LedgerEntry.ledger == "SEC", m.LedgerEntry.ledger_account == account_id, m.LedgerEntry.key == iid)
    if as_of:
        q = q.where(m.LedgerEntry.value_date <= as_of)
    sb = m.LedgerEntry.sandbox_id
    q = q.where(sb.is_(None) | (sb == sandbox_id)) if sandbox_id else q.where(sb.is_(None))
    return dec(s.scalar(q) or 0)


def economics(s: Session, ref, trade: m.Trade, inst: m.Instrument) -> dict:
    """Compute and return trade economics as strings (stored in trades.economics)."""
    ptype, ccy, td, sd = trade.product_type, trade.settle_ccy, trade.trade_date, trade.settlement_date
    qty, price = dec(trade.quantity), dec(trade.price)
    terms = trade.terms or {}
    out: dict = {}
    if ptype in ("EQUITY", "ETF"):
        sch = ref.schedule(trade.account_id, "EQUITY", td)
        mk = ref.markets[inst.market]
        stamp = mk.stamp_duty_bps if inst.attrs.get("stamp_duty_exempt") == "N" else Decimal(0)
        e = calc.equity_economics(trade.side, qty, price, ccy, sch.commission_bps if sch else 0,
                                  sch.min_commission_usd if sch else 0, ref.usd_per(ccy, td), mk.exchange_fee_bps, stamp)
        out = {**e, "settle_amount": e["net"], "commission_schedule": sch.schedule_id if sch else None}
    elif ptype in ("GOVT_BOND", "CORP_BOND"):
        ai = calc.accrued_per_100(inst.attrs | {"issue_date": _d(inst.attrs["issue_date"]),
                                                "maturity_date": _d(inst.attrs["maturity_date"])}, sd, ref.cals, ccy)
        e = calc.bond_economics(qty, price, ai, ccy)
        out = {**e, "settle_amount": e["gross"]}
    elif ptype in ("FX_SPOT", "FX_FORWARD"):
        a = inst.attrs
        counter = calc.fx_counter(qty, price, a["quote_ccy"])
        out = {"base_amount": q_ccy(qty, a["base_ccy"]), "counter_amount": counter, "counter_ccy": a["quote_ccy"],
               "settle_amount": q_ccy(qty, a["base_ccy"]), "net": q_ccy(qty, a["base_ccy"])}
    elif ptype == "FUTURE":
        mult = dec(inst.attrs["contract_multiplier"])
        out = {"notional": q_ccy(qty * price * mult, ccy),
               "initial_margin": q_ccy(qty * dec(inst.attrs["initial_margin_per_contract"]), ccy),
               "settle_amount": Decimal(0), "net": Decimal(0)}
    elif ptype == "OPTION":
        prem = calc.option_premium(qty, price, inst.attrs["contract_multiplier"], ccy)
        out = {"premium": prem, "settle_amount": prem, "net": prem}
    elif ptype == "IRS":
        a = inst.attrs
        cal = a["payment_calendar"]
        fixed = calc.swap_periods(ref.cals, sd, trade.end_date, int(a["fixed_freq_months"]), cal)
        flt = calc.swap_periods(ref.cals, sd, trade.end_date, int(a["float_freq_months"]), cal)
        fixing = ref.fixings[(a["float_index"], td)]
        out = {"effective_date": sd, "maturity_date": trade.end_date, "fixed_periods": len(fixed), "float_periods": len(flt),
               "first_fixed_amount": calc.swap_fixed(qty, price, fixed[0]["start"], fixed[0]["end"], ccy),
               "first_float_amount": calc.swap_float(qty, fixing, terms.get("irs_spread_bps"), flt[0]["start"], flt[0]["end"], ccy),
               "first_float_fixing_pct": fixing, "first_fixed_payment_date": fixed[0]["pay"],
               "first_float_payment_date": flt[0]["pay"], "settle_amount": Decimal(0), "net": Decimal(0),
               "schedule": {"fixed": [[p["start"].isoformat(), p["end"].isoformat(), p["pay"].isoformat()] for p in fixed],
                            "float": [[p["start"].isoformat(), p["end"].isoformat(), p["pay"].isoformat()] for p in flt]}}
    elif ptype in ("REPO", "REVERSE_REPO"):
        cash = calc.repo_cash(qty, price, terms.get("haircut_pct", 0), ccy)
        interest = calc.repo_interest(cash, terms["repo_rate_pct"], sd, trade.end_date, ccy)
        out = {"cash_amount": cash, "repo_interest": interest, "repurchase_amount": cash + interest,
               "settle_amount": cash, "net": cash}
    elif ptype in ("SEC_LOAN", "SEC_BORROW"):
        coll_ccy = terms.get("collateral_ccy") or ccy
        if coll_ccy == ccy:
            coll = calc.secloan_collateral(qty, price, False, ccy)
        else:
            px = price * ref.usd_per(ccy, td) / ref.usd_per(coll_ccy, td)
            coll = calc.secloan_collateral(qty, px, True, coll_ccy)
        fee = calc.secloan_fee(qty, price, terms["sl_fee_bps"], sd, trade.end_date, ccy)
        out = {"collateral_amount": coll, "collateral_ccy": coll_ccy, "loan_fee": fee,
               "loan_value": q_ccy(qty * price, ccy), "settle_amount": coll, "net": coll}
    return out


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def local_dt(ts: datetime, tz: str) -> datetime:
    return ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz))


def cutoff_missed(ref, conv_id: str | None, release_utc: datetime, settle_date: date) -> bool:
    """R013: an instruction released after the local cut-off on the settlement date misses it."""
    conv = next((c for c in ref.conventions if c.convention_id == conv_id), None)
    if conv is None:
        return False
    local = local_dt(release_utc, conv.timezone)
    hh, mm = map(int, conv.cutoff_local_time.split(":"))
    cutoff = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return local.date() > settle_date or (local.date() == settle_date and local > cutoff)


def next_bday(ref, d: date, ccy: str) -> date:
    return ref.cals.add(d, 1, CCY_CALENDAR[ccy])


_ = timedelta
