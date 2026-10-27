"""Command-line evaluation runner. See regradar/evaluation.py for the metrics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent)]

from regradar.evaluation import format_report, run_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the RegRadar agents against the answer key.")
    parser.add_argument("--out", type=Path, help="write the Markdown report and JSON results here")
    parser.add_argument("--mlflow", action="store_true", help="log metrics to MLflow (requires mlflow)")
    args = parser.parse_args()

    report = run_evaluation()
    text = format_report(report)
    print(text)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "eval_report.md").write_text(text + "\n", encoding="utf-8")
        (args.out / "eval_results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.mlflow:
        import mlflow

        mlflow.set_experiment("p5-regradar")
        with mlflow.start_run(run_name="offline-rules-engine"):
            mlflow.log_params({"retriever": "Project 1 hybrid (BM25 + LSA, RRF)", "mode": report["mode"]})
            mlflow.log_metrics({k: float(v) for k, v in report["metrics"].items() if isinstance(v, (int, float))})
            mlflow.log_dict(report, "eval_results.json")
        print("Logged run to MLflow experiment 'p5-regradar'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
