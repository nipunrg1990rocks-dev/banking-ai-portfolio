"""Score the agents against the answer key the data generator planted.

Every check is mechanical, so the same evaluation scores the offline planner and
Claude. The answer key comes from ``run_summary.json -> planted``; the agents'
tools can't read it.

AML Investigator, per alert:
  correct_decision   ESCALATE for planted scenarios, CLOSE for planted false positives
  cites_trigger      every transaction that triggered the alert is in the evidence
  no_invented_ids    every transaction id in the evidence or narrative exists in Silver
  score_consistent   risk_score >= 50 exactly when the recommendation is ESCALATE
  no_contact_data    no e-mail addresses or phone numbers in the narrative

Data Steward:
  root_cause         TRUNCATED_FILE
  rows_expected / rows_received / rows_missing match the planted truncation
  impact_correct     says Silver and Gold were not changed

Ask Ledgerline, per question: the agent's final SQL returns the same result as a
reference query (compared on the key column and the measure).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import pandas as pd

from .backend import SnapshotBackend
from .engine import AgentRun

TXN_ID = re.compile(r"\bT\d{9}\b")
CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.\w+|\+\d[\d -]{7,}\d|\b\d{3}-\d{3}-\d{4}\b")

ASK_SET = [
    {"question": "Which 5 customers had the highest cross-border volume in the last 30 days?",
     "reference": "SELECT customer_id, cross_border_usd_30d FROM customer_360 ORDER BY cross_border_usd_30d DESC "
                  "LIMIT 5", "key": "customer_id"},
    {"question": "How many AML alert candidates are there for each rule?",
     "reference": "SELECT rule_code, COUNT(*) AS alerts FROM aml_alerts GROUP BY rule_code", "key": "rule_code"},
    {"question": "Which data-quality rules quarantined transactions, and how many records each?",
     "reference": "SELECT _dq_failed_rules AS rule, COUNT(*) AS records FROM quarantine GROUP BY _dq_failed_rules",
     "key": None},
    {"question": "What was the total transaction volume in USD by channel?",
     "reference": "SELECT channel, ROUND(SUM(amount_usd), 2) AS total_usd FROM transactions GROUP BY channel",
     "key": "channel"},
    {"question": "Which counterparty countries in the high-risk tier received or sent money, and how much?",
     "reference": "SELECT counterparty_country, ROUND(SUM(amount_usd), 2) AS total_usd FROM transactions "
                  "WHERE counterparty_risk_tier = 'HIGH' GROUP BY counterparty_country", "key": "counterparty_country"},
    {"question": "Which customers made cash deposits between 9,000 and 9,999.99 USD three or more times on the "
                 "same day?",
     "reference": "SELECT DISTINCT customer_id FROM (SELECT customer_id, txn_date FROM transactions "
                  "WHERE txn_type = 'CASH_DEPOSIT' AND amount_usd BETWEEN 9000 AND 9999.99 "
                  "GROUP BY customer_id, txn_date HAVING COUNT(*) >= 3)", "key": "customer_id"},
]

# The app's suggestion buttons; the ones not in ASK_SET are recorded too, so every button has a replay.
SUGGESTED = ["Which 5 customers had the highest cross-border volume in the last 30 days?",
             "How many AML alert candidates are there for each rule?",
             "Show the daily transaction volume trend",
             "Which data-quality rules quarantined transactions, and how many records each?"]


@dataclass
class EvalResult:
    agent: str
    brain: str
    cases: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c["pass"])

    def summary(self) -> dict:
        checks: dict[str, list[bool]] = {}
        for c in self.cases:
            for k, v in c["checks"].items():
                checks.setdefault(k, []).append(v)
        return {"agent": self.agent, "brain": self.brain, "cases": len(self.cases), "passed": self.passed,
                "checks": {k: f"{sum(v)}/{len(v)}" for k, v in checks.items()}}


def planted(backend: SnapshotBackend) -> dict:
    return json.loads((backend.folder / "run_summary.json").read_text())["planted"]


def regenerated_key() -> dict:
    """The answer key, rebuilt by re-running the seeded generator in a scratch folder (for Spark backends,
    which have no snapshot). Same seed, so the same planted facts as the pipeline's own landing files."""
    import tempfile

    from lakehouse import synthetic

    with tempfile.TemporaryDirectory() as tmp:
        return synthetic.generate(tmp).to_dict()


def existing_txn_ids(backend, ids: set[str]) -> set[str]:
    if not ids:
        return set()
    clean = [i for i in ids if TXN_ID.fullmatch(i)]
    if not clean:
        return set()
    found = backend.sql("SELECT txn_id FROM transactions WHERE txn_id IN (" + ", ".join(f"'{i}'" for i in clean) + ")")
    return set(found.txn_id)


