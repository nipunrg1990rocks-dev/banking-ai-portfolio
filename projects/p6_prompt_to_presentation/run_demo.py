"""Scripted acceptance demo (A-G) - the same steps an interviewer would click through in the app.

    python projects/p6_prompt_to_presentation/run_demo.py [--out DIR]

Prints a Markdown report (used as the CI job summary) and leaves every generated file in
the output folder so they can be opened in PowerPoint.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

from pptx import Presentation  # noqa: E402

from deckpilot.service import ReportingService  # noqa: E402

PROMPT_A = ("Create a six-slide APAC Q3 risk pack, compare Q2, rank top exception drivers, add commentary and "
            "speaker notes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="folder to copy the generated decks into")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="p2p_demo_"))
    t0 = time.perf_counter()
    s = ReportingService(work, use_llm=False)
    s.bootstrap()
    rows = []

    def step(code, what, rec, extra=""):
        n = len(Presentation(rec.file_path).slides)
        rows.append(f"| {code} | {what} | {rec.label} | {n} | {rec.dataset_version} | {rec.period} | "
                    f"{', '.join(f'{k}={v}' for k, v in rec.filters.items()) or 'all'} | {extra} |")

    a, _ = s.generate(PROMPT_A, "ops_risk", actor="analyst")
    step("A", "Generate APAC Q3 six-slide deck", a)
    b, _ = s.follow_up(a.version_id, "change APAC to EMEA and add high-severity exceptions", actor="analyst")
    step("B", "Follow-up: change APAC to EMEA, add high-severity", b)
    step("C", "Second file saved, first kept", b, f"v1 file still present: {Path(a.file_path).exists()}")
    ds = s.stage_upload("ops_risk", (HERE / "data" / "ops_risk_refresh_oct2026.csv").read_bytes(),
                        "ops_risk_refresh_oct2026.csv", actor="analyst", synthetic=True)
    s.activate(ds.version, actor="analyst")
    c, _ = s.follow_up(b.version_id, "refresh using latest data", actor="analyst")
    step("D", "Upload modified dataset, validate, activate, refresh", c,
         f"{ds.report.rows_accepted:,} rows accepted, {ds.report.quarantined} quarantined")
    c2, _ = s.follow_up(c.version_id, "add October", actor="analyst")
    step("D", "Follow-up: add October (now in data)", c2)
    e, notes = s.edit_commentary(c2.version_id, 7, ["EMEA exception rate remains elevated; owners confirm drivers "
                                                    "by 15 Nov.", "Settlement backlog is the main watch item."],
                                 actor="analyst")
    step("E", "Edit management commentary on slide 7", e, "human-edited, evidence kept for unchanged lines")
    s.add_comment(e.version_id, 2, "Please reconcile the high-severity count with the control log.", "risk.reviewer")
    for cmt in s.store.comments(e.version_id):
        s.resolve_comment(cmt["id"], "risk.reviewer")
    approved = s.review(e.version_id, "approved", "risk.reviewer", "Date 15 Nov verified with owners")
    data, name = s.download(e.version_id)
    step("F", "Approved, downloaded editable .pptx", approved, f"{name} ({len(data):,} bytes)")
    w, _ = s.generate("Create the full workforce review pack for 2018 with speaker notes", "workforce",
                      actor="analyst")
    step("WF", "Workforce review (case-study replica)", w)
    w2, _ = s.follow_up(w.version_id, "change to Pune and show only T&M", actor="analyst")
    step("WF", "Follow-up: Pune T&M only", w2)
    ok, _ = s.audit.verify_chain()
    pdf = s.export_pdf(a.version_id)
    for v in (e.version_id, w.version_id):
        s.export_pdf(v)
    elapsed = time.perf_counter() - t0
    print("## Project 06 acceptance demo (A-G)\n")
    print("| Step | What | Version | Slides | Dataset | Period | Filters | Note |")
    print("|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    print(f"\n(G) Audit events: {s.audit.count()}, hash chain intact: {ok}. PDF export: "
          f"{'yes' if pdf else 'not available (LibreOffice missing)'}. Total time {elapsed:.1f}s.")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for f in list((work / "decks").rglob("*.pptx")) + list((work / "decks").rglob("*.pdf")):
            shutil.copy2(f, out / f.name)
        (out / "review_log_D-0001.csv").write_text(s.store.review_log_csv("D-0001"))
        s.audit_frame(500).to_csv(out / "audit_log.csv", index=False)
        import json

        (out / "provenance_D-0001_v5.json").write_text(json.dumps(s.provenance(e.version_id), indent=2, default=str))


if __name__ == "__main__":
    main()
