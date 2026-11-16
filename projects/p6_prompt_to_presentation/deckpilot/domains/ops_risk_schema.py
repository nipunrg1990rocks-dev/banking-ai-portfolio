"""Data contract: the only columns, categories and value ranges the platform accepts.

Everything downstream (validation, the SQL query builder, the prompt parser and the
LLM output validator) reads its allowlists from this module, so there is one place
to change the governed vocabulary.
"""

from __future__ import annotations

REGIONS = ["APAC", "EMEA", "Americas"]
BUSINESS_LINES = ["Retail Banking", "Corporate Banking", "Wealth Management", "Markets Operations"]
PROCESSES = ["Payments", "Trade Settlement", "KYC Onboarding", "Reconciliations", "Loan Servicing"]
SEVERITIES = ["Low", "Medium", "High", "Critical"]
HIGH_SEVERITIES = ["High", "Critical"]
ROOT_CAUSES = [
    "Manual keying error",
    "Missing documentation",
    "System outage",
    "Static data mismatch",
    "Late counterparty confirmation",
    "Screening false positive",
    "Process design gap",
    "Vendor delay",
]
REMEDIATION_STATUSES = ["Open", "In Progress", "Closed"]
DQ_STATUSES = ["Pass", "Warning", "Fail"]

# Column name -> (dtype family, required, description)
COLUMNS: dict[str, tuple[str, bool, str]] = {
    "record_id": ("str", True, "Unique identifier of the exception cluster record"),
    "reporting_month": ("month", True, "Month-end reporting period, YYYY-MM"),
    "region": ("category", True, "Booking region"),
    "business_line": ("category", True, "Business line that owns the process"),
    "process": ("category", True, "Operational process"),
    "transaction_volume": ("int", True, "Transactions processed in the process-month (repeated on every row of that "
                                        "process-month; non-additive across rows)"),
    "completed_on_time": ("int", True, "Transactions completed within SLA in the process-month (repeated, "
                                       "non-additive)"),
    "exceptions": ("int", True, "Exceptions raised in this severity and root-cause cluster"),
    "severity": ("category", True, "Exception severity"),
    "root_cause": ("category", True, "Root cause recorded by the control owner"),
    "ageing_days": ("int", False, "Age of the cluster at month end (days); days-to-close when closed"),
    "control_owner": ("str", True, "Accountable control owner role"),
    "risk_score": ("float", False, "Residual risk score 0-100"),
    "loss_amount_inr": ("float", False, "Operational loss booked against the cluster, INR"),
    "remediation_status": ("category", True, "Open, In Progress or Closed"),
    "data_quality_status": ("category", True, "Source-system data quality flag: Pass, Warning or Fail"),
}
REQUIRED_COLUMNS = [c for c, (_, req, _) in COLUMNS.items() if req]
OPTIONAL_COLUMNS = [c for c, (_, req, _) in COLUMNS.items() if not req]
ALLOWED_COLUMNS = list(COLUMNS)

CATEGORY_DOMAINS: dict[str, list[str]] = {
    "region": REGIONS,
    "business_line": BUSINESS_LINES,
    "process": PROCESSES,
    "severity": SEVERITIES,
    "root_cause": ROOT_CAUSES,
    "remediation_status": REMEDIATION_STATUSES,
    "data_quality_status": DQ_STATUSES,
}

RANGES: dict[str, tuple[float, float]] = {
    "transaction_volume": (0, 5_000_000),
    "completed_on_time": (0, 5_000_000),
    "exceptions": (0, 100_000),
    "ageing_days": (0, 730),
    "risk_score": (0, 100),
    "loss_amount_inr": (0, 5_000_000_000),
}

# The grain at which transaction_volume and completed_on_time are recorded.
VOLUME_GRAIN = ["reporting_month", "region", "business_line", "process"]
# The natural key of one exception cluster row (used for duplicate detection).
ROW_KEY = VOLUME_GRAIN + ["severity", "root_cause"]

# Dimensions a user (or the LLM) may filter on; nothing else can reach a WHERE clause.
FILTER_DIMENSIONS = ["region", "business_line", "process", "severity", "reporting_month"]

AGEING_BANDS = [(0, 30, "0-30 days"), (31, 60, "31-60 days"), (61, 90, "61-90 days"), (91, 10_000, "90+ days")]

STALE_AFTER_DAYS = 45  # data older than this (latest month-end vs today) is flagged as stale
