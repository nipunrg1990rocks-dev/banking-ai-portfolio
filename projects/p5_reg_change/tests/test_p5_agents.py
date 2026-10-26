import json

from regradar.agents import CREW, run_assessment
from regradar.corpus import get_pair


def test_crew_runs_in_order_and_logs_every_agent(fc14):
    starts = [e.agent for e in fc14.events if e.kind == "start"]
    assert starts == [a.key for a in CREW]
    assert fc14.llm_label is None and fc14.summary_source == "template"
    assert "NPCA-FC-2026-14" in fc14.summary and "drafts until signed off" in fc14.summary
    ratings = [f.rating for f in fc14.gaps]
    order = ["Critical", "High", "Medium", "Low"]
    assert ratings == sorted(ratings, key=order.index)


def test_events_stream_to_callback():
    seen = []
    run_assessment(get_pair("NPCA-OR-2026-08"), on_event=seen.append)
    assert seen and seen[0].agent == "intake" and seen[-1].agent == "reporter"


def test_model_disagreement_goes_to_a_human(fake_llm):
    def reviewer(user):
        if "The rules engine says: " not in user:
            return "Summary: 3 policies are weaker than the rule. Drafts await sign-off."
        # Disagree only on the SAR deadline finding.
        if "NPCA-FC-2026-14 §4.1" in user.split("\n")[0]:
            return json.dumps({"status": "COVERED", "policy_ref": None, "rationale": "Looks fine."})
        status = user.split("The rules engine says: ")[1].split(" ")[0]
        return json.dumps({"status": status, "policy_ref": None, "rationale": "Agree."})

    llm = fake_llm(replies=[reviewer] * 20)
    a = run_assessment(get_pair("NPCA-FC-2026-14"), llm=llm)
    sar = next(f for f in a.findings if f.provision.pid == "NPCA-FC-2026-14 §4.1")
    assert sar.needs_review and any("model's verdict is COVERED" in r for r in sar.review_reasons)
    assert sar.status == "CONFLICT"  # the model never overrides the rules engine
    assert a.summary_source == "Fake · test-model" and a.llm_label == "Fake · test-model"


def test_ungrounded_model_summary_is_rejected(fake_llm):
    def reply(user):
        return '{"status": "CONFLICT"}' if "The rules engine says: " in user else "We found 999 gaps."

    llm = fake_llm(replies=[reply] * 20)
    a = run_assessment(get_pair("NPCA-FC-2026-14"), llm=llm)
    assert a.summary_source == "template"
    assert any("Model summary rejected" in e.text for e in a.events)


def test_model_outage_keeps_rules_verdicts(fake_llm):
    a = run_assessment(get_pair("NPCA-FC-2026-14"), llm=fake_llm(fail=True))
    assert a.summary_source == "template"
    assert all(f.status for f in a.findings)
    assert any("model review failed" in e.text for e in a.events)
