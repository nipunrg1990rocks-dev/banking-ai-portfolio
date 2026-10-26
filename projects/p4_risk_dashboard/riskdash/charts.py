"""Altair charts for the dashboard (one look across every view).

Colour roles: status colours (good / warning / critical) are reserved for RAG
status and always appear with a text label; series use the categorical palette
in a fixed order; thresholds are dashed grey rules with direct labels.
"""

from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd

from . import catalog
from .catalog import Metric, rag

STATUS_COLOR = {"GREEN": "#0ca30c", "AMBER": "#fab219", "RED": "#d03b3b", "NONE": "#9aa3ad"}
STATUS_LABEL = {"GREEN": "Within appetite", "AMBER": "Early warning", "RED": "Limit breach", "NONE": "No threshold"}
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, MUTED, GRID = "#1b2430", "#5b6673", "#e6e9ee"


def _base(chart: alt.Chart) -> alt.Chart:
    return chart.configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=GRID, domainColor=GRID,
                                tickColor=GRID, labelFontSize=11, titleFontSize=11) \
        .configure_view(strokeWidth=0).configure_legend(labelColor=INK, titleColor=MUTED, orient="top")


def series_frame(series: pd.Series, metric: Metric, start: date | None = None,
                 end: date | None = None) -> pd.DataFrame:
    s = series.dropna()
    if start:
        s = s[s.index >= start]
    if end:
        s = s[s.index <= end]
    df = pd.DataFrame({"date": pd.to_datetime(list(s.index)), "value": s.values})
    df["status"] = [rag(metric, v) for v in df["value"]]
    df["label"] = [metric.fmt(v) for v in df["value"]]
    return df


def sparkline(series: pd.Series, metric: Metric, end: date, days: int = 42) -> alt.Chart:
    df = series_frame(series, metric, end - timedelta(days=days), end)
    line = alt.Chart(df).mark_line(strokeWidth=2, color="#2a78d6").encode(
        x=alt.X("date:T", axis=None), y=alt.Y("value:Q", axis=None, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("date:T", format="%d %b"), alt.Tooltip("label:N", title=metric.name)])
    last = alt.Chart(df.tail(1)).mark_point(size=60, filled=True, opacity=1).encode(
        x="date:T", y="value:Q",
        color=alt.Color("status:N", scale=alt.Scale(domain=list(STATUS_COLOR), range=list(STATUS_COLOR.values())),
                        legend=None))
    return _base((line + last).properties(height=46))


def trend_chart(series: pd.Series, metric: Metric, end: date, start: date | None = None,
                events: pd.DataFrame | None = None, height: int = 300) -> alt.Chart:
    df = series_frame(series, metric, start, end)
    y = alt.Y("value:Q", title=metric.unit, scale=alt.Scale(zero=metric.unit != "%"))
    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
    present = [k for k in STATUS_COLOR if k in set(df["status"])]
    label_expr = " : ".join(f"datum.label == '{k}' ? '{v}'" for k, v in STATUS_LABEL.items()) + " : datum.label"
    line = alt.Chart(df).mark_line(strokeWidth=2, color="#2a78d6").encode(x=alt.X("date:T", title=None), y=y)
    points = alt.Chart(df).mark_point(size=70, filled=True).encode(
        x="date:T", y="value:Q",
        color=alt.Color("status:N", title="Status",
                        scale=alt.Scale(domain=present, range=[STATUS_COLOR[k] for k in present]),
                        legend=alt.Legend(labelExpr=label_expr)),
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[alt.Tooltip("date:T", format="%a %d %b %Y"), alt.Tooltip("label:N", title=metric.name),
                 alt.Tooltip("status:N")]).add_params(hover)
    rule = alt.Chart(df).mark_rule(color=MUTED, strokeWidth=1).encode(x="date:T").transform_filter(hover)
    layers = [line, rule, points]
    for value, label in ((metric.early_warning, "Early warning"), (metric.limit, "Limit")):
        if value is None:
            continue
        th = pd.DataFrame({"y": [value], "label": [f"{label} {metric.fmt(value)}"]})
        color = STATUS_COLOR["AMBER"] if label == "Early warning" else STATUS_COLOR["RED"]
        layers.append(alt.Chart(th).mark_rule(strokeDash=[5, 4], color=color, strokeWidth=1.5).encode(y="y:Q"))
        layers.append(alt.Chart(th).mark_text(align="left", dx=4, dy=-6, color=MUTED, fontSize=11).encode(
            y="y:Q", x=alt.value(0), text="label:N"))
    if events is not None and not events.empty:
        ev = events.copy()
        ev["date"] = pd.to_datetime(ev["event_date"])
        if start is not None:
            ev = ev[ev["date"] >= pd.Timestamp(start)]
        ev = ev[ev["date"] <= pd.Timestamp(end)]
        layers.append(alt.Chart(ev).mark_rule(color="#6250d6", strokeDash=[2, 3], strokeWidth=1).encode(
            x="date:T", tooltip=["change_id:N", alt.Tooltip("date:T", format="%d %b"), "system:N",
                                 "description:N"]))
        if 0 < len(ev) <= 4:  # label only a short list; otherwise the tooltip carries the id
            ev["row"] = [6 + 14 * (i % 2) for i in range(len(ev))]
            layers.append(alt.Chart(ev).mark_text(color="#6250d6", align="left", dx=4, fontSize=10).encode(
                x="date:T", y=alt.Y("row:Q", scale=None), text="change_id:N"))
    return _base(alt.layer(*layers).properties(height=height))


