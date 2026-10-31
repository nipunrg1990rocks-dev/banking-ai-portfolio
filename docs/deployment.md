# Deployment

## Hosting choice

**Streamlit Community Cloud** (free): it deploys straight from a GitHub repository, redeploys automatically on every push to `main`, and keeps secrets in a built-in secrets manager. One repository hosts all the portfolio apps; each app is a separate deployment pointing at its own `app.py`.

| App | Main file path |
|---|---|
| 01 Compliance Copilot | `projects/p1_compliance_copilot/app.py` |
| 02 Banking Lakehouse | `projects/p2_lakehouse/app.py` (reads the committed run snapshot; no Spark or Java needed) |
| 03 Exception Manager | `projects/p3_exception_manager/app.py` (offline by default; no secrets needed) |
| 06 Prompt-to-Presentation | `projects/p6_prompt_to_presentation/app.py` (offline by default; `packages.txt` adds LibreOffice for PDF) |

Python version: 3.12 (choose it under Advanced settings).

## One-time steps for the account owner

1. Sign in at https://share.streamlit.io with GitHub and authorise Streamlit.
2. Click **Create app**, then **Deploy a public app from GitHub**.
3. Repository: `<your-github-username>/banking-ai-portfolio`, Branch: `main`, Main file path from the table above.
4. Optional App URL, for example `ledgerline-compliance-copilot`.
5. Click **Deploy**.

## Secrets (only if live LLM mode is approved)

In the app's **Settings → Secrets** on Streamlit Cloud (never in chat or in the repository):

```toml
ANTHROPIC_API_KEY = "..."
PORTFOLIO_LLM_MODEL = "claude-haiku-5-5"
```

Set a monthly spend limit on the Anthropic console before adding the key.

## Demo-state behaviour

Each browser session gets its own index, review queue and audit log in memory, so interviewers never see each other's activity, and a restart resets the demo.
