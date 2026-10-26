from copilot.evaluation import format_report, run_evaluation

# Quality gate: a change that lowers these numbers fails CI.
MIN_RECALL_AT_3 = 0.95
MIN_FACT_ACCURACY = 0.90
MIN_ROUTING_ACCURACY = 1.0


def test_golden_set_meets_quality_gate():
    report = run_evaluation()
    m = report["metrics"]
    assert m["cases"] >= 35
    assert m["recall_at_3"] >= MIN_RECALL_AT_3, format_report(report)
    assert m["answer_fact_accuracy"] >= MIN_FACT_ACCURACY, format_report(report)
    assert m["routing_accuracy"] >= MIN_ROUTING_ACCURACY, format_report(report)
    assert m["citation_validity"] == 1.0
