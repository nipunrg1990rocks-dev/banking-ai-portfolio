"""Banking operations risk domain (the brief's acceptance-demo dataset).

Grain: one row per reporting_month x region x business_line x process x severity x
root_cause cluster. ``transaction_volume`` and ``completed_on_time`` are recorded at the
process-month grain and *repeated* on every cluster row, so they are non-additive across
rows: every rate in this module de-duplicates them at ``VOLUME_GRAIN`` before summing.
Severity and root-cause filters narrow the exception numerator only; the volume
denominator stays at process-month grain (documented in the KPI dictionary).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .. import payload as P
from ..spec import month_label
from ..store import Dataset, content_hash
from ..validation import ValidationReport, check_columns, quarantine_rows, strip_formula_text
from . import ops_risk_schema as S
from .base import Context, Domain, SlideType, Template

FILTER_DIMS = {"region": "Region", "business_line": "Business line", "process": "Process", "severity": "Severity"}
VOLUME_DIMS = ["region", "business_line", "process"]


def mon(m: str) -> str:
    return month_label(m)


def band(days: float | None) -> str:
    if days is None or pd.isna(days):
        return "Unknown"
    for lo, hi, label in S.AGEING_BANDS:
        if lo <= days <= hi:
            return label
    return "Unknown"


class OpsRiskDomain(Domain):
    key = "ops_risk"
    label = "Operational risk & controls"
    prefix = "ops"
    tables = {"fact": S.ALLOWED_COLUMNS}
    filter_dimensions = FILTER_DIMS
    synonyms = [
        (r"\bapac\b|asia[- ]pacific", "region", "APAC"),
        (r"\bemea\b|europe", "region", "EMEA"),
        (r"\bamericas\b|\bamer\b|\bnorth america\b", "region", "Americas"),
        (r"\bretail( banking)?\b", "business_line", "Retail Banking"),
        (r"\bcorporate( banking)?\b", "business_line", "Corporate Banking"),
        (r"\bwealth( management)?\b", "business_line", "Wealth Management"),
        (r"\bmarkets( operations)?\b", "business_line", "Markets Operations"),
        (r"\bpayments?\b", "process", "Payments"),
        (r"\btrade settlements?\b|\bsettlements?\b", "process", "Trade Settlement"),
        (r"\bkyc\b|\bonboarding\b", "process", "KYC Onboarding"),
        (r"\breconciliations?\b|\brecs\b", "process", "Reconciliations"),
        (r"\bloan servicing\b|\blending\b", "process", "Loan Servicing"),
        (r"\bcritical\b", "severity", "Critical"),
        (r"\bhigh[- ]severity\b|\bhigh[- ]sev\b", "severity", "High"),
        (r"\bmedium[- ]severity\b", "severity", "Medium"),
        (r"\blow[- ]severity\b", "severity", "Low"),
    ]
    detect_keywords = [r"\brisk\b", r"exception", r"severity", r"\bapac\b", r"\bemea\b", r"americas", r"control",
                       r"remediation", r"ageing", r"aging", r"operational", r"\bops\b", r"loss"]
    templates = {
        "risk_controls": Template("risk_controls", "Risk & Controls pack",
                                  ["cover", "kpi_summary", "period_comparison", "exception_drivers", "severity_mix",
                                   "commentary", "ageing", "remediation", "high_severity", "loss", "data_quality",
                                   "appendix"], [r"\brisk\b", r"controls?"], "#7a1f2b"),
        "executive_summary": Template("executive_summary", "Executive Summary",
                                      ["cover", "kpi_summary", "trend", "exception_drivers", "remediation",
                                       "commentary", "period_comparison", "ageing", "data_quality", "appendix"],
                                      [r"exec(utive)? summary", r"board", r"summary"], "#13315c"),
        "operations_performance": Template("operations_performance", "Operations Performance",
                                           ["cover", "kpi_summary", "operations_performance", "trend", "ageing",
                                            "commentary", "remediation", "exception_drivers", "data_quality",
                                            "appendix"], [r"operations performance", r"\bsla\b", r"on[- ]time",
                                                          r"throughput", r"ops performance"], "#1f5f4a"),
    }
    slide_catalog = {
        "cover": SlideType("cover", "Cover with headline KPIs", [r"\bcover\b", r"title slide"], "Title and scope"),
        "kpi_summary": SlideType("kpi_summary", "KPI summary", [r"kpi", r"scorecard", r"headline"],
                                 "Six validated KPIs, current vs comparison"),
        "trend": SlideType("trend", "Monthly trend", [r"\btrend", r"over time", r"monthly"],
                           "Exceptions and exception rate by month"),
        "period_comparison": SlideType("period_comparison", "Period comparison by business line",
                                       [r"by business line", r"compare", r"comparison", r"\bvs\b"],
                                       "Exceptions by business line, current vs comparison"),
        "exception_drivers": SlideType("exception_drivers", "Top exception drivers",
                                       [r"drivers?", r"root[- ]cause", r"top (\d+ )?(issues|exceptions)", r"rank"],
                                       "Root causes ranked with change"),
        "severity_mix": SlideType("severity_mix", "Severity mix", [r"severity mix", r"by severity"],
                                  "Exceptions by severity by month"),
        "high_severity": SlideType("high_severity", "High-severity exceptions",
                                   [r"high[- ]severity exceptions?", r"high[- ]sev", r"critical exceptions?"],
                                   "Largest High/Critical clusters"),
        "ageing": SlideType("ageing", "Open exceptions by ageing", [r"\bage?ing\b", r"overdue", r"backlog"],
                            "Open exceptions by ageing band"),
        "remediation": SlideType("remediation", "Remediation status", [r"remediation", r"closure"],
                                 "Closure rate and open status"),
        "operations_performance": SlideType("operations_performance", "Operations performance",
                                            [r"on[- ]time", r"\bsla\b", r"throughput", r"volumes?"],
                                            "On-time completion and volume by process"),
        "loss": SlideType("loss", "Operational losses", [r"\bloss(es)?\b"], "Losses by business line"),
        "data_quality": SlideType("data_quality", "Data quality", [r"data quality", r"\bdq\b", r"completeness"],
                                  "Completeness, quarantine and freshness"),
        "commentary": SlideType("commentary", "Management commentary", [r"commentary", r"narrative",
                                r"conclusions?"], "Editable management commentary"),
        "appendix": SlideType("appendix", "Appendix: method and provenance", [r"appendix", r"methodology",
                              r"provenance"], "Definitions and lineage"),
    }
    examples = [
        "Create a six-slide APAC Q3 risk pack, compare Q2, rank top exception drivers, add commentary and "
        "speaker notes",
        "Executive summary for EMEA Payments Q3 2026 year over year, 7 slides",
        "Operations performance pack for Americas, last quarter, include ageing and data quality",
    ]
    followup_examples = ["Change APAC to EMEA and add high-severity exceptions", "Show only high severity",
                         "Add July", "Compare year over year", "Replace slide 4 with ageing",
                         "Make a fresh copy", "Refresh using latest data"]

    def expand(self, dim: str, value: str) -> list[str]:
        if dim == "severity" and value == "High":
            return list(S.HIGH_SEVERITIES)
        return [value]

    # ------------------------------------------------------------------ load + validate
    def load(self, raw: pd.DataFrame, source: str, synthetic: bool, registry, snapshot: str | None = None) -> Dataset:
        rep = ValidationReport(source)
        df = raw.dropna(how="all").copy()
        rep.rows_in = len(df)
        if not check_columns(df, S.REQUIRED_COLUMNS, S.ALLOWED_COLUMNS, rep):
            sha = content_hash({"x": pd.DataFrame({"s": [source]})})
            return Dataset(self.key, registry.next_version(self.prefix, sha), {"fact": pd.DataFrame(
                columns=S.ALLOWED_COLUMNS)}, [], rep, source, synthetic, sha)
        for c in S.OPTIONAL_COLUMNS:
            if c not in df.columns:
                df[c] = None
        df = df[S.ALLOWED_COLUMNS]
        hits = 0
        for c in ["record_id", "control_owner"] + list(S.CATEGORY_DOMAINS):
            df[c], n = strip_formula_text(df[c].astype("string").str.strip())
            hits += n
        if hits:
            rep.add("Formula-like text neutralised", "warning", hits, "stored as plain text")
        # Normalise case/spacing of known categories ("apac " -> "APAC"); unknown values are quarantined.
        normalised = 0
        for col, allowed in S.CATEGORY_DOMAINS.items():
            lut = {a.lower(): a for a in allowed}
            mapped = df[col].str.lower().map(lut)
            normalised += int(((df[col] != mapped) & mapped.notna()).sum())
            df[col] = mapped.fillna(df[col])
        rep.add("Category spelling normalised", "warning", normalised, "e.g. 'Apac' -> 'APAC'")
        q: list[pd.DataFrame] = []
        for col, allowed in S.CATEGORY_DOMAINS.items():
            df = quarantine_rows(df, ~df[col].isin(allowed), f"unexpected {col} value", q)
        df["reporting_month"] = df["reporting_month"].str.slice(0, 7)
        df = quarantine_rows(df, ~df["reporting_month"].str.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", na=False),
                             "invalid reporting_month", q)
        for c in ["transaction_volume", "completed_on_time", "exceptions", "ageing_days", "risk_score",
                  "loss_amount_inr"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        for c in ["transaction_volume", "completed_on_time", "exceptions"]:
            df = quarantine_rows(df, df[c].isna(), f"{c} missing or not numeric", q)
        for c, (lo, hi) in S.RANGES.items():
            df = quarantine_rows(df, df[c].notna() & ((df[c] < lo) | (df[c] > hi)), f"{c} out of range {lo}-{hi}", q)
        df = quarantine_rows(df, df["completed_on_time"] > df["transaction_volume"],
                             "completed_on_time exceeds transaction_volume", q)
        dup = df.duplicated(S.ROW_KEY, keep="first")
        df = quarantine_rows(df, dup, "duplicate of an earlier row (same month/region/line/process/severity/cause)", q)
        rep.add("Rows quarantined", "quarantine", sum(len(x) for x in q), "excluded from every figure")
        vol = df.groupby(S.VOLUME_GRAIN)[["transaction_volume", "completed_on_time"]].nunique()
        inconsistent = int((vol > 1).any(axis=1).sum())
        rep.add("Inconsistent repeated volume within a process-month", "warning", inconsistent,
                "first value used; volume is non-additive and de-duplicated per process-month")
        nulls = int(df[S.OPTIONAL_COLUMNS].isna().sum().sum())
        rep.add("Missing optional values", "warning", nulls, "ageing/risk/loss blanks shown as 'Unknown' / excluded")
        for c in ["transaction_volume", "completed_on_time", "exceptions"]:
            df[c] = df[c].astype("int64")
        df = df.sort_values(S.ROW_KEY).reset_index(drop=True)
        rep.rows_accepted = len(df)
        rep.quarantine = pd.concat(q) if q else None
        if not len(df):
            rep.add("Usable rows", "error", 1, "no rows passed validation")
        tables = {"fact": df}
        sha = content_hash(tables)
        months = sorted(df["reporting_month"].unique().tolist())
        return Dataset(self.key, registry.next_version(self.prefix, sha), tables, months, rep, source, synthetic,
                       sha, dim_table="fact")

    def kpi_dictionary(self) -> pd.DataFrame:
        rows = [
            ("exception_rate", "Exception rate", "sum(exceptions) / sum(transaction_volume de-duplicated at "
             "process-month)", "Process-month", "Ratio of sums; n/a when volume = 0. Severity filter narrows the "
             "numerator only"),
            ("on_time_pct", "On-time completion %", "sum(completed_on_time) / sum(transaction_volume), both "
             "de-duplicated at process-month", "Process-month", "Ratio of sums; unaffected by severity filter"),
            ("high_severity_exceptions", "High-severity exceptions", "sum(exceptions) where severity in High, "
             "Critical", "Cluster row", "Additive"),
            ("open_exceptions", "Open exceptions", "sum(exceptions) where remediation_status != Closed, in the "
             "latest month of the period", "Cluster row", "Point in time (latest month), not summed over months"),
            ("ageing_band", "Open exceptions by ageing band", "Open exceptions grouped 0-30 / 31-60 / 61-90 / 90+ "
             "days", "Cluster row", "Unknown band shown when ageing_days is blank"),
            ("closure_rate", "Remediation closure rate", "Closed exceptions / all exceptions in the period",
             "Cluster row", "Ratio of sums"),
            ("mom_movement", "Month-over-month movement", "Exception rate latest month minus prior month",
             "Month", "Difference in percentage points"),
            ("dq_completeness", "Data quality completeness", "Non-blank optional cells / all optional cells; "
             "plus share of rows flagged Pass", "Cluster row", "Ratio of sums"),
            ("loss_inr", "Operational loss (INR)", "sum(loss_amount_inr)", "Cluster row", "Additive; blanks "
             "excluded and disclosed"),
        ]
        return pd.DataFrame(rows, columns=["metric_id", "name", "definition", "grain", "aggregation_rule"])

    # ------------------------------------------------------------------ metrics
    def frame(self, ctx: Context, months: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(cluster rows with all filters, volumes de-duplicated with dimension filters only)."""
        key = "f:" + ",".join(months)

        def build() -> tuple[pd.DataFrame, pd.DataFrame]:
            rows = ctx.query("fact", S.ALLOWED_COLUMNS, ctx.filters, months)
            vol_filters = {k: v for k, v in ctx.filters.items() if k in VOLUME_DIMS}
            v = ctx.query("fact", S.VOLUME_GRAIN + ["transaction_volume", "completed_on_time"], vol_filters, months)
            v = v.drop_duplicates(S.VOLUME_GRAIN, keep="first")
            return rows, v
        return ctx.memo(key, build)

    def metrics(self, ctx: Context, months: list[str]) -> dict[str, Any]:
        if not months:
            return {k: None for k in ["exception_rate", "on_time_pct", "exceptions", "high_sev", "open", "closure_rate",
                                      "loss", "volume", "dq_completeness", "dq_pass", "mom"]}
        rows, vol = self.frame(ctx, months)
        exc = float(rows["exceptions"].sum())
        volume = float(vol["transaction_volume"].sum())
        latest = max(months)
        last_rows = rows[rows["reporting_month"] == latest]
        open_ = float(last_rows.loc[last_rows["remediation_status"] != "Closed", "exceptions"].sum())
        closed = float(rows.loc[rows["remediation_status"] == "Closed", "exceptions"].sum())
        opt = rows[S.OPTIONAL_COLUMNS]
        out = {
            "exceptions": exc, "volume": volume,
            "exception_rate": P.safe_div(exc, volume),
            "on_time_pct": P.safe_div(float(vol["completed_on_time"].sum()), volume),
            "high_sev": float(rows.loc[rows["severity"].isin(S.HIGH_SEVERITIES), "exceptions"].sum()),
            "open": open_, "closure_rate": P.safe_div(closed, exc),
            "loss": float(rows["loss_amount_inr"].fillna(0).sum()),
            "dq_completeness": P.safe_div(float(opt.notna().sum().sum()), float(opt.size)) if opt.size else None,
            "dq_pass": P.safe_div(float((rows["data_quality_status"] == "Pass").sum()), float(len(rows))),
        }
        ms = sorted(months)
        if len(ms) >= 2:
            a = self.monthly(ctx, ms)
            out["mom"] = P.change(a.iloc[-1]["exception_rate"], a.iloc[-2]["exception_rate"])
        else:
            out["mom"] = None
        return out

    def monthly(self, ctx: Context, months: list[str]) -> pd.DataFrame:
        rows, vol = self.frame(ctx, months)
        e = rows.groupby("reporting_month")["exceptions"].sum()
        v = vol.groupby("reporting_month")[["transaction_volume", "completed_on_time"]].sum()
        df = pd.DataFrame({"exceptions": e}).join(v, how="outer").reindex(sorted(months)).fillna(0)
        df["exception_rate"] = [P.safe_div(a, b) for a, b in zip(df["exceptions"], df["transaction_volume"],
                                                                strict=True)]
        df["on_time_pct"] = [P.safe_div(a, b) for a, b in zip(df["completed_on_time"], df["transaction_volume"],
                                                             strict=True)]
        return df

    def _fl(self, ctx: Context) -> str:
        return ctx.filters_label(FILTER_DIMS)

    def _src(self, ctx: Context) -> str:
        ds = ctx.dataset
        kind = "Synthetic dataset" if ds.synthetic else "Uploaded dataset"
        return (f"{kind} {ds.version} (data to {mon(ds.months[-1])}). Exception rate = exceptions / transaction volume"
                " de-duplicated per process-month.")

    def _window(self, ctx: Context) -> str:
        return f"{ctx.period} vs {ctx.cmp_period}" if ctx.cmp_months else ctx.period

    # ------------------------------------------------------------------ slides
    def build_slide(self, slide_type: str, ctx: Context, no: int, total: int) -> dict[str, Any]:
        return getattr(self, f"_s_{slide_type}")(ctx)

    def _cur_cmp(self, ctx: Context) -> tuple[dict[str, Any], dict[str, Any]]:
        return ctx.memo("m", lambda: (self.metrics(ctx, ctx.months), self.metrics(ctx, ctx.cmp_months)))

    def _s_cover(self, ctx: Context) -> dict[str, Any]:
        cur, cmp_ = self._cur_cmp(ctx)
        fl = self._fl(ctx)
        d_rate = P.change(cur["exception_rate"], cmp_["exception_rate"])
        items = [
            P.kpi("Exception rate", P.fmt_pct(cur["exception_rate"], 2),
                  f"{P.fmt_pp(d_rate, 2)} vs {ctx.cmp_period}" if ctx.cmp_months else ctx.period,
                  P.rag(d_rate, True, 0.0002)),
            P.kpi("High-severity exceptions", P.fmt_int(cur["high_sev"]),
                  f"{P.fmt_int(cmp_['high_sev'])} in {ctx.cmp_period}" if ctx.cmp_months else "High + Critical",
                  P.rag(P.change(cur["high_sev"], cmp_["high_sev"]), True)),
            P.kpi("On-time completion", P.fmt_pct(cur["on_time_pct"], 1), "of transaction volume",
                  P.rag(P.change(cur["on_time_pct"], cmp_["on_time_pct"]), False, 0.001)),
        ]
        com = [P.fact(f"Exception rate {P.fmt_pct(cur['exception_rate'], 2)} in {ctx.period}"
                      + (f" vs {P.fmt_pct(cmp_['exception_rate'], 2)} in {ctx.cmp_period}." if ctx.cmp_months else "."),
                      "exception_rate", cur["exception_rate"], self._window(ctx), fl)]
        return P.slide("cover", ctx.spec.title, f"{ctx.period} · {fl} · data to {mon(ctx.dataset.months[-1])}",
                       "cover", [P.kpis(items)], com, self._src(ctx))

    def _s_kpi_summary(self, ctx: Context) -> dict[str, Any]:
        cur, cmp_ = self._cur_cmp(ctx)
        fl = self._fl(ctx)
        spec_rows = [
            ("Exception rate", "exception_rate", lambda v: P.fmt_pct(v, 2), True, "pp"),
            ("On-time completion %", "on_time_pct", lambda v: P.fmt_pct(v, 1), False, "pp"),
            ("Exceptions", "exceptions", P.fmt_int, True, "n"),
            ("High-severity exceptions", "high_sev", P.fmt_int, True, "n"),
            (f"Open exceptions ({mon(max(ctx.months))})", "open", P.fmt_int, True, "n"),
            ("Remediation closure rate", "closure_rate", lambda v: P.fmt_pct(v, 1), False, "pp"),
            ("Operational loss", "loss", P.fmt_money, True, "n"),
            ("Data quality completeness", "dq_completeness", lambda v: P.fmt_pct(v, 1), False, "pp"),
        ]
        rows = []
        for label, k, f, worse_up, kind in spec_rows:
            d = P.change(cur[k], cmp_[k])
            dtxt = "n/a" if d is None else (P.fmt_pp(d, 2) if kind == "pp" else f"{d:+,.0f}" if k != "loss"
                                            else P.fmt_money(d))
            tol = 0.0005 if kind == "pp" else 0.0
            rows.append([label, f(cur[k]), f(cmp_[k]) if ctx.cmp_months else "n/a", dtxt,
                         {"red": "Worse", "green": "Better", "amber": "Flat", "grey": "n/a"}[P.rag(d, worse_up, tol)]])
        tbl = P.table(f"KPI scorecard: {ctx.period} vs {ctx.cmp_period}", ["KPI", ctx.period, ctx.cmp_period, "Change",
                                                                         "Direction"], rows,
                      col_widths=[3.0, 1.4, 1.4, 1.2, 1.2])
        com = []
        d_rate = P.change(cur["exception_rate"], cmp_["exception_rate"])
        if d_rate is not None:
            verb = "was broadly flat moving" if abs(d_rate) < 0.00005 else ("rose" if d_rate > 0 else "fell")
            com.append(P.fact(f"Exception rate {verb} from "
                              f"{P.fmt_pct(cmp_['exception_rate'], 2)} to {P.fmt_pct(cur['exception_rate'], 2)} "
                              f"({P.fmt_pp(d_rate, 2)}).", "exception_rate", cur["exception_rate"],
                              self._window(ctx), fl))
        d_h = P.change(cur["high_sev"], cmp_["high_sev"])
        com.append(P.fact(f"High-severity exceptions: {P.fmt_int(cur['high_sev'])}"
                          + (f" ({d_h:+,.0f} vs {ctx.cmp_period})." if d_h is not None else "."),
                          "high_severity_exceptions",
                          cur["high_sev"], self._window(ctx), fl))
        com.append(P.fact(f"Closure rate {P.fmt_pct(cur['closure_rate'], 1)}; {P.fmt_int(cur['open'])} exceptions open "
                          f"at {mon(max(ctx.months))}.", "closure_rate", cur["closure_rate"], ctx.period, fl))
        if cur["mom"] is not None:
            com.append(P.fact(f"Month-over-month exception rate movement into {mon(max(ctx.months))}: "
                              f"{P.fmt_pp(cur['mom'], 2)}.", "mom_movement", cur["mom"], ctx.period, fl))
        return P.slide("kpi_summary", "KPI summary", f"{ctx.period} vs {ctx.cmp_period} · {fl}", "table+comm", [tbl],
                       com, self._src(ctx))

    def _s_trend(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        allm = ctx.dataset.months
        end = allm.index(max(ctx.months))
        window = allm[max(0, end - 11): end + 1]
        mth = self.monthly(ctx, window)
        ch1 = P.chart("column", "Exceptions by month", [mon(m) for m in window],
                      [{"name": "Exceptions", "values": mth["exceptions"].tolist()}], highlight=[mon(m) for m in
                                                                                                  ctx.months])
        ch2 = P.chart("line", "Exception rate by month", [mon(m) for m in window],
                      [{"name": "Exception rate", "values": mth["exception_rate"].tolist()}], number_format="0.00%")
        peak = mth["exceptions"].idxmax()
        com = [P.fact(f"Peak month in the trailing window: {mon(peak)} with {mth.loc[peak, 'exceptions']:,.0f} "
                      f"exceptions ({P.fmt_pct(mth.loc[peak, 'exception_rate'], 2)}).", "exceptions",
                      mth.loc[peak, "exceptions"], f"{mon(window[0])}–{mon(window[-1])}", fl)]
        return P.slide("trend", "Monthly trend", f"Trailing {len(window)} months to {mon(window[-1])} · {fl}",
                       "2charts", [ch1, ch2], com, self._src(ctx))

    def _by(self, ctx: Context, months: list[str], dim: str) -> pd.Series:
        if not months:
            return pd.Series(dtype=float)
        rows, _ = self.frame(ctx, months)
        return rows.groupby(dim)["exceptions"].sum()

    def _s_period_comparison(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        cur = self._by(ctx, ctx.months, "business_line")
        cmp_ = self._by(ctx, ctx.cmp_months, "business_line")
        lines = [b for b in S.BUSINESS_LINES if b in cur.index or b in cmp_.index]
        series = [{"name": ctx.period, "values": [float(cur.get(b, 0)) for b in lines]}]
        if ctx.cmp_months:
            series.insert(0, {"name": ctx.cmp_period, "values": [float(cmp_.get(b, 0)) for b in lines]})
        ch = P.chart("column", f"Exceptions by business line: {ctx.period} vs {ctx.cmp_period}", lines, series,
                     data_labels=True)
        com = []
        if ctx.cmp_months and lines:
            d = {b: float(cur.get(b, 0) - cmp_.get(b, 0)) for b in lines}
            up = max(d, key=d.get)
            com.append(P.fact(f"{up} moved most: {cmp_.get(up, 0):,.0f} → {cur.get(up, 0):,.0f} ({d[up]:+,.0f}).",
                              "exceptions", float(cur.get(up, 0)), self._window(ctx), fl))
        if lines:
            top = cur.idxmax()
            com.append(P.fact(f"{top} has the most exceptions in {ctx.period} ({cur.max():,.0f}, "
                              f"{P.fmt_pct(cur.max() / cur.sum(), 0)} of total).", "exceptions", float(cur.max()),
                              ctx.period, fl))
        return P.slide("period_comparison", f"{ctx.period} vs {ctx.cmp_period} by business line", fl, "chart+comm",
                       [ch], com, self._src(ctx))

    def _s_exception_drivers(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        n = ctx.spec.top_n
        cur = self._by(ctx, ctx.months, "root_cause").sort_values(ascending=False)
        cmp_ = self._by(ctx, ctx.cmp_months, "root_cause")
        top = cur.head(n)
        ch = P.chart("bar", f"Top {len(top)} exception drivers, {ctx.period}", list(top.index)[::-1],
                     [{"name": "Exceptions", "values": top.tolist()[::-1]}], data_labels=True)
        total = cur.sum()
        rows = [[i + 1, rc, f"{v:,.0f}", P.fmt_pct(P.safe_div(v, total), 0),
                 f"{v - cmp_.get(rc, 0):+,.0f}" if ctx.cmp_months else "n/a"] for i, (rc, v) in enumerate(top.items())]
        tbl = P.table("Ranked drivers", ["#", "Root cause", "Exceptions", "Share", f"vs {ctx.cmp_period}"], rows,
                      col_widths=[0.4, 2.4, 1.1, 0.8, 1.3])
        com = []
        for rc, v in list(top.items())[:3]:
            com.append(P.fact(f"{rc}: {v:,.0f} exceptions ({P.fmt_pct(P.safe_div(v, total), 0)} of total)"
                              + (f", {v - cmp_.get(rc, 0):+,.0f} vs {ctx.cmp_period}." if ctx.cmp_months else "."),
                              "exceptions", float(v), self._window(ctx), fl))
        com.append(P.fact("Root cause is the category recorded by control owners; it ranks where exceptions were "
                          "logged, not a proven causal explanation.", "exceptions", None, ctx.period, fl,
                          kind="hypothesis"))
        return P.slide("exception_drivers", f"Top {len(top)} exception drivers", f"Ranked by exceptions · "
                       f"{ctx.period} · {fl}", "chart+table", [ch, tbl], com, self._src(ctx))

    def _s_severity_mix(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        months = sorted(set(ctx.cmp_months + ctx.months))
        rows, _ = self.frame(ctx, months)
        pv = rows.pivot_table(index="reporting_month", columns="severity", values="exceptions",
                              aggfunc="sum").reindex(months).fillna(0)
        sev = [s for s in S.SEVERITIES if s in pv.columns]
        ch = P.chart("column_stacked", "Exceptions by severity and month", [mon(m) for m in months],
                     [{"name": s, "values": pv[s].tolist()} for s in sev])
        cur_hs = pv.loc[ctx.months, [s for s in sev if s in S.HIGH_SEVERITIES]].sum().sum()
        cur_all = pv.loc[ctx.months].sum().sum()
        com = [P.fact(f"High + Critical are {P.fmt_pct(P.safe_div(cur_hs, cur_all), 1)} of exceptions in "
                      f"{ctx.period} ({cur_hs:,.0f} of {cur_all:,.0f}).", "high_severity_exceptions", float(cur_hs),
                      ctx.period, fl)]
        if ctx.cmp_months:
            c_hs = pv.loc[ctx.cmp_months, [s for s in sev if s in S.HIGH_SEVERITIES]].sum().sum()
            com.append(P.fact(f"That compares with {c_hs:,.0f} in {ctx.cmp_period}.", "high_severity_exceptions",
                              float(c_hs), ctx.cmp_period, fl))
        return P.slide("severity_mix", "Severity mix", fl, "chart+comm", [ch], com, self._src(ctx))

    def _s_high_severity(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        rows, _ = self.frame(ctx, ctx.months)
        hs = rows[rows["severity"].isin(S.HIGH_SEVERITIES)]
        g = hs.groupby(["region", "business_line", "process", "root_cause"]).agg(
            exceptions=("exceptions", "sum"), loss=("loss_amount_inr", "sum"), age=("ageing_days", "max"),
            open=("remediation_status", lambda s: int((s != "Closed").sum()))).sort_values("exceptions",
                                                                                            ascending=False)
        top = g.head(ctx.spec.top_n)
        trs = [[r, b, p, rc, f"{v.exceptions:,.0f}", P.fmt_money(v.loss), "n/a" if pd.isna(v.age) else int(v.age),
                "Yes" if v.open else "No"] for (r, b, p, rc), v in top.iterrows()]
        tbl = P.table(f"Largest High/Critical clusters, {ctx.period}", ["Region", "Business line", "Process",
                      "Root cause", "Exc.", "Loss", "Max age (d)", "Open"], trs,
                      col_widths=[0.9, 1.6, 1.4, 2.0, 0.7, 1.1, 1.0, 0.6])
        by_bl = hs.groupby("business_line")["exceptions"].sum().reindex(S.BUSINESS_LINES).dropna()
        ch = P.chart("bar", "High/Critical exceptions by business line", list(by_bl.index),
                     [{"name": "High + Critical", "values": by_bl.tolist()}], data_labels=True)
        com = []
        if len(top):
            (r, b, p, rc), v = next(iter(top.iterrows()))
            com.append(P.fact(f"Largest cluster: {r} {b} {p}, '{rc}', {v.exceptions:,.0f} High/Critical exceptions.",
                              "high_severity_exceptions", float(v.exceptions), ctx.period, fl))
        com.append(P.fact(f"{hs['exceptions'].sum():,.0f} High/Critical exceptions in total; "
                          f"{P.fmt_money(hs['loss_amount_inr'].sum())} of losses booked against them.",
                          "high_severity_exceptions", float(hs["exceptions"].sum()), ctx.period, fl))
        return P.slide("high_severity", "High-severity exceptions", f"High and Critical only · {ctx.period} · {fl}",
                       "table+chart", [tbl, ch], com, self._src(ctx))

    def _s_ageing(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        latest = max(ctx.months)
        rows, _ = self.frame(ctx, ctx.months)
        op = rows[(rows["reporting_month"] == latest) & (rows["remediation_status"] != "Closed")].copy()
        op["band"] = op["ageing_days"].map(band)
        order = [b for *_, b in S.AGEING_BANDS] + ["Unknown"]
        b = op.groupby("band")["exceptions"].sum().reindex(order).fillna(0)
        if b.get("Unknown", 0) == 0:
            b = b.drop("Unknown")
        ch = P.chart("column", f"Open exceptions by ageing band at {mon(latest)}", list(b.index),
                     [{"name": "Open exceptions", "values": b.tolist()}], data_labels=True)
        pv = op.pivot_table(index="business_line", columns="band", values="exceptions", aggfunc="sum").reindex(
            columns=list(b.index)).fillna(0)
        trs = [[bl] + [f"{v:,.0f}" for v in r] for bl, r in pv.iterrows()]
        tbl = P.table("Open exceptions: business line x ageing band", ["Business line"] + list(b.index), trs)
        over90 = float(b.get("90+ days", 0))
        com = [P.fact(f"{b.sum():,.0f} exceptions open at {mon(latest)}; {over90:,.0f} "
                      f"({P.fmt_pct(P.safe_div(over90, b.sum()), 0)}) are older than 90 days.", "ageing_band", over90,
                      mon(latest), fl)]
        if b.get("Unknown", 0):
            com.append(P.fact(f"{b['Unknown']:,.0f} open exceptions have no ageing recorded and are shown as "
                              "Unknown.", "dq_completeness", float(b["Unknown"]), mon(latest), fl, kind="caveat"))
        return P.slide("ageing", "Open exceptions by ageing", f"Point in time: {mon(latest)} · {fl}", "chart+table",
                       [ch, tbl], com, self._src(ctx) + " Open = remediation status Open or In Progress.")

    def _s_remediation(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        rows, _ = self.frame(ctx, ctx.months)
        st = rows.groupby("remediation_status")["exceptions"].sum().reindex(S.REMEDIATION_STATUSES).fillna(0)
        ch1 = P.chart("doughnut", f"Remediation status, {ctx.period}", list(st.index),
                      [{"name": "Exceptions", "values": st.tolist()}])
        g = rows.groupby("business_line").apply(
            lambda x: P.safe_div(x.loc[x["remediation_status"] == "Closed", "exceptions"].sum(), x["exceptions"].sum()),
            include_groups=False).reindex(S.BUSINESS_LINES).dropna()
        ch2 = P.chart("bar", "Closure rate by business line", list(g.index), [{"name": "Closure rate",
                      "values": g.tolist()}], number_format="0%", data_labels=True)
        com = [P.fact(f"Closure rate {P.fmt_pct(P.safe_div(st['Closed'], st.sum()), 1)} ({st['Closed']:,.0f} of "
                      f"{st.sum():,.0f}).", "closure_rate", P.safe_div(st["Closed"], st.sum()), ctx.period, fl)]
        if len(g):
            com.append(P.fact(f"Lowest closure rate: {g.idxmin()} at {P.fmt_pct(g.min(), 0)}.", "closure_rate",
                              g.min(), ctx.period, fl))
        return P.slide("remediation", "Remediation progress", f"{ctx.period} · {fl}", "2charts", [ch1, ch2], com,
                       self._src(ctx))

    def _s_operations_performance(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        _, vol = self.frame(ctx, ctx.months)
        g = vol.groupby("process")[["transaction_volume", "completed_on_time"]].sum()
        g["on_time"] = g["completed_on_time"] / g["transaction_volume"].where(g["transaction_volume"] > 0)
        g = g.sort_values("on_time")
        ch = P.chart("bar", f"On-time completion by process, {ctx.period}", list(g.index),
                     [{"name": "On-time %", "values": g["on_time"].tolist()}], number_format="0.0%", data_labels=True)
        trs = [[p, f"{r.transaction_volume:,.0f}", f"{r.completed_on_time:,.0f}", P.fmt_pct(r.on_time, 1)]
               for p, r in g.iterrows()]
        tbl = P.table("Volume and SLA by process", ["Process", "Volume", "On time", "On-time %"], trs)
        com = []
        if len(g):
            com.append(P.fact(f"Weakest process: {g.index[0]} at {P.fmt_pct(g['on_time'].iloc[0], 1)} on time.",
                              "on_time_pct", g["on_time"].iloc[0], ctx.period, fl))
            com.append(P.fact(f"Total volume {g['transaction_volume'].sum():,.0f} transactions.", "volume",
                              float(g["transaction_volume"].sum()), ctx.period, fl))
        if "severity" in ctx.filters:
            com.append(P.fact("Severity filter does not apply to volumes or on-time completion.", "on_time_pct", None,
                              ctx.period, fl, kind="caveat"))
        return P.slide("operations_performance", "Operations performance", f"{ctx.period} · {fl}", "chart+table",
                       [ch, tbl], com, self._src(ctx))

    def _s_loss(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        rows, _ = self.frame(ctx, ctx.months)
        g = rows.groupby("business_line")["loss_amount_inr"].sum().reindex(S.BUSINESS_LINES).fillna(0)
        ch = P.chart("bar", f"Operational loss by business line, {ctx.period} (INR)", list(g.index),
                     [{"name": "Loss (INR)", "values": g.tolist()}], data_labels=True)
        rc = rows.groupby("root_cause")["loss_amount_inr"].sum().sort_values(ascending=False).head(5)
        tbl = P.table("Top root causes by loss", ["Root cause", "Loss"], [[k, P.fmt_money(v)] for k, v in rc.items()])
        missing = int(rows["loss_amount_inr"].isna().sum())
        com = [P.fact(f"Total loss {P.fmt_money(g.sum())}; largest in {g.idxmax()} ({P.fmt_money(g.max())}).",
                      "loss_inr",
                      float(g.sum()), ctx.period, fl)]
        if missing:
            com.append(P.fact(f"{missing} rows have no loss amount and are excluded.", "loss_inr", missing, ctx.period,
                              fl, kind="caveat"))
        return P.slide("loss", "Operational losses", f"{ctx.period} · {fl}", "chart+table", [ch, tbl], com,
                       self._src(ctx))

    def _s_data_quality(self, ctx: Context) -> dict[str, Any]:
        cur, _ = self._cur_cmp(ctx)
        rep = ctx.dataset.report
        rows = [[c.name, c.level.upper(), c.count, c.detail] for c in rep.checks]
        tbl = P.table("Validation checks", ["Check", "Result", "Count", "Note"], rows, col_widths=[3.0, 1.1, 0.8, 3.3])
        com = [P.fact(f"Optional-field completeness {P.fmt_pct(cur['dq_completeness'], 1)}; "
                      f"{P.fmt_pct(cur['dq_pass'], 1)} of rows flagged Pass by source systems.", "dq_completeness",
                      cur["dq_completeness"], ctx.period, self._fl(ctx)),
               P.fact(f"{rep.quarantined} rows quarantined at validation; data runs to {mon(ctx.dataset.months[-1])}.",
                      "dq_completeness", rep.quarantined, ctx.dataset.version, "whole dataset")]
        return P.slide("data_quality", "Data quality and freshness", f"Dataset {ctx.dataset.version}", "table+comm",
                       [tbl], com, f"SHA-256 {ctx.dataset.sha256[:12]}…")

    def _s_commentary(self, ctx: Context) -> dict[str, Any]:
        fl = self._fl(ctx)
        com = []
        for st in ("kpi_summary", "exception_drivers"):
            com += self.build_slide(st, ctx, 0, 0)["commentary"][:2]
        for d in ctx.disclosures[:2]:
            com.append(P.fact(d, "data_quality", None, ctx.dataset.version, fl, kind="caveat"))
        return P.slide("commentary", "Management commentary", f"{self._window(ctx)} · {fl}", "commentary", [], com,
                       self._src(ctx) + " Draft generated from computed metrics; requires human review before "
                       "distribution.")

    def _s_appendix(self, ctx: Context) -> dict[str, Any]:
        ds = ctx.dataset
        cards_ = [
            {"head": "Data", "body": f"{ds.source} ({ds.version}), {len(ds.tables['fact']):,} rows, "
             f"{mon(ds.months[0])}–{mon(ds.months[-1])}; {ds.report.quarantined} rows quarantined."},
            {"head": "KPI rules", "body": "Rates are ratios of sums. Volume and on-time counts are de-duplicated per "
             "process-month before summing; severity filters narrow exceptions only; divide-by-zero shows n/a."},
            {"head": "Provenance", "body": f"Filters: {self._fl(ctx)}. Period {self._window(ctx)}. Query fingerprints: "
             f"{', '.join(q.fingerprint for q in ctx.queries[:4])}."},
        ]
        return P.slide("appendix", "Appendix: method and provenance", "Definitions and lineage", "cards",
                       [P.cards(cards_)], [], self._src(ctx))


# ---------------------------------------------------------------------- synthetic dataset
def synthetic_ops_risk(seed: int = 11, start: str = "2025-01", end: str = "2026-09",
                       anomalies: bool = True) -> pd.DataFrame:
    """Fictional operations-risk data with documented seeded anomalies (see docs/data_dictionary.md)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    months = pd.period_range(start, end, freq="M").strftime("%Y-%m").tolist()
    base_vol = {"Payments": 180_000, "Trade Settlement": 60_000, "KYC Onboarding": 9_000, "Reconciliations": 40_000,
                "Loan Servicing": 25_000}
    bl_mult = {"Retail Banking": 1.4, "Corporate Banking": 1.0, "Wealth Management": 0.4, "Markets Operations": 0.8}
    reg_mult = {"APAC": 1.0, "EMEA": 1.15, "Americas": 0.9}
    base_rate = {"Payments": 0.0035, "Trade Settlement": 0.006, "KYC Onboarding": 0.02, "Reconciliations": 0.005,
                 "Loan Servicing": 0.004}
    sev_p = [0.55, 0.28, 0.13, 0.04]
    rc_p = np.array([0.24, 0.16, 0.08, 0.15, 0.12, 0.09, 0.10, 0.06])
    rows = []
    rid = 0
    for mi, m in enumerate(months):
        season = 1 + 0.08 * np.sin(mi / 12 * 2 * np.pi)
        for r in S.REGIONS:
            for b in S.BUSINESS_LINES:
                for p in S.PROCESSES:
                    vol = int(base_vol[p] * bl_mult[b] * reg_mult[r] * season * rng.uniform(0.92, 1.08))
                    rate = base_rate[p] * rng.uniform(0.8, 1.2) * (1 + 0.004 * mi)
                    on_time = int(vol * min(0.995, rng.uniform(0.955, 0.99)))
                    sev_mult = [1, 1, 1, 1]
                    rc = rc_p.copy()
                    if anomalies and r == "APAC" and p == "Payments" and m in ("2026-07", "2026-08"):
                        rate *= 2.3
                        sev_mult = [1, 1.2, 2.5, 3.0]
                        rc = rc_p.copy()
                        rc[2] *= 6
                        on_time = int(vol * 0.93)
                    if anomalies and r == "APAC" and m >= "2026-07" and p == "Reconciliations":
                        rate *= 1.4
                        rc[3] *= 2.5
                    if anomalies and r == "Americas" and p == "KYC Onboarding" and m >= "2026-06":
                        rc[1] *= 2
                    rc = rc / rc.sum()
                    exc_total = rng.poisson(vol * rate)
                    for si, sev in enumerate(S.SEVERITIES):
                        n_sev = int(exc_total * sev_p[si] * sev_mult[si])
                        k = 1 if n_sev < 15 else 2
                        causes = rng.choice(S.ROOT_CAUSES, size=k, replace=False, p=rc)
                        splits = [n_sev] if k == 1 else [n_sev - n_sev // 3, n_sev // 3]
                        for cause, n in zip(causes, splits, strict=True):
                            age_base = {"Low": 12, "Medium": 25, "High": 40, "Critical": 55}[sev]
                            if anomalies and r == "Americas" and p == "KYC Onboarding" and m >= "2026-06":
                                age_base *= 2.4
                            recency = len(months) - mi
                            closed_p = min(0.97, 0.35 + 0.2 * recency)
                            status = "Closed" if rng.random() < closed_p else rng.choice(["Open", "In Progress"])
                            loss = 0.0
                            if sev in ("High", "Critical") and n:
                                loss = float(round(n * rng.gamma(2, 9_000 if sev == "High" else 30_000), 0))
                            if anomalies and r == "EMEA" and b == "Markets Operations" and p == "Trade Settlement" \
                                    and m == "2026-09" and sev == "Critical":
                                loss = 48_500_000.0
                            rid += 1
                            rows.append({
                                "record_id": f"EXC-{rid:06d}", "reporting_month": m, "region": r, "business_line": b,
                                "process": p, "transaction_volume": vol, "completed_on_time": on_time,
                                "exceptions": int(n), "severity": sev, "root_cause": str(cause),
                                "ageing_days": int(max(0, rng.normal(age_base + recency * 1.5, 8)
                                                       + (rng.exponential(35) if status != "Closed" else 0))),
                                "control_owner": f"{r} {p} Control Lead", "risk_score": round(float(np.clip(
                                    rng.normal({"Low": 22, "Medium": 41, "High": 66, "Critical": 84}[sev], 7), 1,
                                    100)), 1),
                                "loss_amount_inr": loss, "remediation_status": status,
                                "data_quality_status": rng.choice(S.DQ_STATUSES, p=[0.93, 0.05, 0.02]),
                            })
    df = pd.DataFrame(rows)
    if anomalies:
        # Seeded data-quality defects the validation gate must catch (documented in the data dictionary).
        df = pd.concat([df, df.iloc[[100, 2000, 4000]]], ignore_index=True)       # 3 exact duplicates
        df.loc[[10, 20], "ageing_days"] = None                                     # 2 missing ageing values
        df.loc[30, "completed_on_time"] = df.loc[30, "transaction_volume"] + 500  # on-time > volume
        df.loc[40, "region"] = "Apac"                                              # fixable spelling
        df.loc[50, "region"] = "LATAM"                                             # unknown category
        df.loc[60, "loss_amount_inr"] = -2500.0                                    # negative loss
        df.loc[70, "risk_score"] = 140.0                                           # out of range
    return df


def refresh_variant(base: pd.DataFrame, seed: int = 99) -> pd.DataFrame:
    """A 'modified dataset' for the refresh demo: adds Oct 2026, restates Sep 2026 and plants 2 bad rows."""
    oct_ = synthetic_ops_risk(seed=seed, start="2025-10", end="2025-10", anomalies=False)
    oct_["reporting_month"] = "2026-10"
    oct_["record_id"] = [f"EXC-R{i:06d}" for i in range(len(oct_))]
    df = pd.concat([base, oct_], ignore_index=True)
    sep = (df["reporting_month"] == "2026-09") & (df["region"] == "APAC") & (df["process"] == "Payments")
    df.loc[sep & (df["severity"] == "Low"), "exceptions"] = (df.loc[sep & (df["severity"] == "Low"), "exceptions"]
                                                            * 1.25).round().astype(int)
    bad = df.iloc[[5, 6]].copy()
    bad["reporting_month"] = "2026-13"
    bad.loc[bad.index[1], "severity"] = "Catastrophic"
    return pd.concat([df, bad], ignore_index=True)
