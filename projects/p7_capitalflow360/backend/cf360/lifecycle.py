"""Server-side lifecycle state machines, one per product type.

Each product follows its own main path; a few side states (partial settlement, settlement fail,
cancellation) are added by rule. ``check(trade, target)`` raises ``InvalidTransition`` for any
move not in the graph, whichever API or service tries it.
"""

from __future__ import annotations

PATHS = {
    "EQUITY": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "INSTRUCTED", "SETTLED"],
    "ETF": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "INSTRUCTED", "SETTLED"],
    "GOVT_BOND": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "INSTRUCTED", "SETTLED"],
    "CORP_BOND": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "INSTRUCTED", "SETTLED"],
    "FX_SPOT": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "INSTRUCTED", "SETTLED"],
    "FX_FORWARD": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "INSTRUCTED", "SETTLED"],
    "FUTURE": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "OPEN", "EXPIRED"],
    "OPTION": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "INSTRUCTED", "OPEN", "EXPIRED"],
    "IRS": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "OPEN", "MATURED"],
    "REPO": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "INSTRUCTED", "OPEN", "CLOSED"],
    "REVERSE_REPO": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "INSTRUCTED", "OPEN", "CLOSED"],
    "SEC_LOAN": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "INSTRUCTED", "OPEN", "CLOSED"],
    "SEC_BORROW": ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "INSTRUCTED", "OPEN", "CLOSED"],
}

# Stages that do not apply, with the reason (documented in docs/lifecycle_matrix.md)
NOT_APPLICABLE = {
    "GOVT_BOND": {"CLEARED": "OTC bilateral; no CCP in this simulation"},
    "CORP_BOND": {"CLEARED": "OTC bilateral; no CCP in this simulation"},
    "FX_SPOT": {"CLEARED": "Bilateral; payment-versus-payment netting not simulated"},
    "FX_FORWARD": {"CLEARED": "Bilateral; payment-versus-payment netting not simulated"},
    "FUTURE": {"INSTRUCTED": "No delivery of notional; daily variation margin instead",
               "SETTLED": "Positions stay OPEN until expiry; VM settles daily"},
    "IRS": {"INSTRUCTED": "No upfront settlement; scheduled net cash flows instead",
            "SETTLED": "Swap stays OPEN until maturity"},
    "REPO": {"CLEARED": "Bilateral repo; tri-party/CCP not simulated"},
    "REVERSE_REPO": {"CLEARED": "Bilateral repo; tri-party/CCP not simulated"},
    "SEC_LOAN": {"CLEARED": "Bilateral lending"},
    "SEC_BORROW": {"CLEARED": "Bilateral lending"},
}

TERMINAL = {"SETTLED", "EXPIRED", "MATURED", "CLOSED", "CANCELLED", "EXERCISED"}
SETTLEMENT_STATES = {"SETTLED", "PARTIALLY_SETTLED", "OPEN", "CLOSED", "EXPIRED", "MATURED", "EXERCISED"}


class InvalidTransition(Exception):
    def __init__(self, product_type: str, current: str, target: str):
        super().__init__(f"{product_type}: {current} -> {target} is not an allowed transition")
        self.product_type, self.current, self.target = product_type, current, target


def graph(product_type: str) -> dict[str, set[str]]:
    path = PATHS[product_type]
    g: dict[str, set[str]] = {s: set() for s in path}
    for a, b in zip(path, path[1:], strict=False):
        g[a].add(b)
    if "INSTRUCTED" in g:
        after = path[path.index("INSTRUCTED") + 1]
        g.setdefault("PARTIALLY_SETTLED", set())
        g.setdefault("SETTLEMENT_FAILED", set())
        g["INSTRUCTED"] |= {"PARTIALLY_SETTLED", "SETTLEMENT_FAILED"}
        g["PARTIALLY_SETTLED"] |= {after, "SETTLEMENT_FAILED"}
        g["SETTLEMENT_FAILED"] |= {after, "PARTIALLY_SETTLED", "INSTRUCTED"}
    if product_type == "OPTION":
        g["OPEN"] |= {"EXERCISED"}
    if product_type == "FUTURE":
        g["OPEN"] |= {"CLOSED"}
    # amendments send a confirmed trade back for re-matching
    for st in ("CONFIRMED", "CLEARED", "INSTRUCTED", "SETTLEMENT_FAILED"):
        if st in g:
            g[st].add("ALLOCATED")
    # cancellation: always allowed before settlement; afterwards only through an approved cancel (reversal)
    for st in list(g):
        if st not in ("CANCELLED",) and st not in TERMINAL - {"SETTLED", "CLOSED", "EXPIRED", "MATURED"}:
            g[st].add("CANCELLED")
    g["CANCELLED"] = set()
    return g


def allowed(product_type: str, current: str) -> set[str]:
    return graph(product_type).get(current, set())


def check(product_type: str, current: str, target: str) -> None:
    if target not in allowed(product_type, current):
        raise InvalidTransition(product_type, current, target)


def is_settled_state(status: str) -> bool:
    return status in SETTLEMENT_STATES


def matrix() -> list[dict]:
    """Lifecycle applicability matrix for documentation and the UI."""
    stages = ["CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "INSTRUCTED", "SETTLED", "OPEN",
              "EXPIRED", "MATURED", "CLOSED"]
    rows = []
    for p, path in PATHS.items():
        row = {"product_type": p}
        for st in stages:
            if st in path:
                row[st] = "yes"
            else:
                row[st] = NOT_APPLICABLE.get(p, {}).get(st, "n/a")
        rows.append(row)
    return rows
