"""Bronze, Silver and Gold transformations with quality gates and restartable processing.

Serverless-safe: DataFrame API and Spark SQL only (no RDDs, no df.checkpoint()), streaming only
with Trigger.AvailableNow, MERGE through the Delta Lake API.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from cf360_lake.contract import TRACE_COLUMNS
from cf360_lake.store import Store

# Tables refined into Silver, in dependency order, with their business keys.
SILVER = {
    "clients": ["client_id"], "counterparties": ["counterparty_id"], "accounts": ["account_id"], "instruments": ["instrument_id"],
    "ssis": ["ssi_id"], "orders": ["order_id"], "executions": ["exec_id"], "trades": ["trade_id"], "allocations": ["allocation_id"],
    "confirmations": ["confirmation_id"], "settlement_status": ["status_msg_id"], "custody_positions": ["account_id", "instrument_id", "statement_date"],
    "custody_cash": ["account_id", "currency", "statement_date"], "opening_positions": ["account_id", "instrument_id"],
    "opening_cash": ["account_id", "currency"], "prices": ["instrument_id", "price_date"], "fx_rates": ["currency", "rate_date"],
}
REFERENCES = {  # DQ02 referential integrity: column -> (silver table, key)
    "trades": {"instrument_id": ("instruments", "instrument_id"), "account_id": ("accounts", "account_id"),
               "client_id": ("clients", "client_id"), "counterparty_id": ("counterparties", "counterparty_id")},
    "accounts": {"client_id": ("clients", "client_id")},
    "orders": {"instrument_id": ("instruments", "instrument_id"), "account_id": ("accounts", "account_id")},
    "allocations": {"trade_id": ("trades", "trade_id"), "account_id": ("accounts", "account_id")},
    "executions": {"order_id": ("orders", "order_id")},
    "confirmations": {"trade_id": ("trades", "trade_id")},
}
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"]
DOMAINS = {  # DQ04 / R007 domain validity: column -> (allowed values or predicate, rule, error)
    "trades": [("side", ["BUY", "SELL", "PAY_FIXED", "RECEIVE_FIXED", "REPO", "REVERSE_REPO", "SEC_LOAN", "SEC_BORROW"], "DQ04", "E_DOMAIN"),
               ("settle_ccy", CURRENCIES, "DQ04", "E_DOMAIN"),
               ("product_group", ["EQUITY", "BOND", "FX", "LISTED_DERIV", "IRS", "FINANCING"], "DQ04", "E_DOMAIN"),
               ("quantity", "positive", "R007", "E_QTY_INVALID")],
    "allocations": [("alloc_qty", "positive", "R007", "E_QTY_INVALID")],
}
GATE_MAX_QUARANTINE_RATIO = 0.05  # a batch with more than 5% of rows quarantined in any table is held back
SPARK_TYPE = {"STRING": T.StringType(), "DATE": T.DateType(), "TIMESTAMP": T.TimestampType(), "INTEGER": T.LongType(),
              "DECIMAL": T.DecimalType(38, 10)}


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def bronze_schema(spec: dict) -> T.StructType:
    cols = [c[0] for c in spec["columns"]] + sorted(TRACE_COLUMNS)
    return T.StructType([T.StructField(c, T.StringType(), True) for c in cols] + [T.StructField("batch_id", T.StringType(), True)])


# ------------------------------------------------------------------------------------------------ bronze
def bronze(spark: SparkSession, store: Store, contract: dict, table: str) -> int:
    """Incrementally append newly landed Parquet files to bronze_<table> (immutable). The streaming
    checkpoint makes this restartable and exactly-once: a re-run ingests only files it has not seen."""
    src = f"{store.landing.rstrip('/')}/{table}"
    df = (spark.readStream.format("parquet").schema(bronze_schema(contract[table])).load(src)
          .withColumn("_source_file", F.col("_metadata.file_path"))
          .withColumn("_ingested_at", F.current_timestamp()))
    cols = [c[0] for c in contract[table]["columns"]]
    df = df.withColumn("_row_hash", F.sha2(F.concat_ws("\u0001", *[F.coalesce(F.col(c), F.lit("\u0000")) for c in cols]), 256))
    w = (df.writeStream.format("delta").outputMode("append").option("checkpointLocation", store.checkpoint(f"bronze_{table}"))
         .trigger(availableNow=True))
    q = w.start(store.name(f"bronze_{table}")) if store.mode == "path" else w.toTable(store.name(f"bronze_{table}"))
    q.awaitTermination()
    return int(sum(p.get("numInputRows", 0) for p in (q.recentProgress or [])))


# ------------------------------------------------------------------------------------------------ silver
def _typed(df: DataFrame, spec: dict) -> DataFrame:
    out = df
    for name, typ, _ in spec["columns"]:
        out = out.withColumn(f"__raw_{name}", F.col(name))
        if typ == "DECIMAL":
            out = out.withColumn(name, F.col(name).cast(T.DecimalType(38, 10)))
        elif typ == "INTEGER":
            out = out.withColumn(name, F.col(name).cast(T.DecimalType(38, 10)).cast(T.LongType()))
        elif typ == "DATE":
            out = out.withColumn(name, F.to_date(F.substring(F.col(name), 1, 10)))
        elif typ == "TIMESTAMP":
            out = out.withColumn(name, F.to_timestamp(F.col(name)))
    return out


def _failures(spark: SparkSession, store: Store, df: DataFrame, table: str, spec: dict) -> DataFrame:
    """Add an array column __fail of {rule_id, error_code, column} for every failed check."""
    checks = []
    for name, typ, nullable in spec["columns"]:
        raw = F.col(f"__raw_{name}")
        if not nullable:
            checks.append(F.when(raw.isNull(), F.struct(F.lit("DQ01").alias("rule_id"), F.lit("E_REQUIRED_FIELD").alias("error_code"),
                                                        F.lit(name).alias("column"))))
        if typ != "STRING":  # value present but not castable to the contract type
            checks.append(F.when(raw.isNotNull() & F.col(name).isNull(),
                                 F.struct(F.lit("DQ04").alias("rule_id"), F.lit("E_TYPE").alias("error_code"), F.lit(name).alias("column"))))
    for col, allowed, rule, err in DOMAINS.get(table, []):
        bad = (F.col(col) <= 0) if allowed == "positive" else ~F.col(col).isin(allowed)
        checks.append(F.when(F.col(col).isNotNull() & bad, F.struct(F.lit(rule).alias("rule_id"), F.lit(err).alias("error_code"),
                                                                     F.lit(col).alias("column"))))
    for col, (ref_table, ref_key) in REFERENCES.get(table, {}).items():
        if not store.exists(spark, f"silver_{ref_table}"):
            continue
        keys = store.read(spark, f"silver_{ref_table}").select(F.col(ref_key).alias(f"__ref_{col}")).distinct()
        df = df.join(F.broadcast(keys.withColumn(f"__ok_{col}", F.lit(True))), df[col] == keys[f"__ref_{col}"], "left").drop(f"__ref_{col}")
        code = {"instrument_id": "E_UNKNOWN_INSTRUMENT", "account_id": "E_UNKNOWN_ACCOUNT", "client_id": "E_UNKNOWN_CLIENT",
                "counterparty_id": "E_UNKNOWN_COUNTERPARTY"}.get(col, "E_UNKNOWN_REFERENCE")
        checks.append(F.when(F.col(col).isNotNull() & F.col(f"__ok_{col}").isNull(),
                             F.struct(F.lit("DQ02").alias("rule_id"), F.lit(code).alias("error_code"), F.lit(col).alias("column"))))
    return df.withColumn("__fail", F.filter(F.array(*checks), lambda x: x.isNotNull()) if checks else F.array())


def silver(spark: SparkSession, store: Store, contract: dict, table: str, batch_id: str) -> dict:
    """Refine one batch of one table. Returns counts and gate verdict; does not merge (see ``merge``)."""
    spec = contract[table]
    b = store.read(spark, f"bronze_{table}").where(F.col("batch_id") == batch_id)
    typed = _failures(spark, store, _typed(b, spec), table, spec)
    total = typed.count()
    bad = typed.where(F.size("__fail") > 0)
    n_bad = bad.count()
    key = SILVER[table]
    quarantine = (bad.select(F.lit(table).alias("table_name"), F.lit(batch_id).alias("batch_id"),
                             F.concat_ws("|", *[F.col(f"__raw_{k}") for k in key]).alias("record_key"),
                             F.col("negative_case_id"), F.explode("__fail").alias("f"),
                             F.to_json(F.struct(*[F.col(f"__raw_{c[0]}").alias(c[0]) for c in spec["columns"]])).alias("record"),
                             F.current_timestamp().alias("quarantined_at"))
                  .select("table_name", "batch_id", "record_key", "negative_case_id", F.col("f.rule_id").alias("rule_id"),
                          F.col("f.error_code").alias("error_code"), F.col("f.column").alias("column_name"), "record", "quarantined_at"))
    good = typed.where(F.size("__fail") == 0)
    # DQ03 deterministic de-duplication: latest ingest wins per business key
    w = Window.partitionBy(*key).orderBy(F.col("_ingested_at").desc(), F.col("_source_file").desc(), F.col("_row_hash"))
    good = good.withColumn("__rn", F.row_number().over(w)).where("__rn = 1")
    data_cols = [c[0] for c in spec["columns"]]
    good = good.select(*data_cols, "batch_id", "_ingested_at", "_row_hash")
    n_good = good.count()
    ratio = (n_bad / total) if total else 0.0
    return {"table": table, "batch_id": batch_id, "rows_in": total, "rows_out": n_good, "rows_quarantined": n_bad,
            "duplicates_removed": total - n_bad - n_good, "quarantine_ratio": round(ratio, 4),
            "gate": "PASS" if ratio <= GATE_MAX_QUARANTINE_RATIO else "FAIL", "_good": good, "_quarantine": quarantine}


def merge(spark: SparkSession, store: Store, table: str, good: DataFrame) -> None:
    """Idempotent upsert into silver_<table>: replaying the same batch changes nothing."""
    name = f"silver_{table}"
    if not store.exists(spark, name):
        store.write(good, name, "overwrite")
        return
    key = SILVER[table]
    cond = " AND ".join(f"t.`{k}` <=> s.`{k}`" for k in key)
    (store.delta(spark, name).alias("t").merge(good.alias("s"), cond)
     .whenMatchedUpdateAll(condition="t._row_hash <> s._row_hash")
     .whenNotMatchedInsertAll().execute())


def append_quarantine(store: Store, spark: SparkSession, q: DataFrame, table: str, batch_id: str) -> None:
    name = "silver_quarantine"
    if store.exists(spark, name):  # replay-safe: remove this batch/table's earlier quarantine rows first
        store.delta(spark, name).delete(f"table_name = '{table}' AND batch_id = '{batch_id}'")
        store.write(q, name, "append")
    else:
        store.write(q, name, "overwrite")


# ------------------------------------------------------------------------------------------------ gold
def gold(spark: SparkSession, store: Store) -> dict:
    r = lambda t: store.read(spark, f"silver_{t}")  # noqa: E731
    trades, allocs, ex, confs, status = r("trades"), r("allocations"), r("executions"), r("confirmations"), r("settlement_status")
    a = allocs.groupBy("trade_id").agg(F.count("*").alias("allocation_count"), F.sum("alloc_qty").alias("allocated_qty"))
    fills = (ex.groupBy("order_id").agg(F.count("*").alias("fill_count"), F.sum("exec_qty").alias("filled_qty"),
                                        (F.sum(F.col("exec_qty") * F.col("exec_price")) / F.sum("exec_qty")).alias("vwap")))
    c = confs.groupBy("trade_id").agg(F.count("*").alias("confirmations"))
    st = (status.groupBy("trade_id").agg(F.collect_set("status").alias("controlled_outcomes")))
    lifecycle = (trades.join(a, "trade_id", "left").join(fills, "order_id", "left").join(c, "trade_id", "left").join(st, "trade_id", "left")
                 .select("trade_id", "product_group", "product_type", "instrument_id", "client_id", "account_id", "counterparty_id", "side",
                         "quantity", "price", "settle_ccy", "trade_date", "settlement_date", "end_date",
                         F.datediff("settlement_date", "trade_date").alias("calendar_days_to_settle"),
                         F.coalesce("allocation_count", F.lit(0)).alias("allocation_count"),
                         (F.coalesce("allocated_qty", F.lit(0)) == F.col("quantity")).alias("allocation_conserved"),
                         F.coalesce("fill_count", F.lit(0)).alias("fill_count"), "filled_qty", F.round("vwap", 6).alias("fills_vwap"),
                         (F.coalesce("confirmations", F.lit(0)) > 0).alias("has_confirmation"),
                         F.when(F.col("product_type").isin("FUTURE", "OPTION"), "CCP feed").when(F.col("confirmations") > 0, "received")
                         .otherwise("missing").alias("confirmation_source"),
                         F.coalesce("controlled_outcomes", F.array()).alias("controlled_outcomes"), "batch_id"))
    store.write(lifecycle, "gold_trade_lifecycle")

    mult = (F.when(F.col("product_type").isin("GOVT_BOND", "CORP_BOND", "REPO", "REVERSE_REPO"), F.col("quantity") * F.col("price") / 100)
            .when(F.col("product_type").isin("FX_SPOT", "FX_FORWARD", "IRS", "FUTURE"), F.lit(None))
            .otherwise(F.col("quantity") * F.col("price")))
    sign = F.when(F.col("side").isin("BUY", "REVERSE_REPO"), -1).when(F.col("side").isin("SELL", "REPO"), 1).otherwise(0)
    flows = (trades.where(mult.isNotNull() & (sign != 0))
             .select("account_id", F.col("settle_ccy").alias("currency"), F.col("settlement_date").alias("value_date"),
                     (sign * mult).cast(T.DecimalType(38, 2)).alias("flow")))
    opening = r("opening_cash").select("account_id", "currency", F.col("balance").cast(T.DecimalType(38, 2)).alias("opening"))
    proj = (flows.groupBy("account_id", "currency", "value_date").agg(F.sum("flow").alias("gross_flow"))
            .join(opening, ["account_id", "currency"], "left")
            .withColumn("projected_balance", F.coalesce("opening", F.lit(0)) + F.sum("gross_flow").over(
                Window.partitionBy("account_id", "currency").orderBy("value_date").rowsBetween(Window.unboundedPreceding, 0)))
            .withColumn("basis", F.lit("gross principal only, excludes charges, FX, derivatives and lifecycle flows; the platform ledger is the book of record")))
    store.write(proj, "gold_cash_position_projection")

    dq = (store.read(spark, "silver_quarantine").groupBy("batch_id", "table_name", "rule_id", "error_code").agg(F.count("*").alias("failed"))
          if store.exists(spark, "silver_quarantine") else spark.createDataFrame([], "batch_id string, table_name string, rule_id string, error_code string, failed long"))
    store.write(dq, "gold_dq_summary")
    return {"gold_trade_lifecycle": lifecycle.count(), "gold_cash_position_projection": proj.count(), "gold_dq_summary": dq.count()}


# ------------------------------------------------------------------------------------------------ orchestration
def run_batch(spark: SparkSession, store: Store, contract: dict, batch_id: str, environment: str, log: list, dq: list) -> dict:
    """Bronze -> Silver for one landed batch, with an all-or-nothing quality gate before any MERGE."""
    t0 = now()
    ingested = {}
    manifest = _manifest(store, batch_id)
    for rej in manifest.get("rejected", []):  # DQ05: the whole sheet was refused at landing
        dq.append({"batch_id": batch_id, "table_name": table_name_of(rej), "rule_id": "DQ05", "dimension": "schema",
                   "checked": int(rej.get("rows", 0)), "failed": int(rej.get("rows", 0)), "environment": environment})
    for table in [t for t in _landed_tables(store, batch_id) if t in contract]:
        ingested[table] = bronze(spark, store, contract, table)
    log.append(_job(environment, "bronze", batch_id, t0, sum(ingested.values()), sum(ingested.values()), 0, {"tables": ingested}))
    t1 = now()
    results = []
    for table in SILVER:
        if not store.exists(spark, f"bronze_{table}"):
            continue
        res = silver(spark, store, contract, table, batch_id)
        if res["rows_in"] == 0:
            continue
        results.append(res)
        merge(spark, store, table, res["_good"]) if res["gate"] == "PASS" else None
        append_quarantine(store, spark, res["_quarantine"], table, batch_id)
        dq.extend(_dq_rows(res, environment))
        if res["gate"] == "FAIL":
            break  # downstream tables depend on this one; hold the rest of the batch
    held = [r["table"] for r in results if r["gate"] == "FAIL"]
    status = "GATE_FAILED" if held else "SUCCEEDED"
    log.append(_job(environment, "silver", batch_id, t1, sum(r["rows_in"] for r in results), sum(r["rows_out"] for r in results if r["gate"] == "PASS"),
                    sum(r["rows_quarantined"] for r in results), {"tables": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results],
                                                                  "held_tables": held}, status))
    return {"status": status, "held": held, "tables": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]}


def _manifest(store: Store, batch_id: str) -> dict:
    """The landing manifest of a batch. Unity Catalog volumes are mounted as files on Databricks
    (/Volumes/...), so the same file read works locally and on serverless compute."""
    import json
    from pathlib import Path
    return json.loads((Path(store.landing) / "_batches" / f"{batch_id}.json").read_text())


def table_name_of(rejected: dict) -> str:
    return str(rejected.get("sheet", "")).lower()


def _landed_tables(store: Store, batch_id: str) -> list[str]:
    return sorted(_manifest(store, batch_id)["tables"])


def _job(env, task, batch, started, rows_in, rows_out, quarantined, details, status="SUCCEEDED") -> dict:
    fin = now()
    return {"job_run_id": f"{env}-{task}-{batch}-{int(time.time() * 1000)}", "environment": env, "task": task, "batch_id": batch,
            "status": status, "started_at": started.isoformat(), "finished_at": fin.isoformat(), "rows_in": int(rows_in),
            "rows_out": int(rows_out), "rows_quarantined": int(quarantined), "details": details}


def _dq_rows(res: dict, env: str) -> list[dict]:
    q = res["_quarantine"].groupBy("rule_id").count().collect()
    by_rule = {r["rule_id"]: int(r["count"]) for r in q}
    dims = {"DQ01": "completeness", "DQ02": "integrity", "DQ03": "uniqueness", "DQ04": "validity", "R007": "validity"}
    rows = [{"batch_id": res["batch_id"], "table_name": res["table"], "rule_id": rid, "dimension": dims.get(rid, "validity"),
             "checked": res["rows_in"], "failed": n, "environment": env} for rid, n in sorted(by_rule.items())]
    rows.append({"batch_id": res["batch_id"], "table_name": res["table"], "rule_id": "DQ03", "dimension": "uniqueness",
                 "checked": res["rows_in"] - res["rows_quarantined"], "failed": res["duplicates_removed"], "environment": env})
    return rows
