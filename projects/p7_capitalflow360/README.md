# CapitalFlow 360

A simulated multi-asset trade lifecycle platform, from order to reconciled books, tested against an independent oracle.

> **Independent portfolio project on synthetic data.** Nothing here connects to a real exchange, broker, clearing house,
> custodian or payment system. Calculations follow standard market conventions but this is not production-grade pricing,
> and it makes no claim of regulatory compliance.

**Who did what.** Nipun Gupta designed the product scope, lifecycle, business rules and their boundaries, roles and controls,
test scenarios, negative data and the defect injection plan, and reviewed the results. The implementation was written with an
AI coding assistant; every result was run and checked against the oracle, and failures are reported rather than hidden.

## What is in it

| Part | What it does | Where |
|---|---|---|
| Platform | 500 synthetic trades in six asset classes (equities/ETFs 140, bonds 120, FX 80, futures/options 70, swaps 50, repo/securities lending 40) carried through booking, allocation, matching, clearing, settlement, a double-entry ledger, reconciliation and exceptions. 7 role workspaces, 7 dashboards, maker-checker, idempotency, an append-only audit trail with correlation ids. | `backend/`, `frontend/` |
| Data | 13 related Excel workbooks from a fixed seed, plus separate oracle and labelled negative files | `datagen/`, `data/` |
| Rules | 69 rules with boundaries and error codes; 28 hand-calculated worked examples | [docs/rule_catalogue.md](docs/rule_catalogue.md) |
| Lakehouse | Excel to Parquet landing, Bronze, Silver (quality gate, quarantine, MERGE), Gold, on local Spark + Delta Lake; Databricks notebook and job | `lakehouse/` |
| Tests | 2,370 generated cases in 137 scenarios, an executor, Playwright browser tests, six injectable defects | `testkit/`, `frontend/e2e/`, `tests/` |
| Reports | Four PowerPoint decks and a 35-page interview handbook, all built from recorded runs | `reporting/` |

Current status against the brief, generated from files on disk: [docs/acceptance_status.md](docs/acceptance_status.md).
Real defects the tests found and the injected-defect results: [testkit/FINDINGS.md](testkit/FINDINGS.md).

## Run it with Docker

Needs Docker with Compose.

```bash
cd projects/p7_capitalflow360
cp .env.example .env          # then set CF360_JWT_SECRET to a random 32+ character string
docker compose up --build     # first start loads the synthetic baseline (about a minute)
```

Open http://localhost:8000 and sign in as `trader1`, `mo1`, `settle1`, `recon1`, `risk1`, `checker1` or `admin1`, with the
password from `CF360_DEMO_PASSWORD` in your `.env`. Change that password for anything other people can reach.
API documentation is at http://localhost:8000/docs.

## Develop and test without Docker

Needs Python 3.13, Node 22 and PostgreSQL 16 with a `cf360` role (password `cf360`) owning databases `cf360` and `cf360_test`.

```bash
cd projects/p7_capitalflow360
pip install -r requirements-dev.txt
(cd backend && python -m alembic upgrade head && python -m cf360.seed)     # main database
(cd frontend && npm ci && npm run build)
CF360_JWT_SECRET=<random> CF360_STATIC_DIR=frontend/dist uvicorn cf360.main:app --app-dir backend --port 8000

python -m pytest                                                   # unit, property and API tests
PYTHONPATH=backend:. python -m testkit.cases                       # regenerate the case catalogue
PYTHONPATH=backend:. python -m testkit.execute --run-id LOCAL-1    # execute it against a fresh cf360_test
(cd frontend && npx playwright test)                               # browser tests (PW_ALL_BROWSERS=1 adds Firefox and WebKit)
PYTHONPATH=backend:. python -m testkit.execute --run-id D2 --defects D2   # prove the suite catches a defect
python lakehouse/run_local.py                                      # lakehouse on local Spark (needs Java 17+, about 10 minutes)
(cd reporting && npm ci) && python reporting/build_reports.py      # snapshot, decks and handbook
python scripts/build_docs.py && python scripts/verify_acceptance.py
```

CI runs the tests, the catalogue, the executor and the browser suite on Chromium, Firefox and WebKit:
`.github/workflows/p7-capitalflow360.yml`.

## Limits

- Firefox and WebKit could not be installed in the build environment; their results (245 browser runs on three engines, 0 failed, run CI-3) come from
  [GitHub Actions](https://github.com/nipunrg1990rocks-dev/banking-ai-portfolio/actions/runs/43817863937) and are recorded in `testkit/out/ci/`.
- The Databricks notebook (`lakehouse/databricks/`) has not yet been executed on Databricks. Lakehouse evidence is from local Spark.
- Five rules have no dedicated generated case yet (listed in the acceptance status and the rule catalogue).
- Performance figures are single-machine timings, not load tests.
- Public demo on free tiers (Vercel + Neon): https://banking-ai-portfolio.vercel.app . It can be slow to wake and is not a performance reference.
  Setup: [docs/deploy_vercel_neon.md](docs/deploy_vercel_neon.md).

`reporting/vendor/apply_theme.js` is vendored under the Apache License 2.0 (header in the file). All other code is part of this repository.