def breakdown_chart(bd: pd.DataFrame, metric: Metric) -> alt.Chart:
    df = bd.copy().head(10)
    df["direction"] = ["worse" if (c > 0) == (metric.direction == "lower_is_better") else "better"
                       for c in df["contribution"]]
    df["contribution_label"] = df["contribution"].map(lambda v: f"{v:+.2f}" if metric.unit != "count" else f"{v:+.0f}")
    bars = alt.Chart(df).mark_bar(cornerRadius=3, height=16).encode(
        y=alt.Y("member:N", sort=None, title=None),
        x=alt.X("contribution:Q", title=f"Contribution to the 7-day change ({metric.unit})"),
        color=alt.Color("direction:N", scale=alt.Scale(domain=["worse", "better"],
                                                        range=[STATUS_COLOR["RED"], "#2a78d6"]),
                        title="Effect"),
        tooltip=["member:N", alt.Tooltip("contribution_label:N", title="Contribution"),
                 alt.Tooltip("value:Q", format=",.2f", title="Value now"),
                 alt.Tooltip("prior:Q", format=",.2f", title="Value 7 days ago"), "records:Q"])
    return _base(bars.properties(height=max(120, 26 * len(df))))


def member_area(store, metric: Metric, dimension: str, start: date, end: date) -> alt.Chart:
    df = store.views[metric.view]
    df = df[(df["obs_date"] >= start) & (df["obs_date"] <= end)]
    g = df.groupby(["obs_date", dimension])[metric.numerator].sum().reset_index()
    g["value"] = g[metric.numerator] * metric.scale
    g["date"] = pd.to_datetime(g["obs_date"])
    members = list(g.groupby(dimension)["value"].sum().sort_values(ascending=False).index)
    top = members[:7]
    g[dimension] = g[dimension].where(g[dimension].isin(top), "Other")
    g = g.groupby(["date", dimension], as_index=False)["value"].sum()
    order = top + (["Other"] if len(members) > 7 else [])
    chart = alt.Chart(g).mark_area(opacity=0.9, stroke="white", strokeWidth=0.5).encode(
        x=alt.X("date:T", title=None), y=alt.Y("value:Q", stack=True, title=metric.unit),
        color=alt.Color(f"{dimension}:N", sort=order, scale=alt.Scale(domain=order, range=SERIES[:len(order)]),
                        title=dimension.replace("_", " ")),
        tooltip=[alt.Tooltip("date:T", format="%d %b"), f"{dimension}:N", alt.Tooltip("value:Q", format=",.2f")])
    return _base(chart.properties(height=260))


def radar_heatmap(store, end: date, weeks: int = 12) -> alt.Chart:
    """Worst RAG status per metric per week: the bank's risk 'weather map'."""
    rows = []
    start = end - timedelta(weeks=weeks)
    for m in catalog.METRICS:
        if m.direction == "none":
            continue
        s = store.series(m.id)
        s = s[(s.index > start) & (s.index <= end)]
        for d, v in s.items():
            week_end = d + timedelta(days=(4 - d.weekday()) % 7)
            rows.append({"metric": m.name, "domain": m.domain, "week": week_end, "status": rag(m, v),
                         "value": m.fmt(v), "kri": "KRI" if m.kri else "KPI"})
    df = pd.DataFrame(rows)
    rank = {"NONE": 0, "GREEN": 1, "AMBER": 2, "RED": 3}
    df["rank"] = df["status"].map(rank)
    df = df.sort_values("rank").groupby(["metric", "domain", "week", "kri"], as_index=False).last()
    df["week"] = pd.to_datetime(df["week"])
    df["status_label"] = df["status"].map(STATUS_LABEL)
    order = [m.name for m in catalog.METRICS if m.direction != "none"]
    chart = alt.Chart(df).mark_rect(cornerRadius=3, stroke="white", strokeWidth=2).encode(
        x=alt.X("week:O", timeUnit="yearmonthdate", title="Week ending", axis=alt.Axis(format="%d %b", labelAngle=0)),
        y=alt.Y("metric:N", sort=order, title=None),
        color=alt.Color("status:N", scale=alt.Scale(domain=list(STATUS_COLOR), range=list(STATUS_COLOR.values())),
                        legend=alt.Legend(title="Worst status in week")),
        tooltip=["metric:N", "domain:N", alt.Tooltip("week:T", format="%d %b %Y", title="Week ending"),
                 alt.Tooltip("status_label:N", title="Worst status"), alt.Tooltip("value:N", title="Worst value")])
    return _base(chart.properties(height=24 * len(order) + 40))


