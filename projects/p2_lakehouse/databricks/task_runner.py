# Databricks notebook source
# MAGIC %md
# MAGIC # Job task runner
# MAGIC Runs one pipeline step. Used by the **Ledgerline Lakehouse** job created in `01_create_job`;
# MAGIC you don't need to open it yourself.

# COMMAND ----------

dbutils.widgets.text("step", "gold")
dbutils.widgets.text("batch_id", "")
dbutils.widgets.text("run_id", "")
dbutils.widgets.text("catalog", "workspace")

import os
import sys

PROJECT_DIR = os.path.abspath(os.path.join(os.getcwd(), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from lakehouse.pipeline import Pipeline
from lakehouse.store import LakehouseConfig, TableStore

step = dbutils.widgets.get("step")
batch_id = dbutils.widgets.get("batch_id") or None
run_id = "job_" + (dbutils.widgets.get("run_id") or "manual")
store = TableStore(spark, LakehouseConfig.databricks(dbutils.widgets.get("catalog")))
pipeline = Pipeline(store, run_id=run_id)

if step == "land":
    store.setup()
    pipeline.land()
elif step == "reference":
    pipeline.reference()
elif step == "batch":
    pipeline.bronze(batch_id)
    pipeline.silver(batch_id)
elif step == "gold":
    pipeline.gold()
elif step == "optimize":
    pipeline.optimize()
else:
    raise ValueError(f"unknown step {step!r}")
print(f"{step} {batch_id or ''} done for {run_id}")
