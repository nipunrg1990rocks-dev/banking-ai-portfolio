"""Streamlit app smoke test (AppTest): dashboard renders, prompt -> deck -> follow-up works."""

from pathlib import Path

from p6_helpers import APAC_PROMPT
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_generate_and_follow_up():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    assert not at.exception
    at.text_area(key="prompt_text").set_value(APAC_PROMPT)
    next(b for b in at.button if b.label == "Generate deck").click()
    at.run()
    assert not at.exception and not at.error
    at.text_input(key="fu_text").set_value("change APAC to EMEA and add high-severity exceptions")
    next(b for b in at.button if b.label == "Apply follow-up").click()
    at.run()
    assert not at.exception and not at.error
    assert any("D-0001 v2" in s.value for s in at.subheader)


def test_app_workforce_dashboard():
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.radio(key="domain_key").set_value("workforce")
    at.run()
    assert not at.exception
    assert any(m.label == "People on contract" for m in at.metric)
