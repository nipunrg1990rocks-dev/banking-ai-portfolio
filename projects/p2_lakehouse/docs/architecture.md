# Architecture: Agentic Banking Lakehouse

## 1. Context

Banks run on data that arrives late, twice, or broken: a core-banking extract with a missing amount, a payment file re-sent after a timeout, a customer whose KYC risk rating changed overnight. Risk, finance and financial-crime teams still need numbers they can trust and trace back to source. This project builds that foundation for **Ledgerline Bank**, a fictional bank. It is a lakehouse that lands source files, validates and conforms them, keeps full history, and publishes business-ready tables with evidence that nothing was lost or double-counted. Three AI agents then work on top of those tables, the way an analyst would: investigating AML alerts, explaining a blocked load and answering business questions. A person approves everything they propose.

All data is synthetic, generated from a fixed seed. No real bank, customer or employer data is used.

## 2. Component view

```mermaid
flowchart TB
    G[Synthetic generator<br/>synthetic.py] --> F[(Landing files<br/>CSV, JSON lines, manifest)]
    F --> B[Bronze: raw text + lineage<br/>bronze.py]
    B --> C1{{Control: count and total<br/>match the manifest}}
    B --> T[Silver transforms: try_cast,<br/>de-duplicate, PII, FX to USD]
    T --> DQ[18 DQ expectations<br/>dq.py]
    DQ -->|drop| Q[(Quarantine<br/>with reasons)]
    DQ -->|valid| M[Delta MERGE<br/>SCD2, upsert, insert-only]
    M --> C2{{Control: every Bronze<br/>row accounted for}}
    M --> S[Gold Spark SQL<br/>sql/gold/*.sql]
    S --> A[(AML alerts, customer 360,<br/>KPIs, DQ scorecard)]
    S --> C3{{Control: Gold<br/>adds back to Silver}}
    A --> AG[AI agents<br/>agents/]
    AG --> APP[Streamlit app<br/>with human approval]
    B & M & S -.-> L[(audit.pipeline_steps<br/>audit.dq_results)]
```

The `TableStore` class (`lakehouse/store.py`) is the only code that knows where a table lives. Locally a table is a Delta folder, referenced as `` delta.`…/silver/transactions` ``. On Databricks it is a Unity Catalog table, `workspace.ledgerline_silver.transactions`. Everything else is identical, so the code that runs in CI is the code that runs on Databricks.

## 3. Run flow (one batch)

```mermaid
sequenceDiagram
    participant J as Orchestrator (run_all or Databricks job)
    participant L as Landing
    participant B as Bronze
    participant S as Silver
    participant G as Gold
    participant A as audit tables
    J->>L: generate files + manifest
    J->>B: load_batch(20260930)
    B->>B: skip if batch already loaded (idempotent)
    B->>A: control: count + control total vs manifest
    alt mismatch
        B-->>J: DataQualityError, step FAILED, run stops
    end
    J->>S: load customers, accounts, transactions
    S->>S: de-duplicate, expectations, quarantine
    S->>S: MERGE (SCD2 / upsert / insert-only)
    S->>A: per-rule metrics + reconciliation control
    J->>G: build Gold from SQL files
    G->>A: cross-layer reconciliation
    J->>S: OPTIMIZE ZORDER BY (customer_id, txn_date)
```

On Databricks, `databricks/01_create_job.py` creates the same flow as a **Lakeflow Jobs** DAG: `land → reference → batch_20260930 → batch_20261007 → gold → optimize`. All tasks share one `run_id`, so the audit tables read the same way as a local run.

## 4. Data model

| Layer | Table | Grain | Key points |
|---|---|---|---|
| Bronze | `customers`, `accounts`, `transactions` | one row per delivered record | Strings only, append-only, lineage columns |
| Bronze | `ref_fx_rates`, `ref_country_risk`, `ref_branches` | one row per reference record | Full refresh |
| Silver | `customers` | one row per customer **version** | SCD Type 2: `effective_from`, `effective_to`, `is_current`, `record_hash`. E-mail stored as SHA-256, phone masked. Invalid KYC rating defaults to HIGH (fail-safe) |
| Silver | `accounts` | one row per account | SCD Type 1 upsert on status, product and limit changes |
| Silver | `transactions` | one row per `txn_id` | Typed amounts, `amount_usd` at the daily FX rate, direction, counterparty risk tier, `dq_warnings` |
| Silver | `*_quarantine` | one row per rejected record | Raw values kept, plus `_dq_failed_rules` |
| Gold | `aml_alert_candidates` | one row per alert | Rule, rationale, transaction ids, priority, always `PENDING_REVIEW` |
| Gold | `customer_360` | one row per current customer | Profile, holdings, 30-day activity, alerts. No names or contact details |
| Gold | `daily_transaction_summary` | date × type × channel × direction | Counts and USD totals |
| Gold | `segment_monthly_kpis` | month × segment | Joins transactions to the customer version valid **on the transaction date** |
| Gold | `dq_scorecard` | rule × table for the latest run | Rows checked, failed, pass rate, status |
| Audit | `pipeline_steps`, `dq_results` | one row per step/table, per rule | Run log and DQ evidence for every run |

