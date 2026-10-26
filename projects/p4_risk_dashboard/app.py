"""Ledgerline Bank Risk & Data-Quality Control Room (Project 04).

Run locally from the repository root:

    streamlit run projects/p4_risk_dashboard/app.py

Offline by default: the metric layer runs on DuckDB and the Risk Insights Agent
runs its deterministic investigation playbook, so the app costs nothing. With
Databricks connection details in Streamlit secrets it can read live Unity
Catalog tables instead, and let a Databricks foundation model (free on Free
Edition) drive the agent's tools. Claude is a further option with an
ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import hashlib
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from portfolio_core.audit import AuditLog  # noqa: E402
from portfolio_core.ui import FOOTER, PORTFOLIO_CSS, pill  # noqa: E402
from riskdash import catalog, charts  # noqa: E402
from riskdash.agent import ARROW, RiskInsightsAgent  # noqa: E402
from riskdash.chat import EXAMPLES, RiskChat  # noqa: E402
from riskdash.engine import (  # noqa: E402,E501
    DatabricksSQLEngine,
    DuckDBEngine,
    QueryError,
    databricks_configured,
    duckdb_sql_for_display,
)
from riskdash.llm import (  # noqa: E402,E501
    ClaudeRiskAgent,
    RiskLLMAgent,
    databricks_from_secrets,
    databricks_llm_configured,
)
from riskdash.metrics import MetricStore  # noqa: E402
from riskdash.report import markdown_to_html  # noqa: E402
from riskdash.scenarios import SCENARIOS  # noqa: E402
from riskdash.synthetic import AS_OF, generate, load_p2_gold  # noqa: E402
from riskdash.tools import RELEVANT_CHANGE_TYPES  # noqa: E402

st.set_page_config(page_title="Risk Control Room", page_icon="🛰️", layout="wide")
st.markdown(PORTFOLIO_CSS, unsafe_allow_html=True)
st.markdown("""
<style>
  .cr-hero { background: radial-gradient(circle at 15% 20%, #1d4e89 0%, #0b2545 55%, #071a33 100%);
    color: #fff; padding: 1.2rem 1.5rem; border-radius: 14px; margin-bottom: .8rem; position: relative;
    overflow: hidden; }
  .cr-hero h1 { color: #fff; font-size: 1.6rem; margin: 0 0 .2rem 0; }
  .cr-hero p { color: #c9d7ea; margin: 0; font-size: .95rem; }
  .cr-pulse { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: #3ddc84;
    box-shadow: 0 0 0 0 rgba(61,220,132,.7); animation: pulse 1.8s infinite; margin-right: .45rem; }
  @keyframes pulse { 0% {box-shadow: 0 0 0 0 rgba(61,220,132,.6);} 70% {box-shadow: 0 0 0 10px rgba(61,220,132,0);}
    100% {box-shadow: 0 0 0 0 rgba(61,220,132,0);} }
  .kri { border-radius: 12px; padding: .65rem .8rem .4rem .8rem; border: 1px solid #e3e8ef; background: #fff;
    border-left: 6px solid var(--rag); min-height: 118px; }
  .kri .n { font-size: .78rem; color: #5b6673; font-weight: 600; line-height: 1.15; min-height: 1.9rem; }
  .kri .v { font-size: 1.45rem; font-weight: 700; color: #1b2430; line-height: 1.2; }
  .kri .s { font-size: .74rem; font-weight: 700; color: var(--ragtext); }
  .kri .d { font-size: .74rem; color: #5b6673; }
  .brief { border: 1px solid #d6e2f0; background: linear-gradient(180deg,#f7fafe 0%,#fff 100%); border-radius: 12px;
    padding: .9rem 1.1rem; }
  .brief h4 { margin: 0 0 .4rem 0; color: #13315c; }
  .brief li { margin-bottom: .25rem; font-size: .92rem; }
  .finding { border: 1px solid #e3e8ef; border-left: 5px solid var(--rag); border-radius: 10px; padding: .7rem .9rem;
    margin-bottom: .6rem; background: #fff; }
  .finding h5 { margin: 0 0 .3rem 0; font-size: .98rem; }
  .finding li { font-size: .88rem; color: #33404d; }
  .conf { font-size: .72rem; font-weight: 700; padding: .1rem .45rem; border-radius: 999px; background: #e9ecef; }
  .tool { font-family: ui-monospace, Menlo, monospace; font-size: .8rem; color: #13315c; }
</style>
""", unsafe_allow_html=True)

RAG_TEXT = {"GREEN": "#0a7a0a", "AMBER": "#8a5a00", "RED": "#b02a2a", "NONE": "#5b6673"}
RAG_WORD = {"GREEN": "● Within appetite", "AMBER": "▲ Early warning", "RED": "■ Limit breach", "NONE": "Context"}


# ------------------------------------------------------------------ data
@st.cache_resource(show_spinner="Building the risk mart and running the metric layer...")
def offline_store() -> MetricStore:
    gold = load_p2_gold()
    return MetricStore(DuckDBEngine(generate(gold), gold))


@st.cache_resource(show_spinner="Querying Databricks SQL...")
def databricks_store() -> MetricStore:
    return MetricStore(DatabricksSQLEngine())


@st.cache_resource(show_spinner="The Risk Insights Agent is investigating...")
def briefing_for(engine_name: str, as_of: date):
    store = databricks_store() if engine_name == "databricks" else offline_store()
    agent = RiskInsightsAgent(store, as_of)
    return agent, agent.run()


def state() -> dict:
    if "p4" not in st.session_state:
        st.session_state.p4 = {"audit": AuditLog(), "chat": [], "approved": {}, "pending_q": None}
    return st.session_state.p4


S = state()

# --------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### Control panel")
    engine_options = ["DuckDB (offline, free)"] + (["Databricks SQL (live)"] if databricks_configured() else [])
    engine_label = st.radio("SQL engine", engine_options, index=0,
                            help="The same Databricks SQL views run on either engine.")
    engine_name = "databricks" if engine_label.startswith("Databricks") else "duckdb"
    try:
        store = databricks_store() if engine_name == "databricks" else offline_store()
    except QueryError as exc:
        st.error(f"Databricks SQL failed ({exc}). Using the offline engine.")
        engine_name, store = "duckdb", offline_store()
    business_days = [d for d in store.dates if d.weekday() < 5 and d >= store.first_date + timedelta(days=35)]
    as_of = st.select_slider("Reporting date", options=business_days, value=min(AS_OF, business_days[-1]),
                             format_func=lambda d: d.strftime("%a %d %b %Y"),
                             help="Everything on the page, including the agent, recomputes for this date.")
    ai_modes = ["Offline agent (free)"]
    if databricks_llm_configured():
        ai_modes.append("Databricks AI (live)")
    if ClaudeRiskAgent.available():
        ai_modes.append("Claude (live)")
    ai_mode = st.radio("AI mode", ai_modes, index=0,
                       help="Live modes appear only when the app owner has connected a Databricks workspace "
                            "(foundation models, free on Free Edition) or added an Anthropic key.")
    if len(ai_modes) == 1:
        st.caption("Live AI is off: no Databricks workspace or API key connected. The offline agent uses the "
                   "same tools.")
    st.divider()
    st.caption("All data is synthetic (fictional Ledgerline Bank). Project 02 Gold tables + simulated "
               "operations history with 7 planted incidents.")

shared_agent, briefing = briefing_for(engine_name, as_of)
# Per-request agent and tools, so one viewer's questions never mix with another's trace.
agent = RiskInsightsAgent(store, as_of)
tools = agent.tools
snaps = {s.metric.id: s for s in store.snapshots(as_of)}
events = store.engine.run("SELECT * FROM ${mart.change_events}")
events["event_date"] = pd.to_datetime(events["event_date"]).dt.date

kc = briefing.kri_counts
st.markdown(
    f'<div class="cr-hero"><h1>🛰️ Ledgerline Bank · Risk &amp; Data-Quality Control Room</h1>'
    f'<p><span class="cr-pulse"></span>Reporting date <b>{as_of.strftime("%A %d %B %Y")}</b> · '
    f'{kc["RED"]} KRIs in breach · {kc["AMBER"]} at early warning · {len(briefing.findings)} issues investigated by '
    f'the Risk Insights Agent</p></div>', unsafe_allow_html=True)
st.markdown(
    pill(f"Engine: {store.engine.name}", "blue") + pill(ai_mode.split(" (")[0], "grey")
    + pill(f"{len(catalog.METRICS)} metrics in Databricks SQL", "grey")
    + pill(f"{len(briefing.trace)} agent tool calls", "grey") + pill("Synthetic data", "amber"),
    unsafe_allow_html=True)

flash = st.container()  # always present, so messages never shift the tabs

tabs = st.tabs(["🛰️ Control room", "🤖 Ask the risk agent", "📊 Operations & data quality", "🔎 Metric explorer",
                "🧪 What-if lab", "📝 Board pack", "ℹ️ How it works"])


def kri_tile(col, metric_id: str) -> None:
    s = snaps[metric_id]
    m = s.metric
    delta = ""
    if s.delta is not None and s.prior is not None:
        delta = f"7d: {m.fmt(s.prior)} → {m.fmt(s.value)}"
    col.markdown(
        f'<div class="kri" style="--rag:{charts.STATUS_COLOR[s.status]};--ragtext:{RAG_TEXT[s.status]}">'
        f'<div class="n">{m.name}</div><div class="v">{m.fmt(s.value)}</div>'
        f'<div class="s">{RAG_WORD[s.status]}</div><div class="d">{delta}</div></div>', unsafe_allow_html=True)
    col.altair_chart(charts.sparkline(store.series(metric_id), m, as_of), width="stretch")


def render_finding(f) -> None:
    conf = f"<span class='conf'>root cause: {f.confidence} confidence</span>" if f.root_cause else ""
    items = "".join(f"<li>{d}</li>" for d in f.detail)
    rel = ""
    if f.related:
        rel = "<li><i>Also moves: " + ", ".join(catalog.get(r).name for r in dict.fromkeys(f.related)) + "</i></li>"
    st.markdown(f'<div class="finding" style="--rag:{charts.STATUS_COLOR[f.status]}"><h5>{ARROW[f.status]} '
                f'{f.headline} {conf}</h5><ul>{items}{rel}</ul></div>', unsafe_allow_html=True)


def tidy_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Show midnight timestamps as plain dates."""
    out = df.copy()
    for col in out.columns:
        values = out[col].dropna()
        if pd.api.types.is_datetime64_any_dtype(out[col]) and (values.dt.normalize() == values).all():
            out[col] = out[col].dt.date
    return out


def render_trace(calls, title: str = "How the agent worked it out", expanded: bool = False) -> None:
    with st.expander(f"{title}: {len(calls)} tool call(s)", expanded=expanded):
        for i, c in enumerate(calls, 1):
            args = ", ".join(f"{k}={v!r}" for k, v in c.args.items() if v is not None)
            st.markdown(f"<span class='tool'>{i:>2}. {c.tool}({args})</span> · {c.ms} ms", unsafe_allow_html=True)


# ========================================================== control room
with tabs[0]:
    st.markdown("#### Risk appetite (RAS-004 and AML-002 KRIs)")
    kris = [m.id for m in catalog.METRICS if m.kri]
    for row in (kris[:4], kris[4:]):
        cols = st.columns(4)
        for col, mid in zip(cols, row, strict=True):
            kri_tile(col, mid)

    left, right = st.columns([3, 2])
    with left:
        bullets = "".join(f"<li>{s}</li>" for s in briefing.summary)
        st.markdown(f'<div class="brief"><h4>🤖 Agent briefing · {as_of.strftime("%d %b %Y")}</h4>'
                    f'<ul>{bullets}</ul><div style="font-size:.78rem;color:#5b6673">{briefing.mode}. Numbers come '
                    f'from the metric layer; root causes are hypotheses for the owner to confirm.</div></div>',
                    unsafe_allow_html=True)
        if st.button("▶ Watch the agent investigate", key="watch"):
            with st.status("Risk Insights Agent at work...", expanded=True) as status:
                for i, c in enumerate(briefing.trace, 1):
                    args = ", ".join(f"{k}={v!r}" for k, v in c.args.items() if v is not None)
                    st.markdown(f"<span class='tool'>{i:>2}. {c.tool}({args})</span>", unsafe_allow_html=True)
                    time.sleep(0.02)
                status.update(label=f"Done: {len(briefing.trace)} tool calls, {len(briefing.findings)} issues, "
                                    f"{len(briefing.actions)} actions", state="complete", expanded=True)
    with right:
        st.markdown("#### Operations at a glance")
        ops = ["DQ_HEALTH_SCORE", "RECON_OPEN_BREAKS", "EXC_BACKLOG", "EXC_SLA_BREACH_PCT"]
        cols = st.columns(2)
        for i, mid in enumerate(ops):
            kri_tile(cols[i % 2], mid)

    st.markdown("#### Issues the agent investigated")
    for f in briefing.findings:
        render_finding(f)

    st.markdown("#### Risk weather map: worst status per week")
    st.altair_chart(charts.radar_heatmap(store, as_of), width="stretch")

# ============================================================ risk agent
with tabs[1]:
    st.markdown("Ask in plain English. The agent picks its tools, runs them on the metric layer and answers with the "
                "evidence. " + (f"**{ai_mode.split(' (')[0]}** is driving the tools; every number is checked "
                                "against the tool results." if "live" in ai_mode
                                else "**Offline mode**: rule-based routing, free."))
    qcols = st.columns(4)
    for i, q in enumerate(EXAMPLES[:8]):
        if qcols[i % 4].button(q, key=f"ex{i}", width="stretch"):
            S["pending_q"] = q
    chat_box = st.container()
    typed = st.chat_input("Ask the risk agent, e.g. 'What if 400m of deposits leave?'", key="chat_in")
    question = typed or S.pop("pending_q", None)
    S["pending_q"] = None
    if question:
        chat = RiskChat(agent, briefing)
        offline = chat.ask(question)
        entry = {"q": question, "a": offline.text, "data": offline.data, "calls": tools.trace[offline.trace_from:],
                 "mode": "offline", "note": None}
        if "live" in ai_mode:
            brain = ai_mode.split(" (")[0]
            try:
                start = len(tools.trace)
                if brain == "Claude":
                    live_ans = ClaudeRiskAgent(tools).ask(question)
                else:
                    live_ans = RiskLLMAgent(tools, databricks_from_secrets()).ask(question)
                if live_ans.text and live_ans.grounded and not live_ans.error:
                    entry.update({"a": live_ans.text, "mode": live_ans.model,
                                  "calls": tools.trace[start:],
                                  "note": f"{live_ans.tool_calls} tool calls · {live_ans.input_tokens:,} in / "
                                          f"{live_ans.output_tokens:,} out tokens · all numbers grounded"})
                else:
                    entry["note"] = (f"The {brain} answer was not shown because "
                                     + (live_ans.error or f"these numbers were not in any tool result: "
                                        f"{', '.join(live_ans.unsupported)}") + ". Showing the offline answer.")
                S["audit"].record("llm_answer", {"question": question, "grounded": live_ans.grounded,
                                                 "unsupported": live_ans.unsupported, "error": live_ans.error,
                                                 "tokens_in": live_ans.input_tokens,
                                                 "tokens_out": live_ans.output_tokens, "model": live_ans.model},
                                  actor=brain.lower().replace(" ", "_"))
            except Exception as exc:  # network, auth, quota or endpoint problems never break the page
                entry["note"] = f"{brain} is unavailable ({type(exc).__name__}). Showing the offline answer."
        S["audit"].record("question", {"question": question, "intent": offline.intent, "metric": offline.metric_id,
                                       "mode": entry["mode"]}, actor="viewer")
        S["chat"].append(entry)
    with chat_box:
        if not S["chat"]:
            st.info("Try a suggested question above, or type your own below.")
        for e in S["chat"][-6:]:
            with st.chat_message("user"):
                st.markdown(e["q"])
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(e["a"])
                if e.get("note"):
                    st.caption(e["note"])
                data = e.get("data") or {}
                if "records" in data and data["records"]:
                    st.dataframe(pd.DataFrame(data["records"]), hide_index=True, width="stretch")
                wi = data.get("what_if")
                if wi and "path_baseline" in wi:
                    st.altair_chart(charts.path_chart(wi["path_baseline"], wi["path_scenario"], "backlog",
                                                      "Payments Ops backlog"), width="stretch")
                render_trace(e["calls"])

# ============================================== operations & data quality
with tabs[2]:
    st.markdown("#### Data quality")
    c1, c2, c3 = st.columns(3)
    for col, mid in zip((c1, c2, c3), ("DQ_HEALTH_SCORE", "DQ_RULES_BREACHED", "FEED_ON_TIME_PCT"), strict=True):
        kri_tile(col, mid)
    st.markdown("**Rule health by week** (24 rules across Project 02 lakehouse tables and enterprise systems)")
    st.altair_chart(charts.dq_heatmap(store, as_of), width="stretch")
    with st.expander("Latest real lakehouse run (Project 02 Gold dq_scorecard, read as-is)"):
        gold = load_p2_gold()["dq_scorecard"]
        st.dataframe(gold[["layer", "table_name", "rule_name", "action", "rows_checked", "rows_failed",
                           "pass_rate_pct", "status"]], hide_index=True, width="stretch")
    feeds = store.records("FEED_ON_TIME_PCT", as_of)
    if not feeds.empty:
        st.markdown("**Source feeds delivered after their SLA in the last 7 days**")
        late = store.rows("FEED_ON_TIME_PCT", as_of)
        late = late[late["on_time"] == 0][["business_date", "source_system", "feed_name", "sla_ts", "arrived_ts",
                                            "minutes_vs_sla"]]
        if late.empty:
            st.success("Every feed arrived on time.")
        else:
            st.dataframe(tidy_dates(late), hide_index=True, width="stretch")

    st.markdown("#### Reconciliations")
    c1, c2, c3, c4 = st.columns(4)
    for col, mid in zip((c1, c2, c3, c4), ("RECON_OPEN_BREAKS", "RECON_OPEN_VALUE_USD_M", "RECON_AGED_BREAKS",
                                           "RECON_AUTO_MATCH_PCT"), strict=True):
        kri_tile(col, mid)
    rb = store.rows("RECON_OPEN_BREAKS", as_of)
    order = ["0-2 days", "3-5 days", "6-10 days", "over 10 days"]
    if not rb.empty:
        g = rb.groupby(["recon_id", "age_bucket"]).size().reset_index(name="count")
        g["bucket_order"] = g["age_bucket"].map(order.index)
        a, b = st.columns(2)
        a.markdown("**Open breaks by age**")
        a.altair_chart(charts.aging_chart(g, "recon_id", "age_bucket", order, "Open breaks"), width="stretch")
        b.markdown("**Open breaks over time by reconciliation**")
        b.altair_chart(charts.member_area(store, catalog.get("RECON_OPEN_BREAKS"), "recon_id",
                                          as_of - timedelta(days=60), as_of), width="stretch")

    st.markdown("#### Operations and SLAs")
    c1, c2, c3 = st.columns(3)
    for col, mid in zip((c1, c2, c3), ("EXC_BACKLOG", "EXC_PAST_DUE", "EXC_SLA_BREACH_PCT"), strict=True):
        kri_tile(col, mid)
    ex = store.rows("EXC_BACKLOG", as_of)
    eorder = ["under 1 day", "1-2 days", "3-5 days", "over 5 days"]
    if not ex.empty:
        g = ex.groupby(["team", "age_bucket"]).size().reset_index(name="count")
        g["bucket_order"] = g["age_bucket"].map(eorder.index)
        a, b = st.columns(2)
        a.markdown("**Exception aging by team**")
        a.altair_chart(charts.aging_chart(g, "team", "age_bucket", eorder, "Open exceptions"),
                       width="stretch")
        b.markdown("**Exceptions closed late, by team (7-day rate)**")
        b.altair_chart(charts.member_area(store, catalog.get("EXC_PAST_DUE"), "team", as_of - timedelta(days=60),
                                          as_of), width="stretch")

# ======================================================== metric explorer
with tabs[3]:
    names = {m.id: f"{m.domain} · {m.name}" for m in catalog.METRICS}
    default = briefing.findings[0].metric_id if briefing.findings else catalog.METRICS[0].id
    mid = st.selectbox("Metric", list(names), index=list(names).index(default), format_func=names.get)
    m = catalog.get(mid)
    s = snaps[mid]
    h1, h2, h3, h4 = st.columns(4)
    h1.metric(m.name, m.fmt(s.value), None if s.delta is None else
              (f"{s.delta:+.2f}" if m.unit != "count" else f"{s.delta:+.0f}") + " vs 7 days ago",
              delta_color="inverse" if m.direction == "lower_is_better" else "normal")
    h2.metric("Status", RAG_WORD[s.status])
    h3.metric("Early warning / limit", f"{m.fmt(m.early_warning)} / {m.fmt(m.limit)}")
    h4.markdown(f"<div style='font-size:.875rem'>Owner</div><div style='font-size:1.15rem;font-weight:600;"
                f"margin-top:.4rem'>{m.owner}</div>", unsafe_allow_html=True)
    span = st.radio("Window", ["6 weeks", "3 months", "All"], index=1, horizontal=True)
    start = {"6 weeks": as_of - timedelta(days=42), "3 months": as_of - timedelta(days=91), "All": None}[span]
    relevant = events[events["change_type"].isin(RELEVANT_CHANGE_TYPES.get(m.domain, set()))]
    st.altair_chart(charts.trend_chart(store.series(mid), m, as_of, start, relevant), width="stretch")
    if not relevant.empty:
        st.caption("Dashed purple lines are releases, incidents or business changes of the kind that usually "
                   "move this type of metric. Hover a line for details.")
    if m.dimensions:
        dim = st.radio("Break down by", list(m.dimensions), horizontal=True)
        a, b = st.columns(2)
        bd = store.breakdown(mid, dim, as_of)
        a.markdown(f"**What moved it in the last 7 days** (by {dim.replace('_', ' ')}): "
                   "<span style='color:#d03b3b'>■ made it worse</span> · "
                   "<span style='color:#2a78d6'>■ made it better</span>", unsafe_allow_html=True)
        a.altair_chart(charts.breakdown_chart(bd, m), width="stretch")
        b.markdown("**Drill down: pick a row to see its records**")
        show = bd.assign(value=bd["value"].map(m.fmt), prior=bd["prior"].map(m.fmt))
        event = b.dataframe(show, hide_index=True, width="stretch", on_select="rerun",
                            selection_mode="single-row", key=f"bd_{mid}_{dim}")
        rows = event.selection.rows if event and event.selection else []
        member = show.iloc[rows[0]]["member"] if rows else None
        recs = tidy_dates(store.records(mid, as_of, {dim: member} if member else None))
        st.markdown(f"**Records behind the number{f' for {member}' if member else ''}** ({len(recs)})")
        st.dataframe(recs, hide_index=True, width="stretch")
    with st.expander("Definition, formula and SQL (transparent calculation logic)"):
        st.markdown(f"**Definition.** {m.definition}\n\n**Formula.** `{m.formula()}`\n\n"
                    f"**Thresholds.** early-warning {m.fmt(m.early_warning)}, limit {m.fmt(m.limit)}"
                    + (f" ({m.policy_ref})" if m.policy_ref else " (illustrative management target)")
                    + f"\n\n**Fact view:** `{m.view}` · **Dimensions:** {', '.join(m.dimensions) or 'none'}")
        sql = catalog.view_sql(m.view)
        a, b = st.columns(2)
        a.markdown("Databricks SQL (source of truth)")
        a.code(sql, language="sql")
        b.markdown("DuckDB SQL (translated by sqlglot for offline mode)")
        b.code(duckdb_sql_for_display(sql), language="sql")

# ============================================================ what-if lab
with tabs[4]:
    st.markdown("Move a lever and the agent's scenario models recompute instantly. Each result lists its assumptions.")
    a, b = st.columns(2)
    with a:
        st.markdown("##### 👥 Payments Ops capacity")
        n = st.slider("Temporary handlers added", 0, 10, 3)
        r = tools.call("what_if", scenario="payments_extra_handlers", amount=n)
        st.altair_chart(charts.path_chart(r["path_baseline"], r["path_scenario"], "backlog", "Open exceptions"),
                        width="stretch")
        clear = date.fromisoformat(r["backlog_clear_date"]).strftime("%d %b") if r["backlog_clear_date"] else None
        st.markdown(f"Backlog today **{r['backlog_now']}** · in 10 business days: **{r['backlog_in_10_days_baseline']}"
                    f"** as is vs **{r['backlog_in_10_days_scenario']}** with {n} handler(s)"
                    + (f" · cleared by **{clear}**" if clear else "")
                    + f" · {r['handlers_to_stop_growth']} needed just to stop growth.")
        st.caption("Assumptions: " + "; ".join(r["assumptions"]))
    with b:
        st.markdown("##### 💧 Liquidity: further deposit outflow")
        out = st.slider("Outflow (USD m)", 0, 1000, 250, step=25)
        r = tools.call("what_if", scenario="deposit_outflow", amount=out)
        c1, c2 = st.columns(2)
        c1.metric("LCR now", f"{r['lcr_now_pct']:.1f}%")
        c2.metric("LCR after outflow", f"{r['lcr_after_pct']:.1f}%",
                  f"{r['lcr_after_pct'] - r['lcr_now_pct']:+.1f} pts")
        st.markdown(f"Status after: **{RAG_WORD[r['status_after']]}** · headroom before the 110% limit: about **USD "
                    f"{r['max_outflow_before_limit_usd_m']:,.0f}m**.")
        st.caption("Assumptions: " + "; ".join(r["assumptions"]))
    a, b, c = st.columns(3)
    with a:
        st.markdown("##### 💸 New operational loss")
        loss = st.slider("Loss (USD m)", 0.0, 6.0, 1.0, step=0.25)
        r = tools.call("what_if", scenario="op_loss_event", amount=loss)
        st.metric("Losses YTD after", f"USD {r['ytd_after_usd_m']:.2f}m", f"{RAG_WORD[r['status_after']]}",
                  delta_color="off")
        st.caption(f"Headroom to the USD 15m limit today: USD {r['headroom_to_limit_usd_m']:.2f}m.")
    with b:
        st.markdown("##### 🔁 EUR nostro clean-up")
        pct = st.slider("Release R26.10 breaks cleared (%)", 0, 100, 60, step=10)
        r = tools.call("what_if", scenario="eur_nostro_cleanup", amount=pct)
        st.metric("Open breaks after", r["open_breaks_after"], f"{r['open_breaks_after'] - r['open_breaks_now']:+d}",
                  delta_color="inverse")
        st.caption(f"{RAG_WORD[r['status_after']]} · clears USD {r['unreconciled_value_cleared_usd_m']:.2f}m.")
    with c:
        st.markdown("##### 🪪 KYC reviews completed")
        k = st.slider("High-risk reviews completed this week", 0, 6, 1)
        r = tools.call("what_if", scenario="kyc_reviews_completed", amount=k)
        st.metric("Overdue in 14 days", f"{r['pct_in_14_days_after']:.1f}%", RAG_WORD[r["status_in_14_days_after"]],
                  delta_color="off")
        st.caption(f"Today after completions: {r['pct_now_after']:.1f}% ({RAG_WORD[r['status_now_after']]}).")
    with st.expander("Scenario catalogue"):
        st.markdown("\n".join(f"- **{k}**: {v}" for k, v in SCENARIOS.items()))

# ============================================================= board pack
with tabs[5]:
    md = briefing.markdown()
    key = f"{engine_name}:{as_of.isoformat()}"
    approved = S["approved"].get(key)
    st.markdown("The agent drafts the weekly board pack. A named risk owner must review and approve it before it can "
                "be exported as final: AI drafts, humans decide.")
    a, b = st.columns([2, 1])
    with a:
        for domain, c in briefing.commentary.items():
            with st.expander(f"{ARROW[c['status']]} {domain} · {c['status']}", expanded=c["status"] == "RED"):
                for line in c["lines"]:
                    st.markdown(f"- {line}")
        st.markdown("##### Actions")
        st.dataframe(pd.DataFrame([{"Action": x.action, "Owner": x.owner, "Due": x.due, "Basis": x.basis}
                                   for x in briefing.actions]), hide_index=True, width="stretch")
    with b:
        st.markdown("##### Review and sign-off")
        edited = st.text_area("Reviewer notes (added to the pack)", key=f"notes_{key}", height=120)
        reviewer = st.text_input("Reviewer name and role", key=f"rev_{key}", placeholder="e.g. A. Analyst, Head of ERM")
        if approved:
            st.success(f"Approved by {approved['by']} at {approved['at']} · pack hash {approved['hash'][:12]}…")
        elif st.button("✅ Approve board pack", disabled=not reviewer.strip(), type="primary"):
            final = md + (f"\n\n## Reviewer notes\n\n{edited.strip()}" if edited.strip() else "")
            digest = hashlib.sha256(final.encode()).hexdigest()
            ev = S["audit"].record("board_pack_approved", {"as_of": as_of.isoformat(), "reviewer": reviewer.strip(),
                                                           "sha256": digest, "findings": len(briefing.findings)},
                                   actor=reviewer.strip(), correlation_id=key)
            S["approved"][key] = {"by": reviewer.strip(), "at": ev.timestamp[:19].replace("T", " "), "hash": digest,
                                  "md": final}
            flash.success("Board pack approved and logged in the audit trail.")
            st.rerun()
        final_md = approved["md"] if approved else md
        stamp = (f"APPROVED by {approved['by']} · {approved['at']} UTC · sha256 {approved['hash'][:16]}"
                 if approved else "DRAFT · not yet approved by a risk owner")
        st.download_button("⬇️ Download HTML", markdown_to_html(final_md, "Ledgerline risk briefing", stamp),
                           file_name=f"ledgerline_risk_briefing_{as_of}.html", mime="text/html",
                           width="stretch")
        st.download_button("⬇️ Download Markdown", f"{stamp}\n\n{final_md}",
                           file_name=f"ledgerline_risk_briefing_{as_of}.md", width="stretch")
        st.markdown("##### Audit trail")
        ok, broken = S["audit"].verify_chain()
        st.caption(("🔒 Hash chain intact" if ok else f"⚠️ Chain broken at {broken}") +
                   f" · {S['audit'].count()} event(s) this session")
        evs = S["audit"].events(limit=8)
        if evs:
            st.dataframe(pd.DataFrame([{"time": e.timestamp[11:19], "actor": e.actor, "event": e.event_type,
                                        "hash": e.hash[:10]} for e in evs]), hide_index=True,
                         width="stretch")

# =========================================================== how it works
with tabs[6]:
    st.markdown("#### Architecture")
    st.graphviz_chart("""
digraph G { rankdir=TB; nodesep=0.3; ranksep=0.35; bgcolor=transparent; node [shape=box, style="rounded,filled", fontname=Helvetica,
  fontsize=11, color="#13315c", fillcolor="#eef3fa"]; edge [color="#8a96a3", fontname=Helvetica, fontsize=9];
  p2 [label="Project 02 lakehouse\\nGold tables (real pipeline output)\\ncustomer_360 · aml_alert_candidates\\ndaily_transaction_summary · dq_scorecard", fillcolor="#fbf0c9"];
  mart [label="Risk mart (simulated ops history)\\nDQ results · feeds · recons · exceptions\\nKYC reviews · op losses · KRIs · change log", fillcolor="#e9edf1"];
  views [label="12 fact views\\nDatabricks SQL", fillcolor="#dbe9ff"];
  eng [label="Engine\\nDuckDB (offline) · Spark · Databricks SQL"];
  cat [label="Metric catalogue\\n20 KPIs/KRIs · thresholds · RAS-004"];
  store [label="MetricStore\\nseries · breakdown · drill-down"];
  tools [label="Agent tools (13)\\nscan · explain · onset · correlate\\nforecast · what-if · policy", fillcolor="#e8e2fb"];
  agent [label="Risk Insights Agent\\noffline playbook, or an LLM:\\nDatabricks foundation model or Claude", fillcolor="#e8e2fb"];
  ui [label="Control room UI\\nchat · drill-down · what-if · board pack", fillcolor="#d8f3dc"];
  audit [label="Hash-chained audit\\n(portfolio_core)"];
  p2 -> views; mart -> views; views -> eng -> store; cat -> store; store -> tools -> agent -> ui; agent -> audit;
  ui -> audit; {rank=same; p2; mart;} {rank=same; eng; cat;} {rank=same; ui; audit;} }
""")  # noqa: E501
    st.markdown("#### What is real, what is simulated")
    st.markdown("""
| Component | Evidence | Status |
|---|---|---|
| Project 02 Gold tables (customers, AML alerts, payment volumes, DQ scorecard) | Real output of the Spark + Delta pipeline (synthetic bank data) | ✅ Verified |
| Operations history (DQ results, feeds, recons, exceptions, KYC reviews, losses, KRIs, change log) | Simulated by `riskdash/synthetic.py` with a fixed seed and 7 planted incidents | 🧪 Simulated |
| Metric SQL on DuckDB and on Apache Spark | Identical results checked by automated tests | ✅ Verified |
| Offline Risk Insights Agent | Finds all 7 planted incidents and their causes in automated tests | ✅ Verified |
| Databricks SQL live mode | Code path tested with a fake connection; needs the owner's workspace | ⏳ Not yet run |
| Live AI: Databricks foundation model (free on Free Edition) | Tool loop, endpoint discovery and grounding check tested with a fake workspace; needs the owner's workspace | ⏳ Not yet run |
| Live AI: Claude (optional, paid) | Tool loop and grounding check tested with a fake client; needs an API key | ⏳ Not yet run |
""")  # noqa: E501
    backtest = HERE / "docs" / "evaluation" / "backtest_report.md"
    if backtest.exists():
        st.markdown("#### Agent back-test")
        st.markdown(backtest.read_text().split("\n", 2)[2])
        st.caption("Produced by `evaluate.py` and re-run in CI. The incidents were planted in synthetic data, so this "
                   "proves the investigation mechanics work end to end, not real-world accuracy.")
    st.markdown("#### Data sources by metric")
    st.dataframe(pd.DataFrame([{"Metric": m.name, "Domain": m.domain, "Fact view": m.view, "KRI": m.kri,
                                "Early warning": m.fmt(m.early_warning), "Limit": m.fmt(m.limit),
                                "Policy": m.policy_ref or "", "Owner": m.owner} for m in catalog.METRICS]),
                 hide_index=True, width="stretch")

st.markdown(FOOTER, unsafe_allow_html=True)
