"""Synthetic market data: closing prices, FX rates, forward points and rate fixings."""

from __future__ import annotations

import random
from datetime import date, timedelta

from datagen.calendars import CCY_CALENDAR
from datagen.refdata import MARKET

PRICE_START = date(2026, 8, 3)
PRICE_END = date(2026, 11, 13)
STALE_FROM = {"EQ011": date(2026, 10, 23), "BD040": date(2026, 10, 23)}  # vendor feed stops (stale price tests)

USD_PER = {"USD": 1.0, "EUR": 1.1630, "GBP": 1.3420, "JPY": 1 / 151.20, "CAD": 1 / 1.3815, "AUD": 0.6610,
           "CHF": 1 / 0.8010}
FIXINGS = {"SOFR-SIM": ("USD", 3.86), "ESTR-SIM": ("EUR", 1.92), "SONIA-SIM": ("GBP", 3.71), "TONA-SIM": ("JPY", 0.48)}
CCY_RATE = {"USD": 3.86, "EUR": 1.92, "GBP": 3.71, "JPY": 0.48, "CAD": 2.70, "AUD": 3.55, "CHF": 0.20}


def days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def open_on(d: date, hol: set[date]) -> bool:
    return d.weekday() < 5 and d not in hol


def build_prices(rng: random.Random, instruments: dict, seed_px: dict, hol: dict) -> list[dict]:
    rows = []
    common = {r["instrument_id"]: r for r in instruments["Instruments"]}
    for iid, start_px in seed_px.items():
        inst = common[iid]
        cal = MARKET[inst["market"]][1]
        ptype = inst["product_type"]
        vol = {"EQUITY": 0.012, "ETF": 0.008, "GOVT_BOND": 0.0015, "CORP_BOND": 0.002, "FUTURE": 0.009,
               "OPTION": 0.06}[ptype]
        px = float(start_px)
        for d in days(PRICE_START, PRICE_END):
            if not open_on(d, hol[cal]):
                continue
            if iid in STALE_FROM and d >= STALE_FROM[iid]:
                break
            px = px * (1 + rng.gauss(0.0002, vol))
            if ptype == "OPTION":
                px = max(px, 0.05)
            dp = 0 if inst["currency"] == "JPY" and ptype in ("EQUITY", "ETF") else (3 if "BOND" in ptype else 2)
            if ptype == "FUTURE":
                dp = 2
            rows.append({"instrument_id": iid, "price_date": d, "close_price": round(px, dp),
                         "price_type": {"GOVT_BOND": "CLEAN_PCT", "CORP_BOND": "CLEAN_PCT", "FUTURE": "SETTLEMENT",
                                        "OPTION": "PREMIUM"}.get(ptype, "CLOSE"),
                         "currency": inst["currency"], "source": "PRICE-SIM",
                         "source_record_id": f"PX-{iid}-{d:%Y%m%d}"})
    return rows


def build_fx(rng: random.Random, hol: dict) -> tuple[list[dict], dict]:
    rows, table = [], {}
    level = dict(USD_PER)
    for d in days(PRICE_START, PRICE_END):
        if d.weekday() >= 5:
            continue
        for ccy in level:
            if ccy != "USD":
                level[ccy] *= 1 + rng.gauss(0, 0.004)
            v = round(level[ccy], 8) if ccy != "USD" else 1.0
            table[(ccy, d)] = v
            rows.append({"currency": ccy, "rate_date": d, "usd_per_unit": v, "source": "FX-SIM",
                         "source_record_id": f"FX-{ccy}-{d:%Y%m%d}"})
    return rows, table


def build_fixings(rng: random.Random, hol: dict) -> tuple[list[dict], dict]:
    rows, table = [], {}
    for idx, (ccy, lvl) in FIXINGS.items():
        cal = hol[CCY_CALENDAR[ccy]]
        for d in days(PRICE_START, PRICE_END):
            if not open_on(d, cal):
                continue
            lvl = max(0.0, lvl + rng.gauss(0, 0.01))
            table[(idx, d)] = round(lvl, 4)
            rows.append({"rate_index": idx, "fixing_date": d, "rate_pct": round(lvl, 4), "source": "FIX-SIM",
                         "source_record_id": f"FIX-{idx}-{d:%Y%m%d}"})
    return rows, table


def fx_pair_rate(table: dict, base: str, quote: str, d: date, dp: int) -> float:
    return round(table[(base, d)] / table[(quote, d)], dp)


def build_forward_points(fx_table: dict, pairs: list[dict]) -> list[dict]:
    rows = []
    dates = sorted({d for (_, d) in fx_table})
    for p in pairs:
        b, q, pip = p["base_ccy"], p["quote_ccy"], p["pip_factor"]
        for d in dates:
            spot = fx_table[(b, d)] / fx_table[(q, d)]
            for m in (1, 2, 3, 6):
                pts = spot * (CCY_RATE[q] - CCY_RATE[b]) / 100 * (30 * m) / 360 * pip
                rows.append({"pair": b + q, "rate_date": d, "tenor_months": m, "forward_points": round(pts, 2),
                             "source": "FWD-SIM", "source_record_id": f"FWD-{b}{q}-{m}M-{d:%Y%m%d}"})
    return rows
