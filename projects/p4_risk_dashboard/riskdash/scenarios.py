"""Forecasts and what-if scenarios for the risk agent.

Forecasts use the best information available for each metric:

* **Scheduled facts** where the future is already known: a KYC review that falls
  due on 14 October becomes overdue on 15 October unless it is completed; an AML
  alert raised on 9 September passes the 30-day deadline on 10 October.
* **A robust straight-line trend** (Theil-Sen) for everything else, clearly
  labelled as an extrapolation.

What-if scenarios are small, transparent models. Their assumptions are listed in
every result, because a number without its assumptions is not decision-useful.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from . import analytics, catalog
from .catalog import rag

if TYPE_CHECKING:  # pragma: no cover
    from .tools import RiskTools

TREND_WINDOW = {"KRI_OPLOSS_YTD_USD_M": 90, "KRI_CET1_PCT": 120, "KRI_NPL_PCT": 120}


def _business_days_after(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def forecast(tools: RiskTools, metric_id: str, horizon_days: int = 30) -> dict:
    m = catalog.get(metric_id)
    as_of = tools.as_of
    current = tools.store.value(metric_id, as_of)
    base = {"metric_id": metric_id, "as_of": as_of, "current": current, "status_now": rag(m, current),
            "horizon_days": horizon_days, "early_warning": m.early_warning, "limit": m.limit}
    if m.direction == "none":
        return {**base, "method": "none", "note": "Context metric without thresholds; no breach forecast."}

    if metric_id == "KRI_HR_KYC_OVERDUE_PCT":
        due = tools._query("q_kyc_due")
        due["due_date"] = pd.to_datetime(due["due_date"]).dt.date
        population = int(tools.store.rows(metric_id, as_of)["customers"].sum())
        overdue_now = due[due["due_date"] < as_of]
        upcoming = due[(due["due_date"] >= as_of) & (due["due_date"] < as_of + timedelta(days=horizon_days))]
        path = []
        for k in range(horizon_days + 1):
            d = as_of + timedelta(days=k)
            n = int((due["due_date"] < d).sum())
            path.append({"date": d, "overdue": n, "pct": 100 * n / population})
        first_red = next((p["date"] for p in path if rag(m, p["pct"]) == "RED"), None)
        return {**base, "method": "scheduled due dates (no further completions assumed)",
                "population": population, "overdue_now": len(overdue_now),
                "overdue_customers": overdue_now[["customer_id", "due_date"]].to_dict("records"),
                "falling_due": upcoming[["customer_id", "due_date"]].to_dict("records"),
                "projected_pct_end": path[-1]["pct"], "projected_overdue_end": path[-1]["overdue"],
                "first_red_date": first_red,
                "path": [p for p in path if p["date"].weekday() == 4 or p is path[-1]]}

    if metric_id in ("KRI_AML_ALERTS_OVER_30D", "AML_ALERTS_PENDING"):
        rows = tools.store.rows("AML_ALERTS_PENDING", as_of)
        crossings = []
        for r in rows.itertuples():
            cross = pd.Timestamp(r.alert_date).date() + timedelta(days=31)
            if cross > as_of:
                crossings.append({"customer_id": r.customer_id, "rule_code": r.rule_code,
                                  "alert_date": pd.Timestamp(r.alert_date).date(), "breaches_on": cross})
        crossings.sort(key=lambda c: c["breaches_on"])
        within = [c for c in crossings if c["breaches_on"] <= as_of + timedelta(days=horizon_days)]
        return {**base, "method": "alert ages (no further reviews assumed)",
                "projected_end": (current or 0) + len(within), "next_breaches": within,
                "first_red_date": within[0]["breaches_on"] if within and (current or 0) == 0 else
                (as_of if (current or 0) > 0 else None)}

    window = TREND_WINDOW.get(metric_id, 21)
    fit = analytics.theil_sen(tools.store.series(metric_id), as_of, window=window)
    if fit is None or current is None:
        return {**base, "method": "trend", "note": "Not enough history for a trend."}
    slope, _ = fit
    worse_high = m.direction == "lower_is_better"
    projected = current + slope * horizon_days
    if m.unit == "%":
        projected = min(max(projected, 0.0), 100.0) if m.scale == 100.0 else max(projected, 0.0)
    elif m.unit in ("count", "USD m"):
        projected = max(projected, 0.0)
    days_ew = analytics.project_crossing(current, slope, m.early_warning, worse_high, horizon_days)
    days_lim = analytics.project_crossing(current, slope, m.limit, worse_high, horizon_days)
    return {**base, "method": f"Theil-Sen trend over the last {window} days (extrapolation)",
            "trend_per_day": slope, "projected_end": projected, "projected_status_end": rag(m, projected),
            "days_to_early_warning": days_ew, "days_to_limit": days_lim,
            "early_warning_date": as_of + timedelta(days=days_ew) if days_ew is not None else None,
            "limit_date": as_of + timedelta(days=days_lim) if days_lim is not None else None}


# ---------------------------------------------------------------- what-if
HANDLER_CAPACITY_PER_DAY = 9  # exceptions an experienced handler clears per business day
HQLA_USD_M = 3_400.0  # illustrative high-quality liquid assets (synthetic balance sheet)
OPERATIONAL_DEPOSIT_RUNOFF = 0.25  # Basel III LCR run-off rate for operational deposits

SCENARIOS = {
    "payments_extra_handlers": "Add N temporary handlers to Payments Ops (each clears "
                               f"{HANDLER_CAPACITY_PER_DAY} exceptions a business day)",
    "deposit_outflow": "A further USD N million of operational deposits leaves the bank",
    "op_loss_event": "A new operational loss of USD N million is booked",
    "eur_nostro_cleanup": "Clear N% of the open EUR nostro breaks caused by release R26.10 by next week",
    "kyc_reviews_completed": "Complete N overdue or falling-due high-risk KYC reviews this week",
}


def what_if(tools: RiskTools, scenario: str, amount: float) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario {scenario!r}; choose one of {sorted(SCENARIOS)}")
    as_of = tools.as_of
    if scenario == "payments_extra_handlers":
        flow = tools.exception_flow("Payments Ops", days=7)
        backlog = int(tools.store.rows("EXC_BACKLOG", as_of, {"team": "Payments Ops"})["open_items"].sum())
        arrivals = float(flow["arrivals_per_day_recent"]) * 7 / 5  # per business day
        closures = float(flow["closures_per_day_recent"]) * 7 / 5
        extra = int(amount)

        def run(cap: float) -> list[dict]:
            b, d, path = float(backlog), as_of, []
            for _ in range(20):
                d = _business_days_after(d, 1)
                b = max(0.0, b + arrivals - cap)
                path.append({"date": d, "backlog": round(b)})
            return path

        base, scen = run(closures), run(closures + extra * HANDLER_CAPACITY_PER_DAY)
        clear = next((p["date"] for p in scen if p["backlog"] == 0), None)
        needed = max(0, math.ceil((arrivals - closures) / HANDLER_CAPACITY_PER_DAY))
        return {"scenario": scenario, "extra_handlers": extra, "backlog_now": backlog,
                "arrivals_per_business_day": arrivals, "closures_per_business_day": closures,
                "backlog_in_10_days_baseline": base[9]["backlog"], "backlog_in_10_days_scenario": scen[9]["backlog"],
                "backlog_clear_date": clear, "handlers_to_stop_growth": needed,
                "path_baseline": base[:10], "path_scenario": scen[:10],
                "assumptions": ["arrivals and closures stay at the last 7 days' average",
                                f"each extra handler clears {HANDLER_CAPACITY_PER_DAY} exceptions per business day",
                                "no weekend processing"]}
    if scenario == "deposit_outflow":
        lcr = tools.store.value("KRI_LCR_PCT", as_of) / 100
        nco = HQLA_USD_M / lcr
        new_hqla, new_nco = HQLA_USD_M - amount, nco - OPERATIONAL_DEPOSIT_RUNOFF * amount
        new_lcr = 100 * new_hqla / new_nco if new_nco > 0 else float("inf")
        m = catalog.get("KRI_LCR_PCT")
        headroom = (HQLA_USD_M - (m.limit / 100) * nco) / (1 - (m.limit / 100) * OPERATIONAL_DEPOSIT_RUNOFF)
        return {"scenario": scenario, "outflow_usd_m": amount, "lcr_now_pct": 100 * lcr, "lcr_after_pct": new_lcr,
                "status_after": rag(m, new_lcr), "max_outflow_before_limit_usd_m": headroom,
                "assumptions": [f"HQLA of USD {HQLA_USD_M:,.0f}m (illustrative synthetic balance sheet)",
                                "the outflow is paid from HQLA",
                                f"the deposits carried a {OPERATIONAL_DEPOSIT_RUNOFF:.0%} LCR run-off rate"]}
    if scenario == "op_loss_event":
        m = catalog.get("KRI_OPLOSS_YTD_USD_M")
        ytd = tools.store.value(m.id, as_of)
        after = ytd + amount
        return {"scenario": scenario, "new_loss_usd_m": amount, "ytd_now_usd_m": ytd, "ytd_after_usd_m": after,
                "status_after": rag(m, after), "headroom_to_limit_usd_m": m.limit - ytd,
                "assumptions": ["net of recoveries", "booked in the current calendar year"]}
    if scenario == "eur_nostro_cleanup":
        m = catalog.get("RECON_OPEN_BREAKS")
        rows = tools.store.rows(m.id, as_of)
        r26 = rows[(rows["recon_id"] == "NOSTRO_EUR") & rows["root_cause"].str.contains("R26.10")]
        cleared = round(len(r26) * min(max(amount, 0), 100) / 100)
        total = len(rows)
        after = total - cleared
        return {"scenario": scenario, "pct_cleared": amount, "release_breaks_open": len(r26),
                "breaks_cleared": cleared, "open_breaks_now": total, "open_breaks_after": after,
                "status_after": rag(m, after),
                "unreconciled_value_cleared_usd_m": float(r26.nlargest(cleared, "amount_usd")["amount_usd"].sum()
                                                          if cleared else 0) / 1e6,
                "assumptions": ["the largest breaks are cleared first", "no new breaks are raised"]}
    # kyc_reviews_completed
    m = catalog.get("KRI_HR_KYC_OVERDUE_PCT")
    fc = forecast(tools, m.id, horizon_days=14)
    completed = int(amount)
    end_overdue = max(0, fc["projected_overdue_end"] - completed)
    now_overdue = max(0, fc["overdue_now"] - completed)
    return {"scenario": scenario, "reviews_completed": completed, "population": fc["population"],
            "overdue_now_after": now_overdue, "pct_now_after": 100 * now_overdue / fc["population"],
            "status_now_after": rag(m, 100 * now_overdue / fc["population"]),
            "overdue_in_14_days_after": end_overdue, "pct_in_14_days_after": 100 * end_overdue / fc["population"],
            "status_in_14_days_after": rag(m, 100 * end_overdue / fc["population"]),
            "assumptions": ["the oldest reviews are completed first", "no other reviews are completed"]}
