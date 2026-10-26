"""The Streamlit control room renders and its main flows work (Streamlit AppTest, no browser)."""

from datetime import date
from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parent.parent / "app.py")
pytest.importorskip("streamlit.testing.v1")


@pytest.fixture
def app(monkeypatch):
    from streamlit.testing.v1 import AppTest

    for name in ("ANTHROPIC_API_KEY", "DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def test_control_room_renders_offline(app):
    assert [t.label.split(" ", 1)[1] for t in app.tabs][:7] == [
        "Control room", "Ask the risk agent", "Operations & data quality", "Metric explorer", "What-if lab",
        "Board pack", "How it works"]
    assert [r.value for r in app.sidebar.radio] == ["DuckDB (offline, free)", "Offline agent (free)"]
    page = " ".join(m.value for m in app.markdown)
    for name in ("Liquidity coverage ratio", "Overdue high-risk KYC reviews", "Risk Insights Agent"):
        assert name in page


def test_example_question_is_answered_and_audited(app):
    app.button(key="ex1").click().run()
    assert not app.exception
    state = app.session_state["p4"]
    assert state["chat"][-1]["q"] == "Why did reconciliation breaks go up?"
    assert state["audit"].count() == 1 and state["audit"].verify_chain()[0]


def test_board_pack_needs_a_named_reviewer(app):
    approve = next(b for b in app.button if "Approve" in b.label)
    assert approve.disabled
    reviewer = next(t for t in app.text_input if t.key.startswith("rev_"))
    reviewer.input("A. Analyst, Head of ERM").run()
    next(b for b in app.button if "Approve" in b.label).click().run()
    assert not app.exception
    approved = list(app.session_state["p4"]["approved"].values())
    assert approved and approved[0]["by"] == "A. Analyst, Head of ERM" and len(approved[0]["hash"]) == 64


def test_earlier_reporting_date_shows_an_earlier_picture(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.sidebar.select_slider[0].set_value(date(2026, 9, 15)).run()
    assert not at.exception, [e.message for e in at.exception]
    assert "Tuesday 15 September 2026" in " ".join(m.value for m in at.markdown)
