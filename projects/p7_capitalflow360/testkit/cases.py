"""Test case catalogue: deterministic generation of every test case, with lineage and rule traceability.

    python -m testkit.cases            # writes testkit/out/cases.{json,parquet,xlsx} and traceability.xlsx

A case is data, not code: ``kind`` names the executor in ``testkit/execute.py``, ``params`` are its
inputs and ``expected`` comes from a source that is independent of the production code: the oracle
files (data/oracle), the rule catalogue's documented boundaries, the role matrix in
``testkit/oracle/specs.py`` or the hand-written lifecycle matrix there. ``lineage`` says which.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from datagen.calendars import build_holidays, holiday_sets
from testkit.oracle import reference as ora
from testkit.oracle import specs

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = Path(__file__).resolve().parent / "out"


def _read(path: Path) -> dict[str, list[dict]]:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    out = {}
    for ws in wb.worksheets:
        if ws.title in ("Dictionary", "Provenance"):
            continue
        rows = list(ws.iter_rows(values_only=True))
        out[ws.title] = [dict(zip(rows[0], r, strict=False)) for r in rows[1:]]
    wb.close()
    return out


def _csv(name: str) -> list[dict]:
    with open(DATA / "oracle" / name) as f:
        return list(csv.DictReader(f))


def _d(v) -> date:
    return v.date() if isinstance(v, datetime) else v


def C(case_id, scenario_id, suite, title, rule_ids, kind, params, expected, lineage, layer="service", priority="P2"):
    return {"case_id": case_id, "scenario_id": scenario_id, "suite": suite, "title": title, "rule_ids": list(rule_ids),
            "layer": layer, "kind": kind, "params": params, "expected": expected, "lineage": lineage, "priority": priority}


# ----------------------------------------------------------------------------------------------- suites
def book_cases(trades: list[dict]) -> list[dict]:
    """Every baseline trade re-booked (dry run) must reproduce the oracle's economics, dates and allocations."""
    exp = {r["trade_id"]: r for r in _csv("expected_trades.csv")}
    out = []
    for t in sorted(trades, key=lambda r: r["trade_id"]):
        e = exp[t["trade_id"]]
        rules = {"EQUITY": ["R020", "R021", "R022", "R023"], "ETF": ["R020", "R021", "R022", "R023"],
                 "GOVT_BOND": ["R030", "R031"], "CORP_BOND": ["R030", "R031"], "FX_SPOT": ["R040", "R042"],
                 "FX_FORWARD": ["R041", "R042", "R043"], "FUTURE": ["R050"], "OPTION": ["R054"], "IRS": ["R060", "R062"],
                 "REPO": ["R070", "R071"], "REVERSE_REPO": ["R070", "R071"], "SEC_LOAN": ["R073"], "SEC_BORROW": ["R073"]}
        out.append(C(f"TC-BOOK-{t['trade_id']}", f"SC-BOOK-{t['product_type']}", "booking",
                     f"{t['product_type']} {t['side']} {t['instrument_id']}: re-booked economics, settlement date and allocations match the oracle",
                     ["R011", "R024", "R025", *rules[t["product_type"]]], "book_oracle", {"trade_id": t["trade_id"]},
                     {"settlement_date": e["settlement_date"], "fields": "oracle row"},
                     "data/excel/Trades.xlsx row + data/oracle/expected_trades.csv + expected_allocations.csv", priority="P1"))
    return out