## 5. Data-quality rules and controls

| Table | Rule | Action |
|---|---|---|
| customers | `customer_id_present`, `valid_segment` | drop |
| customers | `valid_risk_rating` (defaults to HIGH), `valid_email`, `dob_present`, `known_country` | warn |
| accounts | `account_id_present`, `known_customer`, `valid_currency`, `valid_status` | drop |
| transactions | `txn_id_present`, `amount_present`, `amount_positive`, `valid_currency`, `valid_timestamp`, `not_future_dated`, `known_account`, `fx_rate_available` | drop |

Blocking controls (action `fail`) stop the run and log the step as FAILED:

1. Bronze row count equals the source manifest, per file and batch.
2. The Bronze amount total equals the manifest control total.
3. Silver reconciliation per entity: Bronze = duplicates + quarantined + valid, and for transactions, valid = inserted + already loaded.
4. The Gold daily summary adds back to Silver in count and USD value.
5. Gold segment KPIs cover every Silver transaction, which proves the SCD2 as-of join is complete.

In the committed run, every planted defect was caught and nothing else was quarantined. That gives 25 rows quarantined and 33 duplicates removed. 16 of the 17 blocking controls passed. The 17th tripped on purpose: delivery 20261014 is planted with a truncated `transactions.jsonl` (the manifest says 3,150 rows; 2,835 complete rows and one cut-off line arrived). The Bronze count control blocked it, and the Delta versions of Silver and Gold before and after are identical, which the tests check.

## 6. AI agents

```mermaid
flowchart TB
    U[Goal: an alert id, the incident, or a question] --> E[Agent engine<br/>engine.py]
    E <--> BR{Brain}
    BR --- C[Claude via Anthropic API<br/>tool use, adaptive thinking]
    BR --- P[Offline planner<br/>fixed playbook, no LLM]
    E --> T[14 read-only tools<br/>tools.py]
    T --> BK{Backend}
    BK --- SN[Snapshot in DuckDB<br/>hosted app, tests]
    BK --- SP[Spark SQL views over<br/>Unity Catalog tables]
    E --> S[Submit tool with strict schema:<br/>case, incident report, answer]
    S --> H[Human approves or overrides]
    E -.-> AU[(Hash-chained audit log)]
```

| Agent | Tools | Output |
|---|---|---|
| AML Investigator | `get_alert`, `get_transactions`, `get_customer_profile`, `get_customer_activity`, `compare_to_peers`, `find_counterparty_history`, `counterparty_network`, `run_sql` | `submit_case`: ESCALATE or CLOSE, risk score 0-100, red flags, mitigating factors, evidence transaction ids, narrative, next steps |
| Data Steward | `get_pipeline_status`, `get_dq_findings`, `inspect_delivery`, `get_delivery_history`, `run_sql` | `submit_incident_report`: root-cause category, rows expected, received and missing, downstream impact, remediation, draft ticket |
| Ask Ledgerline | `list_tables`, `run_sql` | `submit_answer`: answer, final SQL, chart spec |

- **Same tools, two brains.** `ClaudeBrain` runs a manual tool-use loop with the Anthropic SDK. Tool errors (for example a SQL typo) go back to the model as `is_error` results so it can correct itself, and prompt caching keeps the growing conversation cheap. `ScriptedBrain` drives the same tools from a Python playbook, so the hosted app works with no key, and the evaluation has a baseline.
- **Guardrails.** Tools only read. SQL must be a single read-only statement, and results are capped at 1,000 rows. Ids are validated by pattern before they reach a query. Each agent has a tool-call limit. Tool results are truncated before going back to the model. The recording script stops at a dollar cap.
- **Human in the loop.** Agents only recommend. On Databricks, case reports land with `review_status = PENDING_HUMAN_REVIEW`; in the app, approve and override are written to the audit log.
- **Evaluation.** `agents/evals.py` scores every output against the generator's answer key (`run_summary.json -> planted`), which the agents' tools cannot read. Checks include the right decision (7 alerts should escalate; the planted payroll false positive for C000212 should close), trigger transactions cited, no invented transaction ids, score consistent with decision, no contact data in narratives, the correct incident row counts, and SQL that returns the same result as a reference query.
- **Recorded runs.** `run_agents.py --brain claude --record` saves every Claude run (thoughts, tool calls, tokens, cost) to `data/agent_runs/`. The hosted app replays them, so visitors see exactly what Claude did without the app calling the API.
- **On Databricks.** `databricks/03_ai_agents.py` runs the agents over Spark SQL views of the Unity Catalog tables and appends to `gold.aml_case_reports` and `audit.agent_steps`.

