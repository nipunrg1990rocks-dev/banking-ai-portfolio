"""The tools the agents can call. Each one is a small, read-only, well-described function.

Design rules (they are what make the agents safe to put in front of a bank's data):

* Tools only read. Nothing an agent does can change a table, send a message or
  close an alert. Decisions are recorded only when a human approves them.
* Inputs are validated (ids must look like ids), so a tool can't be steered into
  arbitrary SQL. Free-form SQL goes through ``run_sql``, which is read-only and capped.
* Outputs are small. A tool returns at most a few dozen rows, so the model sees
  evidence, not a data dump, and every row it cites can be traced.
* No names, e-mails or phone numbers exist in the tables the tools can reach.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from explorer import QueryError

from .backend import Backend

MAX_ROWS_TO_MODEL = 25

IDS = {
    "customer_id": re.compile(r"^C\d{6}$"),
    "txn_id": re.compile(r"^T\d{9}$"),
    "alert_id": re.compile(r"^[0-9a-f]{8,64}$"),
    "batch_id": re.compile(r"^\d{8}$"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
}


class ToolError(ValueError):
    """A problem the agent can fix (bad id, bad SQL). Returned to the model as an error result."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema properties
    required: list[str]
    fn: Callable[..., dict]
    terminal: bool = False

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "strict": True,
            "input_schema": {"type": "object", "properties": self.parameters, "required": self.required,
                             "additionalProperties": False},
        }


def _check(kind: str, value: str) -> str:
    value = str(value).strip()
    if not IDS[kind].match(value):
        raise ToolError(f"{kind} {value!r} is not valid")
    return value


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else round(value, 2)
    if hasattr(value, "item"):  # numpy scalars
        return _clean(value.item())
    if isinstance(value, (pd.Timestamp, date)):
        return str(value)
    return value


def records(df: pd.DataFrame, limit: int = MAX_ROWS_TO_MODEL) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in df.head(limit).to_dict("records")]


def _in_list(ids: list[str], kind: str) -> str:
    return ", ".join(f"'{_check(kind, i)}'" for i in ids)


