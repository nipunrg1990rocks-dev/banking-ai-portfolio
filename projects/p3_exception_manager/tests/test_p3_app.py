"""Headless smoke test of the Streamlit app with Streamlit's own AppTest runner."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def app():
    at = AppTest.from_file(APP, default_timeout=90).run()
    assert not at.exception
    return at


def _button(at, text):
    return next(b for b in at.button if text in b.label)


def test_app_runs_queue_and_shows_results(app):
    _button(app, "Run the agents").click().run()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["Processed by agents"] == "42"
    assert metrics["Straight-through"] == "5"


def test_unauthorised_approval_is_refused_in_the_ui(app):
    _button(app, "Run the agents").click().run()
    # The workbench opens the top pending item for the default persona (an Ops Analyst).
    sanctions = next(o for o in app.selectbox(key="wb_select").options if "Sanctions" in o)
    app.selectbox(key="wb_select").select(sanctions.split(" · ")[0]).run()
    _button(app, "Approve").click().run()
    assert not app.exception
    assert any("Refused by the control" in e.value for e in app.error)


def test_injection_example_is_flagged(app):
    _button(app, "prompt-injection example").click().run()
    _button(app, "Submit and run").click().run()
    assert not app.exception
    assert app.session_state["mgr"].case("EXC-NEW001").state["guardrails"]["flags"] == ["PROMPT_INJECTION"]
