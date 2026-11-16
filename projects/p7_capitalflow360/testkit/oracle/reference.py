"""Independent reference calculations (the test oracle).

This module is deliberately written *separately* from the platform's production code in
``backend/cf360``. It must never import from it (``tests/test_independence.py`` enforces that),
and where possible it uses a different technique:

* exact rational arithmetic (``fractions.Fraction``) instead of ``Decimal`` until the final
  rounding step;
* day-by-day iteration over calendars instead of closed-form date arithmetic;
* explicit look-up tables written out by hand.

If the production code and this oracle agree, both are probably right; if they disagree, a
test fails and somebody has to look. That is the point.
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

# ISO 4217 minor units for the currencies used in the project.
MINOR_UNITS = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0, "CHF": 2, "CAD": 2, "AUD": 2}


def to_frac(x) -> Fraction:
    if isinstance(x, Fraction):
        return x
    return Fraction(str(x))


def round_ccy(amount, ccy: str) -> Decimal:
    """Round to the currency's minor unit, half away from zero (project convention)."""
    q = Decimal(1).scaleb(-MINOR_UNITS[ccy])
    f = to_frac(amount)
    d = Decimal(f.numerator) / Decimal(f.denominator) if f.denominator != 1 else Decimal(f.numerator)
    # Decimal division above is at 28 significant digits which is ample for these magnitudes.
    return d.quantize(q, rounding=ROUND_HALF_UP)


def round_dp(amount, dp: int) -> Decimal:
    f = to_frac(amount)
    d = Decimal(f.numerator) / Decimal(f.denominator)
    return d.quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------------------
# Calendars (iterative)
# --------------------------------------------------------------------------------------

