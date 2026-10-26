"""AI agents that work on top of the Ledgerline lakehouse (see ``specs.py`` for the three agents)."""

from __future__ import annotations

from portfolio_core.audit import AuditLog

from .backend import Backend, SnapshotBackend, SparkBackend
from .brains import ClaudeBrain, ScriptedBrain
from .engine import Agent, AgentRun, AgentSpec, Step
from .specs import POLICIES, SPECS, goal_for
from .tools import LakehouseTools

__all__ = ["Agent", "AgentRun", "AgentSpec", "AuditLog", "Backend", "ClaudeBrain", "LakehouseTools", "SPECS",
           "ScriptedBrain", "SnapshotBackend", "SparkBackend", "Step", "build_agent", "goal_for", "run_agent"]


def build_agent(name: str, backend: Backend, brain=None, audit: AuditLog | None = None) -> Agent:
    """``brain`` is a ClaudeBrain, or None for the offline planner."""
    return Agent(SPECS[name], LakehouseTools(backend), brain or ScriptedBrain(POLICIES), audit)


def run_agent(name: str, backend: Backend, brain=None, audit: AuditLog | None = None, **context) -> AgentRun:
    agent = build_agent(name, backend, brain, audit)
    suffix = (f"SQL dialect: {backend.dialect}. Data covers business dates 2026-09-01 to 2026-10-07; "
              "today is 2026-10-15.")
    return agent.run(goal_for(name, **context), {**context, "system_suffix": suffix})
