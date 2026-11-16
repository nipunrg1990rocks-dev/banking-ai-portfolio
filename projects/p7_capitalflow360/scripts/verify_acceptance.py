"""Check the brief's acceptance criteria against files on disk and write docs/acceptance_status.md.

    python scripts/verify_acceptance.py

Nothing is typed in by hand: every "Actual" is read from the data manifest, case catalogue, recorded runs,
lakehouse evidence, generated decks and handbook. Exit code 1 if any criterion that can be met locally is not met.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
OUT = ROOT / "reporting" / "out"
RESULTS: list[tuple[str, str, str, str, str]] = []  # id, criterion, target, actual, status


def check(cid, criterion, target, actual, ok, pending=False):
    RESULTS.append((cid, criterion, target, str(actual), "MET" if ok else ("PENDING" if pending else "NOT MET")))


def slides(p: Path) -> int:
    with zipfile.ZipFile(p) as z:
        return sum(1 for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))


def pdf_pages(p: Path) -> int:
    out = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True, check=True).stdout
    return int(re.search(r"Pages:\s+(\d+)", out).group(1))


def main() -> int:
    m = json.loads((ROOT / "data" / "manifest.json").read_text())
    split = list(m["trade_counts_by_group"].values())
    check("A1", "Baseline trades and split", "500 split 140/120/80/70/50/40", f"{m['baseline_trade_count']} split {'/'.join(map(str, split))}",
          m["baseline_trade_count"] == 500 and sorted(split, reverse=True) == [140, 120, 80, 70, 50, 40])
    check("A2", "Related Excel workbooks, negative files kept apart", ">= 12 workbooks; negative files separate",
          f"{len(m['files'])} workbooks; {len(m['negative_files'])} negative files in data/negative",
          len(m["files"]) >= 12 and all(v["path"].startswith("data/negative/") for v in m["negative_files"].values()))
    oracle = sorted((ROOT / "data" / "oracle").glob("*")) if (ROOT / "data" / "oracle").exists() else []
    check("A3", "Oracle files separate from the platform's inputs", ">= 1 file in data/oracle", f"{len(oracle)} files", len(oracle) >= 1)

    from cf360.rules.catalogue import RULES
    from testkit.worked_examples import run_all
    we = run_all()
    check("A4", "Business rules documented", ">= 40 with id, boundaries, error code", f"{len(RULES)} (docs/rule_catalogue.md)", len(RULES) >= 40)
    check("A5", "Hand-calculated worked examples", ">= 20, all matching platform and oracle", f"{len(we)}, {sum(w['pass'] for w in we)} pass",
          len(we) >= 20 and all(w["pass"] for w in we))

    cases = json.loads((ROOT / "testkit" / "out" / "cases.json").read_text())
    scen = {c["scenario_id"] for c in cases}
    check("A6", "Generated test cases", ">= 1,500", f"{len(cases):,}", len(cases) >= 1500)
    check("A7", "Distinct scenarios", ">= 100", len(scen), len(scen) >= 100)

    runs = {p.stem: json.loads(p.read_text())["summary"] for p in sorted((ROOT / "testkit" / "out" / "runs").glob("*.json"))}
    # CI runs: summaries copied from the GitHub Actions log of the CapitalFlow 360 workflow, with the run's URL (testkit/out/ci)
    runs |= {p.stem: json.loads(p.read_text()) for p in sorted((ROOT / "testkit" / "out" / "ci").glob("*.json"))}
    clean = {k: v for k, v in runs.items() if not v.get("defects_enabled")}
    best = max(clean.values(), key=lambda s: (s.get("browser_runs", 0), s["executed"])) if clean else None
    check("A8", "Executed cases, clean run", ">= 1,500 executed, 0 failed",
          f"{best['run_id']}: {best['executed']:,} executed, {best['failed']} failed" if best else "no clean run",
          bool(best) and best["executed"] >= 1500 and best["failed"] == 0)
    engines = {p.split("-")[0] for p in (best or {}).get("browser_runs_by_project", {})}
    check("A9", "Browser runs on Chromium, Firefox and WebKit", ">= 200 runs; 3 engines",
          f"{best.get('browser_runs', 0)} runs; engines: {', '.join(sorted(engines)) or 'none'}" if best else "none",
          bool(best) and best.get("browser_runs", 0) >= 200 and engines >= {"chromium", "firefox", "webkit"}, pending=True)
    drun = {k: v for k, v in runs.items() if v.get("defects_enabled")}
    caught = [k for k, v in drun.items() if v["failed"] > 0]
    check("A10", "Defect injection with clean rerun", ">= 5 defects each caught; clean rerun 0 failures",
          f"{len(caught)} of {len(drun)} defect runs caught ({', '.join(sorted(caught))}); clean rerun failures {best['failed'] if best else '-'}",
          len(caught) >= 5 and len(caught) == len(drun) and bool(best) and best["failed"] == 0)

    snap = json.loads((OUT / "snapshot.json").read_text())
    check("A11", "Dashboards", "7 with definitions", f"{len(snap['dashboards'])}: {', '.join(snap['dashboards'])}", len(snap["dashboards"]) == 7)
    decks = {"CF360_Executive_Summary.pptx": (8, 10), "CF360_Business_Process_Walkthrough.pptx": (15, 20),
             "CF360_Test_Strategy_and_Results.pptx": (12, 15), "CF360_Architecture_and_Data.pptx": (10, 12)}
    counts = {f: slides(OUT / f) if (OUT / f).exists() else 0 for f in decks}
    check("A12", "Four PowerPoint decks within slide ranges", "8-10, 15-20, 12-15, 10-12 slides",
          ", ".join(f"{f.split('_', 1)[1][:-5]} {n}" for f, n in counts.items()), all(lo <= counts[f] <= hi for f, (lo, hi) in decks.items()))
    pdf = OUT / "CapitalFlow360_Interview_and_Demonstration_Handbook.pdf"
    pages = pdf_pages(pdf) if pdf.exists() else 0
    qa = len(re.findall(r'class="qa"', (OUT / "handbook.html").read_text())) if (OUT / "handbook.html").exists() else 0
    check("A13", "Interview and demonstration handbook", "30-40 pages, >= 60 Q&A", f"{pages} pages, {qa} Q&A", 30 <= pages <= 40 and qa >= 60)

    lake = json.loads((ROOT / "lakehouse" / "evidence" / "local_run.json").read_text())
    check("A14", "Lakehouse Bronze/Silver/Gold on local Spark", "all checks pass", f"{lake['passed']} passed, {lake['failed']} failed ({lake['environment']})",
          lake["failed"] == 0 and lake["passed"] > 0)
    dbx = ROOT / "lakehouse" / "evidence" / "databricks_run.json"
    check("A15", "Databricks run", "recorded only if actually run", "recorded" if dbx.exists() else "not run yet (needs the owner's workspace)",
          dbx.exists(), pending=True)
    files = ["docker-compose.yml", "Dockerfile", ".env.example", "README.md", "backend/alembic.ini", "docs/rule_catalogue.md", "docs/lifecycle_matrix.md",
             "docs/data_dictionary.md"]
    missing = [f for f in files if not (ROOT / f).exists()] + ([] if (REPO / ".github" / "workflows" / "p7-capitalflow360.yml").exists() else ["CI workflow"])
    check("A16", "Delivery files", "Compose, Dockerfile, .env.example, README, migrations, docs, CI", "all present" if not missing else "missing: " + ", ".join(missing),
          not missing)
    dep = ROOT / "docs" / "deployment.json"
    check("A17", "Public deployment", "recorded only once deployed and checked", (lambda d: f"{d['url']} checked {d['verified']['at']}")(json.loads(dep.read_text())) if dep.exists() else "not deployed yet (Vercel + Neon free tiers, no card; owner sets them up)",
          dep.exists(), pending=True)

    lines = ["# CapitalFlow 360: acceptance status", "",
             f"Generated by `scripts/verify_acceptance.py` on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC from files on disk; do not edit by hand.",
             "PENDING means the criterion needs a step outside the build environment (CI browsers, a Databricks workspace, a host).", "",
             "| # | Criterion | Target | Actual | Status |", "|---|---|---|---|---|"]
    lines += [f"| {a} | {b} | {c} | {d} | **{e}** |" for a, b, c, d, e in RESULTS]
    (ROOT / "docs" / "acceptance_status.md").write_text("\n".join(lines) + "\n")
    for r in RESULTS:
        print(f"{r[0]:4s} {r[4]:8s} {r[1]}: {r[3]}")
    return 1 if any(r[4] == "NOT MET" for r in RESULTS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
