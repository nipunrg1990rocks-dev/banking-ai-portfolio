"""Orchestration: run the medallion pipeline end to end, or one step at a time.

Locally ``python run_pipeline.py`` calls ``Pipeline.run_all()``. On Databricks the
job in ``databricks/`` runs each step as its own task (land, bronze, silver, gold,
optimize) with the same ``run_id``, so the run log reads the same either way.

Every step appends to two audit tables:

* ``audit.pipeline_steps``  what ran, row counts in and out, duration, status
* ``audit.dq_results``      every expectation and control evaluated, with failures

If a blocking control fails the step is logged as FAILED and the exception stops
the run, so Gold is never refreshed from data that did not reconcile.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from . import bronze, gold, silver, synthetic
from .dq import DataQualityError
from .store import TableStore

STEP_SCHEMA = StructType([
    StructField("run_id", StringType()), StructField("run_started_at", TimestampType()),
    StructField("step", StringType()), StructField("layer", StringType()), StructField("table_name", StringType()),
    StructField("batch_id", StringType()), StructField("status", StringType()),
    StructField("rows_in", LongType()), StructField("rows_written", LongType()),
    StructField("duplicates_removed", LongType()), StructField("rows_quarantined", LongType()),
    StructField("details", StringType()), StructField("duration_s", DoubleType()),
    StructField("finished_at", TimestampType()),
])
DQ_SCHEMA = StructType([
    StructField("run_id", StringType()), StructField("run_started_at", TimestampType()),
    StructField("batch_id", StringType()), StructField("layer", StringType()), StructField("table_name", StringType()),
    StructField("rule_name", StringType()), StructField("action", StringType()),
    StructField("description", StringType()), StructField("expression", StringType()),
    StructField("rows_checked", LongType()), StructField("rows_failed", LongType()),
])
_CORE = {"layer", "table", "batch_id", "rows_in", "rows_written", "duplicates_removed", "rows_quarantined"}
OPTIMIZE_TARGETS = {("silver", "transactions"): ["customer_id", "txn_date"], ("silver", "customers"): ["customer_id"]}


def new_run_id() -> str:
    return "run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Pipeline:
    def __init__(self, store: TableStore, run_id: str | None = None) -> None:
        self.store = store
        self.spark = store.spark
        self.run_id = run_id or new_run_id()
        self.run_started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.step_log: list[dict] = []
        self.dq_log: list[dict] = []

    # ------------------------------------------------------------------ logging
    def _record(self, step: str, entries: list[dict], metrics: list[dict], started: float, status: str = "SUCCEEDED",
                batch_id: str | None = None, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        duration = round(time.time() - started, 2)
        rows = []
        for e in entries or [{"layer": step, "table": None}]:
            extra = {k: v for k, v in e.items() if k not in _CORE}
            if error:
                extra["error"] = error
            rows.append({
                "run_id": self.run_id, "run_started_at": self.run_started_at, "step": step,
                "layer": e.get("layer"), "table_name": e.get("table"), "batch_id": e.get("batch_id", batch_id),
                "status": status, "rows_in": e.get("rows_in"), "rows_written": e.get("rows_written"),
                "duplicates_removed": e.get("duplicates_removed"), "rows_quarantined": e.get("rows_quarantined"),
                "details": json.dumps(extra, default=str) if extra else None,
                "duration_s": duration, "finished_at": now,
            })
        dq_rows = [{"run_id": self.run_id, "run_started_at": self.run_started_at, "batch_id": batch_id,
                    **{k: m[k] for k in ("layer", "table_name", "rule_name", "action", "description", "expression",
                                         "rows_checked", "rows_failed")}} for m in metrics]
        self.step_log += rows
        self.dq_log += dq_rows
        self.store.write(self.spark.createDataFrame(rows, STEP_SCHEMA), "audit", "pipeline_steps", mode="append",
                         comment="Pipeline run log: one row per table per step")
        if dq_rows:
            self.store.write(self.spark.createDataFrame(dq_rows, DQ_SCHEMA), "audit", "dq_results", mode="append",
                             comment="Every data-quality expectation and control evaluated, per run")

    def _run(self, step: str, fn, batch_id: str | None = None):
        started = time.time()
        try:
            entries, metrics = fn()
        except DataQualityError as exc:
            self._record(step, [], [], started, status="FAILED", batch_id=batch_id, error=str(exc))
            raise
        self._record(step, entries, metrics, started, batch_id=batch_id)
        return entries

    # -------------------------------------------------------------------- steps
    def land(self, **generator_args) -> dict:
        """Write synthetic source files to the landing zone (a local folder or a Unity Catalog volume)."""
        started = time.time()
        oracle = synthetic.generate(self.store.config.landing, **generator_args)
        entries = [{"layer": "landing", "table": f, "batch_id": b, "rows_written": n}
                   for b, files in oracle.counts.items() for f, n in files.items()]
        self._record("land", entries, [], started)
        return oracle.to_dict()

    def batches(self) -> list[str]:
        root = self.store.config.landing
        if root.startswith("/Volumes") or Path(root).exists():
            return sorted(p.name.split("=", 1)[1] for p in Path(root).glob("batch=*"))
        return sorted(synthetic.BATCHES)

    def reference(self):
        return self._run("reference", lambda: (bronze.load_reference(self.store) + silver.load_reference(self.store),
                                               []))

    def bronze(self, batch_id: str):
        return self._run("bronze", lambda: bronze.load_batch(self.store, batch_id), batch_id)

    def silver(self, batch_id: str):
        business_date = date.fromisoformat(bronze.read_manifest(self.store, batch_id)["business_date"])

        def run():
            steps, metrics = [], []
            for loader in (lambda: silver.load_customers(self.store, batch_id, business_date)[:2],
                           lambda: silver.load_accounts(self.store, batch_id),
                           lambda: silver.load_transactions(self.store, batch_id, business_date)):
                s, m = loader()
                steps += s
                metrics += m
            return steps, metrics

        return self._run("silver", run, batch_id)

    def gold(self):
        return self._run("gold", lambda: gold.build(self.store))

    def optimize(self):
        """Compact small files and co-locate rows by common filter columns (Z-ORDER)."""
        def run():
            out = []
            for (layer, table), cols in OPTIMIZE_TARGETS.items():
                metrics = self.store.delta_table(layer, table).optimize().executeZOrderBy(*cols).collect()[0]
                m = metrics["metrics"]
                out.append({"layer": layer, "table": table, "files_removed": m["numFilesRemoved"],
                            "files_added": m["numFilesAdded"], "zorder_by": cols})
            return out, []
        return self._run("optimize", run)

    def run_all(self, *, land: bool = True, optimize: bool = True, **generator_args) -> dict:
        started = time.time()
        self.store.setup()
        oracle = self.land(**generator_args) if land else None
        self.reference()
        for batch_id in self.batches():
            self.bronze(batch_id)
            self.silver(batch_id)
        self.gold()
        if optimize:
            self.optimize()
        return {"run_id": self.run_id, "duration_s": round(time.time() - started, 1), "oracle": oracle,
                "steps": self.step_log, "dq": self.dq_log}


def failed_rows_by_rule(store: TableStore, run_id: str) -> dict[str, int]:
    df = (store.read("audit", "dq_results").filter(F.col("run_id") == run_id)
          .groupBy("table_name", "rule_name").agg(F.sum("rows_failed").alias("failed")))
    return {f"{r['table_name']}.{r['rule_name']}": int(r["failed"]) for r in df.collect()}
