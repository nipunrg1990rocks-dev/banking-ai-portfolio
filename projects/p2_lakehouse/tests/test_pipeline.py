"""End-to-end checks on one full pipeline run: every planted fact must be found, nothing else."""

from datetime import date

import pytest
from conftest import requires_spark

pytestmark = requires_spark


@pytest.fixture(scope="module")
def run(full_run):
    return full_run


@pytest.fixture(scope="module")
def store(run):
    return run[0]


@pytest.fixture(scope="module")
def oracle(run):
    return run[1]["oracle"]


def _ids(df, col):
    return {r[0] for r in df.select(col).collect()}


# ------------------------------------------------------------------- bronze
def test_bronze_lands_every_source_row_with_lineage(store, oracle):
    from pyspark.sql import functions as F

    txns = store.read("bronze", "transactions")
    for batch, counts in oracle["counts"].items():
        assert txns.filter(F.col("_batch_id") == batch).count() == counts["transactions"]
    missing_lineage = txns.filter(F.col("_source_file").isNull() | F.col("_ingested_at").isNull()).count()
    assert missing_lineage == 0
    assert all(t.dataType.simpleString() == "string" for t in txns.schema.fields if not t.name.startswith("_"))


# ------------------------------------------------------------------- silver
def test_quarantine_catches_exactly_the_planted_defects(store, oracle):
    q = store.read("silver", "transactions_quarantine").collect()
    by_rule = {}
    for row in q:
        assert len(row["_dq_failed_rules"]) == 1, "each planted defect should break exactly one rule"
        by_rule.setdefault(row["_dq_failed_rules"][0], set()).add(row["txn_id"] or "")
    assert by_rule == {rule: set(ids) for rule, ids in oracle["txn_defects"].items()}


def test_every_bronze_transaction_is_accounted_for(store, oracle, run):
    steps = [s for s in run[1]["steps"] if s["step"] == "silver" and s["table_name"] == "transactions"]
    for s in steps:
        import json

        already = json.loads(s["details"])["already_loaded"]
        assert s["rows_in"] == s["duplicates_removed"] + s["rows_quarantined"] + s["rows_written"] + already
    assert sum(oracle["duplicate_txn_rows"].values()) == sum(s["duplicates_removed"] for s in steps)


def test_silver_transactions_are_unique_and_resends_not_double_counted(store, oracle):
    txns = store.read("silver", "transactions")
    assert txns.count() == txns.select("txn_id").distinct().count()
    resent = txns.filter(txns.txn_id.isin(oracle["cross_batch_resends"]))
    assert resent.count() == len(oracle["cross_batch_resends"])
    assert _ids(resent, "_batch_id") == {"20260930"}, "a late re-send must keep its original load"


def test_amounts_are_converted_to_usd_with_the_daily_rate(store):
    from pyspark.sql import functions as F

    txns = store.read("silver", "transactions")
    fx = store.read("silver", "ref_fx_rates").select("rate_date", F.col("currency").alias("fx_ccy"),
                                                     F.col("usd_rate").alias("ref_rate"))
    joined = txns.join(fx, (txns.txn_date == fx.rate_date) & (txns.currency == fx.fx_ccy))
    bad = joined.filter(F.abs(F.col("amount_usd") - F.round(F.col("amount") * F.col("ref_rate"), 2)) > 0.005)
    assert bad.count() == 0
    assert joined.count() == txns.count()
    assert txns.filter("currency <> 'USD'").count() > 100


def test_pii_is_hashed_or_masked_in_silver(store):
    customers = store.read("silver", "customers")
    assert "email" not in customers.columns and "phone" not in customers.columns
    sample = customers.select("email_hash", "phone_masked").limit(50).collect()
    assert all(len(r["email_hash"]) == 64 and r["phone_masked"].startswith("***-***-") for r in sample)


def test_scd2_keeps_history_for_changed_customers(store, oracle):
    from pyspark.sql import functions as F

    customers = store.read("silver", "customers")
    changed = customers.filter(F.col("customer_id").isin(oracle["scd2_changed_customers"]))
    assert changed.count() == 2 * len(oracle["scd2_changed_customers"])
    assert changed.filter("is_current").count() == len(oracle["scd2_changed_customers"])
    closed = changed.filter("NOT is_current").collect()
    assert {r["effective_to"] for r in closed} == {date(2026, 10, 6)}
    unchanged = customers.filter(F.col("customer_id").isin(oracle["unchanged_resent_customers"]))
    assert unchanged.count() == len(oracle["unchanged_resent_customers"]), "identical re-sends add no version"
    per_customer = customers.groupBy("customer_id").agg(F.sum(F.col("is_current").cast("int")).alias("n"))
    assert per_customer.filter("n <> 1").count() == 0, "exactly one current version per customer"


def test_invalid_risk_rating_defaults_to_high(store, oracle):
    from pyspark.sql import functions as F

    cid = oracle["customer_defects"]["valid_risk_rating"][0]
    row = store.read("silver", "customers").filter(F.col("customer_id") == cid).collect()[0]
    assert row["kyc_risk_rating"] == "HIGH" and row["risk_rating_defaulted"]
    assert "valid_risk_rating" in row["dq_warnings"]


def test_account_status_changes_are_upserted(store, oracle):
    from pyspark.sql import functions as F

    accounts = store.read("silver", "accounts")
    assert accounts.count() == accounts.select("account_id").distinct().count()
    changed = accounts.filter(F.col("account_id").isin(oracle["account_status_changes"]))
    assert _ids(changed, "status") <= {"DORMANT", "CLOSED"}
    assert _ids(store.read("silver", "accounts_quarantine"), "account_id") == set(
        oracle["account_defects"]["known_customer"])


