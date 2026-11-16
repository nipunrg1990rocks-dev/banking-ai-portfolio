"""Compare the platform's persisted state with the independent oracle (data/oracle/*.csv).

    python -m testkit.compare_oracle [--json out.json]

Checks per-trade economics, allocation amounts, matching outcomes, settlement status per
instruction, and positions/cash at the statement date. Prints every difference; exit code 1 if any.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "data" / "oracle"
DSN = os.getenv("CF360_PG_DSN", "postgresql://cf360:cf360@localhost:5432/cf360")
ECON_FIELDS = ["gross", "commission", "exchange_fee", "tax", "net", "principal", "accrued", "accrued_per_100", "dirty_price",
               "counter_amount", "premium", "notional", "initial_margin", "cash_amount", "repo_interest", "repurchase_amount",
               "collateral_amount", "loan_fee", "first_fixed_amount", "first_float_amount", "settle_amount",
               "fixed_periods", "float_periods"]
DATE_FIELDS = ["settlement_date", "first_fixed_payment_date", "first_float_payment_date", "maturity_date"]


def rows(name):
    with open(ORACLE / name) as f:
        return list(csv.DictReader(f))


def D(x):
    return Decimal(str(x)) if x not in (None, "") else None


def compare(dsn: str = DSN) -> dict:
    diffs: list[str] = []
    checks = 0
    with psycopg.connect(dsn) as c:
        cur = c.cursor()
        cur.execute("select trade_id, settlement_date, economics, match_status from trades where sandbox_id is null")
        db = {r[0]: r for r in cur.fetchall()}
        for e in rows("expected_trades.csv"):
            t = db.get(e["trade_id"])
            if t is None:
                diffs.append(f"{e['trade_id']}: missing in platform")
                continue
            econ = t[2] or {}
            for f in ECON_FIELDS:
                if e.get(f) not in (None, ""):
                    checks += 1
                    if D(econ.get(f)) != D(e[f]):
                        diffs.append(f"{e['trade_id']} {f}: platform {econ.get(f)} oracle {e[f]}")
            checks += 1
            if str(t[1]) != e["settlement_date"]:
                diffs.append(f"{e['trade_id']} settlement_date: platform {t[1]} oracle {e['settlement_date']}")
            for f in DATE_FIELDS[1:]:
                if e.get(f):
                    checks += 1
                    if str(econ.get(f)) != e[f]:
                        diffs.append(f"{e['trade_id']} {f}: platform {econ.get(f)} oracle {e[f]}")
        for e in rows("expected_matching.csv"):
            checks += 1
            got = db[e["trade_id"]][3]
            want = e["outcome"]
            if (got or "UNMATCHED_MISSING") != want:
                diffs.append(f"{e['trade_id']} matching: platform {got} oracle {want}")
        cur.execute("select allocation_id, settle_amount from allocations a join trades t using(trade_id) where t.sandbox_id is null")
        alloc = {r[0]: r[1] for r in cur.fetchall()}
        for e in rows("expected_allocations.csv"):
            checks += 1
            if D(alloc.get(e["allocation_id"])) != D(e["settle_amount"]):
                diffs.append(f"{e['allocation_id']} alloc amount: platform {alloc.get(e['allocation_id'])} oracle {e['settle_amount']}")
        cur.execute("select allocation_id, status, settled_on, settled_qty, settled_cash from settlement_instructions where sandbox_id is null")
        ins = {r[0]: r for r in cur.fetchall()}
        for e in rows("expected_settlements.csv"):
            checks += 1
            got = ins.get(e["allocation_id"])
            want = e["status"]
            if want == "NOT_DUE":
                if got and got[1] not in ("PENDING",):
                    diffs.append(f"{e['allocation_id']} settlement: platform {got[1]} oracle NOT_DUE")
                continue
            if got is None:
                diffs.append(f"{e['allocation_id']} settlement: platform none oracle {want}")
                continue
            if got[1] != want or (e["settled_on"] and str(got[2]) != e["settled_on"]):
                diffs.append(f"{e['allocation_id']} settlement: platform {got[1]} on {got[2]} oracle {want} on {e['settled_on']}")
        stmt = "2026-11-06"
        cur.execute("""select ledger_account, key, sum(amount) from ledger_entries where ledger='SEC' and value_date <= %s
                       and sandbox_id is null and position(':' in ledger_account)=0 group by 1,2""", (stmt,))
        pos = {(a, k): v for a, k, v in cur.fetchall()}
        exp_pos = {(r["account_id"], r["instrument_id"]): D(r["quantity"]) for r in rows("expected_positions.csv")}
        for k in sorted(set(pos) | set(exp_pos)):
            a, b = D(pos.get(k, 0)), exp_pos.get(k, Decimal(0))
            checks += 1
            if a != b:
                diffs.append(f"position {k}: platform {a} oracle {b}")
        cur.execute("""select ledger_account, key, sum(amount) from ledger_entries where ledger='CASH' and value_date <= %s
                       and sandbox_id is null and position(':' in ledger_account)=0 group by 1,2""", (stmt,))
        cash = {(a, k): v for a, k, v in cur.fetchall()}
        exp_cash = {(r["account_id"], r["currency"]): D(r["balance"]) for r in rows("expected_cash.csv")}
        for k in sorted(set(cash) | set(exp_cash)):
            a, b = D(cash.get(k, 0)), exp_cash.get(k, Decimal(0))
            checks += 1
            if a != b:
                diffs.append(f"cash {k}: platform {a} oracle {b} diff {a - b}")
        # invariants: every posting balances per key; no residual on cancelled trades
        cur.execute("select posting_id, key, sum(amount) from ledger_entries group by 1,2 having sum(amount) <> 0")
        unbalanced = cur.fetchall()
        checks += 1
        if unbalanced:
            diffs.append(f"{len(unbalanced)} unbalanced postings e.g. {unbalanced[:3]}")
        cur.execute("""select a.trade_id from allocations a join trades t using(trade_id) where t.sandbox_id is null
                       group by a.trade_id, t.quantity having sum(a.alloc_qty) <> t.quantity""")
        bad = cur.fetchall()
        checks += 1
        if bad:
            diffs.append(f"allocation quantity not conserved: {bad[:5]}")
        cur.execute("""select a.trade_id from allocations a join trades t using(trade_id) where t.sandbox_id is null
                       and t.net_amount is not null group by a.trade_id, t.net_amount having sum(a.settle_amount) <> t.net_amount""")
        bad = cur.fetchall()
        checks += 1
        if bad:
            diffs.append(f"allocation amounts do not sum to the block: {bad[:5]}")
    return {"checks": checks, "differences": len(diffs), "details": diffs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    res = compare()
    for d in res["details"][:200]:
        print(d)
    print(f"oracle comparison: {res['checks']} checks, {res['differences']} differences")
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(res, indent=2))
    sys.exit(1 if res["differences"] else 0)


if __name__ == "__main__":
    main()
