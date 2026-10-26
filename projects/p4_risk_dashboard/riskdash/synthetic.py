"""Synthetic Ledgerline Bank risk and operations mart (Project 04).

The dashboard reads two kinds of data:

* **Real Project 02 output**: the Gold tables of the lakehouse pipeline
  (``customer_360``, ``aml_alert_candidates``, ``daily_transaction_summary``,
  ``dq_scorecard``). Locally they come from ``projects/p2_lakehouse/data/snapshot``;
  on Databricks they are the ``ledgerline_gold`` tables the pipeline wrote.
* **This module's simulated operational history** (1 June to 9 October 2026):
  daily data-quality results, feed arrivals, reconciliation runs and breaks,
  an operations exception queue, KYC periodic reviews, operational-loss events,
  finance KRIs (CET1, LCR, NPL) and an IT change calendar.

Everything is generated from a fixed seed, so every run, test and demo sees the
same bank. Seven incidents are planted on purpose (see ``PLANTED_INCIDENTS``);
the tests check that the insights agent finds each of them and links it to the
right change-calendar entry, without being told where they are.

All names, identifiers and amounts are fictional.
"""

from __future__ import annotations

import heapq
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20261009
START = date(2026, 6, 1)
AS_OF = date(2026, 10, 9)  # Friday: the reporting date of the weekly management pack
YEAR_START = date(2026, 1, 1)

HERE = Path(__file__).resolve().parent
P2_SNAPSHOT = HERE.parent.parent / "p2_lakehouse" / "data" / "snapshot"

# The answer key. The agent never reads this; tests compare its findings to it.
PLANTED_INCIDENTS = [
    {"id": "INC-1", "metric": "RECON_OPEN_BREAKS", "driver": ("recon_id", "NOSTRO_EUR"),
     "change_id": "CHG-2026-1043", "onset": date(2026, 10, 5),
     "story": "Core banking release R26.10 mis-maps value dates on EUR nostro postings"},
    {"id": "INC-2", "metric": "DQ_HEALTH_SCORE", "driver": ("table_name", "ref.fx_rates"),
     "change_id": "INC-2026-0871", "onset": date(2026, 10, 7),
     "story": "MarketData vendor outage: FX rate file missing and late"},
    {"id": "INC-3", "metric": "DQ_RULES_BREACHED", "driver": ("rule_id", "DQ-KYC-01"),
     "change_id": "CHG-2026-0955", "onset": date(2026, 8, 17),
     "story": "Digital onboarding form v3 made address optional; completeness drifts down"},
    {"id": "INC-4", "metric": "EXC_SLA_BREACH_PCT", "driver": ("team", "Payments Ops"),
     "change_id": "BUS-2026-0012", "onset": date(2026, 9, 28),
     "story": "EUR instant-payments corridor launch lifts repair volumes 65% with no extra staff"},
    {"id": "INC-5", "metric": "KRI_HR_KYC_OVERDUE_PCT", "driver": None,
     "change_id": None, "onset": date(2026, 9, 26),
     "story": "One high-risk customer's annual KYC review is overdue: 1 of 39 is above the 2% limit"},
    {"id": "INC-6", "metric": "KRI_LCR_PCT", "driver": None,
     "change_id": "BUS-2026-0019", "onset": date(2026, 10, 6),
     "story": "Two corporate clients move deposits out; LCR drops below the 120% early-warning trigger"},
    {"id": "INC-7", "metric": "KRI_OPLOSS_YTD_USD_M", "driver": None,
     "change_id": "INC-2026-0815", "onset": date(2026, 9, 22),
     "story": "Duplicate outbound payment batch (USD 1.85m loss) takes YTD losses past the USD 10m trigger"},
]


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _is_business_day(d: date) -> bool:
    return d.weekday() < 5


def _ts(d: date, hours: float) -> datetime:
    return datetime(d.year, d.month, d.day) + timedelta(hours=hours)


