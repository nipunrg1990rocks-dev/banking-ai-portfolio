"""Metric accuracy, non-additive volume handling, divide-by-zero and filter logic."""

import pandas as pd
import pytest
from p6_helpers import APAC_PROMPT  # noqa: F401

from deckpilot.domains.ops_risk_schema import VOLUME_GRAIN
from deckpilot.payload import safe_div
from deckpilot.spec import Comparison, DeckSpec, SlideRequest, comparison_months, period_label

Q3 = ["2026-07", "2026-08", "2026-09"]
Q2 = ["2026-04", "2026-05", "2026-06"]


def spec(domain="ops_risk", months=Q3, filters=None, cmp="custom", cmp_months=Q2):
    return DeckSpec(domain=domain, template="risk_controls" if domain == "ops_risk" else "workforce_review",
                    title="t", months=months, filters=filters or {},
                    comparison=Comparison(mode=cmp, months=cmp_months if cmp == "custom" else []),
                    slides=[SlideRequest(slide_type="cover")] * 3)


def manual(ds, months, region=None, severities=None):
    f = ds.tables["fact"]
    f = f[f["reporting_month"].isin(months)]
    if region:
        f = f[f["region"] == region]
    vol = f.drop_duplicates(VOLUME_GRAIN)
    exc = f if not severities else f[f["severity"].isin(severities)]
    return exc["exceptions"].sum() / vol["transaction_volume"].sum(), vol["completed_on_time"].sum() / vol[
        "transaction_volume"].sum()


def test_exception_rate_matches_independent_calculation(ops_env):
    d, ds, qb = ops_env
    ctx = d.context(spec(filters={"region": ["APAC"]}), ds, qb)
    m = d.metrics(ctx, Q3)
    rate, on_time = manual(ds, Q3, "APAC")
    assert m["exception_rate"] == pytest.approx(rate)
    assert m["on_time_pct"] == pytest.approx(on_time)


def test_volume_is_not_double_counted(ops_env):
    d, ds, qb = ops_env
    ctx = d.context(spec(), ds, qb)
    m = d.metrics(ctx, Q3)
    f = ds.tables["fact"]
    naive = f[f["reporting_month"].isin(Q3)]["transaction_volume"].sum()
    assert m["volume"] < naive / 3  # every process-month repeats volume on 4+ severity rows
    assert m["volume"] == f[f["reporting_month"].isin(Q3)].drop_duplicates(VOLUME_GRAIN)["transaction_volume"].sum()


def test_severity_filter_narrows_numerator_only(ops_env):
    d, ds, qb = ops_env
    all_ = d.metrics(d.context(spec(filters={"region": ["APAC"]}), ds, qb), Q3)
    hs = d.metrics(d.context(spec(filters={"region": ["APAC"], "severity": ["High", "Critical"]}), ds, qb), Q3)
    assert hs["volume"] == all_["volume"]
    assert hs["on_time_pct"] == all_["on_time_pct"]
    assert hs["exceptions"] == all_["high_sev"]
    rate, _ = manual(ds, Q3, "APAC", ["High", "Critical"])
    assert hs["exception_rate"] == pytest.approx(rate)


def test_seeded_apac_payments_spike_is_visible(ops_env):
    d, ds, qb = ops_env
    m_q3 = d.metrics(d.context(spec(filters={"region": ["APAC"], "process": ["Payments"]}), ds, qb), Q3)
    m_q2 = d.metrics(d.context(spec(filters={"region": ["APAC"], "process": ["Payments"]}), ds, qb), Q2)
    assert m_q3["exception_rate"] > 1.5 * m_q2["exception_rate"]
    assert m_q3["high_sev"] > 2 * m_q2["high_sev"]


def test_divide_by_zero_returns_none():
    assert safe_div(5, 0) is None
    assert safe_div(None, 3) is None
    assert safe_div(float("nan"), 3) is None
    assert safe_div(3, 4) == 0.75


def test_empty_selection_gives_na_not_error(ops_env):
    d, ds, qb = ops_env
    ctx = d.context(spec(filters={"region": ["APAC"], "business_line": ["Wealth Management"], "process": ["Payments"],
                                  "severity": ["Critical"]}), ds, qb)
    m = d.metrics(ctx, [])
    assert m["exception_rate"] is None


def test_comparison_windows():
    assert comparison_months(spec(cmp="prior_period")) == Q2
    assert comparison_months(spec(cmp="yoy")) == ["2025-07", "2025-08", "2025-09"]
    assert period_label(Q3) == "Q3 2026"
    assert period_label(["2026-07", "2026-08", "2026-09", "2026-10"]) == "Jul–Oct 2026"


def test_missing_comparison_period_is_disclosed(wf_env):
    d, ds, qb = wf_env
    s = spec("workforce", months=[f"2018-{m:02d}" for m in range(1, 13)], cmp="yoy")
    ctx = d.context(s, ds, qb)
    assert ctx.cmp_months == []
    assert any("not in dataset" in x for x in ctx.disclosures)


# ---------------------------------------------------------------- workforce
def test_workforce_on_contract_matches_raw_roster(wf_env, wf_raw):
    d, ds, qb = wf_env
    ctx = d.context(spec("workforce", months=[f"2018-{m:02d}" for m in range(1, 13)], cmp="none"), ds, qb)
    h = d._headline(ctx)
    raw = wf_raw.drop_duplicates(["Position #", "Name"])
    asof = pd.Timestamp("2018-09-30")
    expected = ((pd.to_datetime(raw["Start Date"]) <= asof) & (pd.to_datetime(raw["End Date"]) >= asof)).sum()
    assert h["on_contract"] == expected
    assert h["unique"] == len(raw)


def test_workforce_q1_tm_india_cost_matches_formula(wf_env, wf_raw):
    """Same rule as the case study: cost = monthly FTE x 22 working days x daily rate."""
    d, ds, qb = wf_env
    q1 = ["2018-01", "2018-02", "2018-03"]
    s = spec("workforce", months=q1, filters={"country": ["IN"], "category": ["T&M"]}, cmp="none")
    ctx = d.context(s, ds, qb)
    slide = d.build_slide("cost_bridge", ctx, 1, 1)
    raw = wf_raw.drop_duplicates(["Position #", "Name"])
    raw = raw[(raw["Country"] == "IN") & (raw["Category"] == "T&M")]
    rate = raw["Clarity Band"].map({"Band_11": 200, "Band_9": 100})
    expected = sum((raw[m] * 22 * rate).sum() for m in ["Jan-18", "Feb-18", "Mar-18"])
    tm_fact = next(c for c in slide["commentary"] if "T&M cost" in c["text"])
    assert tm_fact["evidence"]["value"] == pytest.approx(round(expected, 2))


def test_workforce_zero_allocation_and_duplicate_flags(wf_env):
    d, ds, qb = wf_env
    checks = {c.name: c.count for c in ds.report.checks}
    assert checks["Contracts with zero allocation all year"] == 20
    assert checks["Duplicate rows (Position # + Name)"] == 2
    assert len(ds.tables["people"]) == 220  # 221 rows, one duplicate counted once
