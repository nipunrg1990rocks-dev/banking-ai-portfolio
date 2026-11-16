"""Hand-written specifications the test cases are checked against (not imported from the platform).

LIFECYCLE   the allowed status moves per product, written out edge by edge from the business design
            (docs/lifecycle_matrix.md), so a change to the server's state machine shows up as a test failure.
ENDPOINTS   who may call what: the seven-role design from the brief.
API_CHECKS  hand-written API and workflow checks (implemented in testkit/execute.py).
UI_CASES    browser checks (implemented in frontend/e2e).
"""

from __future__ import annotations

C, E, A, CF, CL, IN, S = "CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "INSTRUCTED", "SETTLED"
PS, SF, X, OP = "PARTIALLY_SETTLED", "SETTLEMENT_FAILED", "CANCELLED", "OPEN"


def _edges(text: str) -> set[tuple[str, str]]:
    out = set()
    for line in text.strip().splitlines():
        a, targets = line.split(":")
        out |= {(a.strip(), b.strip()) for b in targets.split(",") if b.strip()}
    return out


# Cash products cleared at a CCP (equities, ETFs)
CASH_CCP = _edges(f"""
{C}: {E}, {X}
{E}: {A}, {X}
{A}: {CF}, {X}
{CF}: {CL}, {A}, {X}
{CL}: {IN}, {A}, {X}
{IN}: {S}, {PS}, {SF}, {A}, {X}
{PS}: {S}, {SF}, {X}
{SF}: {S}, {PS}, {IN}, {A}, {X}
{S}: {X}
""")
# Bilateral cash products (bonds, FX): no clearing stage
CASH_BILATERAL = _edges(f"""
{C}: {E}, {X}
{E}: {A}, {X}
{A}: {CF}, {X}
{CF}: {IN}, {A}, {X}
{IN}: {S}, {PS}, {SF}, {A}, {X}
{PS}: {S}, {SF}, {X}
{SF}: {S}, {PS}, {IN}, {A}, {X}
{S}: {X}
""")
FUTURE = _edges(f"""
{C}: {E}, {X}
{E}: {A}, {X}
{A}: {CF}, {X}
{CF}: {CL}, {A}, {X}
{CL}: {OP}, {A}, {X}
{OP}: EXPIRED, CLOSED, {X}
EXPIRED: {X}
CLOSED: {X}
""")
OPTION = _edges(f"""
{C}: {E}, {X}
{E}: {A}, {X}
{A}: {CF}, {X}
{CF}: {CL}, {A}, {X}
{CL}: {IN}, {A}, {X}
{IN}: {OP}, {PS}, {SF}, {A}, {X}
{PS}: {OP}, {SF}, {X}
{SF}: {OP}, {PS}, {IN}, {A}, {X}
{OP}: EXPIRED, EXERCISED, {X}
EXPIRED: {X}
""")
IRS = _edges(f"""
{C}: {E}, {X}
{E}: {A}, {X}
{A}: {CF}, {X}
{CF}: {CL}, {A}, {X}
{CL}: {OP}, {A}, {X}
{OP}: MATURED, {X}
MATURED: {X}
""")
FINANCING = _edges(f"""
{C}: {E}, {X}
{E}: {A}, {X}
{A}: {CF}, {X}
{CF}: {IN}, {A}, {X}
{IN}: {OP}, {PS}, {SF}, {A}, {X}
{PS}: {OP}, {SF}, {X}
{SF}: {OP}, {PS}, {IN}, {A}, {X}
{OP}: CLOSED, {X}
CLOSED: {X}
""")

LIFECYCLE = {"EQUITY": CASH_CCP, "ETF": CASH_CCP, "GOVT_BOND": CASH_BILATERAL, "CORP_BOND": CASH_BILATERAL,
             "FX_SPOT": CASH_BILATERAL, "FX_FORWARD": CASH_BILATERAL, "FUTURE": FUTURE, "OPTION": OPTION, "IRS": IRS,
             "REPO": FINANCING, "REVERSE_REPO": FINANCING, "SEC_LOAN": FINANCING, "SEC_BORROW": FINANCING}
