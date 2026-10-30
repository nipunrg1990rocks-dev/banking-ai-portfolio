# Portfolio Tracker

Last updated: 2026-10-26. Evidence links point to files in this repository.

| Project | Scope agreed | Code complete | Tests run | Bugs outstanding | Deployment ready | URL verified | Demo pack | Interview training | Cost to date | Next action | Owner |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 Compliance Copilot | Yes (master brief) | Yes, MVP | 48/48 pytest (local and GitHub Actions CI green); 10/10 browser steps; eval Recall@3 1.00 | None known; limitations in docs/architecture.md | Yes (requirements pinned, Streamlit config, CI) | Yes: https://abv2xygrggmaucidx5hjbg.streamlit.app, smoke-checked by Nipun on 2026-10-26 (ask, review routing, audit chain) | Yes: [interview_pack.md](projects/p1_compliance_copilot/docs/interview_pack.md) | Not started | $0 | Interview rehearsal of the 3-minute demo; optional: approve live LLM mode | Claude (build), Nipun (accounts, review) |
| 02 Agentic Lakehouse | Yes (brief) | Yes: [projects/p2_lakehouse](projects/p2_lakehouse/) (merged); AI agents in PR #4, Databricks free-model brain in PR #5 | 58/58 pytest incl. real Spark 4.1 + Delta 4.4 runs and agents on Spark; 12/12 browser steps; 16 of 17 controls (1 tripped by the planted incident, as designed); offline agent eval 8/8, 1/1, 6/6. LLM scores not captured | None known; limitations in docs/architecture.md | Local/CI yes. Databricks run-all notebook verified 2026-10-26 (checked by Nipun, screenshot); new data version + agents notebook on a free model reported done by Nipun (no screenshot); job DAG not yet run | Yes: https://kqgpivnvrwznbhmq4gfutm.streamlit.app (deployed by Nipun 2026-10-27) | Yes: [interview_pack.md](projects/p2_lakehouse/docs/interview_pack.md) + [PDF](projects/p2_lakehouse/docs/Ledgerline_Banking_Lakehouse_Interview_Pack.pdf) | Not started | $0 | None | Claude (build), Nipun (accounts, review) |
| 03 Agentic Ops | Yes (brief) | Yes: [projects/p3_exception_manager](projects/p3_exception_manager/) | 50/50 pytest (incl. Streamlit AppTest); 10/10 browser steps; eval: route accuracy 100%, 0 regulated auto-executions, 0 violations in 300 fuzzed cases | None known; limitations in docs/architecture.md | Yes (offline, $0; Claude case notes optional, not run live) | No (app not yet deployed) | Yes: [interview_pack.md](projects/p3_exception_manager/docs/interview_pack.md) + [PDF](projects/p3_exception_manager/docs/Ledgerline_Exception_Manager_Interview_Pack.pdf) | Not started | $0 | Nipun: deploy app on Streamlit Cloud and smoke-check it | Claude (build), Nipun (accounts, review) |
| 04 Risk & DQ Dashboard | Yes (brief) | No | - | - | - | - | - | - | $0 | - | - |
| 05 Reg Change Analyzer | Yes (brief) | No | - | - | - | - | - | - | $0 | - | - |

## Accounts and connections

| Service | Status | Secrets (names only) |
|---|---|---|
| GitHub | Connected. Repo nipunrg1990rocks-dev/banking-ai-portfolio (public), code pushed 2026-10-26 | none |
| Streamlit Community Cloud | App deployed: https://abv2xygrggmaucidx5hjbg.streamlit.app | `ANTHROPIC_API_KEY` (only if live mode approved) |
| Databricks Free Edition | Requested sign-up 2026-10-26 (needed for projects 02 and 04) | none (notebooks authenticate as the signed-in user) |
| Anthropic API | Not set up; spend not approved | none |

## Portfolio website

| Item | Status |
|---|---|
| Site source | [`site/`](site/): `portfolio.json` (links, statuses), `template.html`, `build.py` |
| Hosting | GitHub Pages via `.github/workflows/pages.yml`, rebuilt on every push to main |
| URL | https://nipunrg1990rocks-dev.github.io/banking-ai-portfolio/ (live once Pages is switched on: Settings > Pages > Source: GitHub Actions) |
| Tests | `tests/test_site.py`: build succeeds, every local link resolves, hash chain verifies, missing files show "coming soon" |
| To add a demo link | Paste the URL into that project's `live_url` in `site/portfolio.json`, set `status` to `live` and `status_label` to `Live demo` |
