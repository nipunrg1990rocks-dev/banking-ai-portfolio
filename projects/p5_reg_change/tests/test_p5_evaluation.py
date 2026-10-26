from regradar.evaluation import format_report, run_evaluation


def test_evaluation_meets_bar():
    rep = run_evaluation()
    m = rep["metrics"]
    assert m["change_accuracy"] == 1.0
    assert m["parameter_delta_accuracy"] == 1.0
    assert m["gap_status_accuracy"] >= 0.85
    assert m["unsafe_misses"] == 0
    assert m["gap_detection_recall"] == 1.0
    assert rep["held_out"] is not None and rep["held_out_circulars"] == ["NPCA-FC-2026-22"]
    text = format_report(rep)
    assert "Held-out" in text and "Caveat" in text