class LakehouseTools:
    """Builds the tool set over a backend. Agents get a subset (see ``agents.specs``)."""

    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self.all: dict[str, Tool] = {t.name: t for t in self._build()}

    def q(self, sql: str) -> pd.DataFrame:
        try:
            return self.backend.sql(sql)
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    # ---------------------------------------------------------------- general
    def list_tables(self) -> dict:
        return {"dialect": self.backend.dialect, "tables": self.backend.tables()}

    def run_sql(self, query: str) -> dict:
        df = self.q(query)
        return {"row_count": len(df), "columns": list(df.columns), "rows": records(df),
                "truncated": len(df) > MAX_ROWS_TO_MODEL}

    # ---------------------------------------------------------------- AML
    def list_alerts(self) -> dict:
        df = self.q("SELECT alert_id, rule_code, customer_id, alert_date, amount_usd, priority, status "
                    "FROM aml_alerts ORDER BY priority, alert_date")
        return {"alerts": records(df, 50)}

    def get_alert(self, alert_id: str) -> dict:
        alert_id = _check("alert_id", alert_id)
        df = self.q(f"SELECT * FROM aml_alerts WHERE alert_id LIKE '{alert_id}%'")
        if df.empty:
            raise ToolError(f"No alert {alert_id}")
        alert = records(df, 1)[0]
        alert["txn_ids"] = str(alert["txn_ids"]).split(";")
        return alert

    def get_transactions(self, txn_ids: list[str]) -> dict:
        if not txn_ids or len(txn_ids) > 50:
            raise ToolError("Pass between 1 and 50 transaction ids")
        df = self.q("SELECT txn_id, customer_id, account_id, txn_ts, txn_type, channel, direction, amount, currency, "
                    "amount_usd, counterparty_name, counterparty_country, counterparty_risk_tier FROM transactions "
                    f"WHERE txn_id IN ({_in_list(txn_ids, 'txn_id')}) ORDER BY txn_ts")
        missing = sorted(set(txn_ids) - set(df.txn_id))
        return {"transactions": records(df, 50), "not_found": missing}

    def get_customer_profile(self, customer_id: str) -> dict:
        cid = _check("customer_id", customer_id)
        profile = self.q(f"SELECT * FROM customer_360 WHERE customer_id = '{cid}'")
        if profile.empty:
            raise ToolError(f"No customer {cid}")
        history = self.q("SELECT segment, kyc_risk_rating, country, pep_flag, effective_from, effective_to, is_current "
                         f"FROM customer_history WHERE customer_id = '{cid}' ORDER BY effective_from")
        accounts = self.q("SELECT account_id, product, currency, open_date, status FROM accounts "
                          f"WHERE customer_id = '{cid}' ORDER BY account_id")
        return {"profile": records(profile, 1)[0], "kyc_history": records(history),
                "kyc_history_note": "Rows appear only for customers whose profile changed (SCD Type 2).",
                "accounts": records(accounts)}

    def get_customer_activity(self, customer_id: str, start_date: str, end_date: str) -> dict:
        cid = _check("customer_id", customer_id)
        start, end = _check("date", start_date), _check("date", end_date)
        where = f"customer_id = '{cid}' AND txn_date BETWEEN '{start}' AND '{end}'"
        by_type = self.q("SELECT txn_type, direction, COUNT(*) AS txns, ROUND(SUM(amount_usd), 2) AS total_usd, "
                         "ROUND(MAX(amount_usd), 2) AS max_usd, "
                         "SUM(CASE WHEN counterparty_risk_tier = 'HIGH' THEN 1 ELSE 0 END) AS high_risk_txns "
                         f"FROM transactions WHERE {where} GROUP BY txn_type, direction ORDER BY total_usd DESC")
        largest = self.q("SELECT txn_id, txn_ts, txn_type, amount_usd, counterparty_name, counterparty_country, "
                         f"counterparty_risk_tier FROM transactions WHERE {where} ORDER BY amount_usd DESC LIMIT 10")
        return {"window": [start, end], "by_type": records(by_type), "largest_transactions": records(largest)}

    def compare_to_peers(self, customer_id: str) -> dict:
        cid = _check("customer_id", customer_id)
        me = self.q(f"SELECT segment FROM customer_360 WHERE customer_id = '{cid}'")
        if me.empty:
            raise ToolError(f"No customer {cid}")
        segment = me.iloc[0]["segment"]
        cols = ["inflow_usd_30d", "outflow_usd_30d", "cash_usd_30d", "cross_border_usd_30d", "txn_count_30d"]
        peers = self.q(f"SELECT customer_id, {', '.join(cols)} FROM customer_360 WHERE segment = '{segment}'")
        mine = peers[peers.customer_id == cid].iloc[0]
        out = {}
        for c in cols:
            series = pd.to_numeric(peers[c], errors="coerce").fillna(0)
            value = float(pd.to_numeric(pd.Series([mine[c]]), errors="coerce").fillna(0).iloc[0])
            out[c] = {"customer": round(value, 2), "segment_median": round(float(series.median()), 2),
                      "segment_p90": round(float(series.quantile(0.9)), 2),
                      "percentile": round(float((series < value).mean() * 100), 1)}
        return {"segment": segment, "peers": int(len(peers)), "metrics": out}

    def find_counterparty_history(self, customer_id: str, counterparty_name: str) -> dict:
        cid = _check("customer_id", customer_id)
        name = counterparty_name.replace("'", "''")[:80]
        df = self.q("SELECT txn_id, txn_ts, txn_type, amount_usd, counterparty_country FROM transactions "
                    f"WHERE customer_id = '{cid}' AND counterparty_name = '{name}' ORDER BY txn_ts")
        return {"counterparty": counterparty_name, "transactions": records(df), "count": len(df)}

    def counterparty_network(self, counterparty_name: str) -> dict:
        name = counterparty_name.replace("'", "''")[:80]
        df = self.q("SELECT customer_id, COUNT(*) AS txns, ROUND(SUM(amount_usd), 2) AS total_usd, "
                    "MIN(txn_date) AS first_date, MAX(txn_date) AS last_date FROM transactions "
                    f"WHERE counterparty_name = '{name}' GROUP BY customer_id ORDER BY total_usd DESC")
        return {"counterparty": counterparty_name, "customers": records(df), "customer_count": len(df)}

    # ---------------------------------------------------------- data quality
    def get_pipeline_status(self) -> dict:
        steps = self.q("SELECT step, layer, table_name, batch_id, status, rows_in, rows_written, rows_quarantined, "
                       "details FROM pipeline_steps ORDER BY finished_at")
        failed = steps[steps.status != "SUCCEEDED"]
        loaded = steps[(steps.step == "silver") & (steps.table_name == "transactions")]
        return {"steps_run": len(steps), "failed_steps": records(failed),
                "batches_loaded_to_silver": sorted(loaded.batch_id.dropna().unique().tolist()),
                "incident": self.backend.incident()}

    def get_dq_findings(self, batch_id: str) -> dict:
        b = _check("batch_id", batch_id)
        df = self.q("SELECT layer, table_name, rule_name, action, description, rows_checked, rows_failed "
                    f"FROM dq_results WHERE batch_id = '{b}' ORDER BY rows_failed DESC")
        return {"batch_id": b, "checks": records(df, 40), "failing": records(df[df.rows_failed > 0], 40)}

    def inspect_delivery(self, batch_id: str) -> dict:
        try:
            return self.backend.delivery(_check("batch_id", batch_id))
        except QueryError as exc:
            raise ToolError(str(exc)) from exc

    def get_delivery_history(self) -> dict:
        out = []
        for batch_id, d in sorted(self.backend.deliveries().items()):
            txn = d["files"].get("transactions.jsonl", {})
            out.append({"batch_id": batch_id, "business_date": d["business_date"],
                        "manifest_transactions": d["manifest_record_counts"]["transactions"],
                        "manifest_control_total": d["manifest_control_total"],
                        "file_bytes_expected": txn.get("expected_bytes"),
                        "file_bytes_received": txn.get("actual_bytes"),
                        "checksum_matches": txn.get("checksum_matches")})
        daily = self.q("SELECT txn_date, SUM(txn_count) AS txns FROM daily_summary GROUP BY txn_date")
        per_day = pd.to_numeric(daily.txns)
        return {"deliveries": out, "avg_transactions_per_day": round(float(per_day.mean()), 1),
                "min_transactions_per_day": int(per_day.min()), "max_transactions_per_day": int(per_day.max())}

    # ------------------------------------------------------------- build
    def _build(self) -> list[Tool]:
        s, arr = {"type": "string"}, {"type": "array", "items": {"type": "string"}}
        return [
            Tool("list_tables", "List the lakehouse tables you can query and their columns, plus the SQL dialect.",
                 {}, [], self.list_tables),
            Tool("run_sql", "Run ONE read-only SQL query (SELECT/WITH) against the lakehouse tables. Returns at most "
                 "25 rows to you. Use list_tables first if unsure of column names.",
                 {"query": {**s, "description": "A single SELECT or WITH statement"}}, ["query"], self.run_sql),
            Tool("list_alerts", "List all AML alert candidates (id, rule, customer, date, amount, priority).",
                 {}, [], self.list_alerts),
            Tool("get_alert", "Get one AML alert candidate: rule, rationale and the transaction ids that triggered it.",
                 {"alert_id": {**s, "description": "Alert id (or its first 8+ characters)"}}, ["alert_id"],
                 self.get_alert),
            Tool("get_transactions", "Get full details of specific transactions by id (max 50).",
                 {"txn_ids": arr}, ["txn_ids"], self.get_transactions),
            Tool("get_customer_profile", "Customer 360 profile (segment, KYC risk rating, PEP flag, 30-day activity, "
                 "open alerts), KYC rating history (SCD Type 2) and accounts. No names or contact details.",
                 {"customer_id": s}, ["customer_id"], self.get_customer_profile),
            Tool("get_customer_activity", "A customer's transactions between two dates (inclusive), totalled by type "
                 "and direction, plus the 10 largest.",
                 {"customer_id": s, "start_date": {**s, "description": "YYYY-MM-DD"},
                  "end_date": {**s, "description": "YYYY-MM-DD"}},
                 ["customer_id", "start_date", "end_date"], self.get_customer_activity),
            Tool("compare_to_peers", "Compare a customer's 30-day inflow, outflow, cash and cross-border volume with "
                 "customers in the same segment (median, 90th percentile, percentile rank).",
                 {"customer_id": s}, ["customer_id"], self.compare_to_peers),
            Tool("find_counterparty_history", "Every transaction between a customer and one named counterparty, to "
                 "check whether a flow is recurring.",
                 {"customer_id": s, "counterparty_name": s}, ["customer_id", "counterparty_name"],
                 self.find_counterparty_history),
            Tool("counterparty_network", "Which customers of the bank transact with a named counterparty, and how "
                 "much. Use it to spot several unrelated customers paying the same party.",
                 {"counterparty_name": s}, ["counterparty_name"], self.counterparty_network),
            Tool("get_pipeline_status", "The latest pipeline run: failed steps with their error, which batches reached "
                 "Silver, and the open incident if any.", {}, [], self.get_pipeline_status),
            Tool("get_dq_findings", "Every data-quality rule and control evaluated for one batch, with failures.",
                 {"batch_id": s}, ["batch_id"], self.get_dq_findings),
            Tool("inspect_delivery", "File-level evidence for one delivery in the landing zone: bytes and SHA-256 "
                 "versus the manifest, line counts, and whether the last line is complete.",
                 {"batch_id": s}, ["batch_id"], self.inspect_delivery),
            Tool("get_delivery_history", "All deliveries received (manifest counts, control totals, file sizes) and "
                 "the normal daily transaction volume, to judge what a healthy batch looks like.",
                 {}, [], self.get_delivery_history),
        ]


