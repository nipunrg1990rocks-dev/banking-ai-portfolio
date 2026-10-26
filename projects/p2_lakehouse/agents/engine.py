"""The agent loop: a brain decides, tools act, every step is traced and audited.

``Agent.run()`` hands a goal to a *brain* together with a tool belt. The brain
(Claude through the Anthropic API, or the offline planner) decides which tool to
call next. The engine is the part the bank controls:

* it only lets the brain call the tools listed for that agent
* it validates and times every call, and turns tool errors into feedback the
  brain can correct (for example a SQL typo)
* it stops the run after ``max_tool_calls``
* it writes every step to a hash-chained audit log
* it ends the run when the brain calls the agent's single "submit" tool, whose
  output is a recommendation for a human, never an action
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from portfolio_core.audit import AuditLog

from .tools import LakehouseTools, Tool, ToolError, terminal_tools

MAX_RESULT_CHARS = 12_000


@dataclass
class Step:
    kind: str  # thought | tool | final | error | note
    text: str = ""
    tool: str | None = None
    args: dict | None = None
    result: dict | None = None
    ok: bool = True
    ms: int = 0


@dataclass
class AgentRun:
    run_id: str
    agent: str
    goal: str
    brain: str
    model: str | None
    backend: str
    started_at: str
    steps: list[Step] = field(default_factory=list)
    output: dict | None = None
    status: str = "running"  # completed | step_limit | error
    duration_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def tool_calls(self) -> int:
        return sum(1 for s in self.steps if s.kind == "tool")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> AgentRun:
        d = dict(d)
        d["steps"] = [Step(**s) for s in d.get("steps", [])]
        return cls(**d)


@dataclass
class AgentSpec:
    name: str
    title: str
    role: str  # one line for the UI
    system: str
    tools: list[str]
    submit_tool: str
    max_tool_calls: int = 14


class Brain(Protocol):
    name: str
    model: str | None

    def run(self, ctx: AgentContext) -> None: ...


class BudgetExceeded(RuntimeError):
    pass


class AgentContext:
    """What a brain sees: the goal, the tool schemas, and ``call_tool`` / ``think``."""

    def __init__(self, spec: AgentSpec, run: AgentRun, tools: dict[str, Tool], context: dict,
                 audit: AuditLog | None) -> None:
        self.spec, self.run, self.tools, self.context, self.audit = spec, run, tools, context, audit
        self.done = False

    @property
    def goal(self) -> str:
        return self.run.goal

    def tool_schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def think(self, text: str) -> None:
        if text.strip():
            self.run.steps.append(Step(kind="thought", text=text.strip()))

    def call_tool(self, name: str, args: dict) -> tuple[dict, bool]:
        """Run one tool call. Returns (result, ok). Errors come back as results so the brain can recover."""
        if self.done:
            return {"error": "The run is already finished."}, False
        if self.run.tool_calls >= self.spec.max_tool_calls:
            raise BudgetExceeded(f"Stopped after {self.spec.max_tool_calls} tool calls.")
        started = time.time()
        tool = self.tools.get(name)
        try:
            if tool is None:
                raise ToolError(f"Tool {name!r} is not available to this agent.")
            result, ok = tool.fn(**(args or {})), True
        except (ToolError, TypeError) as exc:
            result, ok = {"error": str(exc)}, False
        ms = int((time.time() - started) * 1000)
        self.run.steps.append(Step(kind="tool", tool=name, args=args, result=result, ok=ok, ms=ms))
        if self.audit:
            self.audit.record("agent_tool_call", {"agent": self.spec.name, "tool": name, "args": args, "ok": ok,
                                                  "ms": ms}, actor=f"agent:{self.spec.name}",
                              correlation_id=self.run.run_id)
        if ok and tool is not None and tool.terminal:
            self.run.output = {k: v for k, v in args.items()}
            self.done = True
        return result, ok

    def add_usage(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        self.run.input_tokens += input_tokens
        self.run.output_tokens += output_tokens
        self.run.cost_usd = round(self.run.cost_usd + cost, 5)


class Agent:
    def __init__(self, spec: AgentSpec, tools: LakehouseTools, brain: Brain, audit: AuditLog | None = None) -> None:
        self.spec, self.lakehouse, self.brain, self.audit = spec, tools, brain, audit
        submit = terminal_tools()[spec.submit_tool]
        self.tools = {n: tools.all[n] for n in spec.tools} | {submit.name: submit}

    def run(self, goal: str, context: dict | None = None) -> AgentRun:
        run = AgentRun(run_id=uuid.uuid4().hex[:12], agent=self.spec.name, goal=goal, brain=self.brain.name,
                       model=self.brain.model, backend=self.lakehouse.backend.name,
                       started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        ctx = AgentContext(self.spec, run, self.tools, context or {}, self.audit)
        if self.audit:
            self.audit.record("agent_run_started", {"agent": self.spec.name, "goal": goal, "brain": self.brain.name,
                                                    "model": self.brain.model}, actor=f"agent:{self.spec.name}",
                              correlation_id=run.run_id)
        started = time.time()
        try:
            self.brain.run(ctx)
            run.status = "completed" if run.output else "error"
            if not run.output:
                run.steps.append(Step(kind="error", text="The agent finished without submitting a result.", ok=False))
        except BudgetExceeded as exc:
            run.status = "step_limit"
            run.steps.append(Step(kind="error", text=str(exc), ok=False))
        except Exception as exc:  # noqa: BLE001 - surface any brain/API failure in the trace instead of crashing
            run.status = "error"
            run.steps.append(Step(kind="error", text=f"{type(exc).__name__}: {exc}"[:500], ok=False))
        run.duration_s = round(time.time() - started, 2)
        if run.output:
            run.steps.append(Step(kind="final", text=json.dumps(run.output)[:2000]))
        if self.audit:
            self.audit.record("agent_run_finished", {"agent": self.spec.name, "status": run.status,
                                                     "output": run.output, "tool_calls": run.tool_calls,
                                                     "cost_usd": run.cost_usd}, actor=f"agent:{self.spec.name}",
                              correlation_id=run.run_id)
        return run


def compact(result: dict) -> str:
    """Tool results go back to the model as JSON, capped so one big result can't flood the context."""
    text = json.dumps(result, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + ' ..."(truncated)"'
