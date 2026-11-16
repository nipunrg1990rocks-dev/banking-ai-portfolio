"""Invariants on the processed baseline and the oracle comparison."""

import pytest
from sqlalchemy import text

from cf360.db import SessionLocal

pytestmark = pytest.mark.db


def q(sql):
    with SessionLocal() as s:
        return s.execute(text(sql)).all()


def test_baseline_books_all_500(seeded):
    assert seeded["booked"] == 500 and not seeded["rejected"]
    assert q("select count(*) from trades where sandbox_id is null")[0][0] == 500


def test_every_posting_balances_per_key(seeded):
    assert q("select source_event_id, key from ledger_entries group by 1, 2 having sum(amount) <> 0") == []


def test_ledger_event_legs_unique(seeded):
    assert q("select source_event_id, leg_seq from ledger_entries group by 1, 2 having count(*) > 1") == []


def test_allocation_quantity_conservation(seeded):
    assert q("""select t.trade_id from trades t join allocations a on a.trade_id = t.trade_id and a.status = 'ACTIVE'
                group by t.trade_id, t.quantity having sum(a.alloc_qty) <> t.quantity""") == []


def test_settled_trades_have_only_settled_instructions(seeded):
    assert q("""select t.trade_id from trades t join settlement_instructions i on i.trade_id = t.trade_id
                where t.status = 'SETTLED' and i.status not in ('SETTLED', 'CANCELLED')""") == []


def test_every_trade_status_is_on_its_state_machine(seeded):
    from cf360 import lifecycle
    for ptype, status in q("select distinct product_type, status from trades"):
        assert status in lifecycle.graph(ptype), (ptype, status)


def test_reconciliation_finds_exactly_the_injected_breaks(seeded):
    rows = dict(q("select recon_type, count(*) from recon_breaks group by 1"))
    assert rows == {"POSITION": 5, "CASH": 3}


def test_platform_matches_independent_oracle(seeded):
    from cf360.config import settings
    from testkit.compare_oracle import compare
    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    res = compare(dsn)
    assert res["checks"] > 5000
    assert res["differences"] == 0, res["details"][:10]


def test_audit_trail_is_append_only(seeded):
    with SessionLocal() as s:
        with pytest.raises(Exception, match="append-only"):
            s.execute(text("delete from audit_events where audit_id = (select min(audit_id) from audit_events)"))
        s.rollback()
        with pytest.raises(Exception, match="append-only"):
            s.execute(text("update audit_events set actor = 'x' where audit_id = (select min(audit_id) from audit_events)"))
        s.rollback()
