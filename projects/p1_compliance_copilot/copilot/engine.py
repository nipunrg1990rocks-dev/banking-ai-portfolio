"""The copilot pipeline: guardrails -> retrieval -> answer -> checks -> routing -> audit.

``ComplianceCopilot.ask`` is the single entry point used by the Streamlit UI,
the evaluation script and the tests.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from portfolio_core.audit import AuditLog
from portfolio_core.guardrails import check_input
from portfolio_core.llm import ClaudeClient, LLMUnavailable

from .answering import (
    SYSTEM_PROMPT,
    Confidence,
    assess_confidence,
    build_llm_prompt,
    detect_risk_flags,
    extractive_answer,
    validate_citations,
)
from .documents import Chunk, PolicyDocument, chunk_document, load_documents, parse_document
from .retriever import HybridRetriever, RetrievedChunk
from .review import ReviewQueue

DEFAULT_POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"

MODE_OFFLINE = "offline"
MODE_LLM = "llm"

STATUS_RELEASED = "Released (informational guidance)"
STATUS_REVIEW = "Pending human review"
STATUS_BLOCKED = "Blocked by guardrails"
STATUS_NO_ANSWER = "No supported answer - referred to Compliance"

DISCLAIMER = ("Guidance generated from synthetic policy text. It is not a compliance decision. "
              "Confirm with the policy owner before acting.")


@dataclass
class Source:
    number: int
    chunk_id: str
    doc_id: str
    doc_title: str
    section: str
    version: str
    text: str
    fused_score: float
    bm25: float
    similarity: float


@dataclass
class CopilotAnswer:
    query_id: str
    question: str
    answer: str
    status: str
    mode: str
    mode_label: str
    confidence: Confidence | None
    sources: list[Source] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    pii_redacted: list[str] = field(default_factory=list)
    review_item_id: int | None = None
    latency_ms: int = 0
    model: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.status == STATUS_REVIEW


class ComplianceCopilot:
    def __init__(self, policy_dir: str | Path = DEFAULT_POLICY_DIR, *, audit: AuditLog | None = None,
                 reviews: ReviewQueue | None = None, llm: ClaudeClient | None = None, top_k: int = 5) -> None:
        self.documents: list[PolicyDocument] = load_documents(policy_dir)
        self.audit = audit or AuditLog()
        self.reviews = reviews or ReviewQueue()
        self.llm = llm
        self.top_k = top_k
        self._rebuild()
        self.audit.record("index_built", {
            "documents": [f"{d.doc_id} v{d.version}" for d in self.documents],
            "chunks": len(self.chunks),
        })

    # ------------------------------------------------------------ ingestion
    def _rebuild(self) -> None:
        self.chunks: list[Chunk] = [c for d in self.documents for c in chunk_document(d)]
        self.retriever = HybridRetriever(self.chunks)

    def ingest(self, raw: str, source: str, actor: str = "user") -> PolicyDocument:
        """Validate and add (or replace) a policy document, then re-index."""
        doc = parse_document(raw, source)
        pii_check = check_input(doc.body, max_chars=200_000)
        if pii_check.pii_found:
            self.audit.record("ingest_rejected", {"source": source, "reason": "PII detected",
                                                  "pii_types": pii_check.pii_found}, actor=actor)
            found = ", ".join(pii_check.pii_found)
            raise ValueError(f"Document rejected: it appears to contain personal data ({found}). "
                             "Only synthetic, de-identified policy text may be ingested.")
        replaced = any(d.doc_id == doc.doc_id for d in self.documents)
        self.documents = [d for d in self.documents if d.doc_id != doc.doc_id] + [doc]
        self._rebuild()
        self.audit.record("document_ingested", {"doc_id": doc.doc_id, "version": doc.version, "source": source,
                                                "replaced_existing": replaced,
                                                "chunks": sum(c.doc_id == doc.doc_id for c in self.chunks)},
                          actor=actor)
        return doc

    @property
    def domains(self) -> list[str]:
        return sorted({d.domain for d in self.documents})

    # ------------------------------------------------------------ ask
    def ask(self, question: str, *, user: str = "demo.user", mode: str = MODE_OFFLINE,
            domains: list[str] | None = None) -> CopilotAnswer:
        started = time.perf_counter()
        query_id = uuid.uuid4().hex[:12]
        guard = check_input(question)
        # Only the redacted question is ever logged or sent anywhere.
        self.audit.record("question_received", {
            "question": guard.sanitized, "pii_redacted": guard.pii_found, "mode_requested": mode,
            "domain_filter": domains or "all",
        }, actor=user, correlation_id=query_id)

        if guard.blocked:
            reason = "; ".join(guard.errors) or "Possible prompt-injection attempt detected."
            self.audit.record("question_blocked", {"reason": reason}, actor="guardrails", correlation_id=query_id)
            return CopilotAnswer(query_id, guard.sanitized, f"Request blocked: {reason}", STATUS_BLOCKED, mode,
                                 "Guardrails", None, flags=[reason], pii_redacted=guard.pii_found,
                                 latency_ms=int((time.perf_counter() - started) * 1000))

        results = self.retriever.search(guard.sanitized, k=self.top_k, domains=domains)
        risk_rules = detect_risk_flags(guard.sanitized)
        results = self._pin_governing_sections(results, risk_rules)
        sources = [Source(i + 1, r.chunk.chunk_id, r.chunk.doc_id, r.chunk.doc_title, r.chunk.section,
                          r.chunk.version, r.chunk.text, round(r.score, 5), round(r.bm25, 3), round(r.dense, 3))
                   for i, r in enumerate(results)]
        confidence = assess_confidence(guard.sanitized, results)
        self.audit.record("retrieval_completed", {
            "sources": [{"rank": s.number, "chunk": s.chunk_id, "version": s.version,
                         "bm25": s.bm25, "similarity": s.similarity} for s in sources],
            "confidence": confidence.level, "confidence_score": confidence.score,
        }, actor="retriever", correlation_id=query_id)

        notes: list[str] = []
        flags = [rule.message for rule in risk_rules]
        review_needed = any(rule.requires_review for rule in risk_rules)
        if guard.pii_found:
            flags.append(f"Personal data removed from the question before processing ({', '.join(guard.pii_found)}).")

        answer_text, mode_used, mode_label, model = self._generate(guard.sanitized, results, mode, notes)

        if not answer_text or confidence.level == "Low":
            # Never show an unsupported answer: withhold it and refer the question to a human.
            status = STATUS_NO_ANSWER
            flags.append("Low confidence: the policy library does not clearly answer this question.")
            answer_text = ("The policy library does not clearly answer this question, so no answer is given. "
                           "It has been referred to Compliance. The closest policy sections are listed for reference.")
            review_needed = True
        else:
            if confidence.level == "Medium":
                flags.append("Medium confidence: check the cited sections before relying on this answer.")
            status = STATUS_REVIEW if review_needed else STATUS_RELEASED

        answer = CopilotAnswer(query_id, guard.sanitized, answer_text, status, mode_used, mode_label, confidence,
                               sources, flags, guard.pii_found, None,
                               int((time.perf_counter() - started) * 1000), model, notes)

        if review_needed:
            answer.review_item_id = self.reviews.submit(
                query_id=query_id, asked_by=user, question=guard.sanitized, answer=answer_text,
                confidence=confidence.level, flags=flags, sources=[s.chunk_id for s in sources[:3]])
            self.audit.record("routed_to_review", {"review_item_id": answer.review_item_id, "flags": flags},
                              actor="router", correlation_id=query_id)

        self.audit.record("answer_returned", {
            "status": status, "mode": mode_used, "model": model, "confidence": confidence.level,
            "cited_sources": [s.chunk_id for s in sources[:3]], "flags": flags, "latency_ms": answer.latency_ms,
            "answer_chars": len(answer_text),
        }, actor="copilot", correlation_id=query_id)
        return answer

    def _pin_governing_sections(self, results: list[RetrievedChunk], rules) -> list[RetrievedChunk]:
        """When a risk rule fires, put the policy section that states that rule first."""
        existing = {r.chunk.chunk_id: r for r in results}
        positions = {c.chunk_id: i for i, c in enumerate(self.chunks)}
        pinned: list[RetrievedChunk] = []
        for rule in rules:
            ref = rule.policy_ref
            if not ref or ref not in positions or any(p.chunk.chunk_id == ref for p in pinned):
                continue
            if ref in existing:
                pinned.append(existing[ref])
            else:
                chunk = self.chunks[positions[ref]]
                similarity = float(self.retriever.dense.similarities(chunk.search_text)[positions[ref]])
                pinned.append(RetrievedChunk(chunk, 0.0, 0.0, similarity, 0))
        if not pinned:
            return results
        pinned_ids = {p.chunk.chunk_id for p in pinned}
        merged = (pinned + [r for r in results if r.chunk.chunk_id not in pinned_ids])[: self.top_k]
        for i, r in enumerate(merged):
            r.rank = i + 1
        return merged

    def _generate(self, question: str, results: list[RetrievedChunk], mode: str,
                  notes: list[str]) -> tuple[str, str, str, str | None]:
        if mode == MODE_LLM:
            try:
                llm = self.llm or ClaudeClient()
                response = llm.complete(SYSTEM_PROMPT, build_llm_prompt(question, results))
                if response.text.strip() == "INSUFFICIENT_EVIDENCE":
                    return "", MODE_LLM, f"Live LLM ({response.model})", response.model
                ok, _, problems = validate_citations(response.text, len(results))
                if ok:
                    return response.text, MODE_LLM, f"Live LLM ({response.model})", response.model
                notes.append("LLM answer failed citation validation (" + " ".join(problems) +
                             ") so the offline extractive answer is shown instead.")
            except LLMUnavailable as exc:
                notes.append(f"Live LLM unavailable: {exc} Showing offline extractive answer.")
            except Exception as exc:  # network or API error: degrade gracefully, never crash the UI
                notes.append(f"Live LLM call failed ({type(exc).__name__}). Showing offline extractive answer.")
        draft = extractive_answer(question, results)
        return draft.text, MODE_OFFLINE, "Offline extractive mode (no LLM; sentences quoted from policies)", None

    # ------------------------------------------------------------ review
    def decide_review(self, item_id: int, *, decision: str, reviewer: str, comment: str,
                      amended_answer: str | None = None):
        item = self.reviews.decide(item_id, decision=decision, reviewer=reviewer, comment=comment,
                                   amended_answer=amended_answer)
        self.audit.record("review_decision", {
            "review_item_id": item_id, "decision": decision, "comment": comment,
            "amended": bool(amended_answer),
        }, actor=reviewer, correlation_id=item.query_id)
        return item
