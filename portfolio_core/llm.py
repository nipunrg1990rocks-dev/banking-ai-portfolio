"""Language-model access shared by the portfolio's GenAI projects.

Two modes:

* ``offline`` (default): no external call, no cost. Projects fall back to a
  deterministic method and must label the output as not LLM-generated.
* ``anthropic``: calls Claude through the official Anthropic SDK. Enabled only
  when an ``ANTHROPIC_API_KEY`` is present in the environment or in Streamlit
  secrets. The key is never logged or displayed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = "claude-opus-5-5"


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMUnavailable(RuntimeError):
    pass


def _streamlit_secret(name: str) -> str | None:
    try:
        import streamlit as st  # imported lazily so non-UI code has no Streamlit dependency

        return st.secrets.get(name)  # type: ignore[no-any-return]
    except Exception:
        return None


def resolve_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or _streamlit_secret("ANTHROPIC_API_KEY")


def resolve_model() -> str:
    return os.environ.get("PORTFOLIO_LLM_MODEL") or _streamlit_secret("PORTFOLIO_LLM_MODEL") or DEFAULT_MODEL


def live_llm_available() -> bool:
    return bool(resolve_api_key())


class ClaudeClient:
    """Thin wrapper so projects (and tests) depend on one small interface."""

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None) -> None:
        self.model = model or resolve_model()
        if client is not None:
            self._client = client
            return
        key = api_key or resolve_api_key()
        if not key:
            raise LLMUnavailable("No ANTHROPIC_API_KEY configured; running in offline mode.")
        import anthropic

        self._client = anthropic.Anthropic(api_key=key, timeout=60.0, max_retries=2)

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> LLMResponse:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user}],
        )
        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMUnavailable("The model declined this request.")
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text.strip(),
            model=self.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
