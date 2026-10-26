"""Ask the risk agent: conversational Q&A over the dashboard's data.

Offline mode routes a question to the right tools with simple, inspectable
rules: find the metric the question is about (names and synonyms), find the
intent (status, why, forecast, what-if, list, trend, summary), pull any amount
out of the question for what-if scenarios, then answer from tool results. If
it cannot tell what the question is about it says so and suggests questions it
can answer, instead of guessing.

Live mode (``llm.py``) hands the same tools to Claude instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from . import catalog
from .agent import Briefing, RiskInsightsAgent, _fd

SYNONYMS = {
    "KRI_CET1_PCT": "cet1 capital ratio tier 1 solvency",
    "KRI_LCR_PCT": "lcr liquidity coverage ratio hqla deposits outflow funding",
    "KRI_NPL_PCT": "npl non performing loans credit quality bad loans",
    "KRI_OPLOSS_YTD_USD_M": "operational losses op loss oprisk loss events ytd",
    "KRI_HR_KYC_OVERDUE_PCT": "kyc overdue reviews high risk customers periodic review know your customer",
    "KRI_AML_ALERTS_OVER_30D": "aml alerts overdue investigation 30 days money laundering deadline",
    "KRI_SANCTIONS_BREACHES": "sanctions breaches ofac",
    "KRI_LATE_SARS": "sar late filings suspicious activity reports",
    "DQ_HEALTH_SCORE": "data quality dq health score",
    "DQ_RULES_BREACHED": "dq rules breached failing data quality rules address completeness",
    "FEED_ON_TIME_PCT": "feeds late source systems on time timeliness market data fx file",
    "RECON_OPEN_BREAKS": "reconciliation breaks recon open breaks nostro",
    "RECON_OPEN_VALUE_USD_M": "unreconciled value recon value amount",
    "RECON_AGED_BREAKS": "aged breaks old breaks over 5 days",
    "RECON_AUTO_MATCH_PCT": "auto match rate matching",
    "EXC_BACKLOG": "exceptions backlog open exceptions operations queue payments ops",
    "EXC_PAST_DUE": "past due exceptions overdue exceptions sla",
    "EXC_SLA_BREACH_PCT": "sla breach closed late service level",
    "AML_ALERTS_PENDING": "aml alerts pending review",
    "TXN_VOLUME_USD_M": "payment volume transactions volume activity",
}
INTENTS = [
    ("whatif", r"\bwhat if\b|\bif we\b|\bsuppose\b|\bscenario\b|\bwhat happens\b"
               r"|\badd(ing)?\b.*\b(handler|staff|people)"),
    ("summary", r"\bsummar|\bbrief|\btop risks?\b|\boverall\b|\bboard\b|\bheadline|\bwhat should i worry\b"),
    ("forecast", r"\bwhen\b|\bwill\b|\bforecast|\bpredict|\bnext (week|month)\b|\bgoing to\b|\bproject"),
    ("why", r"\bwhy\b|\bcause|\bdriv|\bexplain|\broot\b|\bwhat happened\b|\bwhat changed\b"),
    ("list", r"\bwhich\b|\blist\b|\bshow me\b|\bwho\b|\bwhat are the\b"),
    ("trend", r"\btrend|\bhistory|\bover time\b|\blast (month|weeks?)\b"),
]
EXAMPLES = [
    "What are the top risks this week?",
    "Why did reconciliation breaks go up?",
    "When will the AML alert KRI get worse?",
    "What if we add 3 handlers to Payments Ops?",
    "What if another USD 300m of deposits leaves?",
    "Which high-risk customers are overdue for KYC review?",
    "Why is the data-quality score amber?",
]


@dataclass
class ChatAnswer:
    question: str
    intent: str
    metric_id: str | None
    text: str
    data: dict = field(default_factory=dict)
    trace_from: int = 0


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def detect_metric(question: str) -> str | None:
    q = _tokens(question)
    best, best_score = None, 0.0
    for m in catalog.METRICS:
        vocab = _tokens(m.name + " " + SYNONYMS.get(m.id, ""))
        overlap = q & vocab - {"of", "the", "rate", "days", "over", "open", "ytd", "and"}
        score = len(overlap) + (0.5 if m.kri else 0) * bool(overlap)
        if score > best_score:
            best, best_score = m.id, score
    return best if best_score >= 1 else None


def detect_intent(question: str) -> str:
    q = question.lower()
    for name, pattern in INTENTS:
        if re.search(pattern, q):
            return name
    return "status"


def extract_amount(question: str) -> float | None:
    q = question.lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(bn|billion|b\b|m\b|mn|million|k\b|%|percent)?", q)
    if not m:
        return None
    value, unit = float(m.group(1)), (m.group(2) or "").strip()
    if unit in ("bn", "billion", "b"):
        value *= 1000
    if unit == "k":
        value /= 1000
    return value


SCENARIO_METRIC = {"payments_extra_handlers": "EXC_BACKLOG", "deposit_outflow": "KRI_LCR_PCT",
                   "op_loss_event": "KRI_OPLOSS_YTD_USD_M", "eur_nostro_cleanup": "RECON_OPEN_BREAKS",
                   "kyc_reviews_completed": "KRI_HR_KYC_OVERDUE_PCT"}


def pick_scenario(question: str, metric_id: str | None) -> str | None:
    q = question.lower()
    if re.search(r"handler|staff|people|headcount|fte|analyst", q):
        return "payments_extra_handlers"
    if re.search(r"deposit|outflow|withdraw|liquidity|lcr", q):
        return "deposit_outflow"
    if re.search(r"\blos[st]\b|\blose\b|\bloss|fraud|fine\b|penalt", q):
        return "op_loss_event"
    if re.search(r"nostro|eur|clean|clear.*break|break", q):
        return "eur_nostro_cleanup"
    if re.search(r"kyc|review", q):
        return "kyc_reviews_completed"
    return None


class RiskChat:
    def __init__(self, agent: RiskInsightsAgent, briefing: Briefing) -> None:
        self.agent = agent
        self.tools = agent.tools
        self.briefing = briefing

    def ask(self, question: str) -> ChatAnswer:
        start = len(self.tools.trace)
        question = question.strip()[:500]
        intent = detect_intent(question)
        metric_id = detect_metric(question)
        if intent == "whatif":
            ans = self._whatif(question, metric_id)
        elif intent == "summary":
            ans = self._summary(question)
        elif metric_id is None:
            ans = ChatAnswer(question, intent, None,
                             "I could not tell which metric you mean. I can answer about any KPI or KRI on the "
                             "dashboard, for example:\n\n" + "\n".join(f"- {e}" for e in EXAMPLES))
        elif intent == "why":
            ans = self._why(question, metric_id)
        elif intent == "forecast":
            ans = self._forecast(question, metric_id)
        elif intent == "list":
            ans = self._list(question, metric_id)
        elif intent == "trend":
            ans = self._trend(question, metric_id)
        else:
            ans = self._status(question, metric_id)
        ans.trace_from = start
        return ans

    # ------------------------------------------------------------ intents
    def _summary(self, q: str) -> ChatAnswer:
        text = "**This week's picture** (from the agent's briefing):\n\n" + "\n".join(
            f"- {s}" for s in self.briefing.summary)
        return ChatAnswer(q, "summary", None, text)

    def _status(self, q: str, metric_id: str) -> ChatAnswer:
        r = self.tools.call("get_metric", metric_id=metric_id)
        m = catalog.get(metric_id)
        text = (f"**{m.name}** is **{r['display']}** ({r['status']}) on {_fd(r['as_of'])}. "
                f"A week ago it was {m.fmt(r['prior_7d'])} ({r['status_7d_ago']}).\n\n{m.definition}")
        if m.limit is not None or m.early_warning is not None:
            text += f"\n\nEarly-warning: {m.fmt(m.early_warning)} · Limit: {m.fmt(m.limit)}"
            if m.policy_ref:
                text += f" · Source: {m.policy_ref}"
        return ChatAnswer(q, "status", metric_id, text, {"metric": r})

    def _finding_for(self, metric_id: str):
        for match in (lambda f: f.metric_id == metric_id, lambda f: metric_id in f.related):
            for f in self.briefing.findings:
                if match(f):
                    return f
        scan = self.tools.call("scan_metrics")
        row = next(r for r in scan["metrics"] if r["metric_id"] == metric_id)
        return self.agent.investigate(0, row, "now")

    def _reinvestigate(self, metric_id: str) -> None:
        """Re-run the investigation live so the answer comes with the agent's full tool trace."""
        scan = self.tools.call("scan_metrics")
        row = next((r for r in scan["metrics"] if r["metric_id"] == metric_id), None)
        if row is not None:
            self.agent.investigate(0, row, "now")

    def _why(self, q: str, metric_id: str) -> ChatAnswer:
        m = catalog.get(metric_id)
        snap = self.tools.call("get_metric", metric_id=metric_id)
        if snap["status"] == "GREEN" and snap["status_7d_ago"] == "GREEN" and m.direction != "none":
            return ChatAnswer(q, "why", metric_id, f"**{m.name}** is {snap['display']} and within appetite; "
                              "nothing needs explaining this week.")
        f = self._finding_for(metric_id)
        self._reinvestigate(f.metric_id)
        text = f"**{f.headline}.**\n\n" + "\n".join(f"- {d}" for d in f.detail)
        if f.metric_id != metric_id:
            text = f"{m.name} moves with the theme below.\n\n" + text
        if f.actions:
            text += "\n\n**Suggested actions**\n" + "\n".join(
                f"- {a.action} ({a.owner}, by {_fd(a.due)})" for a in f.actions)
        return ChatAnswer(q, "why", metric_id, text, {"finding": f})

    def _forecast(self, q: str, metric_id: str) -> ChatAnswer:
        m = catalog.get(metric_id)
        fc = self.tools.call("forecast", metric_id=metric_id, horizon_days=30)
        lines = [f"**{m.name}: 30-day outlook** ({fc.get('method', 'n/a')})", ""]
        if fc.get("note"):
            lines.append(fc["note"])
        elif metric_id == "KRI_HR_KYC_OVERDUE_PCT":
            lines.append(f"{fc['overdue_now']} of {fc['population']} high-risk customers are overdue today. "
                         f"{len(fc['falling_due'])} more reviews fall due in the next 30 days; if none are "
                         f"completed the KRI reaches {fc['projected_pct_end']:.1f}%.")
            lines += [f"- {c['customer_id']}: due {_fd(c['due_date'])}" for c in fc["falling_due"]]
        elif "next_breaches" in fc:
            nb = fc["next_breaches"]
            lines.append(f"{len(nb)} pending alert(s) pass the 30-day investigation deadline in the next 30 days.")
            lines += [f"- {c['customer_id']} ({c['rule_code']}, raised {_fd(c['alert_date'])}): "
                      f"deadline passes {_fd(c['breaches_on'])}" for c in nb]
        else:
            lines.append(f"Today {m.fmt(fc['current'])} ({fc['status_now']}); "
                         f"trend {fc['trend_per_day']:+.3f} per day; "
                         f"projected {m.fmt(fc['projected_end'])} ({fc['projected_status_end']}) in 30 days.")
            if fc.get("early_warning_date"):
                lines.append(f"- Reaches the early-warning trigger around {_fd(fc['early_warning_date'])}.")
            if fc.get("limit_date"):
                lines.append(f"- Reaches the limit around {_fd(fc['limit_date'])}.")
            lines.append("\n_A straight-line extrapolation: treat it as a warning signal, not a prediction._")
        return ChatAnswer(q, "forecast", metric_id, "\n".join(lines), {"forecast": fc})

    def _list(self, q: str, metric_id: str) -> ChatAnswer:
        m = catalog.get(metric_id)
        if metric_id == "KRI_HR_KYC_OVERDUE_PCT":
            return self._forecast(q, metric_id)
        r = self.tools.call("list_records", metric_id=metric_id, limit=10)
        text = f"**{r['count']} record(s)** behind {m.name} on {_fd(self.tools.as_of)}"
        text += " (showing up to 10)." if r["count"] > 10 else "."
        return ChatAnswer(q, "list", metric_id, text, {"records": r["records"]})

    def _trend(self, q: str, metric_id: str) -> ChatAnswer:
        m = catalog.get(metric_id)
        r = self.tools.call("get_trend", metric_id=metric_id, days=28)
        text = (f"**{m.name}, last 28 days:** low {m.fmt(r['min'])} on {_fd(r['min_date'])}, high {m.fmt(r['max'])} "
                f"on {_fd(r['max_date'])}; robust trend {r['trend_per_day']:+.3f} {m.unit} per day.")
        return ChatAnswer(q, "trend", metric_id, text, {"trend": r})

    def _whatif(self, q: str, metric_id: str | None) -> ChatAnswer:
        scenario = pick_scenario(q, metric_id)
        amount = extract_amount(q)
        if scenario is None:
            from .scenarios import SCENARIOS

            return ChatAnswer(q, "whatif", metric_id, "I can run these what-if scenarios:\n\n" + "\n".join(
                f"- **{k}**: {v}" for k, v in SCENARIOS.items()))
        default = {"payments_extra_handlers": 3, "deposit_outflow": 250, "op_loss_event": 1.0,
                   "eur_nostro_cleanup": 50, "kyc_reviews_completed": 1}[scenario]
        amount = default if amount is None else amount
        metric_id = SCENARIO_METRIC[scenario]
        r = self.tools.call("what_if", scenario=scenario, amount=amount)
        if scenario == "payments_extra_handlers":
            clear = f"clears the backlog by **{_fd(r['backlog_clear_date'])}**" if r["backlog_clear_date"] else \
                f"leaves **{r['backlog_in_10_days_scenario']}** open after 10 business days"
            text = (f"Payments Ops has **{r['backlog_now']}** open exceptions, receives "
                    f"{r['arrivals_per_business_day']:.0f} "
                    f"and closes {r['closures_per_business_day']:.0f} per business day. As is, the backlog reaches "
                    f"**{r['backlog_in_10_days_baseline']}** in 10 business days. Adding **{r['extra_handlers']}** "
                    f"handler(s) {clear}. It takes {r['handlers_to_stop_growth']} handler(s) just to stop it growing.")
        elif scenario == "deposit_outflow":
            text = (f"A further outflow of **USD {r['outflow_usd_m']:,.0f}m** takes LCR from "
                    f"{r['lcr_now_pct']:.1f}% to "
                    f"**{r['lcr_after_pct']:.1f}%** ({r['status_after']}). Headroom before the 110% limit is about "
                    f"USD {r['max_outflow_before_limit_usd_m']:,.0f}m.")
        elif scenario == "op_loss_event":
            text = (f"A new loss of **USD {r['new_loss_usd_m']:.2f}m** takes year-to-date losses from USD "
                    f"{r['ytd_now_usd_m']:.2f}m to **USD {r['ytd_after_usd_m']:.2f}m** ({r['status_after']}). "
                    f"Headroom to the USD 15m limit today: USD {r['headroom_to_limit_usd_m']:.2f}m.")
        elif scenario == "eur_nostro_cleanup":
            text = (f"Clearing {r['pct_cleared']:.0f}% of the {r['release_breaks_open']} EUR nostro breaks "
                    "from release "
                    f"R26.10 removes {r['breaks_cleared']} breaks (USD {r['unreconciled_value_cleared_usd_m']:.2f}m): "
                    f"open breaks go from {r['open_breaks_now']} to **{r['open_breaks_after']}** "
                    f"({r['status_after']}).")
        else:
            text = (f"Completing {r['reviews_completed']} review(s) brings overdue high-risk reviews to "
                    f"{r['overdue_now_after']} today ({r['pct_now_after']:.1f}%, {r['status_now_after']}) and "
                    f"{r['overdue_in_14_days_after']} in 14 days ({r['pct_in_14_days_after']:.1f}%, "
                    f"{r['status_in_14_days_after']}).")
        text += "\n\n_Assumptions: " + "; ".join(r["assumptions"]) + "._"
        return ChatAnswer(q, "whatif", metric_id, text, {"what_if": r})


def suggested_questions(as_of: date) -> list[str]:
    return EXAMPLES
