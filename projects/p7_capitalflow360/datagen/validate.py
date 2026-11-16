"""Validate the generated dataset: counts, relational integrity, oracle separation, reproducibility.

    python -m datagen.validate [--reproducibility]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

from datagen.workbook import read_workbook

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_GROUPS = {"EQUITY": 140, "BOND": 120, "FX": 80, "LISTED_DERIV": 70, "IRS": 50, "FINANCING": 40}
FORBIDDEN_INPUT_COLUMNS = {"expected_outcome", "label", "defect_type", "expected_error", "expected_status"}


def load(data: Path) -> dict[str, list[dict]]:
    t = {}
    for f in sorted((data / "excel").glob("*.xlsx")):
        for k, v in read_workbook(f).items():
            t[k] = v
    return t


def check(data: Path) -> list[str]:
    errs = []
    t = load(data)
    trades = t["Trades"]
    if len(trades) != 500:
        errs.append(f"baseline trade count {len(trades)} != 500")
    if len({r["trade_id"] for r in trades}) != len(trades):
        errs.append("duplicate trade_id")
    c = Counter(r["product_group"] for r in trades)
    if dict(c) != EXPECTED_GROUPS:
        errs.append(f"group split {dict(c)}")
    ids = {k: {r[col] for r in t[sheet]} for k, sheet, col in [
        ("inst", "Instruments", "instrument_id"), ("acct", "Accounts", "account_id"), ("client", "Clients", "client_id"),
        ("order", "Orders", "order_id"), ("trade", "Trades", "trade_id"), ("cpty", "Counterparties", "counterparty_id"),
        ("alloc", "Allocations", "allocation_id")]}
    fk = [("Trades", "instrument_id", "inst"), ("Trades", "account_id", "acct"), ("Trades", "client_id", "client"),
          ("Trades", "order_id", "order"), ("Trades", "counterparty_id", "cpty"), ("Executions", "order_id", "order"),
          ("Allocations", "trade_id", "trade"), ("Allocations", "account_id", "acct"), ("Accounts", "client_id", "client"),
          ("Orders", "account_id", "acct"), ("Prices", "instrument_id", "inst"), ("Opening_Positions", "account_id", "acct"),
          ("Opening_Positions", "instrument_id", "inst"), ("Dividends", "instrument_id", "inst"),
          ("Coupons", "instrument_id", "inst"), ("Confirmations", "trade_id", "trade"),
          ("Custody_Positions", "account_id", "acct")]
    for sheet, col, ref in fk:
        bad = [r[col] for r in t[sheet] if r[col] not in ids[ref]]
        if bad and not (sheet == "Custody_Positions"):
            errs.append(f"{sheet}.{col}: {len(bad)} orphan values e.g. {bad[:3]}")
    # allocation conservation and fill conservation
    alloc_sum = Counter()
    for a in t["Allocations"]:
        alloc_sum[a["trade_id"]] += a["alloc_qty"]
    fill_sum = Counter()
    for e in t["Executions"]:
        fill_sum[e["order_id"]] += e["exec_qty"]
    for tr in trades:
        if alloc_sum[tr["trade_id"]] != tr["quantity"]:
            errs.append(f"allocation qty mismatch {tr['trade_id']}")
        if fill_sum[tr["order_id"]] != tr["quantity"]:
            errs.append(f"fill qty mismatch {tr['trade_id']}")
    traded_orders = {tr["order_id"] for tr in trades}
    for o in t["Orders"]:
        if o["order_id"] not in traded_orders and o["status"] in ("FILLED", "PARTIALLY_FILLED_DONE"):
            errs.append(f"filled order without trade {o['order_id']}")
    for sheet, rows in t.items():
        if rows and FORBIDDEN_INPUT_COLUMNS & set(rows[0]):
            errs.append(f"oracle column leaked into input sheet {sheet}")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reproducibility", action="store_true")
    a = ap.parse_args()
    data = ROOT / "data"
    errs = check(data)
    result = {"relational_checks": "PASS" if not errs else "FAIL", "errors": errs}
    if a.reproducibility:
        from datagen import generate
        with tempfile.TemporaryDirectory() as tmp:
            m2 = generate.main(Path(tmp))
        m1 = json.loads((data / "manifest.json").read_text())
        diff = [k for k in m1["files"] if m1["files"][k]["sha256"] != m2["files"][k]["sha256"]]
        result["reproducibility"] = "PASS" if not diff else f"FAIL {diff}"
    print(json.dumps(result, indent=2))
    sys.exit(1 if errs or (a.reproducibility and result["reproducibility"] != "PASS") else 0)


if __name__ == "__main__":
    main()
