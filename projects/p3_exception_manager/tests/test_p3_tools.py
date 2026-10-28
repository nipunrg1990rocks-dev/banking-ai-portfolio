import pytest

from opsagent.data import build_bank
from opsagent.tools import ACTION_TOOLS, READ_TOOLS, Toolbox, ToolError, name_similarity


@pytest.fixture
def tb():
    return Toolbox(build_bank())


def test_tool_registry_is_split_into_read_and_action():
    assert len(READ_TOOLS) >= 14 and len(ACTION_TOOLS) >= 9
    assert not set(READ_TOOLS) & set(ACTION_TOOLS)


def test_read_and_action_tools_cannot_be_swapped(tb):
    with pytest.raises(ToolError):
        tb.call("release_payment", payment_id="x")
    with pytest.raises(ToolError):
        tb.act("get_payment", idempotency_key="k", payment_id="x")


def test_missing_record_is_logged_not_raised(tb):
    out = tb.call("get_payment", payment_id="PMT-NOPE")
    assert "error" in out
    assert tb.calls[-1]["ok"] is False


def test_sanctions_screening_separates_strong_and_weak(tb):
    strong = tb.call("screen_name", name="Rasul Tadeev", country="Karvonia", dob="1974-11-02")["hits"][0]
    weak = tb.call("screen_name", name="Victor Malenkoff", country="GB", dob="1988-07-21")["hits"][0]
    assert strong["match_strength"] >= 0.85 > weak["match_strength"]
    assert weak["dob_check"] == "mismatch"
    assert tb.call("screen_name", name="Kestrel Foods Ltd")["hits"] == []


def test_name_similarity_handles_transliteration():
    assert name_similarity("Viktor Malenkov", "Victor Malenkoff") >= 0.6  # the screening threshold
    assert name_similarity("Orion Maritime Holdings Ltd", "Orion Maritime Holdings") == 1.0


def test_actions_are_idempotent(tb):
    pid = next(p for p, v in tb.bank.payments.items() if v["status"] == "HELD")
    first = tb.act("cancel_payment", "EXC-1:cancel", payment_id=pid, reason="dup")
    second = tb.act("cancel_payment", "EXC-1:cancel", payment_id=pid, reason="dup")
    assert first["status"] == "CANCELLED" and second["idempotent_replay"] is True
    assert sum(1 for c in tb.calls if c["tool"] == "cancel_payment") == 1


def test_clearing_suspense_posts_to_the_account(tb):
    sid, item = next((k, v) for k, v in tb.bank.suspense.items())
    acct = next(iter(tb.bank.accounts))
    before = tb.bank.accounts[acct]["balance"]
    out = tb.act("clear_suspense_to_account", "k", suspense_id=sid, account_id=acct)
    assert out["status"] == "CLEARED"
    assert tb.bank.accounts[acct]["balance"] == pytest.approx(before + item["amount"])
    again = tb.act("clear_suspense_to_account", "k2", suspense_id=sid, account_id=acct)
    assert "error" in again  # a second, different action cannot clear it twice