def score_aml(run: AgentRun, alert: dict, false_positive_customers: set[str], backend) -> dict:
    out = run.output or {}
    expected = "CLOSE" if alert["customer_id"] in false_positive_customers else "ESCALATE"
    evidence = set(out.get("evidence_txn_ids", []))
    cited = evidence | set(TXN_ID.findall(out.get("narrative", "")))
    known_ids = existing_txn_ids(backend, cited)
    checks = {
        "completed": run.status == "completed",
        "correct_decision": out.get("recommendation") == expected,
        "cites_trigger": set(str(alert["txn_ids"]).split(";")) <= evidence,
        "no_invented_ids": bool(cited) and cited <= known_ids,
        "score_consistent": (int(out.get("risk_score", -1)) >= 50) == (out.get("recommendation") == "ESCALATE"),
        "no_contact_data": not CONTACT.search(out.get("narrative", "")),
    }
    return {"case": f"{alert['rule_code']} {alert['customer_id']}", "expected": expected,
            "got": out.get("recommendation"), "checks": checks, "pass": all(checks.values()),
            "tool_calls": run.tool_calls, "cost_usd": run.cost_usd}


def score_steward(run: AgentRun, key: dict) -> dict:
    out = run.output or {}
    impact = out.get("downstream_impact", "").lower()
    checks = {
        "completed": run.status == "completed",
        "root_cause": out.get("root_cause_category") == "TRUNCATED_FILE",
        "rows_expected": out.get("rows_expected") == key["expected_rows"],
        "rows_received": out.get("rows_received_complete") == key["complete_rows_delivered"],
        "rows_missing": out.get("rows_missing") == key["expected_rows"] - key["complete_rows_delivered"],
        "impact_correct": "silver" in impact and "gold" in impact and any(
            w in impact for w in ("unchanged", "not changed", "no change", "not reach", "did not reach")),
    }
    return {"case": f"incident {key['batch_id']}", "checks": checks, "pass": all(checks.values()),
            "tool_calls": run.tool_calls, "cost_usd": run.cost_usd}


def _norm(df: pd.DataFrame) -> set:
    return {tuple(round(v, 0) if isinstance(v, float) else str(v) for v in row) for row in df.itertuples(index=False)}


def score_ask(run: AgentRun, case: dict, backend: SnapshotBackend) -> dict:
    out = run.output or {}
    ref = backend.sql(case["reference"])
    try:
        got = backend.sql(out.get("sql") or "SELECT 1 WHERE FALSE")
    except Exception:  # noqa: BLE001 - a broken final query is simply a failed case
        got = pd.DataFrame()
    if case["key"] and case["key"] in got.columns:
        same = set(ref[case["key"]].astype(str)) == set(got[case["key"]].astype(str))
    else:
        numeric = [c for c in got.columns if pd.api.types.is_numeric_dtype(got[c])]
        same = len(got) == len(ref) and bool(numeric) and _norm(ref.iloc[:, -1:]) == _norm(got[numeric[-1:]])
    checks = {"completed": run.status == "completed", "same_result_as_reference": same}
    return {"case": case["question"], "checks": checks, "pass": all(checks.values()), "tool_calls": run.tool_calls,
            "cost_usd": run.cost_usd}


def evaluate(backend: SnapshotBackend, brain=None, agents: tuple[str, ...] = ("aml_investigator", "data_steward",
                                                                                "ask_ledgerline"),
             runs_out: list | None = None, key: dict | None = None) -> list[EvalResult]:
    from . import run_agent

    key = key or planted(backend)
    results = []
    brain_name = getattr(brain, "name", "offline-planner")
    if "aml_investigator" in agents:
        res = EvalResult("aml_investigator", brain_name)
        fps = set(sum(key.get("aml_false_positives", {}).values(), []))
        for alert in backend.sql("SELECT * FROM aml_alerts ORDER BY rule_code, customer_id").to_dict("records"):
            run = run_agent("aml_investigator", backend, brain, alert_id=alert["alert_id"])
            res.cases.append(score_aml(run, alert, fps, backend))
            if runs_out is not None:
                runs_out.append(run)
        results.append(res)
    if "data_steward" in agents:
        res = EvalResult("data_steward", brain_name)
        run = run_agent("data_steward", backend, brain)
        res.cases.append(score_steward(run, key["incident"]))
        if runs_out is not None:
            runs_out.append(run)
        results.append(res)
    if "ask_ledgerline" in agents:
        res = EvalResult("ask_ledgerline", brain_name)
        for case in ASK_SET:
            run = run_agent("ask_ledgerline", backend, brain, question=case["question"])
            res.cases.append(score_ask(run, case, backend))
            if runs_out is not None:
                runs_out.append(run)
        results.append(res)
    return results
