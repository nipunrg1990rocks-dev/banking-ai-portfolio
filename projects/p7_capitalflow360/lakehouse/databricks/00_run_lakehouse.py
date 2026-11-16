# Databricks notebook source
# MAGIC %md
# MAGIC # CapitalFlow 360 lakehouse: Excel to Bronze, Silver and Gold
# MAGIC
# MAGIC **Status: written for Databricks serverless, not yet executed on Databricks.** The same code
# MAGIC (`lakehouse/cf360_lake/`) has been run end to end on local Spark 4.1 + Delta Lake 4.4; see
# MAGIC `lakehouse/evidence/local_run.json`. All data is synthetic.
# MAGIC
# MAGIC **How to run:** open this notebook from the repository's Git folder, pick serverless compute,
# MAGIC click **Run all**. It creates schema `workspace.cf360` and a volume for the landing zone,
# MAGIC converts the 13 baseline workbooks and the negative workbooks to Parquet, and runs the same checks as
# MAGIC the local run. Re-running with reset = yes starts again from empty.

# COMMAND ----------

# MAGIC %pip install openpyxl==3.1.5 --quiet

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog catalog")
dbutils.widgets.text("schema", "cf360", "Schema")
dbutils.widgets.dropdown("reset", "yes", ["yes", "no"], "Drop the schema first")
CATALOG, SCHEMA = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")

import os
import sys
from pathlib import Path

PROJECT = Path(os.getcwd()).parents[1]            # .../projects/p7_capitalflow360
sys.path.insert(0, str(PROJECT / "lakehouse"))

from cf360_lake import contract as C
from cf360_lake import pipeline as P
from cf360_lake.convert import convert
from cf360_lake.store import Store

if dbutils.widgets.get("reset") == "yes":
    spark.sql(f"DROP SCHEMA IF EXISTS {CATALOG}.{SCHEMA} CASCADE")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.lake")
VOL = f"/Volumes/{CATALOG}/{SCHEMA}/lake"
store = Store(landing=f"{VOL}/landing", checkpoints=f"{VOL}/checkpoints", tables=f"{CATALOG}.{SCHEMA}", mode="catalog")
contract = C.build_contract(PROJECT / "data" / "excel")
print("project:", PROJECT, "| tables in contract:", len(contract))

# COMMAND ----------

# MAGIC %md ## 1. Baseline batch: land, Bronze, Silver, Gold

# COMMAND ----------

log, dq = [], []
m1 = convert(sorted((PROJECT / "data" / "excel").glob("*.xlsx")), Path(store.landing), "BASELINE-001", contract)
print("landed:", len(m1["tables"]), "rejected:", m1["rejected"])
r1 = P.run_batch(spark, store, contract, "BASELINE-001", "databricks-serverless", log, dq)
print(r1["status"])
print(P.gold(spark, store))
display(spark.table(store.name("silver_trades")).groupBy("product_group").count().orderBy("product_group"))

# COMMAND ----------

# MAGIC %md ## 2. Restartability: re-run the same batch (expect 0 new Bronze rows, no Silver changes)

# COMMAND ----------

P.run_batch(spark, store, contract, "BASELINE-001", "databricks-serverless", log, dq)
print("bronze rows ingested on re-run:", log[-2]["rows_in"])
display(spark.sql(f"DESCRIBE HISTORY {store.name('silver_trades')} LIMIT 2").select("version", "operation", "operationMetrics"))

# COMMAND ----------

# MAGIC %md ## 3. Negative batch: schema drift refused, DQ cases quarantined, gate holds the batch

# COMMAND ----------

m2 = convert(sorted((PROJECT / "data" / "negative").glob("*.xlsx")), Path(store.landing), "NEGATIVE-001", contract)
print("rejected at landing:", m2["rejected"])
r2 = P.run_batch(spark, store, contract, "NEGATIVE-001", "databricks-serverless", log, dq)
print(r2["status"], r2["held"])
P.gold(spark, store)
display(spark.table(store.name("gold_dq_summary")).orderBy("batch_id", "table_name", "rule_id"))

# COMMAND ----------

# MAGIC %md ## 4. Evidence for the platform's Admin > Job runs page

# COMMAND ----------

import json
out = Path(VOL) / "evidence"
out.mkdir(parents=True, exist_ok=True)
(out / "job_runs.json").write_text(json.dumps({"job_runs": log, "dq_results": dq}, indent=1, default=str))
print("wrote", out / "job_runs.json", "- download it and import with POST /api/admin/job-runs/import")