MAIN_PATH = {
    "EQUITY": [C, E, A, CF, CL, IN, S], "ETF": [C, E, A, CF, CL, IN, S],
    "GOVT_BOND": [C, E, A, CF, IN, S], "CORP_BOND": [C, E, A, CF, IN, S], "FX_SPOT": [C, E, A, CF, IN, S], "FX_FORWARD": [C, E, A, CF, IN, S],
    "FUTURE": [C, E, A, CF, CL, OP, "EXPIRED"], "OPTION": [C, E, A, CF, CL, IN, OP, "EXPIRED"], "IRS": [C, E, A, CF, CL, OP, "MATURED"],
    "REPO": [C, E, A, CF, IN, OP, "CLOSED"], "REVERSE_REPO": [C, E, A, CF, IN, OP, "CLOSED"], "SEC_LOAN": [C, E, A, CF, IN, OP, "CLOSED"],
    "SEC_BORROW": [C, E, A, CF, IN, OP, "CLOSED"]}
TERMINAL = {S, "EXPIRED", "MATURED", "CLOSED", X, "EXERCISED"}

ROLE_USERS = {"TRADER": "trader1", "MIDDLE_OFFICE": "mo1", "SETTLEMENTS": "settle1", "RECONCILIATION": "recon1", "RISK": "risk1",
              "CHECKER": "checker1", "ADMIN": "admin1"}
ALL = set(ROLE_USERS)
BAD = {"__not_a_field__": 1}  # schema-invalid on purpose: a permitted caller gets 422 and nothing changes