# --------------------------------------------------------------------- inputs
def load_p2_gold(snapshot_dir: Path | str = P2_SNAPSHOT) -> dict[str, pd.DataFrame]:
    """Read the Project 02 Gold tables that the dashboard builds on."""
    snapshot_dir = Path(snapshot_dir)
    tables = {}
    for name in ("customer_360", "aml_alert_candidates", "daily_transaction_summary", "dq_scorecard"):
        tables[name] = pd.read_csv(snapshot_dir / f"gold_{name}.csv")
    return tables


# ----------------------------------------------------------------- calendar
def build_calendar() -> pd.DataFrame:
    rows = []
    for d in _days(START, AS_OF):
        week_end = d + timedelta(days=(4 - d.weekday()) % 7)
        rows.append({"d": d, "is_business_day": _is_business_day(d), "week_ending": week_end})
    return pd.DataFrame(rows)


# ------------------------------------------------------------- change log
CHANGE_EVENTS = [
    # Linked to planted incidents
    ("CHG-2026-0955", date(2026, 8, 17), "OnboardIQ", "RELEASE",
     "Digital onboarding form v3: address fields moved to an optional step"),
    ("INC-2026-0815", date(2026, 9, 22), "PayFlow", "INCIDENT",
     "Outbound payment batch PB-0922-07 released twice; USD 1.85m not recovered"),
    ("BUS-2026-0012", date(2026, 9, 28), "PayFlow", "BUSINESS_CHANGE",
     "Launch of EUR instant-payments corridor for SME clients"),
    ("CHG-2026-1043", date(2026, 10, 5), "LedgerCore", "RELEASE",
     "Core banking release R26.10 (posting engine and value-date logic)"),
    ("BUS-2026-0019", date(2026, 10, 6), "Treasury", "BUSINESS_CHANGE",
     "Two corporate clients move USD 410m of operational deposits to a competitor"),
    ("INC-2026-0871", date(2026, 10, 7), "MarketData", "INCIDENT",
     "FX rate vendor outage: end-of-day rate file missing, partial re-delivery next day"),
    ("CHG-2026-1061", date(2026, 10, 8), "LedgerCore", "RELEASE",
     "Hotfix R26.10.1: corrects value-date mapping for EUR nostro postings"),
    # Unrelated changes (the agent must not blame these)
    ("CHG-2026-0731", date(2026, 7, 9), "GeneralLedger", "RELEASE", "Quarter-end close automation scripts"),
    ("CHG-2026-0842", date(2026, 8, 3), "CardHub", "RELEASE", "Card scheme fee table update"),
    ("CHG-2026-0918", date(2026, 9, 3), "CardHub", "RELEASE", "Tokenisation service patch"),
    ("CHG-2026-0977", date(2026, 9, 14), "GeneralLedger", "RELEASE", "Cost-centre hierarchy refresh"),
    ("CHG-2026-1002", date(2026, 10, 1), "PayFlow", "RELEASE", "SWIFT certificate renewal"),
    ("CHG-2026-1019", date(2026, 10, 2), "HR Systems", "RELEASE", "Workforce management tool upgrade"),
]


def build_change_events() -> pd.DataFrame:
    return pd.DataFrame(CHANGE_EVENTS, columns=["change_id", "event_date", "system", "change_type", "description"])


