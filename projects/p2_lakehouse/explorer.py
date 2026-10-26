"""Read-only access to the pipeline snapshot for the Streamlit app (pandas + DuckDB, no Spark).

Kept separate from ``app.py`` so it can be unit-tested without a browser.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

SNAPSHOT = Path(__file__).resolve().parent / "data" / "snapshot"
GOLD_SQL = Path(__file__).resolve().parent / "lakehouse" / "sql" / "gold"

SQL_TABLES = {
    "transactions": "silver_transactions.csv",
    "accounts": "silver_accounts.csv",
    "customer_360": "gold_customer_360.csv",
    "aml_alerts": "gold_aml_alert_candidates.csv",
    "daily_summary": "gold_daily_transaction_summary.csv",
    "segment_kpis": "gold_segment_monthly_kpis.csv",
    "dq_scorecard": "gold_dq_scorecard.csv",
    "quarantine": "quarantine_transactions.csv",
    "customer_history": "scd2_customer_history.csv",
}
EXAMPLE_QUERIES = {  # each value starts with a newline only to keep lines short; stripped below
    "Top 10 customers by cross-border volume (30 days)": """
SELECT customer_id, segment, kyc_risk_rating, cross_border_usd_30d, open_alerts
FROM customer_360
ORDER BY cross_border_usd_30d DESC
LIMIT 10""",
    "Cash deposits just under the 10,000 USD reporting threshold": """
SELECT customer_id, txn_date, COUNT(*) AS deposits, ROUND(SUM(amount_usd), 2) AS total_usd
FROM transactions
WHERE txn_type = 'CASH_DEPOSIT' AND amount_usd BETWEEN 9000 AND 9999.99
GROUP BY customer_id, txn_date
ORDER BY deposits DESC, total_usd DESC""",
    "Monthly volume by segment": """
SELECT month, segment, txn_count, volume_usd, high_risk_txn_pct
FROM segment_kpis
ORDER BY month, volume_usd DESC""",
    "Why were records quarantined?": """
SELECT _dq_failed_rules AS rule, COUNT(*) AS records, MIN(_batch_id) AS first_batch
FROM quarantine
GROUP BY rule
ORDER BY records DESC""",
    "Customers whose KYC risk rating changed (SCD Type 2)": """
SELECT customer_id, kyc_risk_rating, segment, country, effective_from, effective_to, is_current
FROM customer_history
ORDER BY customer_id, effective_from""",
    "Wires with high-risk jurisdictions, any amount": """