# Write endpoints use inputs that cannot change data, so the matrix can run against a shared database.
ENDPOINTS = [
    {"id": "BOOK", "method": "POST", "path": "/api/trades", "body": BAD, "roles": {"TRADER"}, "action": "book a trade"},
    {"id": "VALIDATE", "method": "POST", "path": "/api/trades/validate", "body": BAD, "roles": ALL, "action": "dry-run validate a trade"},
    {"id": "AMEND", "method": "PATCH", "path": "/api/trades/TRD00001", "body": BAD, "roles": {"TRADER", "MIDDLE_OFFICE"}, "action": "amend a trade"},
    {"id": "CANCEL", "method": "POST", "path": "/api/trades/TRD00001/cancel", "body": BAD, "roles": {"TRADER", "MIDDLE_OFFICE"},
     "action": "request a cancellation"},
    {"id": "TRANSITION", "method": "POST", "path": "/api/trades/TRD00001/transition", "body": BAD, "roles": {"MIDDLE_OFFICE", "SETTLEMENTS"},
     "action": "move a trade's status"},
    {"id": "FORCEMATCH", "method": "POST", "path": "/api/trades/TRD00001/force-match", "body": BAD, "roles": {"MIDDLE_OFFICE"},
     "action": "force-match a confirmation"},
    {"id": "ORDER", "method": "POST", "path": "/api/orders", "body": BAD, "roles": {"TRADER"}, "action": "enter an order"},
    {"id": "FILL", "method": "POST", "path": "/api/orders/NO-SUCH-ORDER/fills", "body": BAD, "roles": {"TRADER"}, "action": "add fills"},
    {"id": "ORDERCANCEL", "method": "POST", "path": "/api/orders/NO-SUCH-ORDER/cancel", "body": None, "roles": {"TRADER"},
     "action": "cancel an order"},
    {"id": "ORDERBOOK", "method": "POST", "path": "/api/orders/NO-SUCH-ORDER/book", "body": BAD, "roles": {"TRADER"},
     "action": "book a filled order"},
    {"id": "SSI", "method": "POST", "path": "/api/refdata/ssis", "body": BAD, "roles": {"MIDDLE_OFFICE", "ADMIN"},
     "action": "request an SSI change"},
    {"id": "CLIENTCHG", "method": "POST", "path": "/api/refdata/clients/CL001/changes", "body": BAD, "roles": {"MIDDLE_OFFICE", "ADMIN"},
     "action": "request a client change"},
    {"id": "RETRY", "method": "POST", "path": "/api/settlements/NO-SUCH-INSTRUCTION/retry", "body": None, "roles": {"SETTLEMENTS"},
     "action": "retry a failed settlement"},
    {"id": "RECONRUN", "method": "POST", "path": "/api/recon/run?statement_date=not-a-date", "body": None, "roles": {"RECONCILIATION"},
     "action": "run a reconciliation"},
    {"id": "WRITEOFF", "method": "POST", "path": "/api/recon/breaks/NO-SUCH-BREAK/write-off", "body": BAD, "roles": {"RECONCILIATION"},
     "action": "write off a break"},
    {"id": "EXCACTION", "method": "POST", "path": "/api/exceptions/NO-SUCH-EXCEPTION/actions", "body": BAD,
     "roles": {"MIDDLE_OFFICE", "SETTLEMENTS", "RECONCILIATION", "RISK"}, "action": "work an exception"},
    {"id": "APPROVE", "method": "POST", "path": "/api/approvals/NO-SUCH-APPROVAL/decision", "body": BAD, "roles": {"CHECKER"},
     "action": "decide an approval"},
    {"id": "INTERFACE", "method": "POST", "path": "/api/interfaces/BROKER/messages", "body": BAD, "roles": {"MIDDLE_OFFICE", "SETTLEMENTS"},
     "action": "inject an interface message"},
    {"id": "CLOCK", "method": "PUT", "path": "/api/admin/clock", "body": BAD, "roles": {"ADMIN"}, "action": "move the business date"},
    {"id": "PROCESS", "method": "POST", "path": "/api/admin/process", "body": BAD, "roles": {"SETTLEMENTS", "ADMIN"},
     "action": "run end-of-day processing"},
    {"id": "REPLAY", "method": "POST", "path": "/api/admin/replay", "body": BAD, "roles": {"SETTLEMENTS", "ADMIN"},
     "action": "replay processing"},
    {"id": "USERS", "method": "GET", "path": "/api/admin/users", "body": None, "roles": {"ADMIN"}, "action": "list users"},
    {"id": "TESTIMPORT", "method": "POST", "path": "/api/test-runs/import", "body": BAD, "roles": {"ADMIN"}, "action": "import test results"},
    {"id": "BLOTTER", "method": "GET", "path": "/api/trades?page_size=1", "body": None, "roles": ALL, "action": "read the blotter"},
    {"id": "DETAIL", "method": "GET", "path": "/api/trades/TRD00001", "body": None, "roles": ALL, "action": "read a trade"},
    {"id": "POSITIONS", "method": "GET", "path": "/api/positions?page_size=1", "body": None, "roles": ALL, "action": "read positions"},
    {"id": "CASHLADDER", "method": "GET", "path": "/api/cash/ladder", "body": None, "roles": ALL, "action": "read the cash ladder"},
    {"id": "EXCEPTIONS", "method": "GET", "path": "/api/exceptions?page_size=1", "body": None, "roles": ALL, "action": "read exceptions"},
    {"id": "APPROVALS", "method": "GET", "path": "/api/approvals", "body": None, "roles": ALL, "action": "read approvals"},
    {"id": "AUDIT", "method": "GET", "path": "/api/audit?page_size=1", "body": None, "roles": ALL, "action": "read the audit trail"},
    {"id": "BREAKS", "method": "GET", "path": "/api/recon/breaks", "body": None, "roles": ALL, "action": "read recon breaks"},
    {"id": "DASH", "method": "GET", "path": "/api/dashboards/executive", "body": None, "roles": ALL, "action": "read a dashboard"},
    {"id": "EVENTS", "method": "GET", "path": "/api/events?page_size=1", "body": None, "roles": ALL, "action": "read lifecycle events"},
]

CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"]
DASHBOARDS = ["executive", "operations", "product", "risk", "data-quality", "testing", "engineering"]
API_CHECKS = (
    [("IDEM-REPLAY", "SC-API-IDEMPOTENCY", "Same Idempotency-Key and body returns the original trade (no second booking)", ["R093"]),
     ("IDEM-CONFLICT", "SC-API-IDEMPOTENCY", "Same Idempotency-Key with a different body is refused 409", ["R093"]),
     ("IFMATCH-MISSING", "SC-API-CONCURRENCY", "Amendment without If-Match is refused 428", ["R098"]),
     ("IFMATCH-STALE", "SC-API-CONCURRENCY", "Amendment against a stale version is refused 409", ["R098"]),
     ("AMEND-NONECON", "SC-WF-AMEND", "Non-economic amendment applies directly as a new version", ["R091"]),
     ("AMEND-ECON-PENDING", "SC-WF-AMEND", "Economic amendment waits for checker approval", ["R091"]),
     ("SELF-APPROVAL", "SC-WF-MAKER-CHECKER", "Maker cannot approve their own request; the attempt is audited", ["R092"]),
     ("MAKER-NOT-CHECKER", "SC-WF-MAKER-CHECKER", "A trader cannot approve their own amendment even if they are the maker", ["R092"]),
     ("CHECKER-APPROVES", "SC-WF-MAKER-CHECKER", "A different checker approves; the economic version is applied", ["R091", "R092"]),
     ("AMEND-AFTER-SETTLE", "SC-WF-AMEND", "Economic amendment of a settled trade is refused", ["R090"]),
     ("CANCEL-REVERSAL", "SC-WF-CANCEL", "Approved cancellation of a settled trade posts exact reversals", ["R097"]),
     ("CANCEL-REBOOK", "SC-WF-CANCEL", "Cancel and rebook links the new trade to the old one", ["R097"]),
     ("INVALID-TRANSITION", "SC-API-STATE", "Skipping lifecycle stages through the API is refused 409", ["R099"]),
     ("IFACE-DUP", "SC-API-INTERFACE", "A duplicate interface message is ignored", ["R094"]),
     ("IFACE-GAP", "SC-API-INTERFACE", "An out-of-order message is parked, then released in sequence", ["R094"]),
     ("REPLAY-IDEMPOTENT", "SC-API-REPLAY", "Replaying processing posts nothing new", ["R093"]),
     ("PROCESS-IDEMPOTENT", "SC-API-REPLAY", "Running end-of-day twice for the same date posts nothing new", ["R093"]),
     ("LEDGER-IDEMPOTENT", "SC-API-REPLAY", "Posting the same ledger event twice writes it once", ["R093"]),
     ("PAGE-LIMIT", "SC-API-CONTRACT", "Page size above 500 is refused 422", []),
     ("PAGE-BEYOND", "SC-API-CONTRACT", "A page past the end returns no items and the right total", []),
     ("BAD-SORT", "SC-API-CONTRACT", "Sorting by an unknown column is refused 400", []),
     ("ERROR-ENVELOPE", "SC-API-CONTRACT", "Errors use the envelope with code, message and correlation id", []),
     ("CORRELATION", "SC-API-CONTRACT", "X-Correlation-ID is echoed on success and failure", []),
     ("SCHEMA-422", "SC-API-CONTRACT", "Unknown fields are refused 422 E_SCHEMA", []),
     ("LOGIN-FAIL", "SC-API-AUTH", "Wrong password is refused 401 and audited", []),
     ("TOKEN-BAD", "SC-API-AUTH", "A tampered token is refused 401", []),
     ("EXPORT-CSV", "SC-API-EXPORT", "CSV export row count equals the blotter total for the same filter", []),
     ("EXPORT-XLSX", "SC-API-EXPORT", "XLSX export opens and has the requested columns", []),
     ("DRYRUN-NO-TRACE", "SC-API-VALIDATE", "Dry-run validation leaves no trade, rejection or audit row", []),
     ("REJECT-LOGGED", "SC-API-VALIDATE", "A rejected booking is stored with its rule ids", ["R010"]),
     ("AUDIT-APPEND-ONLY", "SC-API-AUDIT", "Updating or deleting an audit row is blocked by the database", []),
     ("RULES-CATALOGUE", "SC-API-META", "The rule catalogue lists at least 40 rules with error codes", []),
     ("ORDER-VWAP", "SC-API-ORDERS", "Partial fills book at the VWAP to 6 dp", ["R026"]),
     ("ORDER-LIMIT", "SC-API-ORDERS", "A limit order outside the collar is rejected pre-trade", ["R008"]),
     ("SANDBOX-E2E", "SC-API-SANDBOX", "A sandbox trade settles end to end and never shows in the main book", ["R083", "R084"]),
     ("TRADE-DETAIL", "SC-API-TRADES", "Trade detail returns versions, allocations, ledger, audit and allowed transitions", []),
     ("POSITIONS-ORACLE", "SC-API-POSITIONS", "Position and cash totals at the statement date equal the oracle", ["R096"])]
    + [(f"FILTER-CCY-{c}", "SC-API-FILTERS", f"Blotter currency filter {c} returns only {c} trades and the full count", []) for c in CCYS]
    + [(f"DASH-{d.upper()}", "SC-API-DASHBOARDS", f"{d.replace('-', ' ').title()} dashboard returns KPIs with definitions", []) for d in DASHBOARDS]
)

