# Databricks notebook source
# MAGIC %md
# MAGIC # Ledgerline Bank lakehouse: run the whole pipeline
# MAGIC
# MAGIC **What this does:** creates the `ledgerline_*` schemas and a landing volume in Unity Catalog,
# MAGIC writes synthetic source files to the volume, then builds the Bronze, Silver and Gold Delta tables
# MAGIC with data-quality checks and reconciliation controls. All data is synthetic (a fictional bank).
# MAGIC
# MAGIC **How to run:** click **Run all** at the top. It uses serverless compute and takes a few minutes.
# MAGIC Re-running is safe: it starts again from empty schemas.
# MAGIC
# MAGIC Source code: `projects/p2_lakehouse/lakehouse/` in this Git folder.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog catalog")
dbutils.widgets.dropdown("reset", "yes", ["yes", "no"], "Drop existing ledgerline schemas first")
CATALOG = dbutils.widgets.get("catalog")

import os
import sys

PROJECT_DIR = os.path.abspath(os.path.join(os.getcwd(), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from lakehouse.pipeline import Pipeline
from lakehouse.store import LAYERS, LakehouseConfig, TableStore

config = LakehouseConfig.databricks(catalog=CATALOG)
store = TableStore(spark, config)
print("Project code:", PROJECT_DIR)
print("Landing volume:", config.landing)

# COMMAND ----------

# MAGIC %md ## 1. Reset (optional) and create schemas + landing volume

# COMMAND ----------

if dbutils.widgets.get("reset") == "yes":
    for layer in LAYERS:
        spark.sql(f"DROP SCHEMA IF EXISTS {store.schema(layer)} CASCADE")
store.setup()
display(spark.sql(f"SHOW SCHEMAS IN {CATALOG} LIKE 'ledgerline*'"))

# COMMAND ----------

# MAGIC %md ## 2. Run land, Bronze, Silver, Gold and OPTIMIZE

# COMMAND ----------

result = Pipeline(store).run_all()
print(f"Run {result['run_id']} finished in {result['duration_s']} seconds")
display(spark.table(store.name("audit", "pipeline_steps")).filter(f"run_id = '{result['run_id']}'")
        .select("step", "layer", "table_name", "batch_id", "status", "rows_in", "rows_written",
                "duplicates_removed", "rows_quarantined", "duration_s"))

# COMMAND ----------

# MAGIC %md ## 3. Data-quality scorecard and AML alert candidates

# COMMAND ----------

display(spark.table(store.name("gold", "dq_scorecard")).orderBy("status", "table_name", "rule_name"))

# COMMAND ----------

display(spark.table(store.name("gold", "aml_alert_candidates")).orderBy("rule_code", "alert_date"))

# COMMAND ----------

# MAGIC %md ## 4. Delta Lake features: history, time travel and change data feed

# COMMAND ----------

display(spark.sql(f"DESCRIBE HISTORY {store.name('silver', 'customers')}")
        .select("version", "timestamp", "operation", "operationMetrics"))

# COMMAND ----------

september = spark.sql(f"SELECT COUNT(*) AS n FROM {store.name('silver', 'transactions')} VERSION AS OF 0").first().n
now = spark.table(store.name("silver", "transactions")).count()
print(f"silver.transactions: {september} rows after the September load (version 0), {now} rows now")

# COMMAND ----------

display(spark.sql(f"""
  SELECT customer_id, segment, kyc_risk_rating, country, effective_from, effective_to, is_current,
         _change_type, _commit_version
  FROM table_changes('{store.name('silver', 'customers')}', 1)
  ORDER BY customer_id, _commit_version, _change_type
"""))

# COMMAND ----------

# MAGIC %md ## 5. Evidence summary (copy this into the project thread if asked)

# COMMAND ----------

import json

counts = {f"{layer}.{t.tableName}": spark.table(f"{store.schema(layer)}.{t.tableName}").count()
          for layer in ("bronze", "silver", "gold") for t in spark.sql(f"SHOW TABLES IN {store.schema(layer)}").collect()}
print(json.dumps({"run_id": result["run_id"], "spark_version": spark.version,
                  "runtime": os.environ.get("DATABRICKS_RUNTIME_VERSION"), "duration_s": result["duration_s"],
                  "table_counts": counts}, indent=2))
