"""Gold layer: business-ready tables built with Spark SQL.

Each ``sql/gold/NN_name.sql`` file is one table. Table references are written as
``${layer.table}`` placeholders and resolved by the ``TableStore``, so the same
SQL runs against local Delta folders and Unity Catalog tables. Files run in name
order because later tables can read earlier ones (customer_360 counts AML alerts).
"""

from __future__ import annotations

import re
from pathlib import Path

from pyspark.sql import functions as F

from .dq import control_check
from .store import TableStore

SQL_DIR = Path(__file__).parent / "sql" / "gold"
_PLACEHOLDER = re.compile(r"\$\{(\w+)\.(\w+)\}")


def gold_sql_files() -> list[Path]:
    return sorted(SQL_DIR.glob("*.sql"))


def table_name(path: Path) -> str:
    return path.stem.split("_", 1)[1]


def render(sql: str, store: TableStore) -> str:
    return _PLACEHOLDER.sub(lambda m: store.name(m.group(1), m.group(2)), sql)


def build(store: TableStore) -> tuple[list[dict], list[dict]]:
    steps = []
    for path in gold_sql_files():
        name = table_name(path)
        sql = path.read_text(encoding="utf-8")
        comment = sql.splitlines()[0].lstrip("- ").replace("'", "")
        df = store.spark.sql(render(sql, store))
        store.write(df, "gold", name, mode="overwrite", comment=comment)
        steps.append({"layer": "gold", "table": name, "rows_written": store.read("gold", name).count()})

    # Cross-layer reconciliation: Gold aggregates must add back to Silver.
    silver_count = store.read("silver", "transactions").count()
    silver_usd = store.read("silver", "transactions").agg(F.sum("amount_usd")).collect()[0][0]
    daily = store.read("gold", "daily_transaction_summary").agg(
        F.sum("txn_count"), F.sum("total_amount_usd")).collect()[0]
    kpi = store.read("gold", "segment_monthly_kpis").agg(F.sum("txn_count")).collect()[0][0]
    controls = [
        control_check("gold_daily_summary_reconciles_to_silver",
                      daily[0] == silver_count and abs(float(daily[1]) - float(silver_usd)) < 1.0,
                      table="gold.daily_transaction_summary", layer="gold",
                      detail=f"daily summary {daily[0]} txns / {float(daily[1]):,.2f} USD vs silver "
                             f"{silver_count} txns / {float(silver_usd):,.2f} USD"),
        control_check("gold_segment_kpis_cover_every_transaction", kpi == silver_count,
                      table="gold.segment_monthly_kpis", layer="gold",
                      detail=f"segment KPIs count {kpi} txns vs silver {silver_count} (SCD2 as-of join complete)"),
    ]
    return steps, controls
