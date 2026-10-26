"""Load the three inputs: regulator circulars, Ledgerline's internal policies, and the control library.

* Circulars live in ``data/circulars/<id>/current.md`` with an optional
  ``previous.md`` (the version the new one replaces). They are synthetic and
  issued by a fictional regulator, the Northbridge Prudential and Conduct
  Authority (NPCA).
* Policies are Project 1's policy library (the same eight Ledgerline Bank
  policies the Compliance Copilot answers from), loaded with Project 1's parser
  so both projects cite sections identically.
* Controls are a synthetic control library (``data/controls.csv``), each
  mapped to the policy section it implements.

Circular text is split into numbered sections and then into provisions
(sentences). A provision is classed as an obligation ("must", "shall"), a
prohibition ("must not", "may not"), a permission ("may") or information.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd

from portfolio_core.text import split_sentences

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DATA = PROJECT / "data"
CIRCULARS_DIR = DATA / "circulars"
CONTROLS_CSV = DATA / "controls.csv"
P1_DIR = PROJECT.parent / "p1_compliance_copilot"
POLICIES_DIR = P1_DIR / "data" / "policies"

if str(P1_DIR) not in sys.path:
    sys.path.insert(0, str(P1_DIR))

from copilot.documents import PolicyDocument, chunk_document, load_documents  # noqa: E402

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

MAX_CIRCULAR_CHARS = 60_000
# Which Ledgerline policy owns each regulatory domain (used as a prior when matching, and for new clauses).
DOMAIN_POLICY = {"AML": "AML-002", "KYC": "KYC-001", "Sanctions": "SAN-003", "Model Risk & AI": "MRM-006",
                 "Operational Risk": "ORM-005", "Third-Party Risk": "TPR-008", "Data & Privacy": "REC-007",
                 "Enterprise Risk": "RAS-004"}


class CircularError(ValueError):
    pass


@dataclass(frozen=True)
class Provision:
    """One sentence of a circular, with where it sits."""

    pid: str             # e.g. "NPCA-FC-2026-14 §4.1"
    circular_id: str
    section_no: str
    section_title: str
    index: int           # sentence number within the section, from 1
    text: str
    modality: str        # obligation | prohibition | permission | information

    @property
    def ref(self) -> str:
        return f"{self.circular_id} §{self.section_no}"


@dataclass
class Circular:
    circular_id: str
    title: str
    body: str
    metadata: dict[str, str] = field(default_factory=dict)
    provisions: list[Provision] = field(default_factory=list)

    @property
    def effective_date(self) -> str:
        return self.metadata.get("effective_date", "")

    @property
    def published(self) -> str:
        return self.metadata.get("published", "")

    @property
    def domain(self) -> str:
        return self.metadata.get("domain", "General")

    @property
    def regulator(self) -> str:
        return self.metadata.get("regulator", "Regulator")

    def sections(self) -> list[tuple[str, str, str]]:
        """(number, title, text) for every '##' section."""
        return _sections(self.body)


@dataclass
class CircularPair:
    """A circular and the version it replaces (None for a brand-new circular)."""

    key: str
    current: Circular
    previous: Circular | None = None


_MODAL_PROHIBITION = re.compile(r"\b(?:must not|shall not|may not|must never|is prohibited|are prohibited)\b", re.I)
_MODAL_OBLIGATION = re.compile(r"\b(?:must|shall|is required to|are required to|required to|have to|has to)\b", re.I)
_MODAL_PERMISSION = re.compile(r"\b(?:may|can|is permitted to|are permitted to)\b", re.I)


def modality(sentence: str) -> str:
    # A "must not ... without" sentence still imposes a duty; prohibition is the closer reading.
    if _MODAL_PROHIBITION.search(sentence):
        return "prohibition"
    if _MODAL_OBLIGATION.search(sentence):
        return "obligation"
    if _MODAL_PERMISSION.search(sentence):
        return "permission"
    return "information"


def _sections(body: str) -> list[tuple[str, str, str]]:
    matches = list(_SECTION_RE.finditer(body))
    out = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[m.end():end].strip()
        number, _, title = heading.partition(".")
        if not number.strip().isdigit():
            number, title = str(i + 1), heading
        out.append((number.strip(), title.strip() or heading, text))
    return out


def parse_circular(raw: str, source: str = "", default_id: str = "") -> Circular:
    """Parse a circular in the project's Markdown format, or plain text pasted by a user.

    Plain text with no '##' headings is accepted: each paragraph becomes a section.
    """
    if not raw or not raw.strip():
        raise CircularError("The circular is empty.")
    if len(raw) > MAX_CIRCULAR_CHARS:
        raise CircularError(f"The circular is too long ({len(raw):,} characters; limit {MAX_CIRCULAR_CHARS:,}).")
    raw = raw.replace("\r\n", "\n")
    metadata: dict[str, str] = {}
    body = raw
    match = _FRONT_MATTER_RE.match(raw)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
        body = raw[match.end():].strip()
    if not _SECTION_RE.search(body):
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip() and not p.strip().startswith("#")]
        if not paragraphs:
            raise CircularError("No text found in the circular.")
        body = "\n\n".join(f"## {i}. Paragraph {i}\n\n{p}" for i, p in enumerate(paragraphs, 1))
    circular_id = metadata.get("circular_id") or default_id or "USER-CIRCULAR"
    title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = metadata.get("title") or (title_match.group(1).strip() if title_match else circular_id)
    circ = Circular(circular_id, title, body, metadata)
    circ.provisions = _provisions(circ)
    if not circ.provisions:
        raise CircularError(f"{source or circular_id}: no sentences found under the section headings.")
    return circ


def _provisions(circ: Circular) -> list[Provision]:
    out = []
    for number, title, text in circ.sections():
        for i, sentence in enumerate(split_sentences(text), 1):
            out.append(Provision(f"{circ.circular_id} §{number}.{i}", circ.circular_id, number, title, i, sentence,
                                 modality(sentence)))
    return out


@lru_cache(maxsize=1)
def load_circular_pairs(directory: str = str(CIRCULARS_DIR)) -> tuple[CircularPair, ...]:
    pairs = []
    for folder in sorted(Path(directory).iterdir()):
        current_file = folder / "current.md"
        if not current_file.is_file():
            continue
        current = parse_circular(current_file.read_text(encoding="utf-8"), str(current_file), folder.name)
        previous_file = folder / "previous.md"
        previous = (parse_circular(previous_file.read_text(encoding="utf-8"), str(previous_file))
                    if previous_file.is_file() else None)
        pairs.append(CircularPair(folder.name, current, previous))
    if not pairs:
        raise CircularError(f"No circulars found in {directory}")
    return tuple(pairs)


def get_pair(key: str) -> CircularPair:
    for pair in load_circular_pairs():
        if pair.key == key:
            return pair
    raise KeyError(key)


@dataclass(frozen=True)
class PolicySection:
    doc_id: str
    doc_title: str
    section_no: str
    heading: str
    text: str
    owner: str
    version: str

    @property
    def ref(self) -> str:
        return f"{self.doc_id} §{self.section_no}"


@lru_cache(maxsize=1)
def load_policies() -> tuple[PolicyDocument, ...]:
    return tuple(load_documents(POLICIES_DIR))


@lru_cache(maxsize=1)
def policy_sections() -> tuple[PolicySection, ...]:
    """Every numbered section of every policy (sections are short, so one section = one chunk)."""
    out = []
    for doc in load_policies():
        for number, heading, text in _sections(doc.body):
            out.append(PolicySection(doc.doc_id, doc.title, number, heading, text,
                                     doc.metadata.get("owner", "Policy owner"), doc.version))
    return tuple(out)


def policy_chunks():
    """Project 1's chunks, for the hybrid retriever."""
    return [c for doc in load_policies() for c in chunk_document(doc)]


def section_by_ref(ref: str) -> PolicySection | None:
    for s in policy_sections():
        if s.ref == ref:
            return s
    return None


@lru_cache(maxsize=1)
def load_controls() -> pd.DataFrame:
    df = pd.read_csv(CONTROLS_CSV)
    required = {"control_id", "name", "description", "policy_ref", "owner", "last_test_result"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"controls.csv is missing columns: {sorted(missing)}")
    return df
