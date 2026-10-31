"""Build the downloadable documentation PDF for Project 06.

Usage: python projects/p6_prompt_to_presentation/build_docs_pdf.py [out.pdf]

Combines README, architecture, data dictionary, test results and interview pack
into one styled PDF (Chromium via Playwright). Mermaid blocks are replaced with
static HTML diagrams so the build works offline.
"""

from __future__ import annotations

import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
LIVE_URL = "https://banking-ai-portfolio-bbh7cdwafuzerlfuafrmjr.streamlit.app"
REPO_URL = "https://github.com/nipunrg1990rocks-dev/banking-ai-portfolio"


def box(title: str, lines: list[str], tone: str = "") -> str:
    items = "".join(f"<li>{x}</li>" for x in lines)
    return f'<div class="box {tone}"><b>{title}</b><ul>{items}</ul></div>'


ARROW = '<div class="arrow">&#8594;</div>'

COMPONENT_DIAGRAM = (
    '<div class="diagram"><div class="row">'
    + box("Data sources", ["Ops-risk CSV (synthetic, 21 months)", "Workforce roster (case-study shape)",
                           "User upload (CSV/XLSX, in-session)"])
    + ARROW
    + box("Validation gate", ["Schema + required columns", "Allowlists, ranges, duplicates",
                              "Quarantine + warnings report", "Activate to go live"], "ctl")
    + ARROW
    + box("Governed store", ["DuckDB, immutable dataset versions", "QueryBuilder: allowlisted columns",
                             "Bound parameters + fingerprint"], "ctl")
    + '</div><div class="row">'
    + box("Prompt box", ["Guard: injection / SQL / code / PII", "Rules parser (default, free)",
                         "Claude parser (optional)"])
    + ARROW
    + box("DeckSpec", ["Pydantic model", "validate_against() allowlists", "No path from text to SQL"], "ctl")
    + ARROW
    + box("Domain analytics", ["KPIs from governed queries", "Evidence-linked commentary",
                               "Payload JSON with every number"])
    + '</div><div class="row">'
    + box("Renderer", ["python-pptx native charts + tables", "Speaker notes with provenance",
                       "LibreOffice PDF (optional)"])
    + ARROW
    + box("Versions + review", ["Append-only versions (SQLite)", "Comments, maker-checker approval",
                                "Review log CSV"], "ctl")
    + ARROW
    + box("Audit", ["Hash-chained log of datasets, prompts,", "versions, edits and decisions"], "ctl")
    + "</div></div>"
)

SEQUENCE_DIAGRAM = (
    '<div class="diagram"><ol class="seq">'
    "<li><b>Stakeholder</b> types: <i>six-slide APAC Q3 risk pack, compare Q2, rank drivers, commentary</i></li>"
    "<li><b>Guard</b> checks length, injection phrases, SQL/code and PII; rejections are logged</li>"
    "<li><b>Parser</b> (rules, or Claude if approved) proposes template, filters, months, comparison, slides</li>"
    "<li><b>DeckSpec validator</b> accepts only allowlisted values that exist in the active dataset</li>"
    "<li><b>QueryBuilder</b> runs parameterised SELECTs over approved columns and records fingerprints</li>"
    "<li><b>Domain</b> computes KPIs and writes commentary, each sentence linked to its evidence</li>"
    "<li><b>Renderer</b> writes a new .pptx (never overwrites) and hashes it; version D-0001 v1, DRAFT</li>"
    "<li><b>Follow-up</b> <i>change APAC to EMEA and add high-severity exceptions</i> becomes v2 on the "
    "pinned dataset; v1 is kept</li>"
    "</ol></div>"
)

CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: #1d2433; font-size: 10.2pt;
       line-height: 1.45; }
