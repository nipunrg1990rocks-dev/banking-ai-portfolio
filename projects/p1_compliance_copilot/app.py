"""Streamlit UI for the Enterprise Banking Risk & Compliance AI Copilot.

Run locally from the repository root:

    streamlit run projects/p1_compliance_copilot/app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from copilot.engine import (  # noqa: E402
    DISCLAIMER,
    MODE_LLM,
    MODE_OFFLINE,
    STATUS_BLOCKED,
    STATUS_NO_ANSWER,
    STATUS_RELEASED,
    STATUS_REVIEW,
    ComplianceCopilot,
    CopilotAnswer,
)
from copilot.evaluation import run_evaluation  # noqa: E402
from copilot.review import DECISIONS  # noqa: E402
from portfolio_core.llm import live_llm_available, resolve_model  # noqa: E402
from portfolio_core.ui import FOOTER, PORTFOLIO_CSS, banner, pill  # noqa: E402

st.set_page_config(page_title="Compliance AI Copilot", page_icon="🛡️", layout="wide")
st.markdown(PORTFOLIO_CSS, unsafe_allow_html=True)

SAMPLE_QUESTIONS = [
    "How often must high risk customers have their KYC reviewed?",
    "Who must approve onboarding a politically exposed person?",
    "What is the deadline to file a SAR after determination?",
    "Should I release the payment that hit a sanctions alert?",
    "Can I tell the customer that we filed a SAR on them?",
    "Can AI make the final decision on customer onboarding?",
    "What is the bank's dress code for Fridays?",
]

STATUS_COLOR = {STATUS_RELEASED: "green", STATUS_REVIEW: "amber", STATUS_NO_ANSWER: "red", STATUS_BLOCKED: "red"}
CONF_COLOR = {"High": "green", "Medium": "amber", "Low": "red"}


def get_copilot() -> ComplianceCopilot:
    # One copilot (index, audit log, review queue) per browser session keeps demos independent.
    if "copilot" not in st.session_state:
        st.session_state.copilot = ComplianceCopilot()
        st.session_state.history = []
    return st.session_state.copilot


copilot = get_copilot()
llm_ready = live_llm_available()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### Session")
    user = st.text_input("Asking as (employee ID)", value="analyst.priya",
                         help="Recorded in the audit trail. Use a fictional name.")
    mode_options = ["Offline (free, no LLM)"] + ([f"Live Claude ({resolve_model()})"] if llm_ready else [])
    mode_choice = st.radio("Answer mode", mode_options, index=0)
    mode = MODE_LLM if mode_choice.startswith("Live") else MODE_OFFLINE
    if not llm_ready:
        st.caption("Live LLM mode is off: no API key is configured. Offline mode quotes the policies "
                   "directly, so it cannot invent content.")
    domain_filter = st.multiselect("Limit to policy domains", copilot.domains, default=[])
    st.divider()
    sidebar_metrics = st.empty()  # filled at the end of the run so the counts include this run's activity
    if st.button("Reset demo session", use_container_width=True):
        for key in ("copilot", "history", "last_answer"):
            st.session_state.pop(key, None)
        st.rerun()

st.markdown(banner(
    "🛡️ Risk & Compliance AI Copilot",
    "Ask policy questions about KYC, AML, sanctions, risk appetite, operational risk, AI governance, "
    "records and third-party risk. Answers cite the exact policy section, show confidence, flag "
    "regulated decisions for human review, and are written to a tamper-evident audit trail.",
), unsafe_allow_html=True)

tab_ask, tab_review, tab_audit, tab_library, tab_eval, tab_about = st.tabs(
    ["💬 Ask", "🧑‍⚖️ Review queue", "📜 Audit trail", "📚 Policy library", "📊 Evaluation", "🏗️ How it works"])


def render_answer(ans: CopilotAnswer) -> None:
    pills = pill(ans.status, STATUS_COLOR.get(ans.status, "grey"))
    if ans.confidence:
        pills += pill(f"Confidence: {ans.confidence.level} ({ans.confidence.score:.2f})",
                      CONF_COLOR[ans.confidence.level])
    pills += pill(ans.mode_label, "blue") + pill(f"{ans.latency_ms} ms", "grey") + pill(f"ID {ans.query_id}", "grey")
    st.markdown(pills, unsafe_allow_html=True)
    st.markdown(f"**Question (as processed):** {ans.question}")
    st.markdown(ans.answer)
    for note in ans.notes:
        st.info(note)
    for flag in ans.flags:
        st.warning(flag, icon="⚠️")
    if ans.review_item_id:
        st.error(f"Routed to the human review queue as item #{ans.review_item_id}. "
                 "A compliance reviewer must approve, amend or reject it.", icon="🧑‍⚖️")
    if ans.status in (STATUS_RELEASED, STATUS_REVIEW):
        st.caption(DISCLAIMER)
    if ans.confidence:
        with st.expander("Why this confidence level?"):
            for reason in ans.confidence.reasons:
                st.write("- " + reason)
    if ans.sources:
        st.markdown("#### Sources")
        for s in ans.sources:
            with st.expander(f"[{s.number}] {s.doc_id} v{s.version} · §{s.section}", expanded=s.number == 1):
                st.markdown(f'<div class="pf-citation"><b>{s.doc_title}</b><br>{s.text}</div>',
                            unsafe_allow_html=True)
                st.caption(f"Chunk {s.chunk_id} · fused score {s.fused_score} · BM25 {s.bm25} · "
                           f"semantic similarity {s.similarity}")


# ------------------------------------------------------------------ Ask
with tab_ask:
    st.markdown("**Try a sample question:**")
    cols = st.columns(4)
    for i, q in enumerate(SAMPLE_QUESTIONS):
        if cols[i % 4].button(q, key=f"sample{i}", use_container_width=True):
            st.session_state.question = q
    with st.form("ask_form"):
        question = st.text_area("Your question", key="question", height=90,
                                placeholder="e.g. What is the beneficial ownership threshold for legal entities?")
        submitted = st.form_submit_button("Ask the copilot", type="primary")
    if submitted:
        with st.spinner("Retrieving policy sections..."):
            st.session_state.last_answer = copilot.ask(question, user=user or "anonymous", mode=mode,
                                                       domains=domain_filter or None)
            st.session_state.history.insert(0, st.session_state.last_answer)
    if st.session_state.get("last_answer"):
        render_answer(st.session_state.last_answer)
    if len(st.session_state.history) > 1:
        with st.expander(f"Earlier questions this session ({len(st.session_state.history) - 1})"):
            for a in st.session_state.history[1:]:
                st.write(f"- `{a.query_id}` {a.question} → **{a.status}**")

# ------------------------------------------------------------------ Review
with tab_review:
    st.subheader("Human-in-the-loop review")
    st.caption("Answers on regulated decisions, possible tipping-off, policy exceptions, and anything with low "
               "confidence wait here. Maker-checker control: the person who asked cannot review the answer.")
    pending = copilot.reviews.items("Pending")
    if not pending:
        st.success("No items waiting for review. Ask a flagged sample question (for example the sanctions or "
                   "SAR questions) to see this workflow.")
    for item in pending:
        with st.container(border=True):
            st.markdown(pill(f"#{item.item_id}", "grey") + pill(f"Confidence {item.confidence}",
                        CONF_COLOR.get(item.confidence, "grey")) + pill(f"asked by {item.asked_by}", "blue"),
                        unsafe_allow_html=True)
            st.markdown(f"**Q:** {item.question}")
            st.markdown(item.answer)
            for f in item.flags:
                st.caption("⚠️ " + f)
            st.caption("Sources: " + ", ".join(item.sources))
            with st.form(f"review_{item.item_id}"):
                c1, c2 = st.columns([1, 2])
                reviewer = c1.text_input("Reviewer", value="compliance.officer.lee")
                decision = c1.selectbox("Decision", DECISIONS)
                comment = c2.text_area("Rationale (required)", height=80)
                amended = c2.text_area("Amended answer (only for 'Approved with amendment')", height=80)
                if st.form_submit_button("Record decision"):
                    try:
                        copilot.decide_review(item.item_id, decision=decision, reviewer=reviewer,
                                              comment=comment, amended_answer=amended or None)
                        st.success(f"Decision recorded for item #{item.item_id}.")
                        st.rerun()
                    except (ValueError, KeyError) as exc:
                        st.error(str(exc))
    decided = [i for i in copilot.reviews.items() if i.status != "Pending"]
    if decided:
        st.markdown("#### Decided items")
        st.dataframe(pd.DataFrame([{"#": i.item_id, "question": i.question, "decision": i.status,
                                    "reviewer": i.reviewer, "rationale": i.comment, "decided_at": i.decided_at}
                                   for i in decided]), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ Audit
with tab_audit:
    st.subheader("Tamper-evident audit trail")
    ok, broken_at = copilot.audit.verify_chain()
    if ok:
        st.success(f"Hash chain verified: all {copilot.audit.count()} events are intact.", icon="🔒")
    else:
        st.error(f"Hash chain broken at event #{broken_at}: the log has been altered.")
    events = copilot.audit.events(limit=500)
    df = pd.DataFrame([{"seq": e.seq, "timestamp": e.timestamp, "actor": e.actor, "event": e.event_type,
                        "query_id": e.correlation_id, "details": json.dumps(e.payload)[:300],
                        "hash": e.hash[:16] + "…"} for e in events])
    query_filter = st.text_input("Filter by question ID")
    if query_filter and not df.empty:
        df = df[df["query_id"] == query_filter.strip()]
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("Download full audit log (JSON)",
                       json.dumps([e.__dict__ for e in events], indent=2),
                       file_name="copilot_audit_log.json", mime="application/json")

# ------------------------------------------------------------------ Library
with tab_library:
    st.subheader("Policy library (synthetic)")
    st.dataframe(pd.DataFrame([{"doc_id": d.doc_id, "title": d.title, "domain": d.domain, "version": d.version,
                                "owner": d.metadata.get("owner", ""),
                                "effective": d.metadata.get("effective_date", ""),
                                "chunks": sum(c.doc_id == d.doc_id for c in copilot.chunks)}
                               for d in copilot.documents]), use_container_width=True, hide_index=True)
    choice = st.selectbox("Read a policy", [f"{d.doc_id} · {d.title}" for d in copilot.documents])
    doc = next(d for d in copilot.documents if choice.startswith(d.doc_id))
    with st.expander("Policy text", expanded=False):
        st.markdown(doc.body)
    st.markdown("#### Ingest a new policy document")
    st.caption("Upload a Markdown file with a front-matter header (doc_id, title, version, domain) and numbered "
               "'## ' sections. Documents that appear to contain personal data are rejected. Uploaded documents "
               "live only in this browser session.")
    upload = st.file_uploader("Policy file (.md)", type=["md", "txt"])
    if upload is not None and st.button("Validate and ingest"):
        try:
            new_doc = copilot.ingest(upload.read().decode("utf-8", errors="replace"), upload.name, actor=user)
            st.success(f"Ingested {new_doc.doc_id} v{new_doc.version}; the index has been rebuilt.")
        except ValueError as exc:
            st.error(str(exc))

# ------------------------------------------------------------------ Evaluation
with tab_eval:
    st.subheader("Retrieval and answer quality")
    st.caption("Runs the labelled golden set (37 questions, including out-of-scope and prompt-injection cases) "
               "through a fresh copilot in offline mode. Results are computed live, not pre-recorded.")
    if st.button("Run evaluation now"):
        with st.spinner("Evaluating..."):
            st.session_state.eval_report = run_evaluation()
    report = st.session_state.get("eval_report")
    if report:
        m = report["metrics"]
        c = st.columns(4)
        c[0].metric("Recall@1", f"{m['recall_at_1']:.0%}")
        c[1].metric("Recall@3", f"{m['recall_at_3']:.0%}")
        c[2].metric("Answer fact accuracy", f"{m['answer_fact_accuracy']:.0%}")
        c[3].metric("Routing accuracy", f"{m['routing_accuracy']:.0%}")
        st.dataframe(pd.DataFrame(report["cases"]), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ About
with tab_about:
    st.subheader("Architecture")
    st.markdown("""
