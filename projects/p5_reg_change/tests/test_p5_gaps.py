from regradar.gaps import CONFLICT, COVERED, MISSING, PARTIAL, recipient_gap


def find(a, pid):
    return next(f for f in a.findings if f.provision.pid == pid)


def test_sar_deadline_conflict_with_evidence(fc14):
    f = find(fc14, "NPCA-FC-2026-14 §4.1")
    assert f.status == CONFLICT and f.policy_refs == ["AML-002 §4"] and f.confidence == "High"
    check = f.checks[0]
    assert (check.required.text, check.match.policy.text, check.ok) == ("15 calendar days", "30 calendar days", False)
    assert "weaker" in f.rationale[0]
    assert {c.control_id: c.action for c in f.controls}["CTL-AML-05"] == "Reconfigure"


def test_cross_policy_mapping_and_coverage(fc14):
    validation = find(fc14, "NPCA-FC-2026-14 §6.1")
    assert validation.status == COVERED and validation.policy_refs == ["MRM-006 §4"]
    scope = find(fc14, "NPCA-FC-2026-14 §1.2")
    assert scope.status == PARTIAL and {"instant", "crypto"} & set(scope.missing_terms)
    assert find(fc14, "NPCA-FC-2026-14 §5.1").status == MISSING


def test_same_weak_figure_in_two_policies(all_assessments):
    f = find(all_assessments["NPCA-AI-2026-03"], "NPCA-AI-2026-03 §4.2")
    assert f.status == CONFLICT and set(f.policy_refs) == {"MRM-006 §6", "REC-007 §2"}


def test_multi_figure_rule_already_met(all_assessments):
    f = find(all_assessments["NPCA-FC-2026-19"], "NPCA-FC-2026-19 §3.1")
    assert f.status == COVERED and all(c.ok for c in f.checks)


def test_reporting_audience_must_match(all_assessments):
    f = find(all_assessments["NPCA-OR-2026-08"], "NPCA-OR-2026-08 §1.1")
    assert f.status == PARTIAL and "not to the regulator" in f.rationale[-1]
    assert recipient_gap("Firms must notify the Authority within 4 hours.", "Report to the CRO within 2 hours.") \
        == "the regulator"
    assert recipient_gap("Firms must keep records.", "Records are kept.") is None


def test_risk_scoring_and_urgency(all_assessments):
    kyc = find(all_assessments["NPCA-FC-2026-19"], "NPCA-FC-2026-19 §1.1")
    assert kyc.risk.days_to_effective == 67
    assert kyc.rating == "Critical" and any("Raised one level" in x for x in kyc.risk.factors)
    covered = find(all_assessments["NPCA-FC-2026-19"], "NPCA-FC-2026-19 §4.1")
    assert covered.rating == "Low"
    for a in all_assessments.values():
        for f in a.gaps:
            assert 1 <= f.risk.score <= 25 and f.risk.factors[-1].startswith("Score")


def test_every_finding_cites_its_sources(all_assessments):
    for a in all_assessments.values():
        for f in a.findings:
            assert f.provision.pid.startswith(("NPCA-",))
            if f.status in (CONFLICT, PARTIAL, COVERED):
                assert f.policy_refs, f.fid
