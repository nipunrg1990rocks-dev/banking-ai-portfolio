"""Production calculations (Decimal, indexed business-day calendars).

Written independently of testkit/oracle: calendars are pre-indexed lists searched with bisect,
arithmetic is Decimal at 40 significant digits with quantisation only at defined points.
"""

from __future__ import annotations

import bisect
import calendar as _calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext

from cf360.config import defect

MINOR = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0, "CHF": 2, "CAD": 2, "AUD": 2}
CCY_CALENDAR = {"USD": "USNY", "EUR": "EUTA", "GBP": "GBLO", "JPY": "JPTO", "CAD": "CATO", "AUD": "AUSY", "CHF": "CHZU"}
D0 = Decimal(0)
HUNDRED = Decimal(100)
BPS = Decimal(10000)


def dec(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x))


def q_ccy(x: Decimal, ccy: str) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-MINOR[ccy]), rounding=ROUND_HALF_UP)


def q_dp(x: Decimal, dp: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)


def is_ccy_precise(x: Decimal, ccy: str) -> bool:
    return dec(x) == q_ccy(dec(x), ccy)


# --------------------------------------------------------------------------- calendars
class Calendars:
    START, END = date(2014, 1, 1), date(2040, 12, 31)

    def __init__(self, holidays: dict[str, set[date]]):
        self.holidays = holidays
        self._index: dict[tuple, list[date]] = {}

    def _days(self, codes: tuple[str, ...]) -> list[date]:
        key = tuple(sorted(set(codes)))
        if key not in self._index:
            hols = set().union(*(self.holidays.get(c, set()) for c in key))
            out, d = [], self.START
            while d <= self.END:
                if d.weekday() < 5 and d not in hols:
                    out.append(d)
                d += timedelta(days=1)
            self._index[key] = out
        return self._index[key]

    def is_open(self, d: date, *codes: str) -> bool:
        days = self._days(codes)
        i = bisect.bisect_left(days, d)
        return i < len(days) and days[i] == d

    def add(self, d: date, n: int, *codes: str) -> date:
        days = self._days(codes)
        if n == 0:
            return d
        i = bisect.bisect_right(days, d)  # first business day strictly after d
        return days[i + n - 1]

    def following(self, d: date, *codes: str) -> date:
        days = self._days(codes)
        return days[bisect.bisect_left(days, d)]

    def preceding(self, d: date, *codes: str) -> date:
        days = self._days(codes)
        return days[bisect.bisect_right(days, d) - 1]

    def modified_following(self, d: date, *codes: str) -> date:
        f = self.following(d, *codes)
        return f if f.month == d.month else self.preceding(d, *codes)

    def count_between(self, a: date, b: date, *codes: str) -> int:
        """Business days in (a, b]."""
        days = self._days(codes)
        return bisect.bisect_right(days, b) - bisect.bisect_right(days, a)

    def last_of_month(self, y: int, m: int, *codes: str) -> date:
        return self.preceding(date(y, m, _calendar.monthrange(y, m)[1]), *codes)


def add_months(d: date, n: int) -> date:
    total = d.year * 12 + d.month - 1 + n
    y, m = divmod(total, 12)
    m += 1
    return date(y, m, min(d.day, _calendar.monthrange(y, m)[1]))


# --------------------------------------------------------------------------- day counts
def dcf(day_count: str, d1: date, d2: date, period: tuple[date, date] | None = None, freq: int = 1) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        if day_count == "30/360":
            y1, m1, dd1 = d1.year, d1.month, d1.day
            y2, m2, dd2 = d2.year, d2.month, d2.day
            dd1 = min(dd1, 30)
            if dd2 == 31 and dd1 == 30:
                dd2 = 30
            return Decimal(360 * (y2 - y1) + 30 * (m2 - m1) + (dd2 - dd1)) / 360
        actual = Decimal((d2 - d1).days)
        if day_count == "ACT/360":
            return actual / 360
        if day_count == "ACT/365F":
            return actual / 365
        if day_count == "ACT/ACT":  # ICMA: fraction of the regular period, divided by frequency
            p0, p1 = period
            return actual / Decimal((p1 - p0).days) / freq
    raise ValueError(day_count)


