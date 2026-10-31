"""Validation gate: seeded anomalies, schema errors, unsafe content, limits."""

import io

import pandas as pd
import pytest

from deckpilot.domains.ops_risk import OpsRiskDomain
from deckpilot.domains.workforce import WorkforceDomain
from deckpilot.store import DatasetRegistry
from deckpilot.validation import MAX_UPLOAD_BYTES, read_table


def test_seeded_ops_anomalies_are_quarantined(ops_env):
    _, ds, _ = ops_env
    reasons = ds.report.quarantine["quarantine_reason"].value_counts().to_dict()
    assert reasons["duplicate of an earlier row (same month/region/line/process/severity/cause)"] == 3
    assert reasons["unexpected region value"] == 1  # LATAM
    assert reasons["completed_on_time exceeds transaction_volume"] == 1
    assert reasons["risk_score out of range 0-100"] == 1
    assert reasons["loss_amount_inr out of range 0-5000000000"] == 1
    names = {c.name: c for c in ds.report.checks}
    assert names["Category spelling normalised"].count == 1  # 'Apac' fixed, not dropped
    assert names["Missing optional values"].count == 2
    assert ds.report.ok


def test_missing_required_column_blocks_activation(ops_raw):
    reg = DatasetRegistry()
    ds = OpsRiskDomain().load(ops_raw.drop(columns=["exceptions"]), "bad.csv", True, reg)
    assert not ds.report.ok
    reg.stage(ds)
    with pytest.raises(ValueError):
        reg.activate(ds.version)


def test_formula_text_is_neutralised(ops_raw):
    raw = ops_raw.head(50).copy()
    raw.loc[0, "control_owner"] = '=HYPERLINK("http://evil","x")'
    ds = OpsRiskDomain().load(raw, "f.csv", True, DatasetRegistry())
    owners = ds.tables["fact"]["control_owner"]
    assert not owners.str.startswith("=").any()
    assert owners.str.startswith("'=").any()


def test_upload_limits_and_types():
    with pytest.raises(ValueError):
        read_table(b"x" * (MAX_UPLOAD_BYTES + 1), "big.csv")
    with pytest.raises(ValueError):
        read_table(b"print('hi')", "evil.py")


def test_refresh_file_adds_month_and_quarantines_bad_rows(service):
    from p6_helpers import DATA

    ds = service.stage_upload("ops_risk", (DATA / "ops_risk_refresh_oct2026.csv").read_bytes(),
                              "ops_risk_refresh_oct2026.csv")
    assert ds.report.ok
    assert "2026-10" in ds.months
    reasons = ds.report.quarantine["quarantine_reason"].value_counts().to_dict()
    assert reasons.get("invalid reporting_month", 0) >= 1
    # staged data does not change the active dataset until activated
    assert service.dataset("ops_risk").months[-1] == "2026-09"
    service.activate(ds.version)
    assert service.dataset("ops_risk").months[-1] == "2026-10"


def test_workforce_bad_allocation_quarantined(wf_raw):
    raw = wf_raw.copy()
    raw.loc[5, "Mar-18"] = 1.7
    raw.loc[6, "Category"] = "Consultant"
    raw.loc[7, "End Date"] = pd.Timestamp("2017-01-01")
    ds = WorkforceDomain().load(raw, "r.csv", True, DatasetRegistry())
    reasons = set(ds.report.quarantine["quarantine_reason"])
    assert {"allocation missing or outside 0-1", "unknown resource category", "end date before start date"} <= reasons
    assert "email" not in " ".join(ds.tables["people"].columns).lower()  # data minimisation
    assert "name" not in ds.tables["people"].columns


def test_workforce_xlsx_roundtrip(wf_raw):
    buf = io.BytesIO()
    wf_raw.to_excel(buf, sheet_name="Data", index=False)
    df = read_table(buf.getvalue(), "roster.xlsx", sheet_hint="Data")
    ds = WorkforceDomain().load(df, "roster.xlsx", False, DatasetRegistry(), snapshot="2018-09-30")
    assert ds.report.ok and len(ds.months) == 12
