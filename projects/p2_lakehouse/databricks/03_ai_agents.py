# Databricks notebook source
# MAGIC %md
# MAGIC # Ledgerline Bank: AI agents on the lakehouse
# MAGIC
# MAGIC **What this does:** runs the AML Investigator on every open alert and the Data Steward on the
# MAGIC blocked delivery, straight against the Unity Catalog tables built by `00_run_pipeline`. The agents
# MAGIC only read (through Spark SQL views); what they conclude is written to two Delta tables for a person
# MAGIC to review:
# MAGIC
# MAGIC * `ledgerline_gold.aml_case_reports`: one row per alert, `review_status = PENDING_HUMAN_REVIEW`
# MAGIC * `ledgerline_audit.agent_steps`: every thought and tool call the agents made
# MAGIC
# MAGIC **Brain:** `offline` (default) is the rule-based planner: free, no LLM. `claude` uses the Anthropic API
# MAGIC and costs money (roughly $0.05 to $0.30 per investigation); it needs a key in a secret scope
# MAGIC (`ledgerline` / `anthropic_api_key`), never pasted into the notebook.
# MAGIC
# MAGIC **How to run:** run `00_run_pipeline` first, then click **Run all** here.

# COMMAND ----------

# MAGIC %pip install -q duckdb==1.5.6 anthropic==2.3.0

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog catalog")
dbutils.widgets.dropdown("brain", "offline", ["offline", "claude"], "Agent brain")
CATALOG = dbutils.widgets.get("catalog")
BRAIN = dbutils.widgets.get("brain")

import os
import sys

PROJECT_DIR = os.path.abspath(os.path.join(os.getcwd(), ".."))
REPO_DIR = os.path.abspath(os.path.join(PROJECT_DIR, "..", ".."))
for p in (PROJECT_DIR, REPO_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents import ClaudeBrain, SparkBackend
from agents.lakehouse_io import investigate, save
from lakehouse.store import LakehouseConfig, TableStore
from portfolio_core.audit import AuditLog

store = TableStore(spark, LakehouseConfig.databricks(catalog=CATALOG))
backend = SparkBackend(store)
brain = None
if BRAIN == "claude":
    brain = ClaudeBrain(api_key=dbutils.secrets.get("ledgerline", "anthropic_api_key"))
print("Brain:", "Claude " + brain.model if brain else "offline planner (no LLM)")

# COMMAND ----------

# MAGIC %md ## 1. Investigate every alert and the blocked delivery

# COMMAND ----------

audit = AuditLog()
runs = investigate(backend, brain, audit)
for r in runs:
    out = r.output or {}
    verdict = out.get("recommendation") or out.get("root_cause_category")
    print(f"{r.agent:<18} {r.status:<10} {verdict or '-':<15} {r.tool_calls:>2} tool calls  ${r.cost_usd:.3f}")
print("Audit chain intact:", audit.verify_chain()[0], f"| total spend ${sum(r.cost_usd for r in runs):.3f}")

# COMMAND ----------

# MAGIC %md ## 2. Save the case reports and agent steps to Delta

# COMMAND ----------

print(save(store, runs))
display(spark.table(store.name("gold", "aml_case_reports"))
        .select("alert_id", "customer_id", "rule_code", "recommendation", "risk_score", "headline",
                "review_status", "brain", "created_at")
        .orderBy("created_at", ascending=False))

# COMMAND ----------

steward = next((r for r in runs if r.agent == "data_steward"), None)
if steward and steward.output:
    for k in ("root_cause_category", "root_cause", "rows_expected", "rows_received_complete", "rows_missing",
              "downstream_impact"):
        print(f"{k}: {steward.output.get(k)}")

# COMMAND ----------

display(spark.table(store.name("audit", "agent_steps"))
        .select("agent", "run_id", "seq", "kind", "tool", "ok", "text").orderBy("run_id", "seq").limit(200))
