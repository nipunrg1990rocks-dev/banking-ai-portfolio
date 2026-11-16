**Last local run: 31 Oct 2026** (Python 3.13, Linux; CI re-runs on every push with Python 3.12).

| Test file | What it proves | Result |
|---|---|---|
| test_p6_metrics.py | KPI accuracy vs an independent pandas calculation, volume de-duplication (non-additive), severity filter narrows numerator only, divide-by-zero, comparison windows, missing-period disclosure, workforce on-contract and Q1 T&M cost formula | 11/11 passed |
| test_p6_validation.py | Seeded anomalies quarantined (duplicates, LATAM, on-time > volume, negative loss, risk 140), schema errors block activation, formula text neutralised, upload size/type limits, refresh only after activation, roster checks, xlsx round trip | 7/7 passed |
| test_p6_parser.py | Acceptance prompt A, prompt variants, all seven follow-up types, 7 injection/SQL/code prompts rejected, unknown values never reach the spec, LLM output validated and fallback to rules | 25/25 passed |
| test_p6_query.py | Only allowlisted tables/columns; filter values bound as parameters; stable query fingerprints | 3/3 passed |
| test_p6_pptx.py | Decks open in python-pptx, native charts and tables (no images), notes carry provenance, every slide type renders, files never overwritten, PDF export | 5/5 passed |
| test_p6_workflow.py | Acceptance demo A–G end to end, slide replacement keeps other slides identical, fresh copy, unverified-number flag, maker-checker / open-comment / approval rules, audit tamper detection, rejected prompts logged | 7/7 passed |
| test_p6_app.py | Streamlit AppTest: dashboard renders, generate + follow-up, workforce dashboard | 2/2 passed |
| **Total (Project 06)** | | **60/60 passed** |

Whole repository suite at the same time: 194 passed, 26 skipped (the skips are the Project 02 Spark tests, which need Java and PySpark locally; CI runs them).

Lint: `ruff check .` – all checks passed.

Reconciliation against the original case-study roster (run locally with `reconcile_case_study.py`, file not committed): 13/13 figures match the published deck (134 on contract, 219 people, FTE 195 → 85, 105 contracts ending 31 Dec, 67 T&M, 87 external, 54 from the top two vendors, 20 zero-allocation, Q1 T&M India EUR 610,393.55 for 72 contractors, run-rate EUR 691k → 374k).

## Project 06 acceptance demo (A-G)

| Step | What | Version | Slides | Dataset | Period | Filters | Note |
|---|---|---|---|---|---|---|---|
| A | Generate APAC Q3 six-slide deck | D-0001 v1 | 6 | ops-v1-173a1e | Q3 2026 | region=['APAC'] |  |
| B | Follow-up: change APAC to EMEA, add high-severity | D-0001 v2 | 7 | ops-v1-173a1e | Q3 2026 | region=['EMEA'] |  |
| C | Second file saved, first kept | D-0001 v2 | 7 | ops-v1-173a1e | Q3 2026 | region=['EMEA'] | v1 file still present: True |
| D | Upload modified dataset, validate, activate, refresh | D-0001 v3 | 7 | ops-v2-e3e947 | Q3 2026 | region=['EMEA'] | 9,189 rows accepted, 9 quarantined |
| D | Follow-up: add October (now in data) | D-0001 v4 | 7 | ops-v2-e3e947 | Jul–Oct 2026 | region=['EMEA'] |  |
| E | Edit management commentary on slide 7 | D-0001 v5 | 7 | ops-v2-e3e947 | Jul–Oct 2026 | region=['EMEA'] | human-edited, evidence kept for unchanged lines |
| F | Approved, downloaded editable .pptx | D-0001 v5 | 7 | ops-v2-e3e947 | Jul–Oct 2026 | region=['EMEA'] | D-0001_v05_EMEA_Risk___Controls_pack___Jul_Oct_2026_APPROVED.pptx (84,336 bytes) |
| WF | Workforce review (case-study replica) | D-0002 v1 | 11 | wf-v1-696999 | FY 2018 | all |  |
| WF | Follow-up: Pune T&M only | D-0002 v2 | 11 | wf-v1-696999 | FY 2018 | location=['Pune'], category=['T&M'] |  |

(G) Audit events: 18, hash chain intact: True. PDF export: yes. Total time 11.6s.
