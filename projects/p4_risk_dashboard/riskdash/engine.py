"""SQL engines: one set of Databricks SQL views, three places to run them.

* ``DuckDBEngine`` (default, free, offline): loads the Project 02 Gold snapshot
  and the simulated risk mart into an in-memory DuckDB database. The Databricks
  SQL is translated to DuckDB SQL with sqlglot before it runs.
* ``SparkEngine``: runs the SQL unchanged on Apache Spark. CI uses it locally to
  prove the views give identical numbers on Spark; the Databricks notebook uses
  it against Unity Catalog tables.
* ``DatabricksSQLEngine``: runs the SQL unchanged on a Databricks SQL warehouse
  through the official connector. Used only when the app owner adds connection
  details to Streamlit secrets; the token is never shown or logged.

View SQL refers to tables as ``${mart.<table>}`` (the risk mart) and
``${gold.<table>}`` (Project 02 Gold). Each engine decides what those names are.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Any

import pandas as pd

PLACEHOLDER = re.compile(r"\$\{(mart|gold)\.([a-z0-9_]+)\}")

# Column types that CSV or pandas cannot carry reliably. Everything else is inferred.
DATE, TS, BOOL = "DATE", "TIMESTAMP", "BOOLEAN"
COLUMN_TYPES: dict[str, dict[str, str]] = {
    "mart.calendar": {"d": DATE, "week_ending": DATE, "is_business_day": BOOL},
    "mart.change_events": {"event_date": DATE},
    "mart.dq_results": {"run_date": DATE},
    "mart.feed_arrivals": {"business_date": DATE, "sla_ts": TS, "arrived_ts": TS, "on_time": BOOL},
    "mart.recon_runs": {"run_date": DATE},
    "mart.recon_breaks": {"opened_date": DATE, "resolved_date": DATE},
    "mart.exceptions": {"opened_ts": TS, "due_ts": TS, "resolved_ts": TS, "regulated": BOOL},
    "mart.kyc_reviews": {"due_date": DATE, "completed_date": DATE},
    "mart.op_loss_events": {"event_date": DATE},
    "mart.finance_kris": {"obs_date": DATE},
    "mart.compliance_events": {"event_date": DATE, "event_id": "STRING", "event_type": "STRING",
                               "description": "STRING"},
    "gold.customer_360": {"onboarding_date": DATE, "last_txn_date": DATE},
    "gold.aml_alert_candidates": {"alert_date": DATE},
    "gold.daily_transaction_summary": {"txn_date": DATE},
}


class QueryError(RuntimeError):
    pass


def decimals_to_float(df: pd.DataFrame) -> pd.DataFrame:
    """Spark and Databricks return DECIMAL columns as Python Decimals; the metric layer works in floats."""
    for col in df.columns:
        if df[col].dtype == object and any(isinstance(v, Decimal) for v in df[col].dropna().head(20)):
            df[col] = df[col].map(lambda v: None if v is None else float(v)).astype(float)
    return df


def to_duckdb_sql(databricks_sql: str) -> str:
    """Translate Databricks SQL to DuckDB SQL (sqlglot)."""
    import sqlglot

    return sqlglot.transpile(databricks_sql, read="databricks", write="duckdb", pretty=True)[0]


def duckdb_sql_for_display(template: str) -> str:
    """The DuckDB SQL that offline mode actually runs for a view template."""
    schemas = DuckDBEngine.SCHEMAS
    return to_duckdb_sql(PLACEHOLDER.sub(lambda m: f"{schemas[m.group(1)]}.{m.group(2)}", template))


class SqlEngine:
    name = "abstract"
    dialect = "databricks"
    description = ""

    def table_name(self, layer: str, table: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def execute(self, sql: str) -> pd.DataFrame:  # pragma: no cover - interface
        raise NotImplementedError

    def render(self, template: str) -> str:
        """Fill in table names, then translate to the engine's dialect."""
        sql = PLACEHOLDER.sub(lambda m: self.table_name(m.group(1), m.group(2)), template)
        return sql if self.dialect == "databricks" else to_duckdb_sql(sql)

    def run(self, template: str) -> pd.DataFrame:
        return self.execute(self.render(template))


# ------------------------------------------------------------------ DuckDB
class DuckDBEngine(SqlEngine):
    name = "duckdb"
    dialect = "duckdb"
    description = "DuckDB in-memory (offline, free). Databricks SQL translated with sqlglot."

    SCHEMAS = {"mart": "ledgerline_riskmart", "gold": "ledgerline_gold"}

    def __init__(self, mart: dict[str, pd.DataFrame], gold: dict[str, pd.DataFrame]) -> None:
        import duckdb

        self.con = duckdb.connect(":memory:")
        for layer, tables in (("mart", mart), ("gold", gold)):
            self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {self.SCHEMAS[layer]}")
            for table, df in tables.items():
                self._load(layer, table, df)
        # Analysts may only read: no files, no extensions, no network.
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")

    def _load(self, layer: str, table: str, df: pd.DataFrame) -> None:
        types = COLUMN_TYPES.get(f"{layer}.{table}", {})
        frame = df.copy()
        for col, typ in types.items():
            if col in frame.columns and typ in (DATE, TS):
                frame[col] = pd.to_datetime(frame[col])
        self.con.register("_incoming", frame)
        casts = [f"CAST({col} AS {('VARCHAR' if typ == 'STRING' else typ)}) AS {col}"
                 for col, typ in types.items() if col in frame.columns]
        select = f"SELECT * REPLACE ({', '.join(casts)}) FROM _incoming" if casts else "SELECT * FROM _incoming"
        self.con.execute(f"CREATE OR REPLACE TABLE {self.table_name(layer, table)} AS {select}")
        self.con.unregister("_incoming")

    def table_name(self, layer: str, table: str) -> str:
        return f"{self.SCHEMAS[layer]}.{table}"

    def execute(self, sql: str) -> pd.DataFrame:
        try:
            return self.con.execute(sql).df()
        except Exception as exc:  # duckdb raises several error classes
            raise QueryError(str(exc).split("\n")[0]) from exc


