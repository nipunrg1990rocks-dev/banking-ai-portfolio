"""Generate the CapitalFlow 360 synthetic dataset.

    python -m datagen.generate            # writes data/excel, data/negative, data/oracle, data/manifest.json

Everything is driven by one seed and one configuration version, so a re-run reproduces the
same bytes (checked by ``datagen/validate.py --reproducibility``).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from datagen import calendars as cal_mod
from datagen import market, negative, refdata, trades
from datagen.workbook import sha256, write_workbook
from testkit.oracle import expected as oracle
from testkit.oracle import reference as ref

SEED = 20261116
CONFIG_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data"
OPENING_DATE = date(2026, 9, 11)
STATEMENT_DATE = oracle.STATEMENT_DATE

RELATIONSHIPS = [
    ("Trades.instrument_id", "Instruments.instrument_id"), ("Trades.client_id", "Clients.client_id"),
    ("Trades.account_id", "Accounts.account_id"), ("Trades.order_id", "Orders.order_id"),
    ("Trades.counterparty_id", "Clients.xlsx:Counterparties.counterparty_id"),
    ("Executions.order_id", "Orders.order_id"), ("Allocations.trade_id", "Trades.trade_id"),
    ("Allocations.account_id", "Accounts.account_id"), ("Accounts.client_id", "Clients.client_id"),
    ("Settlement_Instructions.owner_id", "Accounts.account_id | Counterparties.counterparty_id"),
    ("Prices.instrument_id", "Instruments.instrument_id"), ("Opening_Positions.account_id", "Accounts.account_id"),
    ("Dividends.instrument_id", "Instruments.instrument_id"), ("Confirmations.trade_id", "Trades.trade_id"),
    ("Settlement_Status.allocation_id", "Allocations.allocation_id (+ -E end leg, -F fee)"),
    ("Custody_Positions.account_id", "Accounts.account_id"),
]


def meta(name: str, desc: str) -> dict:
    return {"workbook": name, "description": desc, "generator": "datagen/generate.py", "generator_seed": SEED,
            "config_version": CONFIG_VERSION, "generated_at": "2026-11-16T00:00:00Z (fixed for reproducibility)",
            "data_classification": "SYNTHETIC - fictional entities, not for real use",
            "business_period": "trade dates 2026-09-14 to 2026-10-30; statement date 2026-11-06"}


def build_ssis(rng, accounts, counterparties) -> list[dict]:
    rows = []
    n = 0
    locs = {"USD": ["SIM-US-CSD", "SIM-PAY"], "EUR": ["SIM-EU-CSD", "SIM-PAY"], "GBP": ["SIM-UK-CSD", "SIM-PAY"],
            "JPY": ["SIM-JP-CSD", "SIM-PAY"], "CAD": ["SIM-PAY"], "AUD": ["SIM-PAY"], "CHF": ["SIM-PAY"]}
    owners = [("ACCOUNT", a["account_id"]) for a in accounts] + \
             [("COUNTERPARTY", c["counterparty_id"]) for c in counterparties if c["counterparty_type"] not in ("CUSTODIAN",)]
    for otype, oid in owners:
        for ccy, ls in locs.items():
            for loc in ls:
                n += 1
                eff_to = None
                rows.append({"ssi_id": f"SSI{n:05d}", "owner_type": otype, "owner_id": oid, "currency": ccy,
                             "settlement_location": loc, "agent_code": f"SIMB{ccy}{(n % 7) + 1}XX",
                             "safekeeping_account": f"SK-{oid}-{ccy}" if loc != "SIM-PAY" else None,
                             "cash_account": f"CSH-{oid}-{ccy}", "effective_from": date(2025, 1, 1),
                             "effective_to": eff_to, "status": "ACTIVE", "version": 1,
                             "source_system": "SSI-SIM", "source_record_id": f"SSI-{n}"})
    # Effective-dated change: CP02 USD custody SSI replaced on 16 Oct 2026 (new agent)
    for r in list(rows):
        if r["owner_id"] == "CP02" and r["currency"] == "USD" and r["settlement_location"] == "SIM-US-CSD":
            r["effective_to"] = date(2026, 10, 15)
            n += 1
            rows.append(dict(r, ssi_id=f"SSI{n:05d}", agent_code="SIMBUSD9XX", effective_from=date(2026, 10, 16),
                             effective_to=None, version=2, source_record_id=f"SSI-{n}"))
    # Counterparty CP08 has no AUD settlement instruction at the custody location -> AUD only via payments
    # Reference-only expired SSI (no baseline trades use it) for temporal tests
    n += 1
    rows.append({"ssi_id": f"SSI{n:05d}", "owner_type": "COUNTERPARTY", "owner_id": "CP06", "currency": "CAD",
                 "settlement_location": "SIM-CA-CSD", "agent_code": "SIMBCADOLD", "safekeeping_account": "SK-CP06-CAD-OLD",
                 "cash_account": "CSH-CP06-CAD-OLD", "effective_from": date(2024, 1, 1), "effective_to": date(2025, 12, 31),
                 "status": "EXPIRED", "version": 1, "source_system": "SSI-SIM", "source_record_id": f"SSI-{n}"})
    return rows


def build_corporate_actions(rng, inst, hol) -> tuple[list[dict], list[dict]]:
    divs = []
    eqs = [r for r in inst["Instruments"] if r["product_type"] in ("EQUITY", "ETF")]
    picks = rng.sample(eqs, 12)
    for k, e in enumerate(picks, start=1):
        m = refdata.MARKET[e["market"]]
        c = [hol[m[1]], hol[cal_mod.CCY_CALENDAR[e["currency"]]]]
        rec = ref.roll_following(date(2026, 9, 22) + timedelta(days=rng.randint(0, 35)), c)
        lag = 1 if e["market"] == "XNYS" else 2
        ex = rec if lag == 1 else ref.roll_preceding(rec - timedelta(days=1), c)
        pay = ref.add_business_days(rec, rng.randint(3, 10), c)
        rate = round(rng.uniform(0.1, 1.5), 4) if e["currency"] != "JPY" else rng.choice([15, 20, 30, 45])
        divs.append({"ca_id": f"DIV{k:03d}", "instrument_id": e["instrument_id"], "event_type": "CASH_DIVIDEND",
                     "ex_date": ex, "record_date": rec, "pay_date": pay, "gross_rate_per_share": rate,
                     "currency": e["currency"], "status": "CONFIRMED", "source_system": "CA-SIM",
                     "source_record_id": f"CA-DIV-{k}"})
    coupons = []
    bonds = {r["instrument_id"]: r for r in inst["Bond_Details"]}
    ccy_of = {r["instrument_id"]: r["currency"] for r in inst["Instruments"]}
    k = 0
    for iid, b in bonds.items():
        ccy = ccy_of[iid]
        hs = hol[cal_mod.CCY_CALENDAR[ccy]]
        for d in ref.coupon_dates(b["maturity_date"], b["coupon_frequency"], b["issue_date"]):
            if date(2026, 9, 14) <= d <= date(2026, 11, 6):
                k += 1
                pay = ref.roll_following(d, [hs])
                if b["ex_dividend_bdays"]:
                    ex = d
                    cnt = 0
                    while cnt < b["ex_dividend_bdays"]:
                        ex -= timedelta(days=1)
                        if ref.is_business_day(ex, hs):
                            cnt += 1
                    rec = ref.roll_preceding(ex - timedelta(days=1), [hs])
                else:
                    rec = ref.roll_preceding(d - timedelta(days=1), [hs])
                coupons.append({"ca_id": f"CPN{k:03d}", "instrument_id": iid, "event_type": "COUPON", "coupon_date": d,
                                "record_date": rec, "pay_date": pay, "coupon_rate_pct": b["coupon_rate_pct"],
                                "amount_per_100": float(ref.round_dp(ref.to_frac(b["coupon_rate_pct"]) / b["coupon_frequency"], 8)),
                                "currency": ccy, "status": "CONFIRMED", "source_system": "CA-SIM",
                                "source_record_id": f"CA-CPN-{k}"})
    return divs, coupons


def build_opening(rng, ctx, accounts, inst) -> tuple[list[dict], list[dict], list[dict]]:
    """Opening positions cover every delivery, except three designed short positions."""
    need = defaultdict(int)
    allocs_by_trade = defaultdict(list)
    for a in ctx.allocs:
        allocs_by_trade[a["trade_id"]].append(a)
    der = {r["instrument_id"]: r for r in inst["Derivative_Details"]}
    for t in ctx.trades:
        for a in allocs_by_trade[t["trade_id"]]:
            if t["product_type"] in ("EQUITY", "ETF", "GOVT_BOND", "CORP_BOND") and t["side"] == "SELL":
                need[(a["account_id"], t["instrument_id"])] += a["alloc_qty"]
            elif t["product_type"] in ("REPO", "SEC_LOAN"):
                need[(a["account_id"], t["instrument_id"])] += a["alloc_qty"]
            elif t["product_type"] == "OPTION":
                d = der[t["instrument_id"]]
                if d["settlement_type"] == "PHYSICAL":
                    deliver = (d["option_type"] == "PUT") == (t["side"] == "BUY")
                    if deliver:
                        need[(a["account_id"], d["underlying_id"])] += a["alloc_qty"] * d["contract_multiplier"]
    lot = {r["instrument_id"]: r["lot_size"] for r in inst["Equity_Details"]}
    shortfalls = []
    eq_sells = [(a, t) for t in ctx.trades if t["product_type"] == "EQUITY" and t["side"] == "SELL"
                for a in allocs_by_trade[t["trade_id"]]]
    used = set()
    for a, t in eq_sells:
        key = (a["account_id"], t["instrument_id"])
        acct = next(x for x in accounts if x["account_id"] == a["account_id"])
        others = [x for x in ctx.trades if x["instrument_id"] == t["instrument_id"] and x["trade_id"] != t["trade_id"]
                  and any(y["account_id"] == a["account_id"] for y in allocs_by_trade[x["trade_id"]])]
        if key in used or others or a["alloc_qty"] < 5 * lot[t["instrument_id"]]:
            continue
        want = "Y" if len(shortfalls) < 3 else "N"
        if acct["partial_settlement_allowed"] != want:
            continue
        shortfalls.append({"allocation_id": a["allocation_id"], "trade_id": t["trade_id"], "account_id": a["account_id"],
                           "instrument_id": t["instrument_id"], "partial_allowed": want})
        used.add(key)
        if len(shortfalls) == 4:
            break
    short_keys = {(s["account_id"], s["instrument_id"]) for s in shortfalls}
    positions = []
    for (acct, iid), q in sorted(need.items()):
        if (acct, iid) in short_keys:
            lt = lot.get(iid, 1)
            qty = int(q * 0.6) // lt * lt
        else:
            buffer = rng.choice([0, 0.1, 0.25, 0.5])
            qty = int(q * (1 + buffer))
            if iid in lot:
                qty = -(-qty // lot[iid]) * lot[iid]
            elif iid.startswith("BD"):
                qty = -(-qty // 1000) * 1000
        positions.append({"account_id": acct, "instrument_id": iid, "quantity": qty, "as_of_date": OPENING_DATE,
                          "position_type": "SETTLED", "source_system": "CUSTODY-SIM",
                          "source_record_id": f"OP-{acct}-{iid}"})
    # some extra long-standing holdings (no trading activity) for reconciliation realism
    eqs = [r["instrument_id"] for r in inst["Equity_Details"]]
    for a in rng.sample(accounts, 20):
        iid = rng.choice(eqs)
        if (a["account_id"], iid) in need:
            continue
        positions.append({"account_id": a["account_id"], "instrument_id": iid, "quantity": 100 * rng.randint(1, 50),
                          "as_of_date": OPENING_DATE, "position_type": "SETTLED", "source_system": "CUSTODY-SIM",
                          "source_record_id": f"OP-{a['account_id']}-{iid}"})
    cash = []
    for a in accounts:
        for ccy, scale in (("USD", 1), ("EUR", 1), ("GBP", 1), ("JPY", 150), ("CAD", 1.4), ("AUD", 1.5), ("CHF", 0.8)):
            bal = round(rng.uniform(150, 400) * 1_000_000 * scale, 0 if ccy == "JPY" else 2)
            cash.append({"account_id": a["account_id"], "currency": ccy, "balance": bal, "as_of_date": OPENING_DATE,
                         "source_system": "CUSTODY-SIM", "source_record_id": f"OC-{a['account_id']}-{ccy}"})
    return positions, cash, shortfalls


def inject_confirmations(rng, tr_rows, econ, labels) -> list[dict]:
    confs = []
    eligible = [t for t in tr_rows if t["product_type"] not in ("FUTURE", "OPTION")]
    pool = [t for t in eligible if econ[t["trade_id"]]["settlement_date"] <= STATEMENT_DATE - timedelta(days=10)
            and econ[t["trade_id"]]["settle_amount"]]
    picks = rng.sample(pool, 17)
    plan = {}
    for t, kind in zip(picks, ["TOL_IN"] * 6 + ["TOL_OUT"] * 5 + ["MISSING"] * 3 + ["SD"] * 2 + ["PRICE"], strict=True):
        plan[t["trade_id"]] = kind
    tol_in_diffs = ["0.37", "1.00", "0.99", "0.01", "0.50", "1.00"]
    tol_out_diffs = ["1.01", "2.50", "40.00", "1.02", "125.00"]
    n = 0
    for t in eligible:
        e = econ[t["trade_id"]]
        kind = plan.get(t["trade_id"])
        if kind == "MISSING":
            labels.append({"artifact": "Confirmations", "key": t["trade_id"], "label": "CONFIRMATION_MISSING",
                           "expected_outcome": "UNMATCHED_MISSING"})
            continue
        n += 1
        amt = Fraction(str(e["settle_amount"]))
        sd = e["settlement_date"]
        price = t["price"]
        if kind in ("TOL_IN", "TOL_OUT"):
            diff = Fraction((tol_in_diffs if kind == "TOL_IN" else tol_out_diffs).pop())
            if t["settle_ccy"] == "JPY":
                diff *= 100
            amt += diff if rng.random() < 0.5 else -diff
            labels.append({"artifact": "Confirmations", "key": t["trade_id"],
                           "label": f"AMOUNT_DIFF_{'WITHIN' if kind == 'TOL_IN' else 'OUTSIDE'}_TOLERANCE",
                           "expected_outcome": "MATCHED_WITHIN_TOLERANCE" if kind == "TOL_IN" else "MISMATCH_AMOUNT"})
        elif kind == "SD":
            sd = sd + timedelta(days=1)
            labels.append({"artifact": "Confirmations", "key": t["trade_id"], "label": "SETTLE_DATE_DIFF",
                           "expected_outcome": "MISMATCH_SETTLE_DATE"})
        elif kind == "PRICE":
            price = round(price * 1.001, 6)
            labels.append({"artifact": "Confirmations", "key": t["trade_id"], "label": "PRICE_DIFF",
                           "expected_outcome": "MISMATCH_PRICE"})
        confs.append({"confirmation_id": f"CNF{n:05d}", "trade_id": t["trade_id"], "counterparty_id": t["counterparty_id"],
                      "cp_trade_ref": f"CP-{t['counterparty_id']}-{100000 + n}", "quantity": t["quantity"], "price": price,
                      "settle_amount": float(amt), "settle_ccy": t["settle_ccy"], "settle_date": sd,
                      "received_ts_utc": t["trade_time_utc"] + timedelta(minutes=rng.randint(5, 240)),
                      "message_type": "CONFIRMATION", "source_system": "CONFIRM-SIM",
                      "source_record_id": f"CONF-{n}"})
    return confs


def build_settlement_status(rng, tr_rows, allocs, econ, matching, hol, labels) -> list[dict]:
    rows = []
    by_trade = defaultdict(list)
    for a in allocs:
        by_trade[a["trade_id"]].append(a)
    buys = [(t, a) for t in tr_rows for a in by_trade[t["trade_id"]]
            if t["product_type"] in ("EQUITY", "ETF", "GOVT_BOND", "CORP_BOND") and t["side"] == "BUY"
            and matching[t["trade_id"]].startswith("MATCHED") and econ[t["trade_id"]]["settlement_date"] <= date(2026, 10, 26)]
    sells = [(t, a) for t in tr_rows for a in by_trade[t["trade_id"]]
             if t["product_type"] in ("EQUITY", "ETF", "GOVT_BOND", "CORP_BOND") and t["side"] == "SELL"
             and matching[t["trade_id"]].startswith("MATCHED") and econ[t["trade_id"]]["settlement_date"] <= date(2026, 10, 26)]
    n = 0
    for (t, a), (reason, resolve_days) in zip(rng.sample(buys, 10), [("CPTY_SECURITIES_SHORTAGE", d) for d in
                                                                    (1, 2, 3, 5, 1, 2, 4, None, None, None)], strict=True):
        n += 1
        sd = econ[t["trade_id"]]["settlement_date"]
        hs = [hol[cal_mod.CCY_CALENDAR[t["settle_ccy"]]]]
        res = ref.add_business_days(sd, resolve_days, hs) if resolve_days else None
        rows.append({"status_msg_id": f"SST{n:05d}", "allocation_id": a["allocation_id"], "trade_id": t["trade_id"],
                     "status": "FAILED", "reason_code": reason, "status_date": sd, "resolved_date": res,
                     "source_system": "CUSTODIAN-SIM", "source_record_id": f"CUST-{n}"})
        labels.append({"artifact": "Settlement_Status", "key": a["allocation_id"], "label": f"FAIL_{reason}",
                       "expected_outcome": f"SETTLED_ON_{res}" if res else "FAILED_OPEN"})
    for t, a in rng.sample(sells, 2):
        n += 1
        sd = econ[t["trade_id"]]["settlement_date"]
        res = ref.add_business_days(sd, 2, [hol[cal_mod.CCY_CALENDAR[t["settle_ccy"]]]])
        rows.append({"status_msg_id": f"SST{n:05d}", "allocation_id": a["allocation_id"], "trade_id": t["trade_id"],
                     "status": "FAILED", "reason_code": "CPTY_CASH_SHORTAGE", "status_date": sd, "resolved_date": res,
                     "source_system": "CUSTODIAN-SIM", "source_record_id": f"CUST-{n}"})
        labels.append({"artifact": "Settlement_Status", "key": a["allocation_id"], "label": "FAIL_CPTY_CASH_SHORTAGE",
                       "expected_outcome": f"SETTLED_ON_{res}"})
    return rows


def custody_statements(rng, E, inst_types, labels):
    pos_rows, cash_rows = [], []
    for (acct, iid), q in sorted(E.positions.items()):
        if inst_types.get(iid) not in ("EQUITY", "ETF", "GOVT_BOND", "CORP_BOND") or q == 0:
            continue
        pos_rows.append({"statement_date": STATEMENT_DATE, "account_id": acct, "instrument_id": iid,
                         "quantity": int(q), "source_system": "CUSTODIAN-SIM",
                         "source_record_id": f"CPOS-{acct}-{iid}"})
    for (acct, ccy), b in sorted(E.cash.items()):
        cash_rows.append({"statement_date": STATEMENT_DATE, "account_id": acct, "currency": ccy,
                          "balance": float(ref.round_ccy(b, ccy)), "source_system": "CUSTODIAN-SIM",
                          "source_record_id": f"CCSH-{acct}-{ccy}"})
    picks = rng.sample(range(len(pos_rows)), 5)
    for k, i in enumerate(picks[:3]):
        r = pos_rows[i]
        delta = [100, -500, 1000][k]
        r["quantity"] += delta
        labels.append({"artifact": "Custody_Positions", "key": f"{r['account_id']}|{r['instrument_id']}",
                       "label": "POSITION_QTY_BREAK", "expected_outcome": f"BREAK_QTY diff={delta}"})
    missing = pos_rows[picks[3]]
    labels.append({"artifact": "Custody_Positions", "key": f"{missing['account_id']}|{missing['instrument_id']}",
                   "label": "MISSING_AT_CUSTODIAN", "expected_outcome": "BREAK_MISSING_EXTERNAL"})
    pos_rows.remove(missing)
    extra = dict(pos_rows[0], instrument_id="EQ003", quantity=2500, source_record_id="CPOS-EXTRA-1")
    if not any(r["account_id"] == extra["account_id"] and r["instrument_id"] == "EQ003" for r in pos_rows):
        pos_rows.append(extra)
        labels.append({"artifact": "Custody_Positions", "key": f"{extra['account_id']}|EQ003",
                       "label": "UNKNOWN_AT_INTERNAL", "expected_outcome": "BREAK_MISSING_INTERNAL"})
    cpicks = rng.sample(range(len(cash_rows)), 4)
    for k, i in enumerate(cpicks):
        r = cash_rows[i]
        delta = ["1500.00", "-250.75", "0.50", "-1.01"][k]
        mult = 100 if r["currency"] == "JPY" else 1
        r["balance"] = float(Decimal(str(r["balance"])) + Decimal(delta) * mult)
        within = delta == "0.50"
        labels.append({"artifact": "Custody_Cash", "key": f"{r['account_id']}|{r['currency']}",
                       "label": "CASH_DIFF_WITHIN_TOLERANCE" if within else "CASH_BREAK",
                       "expected_outcome": "NO_BREAK" if within else f"BREAK_AMOUNT diff={Decimal(delta) * mult}"})
    return pos_rows, cash_rows


def _jsonable(v):
    if isinstance(v, (date,)):
        return v.isoformat()
    if isinstance(v, (Decimal,)):
        return str(v)
    if isinstance(v, Fraction):
        return str(ref.round_dp(v, 8))
    return v


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        path.write_text("")
        return
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: _jsonable(r.get(k)) for k in cols})


def main(out: Path = OUT) -> dict:
    rng = random.Random(SEED)
    (out / "excel").mkdir(parents=True, exist_ok=True)
    (out / "negative").mkdir(parents=True, exist_ok=True)
    (out / "oracle").mkdir(parents=True, exist_ok=True)

    holidays = cal_mod.build_holidays()
    hol = cal_mod.holiday_sets(holidays)
    clients = refdata.build_clients(rng)
    accounts, schedules = refdata.build_accounts(rng, clients)
    cptys = refdata.build_counterparties(rng)
    inst = refdata.build_instruments(rng)
    seed_px = inst.pop("_seed_prices")
    prices = market.build_prices(rng, inst, seed_px, hol)
    fx_rows, fx_table = market.build_fx(rng, hol)
    fix_rows, fix_table = market.build_fixings(rng, hol)
    fwd_rows = market.build_forward_points(fx_table, inst["FX_Details"])

    ctx = trades.Ctx(rng, inst, hol, prices, fx_table, fix_table, fwd_rows, clients, accounts)
    trades.build_all(ctx)
    ssis = build_ssis(rng, accounts, cptys)
    divs, coupons = build_corporate_actions(rng, inst, hol)
    positions, cash, shortfalls = build_opening(rng, ctx, accounts, inst)

    markets_rows = [{"market": m[0], "calendar_code": m[1], "timezone": m[2], "currency": m[3],
                     "settlement_location": m[4], "exchange_fee_bps": float(m[5]), "stamp_duty_bps": float(m[6])}
                    for m in refdata.MARKETS]
    conv_rows = [{"convention_id": c[0], "product_types": c[1], "market": c[2], "offset_bdays": c[3],
                  "calendar_basis": c[4], "cutoff_local_time": c[5], "timezone": c[6], "effective_from": c[7],
                  "effective_to": c[8], "source_reference": c[9], "simplification_note": c[10]}
                 for c in refdata.CONVENTIONS]
    cal_rows = [{"calendar_code": k, "description": v[0], "timezone": v[1]} for k, v in cal_mod.CALENDARS.items()]

    tables = {"Trades": ctx.trades, "Allocations": ctx.allocs, "Holidays": holidays, "Accounts": accounts,
              "Commission_Schedules": schedules, "Prices": prices, "FX_Rates": fx_rows, "Rate_Fixings": fix_rows,
              "Opening_Positions": positions, "Opening_Cash": cash, "Dividends": divs, "Coupons": coupons, **inst}
    labels: list[dict] = []
    # first pass (no external files) for economics, then build confirmations and custodian outcomes
    D = oracle.Data(tables)
    econ = {t["trade_id"]: oracle.trade_economics(D, t) for t in ctx.trades}
    for t in ctx.trades:  # front office books the contractual date: assert consistency with the oracle
        if t["product_type"] not in ("REPO", "REVERSE_REPO"):
            assert t["settlement_date"] == econ[t["trade_id"]]["settlement_date"], t
    confs = inject_confirmations(rng, ctx.trades, econ, labels)
    tables["Confirmations"] = confs
    matching = {t["trade_id"]: oracle.match_confirmation(econ[t["trade_id"]], t,
                                                         next((c for c in confs if c["trade_id"] == t["trade_id"]), None))
                for t in ctx.trades}
    sst = build_settlement_status(rng, ctx.trades, ctx.allocs, econ, matching, hol, labels)
    tables["Settlement_Status"] = sst
    E = oracle.project(tables, STATEMENT_DATE)
    for s in shortfalls:
        st = E.settlements[s["allocation_id"]]
        labels.append({"artifact": "Opening_Positions", "key": s["allocation_id"],
                       "label": "INTERNAL_SHORTFALL_PARTIAL_ALLOWED" if s["partial_allowed"] == "Y" else "INTERNAL_SHORTFALL_NO_PARTIAL",
                       "expected_outcome": st["status"]})
    inst_types = {r["instrument_id"]: r["product_type"] for r in inst["Instruments"]}
    cpos, ccash = custody_statements(rng, E, inst_types, labels)
    for t in ctx.trades:  # duplicate-review look-alikes
        if t["source_trade_ref"].endswith("-B"):
            labels.append({"artifact": "Trades", "key": t["trade_id"], "label": "LOOKALIKE_LEGITIMATE_TRADE",
                           "expected_outcome": "POTENTIAL_DUPLICATE_REVIEW"})

    files = {}
    def wb(name, sheets, desc, columns=None):
        path = out / "excel" / name
        schema = write_workbook(path, sheets, meta(name, desc), columns)
        files[name] = {"path": f"data/excel/{name}", "sha256": sha256(path),
                       "rows": {k: len(v) for k, v in sheets.items()},
                       "schema": {k: [{"column": c, "type": t, "nullable": n} for c, t, n in v] for k, v in schema.items()}}

    wb("Trades.xlsx", {"Trades": ctx.trades}, "500 baseline trades across six product groups",
       {"Trades": trades.TRADE_COLUMNS})
    wb("Clients.xlsx", {"Clients": clients, "Counterparties": cptys}, "Fictional clients and counterparties")
    wb("Accounts.xlsx", {"Accounts": accounts, "Commission_Schedules": schedules},
       "Accounts, permissions, limits and effective-dated commission schedules")
    wb("Instruments.xlsx", inst, "Instrument master with asset-specific detail sheets")
    wb("Orders.xlsx", {"Orders": ctx.orders}, "Orders incl. cancelled/rejected orders without trades")
    wb("Executions.xlsx", {"Executions": ctx.execs}, "Fills (partial fills per order)")
    wb("Allocations.xlsx", {"Allocations": ctx.allocs}, "Block-to-account allocations")
    wb("Settlement_Instructions.xlsx", {"SSIs": ssis}, "Effective-dated standing settlement instructions")
    wb("Prices_FX_Rates.xlsx", {"Prices": prices, "FX_Rates": fx_rows, "Rate_Fixings": fix_rows,
                                "FX_Forward_Points": fwd_rows}, "Closing prices, FX rates, fixings and forward points")
    wb("Business_Calendars.xlsx", {"Calendars": cal_rows, "Holidays": holidays, "Markets": markets_rows,
                                   "Settlement_Conventions": conv_rows},
       "Rule-generated holiday calendars, markets and effective-dated settlement conventions")
    wb("Positions_Cash.xlsx", {"Opening_Positions": positions, "Opening_Cash": cash},
       f"Opening settled positions and cash as of {OPENING_DATE}")
    wb("Corporate_Actions.xlsx", {"Dividends": divs, "Coupons": coupons}, "Cash dividends and bond coupons")
    wb("External_Reconciliation.xlsx", {"Confirmations": confs, "Settlement_Status": sst, "Custody_Positions": cpos,
                                        "Custody_Cash": ccash},
       "Simulated counterparty confirmations, custodian settlement outcomes and custody statements")

    neg = negative.build(rng, tables, clients, accounts, inst, hol, ctx)
    neg_files = {}
    for name, sheets in neg["files"].items():
        path = out / "negative" / name
        cols = {"Trades": trades.TRADE_COLUMNS + ["negative_case_id"]} if name == "Trades_negative.xlsx" else None
        write_workbook(path, sheets, meta(name, "Negative-test variants (NOT part of the 500 baseline)"), cols)
        neg_files[name] = {"path": f"data/negative/{name}", "sha256": sha256(path),
                           "rows": {k: len(v) for k, v in sheets.items()}}
    write_csv(out / "oracle" / "negative_labels.csv", neg["labels"])

    # ---------------- oracle artifacts (never inputs to the platform) ----------------
    write_csv(out / "oracle" / "expected_trades.csv", [dict(v) for v in E.trades.values()])
    write_csv(out / "oracle" / "expected_allocations.csv", list(E.allocations.values()))
    write_csv(out / "oracle" / "expected_matching.csv", [{"trade_id": k, "outcome": v} for k, v in E.matching.items()])
    write_csv(out / "oracle" / "expected_settlements.csv", list(E.settlements.values()))
    write_csv(out / "oracle" / "expected_positions.csv",
              [{"account_id": a, "instrument_id": i, "quantity": q} for (a, i), q in sorted(E.positions.items())])
    write_csv(out / "oracle" / "expected_cash.csv",
              [{"account_id": a, "currency": c, "balance": ref.round_ccy(b, c)} for (a, c), b in sorted(E.cash.items())])
    write_csv(out / "oracle" / "expected_lifecycle_events.csv", E.lifecycle)
    write_csv(out / "oracle" / "defect_labels.csv", labels)

    group_counts = Counter(t["product_group"] for t in ctx.trades)
    manifest = {
        "dataset": "CapitalFlow 360 synthetic baseline", "config_version": CONFIG_VERSION, "generator_seed": SEED,
        "generator": "datagen/generate.py", "statement_date": STATEMENT_DATE.isoformat(),
        "opening_date": OPENING_DATE.isoformat(), "baseline_trade_count": len(ctx.trades),
        "trade_counts_by_group": {refdata.GROUP_LABEL[g]: group_counts[g] for g in refdata.PRODUCT_GROUPS},
        "files": files, "negative_files": neg_files, "relationships": [{"from": a, "to": b} for a, b in RELATIONSHIPS],
        "oracle_artifacts": sorted(p.name for p in (out / "oracle").glob("*.csv")),
        "oracle_note": "Expected outcomes and defect labels are kept in data/oracle and are never loaded by the platform.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=_jsonable))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    m = main(a.out)
    print(json.dumps({"baseline_trade_count": m["baseline_trade_count"], "by_group": m["trade_counts_by_group"],
                      "files": {k: v["rows"] for k, v in m["files"].items()}}, indent=2))