h1 { color: #7a1f2b; font-size: 20pt; margin: 0 0 6px; }
h2 { color: #7a1f2b; font-size: 14pt; border-bottom: 2px solid #e6d3d6; padding-bottom: 3px; margin-top: 22px; }
h3 { font-size: 11.5pt; margin-top: 16px; color: #2a3346; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 8.8pt; }
th { background: #f3eef0; text-align: left; }
th, td { border: 1px solid #d9dde5; padding: 4px 6px; vertical-align: top; }
code { background: #f2f4f7; padding: 0 3px; border-radius: 3px; font-size: 8.8pt; }
pre { background: #f2f4f7; padding: 8px; border-radius: 4px; font-size: 8.5pt; white-space: pre-wrap; }
blockquote { border-left: 3px solid #7a1f2b; margin: 8px 0; padding: 2px 10px; color: #3a4357; background: #faf7f8; }
img { max-width: 100%; border: 1px solid #d9dde5; border-radius: 4px; }
a { color: #1f5fa8; text-decoration: none; }
.part { page-break-before: always; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; }
.cover h1 { font-size: 28pt; }
.cover .sub { font-size: 13pt; color: #3a4357; margin-bottom: 18px; }
.cover .meta td { border: none; padding: 2px 8px 2px 0; font-size: 10pt; }
.badge { display: inline-block; background: #7a1f2b; color: white; padding: 2px 8px; border-radius: 10px;
         font-size: 9pt; margin-right: 6px; }
.toc li { margin: 3px 0; }
.diagram { margin: 10px 0 14px; }
.row { display: flex; align-items: stretch; margin-bottom: 10px; }
.box { flex: 1; border: 1.5px solid #8a94a8; border-radius: 6px; padding: 6px 8px; font-size: 8.6pt;
       background: #f7f9fc; }
.box.ctl { border-color: #7a1f2b; background: #fbf5f6; }
.box ul { margin: 4px 0 0; padding-left: 14px; }
.arrow { display: flex; align-items: center; padding: 0 6px; font-size: 16pt; color: #7a1f2b; }
.seq li { margin: 4px 0; }
.legend { font-size: 8.5pt; color: #5a6377; }
.shots { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.shots figure { margin: 0; font-size: 8.5pt; color: #5a6377; }
"""


def md(text: str) -> str:
    return markdown.markdown(text, extensions=["tables", "fenced_code"])


def section(path: Path, *, drop_title: bool = False) -> str:
    text = path.read_text(encoding="utf-8")
    if drop_title:
        text = re.sub(r"\A# .*\n", "", text)
    mermaids = re.findall(r"```mermaid\n.*?```", text, flags=re.S)
    if mermaids:
        text = text.replace(mermaids[0], "@@COMPONENT@@", 1)
        for m in mermaids[1:]:
            text = text.replace(m, "@@SEQUENCE@@", 1)
    html = md(text)
    html = html.replace("<p>@@COMPONENT@@</p>", COMPONENT_DIAGRAM + '<p class="legend">Dark red boxes are controls.</p>')
    html = html.replace("<p>@@SEQUENCE@@</p>", SEQUENCE_DIAGRAM)
    return html


def img_tag(path: Path, alt: str) -> str:
    data = base64.b64encode(path.read_bytes()).decode()
    return f'<img alt="{alt}" src="data:image/png;base64,{data}">'


def readme_html() -> str:
    text = (HERE / "README.md").read_text(encoding="utf-8")
    text = re.sub(r"\A# .*\n", "", text)
    text = re.sub(r"!\[.*?\]\(.*?\)\n", "", text)
    text = re.sub(r"## Documents.*\Z", "", text, flags=re.S)
    return md(text)


SHOTS = [
    ("01_dashboard_ops.png", "Master dashboard (ops risk)"),
    ("02_generated_apac_q3.png", "A: APAC Q3 six-slide deck"),
    ("03_followup_emea_high_severity.png", "B: follow-up to EMEA + high severity"),
    ("05_upload_validation.png", "D: modified dataset validation"),
    ("07_commentary_edited_v4.png", "E: commentary edited, new version"),
    ("08_provenance_audit.png", "G: provenance and audit chain"),
]


def build_html() -> str:
    shots = "".join(
        f"<figure>{img_tag(DOCS / 'screenshots' / f, cap)}<figcaption>{cap}</figcaption></figure>"
        for f, cap in SHOTS if (DOCS / "screenshots" / f).exists()
    )
    cover = f"""
<div class="cover">
  <div><span class="badge">Project 06</span><span class="badge">Synthetic data</span>
  <span class="badge">Independent, AI-assisted</span></div>
  <h1>Prompt-to-Presentation AI</h1>
  <div class="sub">Dynamic Executive Reporting Platform: documentation, architecture, test evidence and
  interview pack</div>
  <table class="meta">
    <tr><td><b>Author</b></td><td>Nipun Gupta</td></tr>
    <tr><td><b>Live demo</b></td><td><a href="{LIVE_URL}">{LIVE_URL}</a></td></tr>
    <tr><td><b>Source</b></td><td><a href="{REPO_URL}">{REPO_URL}</a>
      (projects/p6_prompt_to_presentation)</td></tr>
    <tr><td><b>Date</b></td><td>31 October 2026</td></tr>
  </table>
  <p style="margin-top:24px;font-size:9pt;color:#5a6377">Fictional bank (Ledgerline Bank). All shipped data is
  synthetic. Not affiliated with any employer or real financial institution.</p>
</div>"""
    toc = """
<div class="part"><h2>Contents</h2><ol class="toc">
<li>Overview, capabilities and verified results</li><li>Screens</li><li>Architecture and data flow</li>
<li>Data dictionary and KPI definitions</li><li>Test results and acceptance demo</li>
<li>Interview pack: demo scripts, 30 Q&amp;As, resume bullets</li></ol></div>"""
    parts = [
        ("1. Overview", readme_html()),
        ("2. Screens", f'<div class="shots">{shots}</div>'),
        ("3. Architecture and data flow", section(DOCS / "architecture.md", drop_title=True)),
        ("4. Data dictionary", section(DOCS / "data_dictionary.md", drop_title=True)),
        ("5. Test results", section(DOCS / "test_results.md")),
        ("6. Interview pack", section(DOCS / "interview_pack.md", drop_title=True)),
    ]
    body = cover + toc + "".join(f'<div class="part"><h1>{t}</h1>{h}</div>' for t, h in parts)
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DOCS / "Ledgerline_Prompt_to_Presentation_Documentation.pdf"
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "doc.html"
        html_path.write_text(build_html(), encoding="utf-8")
        js = f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto('file://{html_path}');
  await p.pdf({{ path: '{out}', format: 'A4', printBackground: true,
    displayHeaderFooter: true, headerTemplate: '<span></span>',
    footerTemplate: '<div style="font-size:7pt;width:100%;text-align:center;color:#888">'
      + 'Prompt-to-Presentation AI - page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
    margin: {{ top: '14mm', bottom: '16mm', left: '12mm', right: '12mm' }} }});
  await b.close();
}})();"""
        subprocess.run(["node", "-e", js], check=True)
    print(out)


if __name__ == "__main__":
    main()
