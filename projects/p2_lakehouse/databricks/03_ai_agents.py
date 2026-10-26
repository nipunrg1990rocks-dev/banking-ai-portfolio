# Databricks notebook source
# MAGIC %md
# MAGIC # Ledgerline Bank: AI agents on the lakehouse
# MAGIC
# MAGIC **What this does:** runs the three agents straight against the Unity Catalog tables built by
# MAGIC `00_run_pipeline`, powered by a **Databricks Foundation Model** endpoint in this workspace:
# MAGIC
# MAGIC * **AML Investigator** works every open alert and recommends ESCALATE or CLOSE
# MAGIC * **Data Steward** finds the root cause of the blocked delivery
# MAGIC * **Ask Ledgerline** answers the six evaluation questions in SQL
# MAGIC
# MAGIC Every answer is scored against the answer key planted in the synthetic data. The agents only
# MAGIC read; results go to `ledgerline_gold.aml_case_reports` (for human review) and
# MAGIC `ledgerline_audit.agent_steps` (every thought and tool call).
# MAGIC
# MAGIC **Cost and keys:** none. The model is called with this notebook's own sign-in, inside the
# MAGIC workspace's free quota. If no endpoint here supports tool calling, the notebook says so and
# MAGIC uses the offline planner (no LLM) instead.
# MAGIC
# MAGIC **How to run:** run `00_run_pipeline` first, then click **Run all** here.

# COMMAND ----------

# MAGIC %pip install -q duckdb==1.5.6 openai==3.30.0

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog catalog")
dbutils.widgets.dropdown("brain", "databricks", ["databricks", "offline"], "Agent brain")
dbutils.widgets.text("endpoint", "", "Model endpoint (blank = pick automatically)")
CATALOG = dbutils.widgets.get("catalog")

import os
import sys

PROJECT_DIR = os.path.abspath(os.path.join(os.getcwd(), ".."))
REPO_DIR = os.path.abspath(os.path.join(PROJECT_DIR, "..", ".."))
for p in (PROJECT_DIR, REPO_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents import SparkBackend, recordings
from agents.evals import ASK_SET, SUGGESTED, evaluate, regenerated_key
from agents.lakehouse_io import save
from lakehouse.store import LakehouseConfig, TableStore

store = TableStore(spark, LakehouseConfig.databricks(catalog=CATALOG))
backend = SparkBackend(store)

# COMMAND ----------

# MAGIC %md ## 1. Find a model endpoint that can call tools

# COMMAND ----------

brain = None
if dbutils.widgets.get("brain") == "databricks":
    from agents.databricks_llm import databricks_brain

    brain, tried = databricks_brain(dbutils.widgets.get("endpoint").strip())
    for name, outcome in tried:
        print(f"{name:<45} {outcome}")
if brain:
    print(f"\nUsing {brain.model}")
else:
    print("\nNo endpoint in this workspace completed a tool call. Using the offline planner (no LLM) instead.")

# COMMAND ----------

# MAGIC %md ## 2. Run the agents and score them against the answer key

# COMMAND ----------

runs = []
results = evaluate(backend, brain, runs_out=runs, key=regenerated_key())
for res in results:
    sm = res.summary()
    print(f"\n{sm['agent']} ({sm['brain']}): {sm['passed']}/{sm['cases']} cases fully correct")
    for check, score in sm["checks"].items():
        print(f"  {check:<26}{score}")
    for case in res.cases:
        if not case["pass"]:
            print("  FAILED", case["case"], [k for k, v in case["checks"].items() if not v])
print(f"\n{len(runs)} runs, {sum(r.tool_calls for r in runs)} tool calls, "
      f"{sum(r.input_tokens + r.output_tokens for r in runs):,} tokens, model: {brain.model if brain else 'none'}")

# COMMAND ----------

# MAGIC %md ## 3. Save the case reports and agent steps to Delta

# COMMAND ----------

print(save(store, runs))
display(spark.table(store.name("gold", "aml_case_reports"))
        .select("alert_id", "customer_id", "rule_code", "recommendation", "risk_score", "headline",
                "review_status", "brain", "model", "created_at")
        .orderBy("created_at", ascending=False))

# COMMAND ----------

# MAGIC %md ## 4. Keep the runs so the public app can replay them
# MAGIC
# MAGIC The recordings are written into this Git folder under `projects/p2_lakehouse/data/agent_runs/`.
# MAGIC To publish them, use the Git folder's **Commit & push** button (optional).

# COMMAND ----------

if brain:
    asked = {c["question"] for c in ASK_SET}
    from agents import run_agent

    extra = [run_agent("ask_ledgerline", backend, brain, question=q) for q in SUGGESTED if q not in asked]
    try:
        out = recordings.save(runs + extra, results, brain.model, where="Databricks Free Edition",
                              root=recordings.RUNS_DIR)
        print("Saved", len(runs) + len(extra), "recordings and", out)
    except OSError as exc:
        print("Could not write into the Git folder:", exc)
else:
    print("Nothing to record: the offline planner already runs in the public app.")
