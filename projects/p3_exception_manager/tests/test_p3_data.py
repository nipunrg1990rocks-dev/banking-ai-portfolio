from opsagent.data import build_bank, corrupt_iban, iban_is_valid, make_iban, public_view


def test_bank_is_deterministic():
    a, b = build_bank(), build_bank()
    assert [e["exception_id"] for e in a.exceptions] == [e["exception_id"] for e in b.exceptions]
    assert a.payments == b.payments


def test_queue_covers_every_category_and_route():
    bank = build_bank()
    cats = {e["expected"]["category"] for e in bank.exceptions}
    assert cats == {"PAYMENT_REPAIR", "NOSTRO_BREAK", "DUPLICATE_PAYMENT", "SANCTIONS_HIT", "KYC_EXPIRED_BLOCK",
                    "SETTLEMENT_FAIL", "GL_SUSPENSE", "LIMIT_BREACH", None}
    assert {e["expected"]["route"] for e in bank.exceptions} == {"AUTO", "HUMAN", "TRIAGE"}
    assert len(bank.exceptions) == 42


def test_iban_checksum():
    iban = make_iban("GB", "LDGR20041540000000")
    assert iban_is_valid(iban)
    assert not iban_is_valid(corrupt_iban(iban))
    assert not iban_is_valid("GB00LDGR00000000000000")
    assert not iban_is_valid("not an iban")


def test_every_reference_resolves():
    bank = build_bank()
    tables = {"payment_id": bank.payments, "customer_id": bank.customers, "nostro_item_id": bank.nostro_ledger,
              "trade_id": bank.trades, "suspense_id": bank.suspense, "account_id": bank.accounts}
    for exc in bank.exceptions:
        for key, value in exc["refs"].items():
            assert value in tables[key], (exc["exception_id"], key)


def test_agent_view_hides_acceptance_criteria():
    exc = build_bank().exceptions[0]
    assert "expected" not in public_view(exc)
    assert "expected" in exc