def negative_cases() -> list[dict]:
    out = []
    for lab in _csv("negative_labels.csv"):
        if lab["defect_type"] == "N25":
            out.append(C("TC-NEG-N25-0001", "SC-NEG-N25", "negative", lab["description"], ["DQ05"], "schema_drift",
                         {"file": "Trades_schema_drift.xlsx"}, {"rule_id": "DQ05", "error": "E_SCHEMA_DRIFT"},
                         "data/negative/Trades_schema_drift.xlsx + negative_labels.csv", priority="P1"))
            continue
        kind = "neg_alloc" if lab["defect_type"] == "N24" else "neg_trade"
        outcome = {"API_REJECT": "REJECTED", "DQ_QUARANTINE": "REJECTED", "ACCEPT_LATE": "ACCEPTED",
                   "ENRICHMENT_EXCEPTION": "ACCEPTED"}[lab["expected_layer"]]
        out.append(C(f"TC-NEG-{lab['negative_case_id']}", f"SC-NEG-{lab['defect_type']}", "negative",
                     f"{lab['description']} ({lab['trade_id']}) is {outcome.lower()} by {lab['rule_id']}", [lab["rule_id"]], kind,
                     {"trade_id": lab["trade_id"], "base_trade_id": lab["base_trade_id"]},
                     {"outcome": outcome, "rule_id": lab["rule_id"], "error": lab["expected_error"] or None, "layer": lab["expected_layer"]},
                     f"data/negative/{'Allocations' if kind == 'neg_alloc' else 'Trades'}_negative.xlsx + negative_labels.csv", priority="P1"))
    return out


PAIRS = [("EUR", "USD"), ("USD", "JPY"), ("GBP", "USD"), ("USD", "CAD"), ("AUD", "USD"), ("USD", "CHF"), ("EUR", "GBP"), ("EUR", "JPY")]


def fx_date_cases(hol) -> list[dict]:
    """FX value dates over every second weekday of the simulation window, for eight pairs and three forward tenors."""
    days = [date(2026, 9, 1) + timedelta(n) for n in range(0, 122)]
    weekdays = [d for d in days if d.weekday() < 5]
    out = []
    for base, quote in PAIRS:
        for d in weekdays[::2]:
            out.append(C(f"TC-FXD-{base}{quote}-{d:%Y%m%d}", f"SC-CAL-FX-{base}{quote}", "calendar",
                         f"{base}/{quote} spot value date for trade date {d}", ["R040"], "fx_date",
                         {"base": base, "quote": quote, "trade_date": d.isoformat(), "months": None},
                         {"source": "oracle.fx_spot_date"}, "testkit/oracle/reference.py fx_spot_date + datagen holidays"))
    for base, quote in PAIRS[:4]:
        for months in (1, 3, 6):
            for d in weekdays[3::9]:
                out.append(C(f"TC-FXF-{base}{quote}-{months}M-{d:%Y%m%d}", "SC-CAL-FX-FWD", "calendar",
                             f"{base}/{quote} {months}M forward value date for trade date {d}", ["R041"], "fx_date",
                             {"base": base, "quote": quote, "trade_date": d.isoformat(), "months": months},
                             {"source": "oracle.fx_forward_date"}, "testkit/oracle/reference.py fx_forward_date"))
    return out


def accrued_cases(inst: dict) -> list[dict]:
    """Bond accrued interest for every bond on ten settlement dates, production vs oracle."""
    bonds = inst["Bond_Details"]
    dates = [date(2026, 9, 15) + timedelta(days=11 * k) for k in range(10)]
    return [C(f"TC-ACR-{b['instrument_id']}-{d:%Y%m%d}", f"SC-CALC-ACCRUED-{b['day_count'].replace('/', '')}", "calculation",
              f"{b['instrument_id']} ({b['day_count']}, {b['coupon_frequency']}x) accrued per 100 settling {d}", ["R030", "R033"],
              "accrued", {"instrument_id": b["instrument_id"], "settle": d.isoformat()}, {"source": "oracle.accrued_interest_per_100"},
              "data/excel/Instruments.xlsx Bond_Details + testkit/oracle/reference.py") for b in bonds for d in dates]


def split_cases() -> list[dict]:
    """Allocation amount splitting: residual cents must land so that the parts sum exactly (R025)."""
    import random
    rng = random.Random(25)
    out = []
    for k in range(60):
        ccy = ["USD", "EUR", "GBP", "JPY"][k % 4]
        n = 2 + k % 5
        qtys = [rng.randint(1, 50) * 100 for _ in range(n)]
        total = f"{rng.randint(10_000, 9_999_999)}.{rng.randint(0, 99):02d}" if ccy != "JPY" else str(rng.randint(100_000, 99_999_999))
        out.append(C(f"TC-SPLIT-{k + 1:03d}", f"SC-CALC-SPLIT-{n}WAY", "calculation",
                     f"Split {ccy} {total} across {n} allocations {qtys}", ["R025"], "split",
                     {"total": total, "qtys": qtys, "ccy": ccy}, {"source": "oracle.allocate", "sum_equals_total": True},
                     "deterministic random (seed 25) + testkit/oracle/reference.py allocate"))
    return out


