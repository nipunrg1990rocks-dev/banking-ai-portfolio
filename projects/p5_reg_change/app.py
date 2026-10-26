"""RegRadar: GenAI Regulatory Change Impact Analyzer (Project 05).

Run locally from the repository root:

    streamlit run projects/p5_reg_change/app.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from portfolio_core.audit import AuditLog  # noqa: E402
from regradar import llm as llm_mod  # noqa: E402
from regradar.agents import CREW, DEMO_AS_OF, run_assessment  # noqa: E402
from regradar.ask import LLMAgent, Tools, offline_answer  # noqa: E402
from regradar.corpus import (  # noqa: E402
    CircularError,
    CircularPair,
    parse_circular,
    section_by_ref,
)
from regradar.evaluation import format_report, run_evaluation  # noqa: E402
from regradar.gaps import STATUS_LABEL  # noqa: E402
from regradar.report import assess_all, build_pack  # noqa: E402
from regradar.signoff import SignOffError, Workflow  # noqa: E402
from regradar.ui import (  # noqa: E402
    CSS,
    FOOTER,
    RATING_COLOR,
    STATUS_COLOR,
    change_card,
    chip,
    circular_card,
    crew_html,
    findings_frame,
    heatmap,
    hero,
    horizon_chart,
    kpis,
    log_html,
    trace_graph,
)

st.set_page_config(page_title="RegRadar · Regulatory Change Impact Analyzer", page_icon="🛰️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

SAMPLE_QUESTIONS = [
    "What are the most urgent gaps?",
    "What changed in the SAR rules?",
    "Which controls need to change for KYC?",
    "When does the AI circular take effect?",
    "Which findings affect AML-002?",
    "What does the new regulation say about beneficial ownership?",
]
PASTE_EXAMPLE = """## 1. Complaints Handling

Firms must acknowledge every customer complaint within 2 business days.
Firms must send a final response to a complaint within 15 business days.

## 2. Vulnerable Customers

