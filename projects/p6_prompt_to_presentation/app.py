"""Prompt-to-Presentation AI: Dynamic Executive Reporting Platform (Project 06).

Run locally from the repository root:

    streamlit run projects/p6_prompt_to_presentation/app.py

Each browser session gets its own workspace (datasets, deck versions, review comments and
hash-chained audit log), so visitors never see each other's work. All data is synthetic
unless a user uploads their own file during the session.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from deckpilot.domains.ops_risk import FILTER_DIMS as OPS_DIMS  # noqa: E402
from deckpilot.domains.ops_risk import band  # noqa: E402
from deckpilot.domains.workforce import FILTER_DIMS as WF_DIMS  # noqa: E402
from deckpilot.domains.workforce import fmt_date, mon  # noqa: E402
from deckpilot.parser import PromptRejected, spec_summary  # noqa: E402
from deckpilot.payload import fmt_int, fmt_money, fmt_pct, fmt_pp  # noqa: E402
from deckpilot.service import ReportingService, ReviewError, csv_bytes  # noqa: E402
from deckpilot.spec import Comparison, DeckSpec, SlideRequest, period_label  # noqa: E402
from portfolio_core.ui import FOOTER, PORTFOLIO_CSS, banner, pill  # noqa: E402

st.set_page_config(page_title="Prompt-to-Presentation AI", page_icon="📊", layout="wide")
st.markdown(PORTFOLIO_CSS, unsafe_allow_html=True)
st.markdown("""
<style>
  .pp-slide { border: 1px solid #d5dce4; border-radius: 8px; padding: .6rem .9rem; margin-bottom: .6rem;
              background: rgba(19,49,92,.03); }
  .pp-slide h4 { margin: 0 0 .2rem 0; color: #13315c; font-size: 1.0rem; }
  .pp-sub { color: #5c6773; font-size: .85rem; }
  .pp-note { font-size: .85rem; border-left: 3px solid #c77d00; padding-left: .6rem; margin: .2rem 0; }
</style>
""", unsafe_allow_html=True)

SERIES = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4", "#008300", "#6250D6", "#E34948"]
NAVY = "#13315C"
DOMAIN_LABEL = {"ops_risk": "Operational risk & controls (brief demo data)",
                "workforce": "Technology workforce review (DB case study shape)"}


def svc() -> ReportingService:
    if "svc" not in st.session_state:
        s = ReportingService(tempfile.mkdtemp(prefix="p2p_"))
        s.bootstrap()
        st.session_state.svc = s
    return st.session_state.svc


def fig_style(fig, height=320):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40, b=10), font=dict(size=12),
                      legend=dict(orientation="h", y=-0.25, title_text=""), plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)", title_font=dict(size=14))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(140,150,160,.25)")
    return fig


S = svc()
st.markdown(banner("Prompt-to-Presentation AI", "Governed master dashboard → natural-language prompt → editable, "
                   "versioned PowerPoint with evidence-linked commentary, human review and full provenance."),
            unsafe_allow_html=True)

# ---------------------------------------------------------------------- sidebar
with st.sidebar:
    st.subheader("Workspace")
    domain_key = st.radio("Dataset", list(DOMAIN_LABEL), format_func=DOMAIN_LABEL.get, key="domain_key")
    domain = S.domains[domain_key]
    ds = S.dataset(domain_key)
    actor = st.text_input("Your name (recorded in the audit trail)", value="analyst", max_chars=40).strip() or \
        "analyst"
    st.markdown(pill(f"Dataset {ds.version}", "blue") + pill("Synthetic" if ds.synthetic else "Uploaded",
                                                              "green" if ds.synthetic else "amber"),
                unsafe_allow_html=True)
    st.caption(f"Data {mon(ds.months[0])} to {mon(ds.months[-1])}"
               + (f" · roster as of {fmt_date(ds.snapshot)}" if ds.snapshot and domain_key == "workforce" else "")
               + f" · {ds.report.quarantined} rows quarantined")
    st.markdown(pill("Parser: " + S.parser_mode, "grey"), unsafe_allow_html=True)
    st.caption("Claude is used only if an approved ANTHROPIC_API_KEY is set in Streamlit secrets; otherwise the "
               "rules parser runs offline at no cost.")
    st.divider()
    st.subheader("Dashboard filters")
    dims = OPS_DIMS if domain_key == "ops_risk" else WF_DIMS
    filters = {}
    for dim, label in dims.items():
        vals = st.multiselect(label, ds.dimension_values(dim), key=f"f_{domain_key}_{dim}",
                              format_func=domain.display)
        if vals:
            filters[dim] = vals
    default = domain.default_months(ds)
    if domain_key == "ops_risk":
        rng = st.select_slider("Reporting months", options=ds.months, value=(default[0], default[-1]),
                               format_func=lambda m: period_label([m]), key=f"m_{domain_key}")
    else:
        rng = st.select_slider("Months", options=ds.months, value=(default[0], default[-1]), format_func=mon,
                               key=f"m_{domain_key}")
    months = ds.months[ds.months.index(rng[0]): ds.months.index(rng[1]) + 1]
    cmp_mode = st.selectbox("Compare with", ["prior_period", "yoy", "none"], index=0 if domain_key == "ops_risk"
                            else 2, format_func={"prior_period": "Prior period", "yoy": "Same period last year",
                                                 "none": "No comparison"}.get, key=f"c_{domain_key}")

dash_spec = DeckSpec(domain=domain_key, template=next(iter(domain.templates)), title="Dashboard view",
                     filters=filters, months=months, comparison=Comparison(mode=cmp_mode),
                     slides=[SlideRequest(slide_type=s) for s in list(domain.templates[next(iter(
                         domain.templates))].slides)[:6]])
problems = dash_spec.validate_against(domain, ds)
ctx = domain.context(dash_spec, ds, S.qbs[domain_key])

tabs = st.tabs(["📊 Master dashboard", "✨ Prompt to deck", "📝 Review & versions", "🗂️ Data & refresh",
                "📚 KPI dictionary & governance", "🔍 Provenance & tests"])


# ---------------------------------------------------------------------- dashboard
def ops_dashboard():
    cur = domain.metrics(ctx, ctx.months)
    prev = domain.metrics(ctx, ctx.cmp_months)
    st.caption(f"{ctx.period} vs {ctx.cmp_period} · {ctx.filters_label(OPS_DIMS)} · data to "
               f"{mon(ds.months[-1])}")
    for d in ctx.disclosures[:3]:
        st.markdown(f'<div class="pp-note">{d}</div>', unsafe_allow_html=True)
    c = st.columns(4)
    d = lambda k: (cur[k] - prev[k]) if cur[k] is not None and prev[k] is not None else None  # noqa: E731
    c[0].metric("Exception rate", fmt_pct(cur["exception_rate"], 2),
                fmt_pp(d("exception_rate"), 2) if d("exception_rate") is not None else None, delta_color="inverse")
    c[1].metric("On-time completion", fmt_pct(cur["on_time_pct"], 1),
                fmt_pp(d("on_time_pct"), 2) if d("on_time_pct") is not None else None)
    c[2].metric("High-severity exceptions", fmt_int(cur["high_sev"]),
                f"{d('high_sev'):+,.0f}" if d("high_sev") is not None else None, delta_color="inverse")
    c[3].metric(f"Open exceptions ({mon(max(ctx.months))})", fmt_int(cur["open"]),
                f"{d('open'):+,.0f}" if d("open") is not None else None, delta_color="inverse")
    c = st.columns(4)
    c[0].metric("Remediation closure rate", fmt_pct(cur["closure_rate"], 1),
                fmt_pp(d("closure_rate"), 1) if d("closure_rate") is not None else None)
    c[1].metric("MoM movement (exception rate)", fmt_pp(cur["mom"], 2) if cur["mom"] is not None else "n/a")
    c[2].metric("Data quality completeness", fmt_pct(cur["dq_completeness"], 1), f"{fmt_pct(cur['dq_pass'], 1)} rows "
                "flagged Pass", delta_color="off")
    c[3].metric("Operational loss", fmt_money(cur["loss"]))
    allm = ds.months
    end = allm.index(max(ctx.months))
    window = allm[max(0, end - 11): end + 1]
    mth = domain.monthly(ctx, window).reset_index(names="month")
    mth["Month"] = mth["month"].map(lambda m: period_label([m]))
    mth["In period"] = mth["month"].isin(ctx.months).map({True: "Selected period", False: "Earlier months"})
    a, b = st.columns(2)
    f1 = px.bar(mth, x="Month", y="exceptions", color="In period", title="Exceptions by month",
                color_discrete_map={"Selected period": NAVY, "Earlier months": "#AEB8C4"})
    a.plotly_chart(fig_style(f1), width="stretch")
    f2 = px.line(mth, x="Month", y="exception_rate", markers=True, title="Exception rate by month")
    f2.update_traces(line_color=SERIES[0], line_width=2, marker_size=8)
    f2.update_yaxes(tickformat=".2%")
    b.plotly_chart(fig_style(f2), width="stretch")
    rows, vol = domain.frame(ctx, ctx.months)
    a, b = st.columns(2)
    g = rows.groupby(["business_line", "severity"], as_index=False)["exceptions"].sum()
    f3 = px.bar(g, x="business_line", y="exceptions", color="severity", title="Exceptions by business line and "
                "severity", category_orders={"severity": ["Low", "Medium", "High", "Critical"]},
                color_discrete_sequence=SERIES)
    a.plotly_chart(fig_style(f3), width="stretch")
    rc = rows.groupby("root_cause", as_index=False)["exceptions"].sum().sort_values("exceptions").tail(8)
    f4 = px.bar(rc, x="exceptions", y="root_cause", orientation="h", title="Top exception drivers (root cause)")
    f4.update_traces(marker_color=NAVY)
    b.plotly_chart(fig_style(f4), width="stretch")
    a, b, c3 = st.columns(3)
    latest = max(ctx.months)
    op = rows[(rows["reporting_month"] == latest) & (rows["remediation_status"] != "Closed")].copy()
    op["band"] = op["ageing_days"].map(band)
    ag = op.groupby("band", as_index=False)["exceptions"].sum()
    f5 = px.bar(ag, x="band", y="exceptions", title=f"Open exceptions by ageing, {mon(latest)}",
                category_orders={"band": ["0-30 days", "31-60 days", "61-90 days", "90+ days", "Unknown"]})
    f5.update_traces(marker_color=NAVY)
    a.plotly_chart(fig_style(f5, 300), width="stretch")
    stt = rows.groupby("remediation_status", as_index=False)["exceptions"].sum()
    f6 = px.pie(stt, names="remediation_status", values="exceptions", hole=0.55, title="Remediation status",
                color_discrete_sequence=SERIES)
    b.plotly_chart(fig_style(f6, 300), width="stretch")
    pr = vol.groupby("process", as_index=False)[["transaction_volume", "completed_on_time"]].sum()
    pr["on_time"] = pr["completed_on_time"] / pr["transaction_volume"]
    f7 = px.bar(pr.sort_values("on_time"), x="on_time", y="process", orientation="h", title="On-time completion "
                "by process")
    f7.update_traces(marker_color=SERIES[2])
    f7.update_xaxes(tickformat=".1%")
    c3.plotly_chart(fig_style(f7, 300), width="stretch")
    with st.expander("Data quality for this view"):
        dq = rows["data_quality_status"].value_counts().rename_axis("status").reset_index(name="rows")
        st.dataframe(dq, hide_index=True)
        st.dataframe(ds.report.as_frame(), hide_index=True, width="stretch")


def wf_dashboard():
    h = domain._headline(ctx)
    mth = domain.monthly(ctx)
    st.caption(f"{ctx.period} · {ctx.filters_label(WF_DIMS)} · on-contract figures as of {fmt_date(h['asof'])}")
    for d in [x for x in ctx.disclosures if "extra spaces" not in x and "E-mail" not in x][:3]:
        st.markdown(f'<div class="pp-note">{d}</div>', unsafe_allow_html=True)
    onc = h["onc"]
    n = len(onc)
    c = st.columns(3)
    c[0].metric("People on contract", fmt_int(n), f"of {h['unique']} on the roster", delta_color="off")
    c[1].metric(f"FTE {mon(h['m1'])}", f"{h['fte1']:.1f}", f"{(h['fte_chg'] or 0) * 100:+.0f}% vs {mon(h['m0'])}")
    asof_m = h["asof"].strftime("%Y-%m")
    run = float(mth.loc[asof_m, "cost"]) if asof_m in mth.index else None
    c[2].metric(f"Monthly cost run-rate ({mon(asof_m)})", fmt_money(run, "€"))
    c = st.columns(3)
    c[0].metric("T&M share of people on contract", fmt_pct((onc["category"] == "T&M").mean() if n else None, 0))
    c[1].metric("Top-2 vendor share (external)", fmt_pct(h["top2_share"], 0),
                ", ".join(h["vend"].index[:2]), delta_color="off")
    c[2].metric(f"Contracts ending {fmt_date(h['cliff_date']) if h['cliff_date'] else ''}", fmt_int(h["cliff_n"]),
                f"{int(onc['zero_allocation'].sum())} zero-allocation", delta_color="off")
    m = mth.loc[ctx.months].reset_index(names="month")
    m["Month"] = m["month"].map(mon)
    a, b = st.columns(2)
    f1 = px.line(m, x="Month", y="fte", markers=True, title="FTE by month")
    f1.update_traces(line_color=NAVY, line_width=2, marker_size=8)
    a.plotly_chart(fig_style(f1), width="stretch")
    f2 = px.bar(m, x="Month", y="cost", title="Monthly cost run-rate (EUR)")
    f2.update_traces(marker_color=SERIES[0])
    b.plotly_chart(fig_style(f2), width="stretch")
    al = domain._alloc(ctx)
    al = al[al["reporting_month"].isin(ctx.months)]
    a, b = st.columns(2)
    loc = al.groupby(["reporting_month", "location"], as_index=False)["fte"].sum()
    loc["Month"] = loc["reporting_month"].map(mon)
    f3 = px.line(loc, x="Month", y="fte", color="location", markers=True, title="FTE by location",
                 color_discrete_sequence=SERIES)
    a.plotly_chart(fig_style(f3), width="stretch")
    ct = onc.groupby(["location", "category"], as_index=False).size()
    f4 = px.bar(ct, x="size", y="location", color="category", orientation="h", title="People on contract: location "
                "x resource type", category_orders={"category": ["Internal", "T&M", "Fixed Price"]},
                color_discrete_sequence=SERIES)
    b.plotly_chart(fig_style(f4), width="stretch")
    a, b = st.columns(2)
    pv = al.pivot_table(index=["location", "category"], columns="reporting_month", values="fte",
                        aggfunc="sum").fillna(0)
    pv.index = [f"{i} · {j}" for i, j in pv.index]
    pv.columns = [mon(x) for x in pv.columns]
    f5 = px.imshow(pv.round(1), text_auto=True, aspect="auto", color_continuous_scale=["#F3F6FA", NAVY],
                   title="FTE heatmap: location · type x month")
    a.plotly_chart(fig_style(f5, 360), width="stretch")
    vend = h["vend"].head(8).sort_values()
    f6 = px.bar(x=vend.values, y=vend.index, orientation="h", title="External people on contract by vendor",
                labels={"x": "people", "y": "vendor"})
    f6.update_traces(marker_color=NAVY)
    b.plotly_chart(fig_style(f6, 360), width="stretch")
    with st.expander("Data quality checks on the roster"):
        st.dataframe(ds.report.as_frame(), hide_index=True, width="stretch")


with tabs[0]:
    if problems:
        st.warning("Current filters are not valid for this dataset: " + "; ".join(problems))
    else:
        (ops_dashboard if domain_key == "ops_risk" else wf_dashboard)()
        st.divider()
        c1, c2, c3 = st.columns([2, 1, 1])
        tpl = c1.selectbox("Create a deck from this exact view: template", list(domain.templates),
                           format_func=lambda k: domain.templates[k].label, key="dash_tpl")
        nsl = c2.number_input("Slides", 3, 12, 6 if tpl != "workforce_review" else 11, key="dash_n")
        if c3.button("Create deck from view", type="primary", width="stretch"):
            from deckpilot.parser import choose_slides, make_title

            spec = dash_spec.model_copy(update={
                "template": tpl, "title": make_title(domain, tpl, filters, months),
                "slides": [SlideRequest(slide_type=s) for s in choose_slides(domain, tpl, int(nsl), [], [])]})
            rec = S.generate_from_spec(spec, "[dashboard view]", actor)
            st.session_state.current = rec.version_id
            st.success(f"Created {rec.label}. Open the 'Prompt to deck' or 'Review & versions' tab.")


# ---------------------------------------------------------------------- preview helpers
def preview_block(b, key):
    if b["kind"] == "kpis":
        cols = st.columns(len(b["items"]))
        for c, it in zip(cols, b["items"], strict=True):
            c.metric(it["label"], it["value"], it.get("sub") or None, delta_color="off")
    elif b["kind"] == "chart":
        kind = b["chart"]
        cats = b["categories"]
        if kind == "doughnut":
            fig = go.Figure(go.Pie(labels=cats, values=b["series"][0]["values"], hole=0.55,
                                   marker_colors=SERIES[:len(cats)]))
        elif kind == "waterfall":
            vals = b["series"][0]["values"]
            meas = ["absolute"] + ["relative"] * (len(vals) - 2) + ["total"]
            fig = go.Figure(go.Waterfall(x=cats, y=vals, measure=meas, decreasing={"marker": {"color": "#B02A37"}},
                                         increasing={"marker": {"color": "#2D6A4F"}},
                                         totals={"marker": {"color": NAVY}}))
        else:
            fig = go.Figure()
            for i, s in enumerate(b["series"]):
                col = SERIES[i % len(SERIES)] if len(b["series"]) > 1 else NAVY
                if kind == "line":
                    fig.add_trace(go.Scatter(x=cats, y=s["values"], name=s["name"], mode="lines+markers",
                                             line=dict(color=col, width=2), marker=dict(size=8)))
                else:
                    horiz = kind.startswith("bar")
                    fig.add_trace(go.Bar(x=s["values"] if horiz else cats, y=cats if horiz else s["values"],
                                         name=s["name"], orientation="h" if horiz else "v", marker_color=col))
            if "stacked" in kind:
                fig.update_layout(barmode="stack")
            if "%" in (b.get("number_format") or ""):
                fig.update_yaxes(tickformat=".1%" if kind != "bar" else None)
                if kind.startswith("bar"):
                    fig.update_xaxes(tickformat=".0%")
        fig.update_layout(title=b["title"], showlegend=len(b["series"]) > 1 or kind == "doughnut")
        st.plotly_chart(fig_style(fig, 300), width="stretch", key=key)
        if b.get("note"):
            st.caption(b["note"])
    elif b["kind"] == "table":
        st.caption(b.get("title", ""))
        st.dataframe(pd.DataFrame(b["rows"], columns=b["columns"]), hide_index=True, width="stretch")
    elif b["kind"] == "cards":
        cols = st.columns(min(len(b["items"]), 4))
        for i, it in enumerate(b["items"]):
            with cols[i % len(cols)]:
                st.markdown(f"**{it['head']}**  \n{it['body']}" + (f"  \n*{it['when']}*" if it.get("when") else ""))


def preview_deck(rec, prefix):
    for i, s in enumerate(rec.payload["slides"], start=1):
        with st.expander(f"Slide {i}: {s['title']}", expanded=i <= 2):
            st.markdown(f'<span class="pp-sub">{s.get("subtitle", "")}</span>', unsafe_allow_html=True)
            for j, b in enumerate(s["blocks"]):
                preview_block(b, f"{prefix}-{rec.version_id}-{i}-{j}")
            if s.get("commentary"):
                st.markdown("**Management commentary (draft)**")
                for c in s["commentary"]:
                    tag = {"hypothesis": "*Hypothesis:* ", "caveat": "*Caveat:* ", "edited": "✍️ "}.get(c["kind"], "")
                    ev = c.get("evidence") or {}
                    src = f" <span class='pp-sub'>[{ev.get('metric')} · {ev.get('window')}]</span>" if ev.get(
                        "metric") else ""
                    st.markdown(f"- {tag}{c['text']}{src}", unsafe_allow_html=True)
            if s.get("footnote"):
                st.caption(s["footnote"])


def download_buttons(rec, key):
    data, name = S.download(rec.version_id)
    c1, c2, c3 = st.columns(3)
    c1.download_button("⬇️ Download editable .pptx", data, file_name=name, key=f"dl-{key}-{rec.version_id}",
                       mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                       width="stretch")
    if c2.button("Export PDF", key=f"pdf-{key}-{rec.version_id}", width="stretch"):
        with st.spinner("Converting with LibreOffice..."):
            pdf = S.export_pdf(rec.version_id)
        if pdf:
            st.session_state[f"pdf-{rec.version_id}"] = str(pdf)
        else:
            st.info("PDF export needs LibreOffice, which is not installed on this host. Download the .pptx and use "
                    "File > Export in PowerPoint.")
    pdfp = st.session_state.get(f"pdf-{rec.version_id}")
    if pdfp and Path(pdfp).exists():
        c2.download_button("⬇️ Download PDF", Path(pdfp).read_bytes(), file_name=Path(pdfp).name,
                           key=f"dlpdf-{key}-{rec.version_id}", width="stretch")
    c3.download_button("⬇️ Provenance (JSON)", json.dumps(S.provenance(rec.version_id), indent=2, default=str),
                       file_name=f"{rec.deck_id}_v{rec.version_no}_provenance.json", key=f"prov-{key}-{rec.version_id}",
                       width="stretch")
    status = {"draft": ("Draft – not for distribution", "amber"), "in_review": ("In review", "blue"),
              "changes_requested": ("Changes requested", "red"), "approved": ("Approved – internal use", "green")}
    t, col = status[rec.review_status]
    st.markdown(pill(t, col) + pill(f"dataset {rec.dataset_version}", "grey") + pill(f"parser: {rec.parser_mode}",
                                                                                     "grey"), unsafe_allow_html=True)


# ---------------------------------------------------------------------- prompt to deck
with tabs[1]:
    st.subheader("1. Describe the deck you need")
    ex_cols = st.columns(3)
    for i, ex in enumerate(domain.examples):
        if ex_cols[i].button(ex, key=f"ex-{domain_key}-{i}", width="stretch"):
            st.session_state.prompt_text = ex
    prompt = st.text_area("Prompt", key="prompt_text", height=90, max_chars=600,
                          placeholder=domain.examples[0])
    if st.button("Generate deck", type="primary"):
        try:
            with st.spinner("Parsing prompt, querying governed data, building slides..."):
                rec, notes = S.generate(prompt, domain_key, actor)
            st.session_state.current = rec.version_id
            if rec.payload["domain"] != domain_key:
                st.info(f"The prompt reads as a {DOMAIN_LABEL[rec.payload['domain']]} request, so that dataset was "
                        "used.")
        except PromptRejected as exc:
            st.error(str(exc))
        except ValueError as exc:
            st.error(str(exc))
    cur_id = st.session_state.get("current")
    if cur_id:
        rec = S.store.get(cur_id)
        d = S.domains[rec.payload["domain"]]
        st.divider()
        st.subheader(f"2. {rec.label}: {rec.payload['slides'][0]['title']}")
        for n in rec.notes:
            st.markdown(f'<div class="pp-note">{n}</div>', unsafe_allow_html=True)
        download_buttons(rec, "p")
        with st.expander("Validated slide specification (what the parser produced)"):
            st.json(spec_summary(DeckSpec.model_validate(rec.spec), d))
            st.json(rec.spec)
        st.subheader("3. Follow up")
        st.caption("Each follow-up saves a new version; earlier files are never overwritten.")
        chips = st.columns(4)
        for i, ex in enumerate(d.followup_examples):
            if chips[i % 4].button(ex, key=f"fu-{rec.payload['domain']}-{i}", width="stretch"):
                st.session_state.fu_text = ex
        fu = st.text_input("Follow-up instruction", key="fu_text", max_chars=300)
        if st.button("Apply follow-up", type="primary"):
            try:
                with st.spinner("Applying..."):
                    new, notes = S.follow_up(rec.version_id, fu, actor)
                st.session_state.current = new.version_id
                st.rerun()
            except (PromptRejected, ValueError) as exc:
                st.error(str(exc))
        st.subheader("4. Preview")
        st.caption("Preview drawn from the same payload as the .pptx. In PowerPoint every chart, table and text box is "
                   "editable (right-click a chart > Edit Data).")
        preview_deck(rec, "p")


# ---------------------------------------------------------------------- review & versions
with tabs[2]:
    decks = S.store.decks()
    if not decks:
        st.info("Generate a deck first.")
    else:
        ids = [x["deck_id"] for x in decks]
        cur_deck = S.store.get(st.session_state["current"]).deck_id if st.session_state.get("current") else ids[-1]
        deck_id = st.selectbox("Deck", ids, index=ids.index(cur_deck),
                               format_func=lambda i: f"{i} · " + next(x["title"] for x in decks if x["deck_id"] == i))
        vers = S.store.versions(deck_id)
        hist = pd.DataFrame([{"version": f"v{v.version_no}", "created (UTC)": v.created_at.replace("T", " ")[:19],
                              "by": v.actor, "action": v.action, "prompt": v.prompt, "dataset": v.dataset_version,
                              "period": v.period, "filters": json.dumps(v.filters), "status": v.review_status,
                              "file sha256": v.file_sha256[:12]} for v in vers])
        st.markdown("**Version history** (append-only; every version keeps its own file)")
        st.dataframe(hist, hide_index=True, width="stretch")
        vno = st.selectbox("Version", [v.version_no for v in vers], index=len(vers) - 1, format_func=lambda n: f"v{n}")
        rec = next(v for v in vers if v.version_no == vno)
        download_buttons(rec, "r")
        st.download_button("⬇️ Review log (CSV): versions, comments and decisions", S.store.review_log_csv(deck_id),
                           file_name=f"{deck_id}_review_log.csv")
        left, right = st.columns([3, 2])
        with left:
            st.markdown("#### Edit management commentary")
            slides = rec.payload["slides"]
            default_slide = next((i for i, x in enumerate(slides) if x["slide_type"] == "commentary"),
                                 1 if len(slides) > 1 else 0)
            sno = st.selectbox("Slide", list(range(1, len(slides) + 1)), index=default_slide,
                               format_func=lambda i: f"{i}: {slides[i - 1]['title']}", key=f"es-{rec.version_id}")
            s = slides[sno - 1]
            new_title = st.text_input("Slide title", s["title"], key=f"t-{rec.version_id}-{sno}")
            txt = st.text_area("Commentary (one bullet per line)", "\n".join(c["text"] for c in s["commentary"]),
                               height=180, key=f"c-{rec.version_id}-{sno}")
            st.caption("Saving creates a new version. Lines you keep unchanged keep their evidence links; new or "
                       "changed lines are marked as human-edited and any number not found in the slide's data is "
                       "flagged.")
            if st.button("Save as new version", key=f"save-{rec.version_id}-{sno}"):
                new, notes = S.edit_commentary(rec.version_id, sno, txt.splitlines(), actor,
                                               title=new_title if new_title != s["title"] else None)
                st.session_state.current = new.version_id
                for n in notes:
                    st.warning(n) if "not found" in n else st.success(n)
                st.rerun()
        with right:
            st.markdown("#### Review comments")
            st.caption("python-pptx cannot write native PowerPoint threaded comments, so comments live here, are "
                       "exported into each slide's speaker notes on download, and appear in the review log.")
            for cmt in S.store.comments(rec.version_id):
                cc1, cc2 = st.columns([4, 1])
                tone = "green" if cmt["status"] == "resolved" else "amber"
                cc1.markdown(f"**Slide {cmt['slide_no']}** · {cmt['author']} · {pill(cmt['status'], tone)}  \n"
                             f"{cmt['text']}", unsafe_allow_html=True)
                if cmt["status"] == "open" and cc2.button("Resolve", key=f"res-{cmt['id']}"):
                    S.resolve_comment(cmt["id"], actor)
                    st.rerun()
            with st.form(f"cmt-{rec.version_id}", clear_on_submit=True):
                cs = st.number_input("Slide", 1, len(slides), 1)
                ct = st.text_area("Comment", max_chars=800, height=80)
                if st.form_submit_button("Add comment"):
                    try:
                        S.add_comment(rec.version_id, int(cs), ct, actor)
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
            st.markdown("#### Review decision")
            with st.form(f"rev-{rec.version_id}"):
                reviewer = st.text_input("Reviewer name", max_chars=40)
                decision = st.selectbox("Decision", ["in_review", "changes_requested", "approved"])
                note = st.text_input("Note", max_chars=300)
                if st.form_submit_button("Record decision"):
                    try:
                        S.review(rec.version_id, decision, reviewer, note)
                        st.rerun()
                    except ReviewError as exc:
                        st.error(str(exc))
            for r in S.store.reviews(rec.version_id):
                st.caption(f"{r['created_at'][:19]} · {r['reviewer']}: {r['decision']} – {r['note']}")
            st.caption("Approval needs a reviewer who is not the author (maker-checker), no open comments, and "
                       "explicit confirmation of any unverified numbers. Nothing is ever sent to clients "
                       "automatically.")
        with st.expander("Preview this version"):
            preview_deck(rec, "r")


# ---------------------------------------------------------------------- data & refresh
with tabs[3]:
    st.markdown(f"#### Active dataset: {ds.version}")
    st.json(ds.meta())
    st.markdown("**Validation report**")
    st.dataframe(ds.report.as_frame(), hide_index=True, width="stretch")
    if ds.report.quarantine is not None:
        with st.expander(f"Quarantined rows ({ds.report.quarantined})"):
            q = ds.report.quarantine
            st.dataframe(q[[c for c in q.columns if c not in ("Email-ID", "Name")]].astype(str), width="stretch")
    st.divider()
    st.markdown("#### Upload or refresh data")
    st.caption("A new file goes through validation first. It only feeds the dashboard and new decks after you "
               "activate it. Existing deck versions stay pinned to the dataset they were built from.")
    sample = {"ops_risk": "ops_risk_refresh_oct2026.csv",
              "workforce": "workforce_roster_refresh_oct2018.csv"}[domain_key]
    up = st.file_uploader("CSV or Excel (.xlsx/.xlsm, first sheet or 'Data'; macros are never run)",
                          type=["csv", "xlsx", "xlsm"], key=f"up-{domain_key}")
    snap = None
    if domain_key == "workforce":
        snap = st.date_input("Roster as-of date ('on contract today')", value=pd.Timestamp("2018-10-31")).isoformat()
    c1, c2 = st.columns(2)
    if c1.button("Validate uploaded file", disabled=up is None):
        st.session_state.staged = S.stage_upload(domain_key, up.getvalue(), up.name, actor, snapshot=snap).version
    if c2.button(f"Use the sample modified dataset ({sample})"):
        st.session_state.staged = S.stage_upload(domain_key, (HERE / "data" / sample).read_bytes(), sample, actor,
                                                 snapshot=snap, synthetic=True).version
    sv = st.session_state.get("staged")
    staged = S.registry.staged.get(sv) if sv else None
    if staged:
        st.markdown(f"**Staged: {staged.version}** from `{staged.source}` – "
                    + ("✅ passed validation" if staged.report.ok else "⛔ failed validation"))
        st.dataframe(staged.report.as_frame(), hide_index=True, width="stretch")
        if staged.months:
            new_m = [m for m in staged.months if m not in ds.months]
            st.caption(f"Rows accepted {staged.report.rows_accepted:,} · quarantined {staged.report.quarantined} · "
                       f"months {mon(staged.months[0])}–{mon(staged.months[-1])}"
                       + (f" · new months: {', '.join(mon(m) for m in new_m)}" if new_m else ""))
        if st.button("Activate this dataset", type="primary", disabled=not staged.report.ok):
            S.activate(staged.version, actor)
            st.session_state.staged = None
            st.success("Activated. The dashboard now uses it; say 'refresh using latest data' to update a deck.")
            st.rerun()
    st.markdown("**Dataset history**")
    st.dataframe(pd.DataFrame([d.meta() for d in S.registry.history(domain_key)]), hide_index=True,
                 width="stretch")
    st.markdown("**Download sample data**")
    for f in sorted((HERE / "data").glob("*.csv")):
        st.download_button(f.name, f.read_bytes(), file_name=f.name, key=f"sd-{f.name}")


# ---------------------------------------------------------------------- KPI dictionary
with tabs[4]:
    st.markdown("#### KPI dictionary")
    st.dataframe(domain.kpi_dictionary(), hide_index=True, width="stretch")
    st.markdown("#### Approved slide catalog and templates")
    st.dataframe(pd.DataFrame([{"slide_type": k, "label": v.label, "what it shows": v.description}
                               for k, v in domain.slide_catalog.items()]), hide_index=True, width="stretch")
    st.dataframe(pd.DataFrame([{"template": k, "label": t.label, "default slide order": ", ".join(t.slides)}
                               for k, t in domain.templates.items()]), hide_index=True, width="stretch")
    st.markdown("#### Governance controls")
    st.markdown("""
- **Synthetic data only** in the repository; uploads stay in the browser session's temporary workspace.
- **No code or SQL execution**: prompts are parsed into a Pydantic `DeckSpec`; only allowlisted tables, columns,
  filter dimensions and dataset values reach a parameterised DuckDB query. Prompts containing code, SQL or
  injection phrases are rejected and logged.
- **Validation gate**: required columns, category allowlists, ranges, duplicates and inconsistent repeated volumes;
  bad rows are quarantined, warnings are disclosed on slides.
- **Grounded commentary**: every sentence carries its metric, value, window and filters; interpretations are
  labelled *Hypothesis*; disclosures (quarantine, missing comparison data, planned months) flow into notes.
- **Versioning**: append-only versions, each with prompt, spec, dataset version + SHA-256, query fingerprints, KPI
  definitions, timestamp and review status; the file hash is recorded.
- **Human review**: draft watermark until approved; maker-checker; open comments block approval; no automatic
  distribution.
- **Costs**: the default parser is offline and free; Claude is used only when an approved key is configured.
""")


# ---------------------------------------------------------------------- provenance & tests
with tabs[5]:
    ok, broken = S.audit.verify_chain()
    st.markdown(pill("Audit chain intact" if ok else f"Audit chain broken at {broken}", "green" if ok else "red")
                + pill(f"{S.audit.count()} events", "grey"), unsafe_allow_html=True)
    af = S.audit_frame()
    st.dataframe(af.drop(columns=["payload"]) if not af.empty else af, hide_index=True, width="stretch")
    if not af.empty:
        seq = st.selectbox("Inspect event", af["seq"].tolist())
        st.json(af.set_index("seq").loc[seq, "payload"])
        st.download_button("⬇️ Audit log (CSV)", csv_bytes(af), file_name="audit_log.csv")
    if st.session_state.get("current"):
        st.markdown("#### Provenance of the current deck version")
        st.json(S.provenance(st.session_state["current"]))
    st.markdown("#### Automated test results")
    tr = HERE / "docs" / "test_results.md"
    if tr.exists():
        st.markdown(tr.read_text())
    else:
        st.caption("Run `pytest projects/p6_prompt_to_presentation` to produce results.")

st.markdown(FOOTER, unsafe_allow_html=True)
