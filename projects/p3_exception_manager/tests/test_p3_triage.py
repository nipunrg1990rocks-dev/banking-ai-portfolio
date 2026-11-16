from opsagent.data import build_bank
from opsagent.triage import CATEGORIES, TriageModel


def test_triage_on_seed_queue():
    model = TriageModel()
    bank = build_bank()
    correct = 0
    for exc in bank.exceptions:
        r = model.predict(exc)
        got = "OTHER" if r.needs_human_triage else r.category
        correct += got == (exc["expected"]["category"] or "OTHER")
    assert correct / len(bank.exceptions) >= 0.9


def test_vague_ticket_goes_to_human_triage():
    r = TriageModel().predict({"source_system": "Branch Ticket", "reason_code": None, "title": "",
                               "description": "customer says something is wrong, please look"})
    assert r.needs_human_triage


def test_prediction_is_explained():
    r = TriageModel().predict({"source_system": "Screening", "reason_code": "SANCTIONS_HIT", "title": "",
                               "description": "potential match against the watchlist, payment held"})
    assert r.category == "SANCTIONS_HIT"
    assert r.top_terms and all(isinstance(t, str) for t in r.top_terms)
    assert set(dict(r.top3)) <= set(CATEGORIES)
