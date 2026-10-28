"""Run the agents inside the lakehouse and keep what they did as Delta tables.

Used by ``databricks/03_ai_agents.py`` (and the Spark tests): the AML Investigator
works every open alert and the Data Steward explains the blocked delivery, through
``SparkBackend``. Results land in

* ``gold.aml_case_reports``  one row per alert: recommendation, score, narrative, evidence
* ``audit.agent_steps``      every thought and tool call, for review and replay

Nothing an agent writes changes a decision by itself: ``review_status`` starts as
``PENDING_HUMAN_REVIEW``.
"""

from __future__ import annotations

import json

from . import run_agent
from .engine import AgentRun

TYPES = {"risk_score": "int", "tool_calls": "int", "cost_usd": "double", "seq": "int", "ok": "boolean", "ms": "int"}


def investigate(backend, brain=None, audit=None) -> list[AgentRun]:
    alerts = backend.sql("SELECT alert_id FROM aml_alerts ORDER BY rule_code, customer_id")
    runs = [run_agent("aml_investigator", backend, brain, audit, alert_id=a) for a in alerts.alert_id]
    if backend.incident():
        runs.append(run_agent("data_steward", backend, brain, audit))
    return runs


def case_rows(runs: list[AgentRun]) -> list[dict]:
    rows = []
    for r in runs:
        if r.agent != "aml_investigator":
            continue
        out = r.output or {}
        rows.append({"run_id": r.run_id, "alert_id": out.get("alert_id"), "headline": out.get("headline"),
                     "recommendation": out.get("recommendation"), "risk_score": out.get("risk_score"),
                     "narrative": out.get("narrative"), "red_flags": json.dumps(out.get("red_flags", [])),
                     "evidence_txn_ids": ";".join(out.get("evidence_txn_ids", [])), "status": r.status,
                     "brain": r.brain, "model": r.model, "tool_calls": r.tool_calls, "cost_usd": r.cost_usd,
                     "review_status": "PENDING_HUMAN_REVIEW", "created_at": r.started_at})
    return rows


def step_rows(runs: list[AgentRun]) -> list[dict]:
    return [{"run_id": r.run_id, "agent": r.agent, "seq": i, "kind": s.kind, "tool": s.tool,
             "args": json.dumps(s.args, default=str) if s.args else None, "ok": s.ok, "ms": s.ms, "text": s.text,
             "brain": r.brain, "started_at": r.started_at}
            for r in runs for i, s in enumerate(r.steps)]


def save(store, runs: list[AgentRun]) -> dict[str, int]:
    """Append the runs to the two Delta tables; returns rows written per table."""
    from pyspark.sql import functions as F

    spark = store.spark
    written = {}
    for layer, table, rows in (("gold", "aml_case_reports", case_rows(runs)),
                               ("audit", "agent_steps", step_rows(runs))):
        if not rows:
            continue
        # Build as text (None-safe schema inference), then cast the numeric and boolean columns.
        cols = list(rows[0])
        df = spark.createDataFrame([[None if row[c] is None else str(row[c]) for c in cols] for row in rows],
                                   ", ".join(f"{c} string" for c in cols))
        df = df.select([F.col(c).cast(TYPES.get(c, "string")).alias(c) for c in cols])
        if table == "aml_case_reports":
            alerts = store.read("gold", "aml_alert_candidates").select("alert_id", "customer_id", "rule_code")
            df = df.join(alerts, "alert_id", "left")
        store.write(df, layer, table, mode="append")
        written[f"{layer}.{table}"] = len(rows)
    return written