Firms must record customer vulnerability and review the record at least every 12 months."""


# ------------------------------------------------------------------ state
def init_state() -> None:
    ss = st.session_state
    if "audit" not in ss:
        ss.audit = AuditLog()
    if "assessments" not in ss:
        ss.assessments = assess_all(DEMO_AS_OF)
        ss.workflows = {k: Workflow(a, ss.audit) for k, a in ss.assessments.items()}
        ss.selected = "NPCA-FC-2026-14" if "NPCA-FC-2026-14" in ss.assessments else next(iter(ss.assessments))
        ss.last_log = {}
        ss.chat = []
        ss.llm = None
        ss.llm_error = None


def flash(kind: str, text: str) -> None:
    st.session_state.flash = (kind, text)


def show_flash(slot) -> None:
    msg = st.session_state.pop("flash", None)
    if msg:
        getattr(slot, msg[0])(msg[1])


def current_llm():
    return st.session_state.llm if st.session_state.get("use_llm") else None


init_state()
ss = st.session_state

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### 👤 Who is working")
    analyst = st.text_input("Analyst (prepares and decides actions)", value="Asha Patel")
    approver = st.text_input("Approver (signs off; must be a different person)", value="",
                             placeholder="e.g. Daniel Okafor")
    st.markdown("### 🧠 AI engine")
    if llm_mod.databricks_configured():
        use = st.toggle("Use Databricks foundation model (live)", key="use_llm")
        if use and ss.llm is None and ss.llm_error is None:
            with st.spinner("Finding a model endpoint in the workspace…"):
                try:
                    ss.llm = llm_mod.from_secrets()
                except llm_mod.LLMError as exc:
                    ss.llm_error = str(exc)
        if use and ss.llm is not None:
            st.success(f"Live: {ss.llm.label}. The Challenger asks it for a second opinion; the Reporter uses it "
                       "for the summary; Ask RegRadar uses it with tools.")
        elif use and ss.llm_error:
            st.error(f"Live AI unavailable: {ss.llm_error}. Running offline.")
            if st.button("Retry connection"):
                ss.llm_error = None
                st.rerun()
    else:
        st.info("Offline engine: rules, retrieval and drafting with no LLM and no cost. Add the Databricks secrets "
                "(DATABRICKS_HOST, DATABRICKS_TOKEN) to switch on the live model.")
    st.markdown("### 🎬 Presentation")
    speed = st.select_slider("Agent replay speed", options=["Instant", "Fast", "Presenter"], value="Fast")
    if st.button("Reset demo"):
        for key in list(ss.keys()):
            del ss[key]
        st.rerun()
    st.caption("Synthetic data only. Ledgerline Bank and the NPCA are fictional.")

DELAY = {"Instant": 0.0, "Fast": 0.05, "Presenter": 0.35}[speed]

# ------------------------------------------------------------------ hero + horizon
st.markdown(hero(), unsafe_allow_html=True)
flash_slot = st.container()
show_flash(flash_slot)

A = ss.assessments
total_gaps = sum(len(a.gaps) for a in A.values())
critical = sum(a.rating_counts().get("Critical", 0) for a in A.values())
st.markdown(kpis([
    (len(A), "circulars on the radar", "blue"),
    (sum(len([c for c in a.changes if c.change_type != "UNCHANGED"]) for a in A.values()), "changes detected", "blue"),
    (total_gaps, "policy gaps", "amber"),
    (critical, "critical gaps", "red"),
    (sum(len(a.actions) for a in A.values()), "draft actions", "green"),
    (len({c for a in A.values() for c in a.impacted_controls()}), "controls to change or review", "amber"),
]), unsafe_allow_html=True)

cols = st.columns(len(A))
for col, (key, a) in zip(cols, A.items(), strict=True):
    with col:
        st.markdown(circular_card(a, DEMO_AS_OF, key == ss.selected), unsafe_allow_html=True)
        if st.button("Open" if key != ss.selected else "Selected ✓", key=f"open-{key}", use_container_width=True,
                     disabled=key == ss.selected):
            ss.selected = key
            st.rerun()

with st.expander("📡 Regulatory horizon: when each circular bites, and which policies it hits", expanded=False):
    h1, h2 = st.columns(2)
    h1.altair_chart(horizon_chart(A), use_container_width=True)
    h2.altair_chart(heatmap(A), use_container_width=True)
    st.caption(f"As-of date {DEMO_AS_OF.isoformat()} (fixed so the demo is reproducible). Bubble size = gaps; "
               "colour = critical gaps. Heatmap numbers = gaps per policy.")

key = ss.selected
a = A[key]
wf: Workflow = ss.workflows[key]
circ = a.circular

st.markdown(f"## {circ.circular_id} · {circ.title}")
st.caption(f"{circ.regulator} · published {circ.published or 'n/a'} · effective {circ.effective_date or 'n/a'} · "
           + (f"replaces {a.pair.previous.circular_id}" if a.pair.previous else "new circular")
           + f" · engine: {a.llm_label or 'offline rules engine'} · run {a.run_id}")

tabs = st.tabs(["🛰️ Agent run", "🔀 What changed", "🧭 Impact map", "📋 Gap register", "🛠️ Remediation & sign-off",
                "💬 Ask RegRadar", "📥 Analyse your own", "🧪 Evaluation", "🔒 Audit trail"])


# ------------------------------------------------------------------ 1. agent run
def replay(pair: CircularPair, crew_slot, log_slot, delay: float):
    states: dict[str, str] = {}
    lines: list[tuple[str, str, str, float]] = []

    def on_event(ev):
        if ev.kind == "start":
            states[ev.agent] = "work"
        elif ev.kind == "done":
            states[ev.agent] = "warn" if states.get(ev.agent) == "warn-pending" else "done"
        elif ev.kind == "warn":
            states[ev.agent] = "warn-pending" if states.get(ev.agent) == "work" else states.get(ev.agent, "work")
        shown = {k: ("work" if v == "warn-pending" else v) for k, v in states.items()}
        lines.append((ev.agent, ev.kind, ev.text, ev.t_ms))
        crew_slot.markdown(crew_html(shown), unsafe_allow_html=True)
        log_slot.markdown(log_html(lines[-60:]), unsafe_allow_html=True)
        if delay:
            time.sleep(delay)

    result = run_assessment(pair, DEMO_AS_OF, llm=current_llm(), on_event=on_event)
    for agent in states:
        if states[agent] in ("work", "warn-pending"):
            states[agent] = "warn" if states[agent] == "warn-pending" else "done"
    crew_slot.markdown(crew_html(states), unsafe_allow_html=True)
    return result, states, lines


with tabs[0]:
    st.markdown("Seven agents work on the circular in turn. Each step is logged; nothing is final until a named "
                "approver signs off on the **Remediation & sign-off** tab.")
    run = st.button(f"▶ Run the agent crew on {circ.circular_id}", type="primary")
    crew_slot, log_slot = st.empty(), st.empty()
    if run:
        new_a, states, lines = replay(a.pair, crew_slot, log_slot, DELAY)
        ss.assessments[key] = new_a
        ss.workflows[key] = Workflow(new_a, ss.audit)
        ss.last_log[key] = (states, lines)
        flash("success", f"Agent run {new_a.run_id} finished: "
              f"{len(new_a.gaps)} gaps, {len(new_a.actions)} draft actions, "
              f"{len(new_a.needs_review())} routed to human review.")
        st.rerun()
    if key in ss.last_log:
        states, lines = ss.last_log[key]
        crew_slot.markdown(crew_html(states), unsafe_allow_html=True)
        log_slot.markdown(log_html(lines[-60:]), unsafe_allow_html=True)
    else:
        crew_slot.markdown(crew_html({c.key: "done" for c in CREW}), unsafe_allow_html=True)
        log_slot.markdown(log_html([(e.agent, e.kind, e.text, e.t_ms) for e in a.events][-60:]),
                          unsafe_allow_html=True)
    counts, cc = a.counts(), a.change_counts()
    st.markdown(kpis([
        (cc["NEW"], "new provisions", "blue"), (cc["AMENDED"], "amended", "blue"), (cc["REMOVED"], "removed", "blue"),
        (counts["CONFLICT"], "policy weaker", "red"), (counts["MISSING"], "not covered", "red"),
        (counts["PARTIAL"], "partly covered", "amber"), (counts["COVERED"], "already covered", "green"),
        (len(a.needs_review()), "for human review", "amber"),
    ]), unsafe_allow_html=True)
    st.markdown("#### 📝 Executive summary")
    st.markdown(f'<div class="rr-summary">{a.summary}</div>', unsafe_allow_html=True)
    st.caption(f"Written by: {a.summary_source}. "
               + ("Every figure in it was checked against the findings." if a.summary_source != "template"
                  else "Template from the findings (offline)."))

# ------------------------------------------------------------------ 2. what changed
with tabs[1]:
    if a.pair.previous is None:
        st.info("This is a new circular with no previous version, so every provision is new.")
    else:
        st.markdown(f"Word-level redline from **{a.pair.previous.circular_id}** to **{circ.circular_id}**: "
                    "<del>struck</del> = removed, <ins>green</ins> = added. Red chips are tightened figures.",
                    unsafe_allow_html=True)
    show = st.multiselect("Show", ["NEW", "AMENDED", "REMOVED", "UNCHANGED"], default=["NEW", "AMENDED", "REMOVED"])
    for c in a.changes:
        if c.change_type in show:
            st.markdown(change_card(c), unsafe_allow_html=True)

# ------------------------------------------------------------------ 3. impact map
with tabs[2]:
    st.markdown("Every changed provision traced to the policy section it lands on and the controls that implement "
                "that section. " + " ".join(chip(STATUS_LABEL[s], STATUS_COLOR[s])
                                            for s in ("CONFLICT", "MISSING", "PARTIAL", "COVERED")),
                unsafe_allow_html=True)
    st.graphviz_chart(trace_graph(a), use_container_width=False)
    st.caption("Red controls hard-code a figure the regulator has changed and must be reconfigured.")

# ------------------------------------------------------------------ 4. gap register
with tabs[3]:
    df = findings_frame(a)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("Download gap register (CSV)", df.to_csv(index=False), f"{circ.circular_id}_gaps.csv",
                       "text/csv")
    st.markdown("#### Evidence for each finding")
    for f in a.findings:
        color = RATING_COLOR.get(f.rating, "#6c7a89")
        title = f"{f.fid} · {f.provision.pid} · {STATUS_LABEL[f.status]} · {f.rating}" + (
            " · 👤 human review" if f.needs_review else "")
        with st.expander(title):
            st.markdown(chip(f.status, STATUS_COLOR[f.status]) + chip(f.rating, color)
                        + chip(f"confidence {f.confidence}", "#2e6bd6"), unsafe_allow_html=True)
            left, right = st.columns(2)
            left.markdown(f'<div class="rr-side reg"><b>Regulation {f.provision.pid}</b><br>{f.provision.text}</div>',
                          unsafe_allow_html=True)
            if f.change.previous and f.change.change_type == "AMENDED":
                left.markdown(f'<div class="rr-side"><b>Previously</b><br>{f.change.previous.text}</div>',
                              unsafe_allow_html=True)
            if f.primary_ref:
                sec = section_by_ref(f.primary_ref)
                right.markdown(f'<div class="rr-side pol"><b>Policy {f.primary_ref}</b> ({sec.heading if sec else ""})'
                               f"<br>{f.best_sentence}</div>", unsafe_allow_html=True)
            else:
                right.markdown('<div class="rr-side pol"><b>No policy section</b> addresses this requirement.</div>',
                               unsafe_allow_html=True)
            st.markdown("**Why**\n" + "\n".join(f"- {r}" for r in f.rationale))
            if f.risk:
                st.markdown("**Risk score**\n" + "\n".join(f"- {r}" for r in f.risk.factors))
            if f.controls:
                st.markdown("**Controls**\n" + "\n".join(f"- {c.control_id} {c.name}: {c.action}. {c.reason}"
                                                         for c in f.controls))
            if f.review_reasons:
                st.warning("Challenger: " + " ".join(f.review_reasons))
            if f.llm_opinion and not f.llm_opinion.get("error"):
                op = f.llm_opinion
                st.info(f"Model second opinion ({op['model']}): {op['status']}"
                        f"{' · agrees' if op['agrees'] else ' · disagrees'} — {op['rationale']}")
            st.caption("Agent trace: " + " → ".join(f.trace))

# ------------------------------------------------------------------ 5. remediation & sign-off
with tabs[4]:
    status_line = (f"✅ Signed off by **{wf.signed_off_by}** at {wf.signed_off_at}." if wf.signed_off_by
                   else "🟠 Draft: not signed off.")
    st.markdown(status_line)
    flagged = a.needs_review()
    if flagged:
        st.markdown("#### 👤 Findings the Challenger sent to a human")
        for f in flagged:
            done = wf.reviewed_flags.get(f.fid)
            with st.container(border=True):
                st.markdown(f"**{f.fid} · {f.provision.pid}** · {STATUS_LABEL[f.status]}  \n"
                            + " ".join(f.review_reasons))
                if done:
                    st.caption(f"Reviewed by {done}.")
                elif not wf.signed_off_by:
                    note = st.text_input("Review note", key=f"note-{a.run_id}-{f.fid}",
                                         placeholder="e.g. Agree: KYC-001 §2 has no liveness requirement")
                    if st.button("Record review", key=f"rev-{a.run_id}-{f.fid}"):
                        try:
                            wf.review_flag(f.fid, analyst, note)
                            flash("success", f"{f.fid} review recorded by {analyst}.")
                        except SignOffError as exc:
                            flash("error", str(exc))
                        st.rerun()
    st.markdown("#### 🛠️ Draft actions")
    bulk = st.columns([1, 3])
    if bulk[0].button("Accept all undecided", disabled=bool(wf.signed_off_by)):
        for x in a.actions:
            if x.decision == "Proposed":
                wf.decide(x.aid, "Accepted", analyst)
        flash("success", f"All undecided actions accepted by {analyst}.")
        st.rerun()
    bulk[1].caption("Each decision is written to the hash-chained audit trail with the analyst's name.")
    for x in a.actions:
        with st.container(border=True):
            top = st.columns([5, 2])
            top[0].markdown(f"**{x.aid} · {x.title}**  \n{x.kind} · owner {x.owner} · due {x.due} · "
                            f"cites {', '.join(x.citations)}")
            top[1].markdown(chip(x.priority, RATING_COLOR.get(x.priority, "#6c7a89"))
                            + chip(x.decision, {"Accepted": "#2f9e6e", "Rejected": "#d64545",
                                                "Edited": "#7a4fd6"}.get(x.decision, "#6c7a89")),
                            unsafe_allow_html=True)
            st.markdown(x.detail)
            if x.after:
                st.markdown(f'<div class="rr-red">{x.redline_html}</div>', unsafe_allow_html=True)
            if x.decided_by:
                st.caption(f"{x.decision} by {x.decided_by}" + (f": {x.comment}" if x.comment else ""))
            if not wf.signed_off_by:
                c1, c2, c3 = st.columns([2, 3, 1])
                choice = c1.radio("Decision", ["Accepted", "Rejected", "Edited"], horizontal=True,
                                  key=f"d-{a.run_id}-{x.aid}", label_visibility="collapsed")
                comment = c2.text_input("Comment", key=f"c-{a.run_id}-{x.aid}", label_visibility="collapsed",
                                        placeholder="Comment (required to reject or edit)")
                new_text = None
                if choice == "Edited":
                    new_text = st.text_area("Edited wording", value=x.after, key=f"t-{a.run_id}-{x.aid}")
                if c3.button("Record", key=f"r-{a.run_id}-{x.aid}"):
                    try:
                        wf.decide(x.aid, choice, analyst, comment, new_text)
                        flash("success", f"{x.aid} {choice.lower()} by {analyst}.")
                    except SignOffError as exc:
                        flash("error", str(exc))
                    st.rerun()
    st.markdown("#### ✍️ Sign-off")
    blockers = wf.blockers()
    if wf.signed_off_by:
        st.success(f"Signed off by {wf.signed_off_by}. Decisions are locked.")
    else:
        if blockers:
            st.warning("Sign-off is blocked until:\n" + "\n".join(f"- {b}" for b in blockers[:8])
                       + (f"\n- … and {len(blockers) - 8} more" if len(blockers) > 8 else ""))
        if st.button(f"Sign off as {approver or '(enter an approver in the sidebar)'}", type="primary",
                     disabled=not approver):
            try:
                wf.sign_off(approver)
                flash("success", f"Assessment signed off by {approver}. Four-eyes check passed.")
            except SignOffError as exc:
                flash("error", str(exc))
            st.rerun()
    st.download_button("⬇️ Download the impact assessment pack (Markdown)", build_pack(wf),
                       f"{circ.circular_id}_impact_assessment.md", "text/markdown")

# ------------------------------------------------------------------ 6. ask
with tabs[5]:
    llm = current_llm()
    st.markdown("Ask about any circular on the radar. " + (
        f"**{llm.label}** answers by calling read-only tools; an answer is shown only if every figure in it came "
        "from a tool." if llm else "Offline mode: answers are assembled from the findings by intent routing and "
                                   "search, with citations. No LLM is used."))
    qcols = st.columns(3)
    picked = None
    for i, q in enumerate(SAMPLE_QUESTIONS):
        if qcols[i % 3].button(q, key=f"q{i}", use_container_width=True):
            picked = q
    typed = st.chat_input("Ask RegRadar…")
    question = typed or picked
    if question:
        tools = Tools(ss.assessments)
        ans = LLMAgent(llm, tools).ask(question) if llm else offline_answer(question, tools)
        ss.chat.append((question, ans))
        ss.audit.record("question_answered", {"question": question[:300], "source": ans.source,
                                              "tools": ans.tool_calls, "blocked": ans.blocked},
                        actor=analyst or "user", correlation_id="ask")
    for q, ans in reversed(ss.chat[-8:]):
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant", avatar="🛰️"):
            st.markdown(ans.text)
            meta = f"Source: {ans.source}"
            if ans.tool_calls:
                meta += " · tools: " + ", ".join(ans.tool_calls)
            if ans.note:
                meta += f" · {ans.note}"
            st.caption(meta)

# ------------------------------------------------------------------ 7. analyse your own
with tabs[6]:
    st.markdown("Paste a circular (Markdown with `## 1. Title` sections, or plain paragraphs). Add the previous "
                "version to see what changed. Use synthetic or public text only: never paste confidential material.")
    c1, c2 = st.columns(2)
    new_text = c1.text_area("New circular", value=PASTE_EXAMPLE, height=240)
    old_text = c2.text_area("Previous version (optional)", value="", height=240)
    meta = st.columns(3)
    user_id = meta[0].text_input("Circular id", value="USER-2026-01")
    domain = meta[1].selectbox("Domain", ["AML", "KYC", "Sanctions", "Model Risk & AI", "Operational Risk",
                                          "Third-Party Risk", "Data & Privacy", "Enterprise Risk"], index=4)
    eff = meta[2].date_input("Effective date", value=pd.Timestamp("2027-06-30"))
    if st.button("▶ Analyse with the agent crew", type="primary"):
        try:
            front = f"---\ncircular_id: {user_id}\ntitle: {user_id}\ndomain: {domain}\neffective_date: {eff}\n---\n\n"
            cur = parse_circular(front + new_text, "pasted circular", user_id)
            prev = parse_circular(old_text, "previous version", user_id + "-PREV") if old_text.strip() else None
            ua = run_assessment(CircularPair(user_id, cur, prev), DEMO_AS_OF, llm=current_llm())
            ss.user_assessment = ua
            ss.audit.record("user_circular_analysed", {"circular": user_id, "chars": len(new_text),
                                                       "findings": len(ua.findings)},
                            actor=analyst or "user", correlation_id=ua.run_id)
        except CircularError as exc:
            st.error(str(exc))
    ua = ss.get("user_assessment")
    if ua is not None:
        cnt = ua.counts()
        st.markdown(kpis([(len(ua.findings), "provisions assessed", "blue"), (cnt["CONFLICT"], "policy weaker", "red"),
                          (cnt["MISSING"], "not covered", "red"), (cnt["PARTIAL"], "partly covered", "amber"),
                          (cnt["COVERED"], "covered", "green"), (len(ua.actions), "draft actions", "green")]),
                    unsafe_allow_html=True)
        st.markdown(f'<div class="rr-summary">{ua.summary}</div>', unsafe_allow_html=True)
        st.dataframe(findings_frame(ua), use_container_width=True, hide_index=True)
        for x in ua.actions[:12]:
            st.markdown(f"**{x.aid} · {x.title}** ({x.priority})")
            if x.after:
                st.markdown(f'<div class="rr-red">{x.redline_html}</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------ 8. evaluation
with tabs[7]:
    st.markdown("Scored against a hand-written answer key for all five circulars (`data/eval/answer_key.json`). "
                "One circular (NPCA-FC-2026-22) is a held-out set: written after the thresholds were frozen.")
    if st.button("Run the evaluation now"):
        ss.eval_report = run_evaluation(DEMO_AS_OF)
    rep = ss.get("eval_report")
    if rep:
        st.markdown(format_report(rep))
        st.dataframe(pd.DataFrame(rep["confusion"]), hide_index=True)
    st.markdown("#### How it works")
    st.markdown(
        "- **Document intelligence:** circulars are split into numbered sections and sentences; each sentence is "
        "classed as obligation, prohibition, permission or information; figures are extracted and normalised "
        "(days, percent, US dollars) with their kind (deadline, frequency, minimum duration, threshold).\n"
        "- **Change comparison:** provisions are aligned old→new by TF-IDF cosine with numbers masked; figures "
        "are paired by surrounding words and marked tightened or loosened.\n"
        "- **RAG mapping:** Project 1's hybrid retriever (BM25 + LSA embeddings, Reciprocal Rank Fusion) finds the "
        "policy sections; sentence matching finds the exact sentence; a weak match triggers a second search.\n"
        "- **Gap logic:** compare figures (stricter or weaker), check scope words and the audience of reporting "
        "duties; score impact × likelihood with written factors.\n"
        "- **Live AI (optional):** a Databricks foundation model gives an independent verdict per finding "
        "(disagreement → human), drafts the summary (numbers checked), and answers questions with tools.")

# ------------------------------------------------------------------ 9. audit
with tabs[8]:
    ok, broken = ss.audit.verify_chain()
    st.markdown(("🔗 Hash chain intact" if ok else f"⛔ Hash chain broken at event {broken}")
                + f" · {ss.audit.count()} events this session")
    st.dataframe(pd.DataFrame([{"#": e.seq, "time (UTC)": e.timestamp, "actor": e.actor, "event": e.event_type,
                                "run": e.correlation_id, "hash": e.hash[:16] + "…"}
                               for e in ss.audit.events(limit=300)]), use_container_width=True, hide_index=True)

st.markdown(FOOTER, unsafe_allow_html=True)
