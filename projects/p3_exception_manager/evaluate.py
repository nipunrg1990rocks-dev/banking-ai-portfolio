"""Command-line evaluation runner. See opsagent/evaluation.py for what is measured."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

from opsagent.evaluation import format_report, run_all  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the agentic exception manager.")
    parser.add_argument("--out", type=Path, help="write the Markdown report and JSON results here")
    args = parser.parse_args()
    report = run_all()
    text = format_report(report)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "eval_report.md").write_text(text + "\n", encoding="utf-8")
        (args.out / "eval_results.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    violations = report["fuzz"]["violations"] or report["scenarios"]["metrics"]["regulated_auto_executed"]
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
