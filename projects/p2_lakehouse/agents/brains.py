"""Brains: what decides the next step.

* ``ClaudeBrain``   Claude through the Anthropic API with native tool use. The model
  reads tool results, decides what to check next, recovers from errors (for
  example a SQL mistake) and writes the final report.
* ``ScriptedBrain`` the offline planner: a fixed investigation playbook written in
  Python, with rule-based scoring. It uses the *same* tools through the *same*
  engine, so the hosted app works with no API key and no cost. It is not an LLM,
  and the app says so wherever its output is shown.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

from portfolio_core.llm import LLMUnavailable, resolve_api_key, resolve_model

from .engine import AgentContext, compact

# USD per million tokens (input, output), Anthropic first-party pricing.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}

Policy = Callable[[AgentContext], Generator[tuple, dict, None]]


class ScriptedBrain:
    name = "offline-planner"
    model = None

    def __init__(self, policies: dict[str, Policy]) -> None:
        self.policies = policies

    def run(self, ctx: AgentContext) -> None:
        """Drive the agent's playbook: each yielded ('think', text) or ('tool', name, args) becomes a step."""
        gen = self.policies[ctx.spec.name](ctx)
        try:
            action = next(gen)
            while True:
                if action[0] == "think":
                    ctx.think(action[1])
                    action = gen.send({})
                else:
                    result, _ = ctx.call_tool(action[1], action[2])
                    action = gen.send(result)
        except StopIteration:
            return


class ClaudeBrain:
    name = "claude"

    def __init__(self, model: str | None = None, effort: str = "medium", client=None, api_key: str | None = None):
        self.model = model or resolve_model()
        self.effort = effort
        if client is not None:
            self.client = client
            return
        key = api_key or resolve_api_key()
        if not key:
            raise LLMUnavailable("No ANTHROPIC_API_KEY configured; live agents are off.")
        import anthropic

        self.client = anthropic.Anthropic(api_key=key, timeout=180.0, max_retries=2)

    def _cost(self, usage) -> tuple[int, int, float]:
        fresh = getattr(usage, "input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        written = getattr(usage, "cache_creation_input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        price_in, price_out = PRICES.get(self.model, PRICES["claude-opus-5-5"])
        # Cache reads bill at 0.05x on Opus 5.5 (0.1x elsewhere) and 5-minute cache writes at 1.25x.
        read_rate = 0.05 if self.model == "claude-opus-5-5" else 0.1
        dollars = (fresh * price_in + read * price_in * read_rate + written * price_in * 1.25 + out * price_out) / 1e6
        return fresh + read + written, out, dollars

    def run(self, ctx: AgentContext) -> None:
        system = ctx.spec.system + "\n\n" + ctx.context.get("system_suffix", "")
        messages: list[dict] = [{"role": "user", "content": ctx.goal}]
        nudged = False
        for _ in range(ctx.spec.max_tool_calls + 4):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                tools=ctx.tool_schemas(),
                messages=messages,
                cache_control={"type": "ephemeral"},  # each turn re-reads the growing history from cache
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            ctx.add_usage(*self._cost(response.usage))
            if response.stop_reason == "refusal":
                raise RuntimeError("The model declined this request.")
            for block in response.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    ctx.think(block.thinking)
                elif block.type == "text":
                    ctx.think(block.text)
            # Append the assistant turn unchanged (thinking blocks included) so the history stays valid.
            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "max_tokens" and not calls:
                raise RuntimeError("The model ran out of output tokens.")
            if not calls:
                if ctx.done or nudged:
                    return
                nudged = True
                messages.append({"role": "user", "content": f"Finish now by calling {ctx.spec.submit_tool}."})
                continue
            results = []
            for call in calls:
                result, ok = ctx.call_tool(call.name, dict(call.input))
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": compact(result),
                                **({} if ok else {"is_error": True})})
            messages.append({"role": "user", "content": results})
            if ctx.done:
                return