def coupon_schedule(issue: date, maturity: date, freq: int) -> list[date]:
    months = 12 // freq
    eom = maturity.day == _calendar.monthrange(maturity.year, maturity.month)[1]
    out, k = [], 0
    while True:
        d = add_months(maturity, -months * k)
        if eom:
            d = date(d.year, d.month, _calendar.monthrange(d.year, d.month)[1])
        out.append(d)
        if d <= issue:
            break
        k += 1
    return out[::-1]


def accrued_per_100(bond: dict, settle: date, cals: Calendars | None = None, ccy: str = "USD") -> Decimal:
    freq = int(bond["coupon_frequency"])
    sched = coupon_schedule(bond["issue_date"], bond["maturity_date"], freq)
    i = bisect.bisect_right(sched, settle)
    prev, nxt = sched[i - 1], sched[i]
    c = dec(bond["coupon_rate_pct"])
    dc = bond["day_count"]
    if dc == "30/360" and defect("D2"):
        dc = "ACT/360"  # injected defect D2
    with localcontext() as ctx:
        ctx.prec = 40
        if dc == "ACT/ACT":
            ai = c * dcf(dc, prev, settle, (prev, nxt), freq)
        else:
            ai = c * dcf(dc, prev, settle)
        exdiv = int(bond.get("ex_dividend_bdays") or 0)
        if exdiv and cals is not None:
            ex_date = nxt
            for _ in range(exdiv):
                ex_date = cals.preceding(ex_date - timedelta(days=1), CCY_CALENDAR[ccy])
            if settle >= ex_date:
                ai = -(c / freq) * Decimal((nxt - settle).days) / Decimal((nxt - prev).days)
        return ai


# --------------------------------------------------------------------------- allocation
def split_amount(total: Decimal, qtys: list[Decimal], ccy: str) -> list[Decimal]:
    block = sum(qtys)
    with localcontext() as ctx:
        ctx.prec = 40
        parts = [q_ccy(total * q / block, ccy) for q in qtys]
    if defect("D1"):
        return parts  # injected defect D1: residual dropped
    residual = total - sum(parts)
    if residual:
        largest = max(range(len(qtys)), key=lambda i: (qtys[i], -i))
        parts[largest] += residual
    return parts


# --------------------------------------------------------------------------- products
def equity_economics(side, qty, price, ccy, commission_bps, min_commission_usd, usd_per_unit, exch_bps, stamp_bps):
    qty, price = dec(qty), dec(price)
    with localcontext() as ctx:
        ctx.prec = 40
        raw = qty * price
        gross = q_ccy(raw, ccy)
        min_ccy = dec(min_commission_usd) / dec(usd_per_unit)
        commission = q_ccy(max(raw * dec(commission_bps) / BPS, min_ccy), ccy)
        fee = q_ccy(raw * dec(exch_bps) / BPS, ccy)
        tax = q_ccy(raw * dec(stamp_bps) / BPS, ccy) if side == "BUY" else q_ccy(D0, ccy)
    sign = 1 if side == "BUY" else -1
    net = gross + sign * (commission + fee + tax)
    return {"gross": gross, "commission": commission, "exchange_fee": fee, "tax": tax, "net": net}


def bond_economics(nominal, clean, ai_per_100: Decimal, ccy):
    n = dec(nominal)
    with localcontext() as ctx:
        ctx.prec = 40
        principal = q_ccy(n * dec(clean) / HUNDRED, ccy)
        accrued = q_ccy(n * ai_per_100 / HUNDRED, ccy)
    return {"principal": principal, "accrued": accrued, "gross": principal + accrued, "net": principal + accrued,
            "dirty_price": q_dp(dec(clean) + ai_per_100, 6), "accrued_per_100": q_dp(ai_per_100, 8)}


