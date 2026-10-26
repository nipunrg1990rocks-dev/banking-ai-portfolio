"""Live mode: Claude drives the same tools, and a grounding check guards its numbers.

Live mode is off unless the app owner adds an ``ANTHROPIC_API_KEY`` to Streamlit
secrets (and has approved the spend). When it is on:

* ``ClaudeRiskAgent.ask`` gives Claude the agent's read-only tools and lets it
  decide which to call (a standard tool-use loop, capped at ``MAX_TOOL_ROUNDS``).
* Every number in Claude's final answer must appear in a tool result from that
  conversation (``grounding_check``). An answer with an unsupported number is
  not shown as fact: the app falls back to the offline answer and logs why.

The key comes from ``portfolio_core.llm`` (environment or Streamlit secrets) and
is never displayed or logged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from portfolio_core.llm import live_llm_available, resolve_api_key, resolve_model

from .scenarios import SCENARIOS
from .tools import TOOL_NAMES, RiskTools

MAX_TOOL_ROUNDS = 8
SYSTEM_PROMPT = """You are the Risk Insights Agent for Ledgerline Bank, a fictional bank with synthetic data.
You answer questions from senior managers about risk KPIs, KRIs, data quality, reconciliations and operations.

Rules:
- Use the tools to get every number. Never estimate, round differently or invent a figure.
- Quote numbers exactly as the tools return them (you may format them, e.g. 117.6%).
- Say which metric and date each number refers to. Name thresholds and the policy (e.g. RAS-004 s.6).
- Root causes are hypotheses: state the evidence (timing, lineage) and the confidence the tool gives.
- You recommend; humans decide. Never say an action has been taken.
- Be concise: at most 180 words, plain business English, short paragraphs or bullets."""

TOOL_SCHEMAS = {
    "list_metrics": ("List every KPI and KRI with its id, domain, unit and dimensions.", {}),
    "scan_metrics": ("Status of every metric today, 7 days ago, and its worst point in the last 7 days.", {}),
    "get_metric": ("One metric's value, RAG status, thresholds and definition on a day (default: reporting day).",
                   {"metric_id": {"type": "string"}, "on": {"type": "string", "description": "YYYY-MM-DD"}}),
    "get_trend": ("Daily values for the last N days with min, max and a robust trend.",
                  {"metric_id": {"type": "string"}, "days": {"type": "integer"}}),
    "explain_change": ("Break a metric's move over compare_days into dimension members; shows the main driver.",
                       {"metric_id": {"type": "string"}, "on": {"type": "string"},
                        "compare_days": {"type": "integer"}, "dimension": {"type": "string"}}),
    "explain_level": ("Which dimension members make up a metric's current shortfall.",
                      {"metric_id": {"type": "string"}, "dimension": {"type": "string"}, "on": {"type": "string"}}),
    "find_onset": ("Change-point: when a move started (step or ramp), for a metric or one dimension member.",
                   {"metric_id": {"type": "string"}, "dimension": {"type": "string"}, "member": {"type": "string"},
                    "on": {"type": "string"}}),
    "correlate_changes": ("Releases, incidents and business changes that could explain a move starting on onset, "
                          "scored by lineage and timing.",
                          {"metric_id": {"type": "string"}, "onset": {"type": "string"},
                           "kind": {"type": "string", "enum": ["step", "ramp"]}, "dimension": {"type": "string"},
                           "member": {"type": "string"}}),
    "forecast": ("When a metric crosses its early-warning trigger or limit (scheduled facts or trend).",
                 {"metric_id": {"type": "string"}, "horizon_days": {"type": "integer"}}),
    "what_if": ("Run a what-if scenario. Scenarios: " + "; ".join(f"{k}: {v}" for k, v in SCENARIOS.items()),
                {"scenario": {"type": "string", "enum": sorted(SCENARIOS)}, "amount": {"type": "number"}}),
    "list_records": ("The records behind a metric today, optionally for one dimension member.",
                     {"metric_id": {"type": "string"}, "dimension": {"type": "string"}, "member": {"type": "string"},
                      "limit": {"type": "integer"}}),
    "exception_flow": ("Daily exception arrivals vs closures for a team.",
                       {"team": {"type": "string"}, "days": {"type": "integer"}}),
    "get_policy": ("Escalation rules from the policy library, e.g. 'RAS-004 s.6' or 'AML-002 s.3'.",
                   {"policy_ref": {"type": "string"}}),
}
REQUIRED = {"get_metric": ["metric_id"], "get_trend": ["metric_id"], "explain_change": ["metric_id"],
            "explain_level": ["metric_id"], "find_onset": ["metric_id"], "correlate_changes": ["metric_id", "onset"],
            "forecast": ["metric_id"], "what_if": ["scenario", "amount"], "list_records": ["metric_id"],
            "exception_flow": ["team"], "get_policy": ["policy_ref"]}
assert set(TOOL_SCHEMAS) == set(TOOL_NAMES)


def tool_definitions() -> list[dict]:
    return [{"name": name, "description": desc,
             "input_schema": {"type": "object", "properties": props, "required": REQUIRED.get(name, [])}}
            for name, (desc, props) in TOOL_SCHEMAS.items()]


# ------------------------------------------------------------ grounding
NUMBER = re.compile(r"(?<![A-Za-z0-9_-])[-+]?\d[\d,]*(?:\.\d+)?")
DATE_LIKE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\b")
ID_LIKE = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*-[\w.-]+\b|\b[A-Z]\d{4,}\b|\b\w+_\w+\b|\bR\d+\.\d+(?:\.\d+)?\b|"
                     r"\b(?:RAS|AML|ORM|KYC|SAN)-\d+(?: s\.\d+)?\b|\bs\.\d+\b|\bCET1\b|\bTier 1\b|\bL[12]\b")


def _numbers(text: str) -> list[str]:
    text = DATE_LIKE.sub(" ", text)
    text = ID_LIKE.sub(" ", text)
    return NUMBER.findall(text)


def _variants(value: float) -> set[float]:
    out = {round(value, 4)}
    for scale in (1, 100, 1e-6, 1e6, 1e-3, 1e3):
        v = value * scale
        for nd in (0, 1, 2):
            out.add(round(v, nd))
    return out


def _collect(obj, sink: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        sink.update(_variants(float(obj)))
    elif isinstance(obj, str):
        for n in _numbers(obj):
            try:
                sink.update(_variants(float(n.replace(",", ""))))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect(v, sink)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect(v, sink)


def grounding_check(text: str, tool_results: list) -> tuple[bool, list[str]]:
    """Every number in ``text`` must match a number in the tool results (allowing rounding and unit scaling).

    Small whole numbers (0-10) are allowed because they are usually counts in prose ("two actions").
    """
    known: set[float] = set()
    _collect(tool_results, known)
    unsupported = []
    for raw in _numbers(text):
        try:
            v = float(raw.replace(",", "").lstrip("+"))
        except ValueError:
            continue
        if v.is_integer() and 0 <= abs(v) <= 10:
            continue
        candidates = {round(v, 4), round(v, 2), round(v, 1), round(v, 0)}
        if not (candidates & known) and not any(abs(v - k) <= 0.051 * max(1, abs(v)) / 100 for k in known):
            unsupported.append(raw)
    return (not unsupported), unsupported


# ------------------------------------------------------------- the agent
@dataclass
class LiveAnswer:
    text: str
    grounded: bool
    unsupported: list[str]
    tool_calls: int
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    error: str | None = None
    results: list = field(default_factory=list)


class ClaudeRiskAgent:
    def __init__(self, tools: RiskTools, client=None, model: str | None = None) -> None:
        self.tools = tools
        self.model = model or resolve_model()
        if client is not None:
            self._client = client
        else:
            import anthropic

            self._client = anthropic.Anthropic(api_key=resolve_api_key(), timeout=90.0, max_retries=2)

    @staticmethod
    def available() -> bool:
        return live_llm_available()

    def ask(self, question: str, history: list[dict] | None = None) -> LiveAnswer:
        messages = list(history or []) + [{"role": "user", "content": question}]
        results, calls, tin, tout = [], 0, 0, 0
        system = SYSTEM_PROMPT + f"\nThe reporting date is {self.tools.as_of.isoformat()}."
        for _ in range(MAX_TOOL_ROUNDS + 1):
            response = self._client.messages.create(
                model=self.model, max_tokens=4000, system=system, tools=tool_definitions(),
                output_config={"effort": "low"}, messages=messages)
            usage = getattr(response, "usage", None)
            tin += getattr(usage, "input_tokens", 0) or 0
            tout += getattr(usage, "output_tokens", 0) or 0
            if response.stop_reason == "refusal":
                return LiveAnswer("", False, [], calls, tin, tout, self.model, error="The model declined.")
            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
                ok, bad = grounding_check(text, results)
                return LiveAnswer(text, ok, bad, calls, tin, tout, self.model, results=results)
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                calls += 1
                args = dict(block.input or {})
                if block.name not in TOOL_NAMES:
                    out, is_error = {"error": f"unknown tool {block.name}"}, True
                else:
                    try:
                        out = self.tools.call(block.name, **args)
                        is_error = "error" in out
                    except TypeError as exc:
                        out, is_error = {"error": f"bad arguments: {exc}"}, True
                results.append(out)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": json.dumps(out)[:20000], "is_error": is_error})
            messages.append({"role": "user", "content": tool_results})
        return LiveAnswer("", False, [], calls, tin, tout, self.model, error="Stopped after too many tool rounds.")
