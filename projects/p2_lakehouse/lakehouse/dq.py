"""Declarative data-quality expectations, in the style of Databricks Lakeflow (DLT) expectations.

Each rule is a SQL boolean expression plus an action:

* ``warn``  keep the row, count the failure (e.g. a malformed e-mail on a customer)
* ``drop``  move the row to a quarantine table with the names of the rules it broke
* ``fail``  stop the pipeline (used for control checks such as reconciliation)

A NULL result counts as a failure, so a rule like ``amount > 0`` also catches a
missing amount. Every evaluation produces metrics rows that are written to
``audit.dq_results``, which feeds the Gold DQ scorecard.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from .store import on_databricks

WARN, DROP, FAIL = "warn", "drop", "fail"


class DataQualityError(RuntimeError):
    """Raised when a ``fail`` rule is broken. The run stops and nothing downstream is published."""


@dataclass(frozen=True)
class Rule:
    name: str
    expression: str
    action: str
    description: str

    def passes(self) -> Column:
        return F.coalesce(F.expr(self.expression), F.lit(False))


@dataclass
class DQResult:
    valid: DataFrame
    quarantine: DataFrame
    metrics: list[dict]


def apply_expectations(df: DataFrame, rules: list[Rule], *, table: str, layer: str = "silver") -> DQResult:
    """Tag every row with the rules it fails, split valid from quarantined rows, and count failures per rule."""
    failed = F.filter(
        F.array(*[F.when(~r.passes(), F.lit(r.name)) for r in rules]),
        lambda x: x.isNotNull(),
    )
    drop_names = [r.name for r in rules if r.action in (DROP, FAIL)]
    tagged = df.withColumn("_dq_failed_rules", failed).withColumn(
        "_dq_dropped", F.arrays_overlap(F.col("_dq_failed_rules"), F.array(*[F.lit(n) for n in drop_names]))
        if drop_names else F.lit(False)
    )
    if not on_databricks():
        tagged = tagged.cache()  # serverless compute does not support caching; it is only a local speed-up

    agg = tagged.agg(
        F.count(F.lit(1)).alias("_rows"),
        *[F.sum(F.when(F.array_contains("_dq_failed_rules", r.name), 1).otherwise(0)).alias(r.name) for r in rules],
    ).collect()[0]
    checked = int(agg["_rows"])
    metrics = [
        {
            "layer": layer,
            "table_name": table,
            "rule_name": r.name,
            "action": r.action,
            "description": r.description,
            "expression": r.expression,
            "rows_checked": checked,
            "rows_failed": int(agg[r.name] or 0),
        }
        for r in rules
    ]
    broken = [m for m in metrics if m["action"] == FAIL and m["rows_failed"]]
    if broken:
        raise DataQualityError(f"{table}: blocking rule(s) failed: "
                               + ", ".join(f"{m['rule_name']} ({m['rows_failed']} rows)" for m in broken))

    valid = tagged.filter(~F.col("_dq_dropped")).drop("_dq_dropped")
    quarantine = tagged.filter(F.col("_dq_dropped")).drop("_dq_dropped")
    return DQResult(valid=valid, quarantine=quarantine, metrics=metrics)


def control_check(name: str, passed: bool, *, table: str, detail: str, layer: str = "control",
                  rows_checked: int = 1) -> dict:
    """A single pass/fail control (e.g. reconciliation to source control totals). Raises if it fails."""
    metric = {
        "layer": layer,
        "table_name": table,
        "rule_name": name,
        "action": FAIL,
        "description": detail,
        "expression": "control",
        "rows_checked": rows_checked,
        "rows_failed": 0 if passed else rows_checked,
    }
    if not passed:
        raise DataQualityError(f"{table}: control '{name}' failed: {detail}")
    return metric


VALID_CURRENCIES_SQL = "('USD','EUR','GBP','INR','SGD','AED','JPY')"

CUSTOMER_RULES = [
    Rule("customer_id_present", "customer_id IS NOT NULL AND customer_id <> ''", DROP,
         "Every customer must have an identifier"),
    Rule("valid_risk_rating", "kyc_risk_rating_raw IN ('LOW','MEDIUM','HIGH')", WARN,
         "KYC risk rating should be LOW, MEDIUM or HIGH; an invalid rating is defaulted to HIGH (fail-safe)"),
    Rule("valid_segment", "segment IN ('RETAIL','PREMIER','SME','CORPORATE')", DROP,
         "Customer segment must be a known value"),
    Rule("valid_email", "email RLIKE '^[^@\\\\s]+@[^@\\\\s]+\\\\.[a-z]{2,}$'", WARN,
         "E-mail should be well formed (kept, flagged for data steward)"),
    Rule("dob_present", "date_of_birth IS NOT NULL", WARN,
         "Date of birth should be present (needed for KYC refresh)"),
    Rule("known_country", "country_known", WARN,
         "Country of residence should exist in the country reference table"),
]

ACCOUNT_RULES = [
    Rule("account_id_present", "account_id IS NOT NULL AND account_id <> ''", DROP, "Every account needs an id"),
    Rule("known_customer", "customer_known", DROP, "Account must belong to a known customer (referential integrity)"),
    Rule("valid_currency", f"currency IN {VALID_CURRENCIES_SQL}", DROP, "Account currency must be supported"),
    Rule("valid_status", "status IN ('ACTIVE','DORMANT','CLOSED')", DROP, "Account status must be a known value"),
]

TRANSACTION_RULES = [
    Rule("txn_id_present", "txn_id IS NOT NULL AND txn_id <> ''", DROP, "Every transaction needs an id"),
    Rule("amount_present", "amount IS NOT NULL", DROP, "Amount must be present and numeric"),
    Rule("amount_positive", "amount IS NULL OR amount > 0", DROP,
         "Amount must be positive (direction comes from the transaction type)"),
    Rule("valid_currency", f"currency IN {VALID_CURRENCIES_SQL}", DROP, "Currency must be supported"),
    Rule("valid_timestamp", "txn_ts IS NOT NULL", DROP, "Timestamp must parse as ISO-8601 UTC"),
    Rule("not_future_dated", "txn_ts IS NULL OR txn_ts <= business_cutoff", DROP,
         "Transaction cannot be dated after the batch business date"),
    Rule("known_account", "account_known", DROP, "Transaction must post to a known account"),
    Rule("fx_rate_available", "usd_rate IS NOT NULL OR currency NOT IN " + VALID_CURRENCIES_SQL
         + " OR txn_ts IS NULL OR txn_ts > business_cutoff",
         DROP, "A USD FX rate must exist for the currency and date (otherwise amount_usd would be wrong)"),
]
