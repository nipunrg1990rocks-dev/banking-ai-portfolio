"""Run the AI agents against the snapshot, score them against the answer key, and record the runs.

    python projects/p2_lakehouse/run_agents.py                 # offline planner, free
    python projects/p2_lakehouse/run_agents.py --brain claude  # Claude via the Anthropic API (needs ANTHROPIC_API_KEY)

With ``--brain claude --record``, every run (thoughts, tool calls, results, tokens,
cost) is saved under ``data/agent_runs/`` and the evaluation under
``data/agent_runs/eval_claude.json``. The hosted app replays those recordings, so
visitors see exactly what Claude did without the app making any API calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

from agent_ui import RUNS_DIR, SUGGESTED, recording_path  # noqa: E402
from agents import ClaudeBrain, SnapshotBackend, run_agent  # noqa: E402
from agents.evals import ASK_SET, evaluate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--brain", choices=["offline", "claude"], default="offline")
    parser.add_argument("--model", default=None, help="Claude model id (default: claude-opus-5-5)")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--agents", default="aml_investigator,data_steward,ask_ledgerline")
    parser.add_argument("--record", action="store_true", help="save runs for the app to replay")
    parser.add_argument("--budget-usd", type=float, default=5.0, help="stop recording if spend passes this")
    args = parser.parse_args()

    backend = SnapshotBackend()
    brain = ClaudeBrain(model=args.model, effort=args.effort) if args.brain == "claude" else None
    runs: list = []
    results = evaluate(backend, brain, agents=tuple(args.agents.split(",")), runs_out=runs)
    spent = sum(r.cost_usd for r in runs)

    for res in results:
        sm = res.summary()
        print(f"\n{sm['agent']} ({sm['brain']}): {sm['passed']}/{sm['cases']} cases fully correct")
        for k, v in sm["checks"].items():
            print(f"  {k:<26}{v}")
        for c in res.cases:
            if not c["pass"]:
                print(f"  FAILED {c['case']}: " + ", ".join(k for k, v in c["checks"].items() if not v))
    print(f"\n{len(runs)} agent runs, {sum(r.tool_calls for r in runs)} tool calls, ${spent:.3f} spent")
    if spent > args.budget_usd:
        print(f"Spend ${spent:.2f} is over the ${args.budget_usd:.2f} budget; not recording.")
        return

    if args.record and args.brain == "claude":
        ask_questions = {c["question"] for c in ASK_SET}
        for q in SUGGESTED:  # the app's suggestion buttons should all have a recording
            if q not in ask_questions and "ask_ledgerline" in args.agents:
                runs.append(run_agent("ask_ledgerline", backend, brain, question=q))
                ask_questions.add(q)
        for run in runs:
            if run.agent == "aml_investigator":
                key = run.output["alert_id"][:12] if run.output else run.goal.split()[3][:12]
            elif run.agent == "data_steward":
                key = "incident"
            else:
                key = next((q for q in ask_questions if q == run.goal), run.goal)
            path = recording_path(run.agent, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(run.to_dict(), indent=1, default=str))
        report = {r.agent: {"summary": r.summary(), "cases": r.cases} for r in results}
        report["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        report["model"] = brain.model
        report["spent_usd"] = round(spent, 4)
        (RUNS_DIR / "eval_claude.json").write_text(json.dumps(report, indent=1))
        print(f"Recorded {len(runs)} runs to {RUNS_DIR}")


if __name__ == "__main__":
    main()
