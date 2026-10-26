# Portfolio Tracker

Last updated: 2026-10-26. Evidence links point to files in this repository.

| Project | Scope agreed | Code complete | Tests run | Bugs outstanding | Deployment ready | URL verified | Demo pack | Interview training | Cost to date | Next action | Owner |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 Compliance Copilot | Yes (master brief) | Yes, MVP | 48/48 pytest (local and GitHub Actions CI green); 10/10 browser steps; eval Recall@3 1.00 | None known; limitations in docs/architecture.md | Yes (requirements pinned, Streamlit config, CI) | Deployed by Nipun at https://abv2xygrggmaucidx5hjbg.streamlit.app on 2026-10-26; awaiting smoke check (Claude's network cannot reach streamlit.app) | Yes: [interview_pack.md](projects/p1_compliance_copilot/docs/interview_pack.md) | Not started | $0 | Deploy on Streamlit Cloud, then verify the live URL with the browser test | Claude (build), Nipun (accounts, review) |
| 02 Lakehouse | Yes (brief) | No | - | - | - | - | - | - | $0 | Start after P1 deploy; needs Databricks Free Edition account | - |
| 03 Agentic Ops | Yes (brief) | No | - | - | - | - | - | - | $0 | - | - |
| 04 Risk & DQ Dashboard | Yes (brief) | No | - | - | - | - | - | - | $0 | - | - |
| 05 Reg Change Analyzer | Yes (brief) | No | - | - | - | - | - | - | $0 | - | - |

## Accounts and connections

| Service | Status | Secrets (names only) |
|---|---|---|
| GitHub | Connected. Repo nipunrg1990rocks-dev/banking-ai-portfolio (public), code pushed 2026-10-26 | none |
| Streamlit Community Cloud | App deployed: https://abv2xygrggmaucidx5hjbg.streamlit.app | `ANTHROPIC_API_KEY` (only if live mode approved) |
| Databricks Free Edition | Not set up (needed for projects 02 and 04) | none |
| Anthropic API | Not set up; spend not approved | none |
