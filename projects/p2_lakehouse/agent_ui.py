"""Streamlit views for the AI agents: Mission Control, the two investigators, Ask Ledgerline.

Kept out of ``app.py`` so the lakehouse explorer and the agent experience stay readable.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from agents import AgentRun, AuditLog, ClaudeBrain, SnapshotBackend, run_agent
from agents.specs import ASK_INTENTS, SPECS

from portfolio_core.llm import live_llm_available
from portfolio_core.ui import pill

RUNS_DIR = Path(__file__).resolve().parent / "data" / "agent_runs"
RECORDED, OFFLINE, LIVE = "Claude · recorded run", "Offline planner · no LLM", "Claude · live"
TOOL_ICON = {"get_alert": "🚨", "get_transactions": "💳", "get_customer_profile": "🪪", "get_customer_activity": "📈",
             "compare_to_peers": "👥", "find_counterparty_history": "🔁", "counterparty_network": "🕸️",
             "run_sql": "🧮", "list_tables": "🗂️", "get_pipeline_status": "🛰️", "get_dq_findings": "🧪",
             "inspect_delivery": "🔬", "get_delivery_history": "📦", "submit_case": "📝",
             "submit_incident_report": "📝", "submit_answer": "✅"}

AGENT_CSS = """
<style>
  .mc { background: radial-gradient(circle at 20% 0%, #163a6b 0%, #0b1d36 55%, #08152a 100%); color: #e8f0fb;
        border-radius: 14px; padding: 1.1rem 1.3rem 1.2rem; margin-bottom: .8rem; }
  .mc h3 { color: #fff; margin: 0 0 .2rem 0; font-size: 1.15rem; letter-spacing: .02em; }
  .mc .sub { color: #a9bfdc; font-size: .85rem; margin-bottom: .8rem; }
  .flow { display: flex; gap: .5rem; align-items: stretch; flex-wrap: wrap; }
  .stage { flex: 1 1 130px; border-radius: 10px; padding: .6rem .7rem; background: rgba(255,255,255,.06);
           border: 1px solid rgba(255,255,255,.12); }
  .stage .t { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: #a9bfdc; }
  .stage .n { font-size: 1.35rem; font-weight: 700; color: #fff; }
  .stage .d { font-size: .75rem; color: #c9d7ea; }
  .stage.ok { box-shadow: inset 0 3px 0 #3ddc97; } .stage.bad { box-shadow: inset 0 3px 0 #ff5d5d;
              background: rgba(255,93,93,.12); }
  .arrow { align-self: center; color: #6f8db5; font-size: 1.2rem; }
  .dot { display: inline-block; width: .55rem; height: .55rem; border-radius: 50%; margin-right: .35rem; }
  .dot.g { background: #3ddc97; box-shadow: 0 0 8px #3ddc97; } .dot.r { background: #ff5d5d; box-shadow: 0 0 8px #ff5d5d; }
  .dot.a { background: #ffc857; box-shadow: 0 0 8px #ffc857; }
  .inbox { border: 1px solid rgba(19,49,92,.15); border-radius: 12px; padding: .8rem 1rem; height: 100%; }
  .inbox h4 { margin: .1rem 0 .3rem 0; font-size: 1rem; }
  .inbox p { margin: 0; font-size: .86rem; color: #4a5568; }
  .step { border-left: 3px solid #c3d0e0; padding: .25rem .8rem; margin: .25rem 0; font-size: .88rem; }
  .step.thought { border-color: #9aa9bd; color: #44546a; font-style: italic; }
  .step.tool { border-color: #13315c; } .step.bad { border-color: #c0392b; }
  .step .tn { font-weight: 700; font-family: ui-monospace, monospace; font-size: .82rem; }
  .step .ms { color: #8a96a3; font-size: .75rem; }
  .case { border-radius: 12px; padding: 1rem 1.2rem; border: 1px solid rgba(19,49,92,.15); }
  .case.esc { box-shadow: inset 5px 0 0 #c0392b; } .case.close { box-shadow: inset 5px 0 0 #2e7d32; }
  .case h4 { margin: 0 0 .4rem 0; }
  .ticket { font-family: ui-monospace, monospace; font-size: .82rem; background: #f6f8fb; border-radius: 8px;
            padding: .8rem 1rem; white-space: pre-wrap; border: 1px dashed #b8c4d4; }
  .brainnote { font-size: .8rem; color: #4a5568; }
</style>
"""


# ------------------------------------------------------------------- state
@st.cache_resource
def backend() -> SnapshotBackend:
    return SnapshotBackend()


def audit() -> AuditLog:
    if "audit" not in st.session_state:
        st.session_state.audit = AuditLog()
    return st.session_state.audit


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def recording_path(agent: str, key: str) -> Path:
    return RUNS_DIR / agent / f"{_slug(key)}.json"


def load_recording(agent: str, key: str) -> AgentRun | None:
    path = recording_path(agent, key)
    return AgentRun.from_dict(json.loads(path.read_text())) if path.exists() else None


def recordings_exist() -> bool:
    return RUNS_DIR.exists() and any(RUNS_DIR.glob("*/*.json"))


def brain_modes() -> list[str]:
    modes = ([RECORDED] if recordings_exist() else []) + [OFFLINE]
    return modes + ([LIVE] if live_llm_available() else [])


def brain_note(mode: str) -> str:
    return {
        RECORDED: "Replaying a real Claude run recorded with run_agents.py: every thought, tool call and result is "
                  "exactly what the model did. No API call is made now.",
        OFFLINE: "The offline planner is a fixed investigation playbook written in Python with rule-based scoring. It "
                 "calls the same tools through the same engine, but it is <b>not an LLM</b>.",
        LIVE: "Claude is called live through the Anthropic API for this run. Each run costs a few cents.",
    }[mode]


def dispatch(agent: str, mode: str, key: str, **context) -> AgentRun:
    if mode == RECORDED:
        run = load_recording(agent, key)
        if run:
            audit().record("agent_run_replayed", {"agent": agent, "recorded_run": run.run_id, "model": run.model},
                           actor="app", correlation_id=run.run_id)
            return run
        st.toast("No recorded Claude run for this one yet; using the offline planner.", icon="ℹ️")
    brain = ClaudeBrain() if mode == LIVE else None
    return run_agent(agent, backend(), brain, audit(), **context)


# ------------------------------------------------------------------ traces
def _result_summary(result: dict | None) -> str:
    if not result:
        return ""
    if "error" in result:
        return f"⚠️ {result['error']}"
    if "row_count" in result:
        return f"{result['row_count']} row(s)"
    if "steps_run" in result:
        failed = result.get("failed_steps") or []
        return f"{result['steps_run']} steps, {len(failed)} failed" + (
            f" (batch {failed[0].get('batch_id')})" if failed else "")
    for k in ("transactions", "customers", "alerts", "checks", "deliveries"):
        if isinstance(result.get(k), list):
            return f"{len(result[k])} {k}"
    if "profile" in result:
        p = result["profile"]
        return f"{p.get('segment')} · KYC {p.get('kyc_risk_rating')} · PEP {p.get('pep_flag')} · {p.get('country')}"
    if "files" in result:
        bad = [n for n, f in result["files"].items() if not f.get("checksum_matches", True)]
        return "checksum mismatch: " + ", ".join(bad) if bad else "all files match the manifest"
    if "metrics" in result:
        return f"{result.get('segment')} peers: {result.get('peers')}"
    if result.get("status") == "submitted":
        return "submitted for human review"
    return ", ".join(list(result)[:4])


def _step_html(step) -> str:
    if step.kind == "thought":
        return f'<div class="step thought">💭 {step.text}</div>'
    if step.kind == "tool":
        icon = TOOL_ICON.get(step.tool, "🔧")
        args = ", ".join(f"{k}={json.dumps(v)[:70]}" for k, v in (step.args or {}).items()
                         if step.tool not in ("submit_case", "submit_incident_report", "submit_answer"))
        return (f'<div class="step tool {"" if step.ok else "bad"}">{icon} <span class="tn">{step.tool}</span>'
                f'(<span style="font-size:.78rem">{args}</span>) <span class="ms">{step.ms} ms</span><br>'
                f'<span style="color:#4a5568">→ {_result_summary(step.result)}</span></div>')
    if step.kind == "error":
        return f'<div class="step bad">🛑 {step.text}</div>'
    return ""


def render_trace(run: AgentRun, animate: bool, label: str) -> None:
    steps = [s for s in run.steps if s.kind in ("thought", "tool", "error")]
    with st.status(label, expanded=True, state="running" if animate else "complete") as box:
        holder = st.empty()
        shown = []
        for step in steps:
            shown.append(_step_html(step))
            if animate:
                holder.markdown("".join(shown), unsafe_allow_html=True)
                time.sleep(0.35 if step.kind == "thought" else 0.55)
        holder.markdown("".join(shown), unsafe_allow_html=True)
        meta = (f"{run.tool_calls} tool calls · {run.duration_s:.1f}s · brain: {run.brain}"
                + (f" ({run.model}) · {run.input_tokens + run.output_tokens:,} tokens · ${run.cost_usd:.3f}"
                   if run.model else ""))
        box.update(label=f"{label}: {run.status} · {meta}", expanded=True,
                   state="complete" if run.status == "completed" else "error")
        with st.popover("Raw tool results"):
            for s in run.steps:
                if s.kind == "tool":
                    st.markdown(f"**{s.tool}**")
                    st.json(s.result, expanded=False)


def decision_buttons(kind: str, run: AgentRun, proposal: str) -> None:
    decided = st.session_state.setdefault("decisions", {})
    key = f"{kind}:{run.run_id}"
    if key in decided:
        d = decided[key]
        st.success(f"Decision recorded: **{d['decision']}** by {d['by']} · audit event #{d['seq']}"
                   + (f" · reason: {d['reason']}" if d.get("reason") else ""))
        return
    reason = st.text_input("Reason (required to override)", key=f"reason-{key}",
                           placeholder="e.g. RM confirmed the payroll contract")
    c1, c2, _ = st.columns([1, 1, 2])
    choice = None
    if c1.button(f"✅ Approve: {proposal}", key=f"ok-{key}", type="primary"):
        choice = f"APPROVED ({proposal})"
    if c2.button("✋ Override", key=f"no-{key}", disabled=not reason.strip()):
        choice = "OVERRIDDEN"
    if choice:
        ev = audit().record("human_decision", {"kind": kind, "agent_run": run.run_id, "decision": choice,
                                               "reason": reason.strip() or None, "agent_output": run.output},
                            actor="human:reviewer", correlation_id=run.run_id)
        decided[key] = {"decision": choice, "by": "you (demo reviewer)", "seq": ev.seq, "reason": reason.strip()}
        st.rerun()


# ------------------------------------------------------------ mission control
def mission_control(snap, h: dict) -> None:
    s = snap.summary
    inc = json.loads((snap_dir() / "incident.json").read_text())["incident"]
    counts = s["table_counts"]
    stage = ('<div class="stage {cls}"><div class="t">{t}</div><div class="n">{n}</div><div class="d">{d}</div></div>')
    flow = "".join([
        stage.format(cls="ok", t="Landing · 2 batches", n=f"{h['source_rows']:,}", d="source records + manifests"),
        '<div class="arrow">➜</div>',
        stage.format(cls="ok", t="Bronze", n=f"{counts['bronze.transactions']:,}", d="raw transactions, as delivered"),
        '<div class="arrow">➜</div>',
        stage.format(cls="ok", t="Silver", n=f"{counts['silver.transactions']:,}",
                     d=f"{h['quarantined']} quarantined · {h['duplicates_removed']} duplicates removed"),
        '<div class="arrow">➜</div>',
        stage.format(cls="ok", t="Gold", n=f"{h['alerts']} alerts", d=f"{h['gold_tables']} business tables"),
        '<div class="arrow">⚡</div>',
        stage.format(cls="bad", t=f"Batch {inc['batch_id']}", n="BLOCKED",
                     d="truncated file stopped at Bronze; nothing downstream changed"),
    ])
    st.markdown(
        f'<div class="mc"><h3><span class="dot g"></span>Ledgerline lakehouse · overnight run {s["run_id"]}</h3>'
        f'<div class="sub">{s["engine"]} · Spark {s["spark_version"]} · Delta {s["delta_version"]} · '
        f'{h["controls_passed"]}/{h["controls"]} blocking controls passed · '
        f'<span class="dot r"></span>{h["controls_tripped"]} tripped (incident) · '
        f'<span class="dot a"></span>{h["alerts"]} AML alerts awaiting review</div>'
        f'<div class="flow">{flow}</div></div>', unsafe_allow_html=True)

    left, right = st.columns([1.15, 1])
    with left:
        st.markdown("#### Replay last night's run")
        if st.button("▶ Replay the pipeline run", key="replay"):
            steps = s["steps"]
            groups = []
            for st_ in steps:
                g = (st_["step"], st_["batch_id"])
                if not groups or groups[-1][0] != g:
                    groups.append((g, []))
                groups[-1][1].append(st_)
            with st.status("Running Ledgerline overnight pipeline…", expanded=True) as box:
                for (step, batch), rows in groups:
                    failed = any(r["status"] != "SUCCEEDED" for r in rows)
                    written = sum(r["rows_written"] or 0 for r in rows)
                    quarantined = sum(r["rows_quarantined"] or 0 for r in rows)
                    label = f"**{step}**" + (f" · batch {batch}" if batch else "")
                    if failed:
                        err = json.loads(rows[0]["details"] or "{}").get("error", "")
                        st.markdown(f"🛑 {label} · BLOCKED: `{err[:140]}`")
                    else:
                        st.markdown(f"✅ {label} · {len(rows)} table(s) · {written:,} rows written"
                                    + (f" · {quarantined} quarantined" if quarantined else ""))
                    time.sleep(0.45)
                box.update(label="Run finished: 2 batches published, 1 delivery blocked at Bronze", state="error")
        st.caption("Every step is read from the real run log (`audit.pipeline_steps`) of the run that produced this "
                   "app's data.")
    with right:
        st.markdown("#### Agent inbox")
        c1, c2 = st.columns(2)
        c1.markdown('<div class="inbox"><h4>🛠️ Data Steward</h4><p><span class="dot r"></span>1 incident open: '
                    f'batch {inc["batch_id"]} blocked. Open the <b>Data Steward</b> tab to dispatch the agent.</p>'
                    '</div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="inbox"><h4>🕵️ AML Investigator</h4><p><span class="dot a"></span>{h["alerts"]} '
                    'alerts pending. Open the <b>AML Investigator</b> tab to work them.</p></div>',
                    unsafe_allow_html=True)
        st.markdown("#### Audit trail (this session)")
        log = audit()
        ok, broken = log.verify_chain()
        st.markdown(pill(f"hash chain {'verified' if ok else f'BROKEN at {broken}'}", "green" if ok else "red")
                    + pill(f"{log.count()} events", "grey"), unsafe_allow_html=True)
        ev = log.events(limit=8)
        if ev:
            st.dataframe(pd.DataFrame([{"#": e.seq, "actor": e.actor, "event": e.event_type,
                                        "hash": e.hash[:12]} for e in ev]), hide_index=True,
                         use_container_width=True, height=250)
        else:
            st.caption("Dispatch an agent or approve a recommendation: every step lands here, hash-chained.")


def snap_dir() -> Path:
    return backend().folder


# ------------------------------------------------------------------ AML
def aml_tab(snap, mode: str, animate: bool) -> None:
    st.markdown(f"**{SPECS['aml_investigator'].title}.** {SPECS['aml_investigator'].role}")
    st.markdown(f'<div class="brainnote">{brain_note(mode)}</div>', unsafe_allow_html=True)
    a = snap.alerts.sort_values(["priority", "alert_date"])
    options = {f"{r.priority} · {r.rule_code.replace('_', ' ').title()} · {r.customer_id} · "
               f"{r.alert_date:%d %b} · {r.amount_usd:,.0f} USD": r.alert_id for r in a.itertuples()}
    pick = st.selectbox("Alert to investigate", list(options), key="aml_pick")
    alert_id = options[pick]
    if st.button("🕵️ Dispatch the AML Investigator", type="primary", key="aml_go"):
        st.session_state.aml_run = (alert_id, mode, dispatch("aml_investigator", mode, alert_id[:12],
                                                                alert_id=alert_id))
        st.session_state.aml_animate = animate
    held = st.session_state.get("aml_run")
    if not held or held[0] != alert_id:
        return
    run = held[2]
    render_trace(run, st.session_state.pop("aml_animate", False), "Investigation")
    out = run.output
    if not out:
        st.error("The agent did not finish. See the trace above.")
        return
    esc = out["recommendation"] == "ESCALATE"
    st.markdown(
        f'<div class="case {"esc" if esc else "close"}"><h4>{"🔴 ESCALATE" if esc else "🟢 CLOSE"} · risk '
        f'{out["risk_score"]}/100</h4><b>{out["headline"]}</b><p style="margin-top:.6rem">{out["narrative"]}</p></div>',
        unsafe_allow_html=True)
    st.progress(min(max(out["risk_score"], 0), 100) / 100, text=f"Risk score {out['risk_score']} (escalate at 50)")
    c1, c2 = st.columns(2)
    c1.markdown("**Red flags**\n" + "\n".join(f"- {f}" for f in out["red_flags"] or ["None"]))
    c2.markdown("**Mitigating factors**\n" + "\n".join(f"- {f}" for f in out["mitigating_factors"] or ["None"]))
    ev = snap.transactions[snap.transactions.txn_id.isin(out["evidence_txn_ids"])]
    st.markdown(f"**Evidence cited** ({len(ev)} transactions, all verified to exist in Silver)")
    st.dataframe(ev[["txn_id", "txn_ts", "txn_type", "channel", "amount_usd", "counterparty_name",
                     "counterparty_country", "counterparty_risk_tier"]], hide_index=True, use_container_width=True)
    st.markdown("**Proposed next steps**\n" + "\n".join(f"- {n}" for n in out["next_steps"]))
    st.markdown("##### Your decision")
    decision_buttons("aml_case", run, out["recommendation"])


def triage_board(mode: str) -> None:
    st.markdown("#### Work the whole queue")
    st.caption("Runs the investigator on every alert and scores it against the answer key planted in the synthetic "
               "data: 7 genuine scenarios, and 1 false positive (a corporate payroll cycle) that should be closed.")
    if st.button("Run on all alerts and score against the answer key", key="triage"):
        from agents.evals import evaluate

        if mode == RECORDED and (RUNS_DIR / "eval_claude.json").exists():
            st.session_state.triage_result = json.loads((RUNS_DIR / "eval_claude.json").read_text())
        else:
            with st.spinner("Investigating every alert…"):
                res = evaluate(backend(), ClaudeBrain() if mode == LIVE else None, agents=("aml_investigator",))
            st.session_state.triage_result = {"aml_investigator": {"summary": res[0].summary(), "cases": res[0].cases}}
    t = st.session_state.get("triage_result", {}).get("aml_investigator")
    if t:
        sm = t["summary"]
        st.markdown(pill(f"{sm['passed']}/{sm['cases']} cases fully correct", "green" if sm["passed"] == sm["cases"]
                         else "amber") + pill(f"brain: {sm['brain']}", "grey")
                    + "".join(pill(f"{k} {v}", "blue") for k, v in sm["checks"].items()), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"alert": c["case"], "expected": c["expected"], "agent": c["got"],
                                    "all checks": "✅" if c["pass"] else "❌", "tool calls": c["tool_calls"]}
                                   for c in t["cases"]]), hide_index=True, use_container_width=True)


# --------------------------------------------------------------- steward
def steward_tab(mode: str, animate: bool) -> None:
    inc = json.loads((snap_dir() / "incident.json").read_text())
    i = inc["incident"]
    st.markdown(f"**{SPECS['data_steward'].title}.** {SPECS['data_steward'].role}")
    st.error(f"**Incident:** delivery {i['batch_id']} was **blocked at Bronze**. `{i['error']}`", icon="🛑")
    st.markdown(f'<div class="brainnote">{brain_note(mode)}</div>', unsafe_allow_html=True)
    if st.button("🛠️ Dispatch the Data Steward", type="primary", key="dq_go"):
        st.session_state.dq_run = dispatch("data_steward", mode, "incident")
        st.session_state.dq_animate = animate
    run = st.session_state.get("dq_run")
    if not run:
        return
    render_trace(run, st.session_state.pop("dq_animate", False), "Investigation")
    out = run.output
    if not out:
        st.error("The agent did not finish. See the trace above.")
        return
    c = st.columns(4)
    c[0].metric("Severity", out["severity"])
    c[1].metric("Rows expected", f"{out['rows_expected']:,}")
    c[2].metric("Complete rows received", f"{out['rows_received_complete']:,}")
    c[3].metric("Missing", f"{out['rows_missing']:,}")
    st.markdown(f'<div class="case esc"><h4>Root cause: {out["root_cause_category"]}</h4>{out["root_cause"]}'
                f'<p style="margin-top:.6rem"><b>Impact.</b> {out["downstream_impact"]}</p></div>',
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    c1.markdown("**Evidence**\n" + "\n".join(f"- {e}" for e in out["evidence"]))
    c2.markdown("**Remediation**\n" + "\n".join(f"- {r}" for r in out["remediation"]))
    t = out["ticket"]
    st.markdown("**Draft ticket** (nothing is sent; approval is recorded in the audit trail)")
    st.markdown(f'<div class="ticket">To: {t["assignee_team"]}\nTitle: {t["title"]}\n\n{t["body"]}</div>',
                unsafe_allow_html=True)
    st.markdown("##### Your decision")
    decision_buttons("dq_ticket", run, "raise ticket")


# ------------------------------------------------------------------- ask
SUGGESTED = ["Which 5 customers had the highest cross-border volume in the last 30 days?",
             "How many AML alert candidates are there for each rule?",
             "Show the daily transaction volume trend",
             "Which data-quality rules quarantined transactions, and how many records each?"]


def ask_tab(mode: str, animate: bool) -> None:
    st.markdown(f"**{SPECS['ask_ledgerline'].title}.** {SPECS['ask_ledgerline'].role}")
    st.markdown(f'<div class="brainnote">{brain_note(mode)}'
                + (" Offline it understands these question types: "
                   + ", ".join(i["title"].lower() for i in ASK_INTENTS) + "." if mode == OFFLINE else "")
                + "</div>", unsafe_allow_html=True)
    history = st.session_state.setdefault("chat", [])
    cols = st.columns(len(SUGGESTED))
    clicked = None
    for col, q in zip(cols, SUGGESTED, strict=True):
        if col.button(q, key=f"sugg-{_slug(q)}", use_container_width=True):
            clicked = q
    question = st.chat_input("Ask about customers, transactions, alerts or data quality…") or clicked
    if question:
        run = dispatch("ask_ledgerline", mode, question, question=question)
        history.append((question, run, animate))
    for n, (q, run, anim) in enumerate(history):
        with st.chat_message("user"):
            st.write(q)
        with st.chat_message("assistant", avatar="🏦"):
            last = n == len(history) - 1
            render_trace(run, anim and last, "Thinking")
            history[n] = (q, run, False)
            out = run.output or {}
            st.markdown(out.get("answer", "I couldn't finish this one."))
            if out.get("sql"):
                try:
                    df = backend().sql(out["sql"])
                except Exception as exc:  # noqa: BLE001 - show the query error instead of failing the page
                    st.warning(f"Query failed: {exc}")
                    continue
                chart = out.get("chart") or {}
                if chart.get("type") in ("bar", "line") and {chart["x"], chart["y"]} <= set(df.columns):
                    mark = alt.Chart(df.head(40)).mark_bar() if chart["type"] == "bar" else alt.Chart(df).mark_line(
                        point=True)
                    st.altair_chart(mark.encode(x=alt.X(f"{chart['x']}:{'T' if 'date' in chart['x'] else 'N'}",
                                                        sort="-y" if chart["type"] == "bar" else None),
                                                y=f"{chart['y']}:Q", tooltip=list(df.columns)[:6])
                                    .properties(height=260), use_container_width=True)
                with st.expander("SQL and result"):
                    st.code(out["sql"], language="sql")
                    st.dataframe(df, hide_index=True, use_container_width=True)


# --------------------------------------------------------------- how it works
@st.cache_data(show_spinner="Scoring the offline planner against the answer key…")
def offline_scorecard() -> list[dict]:
    from agents.evals import evaluate

    return [r.summary() for r in evaluate(backend())]


def agents_explainer() -> None:
    st.subheader("How the AI agents work")
    st.markdown("""
Three agents sit on top of the lakehouse. Each one gets a goal, a **tool belt** of read-only tools and a
**submit tool** with a strict schema. A brain decides which tool to call next, reads the result and keeps going
until it submits. Every step is written to a hash-chained audit log, and nothing an agent proposes takes effect
until a person approves it.

| Agent | Goal | Tools it can use | What it submits |
|---|---|---|---|
| AML Investigator | Decide whether an alert should be escalated | alert, transactions, customer profile, 90-day activity, peer comparison, counterparty history and network, SQL | Case: risk score, red flags, mitigating factors, cited evidence, ESCALATE or CLOSE |
| Data Steward | Explain why a pipeline control failed | pipeline status, DQ findings, delivery forensics (bytes, checksum, last line), delivery history, SQL | Incident report: root cause, rows lost, impact, fix, ticket |
| Ask Ledgerline | Answer a business question in plain English | table list, SQL (read-only, row-capped) | Answer, the final SQL and a chart |

**Guardrails:** tools only read; SQL must be a single read-only statement and results are capped at 1,000 rows;
ids are validated before they reach a query; a tool-call limit stops runaway loops and the recording script stops at
a dollar cap; tool errors go back to the brain so it can correct itself; the evaluation checks case evidence against
Silver so invented transaction ids are caught.

**Two brains, same tools and engine:**
- **Claude** (Anthropic API, tool use with adaptive thinking) decides its own steps and writes its own reports.
  The hosted app replays recorded Claude runs, so visitors see exactly what Claude did without any API calls.
- **Offline planner** (no LLM): a fixed playbook in Python with rule-based scoring. It keeps the app working with no
  key and no cost, and gives the evaluation a baseline. It is labelled wherever its output appears.
""")
    st.markdown("#### Evaluation against the planted answer key")
    st.caption("The data generator plants every scenario, so each agent output is scored mechanically: right decision, "
               "trigger transactions cited, no invented ids, score consistent with decision, no contact data, correct "
               "root cause and row counts, and SQL that returns the same result as a reference query.")
    rows = [{"brain": s["brain"], "agent": s["agent"], "cases fully correct": f"{s['passed']}/{s['cases']}",
             **s["checks"]} for s in offline_scorecard()]
    claude_file = RUNS_DIR / "eval_claude.json"
    if claude_file.exists():
        rep = json.loads(claude_file.read_text())
        for name, body in rep.items():
            if isinstance(body, dict) and "summary" in body:
                s = body["summary"]
                rows.append({"brain": f"claude ({rep.get('model')})", "agent": name,
                             "cases fully correct": f"{s['passed']}/{s['cases']}", **s["checks"]})
        st.caption(f"Claude run recorded {rep.get('recorded_at')}, total API spend ${rep.get('spent_usd')}.")
    else:
        st.info("Claude has not been evaluated yet. The scores below are the offline planner only; Claude's row will "
                "appear here after a real recorded run, never before.")
    st.dataframe(pd.DataFrame(rows).fillna(""), hide_index=True, use_container_width=True)