# ------------------------------------------------------------ data quality
# rule_id, table, column, dimension, description, severity, threshold %, owner, origin, daily rows
DQ_RULES = [
    ("DQ-TXN-01", "silver.transactions", "txn_id", "Completeness", "Transaction id is present",
     "CRITICAL", 99.9, "Head of Payments Data", "P2 lakehouse rule txn_id_present", 450),
    ("DQ-TXN-02", "silver.transactions", "amount", "Completeness", "Amount is present",
     "CRITICAL", 99.9, "Head of Payments Data", "P2 lakehouse rule amount_present", 450),
    ("DQ-TXN-03", "silver.transactions", "currency", "Validity", "Currency is an ISO code we trade",
     "HIGH", 99.9, "Head of Payments Data", "P2 lakehouse rule valid_currency", 450),
    ("DQ-TXN-04", "silver.transactions", "account_id", "Consistency", "Account exists in the account master",
     "CRITICAL", 99.9, "Head of Payments Data", "P2 lakehouse rule known_account", 450),
    ("DQ-TXN-05", "silver.transactions", "txn_ts", "Validity", "Timestamp parses and is not future-dated",
     "HIGH", 99.9, "Head of Payments Data", "P2 lakehouse rules valid_timestamp, not_future_dated", 450),
    ("DQ-TXN-06", "silver.transactions", "amount_usd", "Completeness", "A USD FX rate exists for the currency",
     "CRITICAL", 99.9, "Head of Payments Data", "P2 lakehouse rule fx_rate_available", 450),
    ("DQ-CUS-01", "silver.customers", "customer_id", "Completeness", "Customer id is present",
     "CRITICAL", 99.9, "Head of Client Data", "P2 lakehouse rule customer_id_present", 650),
    ("DQ-CUS-02", "silver.customers", "email", "Validity", "E-mail address is well formed",
     "MEDIUM", 99.0, "Head of Client Data", "P2 lakehouse rule valid_email", 650),
    ("DQ-CUS-03", "silver.customers", "kyc_risk_rating", "Validity", "KYC risk rating is LOW, MEDIUM or HIGH",
     "CRITICAL", 99.5, "Head of Client Data", "P2 lakehouse rule valid_risk_rating", 650),
    ("DQ-CUS-04", "silver.customers", "country", "Validity", "Country is in the country-risk reference",
     "HIGH", 99.5, "Head of Client Data", "P2 lakehouse rule known_country", 650),
    ("DQ-ACC-01", "silver.accounts", "customer_id", "Consistency", "Account owner exists in the customer master",
     "CRITICAL", 99.5, "Head of Client Data", "P2 lakehouse rule known_customer", 1120),
    ("DQ-KYC-01", "kyc.customer_profile", "residential_address", "Completeness",
     "Residential address is captured (needed for KYC and sanctions screening)",
     "HIGH", 98.5, "Head of KYC Operations", "Enterprise DQ rule", 650),
    ("DQ-KYC-02", "kyc.customer_profile", "tax_id", "Completeness", "Tax identification number is captured",
     "HIGH", 99.0, "Head of KYC Operations", "Enterprise DQ rule", 650),
    ("DQ-KYC-03", "kyc.periodic_reviews", "next_review_date", "Validity",
     "Next review date matches the risk-based review cycle", "MEDIUM", 99.5,
     "Head of KYC Operations", "Enterprise DQ rule", 650),
    ("DQ-PAY-01", "payments.swift_messages", "beneficiary_bic", "Validity", "Beneficiary BIC is in the BIC directory",
     "CRITICAL", 99.9, "Head of Payments Operations", "Enterprise DQ rule", 900),
    ("DQ-PAY-02", "payments.swift_messages", "uetr", "Uniqueness", "UETR is unique (no duplicate payment messages)",
     "CRITICAL", 99.99, "Head of Payments Operations", "Enterprise DQ rule", 900),
    ("DQ-FXR-01", "ref.fx_rates", "rate", "Completeness", "A rate is delivered for every traded currency",
     "CRITICAL", 100.0, "Head of Market Data", "Enterprise DQ rule", 36),
    ("DQ-FXR-02", "ref.fx_rates", "delivered_at", "Timeliness", "Rates delivered before the 07:00 cut-off",
     "CRITICAL", 100.0, "Head of Market Data", "Enterprise DQ rule", 36),
    ("DQ-GL-01", "gl.journal_lines", "amount", "Accuracy", "Every journal balances (debits equal credits)",
     "CRITICAL", 100.0, "Financial Controller", "Enterprise DQ rule", 12000),
    ("DQ-GL-02", "gl.journal_lines", "cost_centre", "Validity", "Cost centre exists in the hierarchy",
     "MEDIUM", 99.0, "Financial Controller", "Enterprise DQ rule", 12000),
    ("DQ-LN-01", "risk.loan_book", "collateral_value", "Completeness", "Collateral value is present for secured loans",
     "HIGH", 98.0, "Head of Credit Risk Data", "Enterprise DQ rule", 3400),
    ("DQ-LN-02", "risk.loan_book", "pd", "Validity", "Probability of default is between 0 and 1",
     "HIGH", 99.5, "Head of Credit Risk Data", "Enterprise DQ rule", 3400),
    ("DQ-CRD-01", "cards.settlements", "settlement_ref", "Uniqueness", "Settlement reference is unique",
     "HIGH", 99.95, "Head of Cards Operations", "Enterprise DQ rule", 5200),
    ("DQ-CRD-02", "cards.settlements", "merchant_country", "Validity", "Merchant country is a valid ISO code",
     "MEDIUM", 99.0, "Head of Cards Operations", "Enterprise DQ rule", 5200),
]


