# Banking AI & Databricks Portfolio

Five independent, AI-assisted portfolio projects that apply GenAI and data engineering to banking risk, compliance and operations. **All data is synthetic.** These projects are not affiliated with any employer or real financial institution.

**Portfolio website:** https://nipunrg1990rocks-dev.github.io/banking-ai-portfolio/ (built from [`site/`](site/); every link and status lives in [`site/portfolio.json`](site/portfolio.json)).

| # | Project | Stack | Status |
|---|---|---|---|
| 01 | [Enterprise Banking Risk & Compliance AI Copilot](projects/p1_compliance_copilot/) | Python, RAG (BM25 + vectors), Claude (optional), Streamlit, SQLite audit chain, MLflow | Built, tested, [live demo](https://abv2xygrggmaucidx5hjbg.streamlit.app) |
| 02 | [Banking Data Engineering Lakehouse](projects/p2_lakehouse/) | PySpark, Spark SQL, Delta Lake (MERGE, SCD2, CDF, time travel), Databricks Unity Catalog and Jobs, Streamlit, DuckDB | Built and tested locally, in CI and on Databricks Free Edition; app deploy pending |
| 03 | [Agentic AI Operations Exception Manager](projects/p3_exception_manager/) | LangGraph (interrupt/resume), tool-calling agents, scikit-learn triage, maker-checker controls, Streamlit, hash-chained audit | Built and tested; not yet deployed |
| 04 | AI-Powered Risk & Data Quality Dashboard | Databricks SQL, Streamlit | Planned |
| 05 | GenAI Regulatory Change Impact Analyzer | Document intelligence, RAG, control mapping | Planned |

## Shared building blocks (`portfolio_core/`)

Reused across projects so the portfolio is one coherent system rather than five unrelated ones:

- `audit.py`: tamper-evident, hash-chained audit log
- `guardrails.py`: PII redaction, prompt-injection detection and input validation
- `llm.py`: Claude client with a free offline fallback and secret handling
- `text.py`: tokenisation and stemming
- `ui.py`: common Streamlit styling

## Development

```bash
pip install -r requirements-dev.txt
ruff check . && pytest
```

CI (GitHub Actions) runs lint, tests, the Project 01 and 03 evaluations and the Project 02 Spark pipeline on every push. Project 02's pipeline needs Java 17+ locally. See [PORTFOLIO_TRACKER.md](PORTFOLIO_TRACKER.md) for live status and [docs/deployment.md](docs/deployment.md) for hosting.
