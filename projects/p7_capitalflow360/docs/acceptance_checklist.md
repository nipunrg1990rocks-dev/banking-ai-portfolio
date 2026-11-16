# CapitalFlow 360 — Acceptance checklist

Each item has a measurable target and the command that produces its evidence.
`python scripts/verify_acceptance.py` recomputes every "Actual" from files on disk and writes
`evidence/acceptance_status.json`; nothing in this table is typed in by hand.

| # | Criterion | Target | Evidence source |
|---|---|---|---|
| A1 | Clean setup with documented commands and seeded demo users | `make setup && make seed` succeeds; 9 demo users | `evidence/setup_log.txt` |
| A2 | Baseline trades | exactly 500, split 140/120/80/70/50/40 | `data/manifest.json` |
| A3 | Related workbooks | 12 related files, relationally valid, dictionary sheet + provenance each | `data/manifest.json`, `datagen/validate.py` |
| A4 | Negative variants separate | in `data/negative/`, baseline count unaffected | `data/manifest.json` |
| A5 | Business rules implemented and documented | ≥ 40 rules with ID, rationale, formula, boundaries, error code, coverage | `docs/rule_catalogue.md` |
| A6 | Generated test cases | ≥ 1,500, each with lineage, rules, preconditions, payload, expected outcome | `testkit/out/cases.json` |
| A7 | Distinct scenario definitions | ≥ 100 | `testkit/out/scenarios.json` |
| A8 | Executed parameterised cases | ≥ 1,500 executed through API/UI layers | Playwright JSON + JUnit |
| A9 | Actual browser runs | ≥ 200 | Playwright JSON (browser project results) |
| A10 | Cross-browser core lifecycle | Chromium, Firefox, WebKit where installable; actual coverage stated | Playwright JSON |
| A11 | Independent expected results | oracle separate from backend (import test), ≥ 20 hand-calculated worked examples | `testkit/worked_examples.py`, pytest |
| A12 | Invariants | allocation conservation, balanced cash, idempotent replay, zero residual after reversal | pytest + API suite |
| A13 | Defect injection | ≥ 5 injected defects detected, evidence saved, rerun clean | `evidence/defect_demo/` |
| A14 | Dashboards | 7 views, filters, drill-down to trade/exception/test evidence, definitions | Playwright dashboard specs |
| A15 | Lakehouse | Bronze/Silver/Gold with MERGE, checkpoints, replay, quarantine, quality gates (local run) | `evidence/lakehouse_run.json` |
| A16 | Databricks assets | notebooks + job config deployable; cloud run recorded only if actually run | `lakehouse/databricks/` |
| A17 | Presentations | 4 PPTX from one metrics snapshot, rendered and inspected | `reports/out/` |
| A18 | Interview handbook | 30–40 page searchable PDF, ≥ 60 Q&A, rendered and inspected | `reports/out/` |
| A19 | Traceability | requirement → rule → source → case → test → evidence → defect → metric | `docs/traceability_matrix.xlsx` |
| A20 | Delivery | Docker Compose, migrations, `.env.example`, CI, README, licences, limitations | repo files |
