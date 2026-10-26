"""The data, the metric SQL and the engines."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from riskdash import catalog
from riskdash.engine import DatabricksSQLEngine, DuckDBEngine, QueryError, SparkEngine, duckdb_sql_for_display
from riskdash.synthetic import AS_OF, PLANTED_INCIDENTS, generate

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


def test_generator_is_deterministic(mart, gold):
    again = generate(gold)
    assert set(again) == set(mart)
    for name, df in mart.items():
        pd.testing.assert_frame_equal(df.reset_index(drop=True), again[name].reset_index(drop=True))


def test_committed_sample_matches_the_generator(mart):
    for name, df in mart.items():
        path = SAMPLE / f"{name}.csv"
        assert path.exists(), f"missing sample file {path.name}"
        if df.empty:
            continue
        sample = pd.read_csv(path)
        assert list(sample.columns) == list(df.columns), name
        assert len(sample) == len(df), name


def test_builds_on_project_02_gold(gold, store):
    # The high-risk KYC population comes from P2's customer_360, not from the simulator.
    high = gold["customer_360"].query("kyc_risk_rating == 'HIGH'")
    assert store.value("KRI_HR_KYC_OVERDUE_PCT", AS_OF) == pytest.approx(100 / len(high))


def test_every_metric_has_a_value_and_status(store):
    for m in catalog.METRICS:
        s = store.snapshot(m.id, AS_OF)
        assert s.value is not None, m.id
        assert s.status in {"GREEN", "AMBER", "RED", "NONE"}, m.id
        if m.limit is not None:
            assert s.status != "NONE", m.id


@pytest.mark.parametrize("direction,ew,limit,value,expected", [
    ("higher_is_better", 120, 110, 125, "GREEN"),
    ("higher_is_better", 120, 110, 117.6, "AMBER"),
    ("higher_is_better", 120, 110, 109.9, "RED"),
    ("lower_is_better", 1, 2, 0.5, "GREEN"),
    ("lower_is_better", 1, 2, 1, "AMBER"),
    ("lower_is_better", 1, 2, 2.6, "RED"),
    ("lower_is_better", None, 0, 0, "GREEN"),
    ("lower_is_better", None, 0, 1, "RED"),
])
def test_rag_rules_follow_ras_004(direction, ew, limit, value, expected):
    m = catalog.Metric(id="X", name="x", domain="d", view="v", numerator="n", denominator=None, unit="%",
                       direction=direction, early_warning=ew, limit=limit, owner="o", definition="")
    assert catalog.rag(m, value) == expected


@pytest.mark.parametrize("metric_id,expected", [
    ("KRI_LCR_PCT", 117.6),
    ("KRI_HR_KYC_OVERDUE_PCT", 100 / 39),
    ("KRI_OPLOSS_YTD_USD_M", 10.8),
])
def test_planted_values(store, metric_id, expected):
    assert store.value(metric_id, AS_OF) == pytest.approx(expected, abs=0.01)


def test_planted_incidents_are_visible_in_the_metrics(store):
    for inc in PLANTED_INCIDENTS:
        hist = store.status_history(inc["metric"])
        before = hist.loc[inc["onset"] - timedelta(days=10)]
        after = hist[(hist.index >= inc["onset"]) & (hist.index <= AS_OF)]
        rank = {"NONE": 0, "GREEN": 1, "AMBER": 2, "RED": 3}
        now = max(after, key=rank.get)
        assert now in {"AMBER", "RED"} and rank[now] > rank[before], (inc["id"], before, now)


def test_offline_engine_is_read_only(store):
    with pytest.raises(QueryError):
        store.engine.execute("COPY (SELECT 1) TO '/tmp/riskdash_should_not_exist.csv'")
    with pytest.raises(QueryError):
        store.engine.execute("SET enable_external_access = true")


def test_view_sql_is_databricks_sql_and_translates(store):
    for view in catalog.VIEWS:
        template = catalog.view_sql(view)
        assert "${mart." in template or "${gold." in template
        duck = duckdb_sql_for_display(template)
        assert "${" not in duck and "ledgerline_" in duck


def _text(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    text = str(v)[:19]
    return text[:10] if text.endswith(" 00:00:00") else text


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if any(isinstance(v, Decimal) for v in out[c].dropna().head(5)):  # Spark DECIMAL
            out[c] = out[c].astype(float)
        if pd.api.types.is_bool_dtype(out[c]) or not pd.api.types.is_numeric_dtype(out[c]):
            out[c] = out[c].map(_text)
        else:
            out[c] = out[c].astype(float).round(6)
    return out.sort_values(list(out.columns), na_position="first").reset_index(drop=True)


def test_duckdb_and_spark_give_identical_results(spark, mart, gold):
    """The same Databricks SQL views run unchanged on Spark and translated on DuckDB."""
    duck = DuckDBEngine(mart, gold)
    spk = SparkEngine.with_temp_views(spark, mart, gold)
    for view in catalog.VIEWS:
        a = _normalise(duck.run(catalog.view_sql(view)))
        b = _normalise(spk.run(catalog.view_sql(view)))
        assert list(a.columns) == list(b.columns), view
        pd.testing.assert_frame_equal(a, b, check_dtype=False, obj=view)


class FakeCursor:
    def __init__(self, log):
        self.log = log
        self.description = [("metric",), ("value",)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.log.append(sql)

    def fetchall(self):
        return [("LCR", 117.6)]


class FakeConnection:
    def __init__(self):
        self.log = []

    def cursor(self):
        return FakeCursor(self.log)


def test_databricks_sql_engine_runs_unchanged_sql_on_unity_catalog_names():
    conn = FakeConnection()
    engine = DatabricksSQLEngine(catalog="workspace", connection=conn)
    df = engine.run(catalog.view_sql("fv_hr_kyc_reviews"))
    sql = conn.log[0]
    assert "workspace.ledgerline_riskmart.kyc_reviews" in sql
    assert "workspace.ledgerline_gold.customer_360" in sql
    assert "${" not in sql
    assert df.to_dict("records") == [{"metric": "LCR", "value": 117.6}]


def test_databricks_sql_engine_needs_configuration(monkeypatch):
    for name in ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(QueryError, match="not configured"):
        DatabricksSQLEngine()


def test_databricks_notebook_path_on_local_spark(spark, store, briefing, tmp_path):
    """What the Databricks notebook does, run on local Spark + Delta: tables, views, agent results."""
    from riskdash import lakehouse

    for schema in ("ledgerline_gold", "ledgerline_riskmart"):
        spark.sql(f"DROP SCHEMA IF EXISTS spark_catalog.{schema} CASCADE")
        spark.sql(f"CREATE SCHEMA spark_catalog.{schema} LOCATION '{tmp_path / schema}'")
    lakehouse.write_gold_snapshot(spark, "spark_catalog")
    gold = lakehouse.read_gold(spark, "spark_catalog")
    counts = lakehouse.build_risk_mart(spark, "spark_catalog", gold)
    assert counts["exceptions"] > 1000
    views = lakehouse.create_views(spark, "spark_catalog")
    assert len(views) == len(catalog.VIEWS)
    assert spark.table("spark_catalog.ledgerline_riskmart.fv_hr_kyc_reviews").count() > 0

    uc_store = lakehouse.metric_store(spark, "spark_catalog")
    for m in catalog.METRICS:
        assert uc_store.value(m.id, AS_OF) == pytest.approx(store.value(m.id, AS_OF), abs=1e-6, nan_ok=True), m.id
    uc_briefing, snapshot = lakehouse.publish_results(spark, "spark_catalog", uc_store)
    assert uc_briefing.markdown() == briefing.markdown()  # the agent reaches the same conclusions on Spark
    findings = spark.table("spark_catalog.ledgerline_riskmart.agent_findings").toPandas()
    assert len(findings) == len(briefing.findings)
    assert set(findings["root_cause"].dropna()) == {i["change_id"] for i in PLANTED_INCIDENTS if i["change_id"]}
    assert spark.table("spark_catalog.ledgerline_riskmart.kri_snapshot").count() == len(snapshot)
