"""The Risk Insights Agent: does it find what is really wrong, and only that?"""

from datetime import date, timedelta

import pandas as pd
import pytest

from riskdash import catalog, scenarios
from riskdash.analytics import onset
from riskdash.report import markdown_to_html
from riskdash.synthetic import AS_OF, PLANTED_INCIDENTS


def _finding_for(briefing, metric_id):
    return [f for f in briefing.findings if metric_id == f.metric_id or metric_id in f.related]


@pytest.mark.parametrize("incident", PLANTED_INCIDENTS, ids=[i["id"] for i in PLANTED_INCIDENTS])
def test_agent_finds_every_planted_incident(briefing, incident):
    """Recall: each planted incident is surfaced, linked to the right change and dated close to its onset."""
    candidates = _finding_for(briefing, incident["metric"])
    assert candidates, f"{incident['id']}: no finding mentions {incident['metric']}"
    if incident["change_id"]:
        matches = [f for f in candidates if f.root_cause and f.root_cause["change_id"] == incident["change_id"]]
        assert matches, f"{incident['id']}: root cause not linked to {incident['change_id']}"
        f = matches[0]
        assert f.confidence == "high"
    else:
        f = candidates[0]
        assert f.root_cause is None  # no change to blame, so the agent must not invent one
    assert f.onset is not None and abs((f.onset - incident["onset"]).days) <= 4, (f.onset, incident["onset"])


@pytest.mark.parametrize("incident_id", ["INC-1", "INC-3", "INC-4"])
def test_agent_names_the_planted_driver(briefing, incident_id):
    incident = next(i for i in PLANTED_INCIDENTS if i["id"] == incident_id)
    dim, member = incident["driver"]
    f = next(f for f in _finding_for(briefing, incident["metric"])
             if f.root_cause and f.root_cause["change_id"] == incident["change_id"])
    assert (f.driver["dimension"], f.driver["member"]) == (dim, member)


def test_decoy_changes_are_never_blamed(briefing):
    planted = {i["change_id"] for i in PLANTED_INCIDENTS if i["change_id"]}
    blamed = {f.root_cause["change_id"] for f in briefing.findings if f.root_cause}
    assert blamed <= planted, blamed - planted


def test_findings_status_matches_the_data(briefing, store):
    """Precision: the agent never calls a metric red or amber unless the numbers say so."""
    for f in briefing.findings:
        assert f.status == store.snapshot(f.metric_id, f.focus_date or AS_OF).status, f.metric_id
        if f.status == "GREEN":
            assert f.focus_date and f.focus_date < AS_OF  # only a transient break that has since recovered
    flagged = {f.metric_id for f in briefing.findings} | {r for f in briefing.findings for r in f.related}
    for m in catalog.METRICS:
        s = store.snapshot(m.id, AS_OF)
        if s.status == "RED":
            assert m.id in flagged, f"{m.id} is RED but the briefing does not mention it"


def test_actions_follow_ras_004_deadlines(briefing):
    for f in briefing.findings:
        for a in f.actions:
            assert a.owner and a.due >= AS_OF
            if f.status == "RED":
                assert a.due <= AS_OF + timedelta(days=7)


def test_briefing_renders_as_a_board_pack(briefing):
    md = briefing.markdown()
    assert md.startswith("# ") and "Executive summary" in md
    for domain in ("Capital and liquidity", "Financial crime and compliance", "Data quality"):
        assert domain in md
    html = markdown_to_html(md, "Board pack", stamp="DRAFT - not approved")
    assert html.startswith("<!doctype html>") and "<script" not in html and "DRAFT" in html


def test_agent_is_fast_and_uses_tools(briefing):
    assert 20 <= len(briefing.trace) <= 200
    assert {c.tool for c in briefing.trace} >= {"scan_metrics", "explain_change", "find_onset", "correlate_changes"}


def test_onset_detector_tells_steps_from_ramps():
    days = [date(2026, 8, 1) + timedelta(days=i) for i in range(40)]
    step = pd.Series([10.0] * 25 + [20.0] * 15, index=days)
    ramp = pd.Series([10.0] * 20 + [10.0 + i for i in range(1, 21)], index=days)
    s = onset(step, days[-1])
    r = onset(ramp, days[-1])
    assert s.kind == "step" and s.onset == days[25]
    assert r.kind == "ramp" and abs((r.onset - days[20]).days) <= 2


def test_kyc_forecast_uses_scheduled_due_dates(tools):
    f = scenarios.forecast(tools, "KRI_HR_KYC_OVERDUE_PCT")
    assert f["population"] == 39 and f["overdue_now"] == 1
    assert f["projected_overdue_end"] == 1 + len(f["falling_due"])
    assert all(AS_OF < d["due_date"] <= AS_OF + timedelta(days=30) for d in f["falling_due"])


def test_lcr_forecast_crosses_the_limit(tools):
    f = scenarios.forecast(tools, "KRI_LCR_PCT")
    assert f["status_now"] == "AMBER" and f["trend_per_day"] < 0
    assert f["limit_date"] is not None and f["limit_date"] > AS_OF


@pytest.mark.parametrize("scenario,amount,check", [
    ("payments_extra_handlers", 3, lambda r: r["backlog_in_10_days_scenario"] < r["backlog_now"]
     < r["backlog_in_10_days_baseline"]),
    ("payments_extra_handlers", 0, lambda r: r["backlog_in_10_days_scenario"] == r["backlog_in_10_days_baseline"]),
    ("deposit_outflow", 250, lambda r: r["lcr_after_pct"] < r["lcr_now_pct"] and r["status_after"] == "AMBER"),
    ("deposit_outflow", 400, lambda r: r["status_after"] == "RED"),
    ("op_loss_event", 1, lambda r: r["ytd_after_usd_m"] == pytest.approx(r["ytd_now_usd_m"] + 1)),
    ("eur_nostro_cleanup", 100, lambda r: r["open_breaks_after"] == r["open_breaks_now"] - r["release_breaks_open"]),
    ("kyc_reviews_completed", 1, lambda r: r["status_now_after"] == "GREEN"),
])
def test_what_if_scenarios(tools, scenario, amount, check):
    r = scenarios.what_if(tools, scenario, amount)
    assert check(r), r
    assert r["assumptions"], "every scenario must state its assumptions"


def test_what_if_rejects_unknown_scenarios(tools):
    with pytest.raises(ValueError):
        scenarios.what_if(tools, "print_money", 1)


def test_tools_return_json_safe_values(tools):
    import json

    for name, kwargs in [("scan_metrics", {}), ("get_metric", {"metric_id": "KRI_LCR_PCT"}),
                         ("forecast", {"metric_id": "KRI_LCR_PCT"}),
                         ("list_records", {"metric_id": "RECON_OPEN_BREAKS", "limit": 3}),
                         ("get_policy", {"policy_ref": "RAS-004 s.6"})]:
        json.dumps(tools.call(name, **kwargs))
    assert "error" in tools.call("get_metric", metric_id="NOT_A_METRIC")


def test_backtest_detects_every_incident_without_wrong_causes(store):
    """Replay every business day from August: full recall, never a wrong root cause."""
    from riskdash.evaluation import run_evaluation

    report = run_evaluation(store)
    assert report["recall"] == 1.0, report["incidents"]
    assert report["wrong_root_causes"] == []
    assert all(r["lag_business_days"] <= 3 for r in report["incidents"])
