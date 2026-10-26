"""Live mode: a language model drives the same tools, and a grounding check guards its numbers.

Two backends, both off by default (the offline agent needs neither):

* ``DatabricksFoundationModel``: a foundation-model endpoint in the owner's
  Databricks workspace. On Databricks Free Edition this is free and needs no
  API key. Used by the notebook (with the notebook's own credentials) and by the
  app when the Databricks secrets are set.
* ``AnthropicClaude``: Claude through the Anthropic API, if an
  ``ANTHROPIC_API_KEY`` is configured and the spend approved.

In both, ``RiskLLMAgent.ask`` gives the model the agent's read-only tools and
lets it decide which to call (a standard tool-use loop, capped at ``MAX_TOOL_ROUNDS``).
* Every number in Claude's final answer must appear in a tool result from that
  conversation (``grounding_check``). An answer with an unsupported number is
  not shown as fact: the app falls back to the offline answer and logs why.

Keys and tokens come from the environment, Streamlit secrets or the notebook
context, and are never displayed or logged.
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


# ------------------------------------------------------------ backends
@dataclass
class Turn:
    """One model response, in a provider-neutral shape."""

    stop: str  # "tool", "end" or "refusal"
    text: str = ""
    calls: list[tuple[str, str, dict]] = field(default_factory=list)  # (call id, tool name, arguments)
    input_tokens: int = 0
    output_tokens: int = 0
    raw: object = None


class DatabricksFoundationModel:
    """A Databricks Model Serving foundation-model endpoint (OpenAI-style chat completions with tools).

    On Databricks Free Edition these endpoints are part of the workspace, so there is no API key and no bill.
    Credentials are the workspace host and a token: inside a notebook, the notebook's own context; in the app,
    the same Streamlit secrets as the SQL warehouse. Which endpoints exist differs by workspace, so ``discover``
    lists them and keeps the first candidate that completes a one-call tool probe.
    """

    CANDIDATES = ("databricks-claude-sonnet-4-5", "databricks-claude-sonnet-4",
                  "databricks-meta-llama-3-3-70b-instruct", "databricks-llama-4-maverick", "databricks-gpt-oss-120b")

    def __init__(self, host: str, token: str, endpoint: str, http=None, timeout: float = 90.0) -> None:
        self.host = "https://" + host.replace("https://", "").rstrip("/")
        self._token = token
        self.endpoint = endpoint
        self.label = f"Databricks · {endpoint}"
        self._http = http or self._urllib
        self.timeout = timeout

    def _urllib(self, method: str, url: str, body: dict | None) -> dict:
        import urllib.request

        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {self._token}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - fixed https workspace URL
            return json.loads(resp.read().decode())

    # The conversation is kept in OpenAI chat format.
    def start(self, system: str, question: str, history: list[dict] | None) -> list[dict]:
        return [{"role": "system", "content": system}, *(history or []), {"role": "user", "content": question}]

    def step(self, state: list[dict]) -> Turn:
        tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                   "parameters": t["input_schema"]}} for t in tool_definitions()]
        out = self._http("POST", f"{self.host}/serving-endpoints/{self.endpoint}/invocations",
                         {"messages": state, "tools": tools, "max_tokens": 1500, "temperature": 0})
        choice = out["choices"][0]
        msg = choice["message"]
        usage = out.get("usage") or {}
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": c["function"].get("arguments")}
            calls.append((c["id"], c["function"]["name"], args))
        content = msg.get("content")
        if isinstance(content, list):  # some endpoints return content blocks
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        stop = "tool" if calls else "end"
        return Turn(stop, (content or "").strip(), calls, usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0), raw=msg)

    def add_results(self, state: list[dict], turn: Turn, results: list[tuple[str, str, bool]]) -> None:
        state.append({"role": "assistant", "content": turn.raw.get("content") or "",
                      "tool_calls": turn.raw.get("tool_calls")})
        for call_id, content, _ in results:
            state.append({"role": "tool", "tool_call_id": call_id, "content": content})

    @classmethod
    def discover(cls, host: str, token: str, preferred: str | None = None, http=None) -> DatabricksFoundationModel:
        """Pick a working endpoint: the preferred one if set, else the first candidate that passes a tool probe."""
        probe_client = cls(host, token, preferred or cls.CANDIDATES[0], http=http)
        if preferred:
            return probe_client
        listed = probe_client._http("GET", f"{probe_client.host}/api/2.0/serving-endpoints", None)
        available = {e.get("name") for e in listed.get("endpoints", [])}
        errors = []
        for name in [c for c in cls.CANDIDATES if c in available]:
            candidate = cls(host, token, name, http=http)
            try:
                state = candidate.start("Call the tool asked for.", "Call list_metrics.", None)
                if candidate.step(state).calls:
                    return candidate
                errors.append(f"{name}: no tool call")
            except Exception as exc:  # noqa: BLE001 - any endpoint failure means "try the next one"
                errors.append(f"{name}: {type(exc).__name__}")
        raise RuntimeError("No foundation-model endpoint with tool calling found. Tried: "
                           + ("; ".join(errors) or f"none of {', '.join(cls.CANDIDATES)} are in this workspace"))


class AnthropicClaude:
    """Claude through the Anthropic API (paid; needs ANTHROPIC_API_KEY)."""

    def __init__(self, client=None, model: str | None = None) -> None:
        self.model = model or resolve_model()
        self.label = f"Claude · {self.model}"
        if client is not None:
            self._client = client
        else:
            import anthropic

            self._client = anthropic.Anthropic(api_key=resolve_api_key(), timeout=90.0, max_retries=2)

    def start(self, system: str, question: str, history: list[dict] | None) -> dict:
        return {"system": system, "messages": list(history or []) + [{"role": "user", "content": question}]}

    def step(self, state: dict) -> Turn:
        response = self._client.messages.create(
            model=self.model, max_tokens=4000, system=state["system"], tools=tool_definitions(),
            output_config={"effort": "low"}, messages=state["messages"])
        usage = getattr(response, "usage", None)
        tin, tout = getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0
        if response.stop_reason == "refusal":
            return Turn("refusal", input_tokens=tin, output_tokens=tout)
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
        calls = [(b.id, b.name, dict(b.input or {})) for b in response.content if getattr(b, "type", "") == "tool_use"]
        stop = "tool" if response.stop_reason == "tool_use" else "end"
        return Turn(stop, text, calls, tin, tout, raw=response)

    def add_results(self, state: dict, turn: Turn, results: list[tuple[str, str, bool]]) -> None:
        state["messages"].append({"role": "assistant", "content": turn.raw.content})
        state["messages"].append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": content, "is_error": err}
            for cid, content, err in results]})


def databricks_llm_configured() -> bool:
    from .engine import databricks_configured

    return databricks_configured()


def databricks_from_secrets(http=None) -> DatabricksFoundationModel:
    from .engine import _secret

    return DatabricksFoundationModel.discover(_secret("DATABRICKS_SERVER_HOSTNAME"), _secret("DATABRICKS_TOKEN"),
                                              _secret("DATABRICKS_LLM_ENDPOINT"), http=http)


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


class RiskLLMAgent:
    """An LLM drives the read-only tools; the grounding check decides whether its answer may be shown."""

    def __init__(self, tools: RiskTools, backend) -> None:
        self.tools = tools
        self.backend = backend

    def ask(self, question: str, history: list[dict] | None = None) -> LiveAnswer:
        system = SYSTEM_PROMPT + f"\nThe reporting date is {self.tools.as_of.isoformat()}."
        state = self.backend.start(system, question, history)
        results, calls, tin, tout = [], 0, 0, 0
        label = self.backend.label
        for _ in range(MAX_TOOL_ROUNDS + 1):
            turn = self.backend.step(state)
            tin, tout = tin + turn.input_tokens, tout + turn.output_tokens
            if turn.stop == "refusal":
                return LiveAnswer("", False, [], calls, tin, tout, label, error="The model declined.")
            if turn.stop != "tool":
                ok, bad = grounding_check(turn.text, results)
                return LiveAnswer(turn.text, ok, bad, calls, tin, tout, label, results=results)
            replies = []
            for call_id, name, args in turn.calls:
                calls += 1
                if name not in TOOL_NAMES:
                    out, is_error = {"error": f"unknown tool {name}"}, True
                else:
                    try:
                        out = self.tools.call(name, **args)
                        is_error = "error" in out
                    except TypeError as exc:
                        out, is_error = {"error": f"bad arguments: {exc}"}, True
                results.append(out)
                replies.append((call_id, json.dumps(out)[:20000], is_error))
            self.backend.add_results(state, turn, replies)
        return LiveAnswer("", False, [], calls, tin, tout, label, error="Stopped after too many tool rounds.")


class ClaudeRiskAgent(RiskLLMAgent):
    """Backwards-compatible shortcut: the agent with the Anthropic backend."""

    def __init__(self, tools: RiskTools, client=None, model: str | None = None) -> None:
        super().__init__(tools, AnthropicClaude(client, model))
        self.model = self.backend.model

    @staticmethod
    def available() -> bool:
        return live_llm_available()