def is_business_day(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def add_business_days(d: date, n: int, holiday_sets: list[set[date]]) -> date:
    """Step one calendar day at a time; a day counts only if open on every calendar."""
    cur = d
    counted = 0
    while counted < n:
        cur = cur + timedelta(days=1)
        if all(is_business_day(cur, h) for h in holiday_sets):
            counted += 1
    return cur


def roll_following(d: date, holiday_sets: list[set[date]]) -> date:
    cur = d
    while not all(is_business_day(cur, h) for h in holiday_sets):
        cur += timedelta(days=1)
    return cur


def roll_preceding(d: date, holiday_sets: list[set[date]]) -> date:
    cur = d
    while not all(is_business_day(cur, h) for h in holiday_sets):
        cur -= timedelta(days=1)
    return cur


def roll_modified_following(d: date, holiday_sets: list[set[date]]) -> date:
    f = roll_following(d, holiday_sets)
    if f.month != d.month:
        return roll_preceding(d, holiday_sets)
    return f


def add_months_raw(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    last = _cal.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def last_business_day_of_month(y: int, m: int, holiday_sets) -> date:
    d = date(y, m, _cal.monthrange(y, m)[1])
    return roll_preceding(d, holiday_sets)


# --------------------------------------------------------------------------------------
# Day-count fractions
# --------------------------------------------------------------------------------------

def days_30_360_us(d1: date, d2: date) -> int:
    """30/360 'bond basis' (ISDA 2006 s4.16(f)) without the February end-of-month adjustment."""
    D1, D2 = d1.day, d2.day
    if D1 == 31:
        D1 = 30
    if D2 == 31 and D1 >= 30:
        D2 = 30
    return 360 * (d2.year - d1.year) + 30 * (d2.month - d1.month) + (D2 - D1)


def actual_days(d1: date, d2: date) -> int:
    n = 0
    cur = d1
    while cur < d2:
        cur += timedelta(days=1)
        n += 1
    return n


# --------------------------------------------------------------------------------------
# Bonds
# --------------------------------------------------------------------------------------

def coupon_dates(maturity: date, freq: int, issue: date) -> list[date]:
    """Unadjusted coupon schedule generated backwards from maturity (regular periods)."""
    step = 12 // freq
    out = [maturity]
    k = 1
    while True:
        d = add_months_raw(maturity, -step * k)
        # keep end-of-month alignment if maturity is month end
        if maturity.day == _cal.monthrange(maturity.year, maturity.month)[1]:
            d = date(d.year, d.month, _cal.monthrange(d.year, d.month)[1])
        if d <= issue:
            out.append(d)
            break
        out.append(d)
        k += 1
    return sorted(out)


def prev_next_coupon(settle: date, maturity: date, freq: int, issue: date) -> tuple[date, date]:
    sched = coupon_dates(maturity, freq, issue)
    prev = None
    for d in sched:
        if d <= settle:
            prev = d
        else:
            return prev, d
    raise ValueError("settlement after maturity")


def accrued_interest_per_100(settle: date, coupon_rate, freq: int, day_count: str,
                             maturity: date, issue: date, ex_div_bdays: int = 0,
                             holidays: set[date] | None = None) -> Fraction:
    """Accrued interest per 100 nominal. ``coupon_rate`` is in percent (e.g. 4.25)."""
    c = to_frac(coupon_rate)
    prev, nxt = prev_next_coupon(settle, maturity, freq, issue)
    period_coupon = c / freq
    if day_count == "30/360":
        accrued = c * Fraction(days_30_360_us(prev, settle), 360)
    elif day_count == "ACT/ACT":
        accrued = period_coupon * Fraction(actual_days(prev, settle), actual_days(prev, nxt))
    elif day_count == "ACT/365F":
        accrued = c * Fraction(actual_days(prev, settle), 365)
    elif day_count == "ACT/360":
        accrued = c * Fraction(actual_days(prev, settle), 360)
    else:
        raise ValueError(day_count)
    if ex_div_bdays:
        # Ex-dividend period (UK gilts): from ex-date the buyer does not receive the next coupon,
        # so accrued interest is negative: -(days from settlement to next coupon).
        ex_date = nxt
        counted = 0
        while counted < ex_div_bdays:
            ex_date -= timedelta(days=1)
            if is_business_day(ex_date, holidays or set()):
                counted += 1
        if settle >= ex_date:
            accrued = -period_coupon * Fraction(actual_days(settle, nxt), actual_days(prev, nxt))
    return accrued


def bond_consideration(nominal, clean_price, accrued_per_100: Fraction, ccy: str) -> dict:
    n = to_frac(nominal)
    principal = n * to_frac(clean_price) / 100
    accrued_amt = n * accrued_per_100 / 100
    return {
        "principal": round_ccy(principal, ccy),
        "accrued": round_ccy(accrued_amt, ccy),
        "gross": round_ccy(principal, ccy) + round_ccy(accrued_amt, ccy),
        "dirty_price": round_dp(to_frac(clean_price) + accrued_per_100, 6),
    }


# --------------------------------------------------------------------------------------
# Equities: commissions, fees, taxes
# --------------------------------------------------------------------------------------

def equity_charges(side: str, qty, price, multiplier, ccy: str, commission_bps, min_commission,
                   exchange_fee_bps, stamp_duty_bps) -> dict:
    gross_f = to_frac(qty) * to_frac(price) * to_frac(multiplier)
    gross = round_ccy(gross_f, ccy)
    comm_raw = gross_f * to_frac(commission_bps) / 10000
    commission = round_ccy(max(comm_raw, to_frac(min_commission)), ccy)
    exch = round_ccy(gross_f * to_frac(exchange_fee_bps) / 10000, ccy)
    tax = round_ccy(gross_f * to_frac(stamp_duty_bps) / 10000, ccy) if side == "BUY" else Decimal(0)
    if side == "BUY":
        net = gross + commission + exch + tax
    else:
        net = gross - commission - exch - tax
    return {"gross": gross, "commission": commission, "exchange_fee": exch, "tax": tax,
            "net": net}


def vwap(fills: list[tuple]) -> Fraction:
    q = sum(to_frac(f[0]) for f in fills)
    v = sum(to_frac(f[0]) * to_frac(f[1]) for f in fills)
    return v / q


def allocate(net_total: Decimal, alloc_qtys: list, total_qty, ccy: str) -> list[Decimal]:
    """Pro-rata allocation of a net amount; the rounding residual goes to the largest allocation
    (first one on ties) so the allocations sum exactly to the block amount."""
    parts = [round_ccy(to_frac(net_total) * to_frac(q) / to_frac(total_qty), ccy) for q in alloc_qtys]
    residual = net_total - sum(parts)
    if residual != 0:
        biggest = max(range(len(alloc_qtys)), key=lambda i: (to_frac(alloc_qtys[i]), -i))
        parts[biggest] += residual
    return parts


# --------------------------------------------------------------------------------------
# FX
# --------------------------------------------------------------------------------------

def fx_spot_date(trade_date: date, base: str, quote: str, cal: dict[str, set[date]]) -> date:
    lag = 1 if {base, quote} == {"USD", "CAD"} else 2
    return add_business_days(trade_date, lag, [cal[base], cal[quote]])


def fx_forward_date(trade_date: date, base: str, quote: str, months: int, cal) -> date:
    spot = fx_spot_date(trade_date, base, quote, cal)
    hs = [cal[base], cal[quote]]
    if spot == last_business_day_of_month(spot.year, spot.month, hs):
        target = add_months_raw(date(spot.year, spot.month, 1), months)
        return last_business_day_of_month(target.year, target.month, hs)
    return roll_modified_following(add_months_raw(spot, months), hs)


def fx_counter_amount(base_amount, rate, quote_ccy: str) -> Decimal:
    return round_ccy(to_frac(base_amount) * to_frac(rate), quote_ccy)


def fx_forward_rate(spot_rate, points, quote_ccy: str) -> Fraction:
    pip = Fraction(1, 100) if quote_ccy == "JPY" else Fraction(1, 10000)
    return to_frac(spot_rate) + to_frac(points) * pip


# --------------------------------------------------------------------------------------
# Listed derivatives
# --------------------------------------------------------------------------------------

def futures_variation_margin(side: str, contracts, ref_price, settle_price, multiplier, ccy) -> Decimal:
    sign = 1 if side == "BUY" else -1
    return round_ccy(sign * to_frac(contracts) * (to_frac(settle_price) - to_frac(ref_price)) * to_frac(multiplier), ccy)


def option_premium(contracts, premium, multiplier, ccy) -> Decimal:
    return round_ccy(to_frac(contracts) * to_frac(premium) * to_frac(multiplier), ccy)


def option_exercise(option_type: str, strike, underlying_price, threshold="0.01") -> bool:
    s, k = to_frac(underlying_price), to_frac(strike)
    intrinsic = (s - k) if option_type == "CALL" else (k - s)
    return intrinsic >= to_frac(threshold)


def option_cash_settlement(option_type, strike, underlying_price, contracts, multiplier, ccy) -> Decimal:
    s, k = to_frac(underlying_price), to_frac(strike)
    intrinsic = max(Fraction(0), (s - k) if option_type == "CALL" else (k - s))
    return round_ccy(intrinsic * to_frac(contracts) * to_frac(multiplier), ccy)


# --------------------------------------------------------------------------------------
# Interest rate swaps (simplified: term fixings, no compounding)
# --------------------------------------------------------------------------------------

def swap_schedule(effective: date, maturity: date, months: int, holiday_sets) -> list[tuple[date, date, date]]:
    """(accrual_start, accrual_end, payment_date) with unadjusted accrual and modified-following
    payment dates. Generated forwards from the effective date."""
    periods = []
    k = 1
    start = effective
    while True:
        end_unadj = add_months_raw(effective, months * k)
        if end_unadj >= maturity:
            end_unadj = maturity
        pay = roll_modified_following(end_unadj, holiday_sets)
        periods.append((start, end_unadj, pay))
        if end_unadj == maturity:
            break
        start = end_unadj
        k += 1
    return periods


def swap_fixed_amount(notional, rate_pct, start: date, end: date, ccy) -> Decimal:
    return round_ccy(to_frac(notional) * to_frac(rate_pct) / 100 * Fraction(days_30_360_us(start, end), 360), ccy)


def swap_float_amount(notional, fixing_pct, spread_bps, start: date, end: date, ccy) -> Decimal:
    r = to_frac(fixing_pct) / 100 + to_frac(spread_bps) / 10000
    return round_ccy(to_frac(notional) * r * Fraction(actual_days(start, end), 360), ccy)


# --------------------------------------------------------------------------------------
# Financing
# --------------------------------------------------------------------------------------

REPO_BASIS = {"USD": 360, "EUR": 360, "GBP": 365, "JPY": 365, "CHF": 360, "CAD": 365, "AUD": 365}


def repo_cash_amount(nominal, dirty_price_per_100, haircut_pct, ccy) -> Decimal:
    mv = to_frac(nominal) * to_frac(dirty_price_per_100) / 100
    return round_ccy(mv * (1 - to_frac(haircut_pct) / 100), ccy)


def repo_interest(cash, rate_pct, start: date, end: date, ccy) -> Decimal:
    return round_ccy(to_frac(cash) * to_frac(rate_pct) / 100 * Fraction(actual_days(start, end), REPO_BASIS[ccy]), ccy)


def repo_margin_call(cash_outstanding, collateral_mv, haircut_pct, mta, ccy) -> Decimal:
    """Collateral needed so that MV*(1-h) covers the cash; returns the call amount (in
    collateral value) or 0 if the deficit does not exceed the minimum transfer amount."""
    required = to_frac(cash_outstanding) / (1 - to_frac(haircut_pct) / 100)
    deficit = required - to_frac(collateral_mv)
    if deficit > to_frac(mta):
        return round_ccy(deficit, ccy)
    return Decimal(0).quantize(Decimal(1).scaleb(-MINOR_UNITS[ccy]))


def secloan_fee(qty, price, fee_bps, start: date, end: date, ccy) -> Decimal:
    value = to_frac(qty) * to_frac(price)
    return round_ccy(value * to_frac(fee_bps) / 10000 * Fraction(actual_days(start, end), 360), ccy)


def secloan_collateral_required(qty, price, same_currency: bool, ccy) -> Decimal:
    pct = Fraction(102, 100) if same_currency else Fraction(105, 100)
    return round_ccy(to_frac(qty) * to_frac(price) * pct, ccy)


# --------------------------------------------------------------------------------------
# Matching tolerance and reconciliation
# --------------------------------------------------------------------------------------

AMOUNT_TOLERANCE = {"JPY": Fraction(100), "default": Fraction(1)}


def within_amount_tolerance(ours, theirs, ccy) -> bool:
    tol = AMOUNT_TOLERANCE.get(ccy, AMOUNT_TOLERANCE["default"])
    return abs(to_frac(ours) - to_frac(theirs)) <= tol


def to_usd(amount, ccy: str, usd_per_unit) -> Decimal:
    return round_ccy(to_frac(amount) * to_frac(usd_per_unit), "USD")
