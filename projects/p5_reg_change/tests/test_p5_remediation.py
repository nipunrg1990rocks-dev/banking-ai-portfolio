from datetime import date

from regradar.remediation import adapt_to_bank, due_date


def test_conflict_produces_exact_policy_redline(fc14):
    redlines = [x for x in fc14.actions if x.kind == "Policy redline"]
    sar = next(x for x in redlines if "15 calendar days" in x.title)
    assert "no later than 30 calendar days" in sar.before
    assert sar.after == sar.before.replace("30 calendar days", "15 calendar days")
    assert sar.citations == ["NPCA-FC-2026-14 §4.1", "AML-002 §4"]
    assert "<del>30</del>" in sar.redline_html and "<ins>15</ins>" in sar.redline_html
    freq = next(x for x in redlines if "six months" in x.title)
    assert "at least every six months" in freq.after


def test_control_reconfiguration_changes_the_hard_coded_figure(fc14):
    ctl = next(x for x in fc14.actions if x.kind == "Control reconfiguration" and "CTL-AML-05" in x.title)
    assert "breach at day 30" in ctl.before and "breach at day 15" in ctl.after


def test_missing_requirement_gets_new_clause_and_control(fc14):
    kinds = {x.kind for x in fc14.actions if x.citations[0] == "NPCA-FC-2026-14 §5.1"}
    assert kinds == {"New policy clause", "New control"}


def test_every_action_is_owned_dated_and_cited(all_assessments):
    for a in all_assessments.values():
        for x in a.actions:
            assert x.owner and x.due and x.citations and x.decision == "Proposed"
            assert x.due < a.circular.effective_date


def test_adapt_and_due_date():
    assert adapt_to_bank("Firms must keep records.") == "Ledgerline Bank must keep records."
    assert adapt_to_bank("Firms that onboard customers remotely must verify identity.").startswith(
        "Where Ledgerline Bank onboards customers remotely, it must")

    class C:
        effective_date = "2027-01-01"

    assert due_date(C(), date(2026, 10, 26)) == "2026-12-02"
    C.effective_date = "2026-11-01"
    assert due_date(C(), date(2026, 10, 26)) == "2026-11-09"
