"""The committed app snapshot must be what the pipeline really produces."""

import csv
import json
from pathlib import Path

import pytest
from conftest import requires_spark

SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "snapshot"


def test_snapshot_files_present():
    summary = json.loads((SNAPSHOT / "run_summary.json").read_text())
    assert summary["run_id"].startswith("run_")
    for name in ("gold_aml_alert_candidates.csv", "gold_customer_360.csv", "gold_dq_scorecard.csv",
                 "dq_results.csv", "delta_history.json", "scd2_customer_history.csv", "silver_transactions.csv"):
        assert (SNAPSHOT / name).exists(), name


@requires_spark
def test_snapshot_matches_a_fresh_run(full_run):
    store, _ = full_run
    summary = json.loads((SNAPSHOT / "run_summary.json").read_text())
    for key, committed in summary["table_counts"].items():
        layer, table = key.split(".")
        assert store.read(layer, table).count() == committed, key
    with (SNAPSHOT / "gold_aml_alert_candidates.csv").open() as fh:
        committed_alerts = {r["alert_id"] for r in csv.DictReader(fh)}
    fresh = {r["alert_id"] for r in store.read("gold", "aml_alert_candidates").collect()}
    assert committed_alerts == fresh


@pytest.mark.parametrize("name", ["gold_customer_360.csv", "silver_transactions.csv"])
def test_snapshot_has_no_raw_contact_details(name):
    header = (SNAPSHOT / name).read_text().splitlines()[0].split(",")
    assert not {"email", "phone", "first_name", "last_name"} & set(header)