def rule_cases(trades: list[dict], ref: dict) -> list[dict]:
    """Boundary cases from the rule catalogue's documented boundaries, applied as one change to a valid trade."""
    by_type = defaultdict(list)
    for t in sorted(trades, key=lambda r: r["trade_id"]):
        by_type[t["product_type"]].append(t)
    clients = {c["client_id"]: c for c in ref["clients"]}
    accounts = ref["accounts"]
    out = []

    def R(rule, n, title, base, change, outcome, code=None, db=None, special=None, warning=None):
        out.append(C(f"TC-RULE-{rule}-{n:02d}", f"SC-RULE-{rule}", "rules", title, [rule], "rule_mutation",
                     {"base": base["trade_id"], "set": change, "db": db or [], "special": special},
                     {"outcome": outcome, "rule_id": rule if outcome == "REJECTED" else None, "error": code, "warning": warning},
                     "rule catalogue boundaries (cf360/rules/catalogue.py) applied to a baseline trade", priority="P1"))

    eq = by_type["EQUITY"][0]
    eq_tks = next(t for t in by_type["EQUITY"] if t["market"] == "XTKS")
    eq_ny = next(t for t in by_type["EQUITY"] if t["market"] == "XNYS")
    gb = by_type["GOVT_BOND"][0]
    cb = by_type["CORP_BOND"][0]
    fxs = next(t for t in by_type["FX_SPOT"] if "JPY" in (t["trade_ccy"], t["settle_ccy"]) or t["fx_counter_ccy"] == "JPY")
    fxs2 = next(t for t in by_type["FX_SPOT"] if t["fx_counter_ccy"] not in ("JPY",))
    fxf = by_type["FX_FORWARD"][0]
    fut = by_type["FUTURE"][0]
    opt = by_type["OPTION"][0]
    irs = by_type["IRS"][0]
    repo = by_type["REPO"][0] if by_type["REPO"] else by_type["REVERSE_REPO"][0]
    cl = clients[eq["client_id"]]
    # R001 client status
    for i, st in enumerate(["SUSPENDED", "CLOSED"], 1):
        R("R001", i, f"Client {st.lower()} on the trade date is rejected", eq, {}, "REJECTED", "E_CLIENT_INACTIVE",
          db=[["Client", eq["client_id"], {"status": st}]])
    R("R001", 3, "Active client is accepted", eq, {}, "ACCEPTED")
    # R002 KYC boundary: expiry == trade date valid; expiry + 1 day rejected
    td = _d(eq["trade_date"])
    R("R002", 1, "KYC expiring on the trade date is still valid", eq, {}, "ACCEPTED", db=[["Client", eq["client_id"], {"kyc_expiry_date": td.isoformat()}]])
    R("R002", 2, "KYC expired the day before the trade date is rejected", eq, {}, "REJECTED", "E_KYC_EXPIRED",
      db=[["Client", eq["client_id"], {"kyc_expiry_date": (td - timedelta(1)).isoformat()}]])
    # R003 account permissions
    R("R003", 1, "Account without EQUITY permission is rejected", eq, {}, "REJECTED", "E_PRODUCT_NOT_PERMITTED",
      db=[["Account", eq["account_id"], {"permitted_products": "BOND"}]])
    R("R003", 2, "Account with an empty permission list is rejected", eq, {}, "REJECTED", "E_PRODUCT_NOT_PERMITTED",
      db=[["Account", eq["account_id"], {"permitted_products": ""}]])
    other = next(a for a in accounts if a["client_id"] != eq["client_id"])
    R("R003", 3, "Account belonging to another client is rejected", eq, {"account_id": other["account_id"]}, "REJECTED", "E_ACCOUNT_CLIENT_MISMATCH")
    # R004 complex products need professional clients
    for i, base in enumerate([fut, opt, irs, repo], 1):
        R("R004", i, f"RETAIL client cannot trade {base['product_type']}", base, {}, "REJECTED", "E_CLIENT_CATEGORY",
          db=[["Client", base["client_id"], {"client_category": "RETAIL"}]])
    R("R004", 5, "RETAIL client can trade cash EQUITY", eq, {}, "ACCEPTED", db=[["Client", eq["client_id"], {"client_category": "RETAIL"}]])
    # R005 instrument inactive / bond on maturity
    R("R005", 1, "Inactive instrument is rejected", eq, {}, "REJECTED", "E_INSTRUMENT_EXPIRED", special={"instrument_status": "INACTIVE"})
    R("R005", 2, "Bond traded on its maturity date is rejected", gb, {}, "REJECTED", "E_INSTRUMENT_EXPIRED", special={"maturity_is_trade_date": True})
    # R006 exposure limit boundaries
    R("R006", 1, "Exposure exactly at 100% of the limit is accepted (with a warning)", eq, {}, "ACCEPTED", special={"limit": "exact"}, warning="W_LIMIT_WARNING")
    R("R006", 2, "Exposure one dollar above the limit is rejected", eq, {}, "REJECTED", "E_LIMIT_BREACH", special={"limit": "below"})
    R("R006", 3, "Exposure at 90% of the limit warns", eq, {}, "ACCEPTED", special={"limit": "ninety"}, warning="W_LIMIT_WARNING")
    R("R006", 4, "Exposure at 89% of the limit has no warning", eq, {}, "ACCEPTED", special={"limit": "eightynine"})
    # R007 quantity
    lot = 100
    for i, (q, oc, code) in enumerate([(0, "REJECTED", "E_QTY_INVALID"), (-100, "REJECTED", "E_QTY_INVALID"),
                                       (lot, "ACCEPTED", None), (137, "REJECTED", "E_LOT_SIZE"), (lot * 3, "ACCEPTED", None),
                                       (lot + 1, "REJECTED", "E_LOT_SIZE")], 1):
        R("R007", i, f"Tokyo equity (lot {lot}) quantity {q}", eq_tks, {"quantity": q, "allocations": "single"}, oc, code)
    R("R007", 7, "Fractional futures contracts are rejected", fut, {"quantity": 1.5, "allocations": "single"}, "REJECTED", "E_LOT_SIZE")
    # R008 price collar
    for i, (f, oc) in enumerate([("1.10", "ACCEPTED"), ("1.1001", "REJECTED"), ("0.90", "ACCEPTED"), ("0.8999", "REJECTED")], 1):
        R("R008", i, f"Equity price at {f} x reference price", eq_ny, {"settlement_date": None}, oc, "E_PRICE_COLLAR" if oc == "REJECTED" else None,
          special={"price_vs_ref": f})
    for i, (pts, oc) in enumerate([("5", "ACCEPTED"), ("5.01", "REJECTED"), ("-5", "ACCEPTED"), ("-5.01", "REJECTED")], 5):
        R("R008", i, f"Bond price at reference {pts} points", cb, {"settlement_date": None}, oc, "E_PRICE_COLLAR" if oc == "REJECTED" else None,
          special={"price_plus_ref": pts})
    R("R008", 9, "FX spot rate 2.5% away from the reference rate is rejected", fxs2, {"fx_counter_amount": None}, "REJECTED", "E_PRICE_COLLAR",
      special={"fx_rate_vs_ref": "1.025"})
    # R009 stale price: EQ011's price feed stops; 3 business days after the last price is accepted, 4 is stale
    last = max(_d(p["price_date"]) for p in ref["prices"] if p["instrument_id"] == "EQ011")
    for i, (n, oc) in enumerate([(3, "ACCEPTED"), (4, "REJECTED")], 1):
        d = ora.add_business_days(last, n, [ref["hol"]["XLON"]])
        R("R009", i, f"EQ011 traded {d}: last reference price {last} is {n} business days old", eq,
          {"instrument_id": "EQ011", "product_type": "EQUITY", "trade_date": d.isoformat(), "settlement_date": None, "trade_ccy": "GBP",
           "settle_ccy": "GBP", "trade_time_utc": None, "order_id": None}, oc, "E_STALE_PRICE" if oc == "REJECTED" else None,
          special={"price_vs_ref": "1"})
    # R010 trade date on holidays / weekends
    for i, d in enumerate(["2026-11-26", "2026-12-25", "2026-09-07", "2026-10-10"], 1):
        R("R010", i, f"US equity traded on {d} (closed)", eq_ny, {"trade_date": d, "settlement_date": None, "trade_time_utc": None, "order_id": None},
          "REJECTED", "E_TRADE_DATE_HOLIDAY")
    # R011 settlement date convention
    for i, delta in enumerate([1, -1], 1):
        R("R011", i, f"Equity settlement date {'+' if delta > 0 else ''}{delta} business day from convention", eq_ny,
          {}, "REJECTED", "E_SETTLE_DATE_MISMATCH", special={"settle_shift_days": delta})
    R("R011", 3, "Bond settlement date one day late", gb, {}, "REJECTED", "E_SETTLE_DATE_MISMATCH", special={"settle_shift_days": 1})
    R("R011", 4, "Settlement date omitted is derived from the convention", eq_ny, {"settlement_date": None}, "ACCEPTED")
    # R014 trade date from local time
    R("R014", 1, "Tokyo trade executed 23:30 UTC the previous day keeps the local date", eq_tks,
      {"trade_time_utc": "PREV_DAY_2330"}, "ACCEPTED")
    R("R014", 2, "Tokyo trade stamped 15:30 UTC the same day is local next day and rejected", eq_tks,
      {"trade_time_utc": "SAME_DAY_1530"}, "REJECTED", "E_TRADE_DATE_TZ")
    # R024 allocations
    for i, (off, oc) in enumerate([(0, "ACCEPTED"), (1, "REJECTED"), (-1, "REJECTED")], 1):
        R("R024", i, f"Allocations summing to block {'+' if off >= 0 else ''}{off}", eq_ny, {"allocations": f"offset:{off}"}, oc,
          "E_ALLOC_QTY_MISMATCH" if oc == "REJECTED" else None)
    # R028 currency precision, R042 counter amount, R043 forward points
    R("R028", 1, "JPY counter amount with decimals is rejected", fxs, {}, "REJECTED", "E_CCY_PRECISION", special={"counter_add": "0.5", "ccy": "JPY"})
    for i, (add, oc) in enumerate([("0", "ACCEPTED"), ("MINOR1", "ACCEPTED"), ("MINOR2", "REJECTED")], 1):
        R("R042", i, f"FX counter amount off by {add.replace('MINOR', '')} minor unit(s)" if add != "0" else "FX counter amount exact",
          fxs2, {}, oc, "E_FX_COUNTER_MISMATCH" if oc == "REJECTED" else None, special={"counter_add": add})
    R("R043", 1, "Forward outright equal to spot + points is accepted", fxf, {}, "ACCEPTED")
    R("R043", 2, "Forward outright one pip away from spot + points is rejected", fxf, {}, "REJECTED", "E_FX_POINTS_MISMATCH",
      special={"points_add": "1"})
    # R029 duplicates
    R("R029", 1, "Same source reference twice is rejected", eq, {"source_trade_ref": "KEEP"}, "REJECTED", "E_DUPLICATE")
    R("R029", 2, "Same economics with a new reference warns of a potential duplicate", eq, {}, "ACCEPTED", warning="W_POTENTIAL_DUPLICATE")
    # R034 bond denomination
    gb_attrs = ref["bonds"][gb["instrument_id"]]
    md, inc = int(gb_attrs["min_denomination"]), int(gb_attrs["min_increment"])
    below = md - inc if md > inc else md // 2
    for i, (q, oc) in enumerate([(md, "ACCEPTED"), (below, "REJECTED"), (md + inc, "ACCEPTED"), (md + inc // 2, "REJECTED")], 1):
        R("R034", i, f"Bond nominal {q:,} (minimum {md:,}, increment {inc:,})", gb, {"quantity": q, "allocations": "single"}, oc,
          "E_DENOMINATION" if oc == "REJECTED" else None)
    # R052 futures last trading day: a future whose last trading day falls inside the priced window
    fut_ltd = sorted((d for d in ref["derivs"] if d["instrument_id"] in ref["futures"]
                      and date(2026, 9, 15) <= _d(d["last_trading_day"]) <= date(2026, 11, 5)), key=lambda d: d["instrument_id"])
    if fut_ltd:
        iid = fut_ltd[0]["instrument_id"]
        for i, (off, oc) in enumerate([(0, "ACCEPTED"), (1, "REJECTED")], 1):
            R("R052", i, f"Future {iid} traded on its last trading day{' + 1 business day' if off else ''}", fut,
              {"instrument_id": iid, "settlement_date": None, "trade_time_utc": None, "order_id": None, "allocations": "single"}, oc,
              "E_CONTRACT_EXPIRED" if oc == "REJECTED" else None, special={"trade_on_ltd_plus": off, "price_vs_ref": "1"})
    # R061 / R063 swaps
    R("R063", 1, "Swap maturity equal to the effective date is rejected", irs, {}, "REJECTED", "E_SWAP_DATES", special={"end_is_settle": True})
    R("R063", 2, "Swap maturity before the effective date is rejected", irs, {}, "REJECTED", "E_SWAP_DATES", special={"end_is_settle": -5})
    # R012 financing end date must follow the start date
    R("R012", 1, "Repo with an end date equal to the start date is rejected", repo, {}, "REJECTED", "E_SETTLE_DATE_HOLIDAY",
      special={"end_is_settle": True})
    # DQ01 / DQ02 / DQ04
    for i, f in enumerate(["instrument_id", "client_id", "account_id", "counterparty_id", "trade_date", "side", "quantity", "price",
                           "settle_ccy", "product_type"], 1):
        R("DQ01", i, f"Missing {f} is rejected", eq, {f: None}, "REJECTED", "E_REQUIRED_FIELD")
    for i, (f, v, code) in enumerate([("instrument_id", "EQ999", "E_UNKNOWN_INSTRUMENT"), ("client_id", "CL999", "E_UNKNOWN_CLIENT"),
                                      ("account_id", "AC9999", "E_UNKNOWN_ACCOUNT"), ("counterparty_id", "CP999", "E_UNKNOWN_COUNTERPARTY")], 1):
        R("DQ02", i, f"Unknown {f.replace('_id', '')} {v} is rejected", eq, {f: v}, "REJECTED", code)
    R("DQ04", 1, "Product type that does not fit the instrument is rejected", eq, {"product_type": "GOVT_BOND"}, "REJECTED", "E_DOMAIN")
    _ = cl
    return out


def lifecycle_cases() -> list[dict]:
    """Every allowed edge of each product's documented lifecycle, plus skip-ahead, backwards and from-terminal moves."""
    out = []
    for ptype, edges in specs.LIFECYCLE.items():
        nodes = sorted({a for a, _ in edges} | {b for _, b in edges})
        allowed = set(edges)
        tried = set()
        for a, b in sorted(allowed):
            tried.add((a, b))
            out.append(C(f"TC-LC-{ptype}-{a}-{b}", f"SC-LC-{ptype}", "lifecycle", f"{ptype}: {a} -> {b} is allowed", ["R099"],
                         "lifecycle", {"product_type": ptype, "from": a, "to": b}, {"allowed": True}, "testkit/oracle/specs.py LIFECYCLE (docs/lifecycle_matrix.md)"))
        path = specs.MAIN_PATH[ptype]
        bad = []
        for i, a in enumerate(path):
            bad += [(a, b) for b in path[i + 2:i + 3]]          # skip one stage
            bad += [(a, b) for b in path[max(0, i - 2):i - 1]]  # go back two stages
        bad += [(t, "CAPTURED") for t in nodes if t in specs.TERMINAL]
        bad += [("CANCELLED", path[1])]
        for a, b in bad:
            if (a, b) in tried or (a, b) in allowed:
                continue
            tried.add((a, b))
            out.append(C(f"TC-LC-{ptype}-{a}-{b}", f"SC-LC-{ptype}", "lifecycle", f"{ptype}: {a} -> {b} is refused (R099)", ["R099"],
                         "lifecycle", {"product_type": ptype, "from": a, "to": b}, {"allowed": False, "error": "E_INVALID_TRANSITION"},
                         "testkit/oracle/specs.py LIFECYCLE (docs/lifecycle_matrix.md)"))
    return out


def permission_cases() -> list[dict]:
    out = []
    for ep in specs.ENDPOINTS:
        for role in specs.ROLE_USERS:
            ok = role in ep["roles"]
            out.append(C(f"TC-PERM-{ep['id']}-{role}", f"SC-PERM-{role}", "access", f"{role} {'may' if ok else 'may not'} {ep['action']}",
                         ["R092"] if ep["id"].startswith("APPROVE") else [], "permission",
                         {"method": ep["method"], "path": ep["path"], "body": ep.get("body"), "role": role},
                         {"forbidden": not ok}, "testkit/oracle/specs.py ROLE matrix (brief: 7 role workspaces)", layer="api"))
        out.append(C(f"TC-PERM-{ep['id']}-ANON", "SC-PERM-ANONYMOUS", "access", f"Anonymous caller is refused: {ep['action']}", [],
                     "permission", {"method": ep["method"], "path": ep["path"], "body": ep.get("body"), "role": None},
                     {"status": 401}, "API contract: every endpoint except login/health needs a token", layer="api"))
    return out


def outcome_cases() -> list[dict]:
    """Seeded controlled outcomes (matching, settlement fails, partials, recon breaks) as labelled in defect_labels.csv."""
    out = []
    for i, r in enumerate(_csv("defect_labels.csv"), 1):
        rule = {"Confirmations": "R082", "Settlement_Status": "R086", "Opening_Positions": "R085", "Custody_Positions": "R096",
                "Custody_Cash": "R096", "Trades": "R029"}[r["artifact"]]
        out.append(C(f"TC-OUT-{i:03d}", f"SC-OUT-{r['artifact'].upper()}", "outcomes", f"{r['artifact']} {r['key']}: {r['label']} -> {r['expected_outcome']}",
                     [rule], "label_outcome", {"artifact": r["artifact"], "key": r["key"], "label": r["label"]},
                     {"outcome": r["expected_outcome"]}, "data/oracle/defect_labels.csv", layer="api", priority="P1"))
    return out


def api_cases() -> list[dict]:
    """Hand-written API and workflow checks (executor functions in testkit/execute.py, API_CHECKS)."""
    return [C(f"TC-API-{k}", sc, "api", title, rules, "api_check", {"check": k}, {"pass": True}, "API contract / workflow spec", layer="api",
              priority="P1")
            for k, sc, title, rules in specs.API_CHECKS]


def worked_example_cases() -> list[dict]:
    from testkit.worked_examples import EXAMPLES
    return [C(f"TC-WE-{e['id']}", "SC-CALC-WORKED", "calculation", f"Worked example {e['id']}: {e['title']}", e["rules"], "worked_example",
              {"id": e["id"]}, {"value": e["expected"]}, "testkit/worked_examples.py (hand calculation)", priority="P1") for e in EXAMPLES]


LAKEHOUSE_CHECKS = ["baseline landing", "baseline batch passes", "silver_trades holds 500", "gold: allocated quantity",
                    "silver trades match the oracle", "silver trades match the platform", "re-running the same batch ingests",
                    "re-running the same batch changes no silver", "schema drift is refused", "quarantine catches every case",
                    "quarantine catches no case", "negative batch fails the quality gate", "silver is unchanged",
                    "full rebuild from the landing zone"]


def lakehouse_cases() -> list[dict]:
    """Executed by lakehouse/run_local.py on Spark + Delta; results are read from its evidence file."""
    rules = {2: ["DQ01", "DQ02", "DQ04"], 6: ["DQ03"], 7: ["DQ03"], 8: ["DQ05"], 9: ["DQ01", "DQ02", "R007"], 13: ["DQ03"]}
    return [C(f"TC-LAKE-{i + 1:02d}", "SC-LAKEHOUSE", "lakehouse", f"Lakehouse: {k}", rules.get(i, []), "lakehouse_evidence",
              {"check_prefix": k}, {"result": "PASS"}, "lakehouse/run_local.py -> lakehouse/evidence/local_run.json", layer="lakehouse")
            for i, k in enumerate(LAKEHOUSE_CHECKS)]


def ui_cases() -> list[dict]:
    """Browser cases, run by Playwright on every configured browser (frontend/e2e)."""
    return [C(f"TC-UI-{k}", sc, "ui", title, rules, "playwright", {"spec": k}, {"pass": True}, "frontend/e2e specs", layer="ui")
            for k, sc, title, rules in specs.UI_CASES]


def build() -> list[dict]:
    x = DATA / "excel"
    trades = _read(x / "Trades.xlsx")["Trades"]
    inst = _read(x / "Instruments.xlsx")
    clients = _read(x / "Clients.xlsx")
    hol = holiday_sets(build_holidays())
    ref = {"clients": clients["Clients"], "accounts": _read(x / "Accounts.xlsx")["Accounts"],
           "bonds": {b["instrument_id"]: b for b in inst["Bond_Details"]}, "prices": _read(x / "Prices_FX_Rates.xlsx")["Prices"],
           "hol": hol, "derivs": inst["Derivative_Details"],
           "futures": {i["instrument_id"] for i in inst["Instruments"] if i["product_type"] == "FUTURE"}}
    cases = (book_cases(trades) + negative_cases() + fx_date_cases(hol) + accrued_cases(inst) + split_cases() + rule_cases(trades, ref)
             + worked_example_cases() + lifecycle_cases() + permission_cases() + outcome_cases() + api_cases() + lakehouse_cases()
             + ui_cases())
    ids = Counter(c["case_id"] for c in cases)
    dupes = [k for k, v in ids.items() if v > 1]
    if dupes:
        raise ValueError(f"duplicate case ids: {dupes[:5]}")
    return cases


def write(cases: list[dict]) -> dict:
    import pandas as pd
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cases.json").write_text(json.dumps(cases, indent=1, default=str))
    flat = pd.DataFrame([{**{k: v for k, v in c.items() if k not in ("params", "expected", "rule_ids")}, "rule_ids": ",".join(c["rule_ids"]),
                          "params": json.dumps(c["params"], default=str), "expected": json.dumps(c["expected"], default=str)} for c in cases])
    flat.to_parquet(OUT / "cases.parquet", index=False)
    scen = flat.groupby(["scenario_id", "suite"]).agg(cases=("case_id", "count")).reset_index()
    from cf360.rules.catalogue import RULES
    trace = []
    for r in RULES:
        ids = [c["case_id"] for c in cases if r["id"] in c["rule_ids"]]
        trace.append({"rule_id": r["id"], "family": r["family"], "rule": r["name"], "cases": len(ids),
                      "suites": ",".join(sorted({c["suite"] for c in cases if r["id"] in c["rule_ids"]})), "example_cases": ", ".join(ids[:5])})
    tr = pd.DataFrame(trace)
    with pd.ExcelWriter(OUT / "cases.xlsx") as w:
        flat.to_excel(w, sheet_name="Cases", index=False)
        scen.to_excel(w, sheet_name="Scenarios", index=False)
        tr.to_excel(w, sheet_name="Traceability", index=False)
    tr.to_excel(OUT / "traceability.xlsx", index=False)
    summary = {"cases": len(cases), "scenarios": int(scen.shape[0]), "by_suite": dict(Counter(c["suite"] for c in cases)),
               "by_layer": dict(Counter(c["layer"] for c in cases)), "rules_covered": int((tr["cases"] > 0).sum()),
               "rules_total": len(RULES), "rules_uncovered": tr.loc[tr["cases"] == 0, "rule_id"].tolist()}
    (OUT / "catalogue_summary.json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT / "backend"))
    print(json.dumps(write(build()), indent=1))
