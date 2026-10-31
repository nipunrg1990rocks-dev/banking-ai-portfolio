# Interview pack: Prompt-to-Presentation AI

Everything here describes what was actually built and tested. Numbers come from `docs/test_results.md` and `run_demo.py`. If you change the code, re-run them before quoting.

**Honest framing.** This is an independent, AI-assisted portfolio project. I owned the problem definition, banking requirements, data contracts, KPI definitions, control design, acceptance criteria and validation of outputs. Much of the code was written with an AI coding assistant under my direction and review. It runs on synthetic data for a fictional bank. It is not a production system and has not been used with any employer's data.

---

## 1. Three-minute demo script

| Time | Do | Say |
|---|---|---|
| 0:00 | Open the **Dashboard** tab (ops risk). | "Executive reporting at most banks is a monthly rebuild: extract, fix formulas, copy charts into PowerPoint, rewrite commentary. This replaces that with one governed dashboard and a prompt box that produces an editable deck." |
| 0:25 | Change region to APAC, months to Q3. Point at the exception-rate trend. | "Every tile is a governed KPI. Volume in this data is non-additive, so it's de-duplicated before any ratio. The APAC Payments spike in July and August is a seeded anomaly I use to test the story." |
| 0:50 | **Prompt to deck** → example 1 → **Generate deck**. | "The prompt never becomes SQL. It becomes a validated spec: template, filters, months, comparison, slides. Anything outside the allowlist is rejected." |
| 1:15 | Show the preview and the spec. Click the chip **Change APAC to EMEA and add high-severity exceptions**. | "Follow-ups modify the spec, not the slides. Version 2 is a new file. Version 1 is untouched." |
| 1:45 | **Review & versions** → download v2 and open it in PowerPoint. Click into a chart and choose Edit Data. | "These are native charts and tables, so the team can still edit them. The speaker notes carry the talk track, the evidence for every sentence, and provenance: dataset version, hash, filters and query fingerprints." |
| 2:15 | Try to approve as the author. | "Maker-checker: the author can't approve, open comments block approval, and edited commentary with numbers that aren't in the data must be confirmed as verified." |
| 2:40 | **Provenance & tests** → Verify chain. | "Every prompt, version, edit and decision is in a hash-chained audit log. It has 60 automated tests and an 11-step browser test, and it reproduces 13 of 13 published figures from a real case-study deck." |

## 2. Ten-minute demo script

**1. Problem and goal (1 min).** Monthly packs take days and break easily. They depend on formula maintenance and static decks, and nobody can say where a number came from. The goal is a single source of truth, prompt-driven decks, and governance built in, not added afterwards.

**2. Data and validation (1.5 min).** Go to **Data & refresh**. Show the two datasets. Ops risk covers 21 months, 3 regions, 4 business lines and 5 processes. Workforce has the same shape as a monthly resource tracker. Show the validation report: required columns, category allowlists, ranges and duplicates. "Apac" is normalised to APAC, LATAM is quarantined, completed-on-time greater than volume is quarantined, and formula text is neutralised. A new file goes live only after it is activated.

**3. Dashboard (1 min).** Show the filters for region, business line, process, severity and months. Cover trend, drivers, ageing bands, remediation, on-time and data quality. Then **Create deck from this view**.

**4. Prompt to deck, acceptance A (1.5 min).** Generate "six-slide APAC Q3 risk pack, compare with Q2, rank top exception drivers, add commentary and speaker notes". Show the parsed spec and the parser mode (rules). Then open the .pptx and walk through it. The cover has a DRAFT stamp. The KPI table carries RAG and Better/Worse labels. Then the Q3 vs Q2 comparison, drivers, the commentary slide labelled *draft*, and the appendix with KPI definitions.

**5. Follow-ups, acceptance B and C (1.5 min).**
- "Change APAC to EMEA and add high-severity exceptions" produces v2 with seven slides. The ₹4.85 cr Critical loss in EMEA Markets now appears.
- "Replace slide 4 with ageing" regenerates only that slide.
- "Make a fresh copy" creates a new deck id.
- Show that v1's file and hash are still there.

**6. Refresh, acceptance D (1 min).** Upload the sample modified dataset. The report shows Oct 2026 added, September restated and the bad rows quarantined. Activate it, then "Refresh using latest data" gives v3 on `ops-v2-…`. Explain that earlier versions stay pinned to the dataset they were built on, so numbers never change silently.

**7. Review, acceptance E and F (1 min).** Edit the commentary and save it as a new version. Add a review comment, then log in as another reviewer: approval is blocked until the comment is resolved. Then approve. The approved file carries an APPROVED stamp. Download it with comments in the notes, plus the review-log CSV and the PDF.