SELECT txn_id, customer_id, txn_type, amount_usd, counterparty_country, counterparty_risk_tier
FROM transactions
WHERE counterparty_risk_tier = 'HIGH'
ORDER BY amount_usd DESC""",
}
EXAMPLE_QUERIES = {k: v.strip() for k, v in EXAMPLE_QUERIES.items()}
_READ_ONLY = re.compile(r"^\s*(SELECT|WITH|DESCRIBE|SHOW|SUMMARIZE|FROM|PIVOT|UNPIVOT)\b", re.IGNORECASE)
MAX_ROWS = 1000


class QueryError(ValueError):
    pass


def _csv(folder: Path, name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(folder / name, **kw)


@dataclass
class Snapshot:
    summary: dict
    history: dict
    dq: pd.DataFrame
    scorecard: pd.DataFrame
    alerts: pd.DataFrame
    customer_360: pd.DataFrame
    daily: pd.DataFrame
    kpis: pd.DataFrame
    transactions: pd.DataFrame
    scd2: pd.DataFrame
    cdf_customers: pd.DataFrame
    cdf_accounts: pd.DataFrame
    quarantine: dict[str, pd.DataFrame]

    @classmethod
    def load(cls, folder: Path = SNAPSHOT) -> Snapshot:
        txns = _csv(folder, "silver_transactions.csv", parse_dates=["txn_ts"])
        txns["txn_date"] = pd.to_datetime(txns["txn_date"])
        return cls(
            summary=json.loads((folder / "run_summary.json").read_text()),
            history=json.loads((folder / "delta_history.json").read_text()),
            dq=_csv(folder, "dq_results.csv"),
            scorecard=_csv(folder, "gold_dq_scorecard.csv"),
            alerts=_csv(folder, "gold_aml_alert_candidates.csv", parse_dates=["alert_date"]),
            customer_360=_csv(folder, "gold_customer_360.csv"),
            daily=_csv(folder, "gold_daily_transaction_summary.csv", parse_dates=["txn_date"]),
            kpis=_csv(folder, "gold_segment_monthly_kpis.csv", parse_dates=["month"]),
            transactions=txns,
            scd2=_csv(folder, "scd2_customer_history.csv"),
            cdf_customers=_csv(folder, "cdf_customers.csv"),
            cdf_accounts=_csv(folder, "cdf_accounts.csv"),
            quarantine={t: _csv(folder, f"quarantine_{t}.csv") for t in ("transactions", "customers", "accounts")},
        )

    # ------------------------------------------------------------ headline numbers
    def headline(self) -> dict:
        steps = self.summary["steps"]
        landed = sum(s["rows_written"] or 0 for s in steps if s["step"] == "land")
        silver = [s for s in steps if s["step"] == "silver"]
        dq_fail = self.dq[(self.dq.rows_failed > 0)]
        return {
            "source_rows": landed,
            "duplicates_removed": sum(s["duplicates_removed"] or 0 for s in silver),
            "quarantined": sum(s["rows_quarantined"] or 0 for s in silver),
            "checks": len(self.dq),
            "checks_with_findings": len(dq_fail),
            "controls_passed": int(((self.dq.action == "fail") & (self.dq.rows_failed == 0)).sum()),
            "controls": int((self.dq.action == "fail").sum()),
            "alerts": len(self.alerts),
            "silver_transactions": self.summary["table_counts"]["silver.transactions"],
            "gold_tables": sum(1 for k in self.summary["table_counts"] if k.startswith("gold.")),
        }

    def journey(self) -> pd.DataFrame:
        """Where every Bronze transaction went, per batch (the reconciliation the pipeline enforces)."""
        rows = []
        for s in self.summary["steps"]:
            if s["step"] == "silver" and s["table_name"] == "transactions":
                already = json.loads(s["details"] or "{}").get("already_loaded", 0)
                rows.append({"batch": s["batch_id"], "Landed in Bronze": s["rows_in"],
                             "Duplicates removed": s["duplicates_removed"], "Quarantined": s["rows_quarantined"],
                             "Already loaded (re-sent)": already, "Inserted into Silver": s["rows_written"]})
        return pd.DataFrame(rows)

    def controls(self) -> pd.DataFrame:
        return self.dq[self.dq.action == "fail"][["batch_id", "layer", "table_name", "rule_name", "description",
                                                  "rows_failed"]]

    # ------------------------------------------------------------------ AML detail
    def alert_transactions(self, alert_id: str) -> pd.DataFrame:
        alert = self.alerts[self.alerts.alert_id == alert_id].iloc[0]
        ids = str(alert.txn_ids).split(";")
        return self.transactions[self.transactions.txn_id.isin(ids)].sort_values("txn_ts")

    def customer_activity(self, customer_id: str, around: pd.Timestamp, days: int = 7) -> pd.DataFrame:
        t = self.transactions
        window = (t.customer_id == customer_id) & (t.txn_date >= around - pd.Timedelta(days=days)) & (
            t.txn_date <= around + pd.Timedelta(days=days))
        return t[window].sort_values("txn_ts")

    def customer_profile(self, customer_id: str) -> dict:
        row = self.customer_360[self.customer_360.customer_id == customer_id]
        return row.iloc[0].to_dict() if len(row) else {}

    # --------------------------------------------------------------------- Delta
    def history_frame(self, table: str) -> pd.DataFrame:
        return pd.DataFrame([{**{k: v for k, v in h.items() if k != "metrics"}, **h["metrics"]}
                             for h in self.history[table]])


def gold_sql(name: str) -> str:
    match = sorted(GOLD_SQL.glob(f"*_{name}.sql"))
    return match[0].read_text() if match else ""


def sql_console(folder: Path = SNAPSHOT) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB holding the snapshot tables, locked so queries cannot touch files or the network."""
    con = duckdb.connect(":memory:")
    for table, file in SQL_TABLES.items():
        df = pd.read_csv(folder / file)
        con.register("_df", df)
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM _df")
        con.unregister("_df")
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def run_query(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    statements = [s for s in sql.strip().split(";") if s.strip()]
    if len(statements) != 1:
        raise QueryError("Run one statement at a time.")
    if not _READ_ONLY.match(statements[0]):
        raise QueryError("This console is read-only: start with SELECT, WITH, DESCRIBE, SHOW or SUMMARIZE.")
    try:
        return con.execute(statements[0]).df().head(MAX_ROWS)
    except duckdb.Error as exc:
        raise QueryError(str(exc).split("\n")[0]) from exc
