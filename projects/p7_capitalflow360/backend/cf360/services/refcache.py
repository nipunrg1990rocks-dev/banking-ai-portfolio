"""In-memory snapshot of reference data, rebuilt when reference data changes."""

from __future__ import annotations

import bisect
import threading
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cf360 import models as m
from cf360.rules.calc import Calendars

_lock = threading.Lock()
_cache: RefData | None = None
_generation = 0


def invalidate() -> None:
    global _cache, _generation
    with _lock:
        _cache = None
        _generation += 1


def get(session: Session) -> RefData:
    global _cache
    with _lock:
        if _cache is None:
            _cache = RefData(session)
        return _cache


class RefData:
    def __init__(self, s: Session):
        hol = defaultdict(set)
        for h in s.scalars(select(m.Holiday)):
            hol[h.calendar_code].add(h.holiday_date)
        self.cals = Calendars(dict(hol))
        self.markets = {x.market: x for x in s.scalars(select(m.Market))}
        self.conventions = list(s.scalars(select(m.SettlementConvention)))
        self.instruments = {x.instrument_id: x for x in s.scalars(select(m.Instrument))}
        self.counterparties = {x.counterparty_id: x for x in s.scalars(select(m.Counterparty))}
        self.schedules = defaultdict(list)
        for x in s.scalars(select(m.CommissionSchedule)):
            self.schedules[(x.account_id, x.product_group)].append(x)
        self.prices: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
        for p in s.execute(select(m.Price.instrument_id, m.Price.price_date, m.Price.close_price).order_by(
                m.Price.instrument_id, m.Price.price_date)):
            self.prices[p[0]].append((p[1], p[2]))
        self.fx: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
        for r in s.execute(select(m.FxRate.currency, m.FxRate.rate_date, m.FxRate.usd_per_unit).order_by(
                m.FxRate.currency, m.FxRate.rate_date)):
            self.fx[r[0]].append((r[1], r[2]))
        self.fixings = {(r.rate_index, r.fixing_date): r.rate_pct for r in s.scalars(select(m.RateFixing))}
        self.config = list(s.scalars(select(m.ConfigParam)))

    # ---- prices / rates
    def price_on_or_before(self, iid: str, d: date) -> tuple[date, Decimal] | None:
        series = self.prices.get(iid) or []
        i = bisect.bisect_right(series, (d, Decimal("Infinity")))
        return series[i - 1] if i else None

    def price_on(self, iid: str, d: date) -> Decimal | None:
        p = self.price_on_or_before(iid, d)
        return p[1] if p and p[0] == d else None

    def price_before(self, iid: str, d: date) -> tuple[date, Decimal] | None:
        return self.price_on_or_before(iid, d - timedelta(days=1))

    def usd_per(self, ccy: str, d: date) -> Decimal:
        if ccy == "USD":
            return Decimal(1)
        series = self.fx.get(ccy) or []
        i = bisect.bisect_right(series, (d, Decimal("Infinity")))
        if not i or (d - series[i - 1][0]).days > 5:
            raise LookupError(f"E_FX_RATE_MISSING {ccy} {d}")
        return series[i - 1][1]

    def param(self, name: str, on: date, scope: str = "*", default: str | None = None) -> str | None:
        best = None
        for c in self.config:
            if c.param == name and c.scope in (scope, "*") and c.effective_from <= on and (c.effective_to is None or on <= c.effective_to):
                if best is None or (c.scope != "*" and best.scope == "*") or c.effective_from > best.effective_from:
                    best = c
        return best.value if best else default

    def schedule(self, account_id: str, group: str, on: date):
        for sch in self.schedules.get((account_id, group), []):
            if sch.effective_from <= on and (sch.effective_to is None or on <= sch.effective_to):
                return sch
        return None

    def convention(self, product_type: str, market: str, on: date):
        rows = [c for c in self.conventions if product_type in c.product_types.split("|")
                and c.effective_from <= on and (c.effective_to is None or on <= c.effective_to)]
        exact = [c for c in rows if c.market == market]
        return (exact or [c for c in rows if c.market == "*"] or [None])[0]
