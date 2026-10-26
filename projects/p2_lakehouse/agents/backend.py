"""Where the agents' tools read data from.

The agents never touch files or tables directly. They call tools, and every tool
goes through a ``Backend``:

* ``SnapshotBackend``  the exported snapshot of a real pipeline run, loaded into a
  locked, in-memory DuckDB (used by the hosted app and the tests)
* ``SparkBackend``     the live Unity Catalog tables on Databricks, through Spark SQL

Both expose the same logical table names (``transactions``, ``aml_alerts``,
``customer_360`` ...), so the same tools and prompts work in both places. Only
read-only statements are allowed, and results are capped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

import pandas as pd

from explorer import MAX_ROWS, SNAPSHOT, QueryError, run_query, sql_console

_READ_ONLY = re.compile(r"^\s*(SELECT|WITH|DESCRIBE|SHOW|FROM)\b", re.IGNORECASE)

# Logical name -> (layer, physical table) on Databricks. Array columns are flattened to ';'-joined text so
# both backends return the same shapes.
SPARK_VIEWS = {
    "transactions": ("silver", "transactions", None),
    "accounts": ("silver", "accounts", None),
    "customer_360": ("gold", "customer_360", None),
    "aml_alerts": ("gold", "aml_alert_candidates", ["txn_ids"]),
    "daily_summary": ("gold", "daily_transaction_summary", None),
    "segment_kpis": ("gold", "segment_monthly_kpis", None),
    "dq_scorecard": ("gold", "dq_scorecard", None),
    "quarantine": ("silver", "transactions_quarantine", ["_dq_failed_rules"]),
    "customer_history": ("silver", "customers", None),
    "dq_results": ("audit", "dq_results", None),
    "pipeline_steps": ("audit", "pipeline_steps", None),
}


class Backend(Protocol):
    name: str
    dialect: str

    def sql(self, query: str) -> pd.DataFrame: ...

    def tables(self) -> dict[str, list[str]]: ...

    def incident(self) -> dict | None: ...

    def delivery(self, batch_id: str) -> dict: ...


class SnapshotBackend:
    """The committed snapshot of a real run, in DuckDB. No network, no file access from SQL."""

    name = "snapshot"
    dialect = "DuckDB SQL"

    def __init__(self, folder: Path = SNAPSHOT) -> None:
        self.folder = folder
        self.con = sql_console(folder)
        incident_file = folder / "incident.json"
        self._incident = json.loads(incident_file.read_text()) if incident_file.exists() else {}

    def sql(self, query: str) -> pd.DataFrame:
        return run_query(self.con.cursor(), query)  # a cursor per query: safe across Streamlit sessions

    def tables(self) -> dict[str, list[str]]:
        names = [r[0] for r in self.con.execute("SHOW TABLES").fetchall()]
        return {n: [r[0] for r in self.con.execute(f"DESCRIBE {n}").fetchall()] for n in names}

    def incident(self) -> dict | None:
        return self._incident.get("incident")

    def delivery(self, batch_id: str) -> dict:
        deliveries = self._incident.get("deliveries", {})
        if batch_id not in deliveries:
            raise QueryError(f"No delivery {batch_id} in the landing zone. Known: {sorted(deliveries)}")
        return deliveries[batch_id]

    def deliveries(self) -> dict:
        return self._incident.get("deliveries", {})


class SparkBackend:
    """Live tables on Databricks (or a local Spark lakehouse), exposed under the same logical names."""

    name = "databricks"
    dialect = "Spark SQL"

    def __init__(self, store) -> None:  # store: lakehouse.store.TableStore
        from pyspark.sql import functions as F

        self.store = store
        self.spark = store.spark
        for view, (layer, table, arrays) in SPARK_VIEWS.items():
            df = store.read(layer, table)
            for col in arrays or []:
                df = df.withColumn(col, F.concat_ws(";", F.col(col)))
            df.createOrReplaceTempView(view)

    def sql(self, query: str) -> pd.DataFrame:
        statements = [s for s in query.strip().split(";") if s.strip()]
        if len(statements) != 1:
            raise QueryError("Run one statement at a time.")
        if not _READ_ONLY.match(statements[0]):
            raise QueryError("Read-only: start with SELECT, WITH, DESCRIBE or SHOW.")
        try:
            return self.spark.sql(statements[0]).limit(MAX_ROWS).toPandas()
        except Exception as exc:  # noqa: BLE001 - Spark raises several exception types for bad SQL
            raise QueryError(str(exc).split("\n")[0][:400]) from exc

    def tables(self) -> dict[str, list[str]]:
        return {v: self.spark.table(v).columns for v in SPARK_VIEWS}

    def incident(self) -> dict | None:
        failed = self.sql("SELECT run_id, batch_id, details, finished_at FROM pipeline_steps "
                          "WHERE status = 'FAILED' ORDER BY finished_at DESC LIMIT 1")
        if failed.empty:
            return None
        row = failed.iloc[0]
        watched = [("bronze", "transactions"), ("silver", "transactions"), ("gold", "daily_transaction_summary")]
        return {"batch_id": row["batch_id"], "outcome": "BLOCKED", "run_id": row["run_id"],
                "error": json.loads(row["details"] or "{}").get("error"),
                "versions_now": {f"{la}.{t}": self.store.version(la, t) for la, t in watched}}

    def delivery(self, batch_id: str) -> dict:
        from lakehouse.forensics import inspect_delivery

        return inspect_delivery(self.store.config.landing, batch_id)

    def deliveries(self) -> dict:
        root = Path(self.store.config.landing)
        return {p.name.split("=", 1)[1]: self.delivery(p.name.split("=", 1)[1]) for p in sorted(root.glob("batch=*"))}
