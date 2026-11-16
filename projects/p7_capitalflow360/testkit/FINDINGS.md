# Defects found by the test suite

Real defects that the catalogue found while it was being built, before any defect was injected.
Each was fixed, and the case that found it now passes in run CLEAN-001.

| # | Found by | What was wrong | Fix |
|---|----------|----------------|-----|
| F1 | TC-WE-WE12 (worked example, phase 3) | Japanese substitute holidays were generated on bank closing days (2 to 3 January), shifting a USD/JPY spot date | `datagen/calendars.py`; data regenerated |
| F2 | TC-NEG-N24-0001..0010, TC-RULE-R024-02/03 | Booking built the R024 (allocation quantity), R026 (fills) and R003 (allocation account) issues with the field name in the *severity* position, so they were never treated as errors: a 600-share allocation of a 500-share block was accepted | `services/booking.py` passes `field=`; `rules/engine.Issue` now refuses any severity other than ERROR/WARNING |
| F3 | TC-PERM-APPROVE-* (role matrix) | The approval decision endpoint checked the role inside the workflow, after body validation, so a non-checker got 422 instead of 403 | `_may_decide` dependency in `api/routes_ops.py`; the maker still reaches the workflow so a self-approval attempt is refused and audited (R092) |
| F4 | TC-NEG-N15-0088, TC-NEG-N19-* | Negative test data: one stale-price case copied an ETF row onto an EQUITY instrument (caught by DQ04, not R009), and the exposure-limit cases were too small to breach a 900m limit | `datagen/negative.py`; labels unchanged |
| F5 | Importing the first defect run | Generated case ids (lifecycle edges) and environment names were longer than the result table allowed (HTTP 500) | Alembic migration 0003 |

Test-design mistakes corrected on the way (the product was right): the R009 boundary used the wrong last price date,
the R034 "below minimum" case produced a zero quantity when minimum = increment, the R052 case used a future outside
the priced window, and one dashboard slug was misspelt.

# Injected defects (D1 to D6)

Switched on one at a time with `CF360_DEFECTS`; each run starts from a freshly seeded database.

| Defect | What it breaks | Run | Cases failed | Caught by |
|--------|----------------|-----|--------------|-----------|
| D1 | Allocation residual cent dropped | DEFECT-D1 | 25 | TC-BOOK-* allocation amounts vs oracle (5), TC-SPLIT-* (19), TC-WE-* (1) |
| D2 | 30/360 bonds accrue on ACT/360 | DEFECT-D2 | 52 | TC-BOOK-* bond economics (21), TC-ACR-* (29), TC-WE-* (2) |
| D3 | FX spot date ignores the quote-currency calendar | DEFECT-D3 | 46 | TC-FXD-* (26), TC-FXF-* (14), TC-BOOK-* rejected by R011 (5), TC-WE-* (1) |
| D4 | Maker can approve their own request | DEFECT-D4 | 2 | TC-API-SELF-APPROVAL, TC-API-MAKER-NOT-CHECKER |
| D5 | Ledger idempotency key bypassed | DEFECT-D5 | 1 | TC-API-LEDGER-IDEMPOTENT |
| D6 | Blotter ignores the currency filter | DEFECT-D6 | 8 (7 API + 1 browser) | TC-API-FILTER-CCY-*, TC-UI-BLOTTER-FILTER-CCY (screenshot in out/evidence) |

Clean rerun with every defect off: run CLEAN-001, 2,370 of 2,370 catalogue cases executed, 2,410 executions, 0 failures.
