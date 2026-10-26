"""Streamlit explorer for the Ledgerline Bank lakehouse (Project 02).

Run locally from the repository root:

    streamlit run projects/p2_lakehouse/app.py

The pipeline itself runs on Apache Spark + Delta Lake (locally or on Databricks).
This app shows the outputs of the last real run, exported to data/snapshot/.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import altair as alt  # noqa: E402
import streamlit as st  # noqa: E402

from explorer import EXAMPLE_QUERIES, SQL_TABLES, QueryError, Snapshot, gold_sql, run_query, sql_console  # noqa: E402
from portfolio_core.ui import FOOTER, PORTFOLIO_CSS, banner, pill  # noqa: E402

st.set_page_config(page_title="Banking Lakehouse", page_icon="🏦", layout="wide")
st.markdown(PORTFOLIO_CSS, unsafe_allow_html=True)
st.markdown("""
<style>
  .lh-card { border: 1px solid rgba(19,49,92,.15); border-radius: 10px; padding: .8rem 1rem; height: 100%; }
  .lh-card h4 { margin: 0 0 .3rem 0; font-size: 1rem; }
  .lh-card p { margin: 0; font-size: .86rem; color: #4a5568; }
  .lh-bronze { border-top: 5px solid #b07a3b; } .lh-silver { border-top: 5px solid #8d99a6; }
  .lh-gold { border-top: 5px solid #d4a017; } .lh-src { border-top: 5px solid #13315c; }
</style>
""", unsafe_allow_html=True)

RULE_LABEL = {
    "STRUCTURING": "Structuring (cash just under 10k)",
    "HIGH_RISK_JURISDICTION": "High-risk jurisdiction wire",
    "RAPID_MOVEMENT": "Rapid movement of funds",
}
STATUS_COLOR = {"PASS": "green", "WARN": "amber", "QUARANTINED": "red"}


@st.cache_data
def load() -> Snapshot:
    return Snapshot.load()


@st.cache_resource
def console():
    return sql_console()


snap = load()
s = snap.summary
h = snap.headline()

st.markdown(banner(
    "Ledgerline Bank · Banking Data Lakehouse",
    "Bronze → Silver → Gold Delta Lake pipeline on Apache Spark, with data-quality controls, SCD Type 2 history, "
    "reconciliation and AML alerting. Synthetic data for a fictional bank."), unsafe_allow_html=True)
st.markdown(
    pill(f"Run {s['run_id']}", "blue") + pill(f"{s['engine']}", "grey")
    + pill(f"Spark {s['spark_version']} · Delta {s['delta_version']}", "grey")
    + pill(f"Pipeline time {s['duration_s']:.0f}s", "grey")
    + pill(f"{h['controls_passed']}/{h['controls']} blocking controls passed", "green"),
    unsafe_allow_html=True)
st.caption("You are looking at the real outputs of the pipeline run above, exported after it finished. "
           "The hosted app does not run Spark itself; `python projects/p2_lakehouse/run_pipeline.py` "
           "reproduces these numbers exactly (fixed random seed), and the automated tests check that.")

tabs = st.tabs(["Overview", "Data quality", "AML alerts", "Customer history (SCD2)", "Delta Lake",
                "SQL console", "Gold tables", "How it works"])

# ------------------------------------------------------------------ overview
with tabs[0]:
    c = st.columns(6)
    c[0].metric("Source rows landed", f"{h['source_rows']:,}")
    c[1].metric("Silver transactions", f"{h['silver_transactions']:,}")
    c[2].metric("Duplicates removed", h["duplicates_removed"])
    c[3].metric("Records quarantined", h["quarantined"])
    c[4].metric("DQ checks evaluated", h["checks"], help=f"{h['checks_with_findings']} checks found issues")
    c[5].metric("AML alert candidates", h["alerts"], help="All pending human review")

    st.subheader("Medallion lineage")
    counts = s["table_counts"]

    def node(name: str, layer: str, color: str) -> str:
        rows = counts.get(f"{layer}.{name}", 0)
        return f'"{layer}.{name}" [label="{name}\\n{rows:,} rows", fillcolor="{color}"]'

    def cluster(key: str, label: str, color: str, nodes: list[str]) -> str:
        return (f'subgraph cluster_{key} {{ label="{label}"; labeljust=l; style="rounded,dashed"; color="{color}"; '
                f'fontcolor="{color}"; fontname="Helvetica-Bold"; ' + "; ".join(nodes) + "; }")

    edge = "->"
    dot = [
        'digraph G { rankdir=LR; newrank=true; bgcolor=transparent; nodesep=0.3; ranksep=1.1; splines=spline; '
        'node [shape=box, style="rounded,filled", fontname=Helvetica, fontsize=11, color="#13315c", '
        'width=2.1]; edge [color="#8a96a3", arrowsize=0.7, fontname=Helvetica, fontsize=9, fontcolor="#4a5568"];',
        cluster("l", "LANDING  files", "#13315c", [
            '"src.customers" [label="customers.csv", fillcolor="#dbe9ff"]',
            '"src.accounts" [label="accounts.csv", fillcolor="#dbe9ff"]',
            '"src.transactions" [label="transactions.jsonl", fillcolor="#dbe9ff"]',
            '"src.reference" [label="reference data\\nFX · country risk · branches", fillcolor="#dbe9ff"]']),
        cluster("b", "BRONZE  raw, as delivered", "#9c6b30",
                [node(t, "bronze", "#f3e3cf") for t in ("customers", "accounts", "transactions")]),
        cluster("s", "SILVER  validated, conformed", "#5f6b78",
                [node(t, "silver", "#e9edf1") for t in ("customers", "accounts", "transactions", "ref_fx_rates")]
                + [node("transactions_quarantine", "silver", "#f8d7da")]),
        cluster("g", "GOLD  business-ready", "#a67c00",
                [node(t, "gold", "#fbf0c9") for t in ("aml_alert_candidates", "customer_360",
                                                      "daily_transaction_summary", "segment_monthly_kpis",
                                                      "dq_scorecard")]),
        f'"src.customers" {edge} "bronze.customers"; "src.accounts" {edge} "bronze.accounts"; '
        f'"src.transactions" {edge} "bronze.transactions"; "src.reference" {edge} "silver.ref_fx_rates";',
        f'"bronze.customers" {edge} "silver.customers" [label="SCD2 MERGE"]; '
        f'"bronze.accounts" {edge} "silver.accounts" [label="upsert MERGE"]; '
        f'"bronze.transactions" {edge} "silver.transactions" [label="insert-only MERGE"]; '
        f'"bronze.transactions" {edge} "silver.transactions_quarantine" [label="failed DQ rules", color="#c0392b", '
        'fontcolor="#c0392b"];',
        f'"silver.ref_fx_rates" {edge} "silver.transactions" [style=dashed, label="to USD", constraint=false]; '
        f'"silver.accounts" {edge} "silver.transactions" [style=dashed, label="enrich", constraint=false];',
        f'"silver.transactions" {edge} "gold.aml_alert_candidates"; '
        f'"silver.transactions" {edge} "gold.daily_transaction_summary"; '
        f'"silver.transactions" {edge} "gold.segment_monthly_kpis"; "silver.customers" {edge} "gold.customer_360"; '
        f'"silver.accounts" {edge} "gold.customer_360"; '
        f'"silver.transactions_quarantine" {edge} "gold.dq_scorecard";',
        '{rank=same; "src.customers"; "src.accounts"; "src.transactions"; "src.reference"}',
        '{rank=same; "bronze.customers"; "bronze.accounts"; "bronze.transactions"}',
        '{rank=same; "silver.ref_fx_rates"; "silver.customers"; "silver.accounts"; "silver.transactions"; '
        '"silver.transactions_quarantine"}',
        '{rank=same; "gold.aml_alert_candidates"; "gold.customer_360"; "gold.daily_transaction_summary"; '
        '"gold.segment_monthly_kpis"; "gold.dq_scorecard"}',
        "}",
    ]
    st.graphviz_chart("\n".join(dot), use_container_width=True)

    left, right = st.columns([1.1, 1])
    with left:
        st.subheader("Every transaction accounted for")
        st.caption("The pipeline stops if these don't add up: landed = duplicates + quarantined + already loaded + "
                   "inserted. Batch 2 includes 10 September transactions re-sent by the source, which the MERGE "
                   "recognises and does not insert twice.")
        j = snap.journey()
        def part(value: int, label: str, color: str) -> str:
            return (f'<div style="text-align:center;padding:.4rem .5rem;border-radius:8px;background:{color}">'
                    f'<div style="font-size:1.25rem;font-weight:700">{value:,}</div>'
                    f'<div style="font-size:.72rem;color:#4a5568">{label}</div></div>')

        for r in j.itertuples(index=False):
            balanced = r[1] == sum(r[2:])
            cells = [part(r[1], "landed in Bronze", "#f3e3cf"), "=",
                     part(r[2], "duplicates", "#e9ecef"), "+", part(r[3], "quarantined", "#f8d7da"), "+",
                     part(r[4], "re-sent, already loaded", "#dbe9ff"), "+", part(r[5], "inserted to Silver",
                                                                                  "#d8f3dc")]
            row = "".join(c if c.startswith("<") else f'<div style="font-weight:700;color:#6c757d">{c}</div>'
                          for c in cells)
            st.markdown(f"**Batch {r[0]}** " + pill("balanced" if balanced else "OUT OF BALANCE",
                                                     "green" if balanced else "red")
                        + f'<div style="display:flex;gap:.35rem;align-items:center;margin:.3rem 0 .9rem 0">{row}</div>',
                        unsafe_allow_html=True)
    with right:
        st.subheader("Daily transaction volume (Gold)")
        daily = snap.daily.groupby(["txn_date", "direction"], as_index=False).total_amount_usd.sum()
        st.altair_chart(alt.Chart(daily).mark_area(opacity=.75).encode(
            x=alt.X("txn_date:T", title=None), y=alt.Y("total_amount_usd:Q", title="USD", stack=True),
            color=alt.Color("direction:N", scale=alt.Scale(range=["#2e7d32", "#13315c"])),
            tooltip=["txn_date:T", "direction", alt.Tooltip("total_amount_usd:Q", format=",.0f")],
        ).properties(height=300), use_container_width=True)

# --------------------------------------------------------------- data quality
with tabs[1]:
    st.subheader("Data-quality scorecard (Gold)")
    st.caption("Rules are declared once as SQL expressions with an action: **warn** keeps the row and flags it, "
               "**drop** moves it to quarantine with the rule name, **fail** stops the pipeline. "
               "Every planted defect in the synthetic data was caught, and nothing else was.")
    sc = snap.scorecard.copy()
    sc["status"] = sc["status"].map(lambda v: v)
    order = {"QUARANTINED": 0, "WARN": 1, "PASS": 2}
    sc = sc.sort_values(by=["status", "table_name"], key=lambda col: col.map(order) if col.name == "status" else col)
    cols = st.columns(3)
    for col, status in zip(cols, ["QUARANTINED", "WARN", "PASS"], strict=True):
        col.markdown(pill(status, STATUS_COLOR[status]) + f" **{(sc.status == status).sum()} rules**",
                     unsafe_allow_html=True)
    st.dataframe(sc[["status", "table_name", "rule_name", "action", "rows_checked", "rows_failed", "pass_rate_pct",
                     "description"]], hide_index=True, use_container_width=True,
                 column_config={"pass_rate_pct": st.column_config.ProgressColumn(
                     "pass rate %", min_value=99.0, max_value=100.0, format="%.3f")})
    fails = sc[sc.rows_failed > 0]
    st.altair_chart(alt.Chart(fails).mark_bar().encode(
        x=alt.X("rows_failed:Q", title="Records failing"), y=alt.Y("rule_name:N", sort="-x", title=None),
        color=alt.Color("action:N", scale=alt.Scale(domain=["drop", "warn"], range=["#c0392b", "#e0a800"])),
        tooltip=["table_name", "rule_name", "action", "rows_failed"]).properties(height=260),
        use_container_width=True)

    q1, q2 = st.columns([1.4, 1])
    with q1:
        st.subheader("Quarantine explorer")
        which = st.radio("Table", ["transactions", "customers", "accounts"], horizontal=True)
        q = snap.quarantine[which]
        show = [c for c in ["_dq_failed_rules", "txn_id", "customer_id", "account_id", "amount_raw", "txn_ts_raw",
                            "currency", "kyc_risk_rating_raw", "_batch_id"] if c in q.columns]
        st.dataframe(q[show].rename(columns={"_dq_failed_rules": "failed rule(s)"}), hide_index=True,
                     use_container_width=True)
        st.caption("Raw values are kept exactly as received so a data steward can fix them at source.")
    with q2:
        st.subheader("Blocking controls")
        st.caption("If any of these fails, the step is logged as FAILED and nothing downstream is refreshed.")
        for _, r in snap.controls().iterrows():
            ok = r.rows_failed == 0
            st.markdown(pill("PASS" if ok else "FAIL", "green" if ok else "red")
                        + f" `{r.rule_name}`<br><span style='font-size:.82rem;color:#4a5568'>{r.description}</span>",
                        unsafe_allow_html=True)

# ------------------------------------------------------------------ AML alerts
with tabs[2]:
    st.subheader("AML alert candidates")
    st.info("These are **candidates for an investigator**, not decisions. Every alert starts as PENDING_REVIEW. "
            "Rules and thresholds are illustrative for a synthetic bank, not regulatory guidance.", icon="⚖️")
    a = snap.alerts.copy()
    a["rule"] = a.rule_code.map(RULE_LABEL)
    c = st.columns(len(RULE_LABEL))
    for col, (code, label) in zip(c, RULE_LABEL.items(), strict=True):
        col.metric(label, int((a.rule_code == code).sum()))
    st.dataframe(a[["priority", "rule", "customer_id", "alert_date", "amount_usd", "txn_count", "rationale",
                    "status"]].sort_values(["priority", "alert_date"]),
                 hide_index=True, use_container_width=True,
                 column_config={"amount_usd": st.column_config.NumberColumn("amount USD", format="%,.2f"),
                                "alert_date": st.column_config.DateColumn("date")})

    options = {f"{r.customer_id} · {RULE_LABEL[r.rule_code]} · {r.alert_date:%d %b}": r.alert_id
               for r in a.sort_values("alert_date").itertuples()}
    default = next((i for i, v in enumerate(options) if "Structuring" in v), 0)
    pick = st.selectbox("Investigate an alert", list(options), index=default)
    alert = a[a.alert_id == options[pick]].iloc[0]
    prof = snap.customer_profile(alert.customer_id)
    left, right = st.columns([1, 2])
    with left:
        st.markdown(f"#### Customer {alert.customer_id}")
        st.markdown(pill(f"KYC risk {prof.get('kyc_risk_rating')}",
                         {"HIGH": "red", "MEDIUM": "amber"}.get(prof.get("kyc_risk_rating"), "green"))
                    + pill(prof.get("segment", ""), "blue") + pill(f"priority {alert.priority}", "red"
                                                                   if alert.priority == "HIGH" else "amber"),
                    unsafe_allow_html=True)
        st.write(f"**Why:** {alert.rationale}")
        st.write(f"Country {prof.get('country')} · branch region {prof.get('region')} · "
                 f"{prof.get('accounts_active')} active account(s): {prof.get('products')}")
        st.write(f"Last 30 days: inflow {prof.get('inflow_usd_30d', 0):,.0f} USD, outflow "
                 f"{prof.get('outflow_usd_30d', 0):,.0f} USD, cash {prof.get('cash_usd_30d', 0):,.0f} USD")
        st.caption("Names, e-mail and phone are not in Gold (data minimisation). An investigator with KYC "
                   "access would see them in Silver, behind a Unity Catalog column mask.")
    with right:
        act = snap.customer_activity(alert.customer_id, alert.alert_date)
        flagged = set(snap.alert_transactions(alert.alert_id).txn_id)
        act = act.assign(flagged=act.txn_id.isin(flagged).map({True: "in this alert", False: "other activity"}))
        st.altair_chart(alt.Chart(act).mark_circle(size=120, opacity=.85).encode(
            x=alt.X("txn_ts:T", title="Customer activity within ±7 days of the alert",
                    axis=alt.Axis(format="%d %b", tickCount="day")),
            y=alt.Y("amount_usd:Q", title="USD"),
            color=alt.Color("flagged:N", scale=alt.Scale(domain=["in this alert", "other activity"],
                                                         range=["#c0392b", "#adb5bd"])),
            shape="direction:N",
            tooltip=["txn_id", "txn_type", "channel", alt.Tooltip("amount_usd:Q", format=",.2f"),
                     "counterparty_country", "txn_ts:T"]).properties(height=260), use_container_width=True)
        st.dataframe(snap.alert_transactions(alert.alert_id)[[
            "txn_id", "txn_ts", "txn_type", "channel", "amount", "currency", "amount_usd", "counterparty_name",
            "counterparty_country", "counterparty_risk_tier"]], hide_index=True, use_container_width=True)
    with st.expander("The Spark SQL that produced these alerts"):
        st.code(gold_sql("aml_alert_candidates"), language="sql")

# --------------------------------------------------------------- SCD2 history
with tabs[3]:
    st.subheader("Slowly Changing Dimension Type 2: nothing is overwritten")
    st.caption("Batch 2 changed 40 customers (risk rating, segment or country). Instead of overwriting, the MERGE "
               "closed the old row (effective_to) and inserted a new current row, so you can always answer "
               "'what was this customer's risk rating on the day of the transaction?'. Five customers were re-sent "
               "unchanged; a hash comparison spotted that and added no new version.")
    hist = snap.scd2
    tracked = ["kyc_risk_rating", "segment", "country", "pep_flag", "branch_code"]
    labels = {}
    for cid, grp in hist.sort_values("effective_from").groupby("customer_id"):
        old_v, new_v = grp.iloc[0], grp.iloc[-1]
        diff = [f"{c.replace('kyc_risk_rating', 'risk')} {old_v[c]}→{new_v[c]}"
                for c in tracked if old_v[c] != new_v[c]]
        labels[f"{cid} · {', '.join(diff)}"] = cid
    keys = sorted(labels, key=lambda k: (0 if " risk " in f" {k}" else 1, k))
    cust = labels[st.selectbox("Customer that changed in batch 2", keys)]
    one = hist[hist.customer_id == cust].sort_values("effective_from")
    before, after = one.iloc[0], one.iloc[-1]
    b_col, arrow, a_col = st.columns([5, 1, 5])

    def version_card(col, row, title: str, color: str) -> None:
        lines = "".join(
            f"<div style='display:flex;justify-content:space-between;padding:.15rem 0;"
            f"{'font-weight:700;color:#842029' if before[c] != after[c] else ''}'><span>{c}</span>"
            f"<span>{row[c]}</span></div>" for c in tracked)
        to = row.effective_to if isinstance(row.effective_to, str) else "open (current)"
        col.markdown(f"<div class='lh-card' style='border-top:5px solid {color}'><h4>{title}</h4>"
                     f"<p>effective {row.effective_from} → {to}</p>{lines}</div>", unsafe_allow_html=True)

    version_card(b_col, before, "Version 1 · closed by the MERGE", "#adb5bd")
    arrow.markdown("<div style='font-size:2.2rem;text-align:center;padding-top:3rem;color:#13315c'>→</div>",
                   unsafe_allow_html=True)
    version_card(a_col, after, "Version 2 · current", "#2e7d32")
    st.caption("Changed attributes are highlighted. Gold's segment KPIs join transactions to the version that was "
               "valid on the transaction date (an 'as-of' join), so history is reported correctly.")
    st.markdown("**Change Data Feed:** the row-level changes the batch-2 MERGE wrote to `silver.customers`, "
                "read back from Delta's change feed (what a downstream system would consume incrementally).")
    ctype = st.multiselect("Change types", ["insert", "update_preimage", "update_postimage"],
                           default=["update_preimage", "update_postimage"])
    cdf = snap.cdf_customers[snap.cdf_customers._change_type.isin(ctype)]
    st.dataframe(cdf, hide_index=True, use_container_width=True, height=260)

# ------------------------------------------------------------------ Delta Lake
with tabs[4]:
    st.subheader("Delta Lake transaction log: history and time travel")
    table = st.radio("Table", list(snap.history), horizontal=True)
    hf = snap.history_frame(table)
    st.dataframe(hf, hide_index=True, use_container_width=True)
    versions = hf.version.tolist()
    v = st.select_slider("Time travel: read the table AS OF version", options=versions, value=versions[0])
    row = hf[hf.version == v].iloc[0]
    st.metric(f"{table} rows at version {v} ({row.operation})", f"{int(row.rows_at_version):,}")
    st.code(f"SELECT COUNT(*) FROM {table} VERSION AS OF {v}  -- = {int(row.rows_at_version):,}", language="sql")
    st.markdown(
        "- **WRITE** (v0) is the September load. **MERGE** (v1) applies batch 2: inserts, SCD2 updates and upserts "
        "in one atomic commit; readers never see a half-applied batch.\n"
        "- **OPTIMIZE ... ZORDER BY** compacts small files and co-locates rows by `customer_id` so investigator "
        "queries skip files. Row counts don't change; only the file layout does.\n"
        "- **Schema enforcement**: an append with the wrong column types is rejected (covered by an automated test).")

# ------------------------------------------------------------------ SQL console
with tabs[5]:
    st.subheader("Query the lakehouse yourself")
    st.caption("DuckDB in your browser session, loaded with the snapshot tables. Locked down: read-only "
               "statements, no file or network access, max 1,000 rows returned.")
    con = console()
    ex = st.selectbox("Example queries", list(EXAMPLE_QUERIES))
    sql = st.text_area("SQL", EXAMPLE_QUERIES[ex], height=150, key=f"sql_{ex}")
    st.caption("Tables: " + ", ".join(f"`{t}`" for t in SQL_TABLES))
    if st.button("Run query", type="primary"):
        try:
            res = run_query(con, sql)
            st.success(f"{len(res):,} rows")
            st.dataframe(res, hide_index=True, use_container_width=True)
        except QueryError as exc:
            st.error(str(exc))

# ------------------------------------------------------------------ Gold tables
with tabs[6]:
    names = {"customer_360": snap.customer_360, "aml_alert_candidates": snap.alerts,
             "segment_monthly_kpis": snap.kpis, "daily_transaction_summary": snap.daily,
             "dq_scorecard": snap.scorecard}
    name = st.radio("Gold table", list(names), horizontal=True)
    df = names[name]
    st.write(f"**{len(df):,} rows**")
    st.dataframe(df, hide_index=True, use_container_width=True, height=320)
    st.download_button("Download CSV", df.to_csv(index=False).encode(), file_name=f"{name}.csv")
    with st.expander("Spark SQL definition", expanded=True):
        st.code(gold_sql(name), language="sql")
    if name == "segment_monthly_kpis":
        k = snap.kpis.assign(month=snap.kpis.month.dt.strftime("%b %Y"))
        st.altair_chart(alt.Chart(k).mark_bar().encode(
            x=alt.X("segment:N", title=None), y=alt.Y("volume_usd:Q", title="Volume USD"),
            color="month:N", xOffset="month:N", tooltip=list(k.columns)).properties(height=260),
            use_container_width=True)

# ------------------------------------------------------------------ how it works
with tabs[7]:
    st.subheader("Architecture")
    cols = st.columns(4)
    cards = [
        ("lh-src", "Landing", "Synthetic core-banking extracts (CSV + JSON lines) with a manifest of record counts "
                              "and a control total, written to a folder or a Unity Catalog volume."),
        ("lh-bronze", "Bronze", "Raw copy, every column as text, plus source file, batch id and load time. "
                                "Idempotent: a batch is never loaded twice. Manifest reconciliation is a blocking "
                                "control."),
        ("lh-silver", "Silver", "PySpark: typing with try_cast, de-duplication, 18 declarative DQ rules, quarantine, "
                                "PII hashing/masking, FX to USD, SCD2 and insert-only MERGEs, reconciliation."),
        ("lh-gold", "Gold", "Spark SQL: AML alert candidates, customer 360, daily and monthly KPIs, DQ scorecard. "
                            "Cross-layer reconciliation, then OPTIMIZE + Z-ORDER."),
    ]
    for col, (cls, title, text) in zip(cols, cards, strict=True):
        col.markdown(f'<div class="lh-card {cls}"><h4>{title}</h4><p>{text}</p></div>', unsafe_allow_html=True)
    st.markdown("""
#### Runs in three places, same code
| Where | How | Status |
|---|---|---|
| Laptop / CI | `run_pipeline.py` (PySpark + delta-spark) | Verified: produced this snapshot; tests in CI |
| Databricks Free Edition | `databricks/00_run_pipeline`, Unity Catalog | Verified: serverless, same table counts |
| Lakeflow Jobs | `databricks/01_create_job`: 6-task DAG, serverless | Code ready; not yet run |

A `TableStore` class is the only place that knows whether a table is a local Delta folder or a Unity Catalog
table, so the PySpark and SQL are identical everywhere.

#### What is verified vs not
- **Verified:** the full pipeline on local Spark 4.1 + Delta 4.4; every planted defect, duplicate, re-send, SCD2
  change and AML scenario is found exactly (tests compare against the generator's answer key); time travel, change
  data feed, schema enforcement, idempotent re-runs and blocking controls are covered by tests.
- **Verified on Databricks:** the run-all notebook on Free Edition (serverless, Unity Catalog), with the same
  table counts as the local run (16,644 Silver transactions, 25 quarantined).
- **Not yet verified:** Unity Catalog column masks and tags, and the scheduled 6-task job. They will be marked
  verified here only after a real run.
""")
    st.markdown("Source code: [github.com/nipunrg1990rocks-dev/banking-ai-portfolio]"
                "(https://github.com/nipunrg1990rocks-dev/banking-ai-portfolio/tree/main/projects/p2_lakehouse)")

st.markdown(FOOTER, unsafe_allow_html=True)
