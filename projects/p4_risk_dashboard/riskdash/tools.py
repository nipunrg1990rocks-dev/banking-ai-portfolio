"""The risk agent's tools.

Every tool is a plain Python method that reads the metric store (or runs one of
the read-only SQL queries in ``sql/queries``) and returns a JSON-serialisable
dict. The same tools serve three callers:

* the offline investigator in ``agent.py`` (deterministic, free),
* Claude, when live mode is enabled (``llm.py`` exposes them as tool schemas),
* the chat router and the dashboard itself.

Every call is recorded in ``trace`` so the UI can show exactly which tool ran
with which arguments, and what came back. Tools never write data.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import analytics, catalog, scenarios
from .catalog import rag
from .metrics import MetricStore

QUERY_DIR = Path(__file__).resolve().parent / "sql" / "queries"
POLICY_DIR = Path(__file__).resolve().parents[2] / "p1_compliance_copilot" / "data" / "policies"

# Data lineage: which source systems feed which tables, reconciliations and teams.
LINEAGE: dict[tuple[str, str], list[str]] = {
    ("recon_id", "NOSTRO_USD"): ["LedgerCore"], ("recon_id", "NOSTRO_EUR"): ["LedgerCore"],
    ("recon_id", "NOSTRO_GBP"): ["LedgerCore"], ("recon_id", "GL_SUBLEDGER"): ["GeneralLedger", "LedgerCore"],
    ("recon_id", "CARD_SCHEME"): ["CardHub"], ("recon_id", "SWIFT_PAYMENTS"): ["PayFlow"],
    ("table_name", "silver.transactions"): ["LedgerCore", "PayFlow"],
    ("table_name", "silver.customers"): ["OnboardIQ"], ("table_name", "silver.accounts"): ["LedgerCore"],
    ("table_name", "kyc.customer_profile"): ["OnboardIQ"], ("table_name", "kyc.periodic_reviews"): ["OnboardIQ"],
    ("table_name", "payments.swift_messages"): ["PayFlow"], ("table_name", "ref.fx_rates"): ["MarketData"],
    ("table_name", "gl.journal_lines"): ["GeneralLedger"], ("table_name", "risk.loan_book"): ["LedgerCore"],
    ("table_name", "cards.settlements"): ["CardHub"],
    ("team", "Payments Ops"): ["PayFlow", "HR Systems"], ("team", "Reconciliations Ops"): ["LedgerCore"],
    ("team", "Finance Ops"): ["GeneralLedger"], ("team", "Securities Ops"): ["LedgerCore"],
    ("team", "Financial Crime Ops"): ["PayFlow"], ("team", "KYC Ops"): ["OnboardIQ"],
    ("team", "Credit Risk Ops"): ["LedgerCore"],
}
METRIC_SYSTEMS = {
    "KRI_LCR_PCT": ["Treasury"],
    "KRI_OPLOSS_YTD_USD_M": ["PayFlow", "LedgerCore", "CardHub", "GeneralLedger"],
}
# Which kinds of change usually move which kinds of metric.
RELEVANT_CHANGE_TYPES = {
    "Reconciliations": {"RELEASE", "INCIDENT"},
    "Data quality": {"RELEASE", "INCIDENT"},
    "Operations and SLAs": {"BUSINESS_CHANGE", "INCIDENT"},
    "Capital and liquidity": {"BUSINESS_CHANGE"},
    "Operational risk": {"INCIDENT"},
}
DRIVER_DIMENSION = {  # the dimension that best separates root causes, tried first
    "DQ_HEALTH_SCORE": "table_name", "DQ_RULES_BREACHED": "rule_id", "FEED_ON_TIME_PCT": "source_system",
    "RECON_OPEN_BREAKS": "recon_id", "RECON_OPEN_VALUE_USD_M": "recon_id", "RECON_AGED_BREAKS": "recon_id",
    "RECON_AUTO_MATCH_PCT": "recon_id", "EXC_BACKLOG": "team", "EXC_PAST_DUE": "team",
    "EXC_SLA_BREACH_PCT": "team", "KRI_OPLOSS_YTD_USD_M": "basel_event_type",
}
LEVEL_DIMENSION = {"DQ_HEALTH_SCORE": "rule_id"}  # a lasting shortfall is best traced to the failing rule


def _iso(d: Any) -> Any:
    return d.isoformat() if isinstance(d, date) else d


def _clean(obj: Any) -> Any:
    """Make tool output JSON-safe and compact (rounded floats, ISO dates)."""
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp,)):
        return obj.date().isoformat() if obj == obj.normalize() else obj.isoformat(sep=" ")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return None if np.isnan(obj) else round(float(obj), 4)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


@dataclass
class ToolCall:
    tool: str
    args: dict
    result: dict
    ms: float


@dataclass
class RiskTools:
    store: MetricStore
    as_of: date
    trace: list[ToolCall] = field(default_factory=list)

    # ---------------------------------------------------------------- infra
    def call(self, name: str, **kwargs) -> dict:
        if name not in TOOL_NAMES:
            raise ValueError(f"Unknown tool {name!r}")
        start = time.perf_counter()
        try:
            result = _clean(getattr(self, name)(**kwargs))
        except (KeyError, ValueError) as exc:
            result = {"error": str(exc)}
        self.trace.append(ToolCall(name, _clean(kwargs), result, round((time.perf_counter() - start) * 1000, 1)))
        return result

    def _query(self, name: str) -> pd.DataFrame:
        sql = (QUERY_DIR / f"{name}.sql").read_text().replace("${as_of}", f"DATE '{self.as_of.isoformat()}'")
        return self.store.engine.run(sql)

    def _date(self, d: str | date | None) -> date:
        if d is None:
            return self.as_of
        d = d if isinstance(d, date) else date.fromisoformat(str(d))
        if d < self.store.first_date or d > self.as_of:
            raise ValueError(f"Date {d} is outside the reporting window {self.store.first_date} to {self.as_of}")
        return d

    # ---------------------------------------------------------------- tools
    def list_metrics(self) -> dict:
        """Catalogue of every KPI/KRI the agent can use."""
        return {"metrics": [{"metric_id": m.id, "name": m.name, "domain": m.domain, "unit": m.unit,
                             "dimensions": list(m.dimensions), "kri": m.kri} for m in catalog.METRICS]}

    def scan_metrics(self) -> dict:
        """Status of every metric today, 7 days ago, and its worst point in the last 7 days."""
        out = []
        for m in catalog.METRICS:
            snap = self.store.snapshot(m.id, self.as_of)
            s = self.store.series(m.id)
            week = s[(s.index > self.as_of - timedelta(days=7)) & (s.index <= self.as_of)].dropna()
            worst_d, worst_v = None, None
            if not week.empty and m.direction != "none":
                worst_d = week.idxmin() if m.direction == "higher_is_better" else week.idxmax()
                worst_v = float(week.loc[worst_d])
            row = snap.as_dict()
            row.update({"days_in_status": self.store.days_in_status(m.id, self.as_of),
                        "worst_7d": worst_v, "worst_7d_date": worst_d,
                        "worst_7d_status": rag(m, worst_v) if worst_v is not None else "NONE",
                        "data_to": self.store.latest_data_date(m.id)})
            out.append(row)
        return {"as_of": self.as_of, "metrics": out}

    def get_metric(self, metric_id: str, on: str | None = None) -> dict:
        """One metric's value, RAG status, thresholds and definition on a day."""
        d = self._date(on)
        m = catalog.get(metric_id)
        snap = self.store.snapshot(metric_id, d).as_dict()
        snap.update({"definition": m.definition, "formula": m.formula(),
                     "days_in_status": self.store.days_in_status(metric_id, d)})
        return snap

    def get_trend(self, metric_id: str, days: int = 28) -> dict:
        """Daily values for the last ``days`` days, with min, max and a robust trend."""
        m = catalog.get(metric_id)
        s = self.store.series(metric_id)
        s = s[(s.index > self.as_of - timedelta(days=days)) & (s.index <= self.as_of)].dropna()
        trend = analytics.theil_sen(self.store.series(metric_id), self.as_of, window=days)
        return {"metric_id": metric_id, "unit": m.unit,
                "points": [{"date": d, "value": v} for d, v in s.items()],
                "min": float(s.min()) if len(s) else None, "min_date": s.idxmin() if len(s) else None,
                "max": float(s.max()) if len(s) else None, "max_date": s.idxmax() if len(s) else None,
                "trend_per_day": trend[0] if trend else None}

    def explain_change(self, metric_id: str, on: str | None = None, compare_days: int = 7,
                       dimension: str | None = None) -> dict:
        """Which dimension members drove the metric's move between two days."""
        d = self._date(on)
        m = catalog.get(metric_id)
        dims = [dimension] if dimension else list(m.dimensions)
        if not dims:
            return {"metric_id": metric_id, "note": "This metric has no dimensions to break down by."}
        prev = d - timedelta(days=compare_days)
        total = (self.store.value(metric_id, d) or 0.0) - (self.store.value(metric_id, prev) or 0.0)
        results = []
        for dim in dims:
            bd = self.store.breakdown(metric_id, dim, d, compare_days)
            if bd.empty:
                continue
            top = bd.iloc[0]
            share = float(top["contribution"] / total) if total else None
            results.append({"dimension": dim, "top_member": top["member"], "top_contribution": top["contribution"],
                            "share_of_change": share, "members": bd.head(5).to_dict("records")})
        # Prefer the domain's natural root-cause dimension when it explains most of the move.
        preferred = DRIVER_DIMENSION.get(metric_id)
        results.sort(key=lambda r: (r["dimension"] != preferred or abs(r["share_of_change"] or 0) < 0.5,
                                    -abs(r["share_of_change"] or 0)))
        return {"metric_id": metric_id, "date": d, "compared_with": prev,
                "value": self.store.value(metric_id, d), "prior_value": self.store.value(metric_id, prev),
                "total_change": total, "by_dimension": results}

    def explain_level(self, metric_id: str, dimension: str | None = None, on: str | None = None) -> dict:
        """Which members make up the metric's current shortfall (for a level that has been bad a while)."""
        d = self._date(on)
        m = catalog.get(metric_id)
        dim = dimension or LEVEL_DIMENSION.get(metric_id) or DRIVER_DIMENSION.get(metric_id) or \
            (m.dimensions[0] if m.dimensions else None)
        if dim is None:
            return {"metric_id": metric_id, "note": "This metric has no dimensions to break down by."}
        comp = self.store.composition(metric_id, dim, d)
        return {"metric_id": metric_id, "date": d, "dimension": dim, "value": self.store.value(metric_id, d),
                "members": comp.head(5).to_dict("records")}

    def find_onset(self, metric_id: str, dimension: str | None = None, member: str | None = None,
                   on: str | None = None) -> dict:
        """When did the move start? Change-point fit on the metric (or one dimension member)."""
        d = self._date(on)
        series, label = self._driver_series(metric_id, dimension, member)
        o = analytics.onset(series, d)
        if o is None:
            return {"metric_id": metric_id, "series": label, "onset": None}
        return {"metric_id": metric_id, "series": label, "onset": o.onset, "kind": o.kind,
                "level_before": o.before, "level_now": o.after, "fit_improvement_pct": o.error_reduction_pct}

    def correlate_changes(self, metric_id: str, onset: str, kind: str = "step", dimension: str | None = None,
                          member: str | None = None) -> dict:
        """Change-calendar entries that could explain a move starting on ``onset``.

        Candidates must happen on or shortly before the onset. Each gets a score: +2 if its system
        feeds the driver (lineage), +1 if its type usually moves this kind of metric, plus a proximity bonus.
        """
        m = catalog.get(metric_id)
        start = date.fromisoformat(str(onset))
        before, after = (21, 7) if kind == "ramp" else (5, 0)
        systems = set(METRIC_SYSTEMS.get(metric_id, []))
        if dimension and member:
            systems |= set(self._systems_for(dimension, member))
        relevant_types = RELEVANT_CHANGE_TYPES.get(m.domain, set())
        events = self._query("q_change_events")
        out = []
        for e in events.itertuples():
            ed = pd.Timestamp(e.event_date).date()
            gap = (start - ed).days
            if gap < -after or gap > before:
                continue
            lineage = e.system in systems
            score = (2 if lineage else 0) + (1 if e.change_type in relevant_types else 0) + 1 / (1 + abs(gap))
            out.append({"change_id": e.change_id, "date": ed, "system": e.system, "type": e.change_type,
                        "description": e.description, "days_before_onset": gap, "lineage_match": lineage,
                        "score": round(score, 3)})
        out.sort(key=lambda r: -r["score"])
        best = out[0] if out else None
        confidence = "none"
        if best:
            confidence = "high" if best["lineage_match"] and best["score"] >= 3 else \
                "medium" if best["lineage_match"] else "low"
            if confidence == "low":
                best = None  # timing alone is not evidence of cause
        return {"metric_id": metric_id, "onset": start, "systems_checked": sorted(systems),
                "candidates": out[:4], "best": best, "confidence": confidence}

    def forecast(self, metric_id: str, horizon_days: int = 30) -> dict:
        """When will the metric cross its early-warning trigger or limit?"""
        return scenarios.forecast(self, metric_id, horizon_days)

    def what_if(self, scenario: str, amount: float) -> dict:
        """Run a what-if scenario (see scenarios.SCENARIOS)."""
        return scenarios.what_if(self, scenario, amount)

    def list_records(self, metric_id: str, dimension: str | None = None, member: str | None = None,
                     limit: int = 10) -> dict:
        """The records behind a metric today (optionally for one dimension member)."""
        filters = {dimension: member} if dimension and member else None
        df = self.store.records(metric_id, self.as_of, filters)
        return {"metric_id": metric_id, "filter": filters, "count": len(df),
                "records": df.head(limit).to_dict("records")}

    def exception_flow(self, team: str, days: int = 14) -> dict:
        """Daily exception arrivals vs closures for a team, before and after the last ``days`` days."""
        flow = self._query("q_exception_flow")
        flow["obs_date"] = pd.to_datetime(flow["obs_date"]).dt.date
        flow = flow[(flow["team"] == team) & (flow["obs_date"] <= self.as_of)].sort_values("obs_date")
        recent = flow[flow["obs_date"] > self.as_of - timedelta(days=days)]
        base = flow[(flow["obs_date"] <= self.as_of - timedelta(days=days))
                    & (flow["obs_date"] > self.as_of - timedelta(days=days + 56))]
        s = flow.set_index("obs_date")["opened"].astype(float)
        o = analytics.onset(s[[d.weekday() < 5 for d in s.index]], self.as_of)  # business days only
        return {"team": team, "window_days": days,
                "arrivals_per_day_recent": recent["opened"].mean(), "arrivals_per_day_before": base["opened"].mean(),
                "closures_per_day_recent": recent["closed"].mean(), "closures_per_day_before": base["closed"].mean(),
                "arrivals_change_pct": 100 * (recent["opened"].mean() / base["opened"].mean() - 1)
                if base["opened"].mean() else None,
                "arrivals_onset": o.onset if o else None}

    def get_policy(self, policy_ref: str) -> dict:
        """Escalation rules from the Project 01 policy library (e.g. 'RAS-004 s.6')."""
        doc_id = policy_ref.split()[0]
        path = next(iter(sorted(POLICY_DIR.glob(f"{doc_id}_*.md"))), None)
        if path is None:
            return {"policy_ref": policy_ref, "error": "policy not found"}
        text = path.read_text()
        sections = {}
        for block in text.split("\n## ")[1:]:
            title, _, body = block.partition("\n")
            sections[title.strip()] = body.strip()
        wanted = {k: v for k, v in sections.items() if any(w in k for w in ("Breach", "Escalation", "Triage",
                                                                           "Monitoring", "Reporting"))}
        return {"policy_ref": policy_ref, "document": path.stem, "sections": wanted or sections}

    # -------------------------------------------------------------- helpers
    def _systems_for(self, dimension: str, member: str) -> list[str]:
        if dimension == "rule_id":
            rules = self._query("q_dq_rules")
            match = rules[rules["rule_id"] == member]
            if not match.empty:
                return LINEAGE.get(("table_name", match.iloc[0]["table_name"]), [])
        if dimension == "source_system":
            return [member]
        return LINEAGE.get((dimension, member), [])

    def _driver_series(self, metric_id: str, dimension: str | None, member: str | None) -> tuple[pd.Series, str]:
        m = catalog.get(metric_id)
        if not dimension or not member:
            return self.store.series(metric_id), metric_id
        df = self.store.views[m.view]
        if dimension == "rule_id" and "pass_rate_pct" in df.columns:
            rule = df[df["rule_id"] == member].set_index("obs_date")["pass_rate_pct"].astype(float)
            return rule.reindex(self.store.dates), f"{member} pass rate"
        sub = df[df[dimension].astype(str) == str(member)]
        values = {d: self.store._value(m, g) for d, g in sub.groupby("obs_date")}
        empty = self.store._value(m, sub.iloc[0:0])
        return pd.Series([values.get(d, empty) for d in self.store.dates], index=self.store.dates), \
            f"{metric_id} for {dimension}={member}"


TOOL_NAMES = ["list_metrics", "scan_metrics", "get_metric", "get_trend", "explain_change", "explain_level",
              "find_onset",
              "correlate_changes", "forecast", "what_if", "list_records", "exception_flow", "get_policy"]


def trace_as_json(trace: list[ToolCall]) -> str:
    return json.dumps([{"tool": t.tool, "args": t.args, "ms": t.ms} for t in trace], indent=2)
