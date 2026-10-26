"""Command-line evaluation runner. See copilot/evaluation.py for the metrics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

from copilot.evaluation import format_report, run_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the compliance copilot on the golden set.")
    parser.add_argument("--out", type=Path, help="write the Markdown report and JSON results here")
    parser.add_argument("--mlflow", action="store_true", help="log metrics to MLflow (requires mlflow)")
    args = parser.parse_args()

    report = run_evaluation()
    text = format_report(report)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "eval_report.md").write_text(text + "\n", encoding="utf-8")
        (args.out / "eval_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.mlflow:
        import mlflow

        mlflow.set_experiment("p1-compliance-copilot")
        with mlflow.start_run(run_name="offline-hybrid-retrieval"):
            mlflow.log_params({"retriever": "BM25 + LSA dense, RRF k=60", "top_k": 5, "mode": "offline"})
            mlflow.log_metrics({k: v for k, v in report["metrics"].items() if k != "cases"})
            mlflow.log_dict(report, "eval_results.json")
        print("Logged run to MLflow experiment 'p1-compliance-copilot'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
