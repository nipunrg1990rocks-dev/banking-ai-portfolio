"""Back-test the Risk Insights Agent on every business day. See riskdash/evaluation.py."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

from riskdash.engine import DuckDBEngine  # noqa: E402
from riskdash.evaluation import format_report, run_evaluation  # noqa: E402
from riskdash.metrics import MetricStore  # noqa: E402
from riskdash.synthetic import generate, load_p2_gold  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Back-test the Risk Insights Agent against the planted incidents.")
    parser.add_argument("--out", type=Path, help="write the Markdown report and JSON results here")
    args = parser.parse_args()

    gold = load_p2_gold()
    report = run_evaluation(MetricStore(DuckDBEngine(generate(gold), gold)))
    text = format_report(report)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "backtest_report.md").write_text(text + "\n", encoding="utf-8")
        (args.out / "backtest_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["recall"] == 1 and not report["wrong_root_causes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
