"""Run the CapitalFlow 360 lakehouse end to end on local Spark + Delta Lake and write evidence.

    python lakehouse/run_local.py            # from projects/p7_capitalflow360
    python lakehouse/run_local.py --import-to http://localhost:8000   # also load evidence into the platform

What it proves (each check is recorded in lakehouse/evidence/local_run.json with PASS/FAIL):
  1. Baseline batch: Excel -> Parquet landing -> Bronze -> Silver -> Gold; 500 trades split 140/120/80/70/50/40.
  2. Silver trades agree with the independent oracle (data/oracle/expected_trades.csv) and with the platform DB.
  3. Restartability: re-running the same batch ingests 0 new Bronze rows and changes no Silver row.
  4. Negative batch: schema drift is refused at landing (DQ05); exactly the cases labelled DQ_QUARANTINE are
     quarantined, no API-layer case is; the batch fails the quality gate and Silver is left unchanged.
  5. Full rebuild from the landing zone gives a Silver content hash identical to the incremental run.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lakehouse"))

from cf360_lake import contract as C  # noqa: E402
from cf360_lake import pipeline as P  # noqa: E402
from cf360_lake.convert import convert  # noqa: E402
from cf360_lake.store import Store  # noqa: E402

WORK = ROOT / "lakehouse" / ".work"
EVIDENCE = ROOT / "lakehouse" / "evidence"
ENV = "local-spark"
EXPECTED_GROUPS = {"EQUITY": 140, "BOND": 120, "FX": 80, "LISTED_DERIV": 70, "IRS": 50, "FINANCING": 40}


def spark_session():
    from delta import configure_spark_with_delta_pip
    from pyspark.sql import SparkSession
    b = (SparkSession.builder.appName("cf360-lakehouse").master("local[2]")
         .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
         .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
         .config("spark.sql.shuffle.partitions", "4").config("spark.sql.session.timeZone", "UTC")
         .config("spark.ui.enabled", "false").config("spark.driver.memory", "2g"))
    spark = configure_spark_with_delta_pip(b).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def silver_hash(spark, store: Store) -> dict:
    """Content hash per Silver table over business columns only (ingest timestamps excluded)."""
    from pyspark.sql import functions as F
    out = {}
    for t in P.SILVER:
        if not store.exists(spark, f"silver_{t}"):
            continue
        df = store.read(spark, f"silver_{t}")
        out[t] = df.select(F.sha2(F.concat_ws("|", F.sort_array(F.collect_list("_row_hash"))), 256).alias("h"),
                           F.count("*").alias("n")).first().asDict()
    return out


def last_merge_metrics(spark, store: Store, table: str) -> dict:
    h = store.delta(spark, f"silver_{table}").history(1).collect()[0]
    return {"operation": h["operation"], **{k: int(v) for k, v in (h["operationMetrics"] or {}).items()
                                            if k in ("numTargetRowsInserted", "numTargetRowsUpdated", "numTargetRowsDeleted")}}


def check(results: list, name: str, ok: bool, detail) -> None:
    results.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {json.dumps(detail, default=str)[:300]}")


def platform_trades() -> dict | None:
    try:
        import psycopg
        url = os.environ.get("CF360_PG_DSN", "dbname=cf360 user=cf360 password=cf360 host=/tmp")
        with psycopg.connect(url, connect_timeout=3) as con:
            rows = con.execute("select trade_id, quantity, price from trades where sandbox_id is null and trade_id like 'TRD%'").fetchall()
        return {r[0]: (int(r[1]), float(r[2])) for r in rows}
    except Exception as e:  # the platform DB is optional for this run
        print("platform DB not reachable, skipping that cross-check:", e)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--import-to", help="platform base URL to import job runs and DQ results into")
    args = ap.parse_args()
    started = time.time()
    shutil.rmtree(WORK, ignore_errors=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    contract = C.build_contract(ROOT / "data" / "excel")
    C.save(contract, ROOT / "lakehouse" / "contract.json")
    store = Store(landing=str(WORK / "landing"), checkpoints=str(WORK / "checkpoints"), tables=str(WORK / "tables"))
    spark = spark_session()
    from pyspark.sql import functions as F
    log, dq, results = [], [], []

    # 1. baseline -------------------------------------------------------------------------------
    m1 = convert(sorted((ROOT / "data" / "excel").glob("*.xlsx")), Path(store.landing), "BASELINE-001", contract)
    check(results, "baseline landing: every sheet matches the contract", not m1["rejected"],
          {"tables": len(m1["tables"]), "rejected": m1["rejected"]})
    r1 = P.run_batch(spark, store, contract, "BASELINE-001", ENV, log, dq)
    check(results, "baseline batch passes the quality gate", r1["status"] == "SUCCEEDED",
          {t["table"]: [t["rows_in"], t["rows_out"], t["rows_quarantined"]] for t in r1["tables"]})
    t0 = P.now()
    g = P.gold(spark, store)
    log.append(P._job(ENV, "gold", "BASELINE-001", t0, g["gold_trade_lifecycle"], sum(g.values()), 0, g))
    trades = store.read(spark, "silver_trades")
    groups = {r["product_group"]: r["count"] for r in trades.groupBy("product_group").count().collect()}
    check(results, "silver_trades holds 500 trades split 140/120/80/70/50/40", groups == EXPECTED_GROUPS and trades.count() == 500, groups)
    life = store.read(spark, "gold_trade_lifecycle")
    not_conserved = life.where("allocation_count > 0 AND NOT allocation_conserved").count()
    check(results, "gold: allocated quantity equals block quantity for every allocated trade", not_conserved == 0,
          {"trades_with_allocations": life.where("allocation_count > 0").count(), "not_conserved": not_conserved})

    # 2. cross-checks ---------------------------------------------------------------------------
    oracle_ids = {r["trade_id"]: r for r in csv.DictReader(open(ROOT / "data" / "oracle" / "expected_trades.csv"))}
    silver = {r["trade_id"]: r for r in trades.select("trade_id", "settlement_date", "settle_ccy").collect()}
    mism = [t for t, o in oracle_ids.items() if t not in silver or str(silver[t]["settlement_date"]) != o["settlement_date"]
            or silver[t]["settle_ccy"] != o["settle_ccy"]]
    check(results, "silver trades match the oracle (ids, settlement date, settlement currency)",
          not mism and set(oracle_ids) == set(silver), {"oracle": len(oracle_ids), "silver": len(silver), "mismatches": mism[:10]})
    plat = platform_trades()
    if plat is not None:
        sq = {r["trade_id"]: (int(r["quantity"]), float(r["price"])) for r in trades.select("trade_id", "quantity", "price").collect()}
        diff = [t for t in sq if plat.get(t) != sq[t]]
        check(results, "silver trades match the platform database (ids, quantity, price)", not diff and set(plat) == set(sq),
              {"platform": len(plat), "silver": len(sq), "differences": diff[:10]})

    # 3. restartability -------------------------------------------------------------------------
    h1 = silver_hash(spark, store)
    r_again = P.run_batch(spark, store, contract, "BASELINE-001", ENV, log, dq)
    bronze_new = log[-2]["rows_in"]
    merges = {t: last_merge_metrics(spark, store, t) for t in ("trades", "allocations", "clients")}
    changed = sum(v.get("numTargetRowsInserted", 0) + v.get("numTargetRowsUpdated", 0) for v in merges.values())
    check(results, "re-running the same batch ingests 0 new bronze rows", bronze_new == 0, {"bronze_rows_ingested": bronze_new})
    check(results, "re-running the same batch changes no silver row (MERGE is idempotent)",
          silver_hash(spark, store) == h1 and changed == 0 and r_again["status"] == "SUCCEEDED", merges)

    # 4. negative batch -------------------------------------------------------------------------
    m2 = convert(sorted((ROOT / "data" / "negative").glob("*.xlsx")), Path(store.landing), "NEGATIVE-001", contract)
    drift = [r for r in m2["rejected"] if r["file"] == "Trades_schema_drift.xlsx"]
    check(results, "schema drift is refused whole at landing (DQ05)", len(drift) == 1 and drift[0]["rule_id"] == "DQ05", drift)
    r2 = P.run_batch(spark, store, contract, "NEGATIVE-001", ENV, log, dq)
    labels = list(csv.DictReader(open(ROOT / "data" / "oracle" / "negative_labels.csv")))
    want = {r["negative_case_id"] for r in labels if r["expected_layer"] == "DQ_QUARANTINE" and r["defect_type"] != "N25"}
    q = store.read(spark, "silver_quarantine").where(F.col("batch_id") == "NEGATIVE-001")
    got_rows = q.where(F.col("table_name") == "trades").select("negative_case_id", "rule_id", "error_code").collect()
    got = {r["negative_case_id"] for r in got_rows}
    lab = {r["negative_case_id"]: r for r in labels}
    wrong_code = [r["negative_case_id"] for r in got_rows if r["negative_case_id"] in want
                  and (lab[r["negative_case_id"]]["rule_id"], lab[r["negative_case_id"]]["expected_error"]) != (r["rule_id"], r["error_code"])]
    check(results, "quarantine catches every case labelled DQ_QUARANTINE, with the labelled rule and error", want <= got and not wrong_code,
          {"labelled": len(want), "caught": len(want & got), "missed": sorted(want - got), "wrong_rule_or_error": wrong_code})
    check(results, "quarantine catches no case the labels leave to the API layer", got <= want, {"unexpected": sorted(got - want)})
    check(results, "negative batch fails the quality gate and is held back", r2["status"] == "GATE_FAILED", r2["tables"])
    check(results, "silver is unchanged after the held negative batch", silver_hash(spark, store) == h1,
          {"silver_trades": store.read(spark, "silver_trades").count()})
    P.gold(spark, store)  # refresh gold_dq_summary with the negative batch's quarantine

    # 5. full rebuild ---------------------------------------------------------------------------
    for d in ("tables", "checkpoints"):
        shutil.rmtree(WORK / d, ignore_errors=True)
    rebuild_log: list = []
    P.run_batch(spark, store, contract, "BASELINE-001", ENV + "-rebuild", rebuild_log, [])
    P.run_batch(spark, store, contract, "NEGATIVE-001", ENV + "-rebuild", rebuild_log, [])
    h2 = silver_hash(spark, store)
    check(results, "full rebuild from the landing zone reproduces silver exactly", h2 == h1,
          {t: v["n"] for t, v in h2.items()})
    P.gold(spark, store)

    spark.stop()
    summary = {"environment": ENV, "spark": __import__("pyspark").__version__, "delta_spark": _ver("delta-spark"),
               "elapsed_seconds": round(time.time() - started, 1), "checks": results,
               "passed": sum(r["result"] == "PASS" for r in results), "failed": sum(r["result"] == "FAIL" for r in results),
               "silver_hash": h1, "landing_manifests": [m1, m2], "gold": g}
    (EVIDENCE / "local_run.json").write_text(json.dumps(summary, indent=1, default=str))
    (EVIDENCE / "job_runs.json").write_text(json.dumps({"job_runs": log, "dq_results": dq}, indent=1, default=str))
    print(f"\n{summary['passed']} passed, {summary['failed']} failed in {summary['elapsed_seconds']}s")
    print("DQ rules:", dict(Counter((d["rule_id"]) for d in dq)))
    if args.import_to:
        import_evidence(args.import_to, {"job_runs": log, "dq_results": dq})
    return 0 if summary["failed"] == 0 else 1


def _ver(pkg: str) -> str:
    from importlib.metadata import version
    return version(pkg)


def import_evidence(base: str, body: dict) -> None:
    import httpx
    with httpx.Client(base_url=base, timeout=30) as c:
        tok = c.post("/api/auth/login", json={"username": os.environ.get("CF360_ADMIN_USER", "admin1"), "password": os.environ.get("CF360_DEMO_PASSWORD", "Demo!2026")}).json()
        r = c.post("/api/admin/job-runs/import", json=body, headers={"Authorization": f"Bearer {tok['access_token']}"})
        print("imported:", r.status_code, r.text[:200])


if __name__ == "__main__":
    raise SystemExit(main())
