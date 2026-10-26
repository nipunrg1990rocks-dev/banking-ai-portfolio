import pytest

from copilot.documents import DocumentError, chunk_document, load_documents, parse_document
from copilot.engine import DEFAULT_POLICY_DIR

VALID = """---
doc_id: TST-001
title: Test Policy
version: 1.0
domain: Testing
---

# Test Policy

## 1. Scope

Applies to all test cases.

## 2. Rule

Every test must have an assertion.
"""


def test_all_bundled_policies_load_with_unique_ids():
    docs = load_documents(DEFAULT_POLICY_DIR)
    assert len(docs) == 8
    assert len({d.doc_id for d in docs}) == 8
    assert all(d.metadata.get("version") and d.metadata.get("owner") for d in docs)


def test_every_policy_is_marked_synthetic():
    for d in load_documents(DEFAULT_POLICY_DIR):
        assert "synthetic" in d.metadata["classification"].lower()
        assert "fictional" in d.body.lower()


def test_parse_valid_document_and_chunk_by_section():
    doc = parse_document(VALID, "t.md")
    chunks = chunk_document(doc)
    assert [c.chunk_id for c in chunks] == ["TST-001::1", "TST-001::2"]
    assert chunks[1].section == "2. Rule"
    assert chunks[1].citation_label == "TST-001 §2. Rule"


@pytest.mark.parametrize("raw, message", [
    ("# no header\n## 1. A\ntext", "front-matter"),
    ("---\ntitle: X\n---\n## 1. A\ntext", "doc_id"),
    ("---\ndoc_id: X\ntitle: Y\n---\nno sections here", "section"),
])
def test_invalid_documents_are_rejected(raw, message):
    with pytest.raises(DocumentError, match=message):
        parse_document(raw, "bad.md")


def test_long_sections_are_split_with_overlap():
    long_body = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(10))
    doc = parse_document(VALID.replace("Every test must have an assertion.", long_body), "t.md")
    pieces = [c for c in chunk_document(doc, max_chars=800) if c.chunk_id.startswith("TST-001::2")]
    assert len(pieces) > 1
    assert all(len(p.text) <= 1200 for p in pieces)
    # overlap: last paragraph of one piece starts the next
    assert pieces[0].text.split("Paragraph")[-1] in pieces[1].text
