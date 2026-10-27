import pytest

from regradar.corpus import (
    CircularError,
    load_circular_pairs,
    load_controls,
    modality,
    parse_circular,
    policy_sections,
    section_by_ref,
)


def test_library_loads_five_circulars_with_versions():
    pairs = {p.key: p for p in load_circular_pairs()}
    assert set(pairs) == {"NPCA-AI-2026-03", "NPCA-FC-2026-14", "NPCA-FC-2026-19", "NPCA-FC-2026-22",
                          "NPCA-OR-2026-08"}
    assert pairs["NPCA-AI-2026-03"].previous is None
    assert pairs["NPCA-FC-2026-14"].previous.circular_id == "NPCA-FC-2023-06"
    assert pairs["NPCA-FC-2026-14"].current.effective_date == "2027-03-01"


def test_provisions_have_citable_ids_and_skip_preamble():
    circ = {p.key: p for p in load_circular_pairs()}["NPCA-FC-2026-14"].current
    ids = [p.pid for p in circ.provisions]
    assert "NPCA-FC-2026-14 §4.1" in ids and len(ids) == len(set(ids))
    assert not any("synthetic regulatory circular" in p.text for p in circ.provisions)


@pytest.mark.parametrize("sentence, expected", [
    ("Firms must file a report.", "obligation"),
    ("AI systems must not make final decisions.", "prohibition"),
    ("Firms with fewer than 50 employees may file reports on paper.", "permission"),
    ("These rules apply to all firms.", "information"),
])
def test_modality(sentence, expected):
    assert modality(sentence) == expected


def test_plain_text_is_accepted_and_bad_input_rejected():
    c = parse_circular("Firms must do A within 5 days.\n\nFirms must do B annually.", default_id="X-1")
    assert [p.pid for p in c.provisions] == ["X-1 §1.1", "X-1 §2.1"]
    with pytest.raises(CircularError):
        parse_circular("   ")
    with pytest.raises(CircularError):
        parse_circular("x" * 70_000)


def test_policies_come_from_project_1_and_controls_point_at_real_sections():
    sections = policy_sections()
    assert len({s.doc_id for s in sections}) == 8
    assert section_by_ref("AML-002 §4").heading == "Suspicious Activity Report Filing"
    controls = load_controls()
    assert len(controls) >= 25
    missing = [r for r in controls["policy_ref"] if section_by_ref(r) is None]
    assert missing == []
