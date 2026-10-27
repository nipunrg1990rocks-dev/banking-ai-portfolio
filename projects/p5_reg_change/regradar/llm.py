"""Live AI: a Databricks foundation model as reviewer, writer and question-answering agent.

Nothing here is needed for the app to work. With no Databricks connection the
pipeline runs fully offline and every output says so. When connected:

* **Reviewer** (inside the Challenger agent): the model reads the regulatory
  sentence, the candidate policy sections and the rules engine's verdict, and
  returns its own verdict as JSON. If the two disagree the finding is sent to
  human review; the model never overrides the rules engine by itself.
* **Writer**: drafts the executive summary. Every number in the draft must
  appear in the findings, or the template summary is used instead.
* **Ask RegRadar**: a tool-calling agent that answers questions by calling
  read-only tools over the assessment (changes, findings, policies, controls).

Backend: a Databricks Model Serving foundation-model endpoint, called over its
OpenAI-compatible REST API. On Databricks Free Edition these endpoints are part
of the workspace (no API key, no bill). The app reads the workspace host and a
token from Streamlit secrets; the notebook uses its own credentials. Secrets
are never displayed or logged.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

CANDIDATES = ("databricks-meta-llama-3-3-70b-instruct", "databricks-claude-sonnet-4-5", "databricks-claude-sonnet-4",
              "databricks-llama-4-maverick", "databricks-gpt-oss-120b", "databricks-qwen3-next-80b-a3b-instruct")
SECRET_NAMES = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_LLM_ENDPOINT")


class LLMError(RuntimeError):
    pass


def _secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st

        return st.secrets.get(name)  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001 - no secrets file outside Streamlit
        return None


def databricks_configured() -> bool:
    return bool(_secret("DATABRICKS_HOST") and _secret("DATABRICKS_TOKEN"))


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class DatabricksLLM:
    """Chat completions (optionally with tools) against one serving endpoint."""

    def __init__(self, host: str, token: str, endpoint: str, http=None, timeout: float = 90.0) -> None:
        self.host = "https://" + host.replace("https://", "").replace("http://", "").rstrip("/")
        self._token = token
        self.endpoint = endpoint
        self.label = f"Databricks · {endpoint}"
        self._http = http or self._urllib
        self.timeout = timeout
        self.usage = Usage()

    def __repr__(self) -> str:  # never show the token
        return f"DatabricksLLM(host={self.host!r}, endpoint={self.endpoint!r})"

    def _urllib(self, method: str, url: str, body: dict | None) -> dict:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {self._token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - fixed https host
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise LLMError(f"Databricks returned HTTP {exc.code} for {self.endpoint}") from None
        except urllib.error.URLError as exc:
            raise LLMError(f"Could not reach the Databricks workspace: {exc.reason}") from None

    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 1200,
             temperature: float = 0.0) -> dict:
        body: dict = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            body["tools"] = tools
        out = self._http("POST", f"{self.host}/serving-endpoints/{self.endpoint}/invocations", body)
        try:
            msg = out["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("Unexpected response shape from the serving endpoint.") from None
        usage = out.get("usage") or {}
        self.usage.calls += 1
        self.usage.input_tokens += int(usage.get("prompt_tokens") or 0)
        self.usage.output_tokens += int(usage.get("completion_tokens") or 0)
        return msg

    def complete(self, system: str, user: str, max_tokens: int = 1200) -> str:
        msg = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                        max_tokens=max_tokens)
        return text_of(msg.get("content"))

    @classmethod
    def discover(cls, host: str, token: str, preferred: str | None = None, http=None) -> DatabricksLLM:
        """The preferred endpoint if set; otherwise the first candidate in the workspace that answers a probe."""
        if preferred:
            return cls(host, token, preferred, http=http)
        lister = cls(host, token, CANDIDATES[0], http=http)
        try:
            listed = lister._http("GET", f"{lister.host}/api/2.0/serving-endpoints", None)
            available = {e.get("name") for e in listed.get("endpoints", [])}
        except LLMError:
            available = set()
        errors = []
        for name in [c for c in CANDIDATES if c in available or not available]:
            candidate = cls(host, token, name, http=http)
            try:
                if "OK" in candidate.complete("Reply with the single word OK.", "Ping", max_tokens=5).upper():
                    return candidate
                errors.append(f"{name}: unexpected reply")
            except LLMError as exc:
                errors.append(f"{name}: {exc}")
        raise LLMError("No usable foundation-model endpoint found. Tried: " + "; ".join(errors or ["none"]))


def from_secrets(http=None) -> DatabricksLLM:
    return DatabricksLLM.discover(_secret("DATABRICKS_HOST") or "", _secret("DATABRICKS_TOKEN") or "",
                                  _secret("DATABRICKS_LLM_ENDPOINT"), http=http)


def text_of(content) -> str:
    """Message text; some endpoints return a list of content parts instead of a string."""
    if isinstance(content, str):
        return content.strip()
    parts = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") in (None, "text"):
            parts.append(part.get("text", ""))
    return "".join(parts).strip()


def parse_json(text: str) -> dict:
    """The first JSON object in a model reply (models often wrap JSON in prose or code fences)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise LLMError("The model did not return JSON.")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"The model returned invalid JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise LLMError("The model returned JSON that is not an object.")
    return data


