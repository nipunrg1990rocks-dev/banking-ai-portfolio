"""Export the results of a real pipeline run as small CSV/JSON files for the Streamlit app.

The hosted app (Streamlit Community Cloud) has no Spark or Java, so it shows the
outputs of the last pipeline run rather than re-running it. ``run_summary.json``
records which run produced the files, on which engine and versions, so the app
can say exactly what the visitor is looking at.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pyspark
from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType

from .forensics import inspect_delivery
from .gold import gold_sql_files, table_name
from .store import TableStore, on_databricks

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshot"
SILVER_TABLES = ["customers", "accounts", "transactions", "customers_quarantine", "accounts_quarantine",
                 "transactions_quarantine", "ref_fx_rates", "ref_country_risk", "ref_branches"]
BRONZE_TABLES = ["customers", "accounts", "transactions", "ref_fx_rates", "ref_country_risk", "ref_branches"]


def _flat(df: DataFrame) -> DataFrame:
    for f in df.schema.fields:
        if isinstance(f.dataType, ArrayType):
            df = df.withColumn(f.name, F.concat_ws(";", F.col(f.name)))
    return df


def _csv(df: DataFrame, path: Path, order: list[str] | None = None) -> int:
    pdf = _flat(df.orderBy(*order) if order else df).toPandas()
    pdf.to_csv(path, index=False)
    return len(pdf)


def _delta_version() -> str:
    try:
        from importlib.metadata import version

        return version("delta-spark")
    except Exception:  # noqa: BLE001 - Databricks bundles Delta without the pip package
        return "bundled with Databricks Runtime"


def export_snapshot(store: TableStore, result: dict, out_dir: Path = SNAPSHOT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*"):
        old.unlink()
    spark = store.spark

    for path in gold_sql_files():
        name = table_name(path)
        _csv(store.read("gold", name), out_dir / f"gold_{name}.csv")
    run_dq = store.read("audit", "dq_results").filter(F.col("run_id") == result["run_id"]).drop("run_started_at")
    _csv(run_dq, out_dir / "dq_results.csv", ["batch_id", "layer", "table_name", "rule_name"])
    for t in ("transactions", "customers", "accounts"):
        q = store.read("silver", f"{t}_quarantine").drop("_source_file", "_ingested_at", "_quarantined_at")
        _csv(q, out_dir / f"quarantine_{t}.csv")

    changed = (store.read("silver", "customers").groupBy("customer_id").count().filter("count > 1")
               .select("customer_id"))
    scd2 = (store.read("silver", "customers").join(changed, "customer_id")
            .select("customer_id", "segment", "kyc_risk_rating", "country", "pep_flag", "branch_code",
                    "effective_from", "effective_to", "is_current", "_batch_id"))
    _csv(scd2, out_dir / "scd2_customer_history.csv", ["customer_id", "effective_from"])

    # Row-level changes made by the second batch, read from the Delta Change Data Feed.
    for table in ("customers", "accounts"):
        cdf = (store.changes("silver", table, starting_version=1)
               .filter(F.col("_change_type") != "update_preimage" if table == "accounts" else F.lit(True)))
        keep = ["customer_id", "segment", "kyc_risk_rating", "country", "effective_from", "effective_to",
                "is_current"] if table == "customers" else ["account_id", "customer_id", "product", "status"]
        _csv(cdf.select(*keep, "_change_type", "_commit_version"),
             out_dir / f"cdf_{table}.csv", [keep[0], "_commit_version", "_change_type"])

    # Full Silver transactions (synthetic, ~17k rows) so the app's SQL console and alert drill-down have detail.
    txns = store.read("silver", "transactions").select(
        "txn_id", "account_id", "customer_id", "txn_ts", "txn_date", "txn_type", "channel", "direction", "amount",
        "currency", "amount_usd", "counterparty_name", "counterparty_country", "counterparty_risk_tier", "_batch_id")
    _csv(txns, out_dir / "silver_transactions.csv", ["txn_ts", "txn_id"])
    _csv(store.read("silver", "accounts").drop("_source_file", "_ingested_at", "_updated_at"),
         out_dir / "silver_accounts.csv", ["account_id"])

    history = {}
    for layer, table in (("silver", "transactions"), ("silver", "customers"), ("silver", "accounts")):
        rows = store.history(layer, table).select("version", "timestamp", "operation", "operationMetrics").collect()
        versions = []
        for r in sorted(rows, key=lambda r: r["version"]):
            count = store.read(layer, table, version=r["version"]).count()
            metrics = {k: v for k, v in (r["operationMetrics"] or {}).items()
                       if k in ("numOutputRows", "numTargetRowsInserted", "numTargetRowsUpdated",
                                "numTargetRowsDeleted", "numFilesAdded", "numFilesRemoved", "numDeletedRows")}
            versions.append({"version": r["version"], "timestamp": str(r["timestamp"]), "operation": r["operation"],
                             "rows_at_version": count, "metrics": metrics})
        history[f"{layer}.{table}"] = versions
    (out_dir / "delta_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

    counts = {}
    for layer, tables in (("bronze", BRONZE_TABLES), ("silver", SILVER_TABLES),
                          ("gold", [table_name(p) for p in gold_sql_files()])):
        for t in tables:
            counts[f"{layer}.{t}"] = store.read(layer, t).count()

    # The incident: what the pipeline did, plus file-level evidence for every delivery in the landing zone.
    incident = result.get("incident")
    if incident:
        deliveries = {b: inspect_delivery(store.config.landing, b)
                      for b in sorted(p.name.split("=", 1)[1] for p in Path(store.config.landing).glob("batch=*"))}
        (out_dir / "incident.json").write_text(json.dumps({"incident": incident, "deliveries": deliveries},
                                                          indent=2, default=str), encoding="utf-8")

    oracle = result.get("oracle") or {}
    summary = {
        "run_id": result["run_id"],
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine": "Databricks" if on_databricks() else "Local Apache Spark (single machine)",
        "spark_version": spark.version,
        "pyspark_version": pyspark.__version__,
        "delta_version": _delta_version(),
        "duration_s": result["duration_s"],
        "table_counts": counts,
        "steps": [{k: (str(v) if k in ("run_started_at", "finished_at") else v) for k, v in s.items()}
                  for s in result["steps"]],
        "planted": {
            "aml_scenarios": oracle.get("aml_scenarios", {}),
            "aml_false_positives": oracle.get("aml_false_positives", {}),
            "txn_defects": {k: len(v) for k, v in oracle.get("txn_defects", {}).items()},
            "customer_defects": {k: len(v) for k, v in oracle.get("customer_defects", {}).items()},
            "scd2_changed_customers": len(oracle.get("scd2_changed_customers", [])),
            "cross_batch_resends": len(oracle.get("cross_batch_resends", [])),
            "incident": oracle.get("incident", {}),
        },
    }
    (out_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return out_dir
