# LinkedIn post: Prompt-to-Presentation AI

## Main post (about 1,500 characters)

Every month, someone in banking ops spends days rebuilding the same executive pack. They fix spreadsheet formulas, copy charts into PowerPoint and rewrite the commentary. Then a stakeholder asks for "the same, but for EMEA" and the cycle starts again.

So I built a working alternative as an independent portfolio project: **Prompt-to-Presentation AI**.

You type "Create a six-slide APAC Q3 risk pack, compare with Q2, rank the top exception drivers, add commentary and speaker notes". You get a genuinely editable PowerPoint, with native charts and tables, not images. Then you follow up: "change APAC to EMEA and add high-severity exceptions", "replace slide 4 with ageing", "refresh using latest data". Each follow-up creates a new version and keeps the old one.

What I cared about most as a product owner is the controls:
🔒 The prompt never becomes SQL. It becomes a validated spec, checked against allowlisted templates, filters and columns.
📊 Every number comes from a governed query with a fingerprint, and every commentary sentence is linked to its evidence.
✅ Maker-checker approval: the author can't approve, and open comments block approval.
🧾 A hash-chained audit trail and full provenance in every deck: dataset version, filters, KPI definitions and review status.

It ships with 60 automated tests and an end-to-end browser test, and it reproduces 13 of 13 figures from a published workforce case-study deck.

Built with Python, Streamlit, python-pptx, DuckDB and Pydantic. Claude is optional. I owned the requirements, data and KPI design, controls and validation, and used AI-assisted development for much of the code. All data is synthetic, for a fictional bank.

🔗 Live demo: https://banking-ai-portfolio-bbh7cdwafuzerlfuafrmjr.streamlit.app
💻 Code: https://github.com/nipunrg1990rocks-dev/banking-ai-portfolio

How does your team produce its monthly packs today?

#GenAI #Banking #OperationalRisk #RegTech #ProductManagement #DataGovernance #Python #AI

## Short version (about 600 characters)

Built a prompt-to-PowerPoint reporting platform for banking ops as an independent project. Type "six-slide APAC Q3 risk pack, compare Q2" and you get an editable deck with native charts, evidence-linked commentary and speaker notes. Follow-ups like "change to EMEA" create new versions. Prompts map to a validated spec, never SQL. It has maker-checker approval, a hash-chained audit trail and 60 automated tests. Synthetic data, AI-assisted build.

Demo: https://banking-ai-portfolio-bbh7cdwafuzerlfuafrmjr.streamlit.app

#GenAI #Banking #DataGovernance

## Posting tips

- Attach 2–3 screenshots from `docs/screenshots/`: the dashboard (01), the generated deck (02) and the provenance and audit view (08). Or attach the documentation PDF as a document post.
- Before you post, open the demo link once to wake it. Streamlit Community Cloud apps sleep when idle.
- Don't claim production use, real bank data, or time savings measured in a real team.
