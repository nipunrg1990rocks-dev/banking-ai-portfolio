"""Back-test the Risk Insights Agent against the planted incidents.

The agent is run as if it were each business day in turn (it only sees data up
to that day). For every planted incident we record the first day the agent
linked it to the right cause, and on every day we check that it never blamed
a change that is not behind an incident.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .agent import RiskInsightsAgent
from .metrics import MetricStore
from .synthetic import AS_OF, PLANTED_INCIDENTS


def _detected(briefing, incident) -> bool:
    for f in briefing.findings:
        if incident["metric"] != f.metric_id and incident["metric"] not in f.related:
            continue
        if incident["change_id"] is None or (f.root_cause and f.root_cause["change_id"] == incident["change_id"]):
            return True
    return False


def run_evaluation(store: MetricStore, start: date = date(2026, 8, 3), end: date = AS_OF) -> dict:
    planted = {i["change_id"] for i in PLANTED_INCIDENTS if i["change_id"]}
    days = [d for d in store.dates if start <= d <= end and d.weekday() < 5]
    first_seen: dict[str, date] = {}
    wrong_causes: list[dict] = []
    findings_per_day = []
    for d in days:
        briefing = RiskInsightsAgent(store, d).run()
        findings_per_day.append(len(briefing.findings))
        for f in briefing.findings:
            if f.root_cause and f.root_cause["change_id"] not in planted:
                wrong_causes.append({"date": d.isoformat(), "metric": f.metric_id,
                                     "change_id": f.root_cause["change_id"]})
        for inc in PLANTED_INCIDENTS:
            if inc["id"] not in first_seen and d >= inc["onset"] - timedelta(days=3) and _detected(briefing, inc):
                first_seen[inc["id"]] = d
    rows = []
    for inc in PLANTED_INCIDENTS:
        seen = first_seen.get(inc["id"])
        lag = int(np.busday_count(inc["onset"], seen)) if seen else None
        rows.append({"id": inc["id"], "metric": inc["metric"], "cause": inc["change_id"] or "(no change: overdue item)",
                     "onset": inc["onset"].isoformat(), "first_detected": seen.isoformat() if seen else None,
                     "lag_business_days": lag, "story": inc["story"]})
    detected = [r for r in rows if r["first_detected"]]
    return {
        "reporting_days": len(days), "from": days[0].isoformat(), "to": days[-1].isoformat(),
        "incidents": rows, "recall": len(detected) / len(rows),
        "median_lag_business_days": float(np.median([r["lag_business_days"] for r in detected])) if detected else None,
        "wrong_root_causes": wrong_causes, "avg_findings_per_day": round(float(np.mean(findings_per_day)), 1),
    }


def format_report(report: dict) -> str:
    lines = ["# Risk Insights Agent back-test", "",
             f"Replayed {report['reporting_days']} business days ({report['from']} to {report['to']}); on each day the "
             "agent saw only the data up to that day.", "",
             f"- Incidents detected with the right cause: **{report['recall']:.0%}** "
             f"({sum(1 for r in report['incidents'] if r['first_detected'])} of {len(report['incidents'])})",
             f"- Median detection lag: **{report['median_lag_business_days']:.0f} business day(s)** after onset",
             f"- Wrong root causes on any day: **{len(report['wrong_root_causes'])}**",
             f"- Average findings per briefing: {report['avg_findings_per_day']}", "",
             "| Incident | Metric | Cause | Onset | First detected | Lag (business days) |",
             "|---|---|---|---|---|---|"]
    for r in report["incidents"]:
        lines.append(f"| {r['id']} | {r['metric']} | {r['cause']} | {r['onset']} | {r['first_detected'] or 'missed'} "
                     f"| {'' if r['lag_business_days'] is None else r['lag_business_days']} |")
    if report["wrong_root_causes"]:
        lines += ["", "Wrong root causes:", ""] + [f"- {w['date']}: {w['metric']} blamed on {w['change_id']}"
                                                  for w in report["wrong_root_causes"]]
    return "\n".join(lines)
