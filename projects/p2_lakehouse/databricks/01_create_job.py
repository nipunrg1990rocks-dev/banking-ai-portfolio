# Databricks notebook source
# MAGIC %md
# MAGIC # Create the orchestrated job (Lakeflow Jobs)
# MAGIC
# MAGIC Creates a job called **Ledgerline Lakehouse** with one task per pipeline stage, so the
# MAGIC Jobs UI shows a real dependency graph (DAG):
# MAGIC
# MAGIC `land → reference → batch_20260930 → batch_20261007 → gold → optimize`
# MAGIC
# MAGIC It then starts one run. Click **Run all**, then open **Jobs & Pipelines** in the left bar.
# MAGIC Uses serverless compute (no clusters to manage). Running this notebook again updates the same job.
# MAGIC Run `00_run_pipeline` with reset = yes first, or the land step will reuse existing data.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog catalog")
CATALOG = dbutils.widgets.get("catalog")

import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs

w = WorkspaceClient()  # authenticates as you, through this notebook; no token needed
here = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
runner = os.path.join(os.path.dirname(here), "task_runner")
JOB_NAME = "Ledgerline Lakehouse"


def task(key, step, depends_on=None, batch_id=""):
    return jobs.Task(
        task_key=key,
        description=f"Ledgerline lakehouse step: {step} {batch_id}".strip(),
        notebook_task=jobs.NotebookTask(
            notebook_path=runner,
            base_parameters={"step": step, "batch_id": batch_id, "catalog": CATALOG, "run_id": "{{job.run_id}}"},
        ),
        depends_on=[jobs.TaskDependency(task_key=d) for d in (depends_on or [])],
        max_retries=1,
        min_retry_interval_millis=30_000,
    )


tasks = [
    task("land", "land"),
    task("reference", "reference", ["land"]),
    task("batch_20260930", "batch", ["reference"], "20260930"),
    task("batch_20261007", "batch", ["batch_20260930"], "20261007"),
    task("gold", "gold", ["batch_20261007"]),
    task("optimize", "optimize", ["gold"]),
]
settings = dict(name=JOB_NAME, tasks=tasks, max_concurrent_runs=1,
                tags={"project": "ledgerline-lakehouse", "data": "synthetic"})

existing = [j for j in w.jobs.list(name=JOB_NAME)]
if existing:
    job_id = existing[0].job_id
    w.jobs.reset(job_id=job_id, new_settings=jobs.JobSettings(**settings))
    print(f"Updated job {job_id}")
else:
    job_id = w.jobs.create(**settings).job_id
    print(f"Created job {job_id}")

run = w.jobs.run_now(job_id=job_id)
print(f"Started run {run.run_id}. Open Jobs & Pipelines > {JOB_NAME} to watch the DAG.")