def build_dq(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    rules = pd.DataFrame(
        [r[:9] for r in DQ_RULES],
        columns=["rule_id", "table_name", "column_name", "dimension", "description", "severity",
                 "threshold_pct", "owner", "origin"])
    rows = []
    drift_start = date(2026, 8, 17)
    for d in _days(START, AS_OF):
        for rule_id, table, *_rest, sev, thr, _owner, _origin, volume in DQ_RULES:
            n = int(volume * (rng.uniform(0.9, 1.1) if volume > 100 else 1))
            if not _is_business_day(d) and volume > 100 and not table.startswith("silver.c"):
                n = int(n * 0.35)
            base_p = (100.0 - thr) / 100.0 * rng.uniform(0.05, 0.35)
            if sev == "MEDIUM" and d < date(2026, 9, 20) and rng.random() < 0.02:
                base_p = (100.0 - thr) / 100.0 * rng.uniform(1.1, 1.6)  # occasional noise breach
            failed = int(rng.binomial(n, base_p)) if base_p > 0 else 0
            if base_p < (100.0 - thr) / 100.0:
                failed = min(failed, int(n * (100.0 - thr) / 100.0))  # background noise stays within threshold
            # INC-3: address completeness drifts down after onboarding form v3.
            if rule_id == "DQ-KYC-01" and d >= drift_start:
                frac = (d - drift_start).days / (AS_OF - drift_start).days
                failed = int(rng.binomial(n, 0.005 + 0.020 * frac))
            # INC-2: FX vendor outage.
            if rule_id == "DQ-FXR-01":
                failed = {date(2026, 10, 7): 9, date(2026, 10, 8): 3}.get(d, 0)
            if rule_id == "DQ-FXR-02":
                failed = {date(2026, 10, 7): 36, date(2026, 10, 8): 36}.get(d, 0)
            if thr >= 100.0 and rule_id.startswith("DQ-GL"):
                failed = 0
            rows.append({"run_date": d, "rule_id": rule_id, "rows_checked": n, "rows_failed": failed})
    return rules, pd.DataFrame(rows)


# ------------------------------------------------------------------- feeds
FEEDS = [  # source_system, feed_name, SLA (hours after midnight)
    ("LedgerCore", "Core banking end-of-day balances", 6.0),
    ("CardHub", "Card scheme settlement file", 5.5),
    ("PayFlow", "SWIFT payment messages", 6.5),
    ("MarketData", "End-of-day FX rates", 7.0),
    ("OnboardIQ", "KYC customer profile extract", 6.0),
    ("GeneralLedger", "GL journal extract", 7.5),
]


def build_feed_arrivals(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    overrides = {
        ("MarketData", date(2026, 10, 7)): 14 + 10 / 60,
        ("MarketData", date(2026, 10, 8)): 9 + 45 / 60,
        ("LedgerCore", date(2026, 10, 5)): 7 + 40 / 60,
    }
    for d in _days(START, AS_OF):
        if not _is_business_day(d):
            continue
        for system, feed, sla in FEEDS:
            arrival = sla - rng.uniform(10, 70) / 60
            if rng.random() < 0.025 and d < date(2026, 10, 1):
                arrival = sla + rng.uniform(5, 80) / 60
            arrival = overrides.get((system, d), arrival)
            rows.append({
                "business_date": d, "source_system": system, "feed_name": feed,
                "sla_ts": _ts(d, sla), "arrived_ts": _ts(d, round(arrival * 60) / 60),
                "minutes_vs_sla": round((arrival - sla) * 60), "on_time": arrival <= sla,
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------- reconciliations
RECONS = [  # recon_id, description, owner team, daily items, base breaks/day, mean resolution days, amount scale
    ("NOSTRO_USD", "USD nostro: Harbor Trust Company, New York vs core ledger", "Cash Reconciliations", 2400, 1.6,
     2.2, 40_000),
    ("NOSTRO_EUR", "EUR nostro: Nordbank AG, Frankfurt vs core ledger", "Cash Reconciliations", 1500, 1.2, 2.2,
     35_000),
    ("NOSTRO_GBP", "GBP nostro: Castle & Moor Bank, London vs core ledger", "Cash Reconciliations", 900, 0.8, 2.0,
     30_000),
    ("GL_SUBLEDGER", "Loans sub-ledger vs general ledger", "Finance Reconciliations", 600, 0.7, 3.5, 120_000),
    ("CARD_SCHEME", "Card scheme settlement vs card ledger", "Cards Operations", 5200, 1.9, 1.6, 4_000),
    ("SWIFT_PAYMENTS", "SWIFT messages vs payment engine", "Payments Operations", 900, 1.0, 1.4, 25_000),
]
BREAK_TYPES = ["MISSING_ENTRY", "AMOUNT_MISMATCH", "VALUE_DATE_MISMATCH", "DUPLICATE", "FX_DIFFERENCE"]
ROOT_CAUSES = {
    "MISSING_ENTRY": "Entry booked by correspondent, not yet booked internally",
    "AMOUNT_MISMATCH": "Charges deducted by intermediary bank",
    "VALUE_DATE_MISMATCH": "Value date differs between statement and ledger",
    "DUPLICATE": "Duplicate posting in one system",
    "FX_DIFFERENCE": "FX rate difference on conversion",
}


def build_recons(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    recons = pd.DataFrame([r[:3] for r in RECONS], columns=["recon_id", "description", "owner_team"])
    runs, breaks = [], []
    seq = 0
    incident_days = {date(2026, 10, 5): 13, date(2026, 10, 6): 16, date(2026, 10, 7): 11}
    for d in _days(START, AS_OF):
        if not _is_business_day(d):
            continue
        for recon_id, _desc, team, items, rate, mean_days, scale in RECONS:
            total = int(items * rng.uniform(0.85, 1.15))
            n_breaks = int(rng.poisson(rate))
            planted = incident_days.get(d, 0) if recon_id == "NOSTRO_EUR" else 0
            for i in range(n_breaks + planted):
                seq += 1
                is_planted = i >= n_breaks
                btype = "VALUE_DATE_MISMATCH" if is_planted else str(rng.choice(BREAK_TYPES, p=[.3, .3, .15, .1, .15]))
                cause = ("Value-date mapping defect after core release R26.10" if is_planted
                         else ROOT_CAUSES[btype])
                age = rng.exponential(mean_days * (3.0 if is_planted else 1.0))
                if not is_planted and rng.random() < 0.06:
                    age += rng.uniform(6, 25)  # long-tail investigations
                resolved = d + timedelta(days=max(1, math.ceil(age)))
                if is_planted and d < date(2026, 10, 8) and rng.random() < 0.15:
                    resolved = date(2026, 10, 8) + timedelta(days=int(rng.integers(0, 2)))
                status = "RESOLVED" if resolved <= AS_OF else "OPEN"
                breaks.append({
                    "break_id": f"BRK-{seq:05d}", "recon_id": recon_id, "opened_date": d,
                    "break_type": btype, "amount_usd": round(float(rng.lognormal(math.log(scale), 0.9)), 2),
                    "root_cause": cause, "owner_team": team, "status": status,
                    "resolved_date": resolved if status == "RESOLVED" else None,
                })
            manual = int(total * rng.uniform(0.008, 0.02)) + (planted * 9 if planted else 0)
            runs.append({"run_date": d, "recon_id": recon_id, "items_total": total,
                         "auto_matched": total - manual - n_breaks - planted, "manual_matched": manual,
                         "breaks_raised": n_breaks + planted})
    return recons, pd.DataFrame(runs), pd.DataFrame(breaks)


# -------------------------------------------------------------- exceptions
# category, team, SLA hours, mean arrivals per business day, regulated
EXCEPTION_TYPES = [
    ("PAYMENT_REPAIR", "Payments Ops", 8, 34, False),
    ("DUPLICATE_PAYMENT", "Payments Ops", 24, 3, False),
    ("NOSTRO_BREAK", "Reconciliations Ops", 48, 9, False),
    ("GL_SUSPENSE", "Finance Ops", 72, 6, False),
    ("SANCTIONS_HIT", "Financial Crime Ops", 24, 5, True),
    ("KYC_EXPIRED_BLOCK", "KYC Ops", 72, 4, True),
    ("LIMIT_BREACH", "Credit Risk Ops", 24, 3, True),
    ("SETTLEMENT_FAIL", "Securities Ops", 24, 7, False),
]
CORRIDOR_LAUNCH = date(2026, 9, 28)


def build_exceptions(rng: np.random.Generator) -> pd.DataFrame:
    """Queue simulation: arrivals during the day; each team clears a fixed number per day, priority first."""
    teams: dict[str, list[tuple]] = {}
    for cat, team, sla, rate, regulated in EXCEPTION_TYPES:
        teams.setdefault(team, []).append((cat, sla, rate, regulated))
    rows = []
    seq = 0
    end = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 23, 59)
    for team, cats in sorted(teams.items()):
        base_daily = sum(rate for _, _, rate, _ in cats)
        capacity = math.ceil(base_daily * 1.15) + 1
        backlog: list[tuple] = []  # heap of (priority, opened_ts, idx)
        team_rows: list[dict] = []
        for d in _days(START, AS_OF):
            arrivals = []
            for cat, sla, rate, regulated in cats:
                lam = rate if _is_business_day(d) else rate * 0.15
                if cat == "PAYMENT_REPAIR" and d >= CORRIDOR_LAUNCH:
                    lam *= 1.65  # INC-4
                for _ in range(int(rng.poisson(lam))):
                    seq += 1
                    opened = _ts(d, float(np.clip(rng.normal(12.0, 3.0), 6.5, 19.5)))
                    priority = int(rng.choice([1, 2, 3, 4], p=[.1, .3, .4, .2]))
                    if regulated:
                        priority = min(priority, 2)
                    team_rows.append({
                        "exception_id": f"EXC-{seq:06d}", "category": cat, "team": team, "priority": f"P{priority}",
                        "regulated": regulated, "opened_ts": opened, "sla_hours": sla,
                        "due_ts": opened + timedelta(hours=sla),
                        "amount_usd": round(float(rng.lognormal(math.log(25_000), 1.4)), 2),
                        "resolved_ts": None,
                    })
                    arrivals.append((priority, opened, len(team_rows) - 1))
            arrivals.sort(key=lambda a: a[1])
            day_capacity = capacity if _is_business_day(d) else math.ceil(capacity * 0.35)
            slots = sorted(_ts(d, h) for h in rng.uniform(8.0, 18.5, size=day_capacity))
            a = 0
            for slot in slots:
                while a < len(arrivals) and arrivals[a][1] <= slot - timedelta(minutes=20):
                    heapq.heappush(backlog, arrivals[a])
                    a += 1
                if backlog:
                    _, opened, idx = heapq.heappop(backlog)
                    team_rows[idx]["resolved_ts"] = max(slot, opened + timedelta(minutes=25))
            for item in arrivals[a:]:
                heapq.heappush(backlog, item)
        for r in team_rows:
            if r["resolved_ts"] is not None and r["resolved_ts"] > end:
                r["resolved_ts"] = None
            r["status"] = "RESOLVED" if r["resolved_ts"] is not None else "OPEN"
        rows.extend(team_rows)
    df = pd.DataFrame(rows).sort_values("opened_ts").reset_index(drop=True)
    return df


# --------------------------------------------------------------------- KYC
REVIEW_MONTHS = {"HIGH": 12, "MEDIUM": 24, "LOW": 36}


def build_kyc_reviews(rng: np.random.Generator, customers: pd.DataFrame) -> pd.DataFrame:
    """Periodic KYC reviews for every Project 02 customer, with a risk-based cycle."""
    rows = []
    high = sorted(customers.loc[customers["kyc_risk_rating"] == "HIGH", "customer_id"])
    planted_overdue = high[7]  # INC-5: due 25 Sep, still not completed on 9 Oct
    upcoming = {high[12]: date(2026, 10, 14), high[21]: date(2026, 10, 20), high[30]: date(2026, 10, 27)}
    late_in_july = high[3]
    seq = 0
    for cust in customers.sort_values("customer_id").itertuples():
        months = REVIEW_MONTHS[cust.kyc_risk_rating]
        cycle = months * 30
        first_due = START - timedelta(days=int(rng.integers(0, cycle)))
        due_dates = [first_due + timedelta(days=cycle * k) for k in range(0, 3)]
        if cust.customer_id == planted_overdue:
            due_dates = [date(2025, 9, 25), date(2026, 9, 25)]
        elif cust.customer_id in upcoming:
            due_dates = [upcoming[cust.customer_id] - timedelta(days=365), upcoming[cust.customer_id]]
        elif cust.customer_id == late_in_july:
            due_dates = [date(2025, 7, 14), date(2026, 7, 14)]
        for due in due_dates:
            if due > AS_OF + timedelta(days=60):
                continue
            completed = due - timedelta(days=int(rng.integers(1, 30)))
            if cust.customer_id == planted_overdue and due == date(2026, 9, 25):
                completed = None
            if cust.customer_id in upcoming and due == upcoming[cust.customer_id]:
                completed = None
            if cust.customer_id == late_in_july and due == date(2026, 7, 14):
                completed = date(2026, 7, 20)
            if completed is not None and completed > AS_OF:
                completed = None
            if completed is None and due <= AS_OF and cust.customer_id != planted_overdue:
                completed = due - timedelta(days=1)
            seq += 1
            rows.append({"review_id": f"KYR-{seq:05d}", "customer_id": cust.customer_id,
                         "risk_rating": cust.kyc_risk_rating, "due_date": due, "completed_date": completed})
    return pd.DataFrame(rows)


# --------------------------------------------------------------- op losses
BASEL_TYPES = [
    ("EDPM", "Execution, delivery and process management", .45),
    ("EF", "External fraud", .25),
    ("CPBP", "Clients, products and business practices", .10),
    ("BDSF", "Business disruption and system failures", .08),
    ("IF", "Internal fraud", .03),
    ("EPWS", "Employment practices and workplace safety", .05),
    ("DPA", "Damage to physical assets", .04),
]
BUSINESS_LINES = ["Retail Banking", "Commercial Banking", "Payments and Settlement", "Trading and Sales",
                  "Asset Management"]


def build_op_losses(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    seq = 0
    for d in _days(YEAR_START, AS_OF):
        if not _is_business_day(d):
            continue
        for _ in range(int(rng.poisson(0.55))):
            code = str(rng.choice([b[0] for b in BASEL_TYPES], p=[b[2] for b in BASEL_TYPES]))
            gross = float(rng.lognormal(math.log(22_000), 1.25))
            recovery = gross * float(rng.choice([0, 0, 0.2, 0.5]))
            seq += 1
            rows.append({"event_id": f"OPL-{seq:05d}", "event_date": d, "basel_event_type": code,
                         "business_line": str(rng.choice(BUSINESS_LINES)), "gross_loss_usd": gross,
                         "recovery_usd": recovery, "description": "Operational loss event"})
    df = pd.DataFrame(rows)
    # Scale the background so YTD net loss before the planted event is USD 8.95m.
    net = df["gross_loss_usd"] - df["recovery_usd"]
    factor = 8_950_000 / net.sum()
    df["gross_loss_usd"] = (df["gross_loss_usd"] * factor).round(2)
    df["recovery_usd"] = (df["recovery_usd"] * factor).round(2)
    planted = {"event_id": "OPL-90001", "event_date": date(2026, 9, 22), "basel_event_type": "EDPM",
               "business_line": "Payments and Settlement", "gross_loss_usd": 2_300_000.0, "recovery_usd": 450_000.0,
               "description": "Duplicate outbound payment batch PB-0922-07 (INC-2026-0815)"}
    df = pd.concat([df, pd.DataFrame([planted])], ignore_index=True)
    df["net_loss_usd"] = (df["gross_loss_usd"] - df["recovery_usd"]).round(2)
    return df.sort_values(["event_date", "event_id"]).reset_index(drop=True)


# ------------------------------------------------------------ finance KRIs
def build_finance_kris(rng: np.random.Generator) -> pd.DataFrame:
    """CET1 and NPL move monthly (month-end reporting); LCR is reported daily."""
    cet1 = {6: 13.21, 7: 13.12, 8: 13.04, 9: 12.96, 10: 12.91}
    npl = {6: 1.58, 7: 1.62, 8: 1.66, 9: 1.71, 10: 1.75}
    lcr_override = {date(2026, 10, 6): 121.4, date(2026, 10, 7): 118.9, date(2026, 10, 8): 118.1,
                    date(2026, 10, 9): 117.6}
    rows = []
    lcr = 129.0
    for d in _days(START, AS_OF):
        lcr = 0.85 * lcr + 0.15 * 129.0 + float(rng.normal(0, 1.1))
        value = lcr_override.get(d, round(lcr, 1))
        rows += [
            {"obs_date": d, "kri_id": "CET1_RATIO", "value": cet1[d.month]},
            {"obs_date": d, "kri_id": "LCR", "value": value},
            {"obs_date": d, "kri_id": "NPL_RATIO", "value": npl[d.month]},
        ]
    return pd.DataFrame(rows)


def build_compliance_events() -> pd.DataFrame:
    """Confirmed sanctions breaches and late SAR filings: zero in the period (zero-tolerance metrics)."""
    return pd.DataFrame({"event_id": pd.Series(dtype="string"), "event_date": pd.Series(dtype="object"),
                         "event_type": pd.Series(dtype="string"), "description": pd.Series(dtype="string")})


# --------------------------------------------------------------------- all
def generate(p2_gold: dict[str, pd.DataFrame] | None = None) -> dict[str, pd.DataFrame]:
    """Return every risk-mart table, keyed by table name."""
    rng = np.random.default_rng(SEED)
    p2_gold = p2_gold if p2_gold is not None else load_p2_gold()
    dq_rules, dq_results = build_dq(rng)
    recons, recon_runs, recon_breaks = build_recons(rng)
    return {
        "calendar": build_calendar(),
        "change_events": build_change_events(),
        "dq_rules": dq_rules,
        "dq_results": dq_results,
        "feed_arrivals": build_feed_arrivals(rng),
        "recons": recons,
        "recon_runs": recon_runs,
        "recon_breaks": recon_breaks,
        "exceptions": build_exceptions(rng),
        "kyc_reviews": build_kyc_reviews(rng, p2_gold["customer_360"]),
        "op_loss_events": build_op_losses(rng),
        "finance_kris": build_finance_kris(rng),
        "compliance_events": build_compliance_events(),
    }


def export(out_dir: Path | str) -> dict[str, int]:
    """Write the sample dataset as CSV (the copy committed under data/sample)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, df in generate().items():
        df.to_csv(out_dir / f"{name}.csv", index=False)
        counts[name] = len(df)
    return counts
