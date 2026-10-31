"""The SQL path: only allowlisted tables/columns, parameters always bound."""

import pytest

from deckpilot.domains.ops_risk_schema import ALLOWED_COLUMNS


def test_unknown_table_or_column_is_refused(ops_env):
    _, ds, qb = ops_env
    with pytest.raises(PermissionError):
        qb.select(ds, "sqlite_master", ["name"])
    with pytest.raises(PermissionError):
        qb.select(ds, "fact", ["exceptions; DROP TABLE fact"])
    with pytest.raises(PermissionError):
        qb.select(ds, "fact", ["exceptions"], {"1=1 OR region": ["x"]})


def test_filter_values_are_bound_parameters(ops_env):
    _, ds, qb = ops_env
    df, rec = qb.select(ds, "fact", ["region"], {"region": ["APAC' OR '1'='1"]})
    assert len(df) == 0
    assert "OR '1'='1" not in rec.sql and rec.params == ["APAC' OR '1'='1"]


def test_fingerprint_is_stable_and_versioned(ops_env):
    _, ds, qb = ops_env
    _, a = qb.select(ds, "fact", ALLOWED_COLUMNS, {"region": ["APAC"]}, ["2026-07"])
    _, b = qb.select(ds, "fact", ALLOWED_COLUMNS, {"region": ["APAC"]}, ["2026-07"])
    _, c = qb.select(ds, "fact", ALLOWED_COLUMNS, {"region": ["EMEA"]}, ["2026-07"])
    assert a.fingerprint == b.fingerprint != c.fingerprint
    assert a.dataset_version == ds.version