def fx_value_date(cals: Calendars, td: date, base: str, quote: str, months: int | None) -> date:
    lag = 1 if {base, quote} == {"USD", "CAD"} else 2
    codes = (CCY_CALENDAR[base],) if defect("D3") else (CCY_CALENDAR[base], CCY_CALENDAR[quote])
    spot = cals.add(td, lag, *codes)
    if not months:
        return spot
    if spot == cals.last_of_month(spot.year, spot.month, *codes):
        tgt = add_months(date(spot.year, spot.month, 1), months)
        return cals.last_of_month(tgt.year, tgt.month, *codes)
    return cals.modified_following(add_months(spot, months), *codes)


def fx_counter(base_amount, rate, quote_ccy) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        return q_ccy(dec(base_amount) * dec(rate), quote_ccy)


def fx_outright(spot, points, quote_ccy) -> Decimal:
    pip = Decimal(100) if quote_ccy == "JPY" else BPS
    return dec(spot) + dec(points) / pip


def futures_vm(side, contracts, prev_price, settle_price, mult, ccy) -> Decimal:
    s = 1 if side == "BUY" else -1
    return q_ccy(s * dec(contracts) * (dec(settle_price) - dec(prev_price)) * dec(mult), ccy)


def option_premium(contracts, premium, mult, ccy) -> Decimal:
    return q_ccy(dec(contracts) * dec(premium) * dec(mult), ccy)


def option_in_the_money(option_type, strike, und, threshold=Decimal("0.01")) -> tuple[bool, Decimal]:
    intrinsic = dec(und) - dec(strike) if option_type == "CALL" else dec(strike) - dec(und)
    return intrinsic >= threshold, max(intrinsic, D0)


def swap_periods(cals: Calendars, eff: date, mat: date, months: int, cal_code: str):
    out, k, start = [], 1, eff
    while True:
        end = min(add_months(eff, months * k), mat)
        out.append({"start": start, "end": end, "pay": cals.modified_following(end, cal_code)})
        if end == mat:
            return out
        start, k = end, k + 1


def swap_fixed(notional, rate_pct, start, end, ccy) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        return q_ccy(dec(notional) * dec(rate_pct) / HUNDRED * dcf("30/360", start, end), ccy)


def swap_float(notional, fixing_pct, spread_bps, start, end, ccy) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        r = dec(fixing_pct) / HUNDRED + dec(spread_bps or 0) / BPS
        return q_ccy(dec(notional) * r * dcf("ACT/360", start, end), ccy)


REPO_BASIS = {"USD": 360, "EUR": 360, "CHF": 360, "GBP": 365, "JPY": 365, "CAD": 365, "AUD": 365}


def repo_cash(nominal, dirty, haircut_pct, ccy) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        return q_ccy(dec(nominal) * dec(dirty) / HUNDRED * (1 - dec(haircut_pct) / HUNDRED), ccy)


def repo_interest(cash, rate_pct, start: date, end: date, ccy) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        return q_ccy(dec(cash) * dec(rate_pct) / HUNDRED * Decimal((end - start).days) / REPO_BASIS[ccy], ccy)


def repo_margin_call(cash, collateral_mv, haircut_pct, mta, ccy) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        deficit = dec(cash) / (1 - dec(haircut_pct) / HUNDRED) - dec(collateral_mv)
    return q_ccy(deficit, ccy) if deficit > dec(mta) else q_ccy(D0, ccy)


def secloan_collateral(qty, price_in_coll_ccy, cross_ccy: bool, ccy) -> Decimal:
    pct = Decimal("1.05") if cross_ccy else Decimal("1.02")
    with localcontext() as ctx:
        ctx.prec = 40
        return q_ccy(dec(qty) * dec(price_in_coll_ccy) * pct, ccy)


def secloan_fee(qty, price, fee_bps, start, end, ccy) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        return q_ccy(dec(qty) * dec(price) * dec(fee_bps) / BPS * Decimal((end - start).days) / 360, ccy)


def amount_tolerance(ccy: str) -> Decimal:
    return Decimal(100) if ccy == "JPY" else Decimal(1)
