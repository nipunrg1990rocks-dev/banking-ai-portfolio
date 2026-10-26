"""Recorded agent runs: saved once from a real LLM run, replayed by the hosted app at no cost."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .engine import AgentRun

RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "agent_runs"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def recording_path(agent: str, key: str, root: Path = RUNS_DIR) -> Path:
    return root / agent / f"{slug(key)}.json"


def recording_key(run: AgentRun) -> str:
    if run.agent == "aml_investigator":
        return (run.output or {}).get("alert_id", run.goal.split()[3])[:12]
    if run.agent == "data_steward":
        return "incident"
    return run.goal


def save(runs: list[AgentRun], results: list, model: str, root: Path = RUNS_DIR, where: str = "") -> Path:
    """Write every run plus the evaluation report (``eval_llm.json``) under ``root``."""
    for run in runs:
        path = recording_path(run.agent, recording_key(run), root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(run.to_dict(), indent=1, default=str))
    report = {r.agent: {"summary": r.summary(), "cases": r.cases} for r in results}
    report.update(recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), model=model,
                  brain=runs[0].brain if runs else None, where=where,
                  spent_usd=round(sum(r.cost_usd for r in runs), 4))
    out = root / "eval_llm.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    return out