# ------------------------------------------------------------------- Spark
def spark_frame(spark, df: pd.DataFrame, types: dict[str, str]):
    """pandas -> Spark with explicit types (works for empty frames and None-filled date columns)."""
    from pyspark.sql import functions as F
    from pyspark.sql import types as T

    fields, prepared = [], df.copy()
    for col in df.columns:
        if col in types:
            fields.append(T.StructField(col, T.StringType()))
            prepared[col] = prepared[col].map(lambda v: None if v is None or v != v else str(v))
            if types[col] == BOOL:
                prepared[col] = prepared[col].map(lambda v: None if v is None else str(v).lower())
        elif pd.api.types.is_bool_dtype(df[col]):
            fields.append(T.StructField(col, T.BooleanType()))
        elif pd.api.types.is_integer_dtype(df[col]):
            fields.append(T.StructField(col, T.LongType()))
        elif pd.api.types.is_float_dtype(df[col]):
            fields.append(T.StructField(col, T.DoubleType()))
        else:
            fields.append(T.StructField(col, T.StringType()))
            prepared[col] = prepared[col].map(lambda v: None if v is None or v != v else str(v))
    rows = [tuple(None if (isinstance(v, float) and v != v) else v for v in r)
            for r in prepared.astype(object).itertuples(index=False, name=None)]
    sdf = spark.createDataFrame(rows, T.StructType(fields))
    for col, typ in types.items():
        if col in df.columns and typ != "STRING":
            sdf = sdf.withColumn(col, F.col(col).cast(typ.lower()))
    return sdf


class SparkEngine(SqlEngine):
    name = "spark"
    dialect = "databricks"
    description = "Apache Spark SQL (local Spark in CI, or Databricks)."

    def __init__(self, spark, names: dict[str, str] | None = None) -> None:
        """``names`` maps 'mart' and 'gold' to a format string such as 'workspace.ledgerline_gold.{}'."""
        self.spark = spark
        self.names = names or {"mart": "riskmart_{}", "gold": "gold_{}"}

    @classmethod
    def with_temp_views(cls, spark, mart: dict[str, pd.DataFrame], gold: dict[str, pd.DataFrame]) -> SparkEngine:
        engine = cls(spark)
        for layer, tables in (("mart", mart), ("gold", gold)):
            for table, df in tables.items():
                types = COLUMN_TYPES.get(f"{layer}.{table}", {})
                spark_frame(spark, df, types).createOrReplaceTempView(engine.table_name(layer, table))
        return engine

    def table_name(self, layer: str, table: str) -> str:
        return self.names[layer].format(table)

    def execute(self, sql: str) -> pd.DataFrame:
        try:
            return decimals_to_float(self.spark.sql(sql).toPandas())
        except Exception as exc:
            raise QueryError(str(exc).split("\n")[0]) from exc


# ------------------------------------------------------- Databricks SQL
DATABRICKS_SECRET_NAMES = ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN")


def _secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st

        return st.secrets.get(name)  # type: ignore[no-any-return]
    except Exception:
        return None


def databricks_configured() -> bool:
    return all(_secret(n) for n in DATABRICKS_SECRET_NAMES)


class DatabricksSQLEngine(SqlEngine):
    name = "databricks"
    dialect = "databricks"
    description = "Databricks SQL warehouse (live Unity Catalog tables)."

    def __init__(self, catalog: str | None = None, connection: Any = None) -> None:
        catalog = catalog or _secret("DATABRICKS_CATALOG") or "workspace"
        self.names = {"mart": f"{catalog}.ledgerline_riskmart.{{}}", "gold": f"{catalog}.ledgerline_gold.{{}}"}
        if connection is not None:
            self._conn = connection
            return
        if not databricks_configured():
            raise QueryError("Databricks SQL is not configured (add the connection details to Streamlit secrets).")
        from databricks import sql as dbsql

        self._conn = dbsql.connect(
            server_hostname=_secret("DATABRICKS_SERVER_HOSTNAME"),
            http_path=_secret("DATABRICKS_HTTP_PATH"),
            access_token=_secret("DATABRICKS_TOKEN"),
        )

    def table_name(self, layer: str, table: str) -> str:
        return self.names[layer].format(table)

    def execute(self, sql: str) -> pd.DataFrame:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
                columns = [c[0] for c in cursor.description]
            return decimals_to_float(pd.DataFrame([tuple(r) for r in rows], columns=columns))
        except Exception as exc:
            raise QueryError(str(exc).split("\n")[0]) from exc
