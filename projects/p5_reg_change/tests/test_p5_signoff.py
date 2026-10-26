import pytest

from portfolio_core.audit import AuditLog
from regradar.report import build_pack
from regradar.signoff import SignOffError, Workflow


def test_signoff_controls(fc14):
    log = AuditLog()
    wf = Workflow(fc14, log)
    first = fc14.actions[0]
    with pytest.raises(SignOffError, match="name"):
        wf.decide(first.aid, "Accepted", "")
    with pytest.raises(SignOffError, match="comment"):
        wf.decide(first.aid, "Rejected", "Asha")
    wf.decide(first.aid, "Edited", "Asha", "tighter wording", "Ledgerline Bank must monitor instant payments.")
    assert first.after == "Ledgerline Bank must monitor instant payments." and first.decided_by == "Asha"
    with pytest.raises(SignOffError, match="blocked"):
        wf.sign_off("Dan")
    for x in fc14.actions:
        if x.decision == "Proposed":
            wf.decide(x.aid, "Accepted", "Asha")
    for f in fc14.needs_review():
        with pytest.raises(SignOffError):
            wf.review_flag(f.fid, "Asha", " ")
        wf.review_flag(f.fid, "Asha", "Checked.")
    assert wf.blockers() == []
    with pytest.raises(SignOffError, match="Four-eyes"):
        wf.sign_off("Asha")
    wf.sign_off("Dan")
    with pytest.raises(SignOffError, match="already signed off"):
        wf.decide(first.aid, "Accepted", "Asha")
    events = [e.event_type for e in log.events(correlation_id=fc14.run_id)]
    assert events[0] == "assessment_signed_off" and events[-1] == "assessment_created"
    assert log.verify_chain() == (True, None)
    pack = build_pack(wf)
    assert "SIGNED OFF by Dan" in pack and "| A-001 |" in pack and "hash chain intact" in pack