**8. Workforce domain (0.5 min).** Switch dataset and generate the Workforce Review. It replicates the case-study deck: headcount bridge, location trend, cost bridge, contract cliff and vendor concentration.

**9. Provenance and tests, acceptance G (0.5 min).** Show the audit table, verify the chain and show the provenance JSON. Point to the test results: 60/60 tests and the CI step that runs the A–G demo.

**10. Limits and production path (0.5 min).** Data is synthetic. Live Claude parsing is built but has not been run against the API. For production, you would use Databricks SQL with Unity Catalog row filters, SSO, object storage, and PostgreSQL for versions.

---

## 3. Thirty interview questions and answers

### Product and business

1. **What problem does it solve?** Executive packs are rebuilt by hand every month. It's slow, error-prone and hard to audit. This turns the pack into a governed product: one dataset, one KPI dictionary, prompts to produce decks, and a full trail.

2. **Who are the users?** The Head of Operations and the CRO's office consume the decks. Analysts and PMO create them. Control owners and reviewers sign off. The maker-checker split reflects those roles.

3. **How would you measure success?** Time from data cut-off to approved pack (target: hours, not days). The number of manual corrections found in review. The share of numbers traceable to a query, which should be 100%. Reuse of templates across regions.

4. **Why PowerPoint and not just a dashboard?** Committees still consume decks, annotate them and file them as records. So the output is genuinely editable, with native charts and tables, rather than images.

5. **What did you deliberately leave out?** Free-text Q&A over data, auto-sending to distribution lists, and native PowerPoint comments, which python-pptx can't write. The comments go to the notes and a CSV review log instead, and the app says so.

6. **How did you prioritise?** Correctness and governance first: validation, allowlists, versioning and review. Then the acceptance demo. Then polish. A pretty deck with an untraceable number fails in a bank.

### Data and KPIs

7. **What is non-additive volume and why does it matter?** In the ops data, transaction volume is recorded once per process-month but repeated on every exception cluster row. Summing it naively would inflate the denominator many times over and understate the exception rate. I de-duplicate at the process-month grain before summing.

8. **How does a severity filter affect the exception rate?** It narrows only the numerator. "High-severity exception rate" is high-severity exceptions over all volume, because volume doesn't have a severity.

9. **How do you handle bad data?** Every row is checked against required columns, category allowlists, ranges, date validity, logical rules (on-time ≤ volume) and duplicates. Fixable issues like "Apac" are normalised and disclosed. Others are quarantined with a reason. Warnings and quarantine counts appear on the data-quality slide and in the notes.

10. **What happens when the comparison period has no data?** The comparison is shown as n/a and the gap is disclosed on the slide and in the notes, not silently dropped. Division by zero returns n/a, not zero.

11. **How did you validate the workforce numbers?** I reconciled them against a published case-study deck. A local script compares 13 headline figures (for example, 134 people on contract and €610,393.55 Q1 T&M spend in India) with the engine's output from the original file. All 13 matched. The original file is not in the public repo.

12. **How are KPIs defined?** In a KPI dictionary that ships with the data contract. It shows the metric id, definition, aggregation rule and caveats. The dictionary is shown in the app and printed in each deck's appendix.

### AI and LLM design

13. **Where does the LLM sit?** Only in parsing the prompt into a structured spec. It never writes SQL, never sees the data and never writes the numbers.

14. **Why a rules parser by default?** It's free, deterministic and testable, and works without any credentials. The Claude parser improves coverage of unusual phrasing when an approved key exists. Its JSON goes through the same Pydantic model and allowlist checks, and it falls back to rules on any failure.

15. **How is commentary grounded?** Every sentence is generated from computed metrics and stored with its evidence: metric, value, window and filters. Causal statements are labelled "Hypothesis" and cite the root causes recorded by control owners.

16. **How do you stop prompt injection?** There are three layers. A guard rejects injection phrases, SQL and code patterns. The output space is a closed schema: templates, slide types and filter values must be in allowlists and exist in the dataset. There is no path from text to an executed query.

17. **What if a user edits commentary and types a wrong number?** Numbers in the edited text are checked against the slide's data. Unmatched numbers are flagged, and approval requires the reviewer to note that they were verified.

18. **How would you evaluate the LLM parser?** With a labelled set of prompts and their expected specs, scored on exact match per field, the rejection rate on adversarial prompts and the fallback rate. I'd also track cost and latency per prompt. The rules parser tests already provide the harness.

19. **Did you use the live Claude API?** No. The client is built and tested with a fake. Running it live needs an API key and spend approval, which I haven't given.

