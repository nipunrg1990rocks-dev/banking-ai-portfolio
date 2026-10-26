# Databricks notebook source
# MAGIC %md
# MAGIC # Ledgerline Bank: risk & data-quality mart and Risk Insights Agent
# MAGIC
# MAGIC **What this does:** reads the Project 02 Gold tables (`ledgerline_gold`), adds a simulated
# MAGIC operations history (data-quality runs, feeds, reconciliations, exceptions, KYC reviews, losses,
# MAGIC KRIs and a change calendar), creates the 12 metric fact views in Databricks SQL, then runs the
# MAGIC Risk Insights Agent and saves its findings as Delta tables. All data is synthetic (a fictional bank).
# MAGIC
# MAGIC **How to run:** click **Run all**. It uses serverless compute and takes a few minutes.
# MAGIC Run Project 02's `00_run_pipeline` first; if you have not, set `gold_source` to `snapshot`
# MAGIC and the notebook loads the committed copy of the Project 02 Gold tables instead.
# MAGIC
# MAGIC Source code: `projects/p4_risk_dashboard/riskdash/` in this Git folder.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog catalog")
dbutils.widgets.dropdown("gold_source", "unity_catalog", ["unity_catalog", "snapshot"], "Project 02 Gold tables from")
CATALOG = dbutils.widgets.get("catalog")
GOLD_SOURCE = dbutils.widgets.get("gold_source")

import os
import sys

PROJECT_DIR = os.path.abspath(os.path.join(os.getcwd(), ".."))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from riskdash import lakehouse
from riskdash.synthetic import AS_OF

print("Project code:", PROJECT_DIR)
print("Reporting date:", AS_OF)

# COMMAND ----------

# MAGIC %md ## 1. Project 02 Gold tables

# COMMAND ----------

if GOLD_SOURCE == "snapshot":
    print("Loaded snapshot into:", lakehouse.write_gold_snapshot(spark, CATALOG))
gold = lakehouse.read_gold(spark, CATALOG)
for name, df in gold.items():
    print(f"{name}: {len(df):,} rows")

# COMMAND ----------

# MAGIC %md ## 2. Build the risk mart (Delta tables in `ledgerline_riskmart`)

# COMMAND ----------

counts = lakehouse.build_risk_mart(spark, CATALOG, gold)
display(spark.createDataFrame([(k, v) for k, v in counts.items()], "table STRING, rows LONG"))

# COMMAND ----------

# MAGIC %md ## 3. Create the 12 metric fact views (the same SQL the Streamlit app runs)

# COMMAND ----------

views = lakehouse.create_views(spark, CATALOG)
display(spark.sql(f"SHOW VIEWS IN {CATALOG}.ledgerline_riskmart"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Try one in SQL: overdue high-risk KYC reviews (RAS-004 s.5)

# COMMAND ----------

display(spark.sql(f"""
SELECT obs_date, SUM(customers) AS high_risk_customers, SUM(overdue) AS overdue,
       ROUND(100 * SUM(overdue) / SUM(customers), 2) AS overdue_pct
FROM {CATALOG}.ledgerline_riskmart.fv_hr_kyc_reviews
WHERE obs_date >= DATE'{AS_OF}' - INTERVAL 21 DAYS
GROUP BY obs_date ORDER BY obs_date
"""))

# COMMAND ----------

# MAGIC %md ## 4. Run the Risk Insights Agent on Spark and publish its results

# COMMAND ----------

store = lakehouse.metric_store(spark, CATALOG)
briefing, snapshot = lakehouse.publish_results(spark, CATALOG, store)
print(f"{len(briefing.findings)} findings, {len(briefing.actions)} actions, {len(briefing.trace)} tool calls")
display(spark.table(f"{CATALOG}.ledgerline_riskmart.agent_findings")
        .select("priority", "metric_name", "status", "driver", "onset", "root_cause", "confidence", "headline")
        .orderBy("priority", ascending=False))

# COMMAND ----------

# MAGIC %md ## 5. KRI dashboard on the reporting date

# COMMAND ----------

display(spark.sql(f"""
SELECT domain, metric_name, ROUND(value, 2) AS value, status, early_warning, limit_value, policy_ref
FROM {CATALOG}.ledgerline_riskmart.kri_snapshot
WHERE obs_date = DATE'{AS_OF}' AND is_kri
ORDER BY CASE status WHEN 'RED' THEN 0 WHEN 'AMBER' THEN 1 ELSE 2 END, domain
"""))

# COMMAND ----------

# MAGIC %md ## 6. The agent's board-pack draft (for a risk owner to review)

# COMMAND ----------

print(briefing.markdown())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next: connect the Streamlit app to this workspace (optional)
# MAGIC
# MAGIC The app can run the same views on a Databricks SQL warehouse instead of offline DuckDB.
# MAGIC Add these to the app's Streamlit **Secrets** (never paste them in chat or commit them):
# MAGIC `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN` and, if not `workspace`,
# MAGIC `DATABRICKS_CATALOG`. The hostname and HTTP path are on the SQL warehouse's **Connection details** tab.
