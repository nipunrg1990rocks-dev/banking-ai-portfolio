"""Build the risk mart in Unity Catalog and publish the agent's results as Delta tables.

Used by ``databricks/00_build_risk_mart.py``. Tests run the same functions on local
Spark (catalog ``spark_catalog``), so the notebook path is exercised in CI.

Tables written to ``<catalog>.ledgerline_riskmart``:

* the 13 simulated operations tables (``dq_results``, ``recon_breaks`` ...)
* the 12 fact views (``fv_*``), created from the same Databricks SQL the app runs
* ``kri_snapshot``: every metric's value and RAG status for each business day
* ``agent_findings`` and ``agent_actions``: the Risk Insights Agent's briefing
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from . import catalog
from .agent import RiskInsightsAgent
from .engine import COLUMN_TYPES, SparkEngine, spark_frame
from .metrics import MetricStore
from .synthetic import AS_OF, generate, load_p2_gold

GOLD_TABLES = ("customer_360", "aml_alert_candidates", "daily_transaction_summary", "dq_scorecard")


def names(catalog_name: str) -> dict[str, str]:
    return {"mart": f"{catalog_name}.ledgerline_riskmart.{{}}", "gold": f"{catalog_name}.ledgerline_gold.{{}}"}


def read_gold(spark, catalog_name: str, source: str = "unity_catalog") -> dict[str, pd.DataFrame]:
    """Project 02 Gold tables: from Unity Catalog (P2 has run) or from the committed snapshot."""
    if source == "snapshot":
        return load_p2_gold()
    return {t: spark.table(names(catalog_name)["gold"].format(t)).toPandas() for t in GOLD_TABLES}


def write_gold_snapshot(spark, catalog_name: str) -> list[str]:
    """Load the committed P2 Gold snapshot into ``ledgerline_gold`` (only when P2 has not been run)."""
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog_name}.ledgerline_gold")
    written = []
    for table, df in load_p2_gold().items():
        target = names(catalog_name)["gold"].format(table)
        spark_frame(spark, df, COLUMN_TYPES.get(f"gold.{table}", {})).write.mode("overwrite") \
            .option("overwriteSchema", "true").saveAsTable(target)
        written.append(target)
    return written


def build_risk_mart(spark, catalog_name: str, gold: dict[str, pd.DataFrame]) -> dict[str, int]:
    """Generate the simulated operations history and save it as Delta tables."""
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog_name}.ledgerline_riskmart "
              "COMMENT 'Ledgerline Bank risk mart (synthetic operations history for Project 04)'")
    counts = {}
    for table, df in generate(gold).items():
        target = names(catalog_name)["mart"].format(table)
        spark_frame(spark, df, COLUMN_TYPES.get(f"mart.{table}", {})).write.mode("overwrite") \
            .option("overwriteSchema", "true").saveAsTable(target)
        counts[table] = len(df)
    return counts


def _strip_comments(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--")).strip()


def create_views(spark, catalog_name: str) -> list[str]:
    """Create the metric fact views in Unity Catalog from the same SQL files the app runs."""
    engine = SparkEngine(spark, names(catalog_name))
    created = []
    for view in catalog.VIEWS:
        target = names(catalog_name)["mart"].format(view)
        template = catalog.view_sql(view)
        first = next((ln for ln in template.splitlines() if ln.startswith("--")), "")
        comment = re.sub(r"^--\s*", "", first).replace("'", "")
        spark.sql(f"CREATE OR REPLACE VIEW {target} COMMENT '{comment}' AS\n{_strip_comments(engine.render(template))}")
        created.append(target)
    return created


def metric_store(spark, catalog_name: str) -> MetricStore:
    return MetricStore(SparkEngine(spark, names(catalog_name)))


def kri_snapshot(store: MetricStore) -> pd.DataFrame:
    rows = []
    for d in store.dates:
        if d.weekday() >= 5:
            continue
        for s in store.snapshots(d):
            m = s.metric
            rows.append({"obs_date": d, "metric_id": m.id, "metric_name": m.name, "domain": m.domain,
                         "is_kri": m.kri, "value": s.value, "status": s.status, "early_warning": m.early_warning,
                         "limit_value": m.limit, "unit": m.unit, "policy_ref": m.policy_ref})
    return pd.DataFrame(rows)


def publish_results(spark, catalog_name: str, store: MetricStore, as_of: date = AS_OF):
    """Run the agent and save the KRI history, its findings and its actions as Delta tables."""
    briefing = RiskInsightsAgent(store, as_of).run()
    findings = pd.DataFrame([{
        "as_of": as_of, "priority": f.priority, "metric_id": f.metric_id, "metric_name": f.name, "domain": f.domain,
        "status": f.status, "headline": f.headline, "related_metrics": ", ".join(f.related),
        "driver": f"{f.driver['dimension']} = {f.driver['member']}" if f.driver else None,
        "onset": f.onset, "root_cause": f.root_cause["change_id"] if f.root_cause else None,
        "confidence": f.confidence, "detail": "\n".join(f.detail)} for f in briefing.findings])
    actions = pd.DataFrame([{"as_of": as_of, "action": a.action, "owner": a.owner, "due": a.due, "basis": a.basis}
                            for a in briefing.actions])
    snapshot = kri_snapshot(store)
    date_cols = {"kri_snapshot": {"obs_date": "DATE"}, "agent_findings": {"as_of": "DATE", "onset": "DATE"},
                 "agent_actions": {"as_of": "DATE", "due": "DATE"}}
    for table, df in (("kri_snapshot", snapshot), ("agent_findings", findings), ("agent_actions", actions)):
        spark_frame(spark, df, date_cols[table]).write.mode("overwrite").option("overwriteSchema", "true") \
            .saveAsTable(names(catalog_name)["mart"].format(table))
    return briefing, snapshot


def ask_questions(store: MetricStore, questions: list[str], backend=None, as_of: date = AS_OF) -> pd.DataFrame:
    """Answer questions with the LLM backend (if any), falling back to the offline answer when it is not grounded."""
    from .chat import RiskChat
    from .llm import RiskLLMAgent
    from .tools import RiskTools

    agent = RiskInsightsAgent(store, as_of)
    chat = RiskChat(agent, agent.run())
    rows = []
    for q in questions:
        offline = chat.ask(q).text
        row = {"as_of": as_of, "question": q, "shown": "offline", "model": "offline agent", "grounded": None,
               "unsupported_numbers": None, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0,
               "answer": offline, "offline_answer": offline, "error": None}
        if backend is not None:
            try:
                live = RiskLLMAgent(RiskTools(store, as_of), backend).ask(q)
                row.update({"model": live.model, "grounded": live.grounded, "tool_calls": live.tool_calls,
                            "unsupported_numbers": ", ".join(live.unsupported) or None, "error": live.error,
                            "tokens_in": live.input_tokens, "tokens_out": live.output_tokens})
                if live.text and live.grounded and not live.error:
                    row.update({"shown": "llm", "answer": live.text})
            except Exception as exc:  # noqa: BLE001 - an endpoint failure must not stop the notebook
                row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        rows.append(row)
    return pd.DataFrame(rows)
