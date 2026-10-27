from regradar.corpus import get_pair
from regradar.diff import AMENDED, NEW, REMOVED, UNCHANGED, compare, redline_html


def by_pid(changes):
    return {c.pid: c for c in changes}


def test_change_types_and_parameter_moves():
    ch = by_pid(compare(get_pair("NPCA-FC-2026-14")))
    assert ch["NPCA-FC-2026-14 §1.1"].change_type == UNCHANGED
    sar = ch["NPCA-FC-2026-14 §4.1"]
    assert sar.change_type == AMENDED and sar.materiality == "High"
    assert [(p.old.text, p.new.text, p.direction) for p in sar.parameter_changes] == [
        ("30 calendar days", "15 calendar days", "tightened")]
    assert ch["NPCA-FC-2026-14 §5.1"].change_type == NEW
    assert ch["NPCA-FC-2023-06 §4.4"].change_type == REMOVED
    scope = ch["NPCA-FC-2026-14 §1.2"]
    assert {"instant", "crypto-asset"} <= set(scope.inserted_words)


def test_new_circular_is_all_new():
    changes = compare(get_pair("NPCA-AI-2026-03"))
    assert changes and all(c.change_type == NEW for c in changes)


def test_added_parameter_is_reported():
    ch = by_pid(compare(get_pair("NPCA-OR-2026-08")))
    exit_plans = ch["NPCA-OR-2026-08 §3.2"]
    assert exit_plans.change_type == AMENDED
    assert [q.text for q in exit_plans.new_parameters] == ["two years"]
    assert "every two years" in exit_plans.headline


def test_redline_marks_and_escapes():
    ch = by_pid(compare(get_pair("NPCA-FC-2026-14")))
    html = redline_html(ch["NPCA-FC-2026-14 §4.1"])
    assert "<del>30</del>" in html and "<ins>15</ins>" in html
    assert "<script>" not in redline_html(ch["NPCA-FC-2026-14 §5.1"])
