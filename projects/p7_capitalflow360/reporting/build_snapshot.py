"""Build the single metrics snapshot every deck and the handbook read from (reporting/out/snapshot.json).

    python reporting/build_snapshot.py

Sources, all on disk or in the platform database (CF360_DATABASE_URL, default the main cf360 database):
dashboard metrics through the API code path, test runs in testkit/out/runs, the case catalogue, lakehouse
evidence, the rule catalogue, worked examples and data manifest. No number in a deck is typed by hand.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reporting" / "out"
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
os.environ.setdefault("CF360_JWT_SECRET", "reporting-only-secret-at-least-32-bytes!!")


def dashboards() -> dict:
    from fastapi.testclient import TestClient

    from cf360.main import app
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"username": "risk1", "password": os.getenv("CF360_DEMO_PASSWORD", "Demo!2026")}).json()
    r = c.get("/api/reports/snapshot", headers={"Authorization": f"Bearer {tok['access_token']}"})
    r.raise_for_status()
    return r.json()


def runs() -> dict:
    out = {}
    for p in sorted((ROOT / "testkit" / "out" / "runs").glob("*.json")):
        d = json.loads(p.read_text())
        s = d["summary"]
        failed = [r for r in d["results"] if r["status"] == "failed"]
        s["failed_by_prefix"] = dict(Counter(r["case_id"].split("-")[1] for r in failed if r["case_id"]))
        s["failed_examples"] = [{"case_id": r["case_id"], "error": (r["error"] or "")[:160]} for r in failed[:4]]
        out[s["run_id"]] = s
    return out


def platform_facts() -> dict:
    from sqlalchemy import text

    from cf360.db import SessionLocal
    q = {"ledger_rows": "select count(*) from ledger_entries where sandbox_id is null",
         "audit_events": "select count(*) from audit_events",
         "lifecycle_events": "select count(*) from lifecycle_events where sandbox_id is null",
         "instructions": "select count(*) from settlement_instructions where sandbox_id is null",
         "confirmations": "select count(*) from confirmations where sandbox_id is null"}
    with SessionLocal() as s:
        out = {k: s.scalar(text(v)) for k, v in q.items()}
        out["instruction_status"] = dict(s.execute(text("select status, count(*) from settlement_instructions where sandbox_id is null group by 1")).all())
        out["trade_status"] = dict(s.execute(text("select status, count(*) from trades where sandbox_id is null group by 1")).all())
        out["breaks"] = [dict(r._mapping) for r in s.execute(text(
            "select recon_type, account_id, key, break_type, difference::float as difference from recon_breaks order by 1, 2, 3"))]
    return out


def main() -> None:
    from cf360.rules.catalogue import RULES
    from testkit.worked_examples import run_all
    cases = json.loads((ROOT / "testkit" / "out" / "cases.json").read_text())
    trace = Counter(r for c in cases for r in c["rule_ids"])
    lake = json.loads((ROOT / "lakehouse" / "evidence" / "local_run.json").read_text())
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text())
    snap = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dashboards": dashboards()["dashboards"],
        "runs": runs(),
        "platform": platform_facts(),
        "catalogue": {"cases": len(cases), "scenarios": len({c["scenario_id"] for c in cases}),
                      "by_suite": dict(Counter(c["suite"] for c in cases)), "by_layer": dict(Counter(c["layer"] for c in cases)),
                      "rules_total": len(RULES), "rules_covered": sum(1 for r in RULES if trace[r["id"]]),
                      "rules_uncovered": [r["id"] for r in RULES if not trace[r["id"]]],
                      "rule_families": dict(Counter(r["family"] for r in RULES))},
        "rules": [{k: r[k] for k in ("id", "family", "name", "error_code", "boundaries")} | {"cases": trace[r["id"]]} for r in RULES],
        "worked_examples": [{k: e[k] for k in ("id", "title", "rules", "inputs", "working", "expected", "pass")} for e in run_all()],
        "lakehouse": {"environment": lake["environment"], "spark": lake["spark"], "delta": lake["delta_spark"],
                      "elapsed_seconds": lake["elapsed_seconds"], "passed": lake["passed"], "failed": lake["failed"],
                      "checks": [{"check": c["check"], "result": c["result"]} for c in lake["checks"]],
                      "silver_counts": {t: v["n"] for t, v in lake["silver_hash"].items()}, "gold": lake["gold"],
                      "quarantine": next(c["detail"] for c in lake["checks"] if c["check"].startswith("quarantine catches every case")),
                      "negative_gate": next(c["detail"] for c in lake["checks"] if c["check"].startswith("negative batch fails"))[0]},
        "data": {"trades": manifest["baseline_trade_count"], "by_group": manifest["trade_counts_by_group"], "files": manifest["files"],
                 "negative_files": manifest["negative_files"], "seed": manifest["generator_seed"],
                 "opening_date": manifest["opening_date"], "statement_date": manifest["statement_date"]},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "snapshot.json").write_text(json.dumps(snap, indent=1, default=str))
    print("snapshot:", OUT / "snapshot.json", "| runs:", list(snap["runs"]), "| cases:", snap["catalogue"]["cases"])


if __name__ == "__main__":
    main()