```
Question ─► Guardrails ─► Hybrid retrieval ─► Answer generation ─► Checks & routing ─► Response
            • PII redaction   • BM25 keywords        • Offline: extractive   • Citation validation   • Citations
            • Injection block • LSA dense vectors    • Live: Claude (opt-in) • Confidence score      • Confidence
            • Length checks   • RRF fusion           • Prompt grounded in    • Risk-rule flags       • Review status
                              • Acronym expansion      numbered sources      • Human review queue
                                                                             │
                                    Every step ──► Hash-chained audit log (SQLite, SHA-256)
```
""")
    st.markdown("""
- **Documents:** 8 synthetic policies for the fictional *Ledgerline Bank*, split into section-level chunks.
- **Retrieval:** BM25 keyword ranking and dense semantic vectors (TF-IDF projected with truncated SVD), fused
  with Reciprocal Rank Fusion. Banking acronyms such as PEP, SAR and CET1 are expanded before searching.
- **Answers:** offline mode quotes the most relevant policy sentences with citations, so it cannot invent
  content. Live mode (when an API key is configured) asks Claude to answer only from the numbered sources.
  Its citations are validated, and it falls back to offline mode if validation fails.
- **Controls:** PII redaction, prompt-injection blocking, confidence thresholds, risk rules for sanctions,
  tipping-off, PEPs, policy exceptions and regulated decisions, maker-checker human review, and a
  tamper-evident audit log.
""")
    st.markdown(FOOTER, unsafe_allow_html=True)

with sidebar_metrics.container():
    st.metric("Pending reviews", copilot.reviews.counts().get("Pending", 0))
    st.metric("Audit events", copilot.audit.count())
