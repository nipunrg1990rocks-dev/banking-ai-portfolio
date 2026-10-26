"""Offline evaluation of the copilot against a labelled golden question set.

Metrics (all deterministic in offline mode):

* Recall@1 / Recall@3 / Recall@5 - is the expected policy section among the top-k sources?
* MRR - mean reciprocal rank of the expected section.
* Answer fact accuracy - does the answer contain the expected key fact (e.g. "30 calendar days")?
* Citation validity - does every answer cite only retrieved sources?
* Routing accuracy - was the question released, sent to review, refused or blocked as expected?

Run from the repository root:

    python projects/p1_compliance_copilot/evaluate.py            # prints a report
    python projects/p1_compliance_copilot/evaluate.py --mlflow   # also logs to MLflow (pip install mlflow)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .answering import validate_citations
from .engine import STATUS_BLOCKED, STATUS_NO_ANSWER, STATUS_RELEASED, STATUS_REVIEW, ComplianceCopilot

GOLDEN_SET = Path(__file__).resolve().parent.parent / "data" / "eval" / "golden_set.json"
ROUTES = {STATUS_RELEASED: "released", STATUS_REVIEW: "review", STATUS_NO_ANSWER: "no_answer",
          STATUS_BLOCKED: "blocked"}


@dataclass
class CaseResult:
    id: str
    question: str
    expected_chunk: str | None
    rank: int | None
    fact_found: bool | None
    citations_valid: bool | None
    expected_route: str
    actual_route: str
    confidence: str | None

    @property
    def route_ok(self) -> bool:
        return self.expected_route == self.actual_route


def load_golden_set(path: str | Path = GOLDEN_SET) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_evaluation(copilot: ComplianceCopilot | None = None, cases: list[dict] | None = None) -> dict:
    copilot = copilot or ComplianceCopilot()
    cases = cases if cases is not None else load_golden_set()
    results: list[CaseResult] = []
    for case in cases:
        answer = copilot.ask(case["question"], user="evaluation.harness")
        ids = [s.chunk_id for s in answer.sources]
        rank = ids.index(case["expected_chunk"]) + 1 if case["expected_chunk"] in ids else None
        fact = None
        cites = None
        if case["expected_fact"]:
            fact = case["expected_fact"].lower() in answer.answer.lower()
        if answer.status in (STATUS_RELEASED, STATUS_REVIEW):
            cites = validate_citations(answer.answer, len(answer.sources))[0]
        results.append(CaseResult(case["id"], case["question"], case["expected_chunk"], rank, fact, cites,
                                  case["expected_route"], ROUTES[answer.status],
                                  answer.confidence.level if answer.confidence else None))

    retrieval = [r for r in results if r.expected_chunk]
    facts = [r for r in results if r.fact_found is not None]
    cited = [r for r in results if r.citations_valid is not None]

    def recall(k: int) -> float:
        return sum(1 for r in retrieval if r.rank and r.rank <= k) / len(retrieval)

    metrics = {
        "cases": len(results),
        "recall_at_1": round(recall(1), 3),
        "recall_at_3": round(recall(3), 3),
        "recall_at_5": round(recall(5), 3),
        "mrr": round(sum(1 / r.rank for r in retrieval if r.rank) / len(retrieval), 3),
        "answer_fact_accuracy": round(sum(r.fact_found for r in facts) / len(facts), 3),
        "citation_validity": round(sum(r.citations_valid for r in cited) / len(cited), 3) if cited else 0.0,
        "routing_accuracy": round(sum(r.route_ok for r in results) / len(results), 3),
    }
    return {"metrics": metrics, "cases": [asdict(r) | {"route_ok": r.route_ok} for r in results]}


def format_report(report: dict) -> str:
    m = report["metrics"]
    lines = ["# Copilot evaluation report (offline mode)", "",
             "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in m.items()]
    misses = [c for c in report["cases"]
              if not c["route_ok"] or c["fact_found"] is False or (c["expected_chunk"] and c["rank"] != 1)]
    lines += ["", f"## Cases needing attention ({len(misses)})", ""]
    if not misses:
        lines.append("None.")
    for c in misses:
        lines.append(f"- {c['id']} {c['question']!r}: expected {c['expected_chunk']} at rank {c['rank']}, "
                     f"fact found={c['fact_found']}, route {c['actual_route']} (expected {c['expected_route']})")
    return "\n".join(lines)
