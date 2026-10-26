import pytest

from copilot.retriever import expand_query


def test_acronym_and_synonym_expansion():
    assert "politically exposed person" in expand_query("Who approves a PEP?")
    assert "disclose" in expand_query("Can I tell the customer?")
    assert expand_query("plain question") == "plain question"


@pytest.mark.parametrize("question, expected", [
    ("How often must high risk customers be reviewed?", "KYC-001::7"),
    ("What is the beneficial ownership threshold?", "KYC-001::4"),
    ("What is the deadline to file a SAR?", "AML-002::4"),
    ("What is the fuzzy matching threshold for sanctions?", "SAN-003::5"),
    ("What is the CET1 limit?", "RAS-004::3"),
    ("When must a Severity 1 incident be reported?", "ORM-005::3"),
    ("What contract terms are needed for critical vendors?", "TPR-008::4"),
])
def test_expected_section_is_top_ranked(shared_copilot, question, expected):
    results = shared_copilot.retriever.search(question, k=5)
    assert results[0].chunk.chunk_id == expected


def test_domain_filter_restricts_results(shared_copilot):
    results = shared_copilot.retriever.search("approval requirements", k=5, domains=["Sanctions"])
    assert results and all(r.chunk.domain == "Sanctions" for r in results)


def test_search_is_deterministic(shared_copilot):
    a = [r.chunk.chunk_id for r in shared_copilot.retriever.search("record retention period", k=5)]
    b = [r.chunk.chunk_id for r in shared_copilot.retriever.search("record retention period", k=5)]
    assert a == b
