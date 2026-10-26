import pytest

from regradar.params import (
    context_similarity,
    direction,
    extract_quantities,
    meets,
    pair_quantities,
    phrase,
    replace_quantity_text,
)


def one(sentence):
    qs = extract_quantities(sentence)
    assert len(qs) == 1, qs
    return qs[0]


@pytest.mark.parametrize("sentence, text, base, kind", [
    ("Firms must file a report no later than 15 calendar days after it.", "15 calendar days", 15, "deadline"),
    ("Firms must triage every alert within 5 business days of generation.", "5 business days", 7, "deadline"),
    ("Firms must notify the Authority within 4 hours of identification.", "4 hours", 4 / 24, "deadline"),
    ("Firms must retain records for at least five years from filing.", "five years", 5 * 365, "min_duration"),
    ("Firms must refresh due diligence at least every 12 months.", "12 months", 12 * 30.4, "frequency"),
    ("Scenario thresholds must be tuned at least annually.", "annually", 365, "frequency"),
    ("Firms must re-screen the customer base at least monthly.", "monthly", 30.4, "frequency"),
    ("The base must be fully re-screened at least once per month.", "once per month", 30.4, "frequency"),
    ("Escalate to the AML Officer on the same business day.", "same business day", 1, "deadline"),
])
def test_time_quantities(sentence, text, base, kind):
    q = one(sentence)
    assert q.text == text and q.dimension == "time" and q.kind == kind
    assert q.base == pytest.approx(base)


def test_percent_and_money():
    q = one("Identify every individual who owns 10 percent or more of the entity.")
    assert (q.dimension, q.base, q.kind) == ("percent", 10, "threshold")
    m = one("Firms must record operational losses of 5,000 US dollars or more.")
    assert (m.dimension, m.base) == ("money", 5000)
    big = extract_quantities("Severity 1: estimated loss above 1 million US dollars.")
    assert big[0].base == 1_000_000


def test_strictness_depends_on_kind():
    deadline_new, deadline_old = one("within 15 days"), one("within 30 days")
    assert direction(deadline_old, deadline_new) == "tightened"
    assert not meets(deadline_old, deadline_new) and meets(deadline_new, deadline_old)
    keep_new, keep_old = one("retain for at least seven years"), one("retain for at least five years")
    assert direction(keep_old, keep_new) == "tightened"
    assert not meets(keep_old, keep_new)
    assert direction(deadline_old, deadline_old) == "unchanged"


def test_context_stops_at_neighbouring_numbers():
    old = extract_quantities("Refresh high risk customers every 12 months, medium risk customers every 36 months "
                             "and low risk customers every 60 months.")
    new = extract_quantities("Refresh high risk customers every 12 months, medium risk customers every 24 months "
                             "and low risk customers every 36 months.")
    assert "high" not in old[1].context and "medium" in old[1].context
    pairs = {(a.text, b.text): direction(a, b) for a, b, _ in pair_quantities(old, new)}
    assert pairs == {("12 months", "12 months"): "unchanged", ("36 months", "24 months"): "tightened",
                     ("60 months", "36 months"): "tightened"}


def test_context_similarity_and_phrase():
    a = one("A SAR must be filed no later than 30 calendar days after determination.")
    b = one("Firms must file a SAR no later than 15 calendar days after determination.")
    assert context_similarity(a, b) > 0.4
    assert phrase(one("test them at least every two years")) == "every two years"
    assert phrase(b) == "within 15 calendar days"


def test_replace_quantity_text():
    s = "A SAR must be filed no later than 30 calendar days after determination."
    q = one(s)
    assert replace_quantity_text(s, q, "15 calendar days") == s.replace("30", "15")


def test_no_false_quantities():
    assert extract_quantities("Structures with more than three layers must be documented.") == []
    assert extract_quantities("Firms must tell customers when they are interacting with an AI system.") == []