## 7. Delta Lake features used

| Feature | Where | Why it matters in a bank |
|---|---|---|
| ACID MERGE | `silver.py` | A batch is applied completely or not at all, so readers never see half a load |
| SCD Type 2 via MERGE | `load_customers` | You can answer "what was this customer's rating when the payment happened?" |
| Insert-only MERGE | `load_transactions` | A re-sent payment file cannot double-count money |
| Time travel (`VERSION AS OF`) | tests, app | Reproduce a report exactly as it was, for audit or incident review |
| Change Data Feed | Silver tables | Downstream systems consume only what changed |
| Schema enforcement | test | A bad upstream change fails loudly instead of corrupting a table |
| OPTIMIZE + Z-ORDER | `pipeline.optimize` | Fewer, better-organised files, so investigator queries skip irrelevant data |
| `_metadata.file_path` | `bronze.py` | Row-level lineage back to the delivered file |

## 8. Security and privacy

- Synthetic data only. High-risk jurisdictions use fictional ISO user-assigned codes (XR, XS, XE), so the data makes no claims about real countries.
- PII minimisation by layer. Bronze keeps the raw delivery, as a restricted evidence copy. Silver stores a hashed e-mail and a masked phone. Gold has no names, e-mail, phone or full date of birth.
- On Databricks, `02_unity_catalog_governance.sql` adds column comments, `classification = pii` tags and a column mask that shows names only to members of a KYC analyst group.
- The app's SQL console is DuckDB in memory with `enable_external_access = false` and the configuration locked. It accepts read-only statements and returns at most 1,000 rows. Tests prove that file reads, COPY, DROP, INSTALL and configuration changes are all blocked.
- There are no credentials in the code. Databricks notebooks authenticate as the signed-in user. The Anthropic API key, when used, comes from an environment variable, Streamlit secrets or a Databricks secret scope.
- Agents see Silver and Gold only, through read-only tools. Gold has no names or contact details, and the evaluation fails any narrative that contains an e-mail address or phone number.

## 9. Cost

| Item | Cost |
|---|---|
| Local / CI runs (GitHub Actions) | $0 |
| Streamlit Community Cloud hosting | $0 |
| Databricks Free Edition (serverless, quota-limited) | $0 |
| Claude API (optional) | $0 by default: the hosted app replays recordings or uses the offline planner. A full recorded evaluation (16 runs) is capped at $5 and only runs with the owner's approval |

## 10. Trade-offs and limitations

- **The app shows a snapshot.** Community Cloud has no Java or Spark, so the app reads CSV/JSON exported from a real run. The run id, engine and versions are shown in the header, and a test checks that a fresh run reproduces the snapshot exactly.
- **Batch, not streaming.** The landing zone is read with batch reads. On Databricks the natural next step is Auto Loader (`cloudFiles`) with `availableNow` triggers, or Lakeflow Declarative Pipelines, whose expectations map one-to-one onto `dq.py` rules.
- **Gold is fully recomputed** on each run. That is fine at this scale. At bank scale, Gold would read the Silver Change Data Feed incrementally.
- **AML rules are simple and illustrative.** They are thresholds, not a model, and are not regulatory guidance. They produce candidates for a human investigator and never decisions.
- **The offline planner is not AI.** It is a fixed playbook with rule-based scoring, there so the app works for free and the evaluation has a baseline. The app labels it everywhere it appears. Claude's own scores appear only after a real recorded run.
- **The agents' evaluation is small.** It has 8 alerts, 1 incident and 6 questions. It catches regressions and hallucinated evidence, but it is not a statistically meaningful benchmark.
- **Single-machine Spark locally.** Performance numbers are not representative of a cluster.
- **Databricks: notebook run verified, job and governance not yet.** On 2026-10-26, Nipun ran `00_run_pipeline` on Databricks Free Edition (serverless, Spark 4.2.0, Unity Catalog, run `run_20261026T144959Z`, 356 s). Every table count matched the local run of that data version exactly (see `docs/evidence/databricks_run_20261026.png`). The newer data version and the agents notebook (`03_ai_agents`, free Foundation Model endpoint) were then run on Free Edition too, reported done by Nipun on 2026-10-26 (no screenshot; which free model was used and its scores were not captured). The 6-task Lakeflow job (`01_create_job`) and the column masks and tags (`02_unity_catalog_governance.sql`) have not been run yet.
