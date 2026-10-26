"""Synthetic source-system extracts for Ledgerline Bank (a fictional bank).

Produces the files that upstream systems would drop into a landing zone:

* ``reference/``  branches, country risk tiers and daily FX rates (full refresh)
* ``batch=<id>/`` customers.csv, accounts.csv, transactions.jsonl and a
  ``manifest.json`` with control totals, as a core-banking extract would send.

Batch 1 is a September 2026 month-end load. Batch 2 is the first week of
October: new customers and transactions, customer attribute changes (for SCD
Type 2), account status changes (for upserts) and late re-sends of September
transactions (to prove the load is idempotent).

A third delivery (``incoming/batch=20261014``) is the incident scenario: its
transactions file is cut off mid-transfer, so it no longer matches its manifest.
``Pipeline.incident()`` moves it into the landing zone, and the Bronze controls
must block it.

Known data defects and AML scenarios are planted on purpose and returned in an
``Oracle`` so tests can check that the pipeline catches exactly those, no more
and no less. Everything is generated from a fixed seed, so a run is
reproducible. No real people, accounts or institutions are represented.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SEED = 20260930
HOME_COUNTRY = "US"
BATCHES = {
    "20260930": (date(2026, 9, 1), date(2026, 9, 30)),
    "20261007": (date(2026, 10, 1), date(2026, 10, 7)),
}
INCIDENT_BATCH = "20261014"
INCIDENT_DAYS = (date(2026, 10, 8), date(2026, 10, 14))
INCIDENT_KEEP_FRACTION = 0.9  # share of transaction lines that arrive intact before the transfer cut off

FIRST_NAMES = [
    "Aarav", "Amelia", "Andre", "Beatriz", "Chen", "Chloe", "Daniel", "Divya", "Elena", "Ethan", "Farah", "Gabriel",
    "Grace", "Hannah", "Hiro", "Isabel", "Jamal", "Julia", "Kofi", "Laila", "Liam", "Lucia", "Marcus", "Maya",
    "Mei", "Mohammed", "Nadia", "Noah", "Olivia", "Omar", "Priya", "Rafael", "Rohan", "Sara", "Sofia", "Tariq",
    "Theo", "Uma", "Victor", "Wei", "Yara", "Zoe",
]
LAST_NAMES = [
    "Adeyemi", "Alvarez", "Bennett", "Carter", "Chowdhury", "Costa", "Dubois", "Evans", "Fischer", "Garcia",
    "Gupta", "Hansen", "Ibrahim", "Ito", "Jensen", "Kapoor", "Kim", "Kowalski", "Lee", "Martin", "Mensah",
    "Moreau", "Nakamura", "Novak", "Okafor", "Patel", "Quinn", "Rossi", "Santos", "Schmidt", "Silva", "Singh",
    "Tanaka", "Torres", "Walker", "Wong", "Young", "Zhang",
]
BRANCHES = [
    ("B001", "Ledgerline Midtown", "New York", "Northeast"),
    ("B002", "Ledgerline Financial District", "New York", "Northeast"),
    ("B003", "Ledgerline Back Bay", "Boston", "Northeast"),
    ("B004", "Ledgerline Loop", "Chicago", "Midwest"),
    ("B005", "Ledgerline Uptown", "Dallas", "South"),
    ("B006", "Ledgerline Brickell", "Miami", "South"),
    ("B007", "Ledgerline SoMa", "San Francisco", "West"),
    ("B008", "Ledgerline Pearl District", "Portland", "West"),
    ("B009", "Ledgerline Capitol Hill", "Seattle", "West"),
    ("B010", "Ledgerline Digital", "Online", "Digital"),
]
# Real jurisdictions are all tiered STANDARD. Elevated and high tiers use fictional
# ISO user-assigned codes, so the data makes no claims about real countries.
COUNTRY_RISK = [
    ("US", "United States", "STANDARD"),
    ("CA", "Canada", "STANDARD"),
    ("GB", "United Kingdom", "STANDARD"),
    ("DE", "Germany", "STANDARD"),
    ("FR", "France", "STANDARD"),
    ("IN", "India", "STANDARD"),
    ("SG", "Singapore", "STANDARD"),
    ("AE", "United Arab Emirates", "STANDARD"),
    ("MX", "Mexico", "STANDARD"),
    ("JP", "Japan", "STANDARD"),
    ("XE", "Elvaria (fictional)", "ELEVATED"),
    ("XR", "Republic of Xerxia (fictional)", "HIGH"),
    ("XS", "Sorvania (fictional)", "HIGH"),
]
HIGH_RISK_COUNTRIES = [c for c, _, tier in COUNTRY_RISK if tier == "HIGH"]
FX_BASE = {"USD": 1.0, "EUR": 1.0850, "GBP": 1.2710, "INR": 0.01195, "SGD": 0.7420, "AED": 0.27230, "JPY": 0.00668}
VALID_CURRENCIES = sorted(FX_BASE)
CUSTOMER_COUNTRY_CCY = {"US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "IN": "INR", "SG": "SGD", "AE": "AED",
                        "CA": "USD", "JP": "JPY", "MX": "USD"}
SEGMENTS = [("RETAIL", 0.70), ("PREMIER", 0.15), ("SME", 0.12), ("CORPORATE", 0.03)]
RISK_RATINGS = [("LOW", 0.72), ("MEDIUM", 0.23), ("HIGH", 0.05)]
PRODUCTS_BY_SEGMENT = {
    "RETAIL": ["CHECKING", "SAVINGS", "CREDIT_CARD"],
    "PREMIER": ["CHECKING", "SAVINGS", "CREDIT_CARD", "TERM_DEPOSIT"],
    "SME": ["BUSINESS_CURRENT", "BUSINESS_SAVINGS", "CREDIT_CARD"],
    "CORPORATE": ["BUSINESS_CURRENT", "BUSINESS_SAVINGS"],
}
# (type, weight, channels, median amount, hard cap). Caps keep random activity
# below every AML threshold so only the planted scenarios raise alerts.
TXN_TYPES = [
    ("CARD_PURCHASE", 45, ["POS", "ECOM"], 45, 2_500),
    ("ACH_IN", 10, ["BATCH"], 2_800, 15_000),
    ("ACH_OUT", 10, ["BATCH"], 600, 8_000),
    ("TRANSFER_OUT", 8, ["MOBILE", "ONLINE"], 300, 8_000),
    ("TRANSFER_IN", 7, ["MOBILE", "ONLINE"], 300, 8_000),
    ("CASH_WITHDRAWAL", 7, ["ATM", "BRANCH"], 200, 3_000),
    ("CASH_DEPOSIT", 6, ["BRANCH", "ATM"], 700, 7_500),
    ("WIRE_IN", 3.5, ["ONLINE", "BRANCH"], 4_000, 40_000),
    ("WIRE_OUT", 3.5, ["ONLINE", "BRANCH"], 3_500, 40_000),
]
MERCHANTS = ["Northwind Grocers", "Contoso Fuel", "Fabrikam Electronics", "Tailspin Air", "Adventure Works Outdoor",
             "Wide World Imports", "Litware Books", "Proseware Pharmacy", "Coho Coffee", "Lucerne Utilities"]
COUNTERPARTIES = ["Blue Yonder Ltd", "Margie Travel", "Woodgrove Payroll", "Alpine Ski House", "Trey Research",
                  "Graphic Design Institute", "Humongous Insurance", "City Power & Light", "Fourth Coffee Holdings"]


@dataclass
class Oracle:
    """What was planted, so tests can assert the pipeline found exactly this."""

    customer_defects: dict[str, list[str]] = field(default_factory=dict)  # rule -> customer ids / row keys
    account_defects: dict[str, list[str]] = field(default_factory=dict)
    txn_defects: dict[str, list[str]] = field(default_factory=dict)  # rule -> txn ids
    duplicate_txn_rows: dict[str, int] = field(default_factory=dict)  # batch -> exact re-sent rows
    duplicate_customer_rows: int = 0
    duplicate_account_rows: int = 0
    cross_batch_resends: list[str] = field(default_factory=list)  # Sept txn ids re-sent in batch 2
    aml_scenarios: dict[str, list[str]] = field(default_factory=dict)  # rule code -> customer ids
    # Alerts the rules raise on legitimate activity; an investigator should close them with a reason.
    aml_false_positives: dict[str, list[str]] = field(default_factory=dict)
    scd2_changed_customers: list[str] = field(default_factory=list)
    unchanged_resent_customers: list[str] = field(default_factory=list)
    account_status_changes: list[str] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)  # batch -> file -> rows
    incident: dict = field(default_factory=dict)  # what was cut off in the incident batch

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _weighted(rng: random.Random, options: list[tuple[str, float]]) -> str:
    return rng.choices([o for o, _ in options], weights=[w for _, w in options])[0]


def _amount(rng: random.Random, median: float, cap: float) -> float:
    return round(min(rng.lognormvariate(0, 0.9) * median, cap), 2)


def _ts(d: date, rng: random.Random) -> str:
    t = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(seconds=rng.randint(6 * 3600, 22 * 3600))
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


class _Generator:
    def __init__(self, seed: int, n_customers: int, txns_per_day: int) -> None:
        self.rng = random.Random(seed)
        self.n_customers = n_customers
        self.txns_per_day = txns_per_day
        self.oracle = Oracle()
        self.customers: dict[str, dict] = {}
        self.accounts: dict[str, dict] = {}
        self.txn_seq = 0
        self.sept_txns: list[dict] = []

    # ------------------------------------------------------------- customers
    def _new_customer(self, idx: int, onboard_before: date) -> dict:
        rng = self.rng
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        country = HOME_COUNTRY if rng.random() < 0.82 else rng.choice(["GB", "DE", "FR", "IN", "SG", "AE", "CA", "JP"])
        segment = _weighted(rng, SEGMENTS)
        return {
            "customer_id": f"C{idx:06d}",
            "first_name": first,
            "last_name": last,
            "date_of_birth": (date(1950, 1, 1) + timedelta(days=rng.randint(0, 18_000))).isoformat(),
            "email": f"{first}.{last}{idx}@example.com".lower(),
            "phone": f"+1-555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
            "country": country,
            "segment": segment,
            "kyc_risk_rating": _weighted(rng, RISK_RATINGS),
            "pep_flag": "Y" if rng.random() < 0.01 else "N",
            "onboarding_date": (onboard_before - timedelta(days=rng.randint(30, 3_650))).isoformat(),
            "branch_code": rng.choice(BRANCHES)[0],
        }

    def _new_accounts(self, customer: dict, start_seq: int, open_after: date) -> list[dict]:
        rng = self.rng
        products = PRODUCTS_BY_SEGMENT[customer["segment"]]
        count = rng.choice([1, 1, 2, 2, 3])
        accounts = []
        for i, product in enumerate(rng.sample(products, k=min(count, len(products)))):
            ccy = CUSTOMER_COUNTRY_CCY.get(customer["country"], "USD") if rng.random() < 0.6 else "USD"
            accounts.append({
                "account_id": f"A{start_seq + i:07d}",
                "customer_id": customer["customer_id"],
                "product": product,
                "currency": ccy,
                "open_date": max(date.fromisoformat(customer["onboarding_date"]), open_after).isoformat(),
                "status": "ACTIVE",
                "branch_code": customer["branch_code"],
                "credit_limit": str(rng.choice([2_000, 5_000, 10_000, 25_000])) if product == "CREDIT_CARD" else "",
            })
        return accounts

    # ---------------------------------------------------------- transactions
    def _txn(self, account: dict, d: date, txn_type: str, amount: float, *, channel: str | None = None,
             cp_country: str | None = None, cp_name: str | None = None) -> dict:
        rng = self.rng
        self.txn_seq += 1
        spec = next(t for t in TXN_TYPES if t[0] == txn_type)
        if cp_country is None:
            cp_country = account["_country"]
            if txn_type.startswith("WIRE") and rng.random() < 0.3:
                cp_country = rng.choice([c for c, _, tier in COUNTRY_RISK if tier == "STANDARD"])
                # Small, sub-threshold flows to high-risk places are normal background noise.
                if amount * FX_BASE[account["currency"]] < 9_000 and rng.random() < 0.08:
                    cp_country = rng.choice(HIGH_RISK_COUNTRIES + ["XE"])
        if cp_name is None:
            cp_name = rng.choice(MERCHANTS) if txn_type == "CARD_PURCHASE" else (
                "Cash" if txn_type.startswith("CASH") else rng.choice(COUNTERPARTIES))
        return {
            "txn_id": f"T{self.txn_seq:09d}",
            "account_id": account["account_id"],
            "txn_ts": _ts(d, rng),
            "amount": f"{amount:.2f}",
            "currency": account["currency"],
            "txn_type": txn_type,
            "channel": channel or rng.choice(spec[2]),
            "counterparty_name": cp_name,
            "counterparty_country": cp_country,
            "reference": f"REF{rng.randint(10**7, 10**8 - 1)}",
        }

    def _random_txns(self, days: list[date], per_day: int, active_accounts: list[dict]) -> list[dict]:
        rng = self.rng
        rows = []
        weights = [t[1] for t in TXN_TYPES]
        for d in days:
            for _ in range(per_day):
                account = rng.choice(active_accounts)
                spec = rng.choices(TXN_TYPES, weights=weights)[0]
                # Amounts are drawn in USD terms, then expressed in the account currency.
                amount = round(_amount(rng, spec[3], spec[4]) / FX_BASE[account["currency"]], 2)
                rows.append(self._txn(account, d, spec[0], amount))
        return rows

    def _account_for(self, customer_id: str) -> dict:
        return next(a for a in self.accounts.values() if a["customer_id"] == customer_id and a["status"] == "ACTIVE")

    def _plant_structuring(self, customer_id: str, d: date, n: int = 4) -> list[dict]:
        acct = self._account_for(customer_id)
        return [self._txn(acct, d, "CASH_DEPOSIT", round(self.rng.uniform(9_200, 9_950), 2), channel="BRANCH")
                for _ in range(n)]

    def _plant_high_risk_wire(self, customer_id: str, d: date) -> list[dict]:
        acct = self._account_for(customer_id)
        amount = round(self.rng.uniform(12_000, 48_000), 2)
        return [self._txn(acct, d, "WIRE_OUT", amount, cp_country=self.rng.choice(HIGH_RISK_COUNTRIES),
                          cp_name="Kestrel Trading FZE")]

    def _plant_rapid_movement(self, customer_id: str, d: date) -> list[dict]:
        acct = self._account_for(customer_id)
        inbound = round(self.rng.uniform(70_000, 90_000), 2)
        return [
            self._txn(acct, d, "WIRE_IN", inbound, cp_name="Harbor Point Holdings"),
            self._txn(acct, d + timedelta(days=1), "WIRE_OUT", round(inbound * 0.96, 2), cp_name="Meridian Escrow"),
        ]

    def _plant_payroll_cycle(self, d: date, inbound: float) -> list[dict]:
        """A false positive: a corporate client's monthly receipt, paid straight out as payroll.

        It uses its own RNG so the rest of the data set is unchanged. The September cycle stays
        under the RAPID_MOVEMENT threshold; the October cycle trips it. An investigator who looks
        at the history sees the same counterparties, a month apart, on a low-risk corporate account.
        """
        rng = random.Random(SEED + 3)
        if not self.oracle.aml_false_positives:
            blocked = set(sum(self.oracle.aml_scenarios.values(), [])) | set(
                sum(self.oracle.customer_defects.values(), []))
            pool = sorted({a["customer_id"] for a in self.accounts.values()
                           if a["product"] == "BUSINESS_CURRENT" and a["currency"] == "USD" and a["status"] == "ACTIVE"
                           and self.customers[a["customer_id"]]["segment"] == "CORPORATE"
                           and self.customers[a["customer_id"]]["kyc_risk_rating"] == "LOW"
                           and a["customer_id"] not in blocked})
            self.oracle.aml_false_positives = {"RAPID_MOVEMENT": [rng.choice(pool)] if pool else []}
        if not self.oracle.aml_false_positives["RAPID_MOVEMENT"]:
            return []  # small test data sets may have no suitable corporate customer
        cid = self.oracle.aml_false_positives["RAPID_MOVEMENT"][0]
        acct = next(a for a in self.accounts.values() if a["customer_id"] == cid and a["product"] == "BUSINESS_CURRENT")
        saved, self.rng = self.rng, rng
        try:
            return [
                self._txn(acct, d, "WIRE_IN", inbound, cp_country="US", cp_name="Northbridge Logistics Inc"),
                self._txn(acct, d + timedelta(days=1), "WIRE_OUT", round(inbound * 0.97, 2), cp_country="US",
                          cp_name="Woodgrove Payroll Services"),
            ]
        finally:
            self.rng = saved

    @staticmethod
    def _usd_customer_ids(customers: list[dict], accounts: dict[str, dict]) -> list[str]:
        # Planted scenarios use USD accounts so the thresholds read naturally in dollars.
        usd = {a["customer_id"] for a in accounts.values() if a["currency"] == "USD" and a["status"] == "ACTIVE"}
        return [c["customer_id"] for c in customers if c["customer_id"] in usd]

    # ---------------------------------------------------------------- batches
    def batch1(self) -> dict[str, list[dict]]:
        rng, oracle = self.rng, self.oracle
        start, end = BATCHES["20260930"]
        customers = [self._new_customer(i + 1, start) for i in range(self.n_customers)]
        for c in customers:
            self.customers[c["customer_id"]] = c
        acct_seq = 1
        for c in customers:
            for a in self._new_accounts(c, acct_seq, date(2016, 1, 1)):
                self.accounts[a["account_id"]] = a
                acct_seq += 1
        self.next_account_seq = acct_seq
        for a in self.accounts.values():
            a["_country"] = HOME_COUNTRY

        txns = self._random_txns(_days(start, end), self.txns_per_day, list(self.accounts.values()))

        # Planted AML scenarios on distinct USD customers.
        usd_ids = self._usd_customer_ids(customers, self.accounts)
        picks = rng.sample(usd_ids, 6)
        oracle.aml_scenarios = {"STRUCTURING": picks[0:2], "HIGH_RISK_JURISDICTION": picks[2:5],
                                "RAPID_MOVEMENT": picks[5:6]}
        txns += self._plant_structuring(picks[0], date(2026, 9, 9))
        txns += self._plant_structuring(picks[1], date(2026, 9, 22), n=3)
        for cid, day in zip(picks[2:5], (4, 15, 27), strict=True):
            txns += self._plant_high_risk_wire(cid, date(2026, 9, day))
        txns += self._plant_rapid_movement(picks[5], date(2026, 9, 18))
        self.sept_txns = list(txns)

        # Planted transaction defects, each on its own clean row.
        defect_pool = rng.sample([t for t in txns if t["account_id"] and t["txn_type"] == "CARD_PURCHASE"], 18)
        plan = [("amount_present", 4), ("amount_positive", 3), ("valid_currency", 3), ("known_account", 4),
                ("not_future_dated", 2), ("valid_timestamp", 2)]
        oracle.txn_defects = {}
        i = 0
        for rule, n in plan:
            for t in defect_pool[i:i + n]:
                if rule == "amount_present":
                    t["amount"] = ""
                elif rule == "amount_positive":
                    t["amount"] = f"-{t['amount']}"
                elif rule == "valid_currency":
                    t["currency"] = "XXX"
                elif rule == "known_account":
                    t["account_id"] = f"A9{rng.randint(100000, 999999)}"
                elif rule == "not_future_dated":
                    t["txn_ts"] = "2027-01-15T09:30:00Z"
                elif rule == "valid_timestamp":
                    t["txn_ts"] = "31/09/2026 25:00"
                oracle.txn_defects.setdefault(rule, []).append(t["txn_id"])
            i += n
        missing_id = rng.choice([t for t in txns if t["txn_id"] not in sum(oracle.txn_defects.values(), [])
                                 and t["txn_type"] == "CARD_PURCHASE"])
        missing_id["txn_id"] = ""
        oracle.txn_defects["txn_id_present"] = [""]

        dupes = [dict(t) for t in rng.sample([t for t in txns if t["txn_id"]], 25)]
        oracle.duplicate_txn_rows["20260930"] = len(dupes)
        txns += dupes
        rng.shuffle(txns)

        # Customer and account defects.
        clean = [c for c in customers if c["customer_id"] not in sum(oracle.aml_scenarios.values(), [])]
        bad = rng.sample(clean, 9)
        for c in bad[0:3]:
            c["email"] = c["email"].replace("@", "_at_")
        for c in bad[3:5]:
            c["date_of_birth"] = ""
        for c in bad[5:7]:
            c["country"] = "ZZ"
        bad[7]["kyc_risk_rating"] = "UNKNOWN"
        oracle.customer_defects = {
            "valid_email": [c["customer_id"] for c in bad[0:3]],
            "dob_present": [c["customer_id"] for c in bad[3:5]],
            "known_country": [c["customer_id"] for c in bad[5:7]],
            "valid_risk_rating": [bad[7]["customer_id"]],
            "customer_id_present": [""],
        }
        no_id = dict(bad[8])
        no_id["customer_id"] = ""
        cust_rows = [dict(c) for c in customers] + [no_id] + [dict(c) for c in rng.sample(clean, 2)]
        oracle.duplicate_customer_rows = 2

        acct_rows = [{k: v for k, v in a.items() if not k.startswith("_")} for a in self.accounts.values()]
        orphans = []
        for _ in range(2):
            orphan = dict(rng.choice(acct_rows))
            self.next_account_seq += 1
            orphan["account_id"] = f"A{self.next_account_seq:07d}"
            orphan["customer_id"] = f"C9{rng.randint(10000, 99999)}"
            orphans.append(orphan)
        oracle.account_defects = {"known_customer": [o["account_id"] for o in orphans]}
        acct_rows += orphans + [dict(rng.choice(acct_rows))]
        oracle.duplicate_account_rows = 1
        txns += self._plant_payroll_cycle(date(2026, 9, 29), inbound=48_700.00)  # below the 50k rule threshold
        return {"customers": cust_rows, "accounts": acct_rows, "transactions": txns}

    def batch2(self) -> dict[str, list[dict]]:
        rng, oracle = self.rng, self.oracle
        start, end = BATCHES["20261007"]
        blocked = set(sum(oracle.customer_defects.values(), [])) | set(sum(oracle.aml_scenarios.values(), []))
        stable = [c for c in self.customers.values() if c["customer_id"] not in blocked]

        changed = rng.sample(stable, 40)
        for c in changed[:20]:
            c["kyc_risk_rating"] = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "MEDIUM"}[c["kyc_risk_rating"]]
        for c in changed[20:30]:
            c["segment"] = "PREMIER" if c["segment"] == "RETAIL" else "RETAIL"
        for c in changed[30:40]:
            c["country"] = "GB" if c["country"] != "GB" else "SG"
        oracle.scd2_changed_customers = sorted(c["customer_id"] for c in changed)
        unchanged = rng.sample([c for c in stable if c not in changed], 5)
        oracle.unchanged_resent_customers = sorted(c["customer_id"] for c in unchanged)

        new_customers = [self._new_customer(self.n_customers + i + 1, start) for i in range(25)]
        new_accounts = []
        for c in new_customers:
            c["onboarding_date"] = start.isoformat()
            self.customers[c["customer_id"]] = c
            for a in self._new_accounts(c, self.next_account_seq + 1, date.fromisoformat(c["onboarding_date"])):
                a["_country"] = HOME_COUNTRY
                self.accounts[a["account_id"]] = a
                new_accounts.append(a)
                self.next_account_seq += 1
        stable_ids = {c["customer_id"] for c in stable}
        closing = rng.sample([a for a in self.accounts.values()
                              if a["customer_id"] in stable_ids and a["status"] == "ACTIVE"], 10)
        for a in closing:
            a["status"] = rng.choice(["DORMANT", "CLOSED"])
        oracle.account_status_changes = sorted(a["account_id"] for a in closing)

        active = [a for a in self.accounts.values() if a["status"] == "ACTIVE"]
        txns = self._random_txns(_days(start, end), self.txns_per_day, active)
        struct_cid = rng.choice([cid for cid in self._usd_customer_ids(stable, self.accounts)
                                 if cid not in {c["customer_id"] for c in changed}])
        oracle.aml_scenarios["STRUCTURING"].append(struct_cid)
        txns += self._plant_structuring(struct_cid, date(2026, 10, 3))

        defects = rng.sample([t for t in txns if t["txn_type"] == "CARD_PURCHASE"], 3)
        defects[0]["amount"] = ""
        defects[1]["amount"] = ""
        defects[2]["account_id"] = "A9000001"
        oracle.txn_defects["amount_present"] += [defects[0]["txn_id"], defects[1]["txn_id"]]
        oracle.txn_defects["known_account"].append(defects[2]["txn_id"])

        resends = [dict(t) for t in rng.sample([t for t in self.sept_txns if t["txn_id"] and t["amount"]
                                                and t["txn_id"] not in sum(oracle.txn_defects.values(), [])], 10)]
        oracle.cross_batch_resends = sorted(t["txn_id"] for t in resends)
        dupes = [dict(t) for t in rng.sample(txns, 5)]
        oracle.duplicate_txn_rows["20261007"] = len(dupes)
        txns += resends + dupes
        rng.shuffle(txns)

        txns += self._plant_payroll_cycle(date(2026, 10, 6), inbound=52_400.00)  # same cycle, now above it

        cust_rows = [dict(c) for c in changed + unchanged + new_customers]
        acct_rows = [{k: v for k, v in a.items() if not k.startswith("_")} for a in closing + new_accounts]
        return {"customers": cust_rows, "accounts": acct_rows, "transactions": txns}

    def incident_batch(self, seed: int) -> dict[str, list[dict]]:
        """Week 2 of October: routine changes and transactions. Its own RNG, so batches 1 and 2 never change."""
        self.rng = rng = random.Random(seed)
        stable = [c for c in self.customers.values() if c["customer_id"] not in set(self.oracle.scd2_changed_customers)]
        changed = [dict(c) for c in rng.sample(stable, 6)]
        for c in changed:
            c["kyc_risk_rating"] = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "MEDIUM"}[c["kyc_risk_rating"]]
        active = [a for a in self.accounts.values() if a["status"] == "ACTIVE"]
        closing = [dict(a) for a in rng.sample(active, 3)]
        for a in closing:
            a["status"] = "DORMANT"
        txns = self._random_txns(_days(*INCIDENT_DAYS), self.txns_per_day, active)
        rng.shuffle(txns)
        accounts = [{k: v for k, v in a.items() if not k.startswith("_")} for a in closing]
        return {"customers": changed, "accounts": accounts, "transactions": txns}


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def file_fingerprint(path: Path) -> dict:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _write_batch(folder: Path, batch_id: str, business_date: date, files: dict[str, list[dict]]) -> dict:
    """Write one delivery plus its manifest (record counts, control total, file sizes and checksums)."""
    _write_csv(folder / "customers.csv", files["customers"])
    _write_csv(folder / "accounts.csv", files["accounts"])
    _write_jsonl(folder / "transactions.jsonl", files["transactions"])
    txns = files["transactions"]
    manifest = {
        "source_system": "LEDGERLINE_CORE",
        "batch_id": batch_id,
        "business_date": business_date.isoformat(),
        "record_counts": {k: len(v) for k, v in files.items()},
        "transactions_control_total": round(sum(float(t["amount"]) for t in txns if t["amount"]), 2),
        "files": {name: file_fingerprint(folder / name)
                  for name in ("customers.csv", "accounts.csv", "transactions.jsonl")},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _truncate(path: Path, keep_fraction: float) -> dict:
    """Simulate a transfer that stopped part-way: keep whole lines, then half of the next one."""
    lines = path.read_text(encoding="utf-8").splitlines()
    keep = int(len(lines) * keep_fraction)
    partial = lines[keep][: len(lines[keep]) // 2]
    path.write_text("\n".join(lines[:keep]) + "\n" + partial, encoding="utf-8")
    lost = [json.loads(line) for line in lines[keep:]]
    return {
        "batch_id": INCIDENT_BATCH,
        "file": "transactions.jsonl",
        "expected_rows": len(lines),
        "complete_rows_delivered": keep,
        "partial_rows": 1,
        "rows_lost": len(lines) - keep,
        "amount_lost": round(sum(float(t["amount"]) for t in lost if t["amount"]), 2),
        "first_lost_txn_id": lost[0]["txn_id"],
    }


def _reference(rng: random.Random) -> dict[str, list[dict]]:
    fx = []
    start, end = BATCHES["20260930"][0], BATCHES["20261007"][1]
    levels = dict(FX_BASE)
    for d in _days(start, end):
        for ccy in VALID_CURRENCIES:
            if ccy != "USD":
                levels[ccy] *= 1 + rng.uniform(-0.003, 0.003)
            fx.append({"rate_date": d.isoformat(), "currency": ccy, "usd_rate": f"{levels[ccy]:.6f}"})
    return {
        "branches": [dict(zip(["branch_code", "branch_name", "city", "region"], b, strict=True)) for b in BRANCHES],
        "country_risk": [dict(zip(["country_code", "country_name", "risk_tier"], c, strict=True))
                         for c in COUNTRY_RISK],
        "fx_rates": fx,
    }


def generate(landing: str | Path, *, seed: int = SEED, n_customers: int = 600, txns_per_day: int = 450) -> Oracle:
    """Write reference data and both batches under ``landing``. Returns the oracle of planted facts."""
    landing = Path(landing)
    gen = _Generator(seed, n_customers, txns_per_day)
    for name, rows in _reference(random.Random(seed + 1)).items():
        _write_csv(landing / "reference" / f"{name}.csv", rows)
    for batch_id, build in (("20260930", gen.batch1), ("20261007", gen.batch2)):
        manifest = _write_batch(landing / f"batch={batch_id}", batch_id, BATCHES[batch_id][1], build())
        gen.oracle.counts[batch_id] = manifest["record_counts"]
    # The incident delivery waits in incoming/ until Pipeline.incident() moves it into the landing zone.
    incoming = landing / "incoming" / f"batch={INCIDENT_BATCH}"
    _write_batch(incoming, INCIDENT_BATCH, INCIDENT_DAYS[1], gen.incident_batch(seed + 2))
    gen.oracle.incident = _truncate(incoming / "transactions.jsonl", INCIDENT_KEEP_FRACTION)
    return gen.oracle


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "landing"
    result = generate(target)
    print(json.dumps(result.counts, indent=2))
