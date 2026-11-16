"""Negative-test variants, kept OUT of the 500-trade baseline.

Each variant is a copy of a baseline trade with one controlled defect. The label (which rule
should catch it, with which error code, at which layer) goes to data/oracle/negative_labels.csv
and is never part of the input file.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

from testkit.oracle import reference as ref

# (code, description, rule_id, expected_error, layer, per_type)
TYPES = [
    ("N01", "Unknown instrument", "DQ02", "E_UNKNOWN_INSTRUMENT", "DQ_QUARANTINE", 6),
    ("N02", "Unknown account", "DQ02", "E_UNKNOWN_ACCOUNT", "DQ_QUARANTINE", 6),
    ("N03", "Trade date on a market holiday", "R010", "E_TRADE_DATE_HOLIDAY", "API_REJECT", 6),
    ("N04", "Settlement date not per convention", "R011", "E_SETTLE_DATE_MISMATCH", "API_REJECT", 8),
    ("N05", "Duplicate booking (same source reference)", "R029", "E_DUPLICATE", "API_REJECT", 8),
    ("N06", "Quantity not a multiple of the lot size", "R007", "E_LOT_SIZE", "API_REJECT", 6),
    ("N07", "Price outside collar", "R008", "E_PRICE_COLLAR", "API_REJECT", 8),
    ("N08", "JPY amount with decimals", "R028", "E_CCY_PRECISION", "API_REJECT", 5),
    ("N09", "FX counter amount inconsistent with rate", "R042", "E_FX_COUNTER_MISMATCH", "API_REJECT", 6),
    ("N10", "Suspended client", "R001", "E_CLIENT_INACTIVE", "API_REJECT", 5),
    ("N11", "Client KYC expired on trade date", "R002", "E_KYC_EXPIRED", "API_REJECT", 5),
    ("N12", "Retail client trading derivatives", "R004", "E_CLIENT_CATEGORY", "API_REJECT", 5),
    ("N13", "Product not permitted for account", "R003", "E_PRODUCT_NOT_PERMITTED", "API_REJECT", 6),
    ("N14", "Trade after contract last trading day", "R052", "E_CONTRACT_EXPIRED", "API_REJECT", 5),
    ("N15", "Stale reference price", "R009", "E_STALE_PRICE", "API_REJECT", 5),
    ("N16", "Negative quantity", "R007", "E_QTY_INVALID", "DQ_QUARANTINE", 5),
    ("N17", "Missing required field", "DQ01", "E_REQUIRED_FIELD", "DQ_QUARANTINE", 6),
    ("N18", "Bond nominal below minimum denomination / increment", "R034", "E_DENOMINATION", "API_REJECT", 6),
    ("N19", "Exposure limit breach", "R006", "E_LIMIT_BREACH", "API_REJECT", 5),
    ("N20", "Swap maturity not after effective date", "R063", "E_SWAP_DATES", "API_REJECT", 4),
    ("N21", "Late-arriving valid trade", "R095", None, "ACCEPT_LATE", 8),
    ("N22", "Settlement currency without a matching SSI", "R081", "E_SSI_MISSING", "ENRICHMENT_EXCEPTION", 5),
    ("N23", "Trade date stated in UTC instead of market local date", "R014", "E_TRADE_DATE_TZ", "API_REJECT", 4),
]


USD_PER_UNIT_FLOOR = {"USD": Decimal("1"), "EUR": Decimal("1.05"), "GBP": Decimal("1.25"), "JPY": Decimal("0.006"),
                      "CAD": Decimal("0.68"), "AUD": Decimal("0.60"), "CHF": Decimal("1.10")}


def build(rng, tables, clients, accounts, inst, hol, ctx) -> dict:
    trades = tables["Trades"]
    by_group = {}
    for t in trades:
        by_group.setdefault(t["product_group"], []).append(t)
    labels, rows = [], []
    n = 0
    eq_lot100 = [t for t in trades if t["market"] == "XTKS" and t["product_type"] == "EQUITY"]
    hol_xnys = sorted(d for d in hol["XNYS"] if date(2026, 9, 1) <= d <= date(2026, 12, 31))
    derivs = {r["instrument_id"]: r for r in inst["Derivative_Details"]}
    retail = [c["client_id"] for c in clients if c["client_category"] == "RETAIL" and c["status"] == "ACTIVE"]
    acct_by_client = {}
    for a in accounts:
        acct_by_client.setdefault(a["client_id"], []).append(a)

    def emit(code, base, changes, note=""):
        nonlocal n
        n += 1
        r = dict(base)
        r.update(changes)
        r["trade_id"] = f"NEG{n:05d}"
        r["source_record_id"] = f"NEG-{n}"
        r["ingest_batch"] = "NEGATIVE-001" if code != "N21" else "BASELINE-002-LATE"
        if code != "N05":
            r["source_trade_ref"] = f"NEG-REF-{n}"
        r["negative_case_id"] = f"{code}-{n:04d}"
        rows.append(r)
        t = next(x for x in TYPES if x[0] == code)
        labels.append({"negative_case_id": r["negative_case_id"], "trade_id": r["trade_id"], "base_trade_id": base["trade_id"],
                       "defect_type": code, "description": t[1], "rule_id": t[2], "expected_error": t[3],
                       "expected_layer": t[4], "note": note})

    for code, _desc, _rule, _err, _layer, k in TYPES:
        for _ in range(k):
            if code == "N01":
                emit(code, rng.choice(trades), {"instrument_id": "EQ999"})
            elif code == "N02":
                emit(code, rng.choice(trades), {"account_id": "AC9999"})
            elif code == "N03":
                b = rng.choice([t for t in by_group["EQUITY"] if t["market"] == "XNYS"])
                h = rng.choice(hol_xnys)
                emit(code, b, {"trade_date": h, "settlement_date": ref.add_business_days(h, 1, [hol["XNYS"], hol["USNY"]])},
                     note=f"{h} is a XNYS holiday")
            elif code == "N04":
                b = rng.choice(by_group["EQUITY"] + by_group["BOND"])
                emit(code, b, {"settlement_date": b["settlement_date"] + timedelta(days=rng.choice([1, 7]))})
            elif code == "N05":
                emit(code, rng.choice(trades), {})
            elif code == "N06":
                b = rng.choice(eq_lot100)
                emit(code, b, {"quantity": b["quantity"] + 37})
            elif code == "N07":
                b = rng.choice(by_group["EQUITY"] + by_group["BOND"])
                factor = rng.choice([1.15, 0.8, 1.25]) if b["product_group"] == "EQUITY" else None
                emit(code, b, {"price": round(b["price"] * factor, 4) if factor else round(b["price"] + rng.choice([7, -9]), 4)})
            elif code == "N08":
                b = rng.choice([t for t in by_group["FX"] if t["fx_counter_ccy"] == "JPY"])
                emit(code, b, {"fx_counter_amount": b["fx_counter_amount"] + 0.37})
            elif code == "N09":
                b = rng.choice(by_group["FX"])
                emit(code, b, {"fx_counter_amount": round(b["fx_counter_amount"] * 1.01, 2)})
            elif code == "N10":
                b = rng.choice(by_group["EQUITY"])
                emit(code, b, {"client_id": "CL022", "account_id": acct_by_client["CL022"][0]["account_id"]})
            elif code == "N11":
                b = rng.choice([t for t in by_group["EQUITY"] if t["market"] == "XNYS"])
                td = date(2026, 11, 2) + timedelta(days=rng.randint(0, 3))
                emit(code, b, {"client_id": "CL019", "account_id": acct_by_client["CL019"][0]["account_id"],
                               "trade_date": td, "settlement_date": ref.add_business_days(td, 1, [hol["XNYS"], hol["USNY"]])})
            elif code == "N12":
                b = rng.choice(by_group["LISTED_DERIV"])
                c = rng.choice(retail)
                emit(code, b, {"client_id": c, "account_id": acct_by_client[c][0]["account_id"]})
            elif code == "N13":
                b = rng.choice(by_group["IRS"])
                c = [x for x in clients if x["client_type"] == "ASSET_MANAGER"][0]["client_id"]
                emit(code, b, {"client_id": c, "account_id": acct_by_client[c][0]["account_id"]})
            elif code == "N14":
                b = rng.choice([t for t in by_group["LISTED_DERIV"] if t["instrument_id"] in ("FUT002", "FUT006")] or by_group["LISTED_DERIV"])
                ltd = derivs[b["instrument_id"]]["last_trading_day"]
                td = ref.add_business_days(ltd, rng.randint(1, 4), [hol["XCME"]])
                emit(code, b, {"trade_date": td, "settlement_date": td})
            elif code == "N15":
                b = rng.choice(by_group["EQUITY"])
                td = date(2026, 10, 29)
                emit(code, b, {"instrument_id": "EQ011", "product_type": "EQUITY", "market": "XLON", "trade_ccy": "GBP", "settle_ccy": "GBP",
                               "trade_date": td, "settlement_date": ref.add_business_days(td, 2, [hol["XLON"], hol["GBLO"]])},
                     note="EQ011 price feed stops 2026-10-23")
            elif code == "N16":
                b = rng.choice(trades)
                emit(code, b, {"quantity": -abs(b["quantity"])})
            elif code == "N17":
                b = rng.choice(trades)
                emit(code, b, {rng.choice(["instrument_id", "trade_date", "side", "quantity"]): None})
            elif code == "N18":
                b = rng.choice([t for t in by_group["BOND"] if t["settle_ccy"] == "EUR"] or by_group["BOND"])
                emit(code, b, {"quantity": b["quantity"] + 500})
            elif code == "N19":
                b = rng.choice(by_group["EQUITY"])
                limit = next(c["exposure_limit_usd"] for c in clients if c["client_id"] == b["client_id"])
                floor = USD_PER_UNIT_FLOOR[b["trade_ccy"]]  # below every simulated rate, so the breach holds on any date
                qty = math.ceil(Decimal(limit) * Decimal("1.1") / (Decimal(str(b["price"])) * floor) / 100) * 100
                emit(code, b, {"quantity": max(b["quantity"] * 4000, qty)})
            elif code == "N20":
                b = rng.choice(by_group["IRS"])
                emit(code, b, {"end_date": b["settlement_date"], "irs_tenor_years": 0})
            elif code == "N21":
                emit(code, rng.choice(trades), {"source_trade_ref": None}, note="valid economics, arrives in a later batch")
                rows[-1]["source_trade_ref"] = f"LATE-REF-{n}"
            elif code == "N22":
                b = rng.choice([t for t in by_group["EQUITY"] if t["market"] == "XNYS"])
                emit(code, b, {"settle_ccy": "CHF"})
            elif code == "N23":
                b = rng.choice([t for t in by_group["BOND"] if t["market"] == "OTC-JP" and t["trade_time_utc"].hour >= 23]
                               or by_group["BOND"])
                emit(code, b, {"trade_date": b["trade_time_utc"].date()})

    # allocation negatives: quantities that do not add up to the block
    alloc_rows, k = [], 0
    for t in rng.sample(by_group["EQUITY"], 10):
        k += 1
        allocs = [a for a in tables["Allocations"] if a["trade_id"] == t["trade_id"]]
        for a in allocs:
            alloc_rows.append(dict(a, allocation_id=f"NAL{k:03d}-{a['alloc_seq']}",
                                   alloc_qty=a["alloc_qty"] + (100 if a["alloc_seq"] == 1 else 0)))
        labels.append({"negative_case_id": f"N24-{k:04d}", "trade_id": t["trade_id"], "base_trade_id": t["trade_id"],
                       "defect_type": "N24", "description": "Allocation quantities do not sum to the block",
                       "rule_id": "R024", "expected_error": "E_ALLOC_QTY_MISMATCH", "expected_layer": "API_REJECT",
                       "note": "Allocations_negative.xlsx"})
    drift = []
    for t in rng.sample(trades, 20):
        r = {("qty" if c == "quantity" else c): v for c, v in t.items()}
        r["desk_comment"] = "schema drift test"
        drift.append(r)
    labels.append({"negative_case_id": "N25-0001", "trade_id": "*", "base_trade_id": "*", "defect_type": "N25",
                   "description": "Schema drift: quantity renamed to qty, extra column desk_comment", "rule_id": "DQ05",
                   "expected_error": "E_SCHEMA_DRIFT", "expected_layer": "DQ_QUARANTINE",
                   "note": "Trades_schema_drift.xlsx"})
    return {"files": {"Trades_negative.xlsx": {"Trades": rows}, "Allocations_negative.xlsx": {"Allocations": alloc_rows},
                      "Trades_schema_drift.xlsx": {"Trades": drift}},
            "labels": labels}
