"""The metric catalogue: every KPI and KRI on the dashboard, defined once.

The design follows a semantic layer (the same idea as Unity Catalog metric
views or dbt metrics):

* A **fact view** is a SQL file in ``sql/views/``, written in Databricks SQL.
  It returns one row per reporting day (``obs_date``) per underlying record,
  with the record's dimensions and numeric columns.
* A **metric** names a fact view, a numerator column and an optional
  denominator column. Its value for any slice is
  ``scale * SUM(numerator) / SUM(denominator)`` (or ``scale * SUM(numerator)``).
  Because both parts are sums, any metric can be broken down by any of its
  dimensions and drilled to the records behind it, and the numbers add up.

Thresholds follow the Risk Appetite Statement (RAS-004, Project 01's policy
library) where it sets them. For operational KPIs the thresholds are
illustrative management targets for a synthetic bank.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SQL_DIR = Path(__file__).resolve().parent / "sql" / "views"

DOMAINS = [
    "Capital and liquidity",
    "Credit risk",
    "Operational risk",
    "Financial crime and compliance",
    "Data quality",
    "Reconciliations",
    "Operations and SLAs",
    "Business activity",
]


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    domain: str
    view: str
    numerator: str
    denominator: str | None
    unit: str  # "%", "count", "USD m"
    direction: str  # "higher_is_better", "lower_is_better" or "none" (context only)
    early_warning: float | None
    limit: float | None
    owner: str
    definition: str
    dimensions: tuple[str, ...] = ()
    scale: float = 1.0
    decimals: int = 1
    policy_ref: str | None = None
    kri: bool = False  # part of the board risk-appetite dashboard
    fill_zero: bool = True  # a day with no rows means 0 (True) or "no data" (False)
    record_key: str | None = None
    record_columns: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sql_path(self) -> Path:
        return SQL_DIR / f"{self.view}.sql"

    @property
    def zero_tolerance(self) -> bool:
        return self.limit == 0 and self.direction == "lower_is_better"

    def formula(self) -> str:
        num = f"SUM({self.numerator})"
        expr = f"{num} / SUM({self.denominator})" if self.denominator else num
        if self.scale != 1:
            expr = f"{self.scale:g} x {expr}"
        return f"{expr}  over  {self.view}, for the reporting day"

    def fmt(self, value: float | None) -> str:
        if value is None or value != value:  # NaN
            return "n/a"
        if self.unit == "%":
            return f"{value:.{self.decimals}f}%"
        if self.unit == "USD m":
            return f"USD {value:,.{self.decimals}f}m"
        return f"{value:,.0f}"


METRICS: list[Metric] = [
    # ---------------------------------------------------------- RAS-004 KRIs
    Metric("KRI_CET1_PCT", "CET1 ratio", "Capital and liquidity", "fv_finance_kris", "cet1_pct", None, "%",
           "higher_is_better", 12.5, 11.5, "Chief Financial Officer",
           "Common Equity Tier 1 capital as a share of risk-weighted assets (month-end, from Finance).",
           decimals=2, policy_ref="RAS-004 s.3", kri=True, fill_zero=False),
    Metric("KRI_LCR_PCT", "Liquidity coverage ratio", "Capital and liquidity", "fv_finance_kris", "lcr_pct", None,
           "%", "higher_is_better", 120.0, 110.0, "Treasurer",
           "High-quality liquid assets divided by 30-day stressed net cash outflows (daily, from Treasury).",
           policy_ref="RAS-004 s.3", kri=True, fill_zero=False),
    Metric("KRI_NPL_PCT", "Non-performing loans", "Credit risk", "fv_finance_kris", "npl_pct", None, "%",
           "lower_is_better", 2.0, 2.5, "Chief Credit Officer",
           "Non-performing loans as a share of the gross loan book (month-end, from Finance).",
           decimals=2, policy_ref="RAS-004 s.4", kri=True, fill_zero=False),
    Metric("KRI_OPLOSS_YTD_USD_M", "Operational losses, year to date", "Operational risk", "fv_op_loss_ytd",
           "net_loss_usd", None, "USD m", "lower_is_better", 10.0, 15.0, "Head of Operational Risk",
           "Net operational-loss amount (gross loss minus recoveries) of events dated this calendar year.",
           dimensions=("basel_event_type", "business_line"), scale=1e-6, decimals=2, policy_ref="RAS-004 s.5",
           kri=True, record_key="event_id",
           record_columns=("event_id", "event_date", "basel_event_type", "business_line", "net_loss_usd",
                           "description")),
    Metric("KRI_HR_KYC_OVERDUE_PCT", "Overdue high-risk KYC reviews", "Financial crime and compliance",
           "fv_hr_kyc_reviews", "overdue", "customers", "%", "lower_is_better", 1.0, 2.0, "Head of KYC Operations",
           "High-risk customers with a periodic KYC review past its due date, as a share of all high-risk "
           "customers in the Project 02 Gold customer_360 table.",
           dimensions=("segment", "region"), scale=100.0, policy_ref="RAS-004 s.5", kri=True,
           record_key="customer_id", record_columns=("customer_id", "segment", "region", "overdue_since")),
    Metric("KRI_AML_ALERTS_OVER_30D", "AML alerts open over 30 days", "Financial crime and compliance",
           "fv_aml_alerts_pending", "over_30_days", None, "count", "lower_is_better", None, 0.0, "AML Officer",
           "AML alert candidates from the Project 02 Gold layer still pending review more than 30 calendar days "
           "after the alert (the AML-002 investigation deadline).",
           dimensions=("rule_code", "priority", "segment"), policy_ref="AML-002 s.3", kri=True,
           record_key="alert_id", record_columns=("customer_id", "rule_code", "priority", "alert_date", "age_days",
                                                  "amount_usd")),
    Metric("KRI_SANCTIONS_BREACHES", "Confirmed sanctions breaches (YTD)", "Financial crime and compliance",
           "fv_compliance_ytd", "sanctions_breaches", None, "count", "lower_is_better", None, 0.0, "MLRO",
           "Confirmed sanctions breaches this calendar year. Zero tolerance.", policy_ref="RAS-004 s.5", kri=True),
    Metric("KRI_LATE_SARS", "Late SAR filings (YTD)", "Financial crime and compliance", "fv_compliance_ytd",
           "late_sars", None, "count", "lower_is_better", None, 0.0, "AML Officer",
           "Suspicious activity reports filed after the AML-002 deadline this calendar year. Zero tolerance.",
           policy_ref="RAS-004 s.5", kri=True),
    # ---------------------------------------------------------- data quality
    Metric("DQ_HEALTH_SCORE", "Data-quality health score", "Data quality", "fv_dq_rule_results", "weight_met",
           "weight", "%", "higher_is_better", 98.0, 95.0, "Chief Data Officer",
           "Severity-weighted share of data-quality rules meeting their threshold that day "
           "(critical rules count 3, high 2, medium 1).",
           dimensions=("table_name", "dimension", "severity", "rule_id"), scale=100.0, record_key="rule_id",
           record_columns=("rule_id", "table_name", "column_name", "dimension", "severity", "rows_checked",
                           "rows_failed", "pass_rate_pct", "threshold_pct")),
    Metric("DQ_RULES_BREACHED", "Data-quality rules breached", "Data quality", "fv_dq_rule_results", "breached",
           None, "count", "lower_is_better", 1.0, 2.0, "Chief Data Officer",
           "Number of data-quality rules whose pass rate is below their threshold that day.",
           dimensions=("table_name", "dimension", "severity", "rule_id"), record_key="rule_id",
           record_columns=("rule_id", "table_name", "column_name", "dimension", "severity", "rows_checked",
                           "rows_failed", "pass_rate_pct", "threshold_pct")),
    Metric("FEED_ON_TIME_PCT", "Source feeds on time (7 days)", "Data quality", "fv_feed_arrivals_7d", "on_time",
           "feeds", "%", "higher_is_better", 95.0, 90.0, "Chief Data Officer",
           "Share of source-system feeds in the last 7 days that arrived before their delivery SLA.",
           dimensions=("source_system",), scale=100.0, record_key="feed_name",
           record_columns=("business_date", "source_system", "feed_name", "sla_ts", "arrived_ts", "minutes_vs_sla")),
    # -------------------------------------------------------- reconciliations
    Metric("RECON_OPEN_BREAKS", "Open reconciliation breaks", "Reconciliations", "fv_recon_open_breaks", "breaks",
           None, "count", "lower_is_better", 30.0, 45.0, "Head of Reconciliations",
           "Reconciliation breaks not yet resolved at the end of the day.",
           dimensions=("recon_id", "break_type", "owner_team", "age_bucket"), record_key="break_id",
           record_columns=("break_id", "recon_id", "break_type", "opened_date", "age_days", "amount_usd",
                           "root_cause")),
    Metric("RECON_OPEN_VALUE_USD_M", "Unreconciled value", "Reconciliations", "fv_recon_open_breaks", "amount_usd",
           None, "USD m", "lower_is_better", 2.0, 4.0, "Head of Reconciliations",
           "Absolute USD value of open reconciliation breaks at the end of the day.",
           dimensions=("recon_id", "break_type", "owner_team", "age_bucket"), scale=1e-6, decimals=2,
           record_key="break_id",
           record_columns=("break_id", "recon_id", "break_type", "opened_date", "age_days", "amount_usd",
                           "root_cause")),
    Metric("RECON_AGED_BREAKS", "Breaks open over 5 days", "Reconciliations", "fv_recon_open_breaks",
           "aged_over_5d", None, "count", "lower_is_better", 8.0, 15.0, "Head of Reconciliations",
           "Open reconciliation breaks older than 5 calendar days.",
           dimensions=("recon_id", "break_type", "owner_team"), record_key="break_id",
           record_columns=("break_id", "recon_id", "break_type", "opened_date", "age_days", "amount_usd",
                           "root_cause")),
    Metric("RECON_AUTO_MATCH_PCT", "Auto-match rate (7 days)", "Reconciliations", "fv_recon_runs_7d",
           "auto_matched", "items_total", "%", "higher_is_better", 98.0, 97.0, "Head of Reconciliations",
           "Share of items matched automatically by the reconciliation engine in the last 7 days.",
           dimensions=("recon_id", "owner_team"), scale=100.0, decimals=2),
    # ---------------------------------------------------- operations and SLA
    Metric("EXC_BACKLOG", "Open operational exceptions", "Operations and SLAs", "fv_exceptions_open",
           "open_items", None, "count", "lower_is_better", 60.0, 100.0, "Head of Operations",
           "Operational exceptions still open at the end of the day.",
           dimensions=("team", "category", "priority", "age_bucket"), record_key="exception_id",
           record_columns=("exception_id", "category", "team", "priority", "opened_ts", "due_ts", "age_days",
                           "amount_usd")),
    Metric("EXC_PAST_DUE", "Open exceptions past SLA", "Operations and SLAs", "fv_exceptions_open", "past_due",
           None, "count", "lower_is_better", 15.0, 40.0, "Head of Operations",
           "Open operational exceptions whose SLA due time has passed.",
           dimensions=("team", "category", "priority", "age_bucket"), record_key="exception_id",
           record_columns=("exception_id", "category", "team", "priority", "opened_ts", "due_ts", "age_days",
                           "amount_usd")),
    Metric("EXC_SLA_BREACH_PCT", "Exceptions closed late (7 days)", "Operations and SLAs",
           "fv_exceptions_closed_7d", "sla_missed", "closed_items", "%", "lower_is_better", 5.0, 10.0,
           "Head of Operations",
           "Share of exceptions closed in the last 7 days that were closed after their SLA due time.",
           dimensions=("team", "category", "priority"), scale=100.0),
    # ----------------------------------------------------- business activity
    Metric("AML_ALERTS_PENDING", "AML alerts pending review", "Business activity", "fv_aml_alerts_pending",
           "alerts", None, "count", "none", None, None, "AML Officer",
           "AML alert candidates from the Project 02 Gold layer awaiting human review.",
           dimensions=("rule_code", "priority", "segment"), record_key="alert_id",
           record_columns=("customer_id", "rule_code", "priority", "alert_date", "age_days", "amount_usd")),
    Metric("TXN_VOLUME_USD_M", "Payment volume", "Business activity", "fv_txn_volume", "total_amount_usd", None,
           "USD m", "none", None, None, "Head of Payments",
           "Daily transaction value in USD from the Project 02 Gold daily_transaction_summary table.",
           dimensions=("txn_type", "channel", "direction"), scale=1e-6, decimals=2, fill_zero=False),
]

BY_ID = {m.id: m for m in METRICS}
VIEWS = sorted({m.view for m in METRICS})


def get(metric_id: str) -> Metric:
    try:
        return BY_ID[metric_id]
    except KeyError as exc:
        raise KeyError(f"Unknown metric {metric_id!r}") from exc


def view_sql(view: str) -> str:
    return (SQL_DIR / f"{view}.sql").read_text()


def rag(metric: Metric, value: float | None) -> str:
    """GREEN / AMBER / RED against the early-warning trigger and the limit (RAS-004 s.7 semantics)."""
    if value is None or value != value or metric.direction == "none":
        return "NONE"
    ew, lim = metric.early_warning, metric.limit
    if metric.direction == "higher_is_better":
        if lim is not None and value < lim:
            return "RED"
        if ew is not None and value < ew:
            return "AMBER"
        return "GREEN"
    if lim is not None and value > lim:
        return "RED"
    if ew is not None and value >= ew:
        return "AMBER"
    return "GREEN"
