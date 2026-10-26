"""Data-quality engine and blocking controls, tested in isolation."""

import json

import pytest
from conftest import requires_spark

pytestmark = requires_spark


def test_null_counts_as_failure_and_warn_keeps_rows(spark):
    from lakehouse.dq import DROP, WARN, Rule, apply_expectations

    df = spark.createDataFrame([(1, 10.0, "a@x.com"), (2, None, "bad"), (3, -5.0, "c@x.com")],
                               "id int, amount double, email string")
    rules = [Rule("amount_positive", "amount > 0", DROP, ""), Rule("email_ok", "email LIKE '%@%'", WARN, "")]
    res = apply_expectations(df, rules, table="t")
    assert sorted(r["id"] for r in res.valid.collect()) == [1]
    assert sorted(r["id"] for r in res.quarantine.collect()) == [2, 3]
    metrics = {m["rule_name"]: m["rows_failed"] for m in res.metrics}
    assert metrics == {"amount_positive": 2, "email_ok": 1}


def test_fail_rule_stops_the_pipeline(spark):
    from lakehouse.dq import FAIL, DataQualityError, Rule, apply_expectations

    df = spark.createDataFrame([(1,), (None,)], "id int")
    with pytest.raises(DataQualityError, match="id_present"):
        apply_expectations(df, [Rule("id_present", "id IS NOT NULL", FAIL, "")], table="t")


def test_manifest_mismatch_blocks_bronze_and_is_logged(spark, tmp_path):
    from lakehouse import synthetic
    from lakehouse.dq import DataQualityError
    from lakehouse.pipeline import Pipeline
    from lakehouse.store import LakehouseConfig, TableStore

    store = TableStore(spark, LakehouseConfig.local(tmp_path))
    synthetic.generate(store.config.landing, n_customers=150, txns_per_day=40)
    manifest_path = tmp_path / "landing" / "batch=20260930" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["record_counts"]["transactions"] += 1  # the source claims a row we never received
    manifest_path.write_text(json.dumps(manifest))

    p = Pipeline(store)
    p.reference()
    with pytest.raises(DataQualityError, match="bronze_record_count_matches_manifest"):
        p.bronze("20260930")
    failed = store.read("audit", "pipeline_steps").filter("status = 'FAILED'").collect()
    assert len(failed) == 1 and "manifest" in failed[0]["details"]


def test_gold_sql_resolves_every_table_reference(spark, tmp_path):
    from lakehouse.gold import gold_sql_files, render
    from lakehouse.store import LakehouseConfig, TableStore

    local = TableStore(spark, LakehouseConfig.local(tmp_path))
    uc = TableStore(spark, LakehouseConfig.databricks("workspace"))
    for path in gold_sql_files():
        sql = path.read_text()
        assert "${" not in render(sql, local)
        rendered = render(sql, uc)
        assert "${" not in rendered and "workspace.ledgerline_" in rendered
