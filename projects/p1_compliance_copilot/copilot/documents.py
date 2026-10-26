"""Policy document loading, parsing and chunking.

Policies are Markdown files with a small front-matter header (doc_id, title,
version, owner, effective date). Each document is split on its numbered
``##`` section headings so every chunk maps to a citable policy section.
Sections longer than ``max_chars`` are split further on paragraph boundaries
with one paragraph of overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_FIELDS = ("doc_id", "title")
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class DocumentError(ValueError):
    pass


@dataclass
class PolicyDocument:
    doc_id: str
    title: str
    body: str
    metadata: dict[str, str] = field(default_factory=dict)
    source: str = ""

    @property
    def version(self) -> str:
        return self.metadata.get("version", "n/a")

    @property
    def domain(self) -> str:
        return self.metadata.get("domain", "General")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    section: str
    text: str
    version: str
    domain: str

    @property
    def citation_label(self) -> str:
        return f"{self.doc_id} §{self.section}"

    @property
    def search_text(self) -> str:
        # Title and section heading are included so they influence retrieval.
        return f"{self.doc_title}. {self.section}. {self.text}"


def parse_document(raw: str, source: str = "") -> PolicyDocument:
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        raise DocumentError(f"{source or 'document'}: missing '---' front-matter header")
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    missing = [f for f in REQUIRED_FIELDS if not metadata.get(f)]
    if missing:
        raise DocumentError(f"{source or 'document'}: missing required field(s): {', '.join(missing)}")
    body = raw[match.end():].strip()
    if not _SECTION_RE.search(body):
        raise DocumentError(f"{source or 'document'}: no '## ' section headings found")
    return PolicyDocument(metadata["doc_id"], metadata["title"], body, metadata, source)


def load_documents(directory: str | Path) -> list[PolicyDocument]:
    docs = [parse_document(p.read_text(encoding="utf-8"), p.name)
            for p in sorted(Path(directory).glob("*.md"))]
    ids = [d.doc_id for d in docs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise DocumentError(f"duplicate doc_id(s): {', '.join(sorted(dupes))}")
    return docs


def _split_long(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) <= 1:
        # Fall back to sentence-level packing for one long paragraph.
        paragraphs = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    current: list[str] = []
    for para in paragraphs:
        if current and len(" ".join(current)) + len(para) > max_chars:
            pieces.append(" ".join(current))
            current = [current[-1]]  # one unit of overlap keeps context across the cut
        current.append(para)
    if current:
        pieces.append(" ".join(current))
    return pieces


def chunk_document(doc: PolicyDocument, max_chars: int = 1400) -> list[Chunk]:
    matches = list(_SECTION_RE.finditer(doc.body))
    chunks: list[Chunk] = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(doc.body)
        text = doc.body[m.end():end].strip()
        if not text:
            continue
        section_no = heading.split(".", 1)[0].strip() if heading[0].isdigit() else heading
        pieces = [text] if len(text) <= max_chars else _split_long(text, max_chars)
        for j, piece in enumerate(pieces):
            suffix = f"-{j + 1}" if len(pieces) > 1 else ""
            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}::{section_no}{suffix}",
                doc_id=doc.doc_id,
                doc_title=doc.title,
                section=heading,
                text=piece,
                version=doc.version,
                domain=doc.domain,
            ))
    return chunks