def _submit(**kwargs) -> dict:
    return {"status": "submitted", **kwargs}


def terminal_tools() -> dict[str, Tool]:
    s, arr = {"type": "string"}, {"type": "array", "items": {"type": "string"}}
    return {t.name: t for t in [
        Tool("submit_answer", "Submit your final answer to the user's question. Call this exactly once, at the end.",
             {"answer": {**s, "description": "Plain-English answer with the key numbers"},
              "sql": {**s, "description": "The final SQL query your answer is based on"},
              "chart": {"type": "object", "additionalProperties": False, "required": ["type", "x", "y"],
                        "properties": {"type": {"type": "string", "enum": ["bar", "line", "none"]},
                                       "x": {**s, "description": "Column for the x axis, or empty"},
                                       "y": {**s, "description": "Numeric column for the y axis, or empty"}}}},
             ["answer", "sql", "chart"], _submit, terminal=True),
        Tool("submit_case", "Submit your investigation of one AML alert for a human investigator to approve. "
             "Call this exactly once, at the end. You recommend; a human decides.",
             {"alert_id": s,
              "recommendation": {"type": "string", "enum": ["ESCALATE", "CLOSE"],
                                 "description": "ESCALATE = refer for a suspicious activity report decision; "
                                                "CLOSE = activity explained, no further action"},
              "risk_score": {"type": "integer", "description": "0-100. 50 or more means escalate."},
              "headline": {**s, "description": "One sentence a manager can read in five seconds"},
              "narrative": {**s, "description": "Case narrative: what happened, what you checked, why you conclude "
                                                "this. Cite transaction ids. 120-250 words."},
              "red_flags": arr, "mitigating_factors": arr,
              "evidence_txn_ids": {**arr, "description": "Every transaction id your conclusion relies on"},
              "next_steps": arr},
             ["alert_id", "recommendation", "risk_score", "headline", "narrative", "red_flags", "mitigating_factors",
              "evidence_txn_ids", "next_steps"], _submit, terminal=True),
        Tool("submit_incident_report", "Submit your data incident report and the draft ticket for the data owner. "
             "Call this exactly once, at the end. A human approves before anything is sent.",
             {"batch_id": s,
              "severity": {"type": "string", "enum": ["SEV1", "SEV2", "SEV3"]},
              "root_cause_category": {"type": "string", "enum": ["TRUNCATED_FILE", "SCHEMA_CHANGE",
                                                                 "DUPLICATE_DELIVERY", "WRONG_MANIFEST",
                                                                 "BAD_REFERENCE_DATA", "OTHER"]},
              "root_cause": {**s, "description": "What went wrong, in two or three sentences, with numbers"},
              "rows_expected": {"type": "integer"}, "rows_received_complete": {"type": "integer"},
              "rows_missing": {"type": "integer"},
              "downstream_impact": {**s, "description": "What happened to Bronze, Silver and Gold and to reports"},
              "evidence": arr, "remediation": arr,
              "ticket": {"type": "object", "additionalProperties": False,
                         "required": ["assignee_team", "title", "body"],
                         "properties": {"assignee_team": s, "title": s, "body": s}}},
             ["batch_id", "severity", "root_cause_category", "root_cause", "rows_expected", "rows_received_complete",
              "rows_missing", "downstream_impact", "evidence", "remediation", "ticket"], _submit, terminal=True),
    ]}


def alert_window(alert_date: str, before: int = 30, after: int = 7) -> tuple[str, str]:
    d = date.fromisoformat(str(alert_date)[:10])
    return (d - timedelta(days=before)).isoformat(), (d + timedelta(days=after)).isoformat()
