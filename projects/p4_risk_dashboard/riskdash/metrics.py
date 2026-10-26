"""Compute every metric from the fact views, for any day, slice or drill-down.

``MetricStore`` runs each fact view once on the chosen engine and keeps the
result. From there every number on the dashboard is a sum over those rows, so a
KPI tile, its trend line, its breakdown by dimension and the list of records
behind it always reconcile with each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import catalog
from .catalog import Metric, rag
from .engine import SqlEngine


@dataclass
class Snapshot:
    metric: Metric
    as_of: date
    value: float | None
    prior: float | None  # 7 days earlier
    status: str
    prior_status: str

    @property
    def delta(self) -> float | None:
        if self.value is None or self.prior is None or np.isnan(self.value) or np.isnan(self.prior):
            return None
        return self.value - self.prior

    def as_dict(self) -> dict:
        m = self.metric
        return {
            "metric_id": m.id, "name": m.name, "domain": m.domain, "as_of": self.as_of.isoformat(),
            "value": _r(self.value), "display": m.fmt(self.value), "prior_7d": _r(self.prior),
            "change_7d": _r(self.delta), "status": self.status, "status_7d_ago": self.prior_status,
            "early_warning": m.early_warning, "limit": m.limit, "unit": m.unit, "direction": m.direction,
            "policy_ref": m.policy_ref, "owner": m.owner,
        }


def _r(v: float | None, nd: int = 4) -> float | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return round(float(v), nd)


def _as_date(v) -> date:
    return v if isinstance(v, date) and not isinstance(v, pd.Timestamp) else pd.Timestamp(v).date()


class MetricStore:
    def __init__(self, engine: SqlEngine) -> None:
        self.engine = engine
        self.views: dict[str, pd.DataFrame] = {}
        for view in catalog.VIEWS:
            df = engine.run(catalog.view_sql(view))
            df["obs_date"] = pd.to_datetime(df["obs_date"]).dt.date
            self.views[view] = df
        cal = engine.run("SELECT d FROM ${mart.calendar} ORDER BY d")
        self.dates: list[date] = [_as_date(d) for d in cal["d"]]
        self.first_date, self.last_date = self.dates[0], self.dates[-1]
        self._series: dict[str, pd.Series] = {}

    # ----------------------------------------------------------- values
    @staticmethod
    def _value(metric: Metric, rows: pd.DataFrame) -> float:
        if rows.empty:
            return 0.0 if metric.fill_zero and metric.denominator is None else float("nan")
        num = pd.to_numeric(rows[metric.numerator], errors="coerce").sum(min_count=1)
        if num is None or (isinstance(num, float) and np.isnan(num)):
            return 0.0 if metric.fill_zero else float("nan")
        if metric.denominator is None:
            return float(num) * metric.scale
        den = pd.to_numeric(rows[metric.denominator], errors="coerce").sum()
        return float(num) / float(den) * metric.scale if den else float("nan")

    def series(self, metric_id: str) -> pd.Series:
        """Daily values for the whole history (index: date)."""
        if metric_id not in self._series:
            m = catalog.get(metric_id)
            df = self.views[m.view]
            grouped = {d: self._value(m, g) for d, g in df.groupby("obs_date")}
            values = [grouped.get(d, self._value(m, df.iloc[0:0])) for d in self.dates]
            self._series[metric_id] = pd.Series(values, index=pd.Index(self.dates, name="obs_date"), name=metric_id)
        return self._series[metric_id]

    def value(self, metric_id: str, d: date) -> float | None:
        s = self.series(metric_id)
        return float(s.loc[d]) if d in s.index else None

    def snapshot(self, metric_id: str, d: date) -> Snapshot:
        m = catalog.get(metric_id)
        value = self.value(metric_id, d)
        prior = self.value(metric_id, d - timedelta(days=7))
        return Snapshot(m, d, value, prior, rag(m, value), rag(m, prior))

    def snapshots(self, d: date) -> list[Snapshot]:
        return [self.snapshot(m.id, d) for m in catalog.METRICS]

    def latest_data_date(self, metric_id: str) -> date | None:
        df = self.views[catalog.get(metric_id).view]
        return max(df["obs_date"]) if not df.empty else None

    def status_history(self, metric_id: str) -> pd.Series:
        m = catalog.get(metric_id)
        return self.series(metric_id).map(lambda v: rag(m, v))

    def days_in_status(self, metric_id: str, d: date) -> int:
        """How many consecutive days up to ``d`` the metric has had its current status."""
        hist = self.status_history(metric_id)
        hist = hist[hist.index <= d]
        current = hist.iloc[-1]
        n = 0
        for s in reversed(hist.tolist()):
            if s != current:
                break
            n += 1
        return n

    # --------------------------------------------------------- drill-down
    def rows(self, metric_id: str, d: date, filters: dict[str, str] | None = None) -> pd.DataFrame:
        m = catalog.get(metric_id)
        df = self.views[m.view]
        df = df[df["obs_date"] == d]
        for col, val in (filters or {}).items():
            df = df[df[col].astype(str) == str(val)]
        return df

    def breakdown(self, metric_id: str, dimension: str, d: date, compare_days: int = 7) -> pd.DataFrame:
        """Value by dimension member now and ``compare_days`` earlier, with each member's share of the change.

        For a sum metric, a member's contribution is simply its own change. For a ratio metric
        (numerator / denominator), the contribution is the change in the member's numerator divided by
        today's total denominator, which is how much of the headline move that member explains.
        """
        m = catalog.get(metric_id)
        if dimension not in m.dimensions:
            raise ValueError(f"{metric_id} cannot be broken down by {dimension!r}; use one of {m.dimensions}")
        prev_d = d - timedelta(days=compare_days)
        cur, prev = self.rows(metric_id, d), self.rows(metric_id, prev_d)
        members = sorted(set(cur[dimension].astype(str)) | set(prev[dimension].astype(str)))
        out = []
        den_now = float(pd.to_numeric(cur[m.denominator]).sum()) if m.denominator else None
        for member in members:
            c = cur[cur[dimension].astype(str) == member]
            p = prev[prev[dimension].astype(str) == member]
            vc, vp = self._value(m, c), self._value(m, p)
            if m.denominator:
                # Rate effect: the member's weight today x the change in its own rate. Volume (mix)
                # shifts between members are left out, so noise in volumes does not hide the driver.
                weight = float(pd.to_numeric(c[m.denominator]).sum()) / den_now if den_now else 0.0
                contribution = weight * (vc - vp) if not (np.isnan(vc) or np.isnan(vp)) else 0.0
            else:
                contribution = (0 if np.isnan(vc) else vc) - (0 if np.isnan(vp) else vp)
            out.append({"member": member, "value": vc, "prior": vp, "contribution": contribution,
                        "records": len(c)})
        df = pd.DataFrame(out, columns=["member", "value", "prior", "contribution", "records"])
        return df.sort_values("contribution", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)

    def records(self, metric_id: str, d: date, filters: dict[str, str] | None = None) -> pd.DataFrame:
        """The records that make up the metric on day ``d`` (only rows that count towards the numerator)."""
        m = catalog.get(metric_id)
        df = self.rows(metric_id, d, filters)
        if m.numerator in df.columns:
            df = df[pd.to_numeric(df[m.numerator], errors="coerce").fillna(0) != 0]
        cols = [c for c in m.record_columns if c in df.columns] or list(df.columns)
        sort_col = next((c for c in ("age_days", "amount_usd", "net_loss_usd", "rows_failed") if c in cols), None)
        if sort_col:  # ties broken by the record key so every engine lists records in the same order
            keys = [sort_col] + ([m.record_key] if m.record_key in df.columns else [])
            df = df.sort_values(keys, ascending=[False] + [True] * (len(keys) - 1))
        return df[cols].reset_index(drop=True)

    def composition(self, metric_id: str, dimension: str, d: date) -> pd.DataFrame:
        """Which members make up today's shortfall (for a level that is bad, rather than a move)."""
        m = catalog.get(metric_id)
        cur = self.rows(metric_id, d)
        if cur.empty:
            return pd.DataFrame(columns=["member", "shortfall", "share"])
        num = pd.to_numeric(cur[m.numerator], errors="coerce").fillna(0)
        if m.direction == "higher_is_better" and m.denominator:
            bad = pd.to_numeric(cur[m.denominator], errors="coerce").fillna(0) - num
        else:
            bad = num
        g = bad.groupby(cur[dimension].astype(str)).sum()
        g = g[g > 0].sort_values(ascending=False)
        total = float(g.sum())
        out = pd.DataFrame({"member": g.index, "shortfall": g.values})
        out["share"] = out["shortfall"] / total if total else 0.0
        return out.reset_index(drop=True)
