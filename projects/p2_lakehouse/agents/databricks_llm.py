"""Reach a Databricks Foundation Model endpoint from a notebook, with the notebook's own credentials.

No API key or token is stored anywhere: inside a Databricks notebook the SDK picks up
the signed-in user's identity. Which pay-per-token models a workspace offers varies
(Free Edition says only that "certain models" are not available), so ``pick_endpoint``
lists the chat endpoints and keeps the first candidate that completes a one-tool probe.
"""

from __future__ import annotations

import json

from .brains import OpenAIToolBrain

# Tried in this order; the first one that answers a tool call is used.
CANDIDATES = [
    "databricks-claude-sonnet-4-5",
    "databricks-claude-sonnet-4",
    "databricks-claude-3-7-sonnet",
    "databricks-meta-llama-3-3-70b-instruct",
    "databricks-llama-4-maverick",
    "databricks-gpt-oss-120b",
    "databricks-qwen3-next-80b-a3b-instruct",
]

PROBE_TOOL = {"type": "function", "function": {
    "name": "record_total", "description": "Record the total of the numbers the user gives.",
    "parameters": {"type": "object", "properties": {"total": {"type": "number"}}, "required": ["total"],
                   "additionalProperties": False}}}


def workspace_client():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


def openai_client(w=None):
    """An ``openai.OpenAI`` client pointed at the workspace's serving endpoints."""
    w = w or workspace_client()
    try:
        return w.serving_endpoints.get_open_ai_client()
    except Exception:  # noqa: BLE001 - older SDKs lack it; build the same client by hand
        pass
    from openai import OpenAI

    token = w.config.authenticate()["Authorization"].split(" ", 1)[1]
    return OpenAI(api_key=token, base_url=f"{w.config.host.rstrip('/')}/serving-endpoints")


def chat_endpoints(w=None) -> list[str]:
    w = w or workspace_client()
    names = []
    try:
        endpoints = list(w.serving_endpoints.list())
    except Exception:  # noqa: BLE001 - listing can be restricted; pick_endpoint then tries every candidate
        return []
    for ep in endpoints:
        task = (getattr(ep, "task", None) or "").lower()
        if not task or "chat" in task:
            names.append(ep.name)
    return names


def probe(client, endpoint: str) -> tuple[bool, str]:
    """True if the endpoint answers with a well-formed call to the probe tool."""
    try:
        r = client.chat.completions.create(
            model=endpoint, temperature=0, max_tokens=200, tools=[PROBE_TOOL],
            messages=[{"role": "user", "content": "Add 17 and 25, then call record_total with the result."}])
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable here"; the reason is reported
        return False, str(exc).split("\n")[0][:200]
    calls = r.choices[0].message.tool_calls or []
    if not calls:
        return False, "answered without calling the tool"
    try:
        total = json.loads(calls[0].function.arguments).get("total")
    except ValueError:
        return False, "tool arguments were not valid JSON"
    return (total == 42, "ok" if total == 42 else f"wrong total {total}")


def pick_endpoint(client, available: list[str], preferred: str = "") -> tuple[str | None, list[tuple[str, str]]]:
    """Returns (endpoint or None, [(endpoint, outcome), ...]) for every endpoint tried."""
    order = [preferred] if preferred else []
    # If the workspace wouldn't list its endpoints, try every candidate anyway.
    order += [c for c in CANDIDATES if (c in available or not available) and c != preferred]
    tried = []
    for name in order:
        ok, why = probe(client, name)
        tried.append((name, why))
        if ok:
            return name, tried
    return None, tried


def databricks_brain(preferred: str = "") -> tuple[OpenAIToolBrain | None, list[tuple[str, str]]]:
    w = workspace_client()
    client = openai_client(w)
    endpoint, tried = pick_endpoint(client, chat_endpoints(w), preferred)
    return (OpenAIToolBrain(client, endpoint) if endpoint else None), tried