# --------------------------------------------------------------------- gold
def test_aml_rules_raise_exactly_the_planted_scenarios(store, oracle):
    """The rules fire on every planted scenario, plus the one planted false positive (a payroll cycle)."""
    alerts = store.read("gold", "aml_alert_candidates").collect()
    found = {}
    for a in alerts:
        found.setdefault(a["rule_code"], set()).add(a["customer_id"])
        assert a["status"] == "PENDING_REVIEW"
    expected = {rule: set(ids) for rule, ids in oracle["aml_scenarios"].items()}
    for rule, ids in oracle["aml_false_positives"].items():
        expected[rule] |= set(ids)
    assert found == expected


def test_customer_360_has_one_row_per_customer_and_no_direct_pii(store):
    c360 = store.read("gold", "customer_360")
    current = store.read("silver", "customers").filter("is_current").count()
    assert c360.count() == current == c360.select("customer_id").distinct().count()
    assert not {"first_name", "last_name", "email_hash", "phone_masked", "date_of_birth"} & set(c360.columns)


def test_gold_reconciles_to_silver(store):
    from pyspark.sql import functions as F

    silver = store.read("silver", "transactions")
    kpis = store.read("gold", "segment_monthly_kpis").agg(F.sum("txn_count"), F.sum("volume_usd")).collect()[0]
    total = silver.agg(F.sum("amount_usd")).collect()[0][0]
    assert kpis[0] == silver.count()
    assert abs(float(kpis[1]) - float(total)) < 1


def test_dq_scorecard_reports_every_rule(store):
    rows = store.read("gold", "dq_scorecard").collect()
    statuses = {r["rule_name"]: r["status"] for r in rows if r["table_name"] == "silver.transactions"}
    assert statuses["amount_present"] == "QUARANTINED"
    assert statuses["fx_rate_available"] == "PASS"
    assert all(r["status"] == "PASS" for r in rows if r["action"] == "fail")


# -------------------------------------------------------------------- delta
def test_time_travel_returns_the_september_state(store, run):
    batch1 = next(s for s in run[1]["steps"] if s["step"] == "silver" and s["table_name"] == "transactions")
    assert store.read("silver", "transactions", version=0).count() == batch1["rows_written"]
    ops = [r["operation"] for r in store.history("silver", "transactions").orderBy("version").collect()]
    assert ops[:2] == ["WRITE", "MERGE"] and "OPTIMIZE" in ops


def test_change_data_feed_shows_scd2_updates(store, oracle):
    from pyspark.sql import functions as F

    cdf = store.changes("silver", "customers", starting_version=1)
    updated = _ids(cdf.filter(F.col("_change_type") == "update_postimage"), "customer_id")
    assert updated == set(oracle["scd2_changed_customers"])


def test_schema_enforcement_rejects_a_bad_append(store, spark):
    bad = spark.createDataFrame([("T1", "not-a-number")], "txn_id string, amount_usd string")
    with pytest.raises(Exception, match="(?i)schema|merge|type"):
        store.write(bad, "silver", "transactions", mode="append")


# ---------------------------------------------------------- idempotency (last: it writes)
def test_rerunning_a_batch_changes_nothing(store):
    from lakehouse.pipeline import Pipeline

    before = {t: store.read("silver", t).count() for t in ("customers", "accounts", "transactions",
                                                           "transactions_quarantine")}
    bronze_before = store.read("bronze", "transactions").count()
    p = Pipeline(store)
    p.bronze("20261007")
    p.silver("20261007")
    after = {t: store.read("silver", t).count() for t in before}
    assert after == before
    assert store.read("bronze", "transactions").count() == bronze_before
    skipped = [s for s in p.step_log if s["step"] == "bronze"]
    assert all("skipped" in (s["details"] or "") for s in skipped)


# ------------------------------------------------------------------ incident
def test_truncated_delivery_is_blocked_and_nothing_downstream_changes(full_run, oracle):
    store, result = full_run
    incident = result["incident"]
    assert incident["outcome"] == "BLOCKED"
    assert "bronze_record_count_matches_manifest" in incident["error"]
    for table in ("bronze.transactions", "silver.transactions", "gold.daily_transaction_summary"):
        assert incident["versions_after"][table] == incident["versions_before"][table], table
    assert incident["rows_landed_in_bronze"]["transactions"] == 0
    failed = [d for d in result["dq"] if d["batch_id"] == oracle["incident"]["batch_id"] and d["rows_failed"]]
    assert [d["rule_name"] for d in failed] == ["bronze_record_count_matches_manifest"]
    assert failed[0]["rows_checked"] == oracle["incident"]["complete_rows_delivered"] + 1
    step = [s for s in result["steps"] if s["status"] == "FAILED"]
    assert len(step) == 1 and step[0]["batch_id"] == oracle["incident"]["batch_id"]


def test_delivery_forensics_find_the_truncation(full_run, oracle):
    from lakehouse.forensics import inspect_delivery

    store, _ = full_run
    evidence = inspect_delivery(store.config.landing, oracle["incident"]["batch_id"])
    txn = evidence["files"]["transactions.jsonl"]
    assert txn["checksum_matches"] is False and txn["last_line_is_valid_json"] is False
    assert txn["actual_bytes"] < txn["expected_bytes"]
    assert evidence["files"]["customers.csv"]["checksum_matches"] is True
    assert inspect_delivery(store.config.landing, "20260930")["files"]["transactions.jsonl"]["checksum_matches"]