# ------------------------------------------------------------------ grounding
_NUMBER = re.compile(r"(?<![\w§.-])\d[\d,]*(?:\.\d+)?(?![\w-])")
_SKIP = re.compile(r"\b(?:NPCA|AML|KYC|MRM|ORM|TPR|REC|SAN|RAS|CTL|F|A)-[\w-]+(?: §\d+(?:\.\d+)?)?|§\s?\d+(?:\.\d+)?|"
                   r"\b\d{4}-\d{2}-\d{2}\b")


def numbers_in(text: str) -> list[str]:
    return _NUMBER.findall(_SKIP.sub(" ", text))


def grounding_check(text: str, sources: list[str]) -> tuple[bool, list[str]]:
    """Every number in ``text`` must appear in ``sources``. Small counts (0-10) are allowed in prose."""
    known = set()
    for s in sources:
        for n in numbers_in(s):
            known.add(n.replace(",", ""))
    bad = []
    for n in numbers_in(text):
        v = n.replace(",", "")
        if v in known:
            continue
        try:
            if float(v).is_integer() and 0 <= float(v) <= 10:
                continue
        except ValueError:
            pass
        bad.append(n)
    return not bad, bad


# ------------------------------------------------------------------ reviewer
REVIEW_SYSTEM = """You are a second-line compliance reviewer at Ledgerline Bank (a fictional bank; all data is synthetic).
You check whether an internal policy meets a regulatory requirement. Use ONLY the texts provided.
Statuses:
- COVERED: the policy already meets the requirement (equal or stricter figures, same scope and audience).
- CONFLICT: the policy sets a weaker figure for the same rule.
- PARTIAL: the policy covers the topic but misses part of the requirement (a figure, a scope item or the audience).
- MISSING: no provided policy section addresses the requirement.
Reply with JSON only: {"status": "...", "policy_ref": "<one of the provided refs or null>", "rationale": "<max 60 words, quote the figures>"}"""


@dataclass
class ReviewOpinion:
    status: str
    policy_ref: str | None
    rationale: str
    grounded: bool
    agrees: bool
    model: str
    error: str | None = None
    unsupported: list[str] = field(default_factory=list)


def review_finding(llm, finding) -> ReviewOpinion:
    sections = []
    for h in finding.hits[:3]:
        sections.append(f"[{h.section.ref}] {h.section.heading}: {h.section.text}")
    user = (f"Regulatory requirement [{finding.provision.pid}]: {finding.provision.text}\n\n"
            + "Candidate policy sections:\n" + "\n".join(sections)
            + f"\n\nThe rules engine says: {finding.status} ({'; '.join(finding.rationale)})\n"
            "Give your own independent verdict.")
    try:
        data = parse_json(llm.complete(REVIEW_SYSTEM, user, max_tokens=400))
    except LLMError as exc:
        return ReviewOpinion(finding.status, None, "", False, True, getattr(llm, "label", "LLM"), error=str(exc))
    status = str(data.get("status", "")).upper().strip()
    ref = data.get("policy_ref")
    rationale = str(data.get("rationale", ""))[:600]
    allowed_refs = {h.section.ref for h in finding.hits[:3]}
    if status not in ("COVERED", "CONFLICT", "PARTIAL", "MISSING"):
        return ReviewOpinion(finding.status, None, rationale, False, True, llm.label,
                             error=f"invalid status {status!r}")
    if ref not in allowed_refs:
        ref = None
    ok, bad = grounding_check(rationale, [finding.provision.text] + [h.section.text for h in finding.hits[:3]])
    return ReviewOpinion(status, ref, rationale, ok, status == finding.status, llm.label, unsupported=bad)


SUMMARY_SYSTEM = """You write the executive summary of a regulatory change impact assessment for Ledgerline Bank's
Regulatory Change Committee (fictional bank, synthetic data). Use ONLY the facts provided. Quote figures exactly.
At most 140 words: one opening sentence, then 3 bullets for the most material gaps, then one sentence on next steps.
Say that actions are drafts awaiting sign-off. Do not invent dates, numbers or policy names."""


def draft_summary(llm, facts: str) -> tuple[str | None, list[str]]:
    """LLM summary if it is grounded in ``facts``; otherwise (None, unsupported numbers)."""
    try:
        text = llm.complete(SUMMARY_SYSTEM, facts, max_tokens=500)
    except LLMError as exc:
        return None, [str(exc)]
    ok, bad = grounding_check(text, [facts])
    return (text if ok and text else None), bad
