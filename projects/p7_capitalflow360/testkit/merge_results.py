"""Merge a non-browser run (testkit/execute.py) with Playwright JSON reports into one test run.

    python -m testkit.merge_results --run testkit/out/runs/CLEAN-001.json --playwright testkit/out/playwright.json \
        [--playwright more.json ...] [--import-to http://localhost:8000]

Every Playwright test title starts with its catalogue case id; a browser run is one (case, browser project) pair.
Catalogue UI cases with no browser result are listed as not executed, never counted as passed.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"


def playwright_results(path: Path, cases: dict) -> list[dict]:
    rep = json.loads(Path(path).read_text())
    out = []

    def walk(suite):
        for spec in suite.get("specs", []):
            m = re.match(r"(TC-UI-[A-Z0-9_-]+)\s", spec["title"])
            cid = m.group(1) if m else None
            c = cases.get(cid, {})
            for t in spec["tests"]:
                res = t["results"][-1] if t["results"] else {"status": "skipped", "duration": 0}
                status = {"passed": "passed", "failed": "failed", "timedOut": "failed", "interrupted": "failed"}.get(res["status"], "skipped")
                if status == "passed" and len(t["results"]) > 1:
                    status = "flaky"
                err = (res.get("error") or {}).get("message")
                out.append({"case_id": cid, "scenario_id": c.get("scenario_id"), "title": spec["title"], "rule_ids": c.get("rule_ids", []),
                            "project": t["projectName"], "status": status, "duration_ms": int(res.get("duration", 0)),
                            "retries": max(0, len(t["results"]) - 1), "error": re.sub(r"\x1b\[[0-9;]*m", "", err)[:2000] if err else None,
                            "evidence": [a.get("path", "") for a in res.get("attachments", []) if a.get("path")], "suite": "ui"})
        for s in suite.get("suites", []):
            walk(s)
    for s in rep.get("suites", []):
        walk(s)
    return out


def merge(run_path: Path, pw_paths: list[Path], run_id: str | None = None) -> dict:
    run = json.loads(Path(run_path).read_text())
    cases = {c["case_id"]: c for c in json.loads((OUT / "cases.json").read_text())}
    ui = [r for p in pw_paths for r in playwright_results(p, cases)]
    results = run["results"] + ui
    s = dict(run["summary"])
    if run_id:
        s["run_id"] = run_id
    ui_cases = {c for c, v in cases.items() if v["layer"] == "ui"}
    ran_ui = {r["case_id"] for r in ui}
    s.update({
        "executed": len(results),
        "passed": sum(r["status"] == "passed" for r in results),
        "failed": sum(r["status"] == "failed" for r in results),
        "flaky": sum(r["status"] == "flaky" for r in results),
        "unique_cases_executed": len({r["case_id"] for r in results}),
        "catalogue_cases": len(cases),
        "scenarios": len({r["scenario_id"] for r in results if r["scenario_id"]}),
        "browser_runs": len(ui),
        "browser_runs_by_project": dict(Counter(r["project"] for r in ui)),
        "browser_failed": sum(r["status"] == "failed" for r in ui),
        "ui_cases_not_executed": sorted(ui_cases - ran_ui),
        "unmapped_browser_tests": sorted({r["title"] for r in ui if r["case_id"] not in cases}),
    })
    s.setdefault("by_suite", {})["ui"] = {"passed": sum(r["status"] == "passed" for r in ui), "failed": sum(r["status"] == "failed" for r in ui)}
    out = {"summary": s, "results": results}
    (OUT / "runs" / f"{s['run_id']}.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--playwright", action="append", type=Path, default=[])
    ap.add_argument("--run-id")
    ap.add_argument("--label")
    ap.add_argument("--import-to")
    a = ap.parse_args()
    out = merge(a.run, a.playwright, a.run_id)
    print(json.dumps({k: v for k, v in out["summary"].items() if k != "by_suite"}, indent=1, default=str))
    if a.import_to:
        from testkit.execute import import_run
        import_run(a.import_to, out, a.label or out["summary"]["run_id"],
                   "local: FastAPI TestClient + PostgreSQL 16; Playwright 1.63.0 " + ", ".join(sorted(out["summary"]["browser_runs_by_project"])))


if __name__ == "__main__":
    main()