### Engineering

20. **Why a payload JSON between analytics and rendering?** It freezes every number with the version. So approval re-renders identical content with a new stamp, provenance doesn't need a re-query, and the renderer is testable on its own.

21. **How are follow-ups implemented?** As spec transformations: change filters, add or remove months, change comparison, replace or add slides. Unchanged slides are copied from the parent payload, and only the affected slides are regenerated.

22. **How is versioning designed?** Append-only in SQLite. Each version stores its prompt, parser mode, spec, dataset version and SHA-256, query fingerprints, file path and hash, and status. The renderer refuses to overwrite an existing file.

23. **Why pin follow-ups to the parent's dataset?** So the same request never silently produces different numbers after a data refresh. Refresh is an explicit action that creates a new version on the new dataset.

24. **How do you make charts editable?** They use python-pptx native chart objects with embedded data. Waterfalls are stacked columns with an invisible base series. The Sankey and Marimekko from the original deck were replaced with stacked bars so they stay editable.

25. **What does the audit log protect against?** Silent changes. Each event includes the hash of the previous one, so editing or deleting a past event breaks the chain. A test proves that tampering is detected.

26. **How is it tested?** 60 pytest tests cover metrics, validation, the parser and follow-ups, injection, the query allowlist, PPTX integrity, versioning, review rules, audit tampering and a Streamlit AppTest. There's also an 11-step Playwright browser run of demo A–G, and CI runs the scripted acceptance demo on every push.

### Risk, controls and production

27. **What controls would a bank's model risk or ops risk team ask about?** Data lineage (dataset hash plus query fingerprints), access control (allowlists, which need SSO and entitlements in production), change control (versioning), maker-checker approval, audit trail and AI use disclosure. Each one is either built here or listed as a production gap.

28. **What would change for production?** Databricks SQL over Delta tables with Unity Catalog row filters for regional entitlements, keeping the same QueryBuilder contract. SSO identities and reviewer roles from the entitlement system. Object storage with retention for decks. PostgreSQL for versions and audit. LibreOffice in a sidecar for PDF. A model-risk review of the LLM parser.

29. **What about data privacy?** Only synthetic data is shipped. On the workforce roster, names and e-mails are dropped at load, and people are identified by a hash. Uploads stay in memory for the session. Prompts pass through PII redaction before any model call.

30. **What would you do next?** Evaluate the live LLM parser on a labelled prompt set. Add role-based entitlements. Add scheduled refresh with data-freshness SLAs. Build a template designer so business users can add slide types without code.

---

## 4. Resume and LinkedIn bullets (ATS-ready)

**Project title line**
Prompt-to-Presentation AI: Dynamic Executive Reporting Platform (independent AI-assisted portfolio project, 2026) | Python, Streamlit, python-pptx, DuckDB, Pydantic, Claude API (optional), pytest, GitHub Actions

**Resume bullets** (choose 3–5)
- Designed and delivered, as product owner using AI-assisted development, a prompt-to-PowerPoint reporting platform that turns natural-language requests into editable executive decks with native charts, KPI tables, commentary and speaker notes, on synthetic banking operational-risk and workforce data.
- Defined data contracts, a KPI dictionary and validation rules for 21 months of synthetic operational-risk data, including de-duplication of non-additive volume, category allowlists, range checks and quarantine reporting.
- Specified a governed AI architecture in which prompts map to a validated Pydantic spec rather than SQL, with allowlisted parameterised queries, prompt-injection guards and an offline rules fallback for the optional LLM parser.
- Built in banking controls: append-only deck versioning, dataset pinning, maker-checker approval, unverified-number checks on edited commentary and a hash-chained audit log, with provenance recorded for each deck.
- Reconciled the analytics engine against a published workforce case-study deck, reproducing 13 of 13 headline figures. Defined acceptance tests covered by 60 automated tests, an 11-step browser test and a CI-run acceptance demo.

**LinkedIn "Projects" entry**
> **Prompt-to-Presentation AI (independent, AI-assisted)**
> A governed reporting platform: one dashboard plus a prompt box that produces editable PowerPoint packs, with follow-ups such as "change APAC to EMEA" and "refresh using latest data". I owned the requirements, data and KPI design, controls and validation, and used an AI coding assistant for much of the implementation. Synthetic data, fictional bank. Features: native charts, evidence-linked commentary, version history, maker-checker review, a hash-chained audit trail, and 60 automated tests. Repo: github.com/nipunrg1990rocks-dev/banking-ai-portfolio

**Do not claim:** production use, real bank data, live LLM accuracy figures, user adoption, time savings measured in a real team, or that you wrote all the code by hand.
