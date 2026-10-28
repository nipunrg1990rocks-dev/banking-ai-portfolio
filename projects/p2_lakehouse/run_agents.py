"""Run the AI agents against the snapshot, score them against the answer key, and record the runs.

    python projects/p2_lakehouse/run_agents.py                 # offline planner, free
    python projects/p2_lakehouse/run_agents.py --brain claude  # Claude via the Anthropic API (needs ANTHROPIC_API_KEY)

With ``--brain claude --record``, every run (thoughts, tool calls, results, tokens,
cost) is saved under ``data/agent_runs/`` and the evaluation under
``data/agent_runs/eval_llm.json``. The hosted app replays those recordings, so
visitors see exactly what Claude did without the app making any API calls.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

from agents import (  # noqa: E402
    ClaudeBrain,
    SnapshotBackend,
    recordings,  # noqa: E402
    run_agent,
)
from agents.evals import ASK_SET, SUGGESTED, evaluate  # noqa: E402


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
        if "ask_ledgerline" in args.agents:
            asked = {c["question"] for c in ASK_SET}
            runs += [run_agent("ask_ledgerline", backend, brain, question=q) for q in SUGGESTED if q not in asked]
        out = recordings.save(runs, results, brain.model, where="Anthropic API")
        print(f"Recorded {len(runs)} runs and {out}")

if __name__ == "__main__":
    main()
