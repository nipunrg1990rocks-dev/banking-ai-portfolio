"""The Risk Insights Agent: investigates KPI moves and writes the management briefing.

Offline mode (default, free) runs a fixed investigation playbook. It is an agent
in the practical sense: it decides which metrics need attention, then for each
one chooses and calls tools (breakdown, change-point, change calendar, forecast,
what-if, policy lookup) based on what the previous tool returned, and stops
when it has a root cause, a forecast and an action. Every step is recorded in a
trace that the dashboard shows.

The playbook, per flagged metric:

1. ``scan_metrics``: what is RED or AMBER now, or went RED at some point this week?
2. ``explain_change``: which reconciliation / team / table / rule drove the move?
3. ``find_onset`` (or ``exception_flow`` for queues): when did it start, step or ramp?
4. ``correlate_changes``: which release, incident or business change lines up,
   with lineage to the driver?
5. ``forecast``: when does it cross the trigger or limit if nothing changes?
6. ``what_if``: how big a fix is needed (for queues, liquidity, losses, KYC)?
7. ``get_policy``: which escalation rule applies (RAS-004 s.6, AML-002 s.3)?

Findings that share a root cause are merged into one theme, ranked, and written
up as an executive summary, a board-style commentary per risk domain and an
action list with owners and due dates. Numbers in the text are always copied
from tool results, never typed by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import catalog
from .catalog import Metric
from .metrics import MetricStore
from .tools import RiskTools

# KRIs that move because a dated obligation passed (a review or investigation deadline).
SCHEDULED = {"KRI_HR_KYC_OVERDUE_PCT", "KRI_AML_ALERTS_OVER_30D"}
STATUS_RANK = {"GREEN": 0, "NONE": 0, "AMBER": 1, "RED": 2}
ARROW = {"RED": "🔴", "AMBER": "🟠", "GREEN": "🟢", "NONE": "⚪"}


def _bd(d: date, n: int) -> date:
    """``n`` business days after ``d``."""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _fd(d) -> str:
    if d is None:
        return "n/a"
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    return f"{d.day} {d.strftime('%b')}"


@dataclass
class Action:
    action: str
    owner: str
    due: date
    basis: str


@dataclass
class Finding:
    metric_id: str
    name: str
    domain: str
    status: str
    priority: int
    headline: str
    detail: list[str] = field(default_factory=list)
    driver: dict | None = None
    onset: date | None = None
    root_cause: dict | None = None
    confidence: str = "none"
    forecast: dict | None = None
    actions: list[Action] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    records: list[dict] = field(default_factory=list)
    focus_date: date | None = None

    @property
    def theme_key(self) -> str:
        if self.root_cause and self.confidence in ("high", "medium"):
            return self.root_cause["change_id"]
        return self.metric_id


@dataclass
class Briefing:
    as_of: date
    findings: list[Finding]
    summary: list[str]
    commentary: dict[str, dict]
    actions: list[Action]
    kri_counts: dict[str, int]
    trace: list
    mode: str = "Offline investigator (deterministic, no LLM)"

    def markdown(self) -> str:
        lines = ["# Ledgerline Bank: weekly risk and data-quality briefing", "",
                 f"Reporting date: {self.as_of.isoformat()} · Prepared by: Risk Insights Agent, {self.mode} · "
                 "Status: DRAFT for risk-owner review", "", "## Executive summary", ""]
        lines += [f"- {s}" for s in self.summary]
        lines += ["", "## Risk commentary by domain", ""]
        for domain, c in self.commentary.items():
            lines.append(f"### {ARROW[c['status']]} {domain} ({c['status']})")
            lines += [f"- {t}" for t in c["lines"]]
            lines.append("")
        lines += ["## Actions", "", "| # | Action | Owner | Due | Basis |", "|---|---|---|---|---|"]
        for i, a in enumerate(self.actions, 1):
            lines.append(f"| {i} | {a.action} | {a.owner} | {a.due.isoformat()} | {a.basis} |")
        lines += ["", "_All data is synthetic (fictional Ledgerline Bank). AI-assisted draft: numbers are computed "
                  "by the metric layer; root-cause statements are hypotheses for the owner to confirm._"]
        return "\n".join(lines)


class RiskInsightsAgent:
    def __init__(self, store: MetricStore, as_of: date | None = None, max_findings: int = 16) -> None:
        self.store = store
        self.as_of = as_of or store.last_date
        self.tools = RiskTools(store, self.as_of)
        self.max_findings = max_findings

    # ------------------------------------------------------------- triage
    def triage(self) -> list[tuple[int, dict, str]]:
        scan = self.tools.call("scan_metrics")
        flagged = []
        for row in scan["metrics"]:
            if row["direction"] == "none":
                continue
            m = catalog.get(row["metric_id"])
            now, worst, prior = row["status"], row["worst_7d_status"], row["status_7d_ago"]
            transient = STATUS_RANK[worst] > STATUS_RANK[now]
            if now == "GREEN" and not transient:
                continue
            score = {"RED": 80, "AMBER": 40}.get(now, 0) or (60 if worst == "RED" else 30)
            score += 20 if m.kri else 0
            score += 15 if STATUS_RANK[now] > STATUS_RANK[prior] else 0
            score += 5 if row["days_in_status"] <= 7 else 0
            flagged.append((score, row, "transient" if transient else "now"))
            if transient and now != "GREEN":
                flagged.append((score - 10, row, "now"))  # the incident is over, but the metric is still not green
        flagged.sort(key=lambda t: -t[0])
        return flagged[: self.max_findings]

    # -------------------------------------------------------- investigate
    def investigate(self, score: int, row: dict, mode: str = "now") -> Finding:
        m = catalog.get(row["metric_id"])
        t = self.tools
        transient = mode == "transient"
        focus = date.fromisoformat(row["worst_7d_date"]) if transient else self.as_of
        status = row["status"]
        f = Finding(m.id, m.name, m.domain, status, score, headline="", focus_date=focus)
        f.evidence = {"value": row["value"], "display": row["display"], "prior_7d": row["prior_7d"],
                      "status_7d_ago": row["status_7d_ago"], "days_in_status": row["days_in_status"]}
        if transient:
            f.evidence.update({"worst_7d": row["worst_7d"], "worst_7d_date": row["worst_7d_date"],
                               "worst_7d_status": row["worst_7d_status"]})

        # 2. Driver
        dim = member = None
        if m.dimensions:
            ex = t.call("explain_change", metric_id=m.id, on=focus.isoformat())
            if ex.get("by_dimension"):
                top = ex["by_dimension"][0]
                share = top["share_of_change"]
                material = abs(ex["total_change"]) > 0.02 * max(abs(ex["value"] or 0), 1e-9)
                if share is not None and share >= 0.4 and material:
                    dim, member = top["dimension"], top["top_member"]
                    f.driver = {"dimension": dim, "member": member, "contribution": top["top_contribution"],
                                "share": share, "total_change": ex["total_change"],
                                "compared_with": ex["compared_with"], "kind": "change"}
            if f.driver is None and status != "GREEN" and not transient and m.id not in SCHEDULED:
                lv = t.call("explain_level", metric_id=m.id, on=focus.isoformat())
                if lv.get("members"):
                    top = lv["members"][0]
                    dim, member = lv["dimension"], top["member"]
                    f.driver = {"dimension": dim, "member": member, "share": top["share"], "kind": "level",
                                "shortfall": top["shortfall"]}

        # 3. Onset
        kind = "step"
        if dim == "team":
            flow = t.call("exception_flow", team=member)
            f.evidence["flow"] = flow
            if flow.get("arrivals_onset"):
                f.onset = date.fromisoformat(flow["arrivals_onset"])
        if f.onset is None:
            on = t.call("find_onset", metric_id=m.id, dimension=dim, member=member, on=focus.isoformat())
            if on.get("onset"):
                f.onset, kind = date.fromisoformat(on["onset"]), on["kind"]
                f.evidence["onset_fit"] = on

        # 4. Root cause (scheduled-deadline KRIs are explained by their records, not by changes)
        if f.onset is not None and m.id not in SCHEDULED:
            cc = t.call("correlate_changes", metric_id=m.id, onset=f.onset.isoformat(), kind=kind,
                        dimension=dim, member=member)
            if cc["best"] and cc["confidence"] != "none":
                f.root_cause, f.confidence = cc["best"], cc["confidence"]

        # 5. Forecast
        f.forecast = t.call("forecast", metric_id=m.id, horizon_days=30)
        f.evidence["onset_kind"] = kind

        # 6. Evidence records
        if m.record_key:
            rec = t.call("list_records", metric_id=m.id, dimension=dim, member=member, limit=5)
            f.records = rec["records"]
            f.evidence["records_count"] = rec["count"]

        self._write(f, m, row, transient)
        return f

    # -------------------------------------------------------------- write
    def _write(self, f: Finding, m: Metric, row: dict, transient: bool) -> None:
        prior = m.fmt(row["prior_7d"])
        moved = ""
        if row["change_7d"] not in (None, 0):
            up = row["change_7d"] > 0
            moved = f", {'up' if up else 'down'} from {prior} a week ago"
        elif row["prior_7d"] is not None:
            moved = ", unchanged from a week ago"
        if transient:
            f.headline = (f"{m.name} hit {m.fmt(row['worst_7d'])} ({row['worst_7d_status']}) on "
                          f"{_fd(row['worst_7d_date'])} and is {row['display']} ({row['status']}) today")
        else:
            f.headline = f"{m.name} is {row['display']} ({row['status']}){moved}"
            if row["days_in_status"] > 7 and row["status"] != "GREEN":
                f.headline += f"; {row['status']} for {row['days_in_status']} days"
        thresholds = []
        if m.early_warning is not None:
            thresholds.append(f"early-warning {m.fmt(m.early_warning)}")
        if m.limit is not None:
            thresholds.append(f"limit {m.fmt(m.limit)}")
        if thresholds:
            f.detail.append(f"Thresholds: {', '.join(thresholds)}" + (f" ({m.policy_ref})." if m.policy_ref else "."))

        if f.driver and f.driver["kind"] == "level":
            d = f.driver
            f.detail.append(f"Driver: {d['dimension']} = {d['member']} accounts for {d['share']:.0%} of the "
                            "current shortfall.")
        elif f.driver:
            d = f.driver
            unit_change = m.fmt(abs(d["total_change"])) if m.unit != "%" else f"{abs(d['total_change']):.1f} pts"
            contrib = m.fmt(abs(d["contribution"])) if m.unit != "%" else f"{abs(d['contribution']):.1f} pts"
            sign = "+" if d["contribution"] >= 0 else "-"
            net = "+" if d["total_change"] >= 0 else "-"
            other = " (other members moved the other way)" if d["share"] > 1.05 else ""
            f.detail.append(f"Driver: {d['dimension']} = {d['member']} accounts for {sign}{contrib} of the "
                            f"{net}{unit_change} net change since {_fd(d['compared_with'])}{other}.")
        flow = f.evidence.get("flow")
        if flow and flow.get("arrivals_change_pct") is not None:
            f.detail.append(f"{flow['team']} receives {flow['arrivals_per_day_recent']:.1f} exceptions a day in the "
                            f"last {flow['window_days']} days vs {flow['arrivals_per_day_before']:.1f} before "
                            f"({flow['arrivals_change_pct']:+.0f}%), while closures run at "
                            f"{flow['closures_per_day_recent']:.1f} a day.")
        if f.onset and m.id not in SCHEDULED:
            label = f.evidence.get("onset_fit", {}).get("series", "")
            what = "decline" if f.evidence.get("onset_kind") == "ramp" else "move"
            f.detail.append(f"The {what} started on {_fd(f.onset)}"
                            + (f" (change-point in {label})." if label and label != m.id else "."))
        if f.root_cause:
            rc = f.root_cause
            gap = rc["days_before_onset"]
            when = "the same day" if gap == 0 else f"{abs(gap)} day{'s' if abs(gap) != 1 else ''} " + \
                ("before" if gap > 0 else "after")
            lineage = f"{rc['system']} feeds the affected data" if rc["lineage_match"] else "no lineage link"
            f.detail.append(f"Likely cause ({f.confidence} confidence): {rc['change_id']} on {_fd(rc['date'])}, "
                            f"{when}: \"{rc['description']}\" ({lineage}).")
        elif f.onset and m.id not in SCHEDULED:
            f.detail.append("No release, incident or business change with a lineage link lines up with it.")
        if m.id == "KRI_HR_KYC_OVERDUE_PCT" and f.forecast and f.forecast.get("overdue_customers"):
            c = f.forecast["overdue_customers"][0]
            f.detail.append(f"Cause: the annual review for {c['customer_id']} fell due on {_fd(c['due_date'])} and "
                            "has not been completed.")
        if m.id == "KRI_AML_ALERTS_OVER_30D" and f.records:
            r = f.records[0]
            f.detail.append(f"Cause: the {r['rule_code']} alert for {r['customer_id']} raised on "
                            f"{_fd(r['alert_date'])} is {r['age_days']} days old with no review.")

        fc = f.forecast or {}
        if fc.get("first_red_date") and m.id == "KRI_HR_KYC_OVERDUE_PCT":
            due = fc["falling_due"]
            f.detail.append(f"Forecast: {fc['overdue_now']} of {fc['population']} high-risk customers overdue now; "
                            f"{len(due)} more reviews fall due in the next 30 days, taking it to "
                            f"{fc['projected_pct_end']:.1f}% if they are not completed.")
        elif m.id == "KRI_AML_ALERTS_OVER_30D" and fc.get("next_breaches") is not None:
            nb = fc["next_breaches"]
            if nb:
                first = nb[0]
                f.detail.append(f"Forecast: {len(nb)} more alert{'s' if len(nb) != 1 else ''} pass the 30-day "
                                f"deadline in the next 30 days, the first on {_fd(first['breaches_on'])} "
                                f"({first['customer_id']}, {first['rule_code']}).")
        elif (f.evidence.get("onset_kind") == "step" and f.onset and (self.as_of - f.onset).days <= 14
              and f.root_cause and f.evidence.get("records_count") and not f.evidence.get("flow")):
            n = f.evidence.get("records_count")
            items = f" the {n} affected items are" if n else " the affected items are"
            f.detail.append(f"Outlook: this is a level shift since {_fd(f.onset)}, not a trend; it will not "
                            f"self-correct until{items} cleared.")
        elif fc.get("method", "").startswith("Theil-Sen"):
            if fc.get("limit_date") and row["status"] != "RED":
                f.detail.append(f"Forecast: on the current trend it breaches the limit around {_fd(fc['limit_date'])}"
                                f" ({fc['days_to_limit']} days).")
            elif fc.get("early_warning_date") and row["status"] == "GREEN":
                f.detail.append(f"Forecast: on the current trend it reaches the early-warning trigger around "
                                f"{_fd(fc['early_warning_date'])}.")
            elif fc.get("projected_end") is not None and row["status"] == "RED":
                f.detail.append(f"Forecast: on the current trend it stays {fc['projected_status_end']} "
                                f"({m.fmt(fc['projected_end'])} in 30 days).")
        self._actions(f, m, row)

    def _actions(self, f: Finding, m: Metric, row: dict) -> None:
        t, a = self.tools, self.as_of
        status = row["status"]
        # Escalation (RAS-004 s.6 / AML-002 s.3)
        if m.kri and m.policy_ref:
            t.call("get_policy", policy_ref="RAS-004 s.6")
            if m.zero_tolerance and status == "RED":
                f.actions.append(Action(f"Report the {m.name.lower()} breach to the Board Risk Committee chair",
                                        "Chief Risk Officer", a + timedelta(days=1), "RAS-004 s.6: zero-tolerance "
                                        "breaches within 24 hours"))
            elif status == "RED":
                f.actions.append(Action(f"Notify the CRO of the {m.name.lower()} limit breach",
                                        m.owner, _bd(a, 2), "RAS-004 s.6: limit breach within 2 business days"))
            elif status == "AMBER":
                f.actions.append(Action(f"Present a remediation plan for {m.name.lower()} to the CRO",
                                        m.owner, _bd(a, 5), "RAS-004 s.6: early-warning within 5 business days"))
        fc, rc = f.forecast or {}, f.root_cause
        if m.id == "KRI_HR_KYC_OVERDUE_PCT" and fc.get("overdue_customers") is not None:
            ids = ", ".join(c["customer_id"] for c in fc["overdue_customers"]) or "none"
            f.actions.append(Action(f"Complete the overdue high-risk KYC review(s) for {ids} and the "
                                    f"{len(fc['falling_due'])} falling due", m.owner, _bd(a, 3),
                                    "Restores the KRI to green"))
            wi = t.call("what_if", scenario="kyc_reviews_completed", amount=fc["overdue_now"])
            f.evidence["what_if"] = wi
        elif m.id == "KRI_AML_ALERTS_OVER_30D":
            nxt = (fc.get("next_breaches") or [])[:3]
            who = ", ".join(f"{c['customer_id']} (deadline {_fd(c['breaches_on'])})" for c in nxt)
            f.actions.append(Action("Close or escalate the overdue Level 2 AML investigation(s) and prioritise "
                                    + (f"alerts for {who}" if who else "the remaining queue"),
                                    "AML Officer", _bd(a, 1), "AML-002 s.3: 30-day investigation deadline"))
        elif m.id == "KRI_LCR_PCT":
            wi = t.call("what_if", scenario="deposit_outflow", amount=250)
            f.evidence["what_if"] = wi
            f.detail.append(f"What-if: a further USD 250m outflow would take LCR to {wi['lcr_after_pct']:.1f}% "
                            f"({wi['status_after']}); the bank can absorb about USD "
                            f"{wi['max_outflow_before_limit_usd_m']:,.0f}m more before the 110% limit.")
            f.actions.append(Action("Review deposit concentration and the contingency funding plan; confirm HQLA "
                                    "buffer actions with ALCO", "Treasurer", _bd(a, 2),
                                    "LCR below the 120% early-warning trigger"))
        elif m.id == "KRI_OPLOSS_YTD_USD_M":
            wi = t.call("what_if", scenario="op_loss_event", amount=1.0)
            f.evidence["what_if"] = wi
            f.detail.append(f"Headroom to the USD 15m limit is USD {wi['headroom_to_limit_usd_m']:.2f}m.")
        if f.driver and f.driver["dimension"] == "team" and f.driver["member"] == "Payments Ops":
            flow = f.evidence.get("flow", {})
            base = t.call("what_if", scenario="payments_extra_handlers", amount=0)
            n = max(1, base["handlers_to_stop_growth"] + 2)
            wi = t.call("what_if", scenario="payments_extra_handlers", amount=n)
            f.evidence["what_if"] = wi
            clear = f"clears it by {_fd(wi['backlog_clear_date'])}" if wi["backlog_clear_date"] else \
                f"cuts it to {wi['backlog_in_10_days_scenario']} in 10 business days"
            f.detail.append(f"What-if: the Payments Ops backlog of {wi['backlog_now']} grows to "
                            f"{base['backlog_in_10_days_baseline']} in 10 business days as is; adding {n} temporary "
                            f"handlers {clear}.")
            if flow:
                f.actions.append(Action(f"Add {n} temporary handlers to Payments Ops and agree a capacity plan for "
                                        "the EUR instant-payments corridor", "Head of Operations", _bd(a, 2),
                                        "Exceptions SLA breach and backlog"))
        if rc and f.confidence in ("high", "medium") and not any("capacity plan" in x.action for x in f.actions):
            kind = rc["type"]
            if kind == "RELEASE":
                follow = self._follow_up_change(rc)
                fix = f"; confirm {follow['change_id']} ({_fd(follow['date'])}) fixed new items" if follow else ""
                f.actions.append(Action(f"Post-implementation review of {rc['change_id']} with {rc['system']} and "
                                        f"clear the affected items{fix}", m.owner, _bd(a, 3),
                                        f"Root cause hypothesis ({f.confidence} confidence)"))
            elif kind == "INCIDENT" and m.id != "KRI_OPLOSS_YTD_USD_M":
                f.actions.append(Action(f"Close incident {rc['change_id']}: confirm full re-delivery and re-run the "
                                        "failed controls", m.owner, _bd(a, 1),
                                        f"Root cause hypothesis ({f.confidence} confidence)"))
            elif kind == "INCIDENT":
                f.actions.append(Action(f"Complete the root-cause analysis for {rc['change_id']} and confirm the "
                                        "recovery position", "Head of Operational Risk", a + timedelta(days=30),
                                        "ORM-005: RCA within 30 days"))
        if m.id == "DQ_RULES_BREACHED" and f.driver:
            f.actions.append(Action(f"Fix the source of rule {f.driver['member']} and backfill the failing records",
                                    m.owner, _bd(a, 10), "Data-quality rule below threshold"))

    def _follow_up_change(self, rc: dict) -> dict | None:
        events = self.tools._query("q_change_events")
        for e in events.itertuples():
            d = e.event_date.date() if hasattr(e.event_date, "date") else e.event_date
            if e.system == rc["system"] and date.fromisoformat(str(rc["date"])) < d <= self.as_of:
                return {"change_id": e.change_id, "date": d}
        return None

    # --------------------------------------------------------------- run
    def run(self) -> Briefing:
        findings = [self.investigate(score, row, mode) for score, row, mode in self.triage()]
        themes: dict[str, Finding] = {}
        for f in sorted(findings, key=lambda x: -x.priority):
            key = f.theme_key
            if key in themes:
                themes[key].related.append(f.metric_id)
            else:
                themes[key] = f
        ranked = sorted(themes.values(), key=lambda x: -x.priority)
        return Briefing(self.as_of, ranked, self._summary(ranked), self._commentary(ranked),
                        self._action_list(ranked), self._kri_counts(), list(self.tools.trace))

    def _kri_counts(self) -> dict[str, int]:
        counts = {"RED": 0, "AMBER": 0, "GREEN": 0}
        for m in catalog.METRICS:
            if m.kri:
                counts[self.store.snapshot(m.id, self.as_of).status] += 1
        return counts

    def _summary(self, themes: list[Finding]) -> list[str]:
        k = self._kri_counts()
        total = sum(k.values())
        out = [f"{k['RED'] + k['AMBER']} of {total} risk-appetite KRIs are outside appetite this week "
               f"({k['RED']} red, {k['AMBER']} amber)."]
        for f in themes[:6]:
            sentence = f.headline
            if f.root_cause and f.confidence in ("high", "medium"):
                sentence += f", most likely caused by {f.root_cause['change_id']} ({f.root_cause['description']})"
            elif f.driver:
                sentence += f", driven by {f.driver['member']}"
            if f.related:
                names = ", ".join(catalog.get(r).name.lower() for r in f.related)
                sentence += f"; also moves {names}"
            out.append(sentence + ".")
        return out

    def _commentary(self, themes: list[Finding]) -> dict[str, dict]:
        by_metric = {}
        for f in themes:
            by_metric[f.metric_id] = f
            for r in f.related:
                by_metric.setdefault(r, f)
        out = {}
        for domain in catalog.DOMAINS:
            metrics = [m for m in catalog.METRICS if m.domain == domain]
            if not metrics:
                continue
            statuses = [self.store.snapshot(m.id, self.as_of).status for m in metrics]
            worst = max(statuses, key=lambda s: STATUS_RANK[s])
            lines, seen = [], set()
            for m, s in zip(metrics, statuses, strict=True):
                snap = self.store.snapshot(m.id, self.as_of)
                f = by_metric.get(m.id)
                if f is not None and id(f) not in seen and f.metric_id == m.id:
                    seen.add(id(f))
                    lines.append(f.headline + ". " + " ".join(f.detail[1:3]))
                elif s == "NONE":
                    lines.append(self._context_line(m, snap.value))
                elif s == "GREEN" or f is None:
                    lines.append(f"{m.name}: {m.fmt(snap.value)} ({s.lower()}).")
                else:
                    lines.append(f"{m.name}: {m.fmt(snap.value)} ({s.lower()}), see {f.name.lower()}.")
            out[domain] = {"status": "GREEN" if worst == "NONE" else worst, "lines": lines}
        return out

    def _context_line(self, m, value) -> str:
        if value is not None and value == value:
            return f"{m.name}: {m.fmt(value)} (context, no threshold)."
        series = self.store.series(m.id)
        known = series[(series.index <= self.as_of)].dropna()
        if known.empty:
            return f"{m.name}: no data yet (context)."
        return (f"{m.name}: {m.fmt(known.iloc[-1])} on {_fd(known.index[-1])}, the latest day with data "
                "(context, no threshold).")

    @staticmethod
    def _action_list(themes: list[Finding]) -> list[Action]:
        seen, out = set(), []
        for f in themes:
            for a in f.actions:
                if a.action not in seen:
                    seen.add(a.action)
                    out.append(a)
        return sorted(out, key=lambda a: a.due)
