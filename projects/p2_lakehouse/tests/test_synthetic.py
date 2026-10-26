"""Source data generator: reproducible, internally consistent, and honest about what it plants."""

import hashlib
import json

from lakehouse import synthetic


def _digest(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_generation_is_reproducible(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    oa, ob = synthetic.generate(a), synthetic.generate(b)
    assert _digest(a) == _digest(b)
    assert oa.to_dict() == ob.to_dict()


def test_manifest_counts_match_files(tmp_path):
    synthetic.generate(tmp_path)
    for batch in synthetic.BATCHES:
        folder = tmp_path / f"batch={batch}"
        manifest = json.loads((folder / "manifest.json").read_text())
        lines = (folder / "transactions.jsonl").read_text().splitlines()
        assert manifest["record_counts"]["transactions"] == len(lines)
        assert manifest["record_counts"]["customers"] == len((folder / "customers.csv").read_text().splitlines()) - 1
        assert manifest["record_counts"]["accounts"] == len((folder / "accounts.csv").read_text().splitlines()) - 1


def test_oracle_plants_every_scenario(tmp_path):
    oracle = synthetic.generate(tmp_path)
    assert {k: len(v) for k, v in oracle.aml_scenarios.items()} == {
        "STRUCTURING": 3, "HIGH_RISK_JURISDICTION": 3, "RAPID_MOVEMENT": 1}
    assert len(oracle.scd2_changed_customers) == 40
    assert len(oracle.cross_batch_resends) == 10
    assert sum(len(v) for v in oracle.txn_defects.values()) == 22


def test_only_fictional_jurisdictions_are_high_risk():
    for code, name, tier in synthetic.COUNTRY_RISK:
        if tier != "STANDARD":
            assert code.startswith("X") and "fictional" in name
