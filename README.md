# Banking AI & Databricks Portfolio

Five independent, AI-assisted portfolio projects that apply GenAI and data engineering to banking risk, compliance and operations. **All data is synthetic.** These projects are not affiliated with any employer or real financial institution.

| # | Project | Stack | Status |
|---|---|---|---|
| 01 | [Enterprise Banking Risk & Compliance AI Copilot](projects/p1_compliance_copilot/) | Python, RAG (BM25 + vectors), Claude (optional), Streamlit, SQLite audit chain, MLflow | Built, tested, [live demo](https://abv2xygrggmaucidx5hjbg.streamlit.app) |
| 02 | Banking Data Engineering Lakehouse | Databricks, PySpark, Delta Lake, medallion architecture | Planned |
| 03 | Agentic AI Operations Exception Manager | LangGraph, human-in-the-loop, audit trail | Planned |
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

CI (GitHub Actions) runs lint, tests and the Project 01 evaluation on every push. See [PORTFOLIO_TRACKER.md](PORTFOLIO_TRACKER.md) for live status and [docs/deployment.md](docs/deployment.md) for hosting.
