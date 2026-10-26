"""Ask RegRadar: questions about the regulatory horizon, answered from the assessments.

Two answerers share one set of read-only tools:

* ``offline_answer`` routes the question by intent (what changed, what is most
  urgent, which controls, which policy, when) and otherwise searches the
  findings; every answer is assembled from tool output and cites it.
* ``LLMAgent`` lets a Databricks foundation model call the same tools in a loop
  (max 6 rounds). Its final answer is shown only if every number in it appears
  in a tool result; otherwise the offline answer is shown and the reason logged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from portfolio_core.guardrails import check_input
from portfolio_core.text import tokenize

from .agents import Assessment
from .corpus import section_by_ref
from .gaps import RATING_ORDER, STATUS_LABEL
from .llm import grounding_check, text_of

MAX_ROUNDS = 6
KEYWORDS = {
    "NPCA-FC-2026-14": ("sar", "suspicious", "transaction monitoring", "aml", "push payment", "mule", "fraud"),
    "NPCA-FC-2026-19": ("kyc", "due diligence", "beneficial", "pep", "politically", "onboard", "periodic review"),
    "NPCA-AI-2026-03": ("ai ", "artificial", "generative", "genai", "model", "bias", "fairness"),
    "NPCA-OR-2026-08": ("incident", "resilience", "outsourc", "loss", "impact tolerance", "operational"),
    "NPCA-FC-2026-22": ("sanction", "screening", "frozen", "fuzzy", "list update"),
}


class Tools:
    """Read-only tools over a set of assessments (one per circular)."""

    def __init__(self, assessments: dict[str, Assessment]) -> None:
        self.assessments = assessments

    def _find(self, circular_id: str) -> Assessment | None:
        for key, a in self.assessments.items():
            if circular_id in (key, a.circular.circular_id):
                return a
        return None

    @staticmethod
    def _finding_row(f) -> dict:
        return {"finding": f.fid, "provision": f.provision.pid, "requirement": f.provision.text,
                "change": f.change.change_type, "what_changed": f.change.headline, "status": f.status,
                "rating": f.rating, "policy_sections": f.policy_refs, "why": f.rationale,
                "controls": [c.control_id for c in f.controls], "needs_human_review": f.needs_review}

    def list_circulars(self) -> dict:
        return {"circulars": [{"circular_id": a.circular.circular_id, "title": a.circular.title,
                               "effective_date": a.circular.effective_date, "gaps": len(a.gaps),
                               "critical": a.rating_counts().get("Critical", 0)} for a in self.assessments.values()]}

    def get_changes(self, circular_id: str) -> dict:
        a = self._find(circular_id)
        if a is None:
            return {"error": f"unknown circular {circular_id}"}
        return {"circular_id": a.circular.circular_id, "changes": [
            {"provision": c.pid, "type": c.change_type, "what_changed": c.headline, "text": c.provision.text}
            for c in a.changes if c.change_type != "UNCHANGED"]}

    def get_findings(self, circular_id: str = "", status: str = "", policy_ref: str = "") -> dict:
        rows = []
        for a in self.assessments.values():
            if circular_id and circular_id not in (a.circular.circular_id, a.pair.key):
                continue
            for f in a.findings:
                if status and f.status != status.upper():
                    continue
                if policy_ref and not any(r.startswith(policy_ref) for r in f.policy_refs):
                    continue
                rows.append(self._finding_row(f))
        return {"findings": rows[:25], "total": len(rows)}

    def get_policy_section(self, ref: str) -> dict:
        s = section_by_ref(ref)
        if s is None:
            return {"error": f"unknown policy section {ref}; use the form 'AML-002 §4'"}
        return {"ref": s.ref, "title": s.heading, "owner": s.owner, "text": s.text}

    def get_actions(self, circular_id: str) -> dict:
        a = self._find(circular_id)
        if a is None:
            return {"error": f"unknown circular {circular_id}"}
        return {"actions": [{"action": x.aid, "finding": x.finding_id, "type": x.kind, "title": x.title,
                             "owner": x.owner, "due": x.due, "priority": x.priority, "decision": x.decision}
                            for x in a.actions]}

    def top_gaps(self, n: int = 5) -> dict:
        gaps = [(a, f) for a in self.assessments.values() for f in a.gaps]
        gaps.sort(key=lambda af: (-RATING_ORDER.index(af[1].rating), af[0].circular.effective_date or "9999"))
        return {"gaps": [{**self._finding_row(f), "effective_date": a.circular.effective_date}
                         for a, f in gaps[:n]]}

    def call(self, name: str, args: dict) -> dict:
        fn = TOOL_FUNCS.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(self, **args)
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}


TOOL_FUNCS = {"list_circulars": Tools.list_circulars, "get_changes": Tools.get_changes,
              "get_findings": Tools.get_findings, "get_policy_section": Tools.get_policy_section,
              "get_actions": Tools.get_actions, "top_gaps": Tools.top_gaps}
TOOL_SCHEMAS = [
    ("list_circulars", "All analysed circulars with effective dates and gap counts.", {}, []),
    ("get_changes", "What changed in one circular (new, amended and removed provisions).",
     {"circular_id": {"type": "string"}}, ["circular_id"]),
    ("get_findings", "Gap findings, optionally filtered by circular, status (CONFLICT, PARTIAL, MISSING, COVERED) "
     "or policy reference prefix such as 'AML-002'.",
     {"circular_id": {"type": "string"}, "status": {"type": "string"}, "policy_ref": {"type": "string"}}, []),
    ("get_policy_section", "Text of one Ledgerline policy section, e.g. 'KYC-001 §4'.",
     {"ref": {"type": "string"}}, ["ref"]),
    ("get_actions", "Draft remediation actions for one circular.", {"circular_id": {"type": "string"}},
     ["circular_id"]),
    ("top_gaps", "The most severe gaps across all circulars.", {"n": {"type": "integer"}}, []),
]


def openai_tools() -> list[dict]:
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": {
        "type": "object", "properties": p, "required": r}}} for n, d, p, r in TOOL_SCHEMAS]


@dataclass
class Answer:
    text: str
    citations: list[str]
    source: str                      # "offline" or the model label
    tool_calls: list[str] = field(default_factory=list)
    blocked: bool = False
    note: str = ""


def _circulars_in(question: str, tools: Tools) -> list[str]:
    q = question.lower()
    ids = [a.circular.circular_id for a in tools.assessments.values() if a.circular.circular_id.lower() in q]
    if ids:
        return ids
    return [cid for cid, words in KEYWORDS.items() if any(w in q + " " for w in words) and tools._find(cid)]


def _fmt_finding(row: dict) -> str:
    refs = ", ".join(row["policy_sections"]) or "no policy section"
    return (f"**{row['provision']}** [{row['rating']}, {STATUS_LABEL.get(row['status'], row['status'])}]: "
            f"{row['what_changed']}. {row['why'][0] if row['why'] else ''} ({refs})")


def offline_answer(question: str, tools: Tools) -> Answer:
    guard = check_input(question, max_chars=600)
    if guard.blocked:
        msg = guard.errors[0] if guard.errors else "This looks like an attempt to change my instructions, so I " \
                                                   "won't answer it. Ask about the circulars, gaps or actions."
        return Answer(msg, [], "offline", blocked=True)
    q = guard.sanitized.lower()
    circs = _circulars_in(q, tools)
    calls: list[str] = []
    lines: list[str] = []
    cites: list[str] = []

    if re.search(r"\b(urgent|priorit|critical|worst|biggest|most important|first)\b", q):
        calls.append("top_gaps(n=5)")
        rows = tools.top_gaps(5)["gaps"]
        lines.append("The most severe gaps across the regulatory horizon:")
        for r in rows:
            lines.append(f"- {_fmt_finding(r)} Effective {r['effective_date']}.")
            cites += [r["provision"], *r["policy_sections"]]
    elif re.search(r"\bcontrols?\b", q):
        targets = circs or [a.circular.circular_id for a in tools.assessments.values()]
        for cid in targets:
            calls.append(f"get_actions({cid})")
            acts = [x for x in tools.get_actions(cid).get("actions", [])
                    if x["type"] in ("Control reconfiguration", "New control", "Control review")]
            for x in acts:
                lines.append(f"- {x['title']} ({x['priority']}, owner {x['owner']}, due {x['due']})")
                cites.append(cid)
        if lines:
            lines.insert(0, "Controls that need work:")
    elif re.search(r"\b(when|effective|deadline for compliance|take effect|dates?)\b", q):
        calls.append("list_circulars()")
        for c in sorted(tools.list_circulars()["circulars"], key=lambda c: c["effective_date"]):
            if circs and c["circular_id"] not in circs:
                continue
            lines.append(f"- {c['circular_id']} ({c['title']}) takes effect on {c['effective_date']}: "
                         f"{c['gaps']} gaps, {c['critical']} critical.")
            cites.append(c["circular_id"])
    elif re.search(r"\b(what|which)\b.*\b(chang|amend)", q) and circs:
        for cid in circs:
            calls.append(f"get_changes({cid})")
            ch = tools.get_changes(cid)
            lines.append(f"Changes in {cid}:")
            for c in ch.get("changes", []):
                lines.append(f"- {c['provision']} ({c['type'].lower()}): {c['what_changed']}")
                cites.append(c["provision"])
    if not lines:
        pol = re.search(r"\b(AML|KYC|SAN|MRM|ORM|TPR|REC|RAS)-\d{3}\b", guard.sanitized, re.I)
        if pol:
            calls.append(f"get_findings(policy_ref={pol.group(0).upper()})")
            rows = tools.get_findings(policy_ref=pol.group(0).upper())["findings"]
        else:
            calls.append("search findings")
            rows = _search(q, tools, circs)
        rows = [r for r in rows if r["status"] != "OPPORTUNITY"][:4]
        if rows:
            lines.append("Here is what the assessments say:")
            for r in rows:
                lines.append(f"- {_fmt_finding(r)}")
                cites += [r["provision"], *r["policy_sections"]]
    if not lines:
        return Answer("I couldn't find anything in the analysed circulars about that. Try asking what changed in a "
                      "circular, which gaps are most urgent, or which controls need to change.", [], "offline",
                      calls)
    note = "Personal data was redacted from the question." if guard.pii_found else ""
    return Answer("\n".join(lines), list(dict.fromkeys(cites)), "offline", calls, note=note)


def _search(q: str, tools: Tools, circs: list[str]) -> list[dict]:
    rows = [Tools._finding_row(f) for a in tools.assessments.values() for f in a.findings
            if not circs or a.circular.circular_id in circs]
    if not rows:
        return []
    texts = [f"{r['requirement']} {r['what_changed']} {' '.join(r['why'])}" for r in rows]
    vec = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None)
    m = vec.fit_transform(texts + [q])
    sims = cosine_similarity(m[-1], m[:-1])[0]
    order = sims.argsort()[::-1]
    return [rows[i] for i in order if sims[i] > 0.05][:4]


SYSTEM = """You are RegRadar, the regulatory change assistant at Ledgerline Bank (fictional bank, synthetic data).
Answer questions about regulatory circulars, the gaps they create against Ledgerline's policies, and remediation.
Rules:
- Use the tools for every fact. Quote figures, dates and references exactly as the tools return them.
- Cite provisions (e.g. NPCA-FC-2026-14 §4.1) and policy sections (e.g. AML-002 §4) for each claim.
- Remediation actions are drafts awaiting human sign-off; never say a change has been made.
- If the tools do not answer the question, say so. At most 160 words."""


class LLMAgent:
    def __init__(self, llm, tools: Tools) -> None:
        self.llm = llm
        self.tools = tools

    def ask(self, question: str) -> Answer:
        fallback = offline_answer(question, self.tools)
        if fallback.blocked:
            return fallback
        guard = check_input(question, max_chars=600)
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": guard.sanitized}]
        results: list[str] = []
        calls: list[str] = []
        try:
            for _ in range(MAX_ROUNDS):
                msg = self.llm.chat(messages, tools=openai_tools(), max_tokens=900)
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    text = text_of(msg.get("content"))
                    ok, bad = grounding_check(text, results)
                    if ok and text:
                        cites = sorted(set(re.findall(r"\b(?:NPCA-[A-Z]{2}-\d{4}-\d{2}|[A-Z]{3}-\d{3}) §[\d.]+", text)))
                        return Answer(text, cites, self.llm.label, calls)
                    fallback.note = ("The model's answer included figures not found in the tool results ("
                                     + ", ".join(bad[:4]) + "), so the offline answer is shown.")
                    return fallback
                messages.append({"role": "assistant", "content": text_of(msg.get("content")) or "",
                                 "tool_calls": tool_calls})
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    out = self.tools.call(fn.get("name", ""), args if isinstance(args, dict) else {})
                    calls.append(f"{fn.get('name')}({', '.join(f'{k}={v}' for k, v in args.items())})")
                    payload = json.dumps(out, default=str)[:12000]
                    results.append(payload)
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": payload})
        except Exception as exc:  # noqa: BLE001 - any model failure falls back to the offline answer
            fallback.note = f"The model could not answer ({type(exc).__name__}); offline answer shown."
            return fallback
        fallback.note = "The model used too many tool rounds; offline answer shown."
        return fallback
