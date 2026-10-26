"""Silver layer: typed, validated, de-duplicated and conformed data (PySpark DataFrame API).

What happens to each entity:

* reference  cast to proper types, full refresh
* customers  standardise, mask PII, check quality, then **SCD Type 2** MERGE so
             every change to risk rating, segment or country is kept as history
* accounts   check the owning customer exists, then **upsert** (SCD Type 1) MERGE
* transactions parse timestamps and amounts, de-duplicate, check quality, convert
             to USD with the daily FX rate, enrich with customer and counterparty
             country risk, then **insert-only** MERGE on ``txn_id`` so a re-sent
             transaction can never be counted twice

Rows that break a ``drop`` rule go to a ``*_quarantine`` table with the rule names,
so data stewards can fix them at source. A reconciliation control proves every
Bronze row is accounted for: loaded, quarantined, a duplicate, or already present.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from .dq import ACCOUNT_RULES, CUSTOMER_RULES, TRANSACTION_RULES, apply_expectations, control_check
from .store import TableStore

SCD2_TRACKED = ["country", "segment", "kyc_risk_rating", "pep_flag", "branch_code"]
CREDIT_TYPES = ("ACH_IN", "TRANSFER_IN", "WIRE_IN", "CASH_DEPOSIT")


def _try_cast(col: str, type_: str) -> F.Column:
    # try_cast returns NULL for bad input instead of failing the job (Spark 4 runs in ANSI mode).
    return F.expr(f"try_cast(nullif(trim({col}), '') AS {type_})")


def _clean(col: str, upper: bool = True) -> F.Column:
    c = F.trim(F.col(col))
    return F.upper(c) if upper else c


def _dedupe(df: DataFrame, key: str) -> tuple[DataFrame, int]:
    """Keep the latest copy of each key within a delivery. Returns (deduped, rows removed)."""
    w = Window.partitionBy(key).orderBy(F.col("_ingested_at").desc(), F.col("_source_file"))
    deduped = df.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")
    before, after = df.count(), deduped.count()
    return deduped, before - after


def _replace_batch(store: TableStore, df: DataFrame, table: str, batch_id: str) -> None:
    """Idempotent append: re-running a batch replaces its rows instead of duplicating them."""
    if store.exists("silver", table):
        store.delta_table("silver", table).delete(F.col("_batch_id") == batch_id)
        store.write(df, "silver", table, mode="append")
    else:
        store.write(df, "silver", table, mode="overwrite")


def _bronze_batch(store: TableStore, table: str, batch_id: str) -> DataFrame:
    return store.read("bronze", table).filter(F.col("_batch_id") == batch_id)


def _merge_metrics(store: TableStore, table: str) -> dict:
    last = store.delta_table("silver", table).history(1).collect()[0]
    m = last["operationMetrics"] or {}
    return {k: int(v) for k, v in m.items() if k.startswith("numTarget") or k == "numSourceRows"}


# --------------------------------------------------------------------------- reference
def load_reference(store: TableStore) -> list[dict]:
    b = lambda t: store.read("bronze", f"ref_{t}")  # noqa: E731
    tables = {
        "fx_rates": b("fx_rates").select(
            F.to_date("rate_date").alias("rate_date"), _clean("currency").alias("currency"),
            F.col("usd_rate").cast("decimal(18,8)").alias("usd_rate")),
        "country_risk": b("country_risk").select(
            _clean("country_code").alias("country_code"), F.trim("country_name").alias("country_name"),
            _clean("risk_tier").alias("risk_tier")),
        "branches": b("branches").select(
            _clean("branch_code").alias("branch_code"), F.trim("branch_name").alias("branch_name"),
            F.trim("city").alias("city"), F.trim("region").alias("region")),
    }
    steps = []
    for name, df in tables.items():
        store.write(df, "silver", f"ref_{name}", mode="overwrite", comment=f"Conformed {name} reference data")
        steps.append({"layer": "silver", "table": f"ref_{name}", "rows_in": df.count(),
                      "rows_written": store.read("silver", f"ref_{name}").count()})
    return steps


# --------------------------------------------------------------------------- customers
def _customers_staged(store: TableStore, batch_id: str) -> DataFrame:
    countries = store.read("silver", "ref_country_risk").select(
        F.col("country_code").alias("_cc"), F.lit(True).alias("country_known"))
    raw = _bronze_batch(store, "customers", batch_id)
    typed = raw.select(
        F.nullif(F.trim("customer_id"), F.lit("")).alias("customer_id"),
        F.initcap(F.trim("first_name")).alias("first_name"),
        F.initcap(F.trim("last_name")).alias("last_name"),
        _try_cast("date_of_birth", "date").alias("date_of_birth"),
        F.lower(F.trim("email")).alias("email"),
        F.trim("phone").alias("phone"),
        _clean("country").alias("country"),
        _clean("segment").alias("segment"),
        _clean("kyc_risk_rating").alias("kyc_risk_rating_raw"),
        (_clean("pep_flag") == "Y").alias("pep_flag"),
        _try_cast("onboarding_date", "date").alias("onboarding_date"),
        _clean("branch_code").alias("branch_code"),
        "_batch_id", "_source_file", "_ingested_at",
    )
    # Fail-safe: an unrecognised KYC rating is treated as HIGH until a data steward corrects it.
    rating_ok = F.col("kyc_risk_rating_raw").isin("LOW", "MEDIUM", "HIGH")
    return (typed.join(countries, typed.country == countries._cc, "left").drop("_cc")
                 .withColumn("country_known", F.coalesce("country_known", F.lit(False)))
                 .withColumn("kyc_risk_rating", F.when(rating_ok, F.col("kyc_risk_rating_raw")).otherwise("HIGH"))
                 .withColumn("risk_rating_defaulted", ~F.coalesce(rating_ok, F.lit(False))))


def _protect_pii(df: DataFrame) -> DataFrame:
    """Silver never stores the raw e-mail or full phone number."""
    return (df.withColumn("email_hash", F.sha2(F.col("email"), 256))
              .withColumn("email_domain", F.regexp_extract("email", "@(.+)$", 1))
              .withColumn("phone_masked", F.concat(F.lit("***-***-"), F.substring("phone", -4, 4)))
              .drop("email", "phone"))


def load_customers(store: TableStore, batch_id: str, business_date: date) -> tuple[list[dict], list[dict], dict]:
    staged = _customers_staged(store, batch_id)
    bronze_rows = staged.count()
    deduped, dupes = _dedupe(staged, "customer_id")
    dq = apply_expectations(deduped, CUSTOMER_RULES, table="silver.customers")
    quarantine = dq.quarantine.withColumn("_quarantined_at", F.current_timestamp())
    _replace_batch(store, _protect_pii(quarantine), "customers_quarantine", batch_id)

    hash_cols = F.sha2(F.concat_ws("||", *[F.coalesce(F.col(c).cast("string"), F.lit("")) for c in SCD2_TRACKED]), 256)
    valid = (_protect_pii(dq.valid)
             .withColumn("dq_warnings", F.col("_dq_failed_rules"))
             .drop("_dq_failed_rules", "country_known", "kyc_risk_rating_raw")
             .withColumn("record_hash", hash_cols))
    valid_rows = valid.count()

    if not store.exists("silver", "customers"):
        first = (valid.withColumn("effective_from", F.col("onboarding_date"))
                      .withColumn("effective_to", F.lit(None).cast("date"))
                      .withColumn("is_current", F.lit(True)))
        store.write(first, "silver", "customers", mode="overwrite", change_feed=True,
                    comment="Customer master, SCD Type 2 history. PII hashed or masked.")
        result = {"inserted_new": valid_rows, "new_versions": 0, "unchanged": 0}
    else:
        target = store.delta_table("silver", "customers")
        current = store.read("silver", "customers").filter("is_current").select("customer_id", "record_hash")
        src = valid.withColumn("effective_from", F.lit(business_date))
        changed = (src.alias("s").join(current.alias("c"), "customer_id")
                      .filter(F.col("s.record_hash") != F.col("c.record_hash")).select("s.*"))
        n_changed = changed.count()
        n_existing = src.join(current, "customer_id", "left_semi").count()
        # Classic SCD2 MERGE: rows keyed by customer_id close the old version;
        # the NULL-keyed copies of changed rows fall through to INSERT as the new version.
        staged_updates = (src.withColumn("_merge_key", F.col("customer_id"))
                             .unionByName(changed.withColumn("_merge_key", F.lit(None).cast("string"))))
        insert_cols = {c: f"s.{c}" for c in src.columns}
        insert_cols.update({
            # Brand-new customers are effective from onboarding; a changed customer's new version from the batch date.
            "effective_from": "CASE WHEN s._merge_key IS NULL THEN s.effective_from ELSE s.onboarding_date END",
            "effective_to": "CAST(NULL AS DATE)",
            "is_current": "true",
        })
        (target.alias("t")
            .merge(staged_updates.alias("s"), "t.customer_id = s._merge_key AND t.is_current")
            .whenMatchedUpdate(condition="t.record_hash <> s.record_hash",
                               set={"is_current": "false", "effective_to": "date_sub(s.effective_from, 1)"})
            .whenNotMatchedInsert(values=insert_cols)
            .execute())
        result = {"inserted_new": valid_rows - n_existing, "new_versions": n_changed,
                  "unchanged": n_existing - n_changed}

    q_rows = dq.quarantine.count()
    controls = [control_check(
        "silver_customers_reconcile_to_bronze", bronze_rows == dupes + q_rows + valid_rows,
        table="silver.customers", layer="silver",
        detail=f"batch {batch_id}: bronze {bronze_rows} = duplicates {dupes} + quarantined {q_rows} + "
               f"valid {valid_rows}", rows_checked=bronze_rows)]
    step = {"layer": "silver", "table": "customers", "batch_id": batch_id, "rows_in": bronze_rows,
            "duplicates_removed": dupes, "rows_quarantined": q_rows, "rows_written": valid_rows, **result}
    return [step], dq.metrics + controls, result


# --------------------------------------------------------------------------- accounts
def load_accounts(store: TableStore, batch_id: str) -> tuple[list[dict], list[dict]]:
    known = (store.read("silver", "customers").select("customer_id").distinct()
             .withColumn("customer_known", F.lit(True)).withColumnRenamed("customer_id", "_cid"))
    raw = _bronze_batch(store, "accounts", batch_id)
    typed = raw.select(
        F.nullif(F.trim("account_id"), F.lit("")).alias("account_id"),
        F.trim("customer_id").alias("customer_id"),
        _clean("product").alias("product"),
        _clean("currency").alias("currency"),
        _try_cast("open_date", "date").alias("open_date"),
        _clean("status").alias("status"),
        _clean("branch_code").alias("branch_code"),
        _try_cast("credit_limit", "decimal(18,2)").alias("credit_limit"),
        "_batch_id", "_source_file", "_ingested_at",
    )
    typed = (typed.join(known, typed.customer_id == known._cid, "left").drop("_cid")
                  .withColumn("customer_known", F.coalesce("customer_known", F.lit(False))))
    bronze_rows = typed.count()
    deduped, dupes = _dedupe(typed, "account_id")
    dq = apply_expectations(deduped, ACCOUNT_RULES, table="silver.accounts")
    _replace_batch(store, dq.quarantine.withColumn("_quarantined_at", F.current_timestamp()),
                   "accounts_quarantine", batch_id)
    valid = (dq.valid.drop("_dq_failed_rules", "customer_known")
                     .withColumn("_updated_at", F.current_timestamp()))
    valid_rows = valid.count()
    if not store.exists("silver", "accounts"):
        store.write(valid, "silver", "accounts", mode="overwrite", change_feed=True,
                    comment="Account master (SCD Type 1, upserted)")
        merged = {"numTargetRowsInserted": valid_rows, "numTargetRowsUpdated": 0}
    else:
        (store.delta_table("silver", "accounts").alias("t")
            .merge(valid.alias("s"), "t.account_id = s.account_id")
            .whenMatchedUpdateAll(condition="t.status <> s.status OR t.product <> s.product "
                                            "OR NOT (t.credit_limit <=> s.credit_limit)")
            .whenNotMatchedInsertAll()
            .execute())
        merged = _merge_metrics(store, "accounts")
    q_rows = dq.quarantine.count()
    controls = [control_check(
        "silver_accounts_reconcile_to_bronze", bronze_rows == dupes + q_rows + valid_rows,
        table="silver.accounts", layer="silver",
        detail=f"batch {batch_id}: bronze {bronze_rows} = duplicates {dupes} + quarantined {q_rows} + "
               f"valid {valid_rows}", rows_checked=bronze_rows)]
    step = {"layer": "silver", "table": "accounts", "batch_id": batch_id, "rows_in": bronze_rows,
            "duplicates_removed": dupes, "rows_quarantined": q_rows, "rows_written": valid_rows,
            "inserted_new": merged.get("numTargetRowsInserted", 0),
            "updated": merged.get("numTargetRowsUpdated", 0)}
    return [step], dq.metrics + controls


# --------------------------------------------------------------------------- transactions
def _transactions_staged(store: TableStore, batch_id: str, business_date: date) -> DataFrame:
    raw = _bronze_batch(store, "transactions", batch_id)
    accounts = store.read("silver", "accounts").select(
        F.col("account_id").alias("_acct"), "customer_id", F.col("product").alias("account_product"))
    fx = store.read("silver", "ref_fx_rates").select(
        F.col("rate_date").alias("_fx_date"), F.col("currency").alias("_fx_ccy"), "usd_rate")
    risk = store.read("silver", "ref_country_risk").select(
        F.col("country_code").alias("_cp_cc"), F.col("risk_tier").alias("counterparty_risk_tier"))
    cutoff = datetime.combine(business_date + timedelta(days=1), datetime.min.time())

    typed = raw.select(
        F.nullif(F.trim("txn_id"), F.lit("")).alias("txn_id"),
        F.trim("account_id").alias("account_id"),
        F.expr("try_to_timestamp(txn_ts, \"yyyy-MM-dd'T'HH:mm:ss'Z'\")").alias("txn_ts"),
        _try_cast("amount", "decimal(18,2)").alias("amount"),
        _clean("currency").alias("currency"),
        _clean("txn_type").alias("txn_type"),
        _clean("channel").alias("channel"),
        F.trim("counterparty_name").alias("counterparty_name"),
        _clean("counterparty_country").alias("counterparty_country"),
        F.trim("reference").alias("reference"),
        F.col("txn_ts").alias("txn_ts_raw"),
        F.col("amount").alias("amount_raw"),
        "_batch_id", "_source_file", "_ingested_at",
    )
    return (typed
            .join(accounts, typed.account_id == accounts._acct, "left")
            .withColumn("account_known", F.col("_acct").isNotNull()).drop("_acct")
            .join(fx, (F.to_date("txn_ts") == F.col("_fx_date")) & (F.col("currency") == F.col("_fx_ccy")), "left")
            .drop("_fx_date", "_fx_ccy")
            .join(risk, F.col("counterparty_country") == F.col("_cp_cc"), "left").drop("_cp_cc")
            .withColumn("counterparty_risk_tier", F.coalesce("counterparty_risk_tier", F.lit("UNKNOWN")))
            .withColumn("business_cutoff", F.lit(cutoff).cast("timestamp")))


def load_transactions(store: TableStore, batch_id: str, business_date: date) -> tuple[list[dict], list[dict]]:
    staged = _transactions_staged(store, batch_id, business_date)
    bronze_rows = staged.count()
    deduped, dupes = _dedupe(staged, "txn_id")
    dq = apply_expectations(deduped, TRANSACTION_RULES, table="silver.transactions")
    _replace_batch(store, dq.quarantine.drop("business_cutoff").withColumn("_quarantined_at", F.current_timestamp()),
                   "transactions_quarantine", batch_id)

    direction = F.when(F.col("txn_type").isin(*CREDIT_TYPES), "CREDIT").otherwise("DEBIT")
    valid = (dq.valid
             .withColumn("txn_date", F.to_date("txn_ts"))
             .withColumn("amount_usd", F.round(F.col("amount") * F.col("usd_rate"), 2).cast("decimal(18,2)"))
             .withColumn("direction", direction)
             .withColumn("signed_amount_usd",
                         F.when(direction == "CREDIT", F.col("amount_usd")).otherwise(-F.col("amount_usd")))
             .withColumn("dq_warnings", F.col("_dq_failed_rules"))
             .drop("_dq_failed_rules", "account_known", "business_cutoff", "txn_ts_raw", "amount_raw"))
    valid_rows = valid.count()

    if not store.exists("silver", "transactions"):
        store.write(valid, "silver", "transactions", mode="overwrite", change_feed=True,
                    comment="Validated transactions in USD, enriched with customer and counterparty risk")
        inserted = valid_rows
    else:
        # Insert-only MERGE: a transaction id that is already loaded is never inserted again.
        (store.delta_table("silver", "transactions").alias("t")
            .merge(valid.alias("s"), "t.txn_id = s.txn_id")
            .whenNotMatchedInsertAll()
            .execute())
        inserted = _merge_metrics(store, "transactions").get("numTargetRowsInserted", 0)
    already_present = valid_rows - inserted
    q_rows = dq.quarantine.count()
    controls = [
        control_check("silver_transactions_reconcile_to_bronze",
                      bronze_rows == dupes + q_rows + inserted + already_present,
                      table="silver.transactions", layer="silver", rows_checked=bronze_rows,
                      detail=f"batch {batch_id}: bronze {bronze_rows} = duplicates {dupes} + quarantined {q_rows} "
                             f"+ inserted {inserted} + already loaded {already_present}"),
    ]
    step = {"layer": "silver", "table": "transactions", "batch_id": batch_id, "rows_in": bronze_rows,
            "duplicates_removed": dupes, "rows_quarantined": q_rows, "rows_written": inserted,
            "already_loaded": already_present}
    return [step], dq.metrics + controls
