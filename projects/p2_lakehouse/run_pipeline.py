"""Run the whole Ledgerline lakehouse locally and export a snapshot for the Streamlit app.

    python projects/p2_lakehouse/run_pipeline.py                 # build into ./.lakehouse_work
    python projects/p2_lakehouse/run_pipeline.py --workdir /tmp/lh --no-snapshot

Needs Java 17+ and the packages in requirements-pipeline.txt (PySpark + Delta Lake).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from lakehouse.pipeline import Pipeline  # noqa: E402
from lakehouse.snapshot import export_snapshot  # noqa: E402
from lakehouse.store import LakehouseConfig, TableStore, get_spark  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workdir", default=str(HERE / ".lakehouse_work"))
    parser.add_argument("--no-snapshot", action="store_true", help="skip writing data/snapshot for the app")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    if workdir.exists():
        shutil.rmtree(workdir)  # a full run always starts from an empty lakehouse
    store = TableStore(get_spark(), LakehouseConfig.local(workdir))
    result = Pipeline(store).run_all()

    print(f"\nRun {result['run_id']} finished in {result['duration_s']}s\n")
    print(f"{'step':<10}{'table':<34}{'batch':<10}{'in':>8}{'written':>9}{'dupes':>7}{'quarantined':>13}")
    for s in result["steps"]:
        print(f"{s['step']:<10}{(s['layer'] + '.' + (s['table_name'] or '-')):<34}{(s['batch_id'] or '-'):<10}"
              f"{s['rows_in'] if s['rows_in'] is not None else '':>8}"
              f"{s['rows_written'] if s['rows_written'] is not None else '':>9}"
              f"{s['duplicates_removed'] if s['duplicates_removed'] is not None else '':>7}"
              f"{s['rows_quarantined'] if s['rows_quarantined'] is not None else '':>13}")
    failed = [d for d in result["dq"] if d["rows_failed"]]
    print(f"\nData-quality checks evaluated: {len(result['dq'])}; with failures: {len(failed)}")
    for d in failed:
        batch = d["batch_id"] or "-"
        print(f"  {batch:<10}{d['table_name']:<22}{d['rule_name']:<26}{d['action']:<6}{d['rows_failed']}")
    if not args.no_snapshot:
        out = export_snapshot(store, result)
        print(f"\nSnapshot for the app written to {out}")


if __name__ == "__main__":
    main()
