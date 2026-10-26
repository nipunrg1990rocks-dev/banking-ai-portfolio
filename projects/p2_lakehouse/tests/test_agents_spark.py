"""The agents on live Spark tables (the Databricks path), with the offline planner."""

from conftest import requires_spark

pytestmark = requires_spark


def test_agents_work_the_live_lakehouse_and_save_to_delta(full_run):
    from agents import SparkBackend
    from agents.lakehouse_io import investigate, save

    store, result = full_run
    backend = SparkBackend(store)
    runs = investigate(backend)
    aml = [r for r in runs if r.agent == "aml_investigator"]
    oracle = result["oracle"]
    false_positives = set(sum(oracle["aml_false_positives"].values(), []))
    assert len(aml) == 8 and all(r.status == "completed" for r in runs)
    customer = dict(backend.sql("SELECT alert_id, customer_id FROM aml_alerts").values.tolist())
    for r in aml:
        expected = "CLOSE" if customer[r.output["alert_id"]] in false_positives else "ESCALATE"
        assert r.output["recommendation"] == expected, r.output["alert_id"]

    steward = next(r for r in runs if r.agent == "data_steward")
    assert steward.output["root_cause_category"] == "TRUNCATED_FILE"
    assert steward.output["rows_missing"] == oracle["incident"]["expected_rows"] - oracle["incident"][
        "complete_rows_delivered"]

    written = save(store, runs)
    assert written["gold.aml_case_reports"] == 8
    reports = store.read("gold", "aml_case_reports")
    assert dict(reports.dtypes)["risk_score"] == "int"
    assert reports.filter("review_status = 'PENDING_HUMAN_REVIEW' AND customer_id IS NOT NULL").count() == 8
    assert store.read("audit", "agent_steps").count() == written["audit.agent_steps"]
