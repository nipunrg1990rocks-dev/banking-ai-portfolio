from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def test_app_renders_and_runs_the_crew():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception
    assert any("RegRadar" in m.value for m in at.markdown)
    run = next(b for b in at.button if b.label.startswith("▶ Run the agent crew"))
    run.click().run()
    assert not at.exception
    assert any("finished" in s.value for s in at.success)
