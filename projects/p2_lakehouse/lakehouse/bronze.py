"""Bronze layer: land source files as-is, every column a string, plus lineage metadata.

Bronze is the bank's "evidence copy" of what each source system sent. Nothing is
cleaned or rejected here, so any later question ("what exactly did core banking
send on 30 September?") can be answered from Bronze. Each row gets:

* ``_batch_id``     which delivery it came from
* ``_source_file``  the file it was read from (Spark's hidden ``_metadata`` column)
* ``_ingested_at``  when it was loaded

Loads are idempotent: a batch that is already in Bronze is skipped, so re-running
a job after a failure never double-counts data. On Databricks the same pattern
could use Auto Loader (``cloudFiles``) for incremental file discovery.
"""

from __future__ import annotations

import json
from pathlib import Path

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

from .dq import control_check
from .store import TableStore

TXN_FIELDS = ["txn_id", "account_id", "txn_ts", "amount", "currency", "txn_type", "channel",
              "counterparty_name", "counterparty_country", "reference"]
TXN_SCHEMA = StructType([StructField(f, StringType()) for f in TXN_FIELDS]
                        + [StructField("_corrupt_record", StringType())])
REFERENCE_TABLES = ("branches", "country_risk", "fx_rates")
BATCH_FILES = {"customers": "customers.csv", "accounts": "accounts.csv", "transactions": "transactions.jsonl"}


def _with_lineage(df: DataFrame, batch_id: str) -> DataFrame:
    return (df.withColumn("_source_file", F.col("_metadata.file_path"))
              .withColumn("_batch_id", F.lit(batch_id))
              .withColumn("_ingested_at", F.current_timestamp()))


def _read_csv(store: TableStore, path: str) -> DataFrame:
    # inferSchema off: Bronze keeps every value exactly as delivered, as text.
    return store.spark.read.option("header", True).option("inferSchema", False).csv(path)


def read_manifest(store: TableStore, batch_id: str) -> dict:
    path = f"{store.config.landing}/batch={batch_id}/manifest.json"
    # Plain file read: works for local folders and for Unity Catalog volumes (mounted under /Volumes).
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_reference(store: TableStore) -> list[dict]:
    """Reference data is a small full refresh: overwrite each run."""
    steps = []
    for table in REFERENCE_TABLES:
        df = _with_lineage(_read_csv(store, f"{store.config.landing}/reference/{table}.csv"), "reference")
        store.write(df, "bronze", f"ref_{table}", mode="overwrite",
                    comment=f"Raw {table} reference data as delivered")
        steps.append({"layer": "bronze", "table": f"ref_{table}", "rows_in": df.count(),
                      "rows_written": store.read("bronze", f"ref_{table}").count()})
    return steps


def already_loaded(store: TableStore, table: str, batch_id: str) -> bool:
    if not store.exists("bronze", table):
        return False
    return store.read("bronze", table).filter(F.col("_batch_id") == batch_id).limit(1).count() > 0


def load_batch(store: TableStore, batch_id: str) -> tuple[list[dict], list[dict]]:
    """Append one delivery to Bronze. Returns (step log, control results)."""
    manifest = read_manifest(store, batch_id)
    folder = f"{store.config.landing}/batch={batch_id}"
    steps, controls = [], []
    for table, filename in BATCH_FILES.items():
        if already_loaded(store, table, batch_id):
            steps.append({"layer": "bronze", "table": table, "batch_id": batch_id, "rows_in": 0,
                          "rows_written": 0, "note": "batch already loaded, skipped (idempotent re-run)"})
            continue
        if table == "transactions":
            raw = (store.spark.read.schema(TXN_SCHEMA)
                   .option("mode", "PERMISSIVE").option("columnNameOfCorruptRecord", "_corrupt_record")
                   .json(f"{folder}/{filename}"))
        else:
            raw = _read_csv(store, f"{folder}/{filename}")
        df = _with_lineage(raw, batch_id)
        rows = df.count()
        expected = manifest["record_counts"][table]
        # Completeness control: we landed exactly what the source says it sent.
        controls.append(control_check(
            "bronze_record_count_matches_manifest", rows == expected, table=f"bronze.{table}", layer="bronze",
            detail=f"batch {batch_id}: landed {rows} rows, source manifest says {expected}", rows_checked=rows))
        store.write(df, "bronze", table, mode="append", comment=f"Raw {table} extracts as delivered by LEDGERLINE_CORE")
        steps.append({"layer": "bronze", "table": table, "batch_id": batch_id, "rows_in": rows, "rows_written": rows})

    txns = store.read("bronze", "transactions").filter(F.col("_batch_id") == batch_id)
    landed_total = txns.select(F.round(F.sum(F.expr("try_cast(amount AS decimal(18,2))")), 2)).collect()[0][0]
    landed_total = float(landed_total or 0)
    controls.append(control_check(
        "bronze_control_total_matches_manifest", abs(landed_total - manifest["transactions_control_total"]) < 0.01,
        table="bronze.transactions", layer="bronze",
        detail=f"batch {batch_id}: landed amount total {landed_total:,.2f} vs manifest "
               f"{manifest['transactions_control_total']:,.2f}"))
    return steps, controls