def path_chart(baseline: list[dict], scenario: list[dict], y: str, title: str,
               threshold: float | None = None) -> alt.Chart:
    df = pd.DataFrame([{**p, "case": "As is"} for p in baseline] + [{**p, "case": "With action"} for p in scenario])
    df["date"] = pd.to_datetime(df["date"])
    lines = alt.Chart(df).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=40)).encode(
        x=alt.X("date:T", title=None), y=alt.Y(f"{y}:Q", title=title),
        color=alt.Color("case:N", scale=alt.Scale(domain=["As is", "With action"], range=[SERIES[1], SERIES[0]]),
                        title=None),
        tooltip=[alt.Tooltip("date:T", format="%a %d %b"), "case:N", alt.Tooltip(f"{y}:Q", format=",.0f")])
    layers = [lines]
    if threshold is not None:
        layers.append(alt.Chart(pd.DataFrame({"y": [threshold]})).mark_rule(
            strokeDash=[5, 4], color=STATUS_COLOR["RED"]).encode(y="y:Q"))
    return _base(alt.layer(*layers).properties(height=260))


def aging_chart(df: pd.DataFrame, group: str, bucket: str, order: list[str], title: str) -> alt.Chart:
    seq = ["#9ec5f4", "#5598e7", "#256abf", "#0d366b"][: len(order)]
    chart = alt.Chart(df).mark_bar(cornerRadius=2, stroke="white", strokeWidth=1).encode(
        y=alt.Y(f"{group}:N", title=None, sort="-x"), x=alt.X("count:Q", title=title, stack=True),
        color=alt.Color(f"{bucket}:N", sort=order, scale=alt.Scale(domain=order, range=seq), title="Age"),
        order=alt.Order("bucket_order:Q"),
        tooltip=[f"{group}:N", f"{bucket}:N", "count:Q"])
    return _base(chart.properties(height=max(140, 30 * df[group].nunique())))


def dq_heatmap(store, end: date, weeks: int = 10) -> alt.Chart:
    df = store.views["fv_dq_rule_results"]
    df = df[(df["obs_date"] > end - timedelta(weeks=weeks)) & (df["obs_date"] <= end)].copy()
    df["week"] = [d + timedelta(days=(4 - d.weekday()) % 7) for d in df["obs_date"]]
    g = df.groupby(["rule_id", "table_name", "week"], as_index=False).agg(
        min_pass=("pass_rate_pct", "min"), threshold=("threshold_pct", "first"), breaches=("breached", "sum"))
    g["status"] = ["RED" if b >= 3 else "AMBER" if b > 0 else "GREEN" for b in g["breaches"]]
    g["label"] = g["rule_id"] + " · " + g["table_name"]
    g["week"] = pd.to_datetime(g["week"])
    chart = alt.Chart(g).mark_rect(cornerRadius=2, stroke="white", strokeWidth=2).encode(
        x=alt.X("week:O", timeUnit="yearmonthdate", title="Week ending", axis=alt.Axis(format="%d %b", labelAngle=0)),
        y=alt.Y("label:N", title=None, axis=alt.Axis(labelLimit=260)),
        color=alt.Color("status:N", scale=alt.Scale(domain=["GREEN", "AMBER", "RED"],
                                                     range=[STATUS_COLOR["GREEN"], STATUS_COLOR["AMBER"],
                                                            STATUS_COLOR["RED"]]),
                        legend=alt.Legend(title="Days below threshold in week", titleLimit=400,
                                          labelExpr="datum.label == 'GREEN' ? 'None' : "
                                                    "datum.label == 'AMBER' ? '1-2 days' : '3+ days'")),
        tooltip=["rule_id:N", "table_name:N", alt.Tooltip("week:T", format="%d %b"),
                 alt.Tooltip("min_pass:Q", format=".3f", title="Lowest pass rate %"),
                 alt.Tooltip("threshold:Q", title="Threshold %"), alt.Tooltip("breaches:Q", title="Days below")])
    return _base(chart.properties(height=20 * g["label"].nunique() + 40))
