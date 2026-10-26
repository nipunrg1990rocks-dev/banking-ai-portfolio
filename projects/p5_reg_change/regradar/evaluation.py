"""Score the agents against a hand-written answer key (``data/eval/answer_key.json``).

Metrics:

* change detection: NEW / AMENDED / UNCHANGED / REMOVED per provision
* parameter deltas: old figure, new figure and direction for each amended rule
* gap status: COVERED / PARTIAL / CONFLICT / MISSING per changed provision
* policy citation: the cited section is one an analyst would cite
* unsafe misses: a real gap reported as COVERED (the error that matters most)
* review catch rate: of the wrong statuses, how many the Challenger sent to a human

Caveat, stated wherever results are shown: the circulars, policies and answer
key were written for this project, and thresholds were tuned on the same four
circulars, so these are development-set results, not a measure of accuracy on
real regulation.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date

from .agents import DEMO_AS_OF, run_assessment
from .corpus import DATA, load_circular_pairs

ANSWER_KEY = DATA / "eval" / "answer_key.json"
GAP_STATUSES = {"CONFLICT", "PARTIAL", "MISSING"}


def _raw_key() -> dict:
    return json.loads(ANSWER_KEY.read_text(encoding="utf-8"))


def load_key() -> dict:
    return {k: v for k, v in _raw_key().items() if not k.startswith("_")}


def held_out() -> set[str]:
    return set(_raw_key().get("_held_out", []))


def run_evaluation(as_of: date = DEMO_AS_OF, llm=None) -> dict:
    key = load_key()
    rows = []
    param_total = param_ok = 0
    for pair in load_circular_pairs():
        expected = key.get(pair.key, [])
        a = run_assessment(pair, as_of, llm=llm)
        changes = {c.pid: c for c in a.changes}
        findings = {f.provision.pid: f for f in a.findings}
        for exp in expected:
            pid = exp["pid"]
            ch = changes.get(pid)
            f = findings.get(pid)
            row = {"circular": pair.key, "pid": pid, "change_expected": exp["change"],
                   "change_got": ch.change_type if ch else None}
            if "status" in exp:
                row.update({"status_expected": exp["status"], "status_got": f.status if f else None,
                            "policies_expected": exp.get("policies", []),
                            "policies_got": f.policy_refs if f else [], "flagged": bool(f and f.needs_review),
                            "rating": f.rating if f else None})
                if exp.get("policies"):
                    row["citation_ok"] = bool(f and set(f.policy_refs) & set(exp["policies"]))
            for old, new, direction in exp.get("params", []):
                param_total += 1
                hit = ch and any(p.old.text.lower() == old.lower() and p.new.text.lower() == new.lower()
                                 and p.direction == direction for p in ch.parameter_changes)
                param_ok += bool(hit)
            rows.append(row)
    held = held_out()
    return {"metrics": _metrics(rows, param_ok, param_total),
            "development": _metrics([r for r in rows if r["circular"] not in held]),
            "held_out": _metrics([r for r in rows if r["circular"] in held]) if held else None,
            "held_out_circulars": sorted(held), "rows": rows,
            "confusion": [{"expected": e, "got": g, "count": n} for (e, g), n in sorted(
                Counter((r["status_expected"], r["status_got"]) for r in rows if "status_expected" in r).items())],
            "mode": "offline rules engine" if llm is None else llm.label, "as_of": as_of.isoformat()}


def _metrics(rows: list[dict], param_ok: int | None = None, param_total: int | None = None) -> dict:
    status_rows = [r for r in rows if "status_expected" in r]
    wrong = [r for r in status_rows if r["status_expected"] != r["status_got"]]
    cites = [r for r in status_rows if "citation_ok" in r]
    unsafe = [r for r in status_rows if r["status_expected"] in GAP_STATUSES and r["status_got"] == "COVERED"]
    false_alarms = [r for r in status_rows if r["status_expected"] == "COVERED" and r["status_got"] in GAP_STATUSES]
    exp_gaps = [r for r in status_rows if r["status_expected"] in GAP_STATUSES]
    got_gaps = [r for r in status_rows if r["status_got"] in GAP_STATUSES]
    tp = sum(1 for r in status_rows if r["status_expected"] in GAP_STATUSES and r["status_got"] in GAP_STATUSES)
    metrics = {
        "provisions": len(rows),
        "change_accuracy": sum(r["change_expected"] == r["change_got"] for r in rows) / len(rows),
        "parameter_delta_accuracy": (param_ok / param_total if param_total else 1.0) if param_total is not None
        else None,
        "parameter_deltas": param_total,
        "assessed_provisions": len(status_rows),
        "gap_status_accuracy": 1 - len(wrong) / len(status_rows),
        "gap_detection_recall": tp / len(exp_gaps) if exp_gaps else 1.0,
        "gap_detection_precision": tp / len(got_gaps) if got_gaps else 1.0,
        "unsafe_misses": len(unsafe),
        "false_alarms": len(false_alarms),
        "citation_accuracy": sum(r["citation_ok"] for r in cites) / len(cites) if cites else 1.0,
        "wrong_status_flagged_for_review": sum(r["flagged"] for r in wrong),
        "wrong_status": len(wrong),
    }
    return metrics


def format_report(report: dict) -> str:
    m = report["metrics"]
    lines = [
        "# Regulatory Change Impact Analyzer: evaluation",
        "",
        f"Mode: {report['mode']}. As-of date: {report['as_of']}. Answer key: data/eval/answer_key.json.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Change detection (NEW / AMENDED / UNCHANGED / REMOVED) | {m['change_accuracy']:.0%} of {m['provisions']} "
        "provisions |",
        f"| Parameter deltas (old figure, new figure, direction) | {m['parameter_delta_accuracy']:.0%} of "
        f"{m['parameter_deltas']} |",
        f"| Gap status (COVERED / PARTIAL / CONFLICT / MISSING) | {m['gap_status_accuracy']:.0%} of "
        f"{m['assessed_provisions']} |",
        f"| Gap detection recall / precision | {m['gap_detection_recall']:.0%} / {m['gap_detection_precision']:.0%} |",
        f"| Unsafe misses (real gap reported as covered) | {m['unsafe_misses']} |",
        f"| False alarms (covered reported as a gap) | {m['false_alarms']} |",
        f"| Policy citation accuracy | {m['citation_accuracy']:.0%} |",
        f"| Wrong statuses sent to human review by the Challenger | {m['wrong_status_flagged_for_review']} of "
        f"{m['wrong_status']} |",
        "",
        "Caveat: the circulars, policies and answer key are synthetic and were written for this project. "
        "Thresholds were tuned on the four development circulars.",
        "",
        "| Split | Gap status | Citation | Unsafe misses |",
        "|---|---|---|---|",
        _split_row("Development (4 circulars)", report["development"]),
        *([_split_row(f"Held-out ({', '.join(report['held_out_circulars'])})", report["held_out"])]
          if report.get("held_out") else []),
        "",
        "## Wrong statuses",
        "",
    ]
    wrong = [r for r in report["rows"] if "status_expected" in r and r["status_expected"] != r["status_got"]]
    if not wrong:
        lines.append("None.")
    for r in wrong:
        lines.append(f"- {r['pid']}: expected {r['status_expected']}, got {r['status_got']}"
                     f" ({'sent to human review' if r['flagged'] else 'not flagged'})")
    return "\n".join(lines)


def _split_row(name: str, m: dict) -> str:
    return (f"| {name} | {m['gap_status_accuracy']:.0%} of {m['assessed_provisions']} | {m['citation_accuracy']:.0%} "
            f"| {m['unsafe_misses']} |")