PAGES = [("blotter", "Trade blotter"), ("trade-detail", "Trade detail"), ("book", "Book a trade"), ("orders", "Orders"),
         ("queues", "Work queues"), ("approvals", "Approvals"), ("exceptions", "Exceptions"), ("positions", "Positions and cash"),
         ("recon", "Reconciliation"), ("events", "Lifecycle events"), ("refdata", "Reference data"), ("dashboards", "Dashboards"),
         ("test-runs", "Test runs"), ("reports", "Reports"), ("audit", "Audit trail"), ("admin", "Admin")]
UI_CASES = (
    [(f"LOGIN-{r}", "SC-UI-LOGIN", f"{r.replace('_', ' ').title()} signs in and lands on their workspace", []) for r in ROLE_USERS]
    + [(f"PAGE-{p.upper()}", "SC-UI-PAGES", f"{t} page renders without errors", []) for p, t in PAGES]
    + [(f"DASH-{d.upper()}", "SC-UI-DASHBOARDS", f"{d.replace('-', ' ').title()} dashboard shows KPIs and a chart", []) for d in DASHBOARDS]
    + [(f"DETAIL-{p}", "SC-UI-TRADE-DETAIL", f"{p} trade detail shows its lifecycle path", ["R099"])
       for p in ["EQUITY", "ETF", "GOVT_BOND", "CORP_BOND", "FX_SPOT", "FX_FORWARD", "FUTURE", "OPTION", "IRS", "REPO", "REVERSE_REPO",
                 "SEC_LOAN", "SEC_BORROW"]]
    + [("BLOTTER-FILTER-CCY", "SC-UI-BLOTTER", "Blotter currency filter narrows the grid", []),
       ("BLOTTER-SEARCH", "SC-UI-BLOTTER", "Blotter search finds a trade by id", []),
       ("BLOTTER-OPEN", "SC-UI-BLOTTER", "Clicking a blotter row opens the trade", []),
       ("BOOK-REJECT", "SC-UI-BOOKING", "Booking on a holiday shows the R010 error from the server", ["R010"]),
       ("BOOK-VALIDATE", "SC-UI-BOOKING", "Validate (dry run) shows the derived settlement date", ["R011"]),
       ("APPROVE-SELF", "SC-UI-APPROVALS", "Approvals page lists pending requests for a checker", ["R092"]),
       ("NAV-ROLE-TRADER", "SC-UI-NAV", "Trader navigation offers Book a trade", []),
       ("NAV-ROLE-CHECKER", "SC-UI-NAV", "Checker navigation does not offer Book a trade", []),
       ("MOBILE-BLOTTER", "SC-UI-MOBILE", "Blotter is usable at phone width without horizontal page scroll", []),
       ("MOBILE-DASH", "SC-UI-MOBILE", "Executive dashboard fits a phone screen", []),
       ("LOGOUT", "SC-UI-LOGIN", "Sign out returns to the login page", []),
       ("BAD-LOGIN", "SC-UI-LOGIN", "Wrong password shows an error and stays on login", [])]
)
