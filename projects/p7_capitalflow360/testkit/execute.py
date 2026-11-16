"""Execute the test case catalogue against a freshly seeded test database and record every result.

    python -m testkit.execute --run-id CLEAN-001                    # all non-browser cases, no defects
    python -m testkit.execute --run-id DEFECT-D3 --defects D3       # same cases with defect D3 switched on
    python -m testkit.execute ... --import-to http://localhost:8000 # also load the results into the platform

Uses database CF360_TEST_DATABASE_URL (default cf360_test), which is dropped and rebuilt with Alembic
and the baseline seed at the start of every run, so runs are independent and repeatable.
Browser cases (layer "ui") are executed by Playwright (frontend/e2e) and merged by testkit/merge_results.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
TEST_DB = os.getenv("CF360_TEST_DATABASE_URL", "postgresql+psycopg://cf360:cf360@localhost:5432/cf360_test")
os.environ["CF360_DATABASE_URL"] = TEST_DB
os.environ["CF360_TEST_SUPPORT"] = "true"
os.environ.setdefault("CF360_JWT_SECRET", "test-only-secret-at-least-32-bytes-long!!")
os.environ.pop("CF360_DEFECTS", None)
sys.path[:0] = [str(BACKEND), str(ROOT)]

from sqlalchemy import select, text  # noqa: E402

from cf360 import lifecycle  # noqa: E402
from cf360 import models as m  # noqa: E402
from cf360.config import settings  # noqa: E402
from cf360.rules import calc, engine  # noqa: E402
from cf360.services import booking, loader, refcache  # noqa: E402
from datagen.calendars import build_holidays, holiday_sets  # noqa: E402
from testkit.oracle import reference as ora  # noqa: E402
from testkit.oracle import specs  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
PASSWORD = os.getenv("CF360_DEMO_PASSWORD", "Demo!2026")
CCY_CAL = {"USD": "USNY", "EUR": "EUTA", "GBP": "GBLO", "JPY": "JPTO", "CAD": "CATO", "AUD": "AUSY", "CHF": "CHZU"}


class Fail(AssertionError):
    pass


def expect(cond, msg):
    if not cond:
        raise Fail(msg)


def D(x):
    return Decimal(str(x)) if x not in (None, "") else None


def _day(v):
    return v.date() if isinstance(v, datetime) else v


# ------------------------------------------------------------------------------------------------ context
class Ctx:
    def __init__(self):
        from fastapi.testclient import TestClient

        from cf360.db import SessionLocal
        from cf360.main import app
        self.Session = SessionLocal
        self.client = TestClient(app)
        self._tokens: dict[str, str] = {}
        x = ROOT / "data" / "excel"
        self.trades = {r["trade_id"]: r for r in loader.read(x / "Trades.xlsx")["Trades"]}
        self.allocs: dict[str, list[dict]] = {}
        for r in loader.read(x / "Allocations.xlsx")["Allocations"]:
            self.allocs.setdefault(r["trade_id"], []).append(r)
        neg = ROOT / "data" / "negative"
        self.neg_trades = {r["trade_id"]: r for r in loader.read(neg / "Trades_negative.xlsx")["Trades"]}
        self.neg_allocs: dict[str, list[dict]] = {}
        for r in loader.read(neg / "Allocations_negative.xlsx")["Allocations"]:
            self.neg_allocs.setdefault(r["trade_id"], []).append(r)
        o = ROOT / "data" / "oracle"
        self.exp_trades = {r["trade_id"]: r for r in csv.DictReader(open(o / "expected_trades.csv"))}
        self.exp_allocs: dict[str, list[dict]] = {}
        for r in csv.DictReader(open(o / "expected_allocations.csv")):
            self.exp_allocs.setdefault(r["trade_id"], []).append(r)
        self.hol = holiday_sets(build_holidays())
        self.ccy_hols = {c: self.hol[code] for c, code in CCY_CAL.items()}
        self.bonds = {r["instrument_id"]: r for r in loader.read(x / "Instruments.xlsx")["Bond_Details"]}

    def auth(self, user: str) -> dict:
        if user not in self._tokens:
            r = self.client.post("/api/auth/login", json={"username": user, "password": PASSWORD})
            self._tokens[user] = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._tokens[user]}"}


def payload_from_row(r: dict, allocs: list[dict] | None, ref_suffix: str | None = "-TK") -> dict:
    p = {k: (loader._dec(v) if isinstance(v, float) else v) for k, v in r.items() if k != "negative_case_id"}
    p.pop("trade_id", None)
    if ref_suffix and p.get("source_trade_ref"):
        p["source_trade_ref"] = f"{p['source_trade_ref']}{ref_suffix}"
    if allocs is not None:
        p["allocations"] = [{"account_id": a["account_id"], "alloc_qty": loader._dec(a["alloc_qty"]), "alloc_seq": a["alloc_seq"]}
                            for a in sorted(allocs, key=lambda a: a["alloc_seq"])]
    return p


def dry_book(s, p: dict):
    """Book inside a savepoint that is always rolled back. Returns (trade_dict, allocations, None) or (None, None, issues)."""
    sp = s.begin_nested()
    try:
        t = booking.book(s, p, ("testkit", "SYSTEM"), "DRYRUN", reject_log=False)
        al = list(s.scalars(select(m.Allocation).where(m.Allocation.trade_id == t.trade_id).order_by(m.Allocation.alloc_seq)))
        snap = {"trade_id": t.trade_id, "settlement_date": t.settlement_date, "economics": dict(t.economics or {}), "flags": list(t.flags or [])}
        return snap, [(a.alloc_seq, a.alloc_qty, a.settle_amount) for a in al], None
    except booking.BookingError as e:
        return None, None, e.issues
    finally:
        sp.rollback()


def _codes(issues) -> set[tuple[str, str]]:
    return {(i["rule_id"], i["code"]) for i in issues or []}


# ------------------------------------------------------------------------------------------------ executors
def ex_book_oracle(c, ctx, s):
    tid = c["params"]["trade_id"]
    from testkit.compare_oracle import ECON_FIELDS
    t, allocs, issues = dry_book(s, payload_from_row(ctx.trades[tid], ctx.allocs.get(tid, [])))
    expect(issues is None, f"booking rejected: {sorted(_codes(issues))}")
    e = ctx.exp_trades[tid]
    diffs = [f"{f}: got {t['economics'].get(f)} want {e[f]}" for f in ECON_FIELDS
             if e.get(f) not in (None, "") and D(t["economics"].get(f)) != D(e[f])]
    if str(t["settlement_date"]) != e["settlement_date"]:
        diffs.append(f"settlement_date: got {t['settlement_date']} want {e['settlement_date']}")
    want = [D(a["settle_amount"]) for a in sorted(ctx.exp_allocs.get(tid, []), key=lambda a: a["allocation_id"])]
    got = [D(a[2]) for a in allocs]
    if want and got != want:
        diffs.append(f"allocation amounts: got {got} want {want}")
    expect(not diffs, "; ".join(diffs[:4]))
    return [f"{len(ECON_FIELDS)} fields, settlement date and {len(got)} allocation(s) compared with the oracle"]


def ex_neg_trade(c, ctx, s):
    e = c["expected"]
    t, _, issues = dry_book(s, payload_from_row(ctx.neg_trades[c["params"]["trade_id"]], None, ref_suffix=None))
    if e["outcome"] == "REJECTED":
        expect(issues is not None, f"accepted, expected {e['rule_id']} {e['error']}")
        expect((e["rule_id"], e["error"]) in _codes(issues), f"rejected with {sorted(_codes(issues))}, expected {e['rule_id']} {e['error']}")
        return [f"rejected: {sorted(_codes(issues))}"]
    expect(issues is None, f"rejected with {sorted(_codes(issues))}, expected acceptance ({e['layer']})")
    return [f"accepted as {t['trade_id']} flags={t['flags']}"]


def ex_neg_alloc(c, ctx, s):
    base = c["params"]["base_trade_id"]
    p = payload_from_row(ctx.trades[base], ctx.neg_allocs[base])
    _, _, issues = dry_book(s, p)
    expect(issues is not None and ("R024", "E_ALLOC_QTY_MISMATCH") in _codes(issues), f"got {sorted(_codes(issues))}")
    return [f"rejected: {sorted(_codes(issues))}"]


def ex_schema_drift(c, ctx, s):
    sys.path.insert(0, str(ROOT / "lakehouse"))
    from cf360_lake.contract import build_contract
    from cf360_lake.convert import convert
    with tempfile.TemporaryDirectory() as tmp:
        man = convert([ROOT / "data" / "negative" / c["params"]["file"]], Path(tmp), "TK", build_contract(ROOT / "data" / "excel"))
    expect(man["tables"] == {} and [r["rule_id"] for r in man["rejected"]] == ["DQ05"], f"manifest {man['rejected']}")
    return [json.dumps(man["rejected"][0])]


def ex_fx_date(c, ctx, s):
    p = c["params"]
    td = date.fromisoformat(p["trade_date"])
    prod = calc.fx_value_date(refcache.get(s).cals, td, p["base"], p["quote"], p["months"])
    want = (ora.fx_forward_date(td, p["base"], p["quote"], p["months"], ctx.ccy_hols) if p["months"]
            else ora.fx_spot_date(td, p["base"], p["quote"], ctx.ccy_hols))
    expect(prod == want, f"platform {prod} oracle {want}")
    return [f"value date {prod}"]


def ex_accrued(c, ctx, s):
    p = c["params"]
    ref = refcache.get(s)
    inst = ref.instruments[p["instrument_id"]]
    settle = date.fromisoformat(p["settle"])
    ccy = inst.currency
    prod = calc.accrued_per_100(inst.attrs | {"issue_date": engine._d(inst.attrs["issue_date"]),
                                              "maturity_date": engine._d(inst.attrs["maturity_date"])}, settle, ref.cals, ccy)
    b = ctx.bonds[p["instrument_id"]]
    want = ora.accrued_interest_per_100(settle, b["coupon_rate_pct"], int(b["coupon_frequency"]), b["day_count"], _day(b["maturity_date"]),
                                        _day(b["issue_date"]), int(b["ex_dividend_bdays"] or 0), ctx.hol[CCY_CAL[ccy]])
    a, w = calc.q_dp(prod, 10), ora.round_dp(want, 10)
    expect(a == w, f"platform {a} oracle {w}")
    return [f"accrued per 100 = {a}"]


def ex_split(c, ctx, s):
    p = c["params"]
    total, qtys = Decimal(p["total"]), [Decimal(q) for q in p["qtys"]]
    prod = calc.split_amount(total, qtys, p["ccy"])
    want = ora.allocate(total, qtys, sum(qtys), p["ccy"])
    expect(sum(prod) == total, f"parts sum to {sum(prod)}, block is {total} (residual lost)")
    expect([D(x) for x in prod] == [D(x) for x in want], f"platform {prod} oracle {want}")
    return [f"parts {[str(x) for x in prod]}"]


def ex_worked(c, ctx, s):
    from testkit.worked_examples import EXAMPLES
    e = next(x for x in EXAMPLES if x["id"] == c["params"]["id"])
    p, o = str(e["prod"]()), str(e["oracle"]())
    expect(p == e["expected"] and o == e["expected"], f"hand {e['expected']} platform {p} oracle {o}")
    return [f"hand = platform = oracle = {p}"]


def _apply_db(s, ops):
    models = {"Client": m.Client, "Account": m.Account}
    for model, key, changes in ops:
        obj = s.get(models[model], key)
        for k, v in changes.items():
            col = getattr(models[model], k).type.python_type
            setattr(obj, k, date.fromisoformat(v) if col is date and isinstance(v, str) else v)
    s.flush()


def ex_rule_mutation(c, ctx, s):
    p, e = c["params"], c["expected"]
    base = ctx.trades[p["base"]]
    ref = refcache.get(s)
    pl = payload_from_row(base, ctx.allocs.get(p["base"], []), ref_suffix=f"-{c['case_id']}")
    pl["order_id"] = None  # fills consistency (R026) has its own cases
    for k, v in p["set"].items():
        if k == "allocations":
            if v == "single":
                pl["allocations"] = None
            elif v.startswith("offset:"):
                pl["allocations"] = [{"account_id": base["account_id"], "alloc_qty": D(base["quantity"]) + int(v.split(":")[1]), "alloc_seq": 1}]
        elif k == "trade_time_utc" and v == "PREV_DAY_2330":
            pl[k] = datetime.combine(_day(base["trade_date"]) - timedelta(1), datetime.min.time()).replace(hour=23, minute=30)
        elif k == "trade_time_utc" and v == "SAME_DAY_1530":
            pl[k] = datetime.combine(_day(base["trade_date"]), datetime.min.time()).replace(hour=15, minute=30)
        elif k == "source_trade_ref" and v == "KEEP":
            pl[k] = base["source_trade_ref"]
        elif k == "trade_date":
            pl[k] = datetime.fromisoformat(v) if v else None
        elif k == "quantity":
            pl[k] = D(v)
        else:
            pl[k] = v
    sp = s.begin_nested()
    restore = []
    try:
        _apply_db(s, p["db"])
        sp_ = p.get("special") or {}
        inst = ref.instruments.get(pl.get("instrument_id") or "")
        td = _day(pl["trade_date"]) if pl.get("trade_date") else None
        if "instrument_status" in sp_:
            restore.append((inst, "status", inst.status))
            inst.status = sp_["instrument_status"]
        if sp_.get("maturity_is_trade_date"):
            restore.append((inst, "maturity_date", inst.maturity_date))
            inst.maturity_date = td
        if "price_vs_ref" in sp_:
            pl["price"] = calc.q_dp(ref.price_on_or_before(inst.instrument_id, td)[1] * Decimal(sp_["price_vs_ref"]), 6)
        if "price_plus_ref" in sp_:
            pl["price"] = ref.price_on_or_before(inst.instrument_id, td)[1] + Decimal(sp_["price_plus_ref"])
        if "fx_rate_vs_ref" in sp_:
            a = inst.attrs
            pl["price"] = calc.q_dp(ref.usd_per(a["base_ccy"], td) / ref.usd_per(a["quote_ccy"], td) * Decimal(sp_["fx_rate_vs_ref"]), 6)
        if "settle_shift_days" in sp_:
            pl["settlement_date"] = _day(pl["settlement_date"]) + timedelta(days=sp_["settle_shift_days"])
        if "counter_add" in sp_:
            q = inst.attrs["quote_ccy"]
            exact = ora.fx_counter_amount(pl["quantity"], pl["price"], q)
            add = sp_["counter_add"]
            minor = Decimal(1).scaleb(-calc.MINOR[q])
            pl["fx_counter_amount"] = exact + (minor * int(add[5:]) if add.startswith("MINOR") else Decimal(add))
        if "points_add" in sp_:
            pl["fx_forward_points"] = D(pl["fx_forward_points"]) + Decimal(sp_["points_add"])
        if "trade_on_ltd_plus" in sp_:
            ltd = date.fromisoformat(str(inst.attrs["last_trading_day"]))
            d = ref.cals.add(ltd, sp_["trade_on_ltd_plus"], *engine.trade_calendars(ref, inst, pl["product_type"])) if sp_["trade_on_ltd_plus"] else ltd
            pl["trade_date"] = datetime.combine(d, datetime.min.time())
        if "end_is_settle" in sp_:
            sd = _day(pl["settlement_date"])
            pl["end_date"] = sd if sp_["end_is_settle"] is True else sd + timedelta(days=sp_["end_is_settle"])
        if "limit" in sp_:
            used = engine.validate(s, ref, booking.normalise(pl), "DRYRUN")[1]["exposure_used"]
            lim = {"exact": used, "below": used - 1, "ninety": (used / Decimal("0.9")).quantize(Decimal("0.01"), ROUND_DOWN),
                   "eightynine": (used / Decimal("0.89")).quantize(Decimal("0.01"), ROUND_UP)}[sp_["limit"]]
            _apply_db(s, [["Client", pl["client_id"], {"exposure_limit_usd": lim}]])
        t, _, issues = dry_book(s, pl)
    finally:
        for obj, attr, val in restore:
            setattr(obj, attr, val)
        sp.rollback()
    if e["outcome"] == "REJECTED":
        expect(issues is not None, f"accepted (flags {t and t['flags']}), expected {e['rule_id']} {e['error']}")
        expect((e["rule_id"], e["error"]) in _codes(issues), f"rejected with {sorted(_codes(issues))}, expected {e['rule_id']} {e['error']}")
        return [f"rejected: {sorted(_codes(issues))}"]
    expect(issues is None, f"rejected with {sorted(_codes(issues))}, expected acceptance")
    if e.get("warning"):
        expect(e["warning"] in t["flags"], f"accepted without warning {e['warning']} (flags {t['flags']})")
    if (p.get("special") or {}).get("limit") == "eightynine":
        expect("W_LIMIT_WARNING" not in t["flags"], "warned below 90% of the limit")
    return [f"accepted, flags {t['flags']}"]


def ex_lifecycle(c, ctx, s):
    p = c["params"]
    ok = p["to"] in lifecycle.allowed(p["product_type"], p["from"])
    expect(ok == c["expected"]["allowed"], f"server says {'allowed' if ok else 'refused'}")
    return [f"{p['from']} -> {p['to']}: {'allowed' if ok else 'refused'}"]


def ex_permission(c, ctx, s):
    p = c["params"]
    h = ctx.auth(specs.ROLE_USERS[p["role"]]) if p["role"] else {}
    kw = {"headers": h}
    if p["body"] is not None:
        kw["json"] = p["body"]
    r = ctx.client.request(p["method"], p["path"], **kw)
    if p["role"] is None:
        expect(r.status_code == 401, f"status {r.status_code}")
    elif c["expected"]["forbidden"]:
        expect(r.status_code == 403 and r.json()["error"]["code"] == "E_FORBIDDEN", f"status {r.status_code}")
    else:
        expect(r.status_code not in (401, 403) and r.status_code < 500, f"status {r.status_code}: {r.text[:120]}")
    return [f"{p['method']} {p['path']} as {p['role'] or 'anonymous'} -> {r.status_code}"]


def ex_label_outcome(c, ctx, s):
    p, want = c["params"], c["expected"]["outcome"]
    art, key = p["artifact"], p["key"]
    if art == "Confirmations":
        got = s.get(m.Trade, key).match_status or "UNMATCHED_MISSING"
        expect(got == want, f"match status {got}")
    elif art in ("Settlement_Status", "Opening_Positions"):
        ins = s.scalars(select(m.SettlementInstruction).where(m.SettlementInstruction.allocation_id == key,
                                                              m.SettlementInstruction.sandbox_id.is_(None))).first()
        got = f"{ins.status}" + (f" {ins.settled_on}" if ins.settled_on else "")
        if want.startswith("SETTLED_ON_"):
            expect(ins.status == "SETTLED" and str(ins.settled_on) == want[11:], f"instruction {got}")
        else:
            expect(ins.status == {"FAILED_OPEN": "FAILED", "PARTIAL": "PARTIAL", "FAILED": "FAILED"}[want], f"instruction {got}")
    elif art in ("Custody_Positions", "Custody_Cash"):
        acct, k = key.split("|")
        brk = s.scalars(select(m.ReconBreak).where(m.ReconBreak.account_id == acct, m.ReconBreak.key == k)).first()
        got = f"{brk.break_type} diff={brk.difference}" if brk else "NO_BREAK"
        if want == "NO_BREAK":
            expect(brk is None, got)
        elif "diff=" in want:
            expect(brk is not None and D(brk.difference) == D(want.split("diff=")[1]), got)
        else:
            expect(brk is not None and brk.break_type == want.replace("BREAK_", ""), got)
    elif art == "Trades":
        got = s.get(m.Trade, key).flags or []
        expect("W_POTENTIAL_DUPLICATE" in got, f"flags {got}")
    else:
        raise Fail(f"unknown artifact {art}")
    return [f"{art} {key}: {got}"]


def ex_lakehouse(c, ctx, s):
    ev = json.loads((ROOT / "lakehouse" / "evidence" / "local_run.json").read_text())
    hit = [x for x in ev["checks"] if x["check"].startswith(c["params"]["check_prefix"])]
    expect(len(hit) == 1, f"{len(hit)} evidence rows match")
    expect(hit[0]["result"] == "PASS", json.dumps(hit[0]["detail"])[:200])
    return [f"lakehouse/evidence/local_run.json: {hit[0]['check']} = PASS ({ev['environment']}, Spark {ev['spark']})"]


# ------------------------------------------------------------------------------------------------ API / workflow checks
def _trade_body(ctx, **over):
    src = ctx.client.get("/api/trades/TRD00010", headers=ctx.auth("mo1")).json()["trade"]
    body = {k: src[k] for k in ("product_type", "instrument_id", "client_id", "account_id", "counterparty_id", "side", "quantity",
                                "price", "settle_ccy")}
    body.update(trade_date="2026-11-06", source_trade_ref="TK-" + uuid.uuid4().hex[:10], book="EQ-FLOW")
    body.update(over)
    return body


def _book(ctx, **over):
    r = ctx.client.post("/api/trades", headers=ctx.auth("trader1"), json=_trade_body(ctx, **over))
    expect(r.status_code == 201, f"booking failed {r.status_code} {r.text[:200]}")
    return r.json()["trade"]


def api_check(name):
    def deco(fn):
        API[name] = fn
        return fn
    return deco


API: dict = {}


@api_check("IDEM-REPLAY")
def _(ctx):
    body, h = _trade_body(ctx), {**ctx.auth("trader1"), "Idempotency-Key": uuid.uuid4().hex}
    a, b = ctx.client.post("/api/trades", headers=h, json=body), ctx.client.post("/api/trades", headers=h, json=body)
    expect(a.status_code == b.status_code == 201, f"{a.status_code}/{b.status_code}")
    expect(a.json()["trade"]["trade_id"] == b.json()["trade"]["trade_id"] and b.headers.get("Idempotent-Replay") == "true", "second call booked again")
    n = ctx.client.get("/api/trades", headers=ctx.auth("mo1"), params={"q": body["source_trade_ref"]}).json()["total"]
    expect(n == 1, f"{n} trades with that reference")
    return [f"one trade {a.json()['trade']['trade_id']}, replay header true"]


@api_check("IDEM-CONFLICT")
def _(ctx):
    body, h = _trade_body(ctx), {**ctx.auth("trader1"), "Idempotency-Key": uuid.uuid4().hex}
    ctx.client.post("/api/trades", headers=h, json=body)
    c = ctx.client.post("/api/trades", headers=h, json={**body, "quantity": "300"})
    expect(c.status_code == 409 and c.json()["error"]["code"] == "E_IDEMPOTENCY_CONFLICT", f"{c.status_code} {c.text[:100]}")
    return ["409 E_IDEMPOTENCY_CONFLICT"]


@api_check("IFMATCH-MISSING")
def _(ctx):
    t = _book(ctx)
    r = ctx.client.patch(f"/api/trades/{t['trade_id']}", headers=ctx.auth("mo1"), json={"changes": {"book": "X"}, "reason": "no version"})
    expect(r.status_code == 428, f"{r.status_code}")
    return ["428"]


@api_check("IFMATCH-STALE")
def _(ctx):
    t = _book(ctx)
    h = {**ctx.auth("mo1"), "If-Match": "1"}
    ctx.client.patch(f"/api/trades/{t['trade_id']}", headers=h, json={"changes": {"book": "EQ-HEDGE"}, "reason": "first"})
    r = ctx.client.patch(f"/api/trades/{t['trade_id']}", headers=h, json={"changes": {"book": "Y"}, "reason": "stale"})
    expect(r.status_code == 409 and r.json()["error"]["code"] == "E_STALE_VERSION", f"{r.status_code} {r.text[:100]}")
    return ["409 E_STALE_VERSION"]


@api_check("AMEND-NONECON")
def _(ctx):
    t = _book(ctx)
    r = ctx.client.patch(f"/api/trades/{t['trade_id']}", headers={**ctx.auth("mo1"), "If-Match": "1"},
                         json={"changes": {"book": "EQ-HEDGE"}, "reason": "rebook to hedge book"})
    expect(r.status_code == 200 and r.json()["version"] == 2 and not r.json().get("approval_id"), r.text[:200])
    return ["version 2 without approval"]


def _econ_amend(ctx, maker="mo1"):
    t = _book(ctx)
    r = ctx.client.patch(f"/api/trades/{t['trade_id']}", headers={**ctx.auth(maker), "If-Match": "1"},
                         json={"changes": {"quantity": "300"}, "reason": "client corrected size"})
    expect(r.status_code == 200 and r.json().get("approval_id"), r.text[:200])
    return t, r.json()["approval_id"]


@api_check("AMEND-ECON-PENDING")
def _(ctx):
    t, apr = _econ_amend(ctx)
    d = ctx.client.get(f"/api/trades/{t['trade_id']}", headers=ctx.auth("mo1")).json()
    expect(not d["trade"]["quantity"].startswith("300"), "economic change applied before approval")
    expect(any(a["approval_id"] == apr and a["status"] == "PENDING" for a in d["approvals"]), "no pending approval")
    return [f"approval {apr} pending; quantity unchanged"]


@api_check("SELF-APPROVAL")
def _(ctx):
    t, apr = _econ_amend(ctx, "mo1")
    r = ctx.client.post(f"/api/approvals/{apr}/decision", headers=ctx.auth("mo1"), json={"approve": True})
    expect(r.status_code == 403, f"maker approved own request: {r.status_code} {r.text[:120]}")
    n = ctx.client.get("/api/audit", headers=ctx.auth("risk1"), params={"action": "APPROVAL_BLOCKED_SELF", "entity_id": apr}).json()["total"]
    expect(n == 1, f"{n} audit rows")
    return ["403 and audited"]


@api_check("MAKER-NOT-CHECKER")
def _(ctx):
    t, apr = _econ_amend(ctx, "trader1")
    r = ctx.client.post(f"/api/approvals/{apr}/decision", headers=ctx.auth("trader1"), json={"approve": True})
    expect(r.status_code == 403, f"trader approved own amendment: {r.status_code} {r.text[:120]}")
    return [f"{r.status_code} {r.json()['error']['code']}"]


@api_check("CHECKER-APPROVES")
def _(ctx):
    t, apr = _econ_amend(ctx)
    r = ctx.client.post(f"/api/approvals/{apr}/decision", headers=ctx.auth("checker1"), json={"approve": True})
    expect(r.status_code == 200, r.text[:200])
    d = ctx.client.get(f"/api/trades/{t['trade_id']}", headers=ctx.auth("mo1")).json()
    expect(d["trade"]["quantity"].startswith("300") and d["versions"][-1]["change_type"] == "ECONOMIC_AMEND", "not applied")
    return ["approved, new economic version"]


def _settled(ctx, ptype="EQUITY", n=0):
    return ctx.client.get("/api/trades", headers=ctx.auth("mo1"), params={"status": "SETTLED", "product_type": ptype, "page_size": n + 1,
                                                                         "sort": "trade_id"}).json()["items"][n]


@api_check("AMEND-AFTER-SETTLE")
def _(ctx):
    t = _settled(ctx, n=3)
    r = ctx.client.patch(f"/api/trades/{t['trade_id']}", headers={**ctx.auth("mo1"), "If-Match": str(t["version"])},
                         json={"changes": {"price": "1"}, "reason": "try"})
    expect(r.status_code == 409 and r.json()["error"]["code"] == "E_AMEND_AFTER_SETTLEMENT", f"{r.status_code} {r.text[:100]}")
    return ["409 E_AMEND_AFTER_SETTLEMENT"]


@api_check("CANCEL-REVERSAL")
def _(ctx):
    t = _settled(ctx, n=0)
    r = ctx.client.post(f"/api/trades/{t['trade_id']}/cancel", headers=ctx.auth("mo1"), json={"reason": "wrong account; cancel (R090)"})
    out = ctx.client.post(f"/api/approvals/{r.json()['approval_id']}/decision", headers=ctx.auth("checker1"), json={"approve": True}).json()
    expect(out["result"]["reversed_postings"] > 0, "no reversals")
    tot: dict = {}
    for e in ctx.client.get(f"/api/trades/{t['trade_id']}", headers=ctx.auth("mo1")).json()["ledger"]:
        if ":" not in e["ledger_account"]:
            tot[(e["ledger_account"], e["key"])] = tot.get((e["ledger_account"], e["key"]), Decimal(0)) + D(e["amount"])
    expect(all(v == 0 for v in tot.values()), f"residual {tot}")
    return [f"{out['result']['reversed_postings']} postings reversed; nets to zero"]


@api_check("CANCEL-REBOOK")
def _(ctx):
    t = _book(ctx)
    r = ctx.client.post(f"/api/trades/{t['trade_id']}/cancel", headers=ctx.auth("trader1"), json={"reason": "booked in error",
                                                                                                    "rebook": {"quantity": "100"}})
    d = ctx.client.post(f"/api/approvals/{r.json()['approval_id']}/decision", headers=ctx.auth("checker2"), json={"approve": True}).json()
    new = d["result"]["rebooked_trade_id"]
    expect(ctx.client.get(f"/api/trades/{new}", headers=ctx.auth("mo1")).json()["trade"]["rebook_of"] == t["trade_id"], "not linked")
    return [f"{t['trade_id']} cancelled, rebooked as {new}"]


@api_check("INVALID-TRANSITION")
def _(ctx):
    tid = ctx.client.get("/api/queues/matching", headers=ctx.auth("mo1")).json()["items"][0]["trade_id"]
    r = ctx.client.post(f"/api/trades/{tid}/transition", headers=ctx.auth("mo1"), json={"target": "SETTLED"})
    expect(r.status_code == 409 and r.json()["error"]["details"]["rule_id"] == "R099", f"{r.status_code}")
    return ["409 R099"]


def _iface(ctx, msgs):
    h = ctx.auth("mo1")
    base = uuid.uuid4().hex[:8]
    first = ctx.client.post("/api/interfaces/CCP/messages", headers=h, json={"message_id": f"TK{base}-0", "message_type": "CLEARING_ACK",
                                                                             "sequence_no": 1}).json()
    exp = first["next_expected"]
    return [ctx.client.post("/api/interfaces/CCP/messages", headers=h, json={"message_id": f"TK{base}-{i}", "message_type": "CLEARING_ACK",
                                                                             "sequence_no": exp + n}).json() for i, n in msgs]


@api_check("IFACE-DUP")
def _(ctx):
    res = _iface(ctx, [(1, 0), (1, 0)])
    expect([r["status"] for r in res] == ["PROCESSED", "IGNORED_DUPLICATE"], str([r["status"] for r in res]))
    return ["second copy ignored"]


@api_check("IFACE-GAP")
def _(ctx):
    res = _iface(ctx, [(3, 2), (1, 0), (2, 1)])
    expect([r["status"] for r in res] == ["PARKED", "PROCESSED", "PROCESSED"], str([r["status"] for r in res]))
    expect(len(res[2]["processed"]) == 2, f"released {res[2]['processed']}")
    return ["parked, then released in order"]


@api_check("REPLAY-IDEMPOTENT")
def _(ctx):
    r = ctx.client.post("/api/admin/replay", headers=ctx.auth("admin1"), json={"business_date": "2026-11-06"}).json()
    expect(r["new_postings"] == 0 and r["idempotent"] is True, f"replay posted {r.get('new_postings')}")
    return ["0 new postings"]


@api_check("PROCESS-IDEMPOTENT")
def _(ctx):
    h = ctx.auth("admin1")
    with ctx.Session() as s:
        before = s.scalar(text("select count(*) from ledger_entries where sandbox_id is null"))
    ctx.client.post("/api/admin/process", headers=h, json={"business_date": "2026-11-06"})
    with ctx.Session() as s:
        after = s.scalar(text("select count(*) from ledger_entries where sandbox_id is null"))
    expect(after == before, f"{after - before} new ledger rows")
    return [f"{before} ledger rows before and after"]


@api_check("LEDGER-IDEMPOTENT")
def _(ctx):
    from cf360.services import core
    legs = [("AC0001", "USD", Decimal("-100.00")), ("NOSTRO:USD", "USD", Decimal("100.00"))]
    with ctx.Session() as s:
        first = core.post(s, "TK-EVT-1", "CASH", legs, date(2026, 11, 6), "DVP", sandbox_id="TK-LEDGER")
        second = core.post(s, "TK-EVT-1", "CASH", legs, date(2026, 11, 6), "DVP", sandbox_id="TK-LEDGER")
        n = s.scalar(text("select count(*) from ledger_entries where sandbox_id = 'TK-LEDGER'"))
        s.rollback()
    expect(first is True and second is False and n == 2, f"first={first} second={second} rows={n}")
    return ["second posting with the same event id was a no-op"]


@api_check("PAGE-LIMIT")
def _(ctx):
    r = ctx.client.get("/api/trades", headers=ctx.auth("mo1"), params={"page_size": 501})
    expect(r.status_code == 422, f"{r.status_code}")
    return ["422"]


@api_check("PAGE-BEYOND")
def _(ctx):
    r = ctx.client.get("/api/trades", headers=ctx.auth("mo1"), params={"page_size": 100, "page": 99}).json()
    expect(r["items"] == [] and r["total"] >= 500, f"{len(r['items'])} items total {r['total']}")
    return [f"0 items, total {r['total']}"]


@api_check("BAD-SORT")
def _(ctx):
    r = ctx.client.get("/api/trades", headers=ctx.auth("mo1"), params={"sort": "nope"})
    expect(r.status_code == 400 and r.json()["error"]["code"] == "E_BAD_SORT", f"{r.status_code}")
    return ["400 E_BAD_SORT"]


@api_check("ERROR-ENVELOPE")
def _(ctx):
    r = ctx.client.get("/api/trades/NOPE", headers=ctx.auth("mo1")).json()
    expect(set(r["error"]) >= {"code", "message", "correlation_id"}, str(r)[:200])
    return [r["error"]["code"]]


@api_check("CORRELATION")
def _(ctx):
    a = ctx.client.get("/api/trades/TRD00001", headers={**ctx.auth("mo1"), "X-Correlation-ID": "tk-ok-1"})
    b = ctx.client.get("/api/trades/NOPE", headers={**ctx.auth("mo1"), "X-Correlation-ID": "tk-err-1"})
    expect(a.headers["X-Correlation-ID"] == "tk-ok-1" and b.json()["error"]["correlation_id"] == "tk-err-1", "not echoed")
    return ["echoed on 200 and 404"]


@api_check("SCHEMA-422")
def _(ctx):
    r = ctx.client.post("/api/trades", headers=ctx.auth("trader1"), json={**_trade_body(ctx), "rogue": 1})
    expect(r.status_code == 422 and r.json()["error"]["code"] == "E_SCHEMA", f"{r.status_code}")
    return ["422 E_SCHEMA"]


@api_check("LOGIN-FAIL")
def _(ctx):
    r = ctx.client.post("/api/auth/login", json={"username": "trader2", "password": "wrong"})
    expect(r.status_code == 401, f"{r.status_code}")
    n = ctx.client.get("/api/audit", headers=ctx.auth("risk1"), params={"action": "LOGIN_FAILED", "entity_id": "trader2"}).json()["total"]
    expect(n >= 1, "not audited")
    return ["401 and audited"]


@api_check("TOKEN-BAD")
def _(ctx):
    tok = ctx.auth("mo1")["Authorization"][:-3] + "abc"
    r = ctx.client.get("/api/trades", headers={"Authorization": tok})
    expect(r.status_code == 401, f"{r.status_code}")
    return ["401"]


@api_check("EXPORT-CSV")
def _(ctx):
    h = ctx.auth("trader1")
    total = ctx.client.get("/api/trades", headers=h, params={"product_group": "FX", "page_size": 1}).json()["total"]
    lines = ctx.client.get("/api/trades/export", headers=h, params={"product_group": "FX", "columns": "trade_id,status"}).text.strip().splitlines()
    expect(lines[0] == "trade_id,status" and len(lines) - 1 == total, f"{len(lines) - 1} rows vs {total}")
    return [f"{total} rows"]


@api_check("EXPORT-XLSX")
def _(ctx):
    import io

    from openpyxl import load_workbook
    x = ctx.client.get("/api/trades/export", headers=ctx.auth("trader1"), params={"format": "xlsx", "columns": "trade_id,settle_ccy"})
    ws = load_workbook(io.BytesIO(x.content), read_only=True).worksheets[0]
    head = [c.value for c in next(ws.iter_rows(max_row=1))]
    expect(head == ["trade_id", "settle_ccy"], str(head))
    return ["opens; columns trade_id, settle_ccy"]


@api_check("DRYRUN-NO-TRACE")
def _(ctx):
    with ctx.Session() as s:
        q = "select (select count(*) from trades) + (select count(*) from booking_rejections) + (select count(*) from audit_events)"
        before = s.scalar(text(q))
    body = _trade_body(ctx)
    r = ctx.client.post("/api/trades/validate", headers=ctx.auth("mo1"), json=body).json()
    expect(r["valid"] is True, str(r)[:200])
    ctx.client.post("/api/trades/validate", headers=ctx.auth("mo1"), json={**body, "trade_date": "2026-11-07"})
    with ctx.Session() as s:
        after = s.scalar(text(q))
    expect(after == before, f"{after - before} rows written")
    return ["no rows written by two dry runs"]


@api_check("REJECT-LOGGED")
def _(ctx):
    body = _trade_body(ctx, trade_date="2026-11-07")
    r = ctx.client.post("/api/trades", headers=ctx.auth("trader1"), json=body)
    expect(r.status_code == 422 and r.json()["error"]["details"]["issues"][0]["rule_id"] == "R010", f"{r.status_code}")
    rej = ctx.client.get("/api/admin/rejections", headers=ctx.auth("risk1"), params={"q": body["source_trade_ref"]}).json()
    expect(any(body["source_trade_ref"] == i.get("source_trade_ref") for i in rej.get("items", [])), "rejection not stored")
    return ["422 R010 and stored"]


@api_check("AUDIT-APPEND-ONLY")
def _(ctx):
    for sql in ("update audit_events set action = 'X' where id = (select min(id) from audit_events)",
                "delete from audit_events where id = (select min(id) from audit_events)"):
        with ctx.Session() as s:
            try:
                s.execute(text(sql))
                s.commit()
                raise Fail(f"allowed: {sql}")
            except Fail:
                raise
            except Exception:
                s.rollback()
    return ["UPDATE and DELETE blocked by trigger"]


@api_check("RULES-CATALOGUE")
def _(ctx):
    r = ctx.client.get("/api/meta/rules", headers=ctx.auth("risk1")).json()
    items = r.get("items", r)
    expect(len(items) >= 40, f"{len(items)} rules")
    return [f"{len(items)} rules"]


@api_check("ORDER-VWAP")
def _(ctx):
    src = ctx.client.get("/api/trades/TRD00010", headers=ctx.auth("mo1")).json()["trade"]
    o = {"client_id": src["client_id"], "account_id": src["account_id"], "instrument_id": src["instrument_id"], "product_type": "EQUITY",
         "side": "BUY", "order_qty": "1000"}
    r = ctx.client.post("/api/orders", headers=ctx.auth("trader1"), json={**o, "simulate_fills": [{"qty": "600", "price": "71.00"},
                                                                                                  {"qty": "300", "price": "71.10"}]})
    oid = r.json()["order"]["order_id"]
    b = ctx.client.post(f"/api/orders/{oid}/book", headers=ctx.auth("trader1"), json={"counterparty_id": src["counterparty_id"]})
    expect(b.status_code == 201 and b.json()["vwap"] == "71.033333", b.text[:200])
    return ["VWAP 71.033333 on 900"]


@api_check("ORDER-LIMIT")
def _(ctx):
    src = ctx.client.get("/api/trades/TRD00010", headers=ctx.auth("mo1")).json()["trade"]
    o = {"client_id": src["client_id"], "account_id": src["account_id"], "instrument_id": src["instrument_id"], "product_type": "EQUITY",
         "side": "BUY", "order_qty": "1000", "order_type": "LIMIT", "limit_price": "95"}
    rej = ctx.client.post("/api/orders", headers=ctx.auth("trader1"), json=o).json()
    expect(rej["order"]["status"] == "REJECTED" and rej["pretrade"][0]["rule_id"] == "R008", str(rej)[:200])
    return ["REJECTED R008"]


@api_check("SANDBOX-E2E")
def _(ctx):
    sb = ctx.client.post("/api/test-support/sandboxes", headers=ctx.auth("admin1"), json={}).json()["sandbox_id"]
    t = ctx.client.post(f"/api/test-support/sandboxes/{sb}/trades", headers=ctx.auth("trader1"), json=_trade_body(ctx, trade_date="2026-10-01")).json()["trade"]
    ctx.client.post(f"/api/test-support/sandboxes/{sb}/confirmations", headers=ctx.auth("mo1"), json={"trade_id": t["trade_id"]})
    ctx.client.post(f"/api/test-support/sandboxes/{sb}/process", headers=ctx.auth("admin1"), params={"through": "2026-10-10", "start": "2026-10-01"})
    st = ctx.client.get(f"/api/test-support/sandboxes/{sb}", headers=ctx.auth("admin1")).json()
    expect(st["trades"][0]["status"] == "SETTLED", st["trades"][0]["status"])
    ctx.client.delete(f"/api/test-support/sandboxes/{sb}", headers=ctx.auth("admin1"))
    expect(ctx.client.get("/api/trades", headers=ctx.auth("mo1"), params={"q": t["trade_id"]}).json()["total"] == 0, "leaked to main book")
    return [f"{t['trade_id']} settled in sandbox {sb}"]


@api_check("TRADE-DETAIL")
def _(ctx):
    d = ctx.client.get("/api/trades/TRD00001", headers=ctx.auth("mo1")).json()
    need = {"trade", "versions", "allocations", "ledger", "audit", "allowed_transitions", "lifecycle_path"}
    expect(need <= set(d), f"missing {need - set(d)}")
    return ["all sections present"]


@api_check("POSITIONS-ORACLE")
def _(ctx):
    from testkit.compare_oracle import compare
    dsn = TEST_DB.replace("postgresql+psycopg://", "postgresql://")
    res = compare(dsn)
    expect(res["differences"] == 0, "; ".join(res["details"][:3]))
    return [f"{res['checks']} oracle checks, 0 differences"]


def _filter_ccy(ccy):
    def fn(ctx):
        h = ctx.auth("trader1")
        r = ctx.client.get("/api/trades", headers=h, params={"currency": ccy, "page_size": 500, "columns": "trade_id,settle_ccy"}).json()
        with ctx.Session() as s:
            want = s.scalar(text("select count(*) from trades where settle_ccy = :c and sandbox_id is null"), {"c": ccy})
        bad = [i["trade_id"] for i in r["items"] if i["settle_ccy"] != ccy]
        expect(not bad and r["total"] == want, f"{len(bad)} rows in other currencies; total {r['total']} vs {want}")
        return [f"{want} {ccy} trades"]
    return fn


for _c in specs.CCYS:
    API[f"FILTER-CCY-{_c}"] = _filter_ccy(_c)


def _dash(name):
    def fn(ctx):
        d = ctx.client.get(f"/api/dashboards/{name}", headers=ctx.auth("risk1"))
        expect(d.status_code == 200, f"{d.status_code}")
        k = d.json().get("kpis", [])
        expect(k and all(x.get("definition") for x in k), f"{len(k)} KPIs, some without definitions")
        return [f"{len(k)} KPIs"]
    return fn


for _d in specs.DASHBOARDS:
    API[f"DASH-{_d.upper()}"] = _dash(_d)


def ex_api_check(c, ctx, s):
    return API[c["params"]["check"]](ctx)


EXECUTORS = {"book_oracle": ex_book_oracle, "neg_trade": ex_neg_trade, "neg_alloc": ex_neg_alloc, "schema_drift": ex_schema_drift,
             "fx_date": ex_fx_date, "accrued": ex_accrued, "split": ex_split, "worked_example": ex_worked, "rule_mutation": ex_rule_mutation,
             "lifecycle": ex_lifecycle, "permission": ex_permission, "label_outcome": ex_label_outcome, "api_check": ex_api_check,
             "lakehouse_evidence": ex_lakehouse}
ORDER = ["book_oracle", "neg_trade", "neg_alloc", "schema_drift", "fx_date", "accrued", "split", "worked_example", "rule_mutation",
         "lifecycle", "label_outcome", "permission", "lakehouse_evidence", "api_check"]  # read-only first, mutating API checks last


# ------------------------------------------------------------------------------------------------ run
def seed():
    env = {**os.environ, "PYTHONPATH": str(BACKEND)}
    from cf360.db import engine as db_engine
    with db_engine.begin() as con:
        con.execute(text("drop schema public cascade; create schema public"))
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)
    with __import__("cf360.db", fromlist=["SessionLocal"]).SessionLocal() as s:
        loader.seed(s)
    refcache.invalidate()


def run(run_id: str, defects: list[str], only: str | None = None) -> dict:
    t0 = time.time()
    seed()
    settings.defects = frozenset(defects)
    cases = json.loads((OUT / "cases.json").read_text())
    todo = [c for c in cases if c["layer"] != "ui" and (not only or c["suite"] == only)]
    first = {"TC-API-POSITIONS-ORACLE"}  # reads the untouched seeded book, so it runs before any mutating API check
    todo.sort(key=lambda c: (ORDER.index(c["kind"]) if c["case_id"] not in first else ORDER.index("label_outcome")))
    ctx = Ctx()
    results = []
    with ctx.Session() as s:
        for c in todo:
            start = time.perf_counter()
            status, err, ev = "passed", None, []
            try:
                ev = EXECUTORS[c["kind"]](c, ctx, s) or []
            except Fail as e:
                status, err = "failed", str(e)
            except Exception as e:  # an executor crash is a failed case, with the traceback as evidence
                status, err = "failed", f"{type(e).__name__}: {e}"
                ev = [traceback.format_exc(limit=3)[-800:]]
                s.rollback()
            results.append({"case_id": c["case_id"], "scenario_id": c["scenario_id"], "title": c["title"], "rule_ids": c["rule_ids"],
                            "project": c["layer"], "status": status, "duration_ms": int((time.perf_counter() - start) * 1000),
                            "retries": 0, "error": err, "evidence": ev, "suite": c["suite"]})
    settings.defects = frozenset()
    passed = sum(r["status"] == "passed" for r in results)
    summary = {"run_id": run_id, "defects_enabled": defects, "executed": len(results), "passed": passed, "failed": len(results) - passed,
               "scenarios": len({r["scenario_id"] for r in results}), "elapsed_seconds": round(time.time() - t0, 1),
               "by_suite": {}, "started_at": datetime.fromtimestamp(t0, UTC).isoformat()}
    for r in results:
        b = summary["by_suite"].setdefault(r["suite"], {"passed": 0, "failed": 0})
        b[r["status"]] += 1
    out = {"summary": summary, "results": results}
    (OUT / "runs").mkdir(parents=True, exist_ok=True)
    (OUT / "runs" / f"{run_id}.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def import_run(base: str, out: dict, label: str, environment: str) -> None:
    import httpx
    s = out["summary"]
    with httpx.Client(base_url=base, timeout=120) as c:
        tok = c.post("/api/auth/login", json={"username": "admin1", "password": PASSWORD}).json()["access_token"]
        body = {"run_id": s["run_id"], "label": label, "environment": environment, "started_at": s["started_at"],
                "defects_enabled": ",".join(s["defects_enabled"]), "summary": s,
                "results": [{k: v for k, v in r.items() if k != "suite"} | {"defect_id": (s["defects_enabled"][0] if len(s["defects_enabled"]) == 1
                                                                                      and r["status"] == "failed" else None)}
                            for r in out["results"]]}
        r = c.post("/api/test-runs/import", json=body, headers={"Authorization": f"Bearer {tok}"})
        print("import", s["run_id"], r.status_code, r.text[:120])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--defects", default="")
    ap.add_argument("--suite")
    ap.add_argument("--label")
    ap.add_argument("--import-to")
    a = ap.parse_args()
    defects = [d for d in a.defects.split(",") if d]
    out = run(a.run_id, defects, a.suite)
    s = out["summary"]
    print(json.dumps({k: v for k, v in s.items() if k != "by_suite"}, default=str))
    for k, v in s["by_suite"].items():
        print(f"  {k:12s} passed {v['passed']:5d} failed {v['failed']:4d}")
    for r in [r for r in out["results"] if r["status"] == "failed"][:25]:
        print("  FAIL", r["case_id"], "|", (r["error"] or "")[:160])
    if a.import_to:
        import_run(a.import_to, out, a.label or a.run_id, "local (FastAPI TestClient + PostgreSQL 16)")


if __name__ == "__main__":
    main()
