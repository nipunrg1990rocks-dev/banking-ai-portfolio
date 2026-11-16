"""Landing-zone conversion (no Spark needed): contract coverage, text landing and DQ05 schema drift."""

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lakehouse"))

from cf360_lake.contract import build_contract  # noqa: E402
from cf360_lake.convert import convert  # noqa: E402


def test_baseline_lands_every_contract_table_as_text(tmp_path):
    contract = build_contract(ROOT / "data" / "excel")
    m = convert(sorted((ROOT / "data" / "excel").glob("*.xlsx")), tmp_path, "B1", contract)
    assert not m["rejected"] and set(m["tables"]) == set(contract)
    t = pq.read_table(tmp_path / "trades" / "batch_id=B1" / "part-0.parquet")
    assert t.num_rows == 500 and all(str(f.type) == "string" for f in t.schema if f.name != "batch_id")
    assert len(m["files"]) == 13 and all(len(f["sha256"]) == 64 for f in m["files"])
    assert json.loads((tmp_path / "_batches" / "B1.json").read_text())["batch_id"] == "B1"


def test_schema_drift_is_rejected_whole(tmp_path):
    contract = build_contract(ROOT / "data" / "excel")
    m = convert([ROOT / "data" / "negative" / "Trades_schema_drift.xlsx"], tmp_path, "N1", contract)
    assert m["tables"] == {}
    [r] = m["rejected"]
    assert (r["rule_id"], r["error"], r["missing"], sorted(r["unexpected"])) == ("DQ05", "E_SCHEMA_DRIFT", ["quantity"], ["desk_comment", "qty"])


def test_local_lakehouse_evidence_is_all_pass():
    ev = json.loads((ROOT / "lakehouse" / "evidence" / "local_run.json").read_text())
    assert ev["failed"] == 0 and ev["passed"] == len(ev["checks"]) >= 14
