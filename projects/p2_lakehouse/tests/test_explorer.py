"""The app's data layer: headline numbers, reconciliation and the locked-down SQL console."""

import pytest

from explorer import EXAMPLE_QUERIES, QueryError, Snapshot, run_query, sql_console


@pytest.fixture(scope="module")
def snap():
    return Snapshot.load()


@pytest.fixture(scope="module")
def con():
    return sql_console()


def test_headline_numbers_are_consistent(snap):
    h = snap.headline()
    assert h["silver_transactions"] == len(snap.transactions)
    assert h["alerts"] == 8
    # Every control on the two good batches passed; exactly one tripped, blocking the truncated incident batch.
    assert h["controls_passed"] == h["controls"] - 1 > 0
    assert h["controls_tripped"] == 1


def test_every_batch_balances(snap):
    for r in snap.journey().itertuples(index=False):
        assert r[1] == sum(r[2:])


def test_alert_drilldown_returns_the_flagged_transactions(snap):
    for alert in snap.alerts.itertuples():
        txns = snap.alert_transactions(alert.alert_id)
        assert len(txns) == alert.txn_count
        assert set(txns.customer_id) == {alert.customer_id}


@pytest.mark.parametrize("name", list(EXAMPLE_QUERIES))
def test_example_queries_run(con, name):
    assert len(run_query(con, EXAMPLE_QUERIES[name])) > 0


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "COPY transactions TO '/tmp/out.csv'",
    "DROP TABLE transactions",
    "SELECT 1; DROP TABLE transactions",
    "SET enable_external_access = true",
    "INSTALL httpfs",
])
def test_console_blocks_writes_and_file_access(con, sql):
    with pytest.raises(QueryError):
        run_query(con, sql)
    assert run_query(con, "SELECT COUNT(*) AS n FROM transactions").n[0] > 0
