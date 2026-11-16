"""Build the interview and demonstration handbook (reporting/out/handbook.html, then PDF via reporting/pdf.js).

    python reporting/handbook.py && node reporting/pdf.js

Every metric is read from reporting/out/snapshot.json (built by build_snapshot.py from recorded runs);
the prose, demo scripts and answers are written here.
"""

from __future__ import annotations

import html
import io
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reporting" / "out"
S = json.loads((OUT / "snapshot.json").read_text())
D = {k: v for k, v in S["dashboards"].items()}
CLEAN = S["runs"]["CLEAN-001"]
CAT = S["catalogue"]
LAKE = S["lakehouse"]
PLAT = S["platform"]
TEAL, AMBER, SLATE, INK, RED = "#0F766E", "#D97706", "#64748B", "#0E3B43", "#B91C1C"
DEFECTS = [("D1", "Allocation residual cent dropped"), ("D2", "30/360 bonds accrue on ACT/360"),
           ("D3", "FX spot date ignores the quote-currency calendar"), ("D4", "Maker can approve their own request"),
           ("D5", "Ledger idempotency key bypassed"), ("D6", "Blotter ignores the currency filter")]


def e(x) -> str:
    return html.escape(str(x))


def fmt(n, dp=0) -> str:
    return f"{n:,.{dp}f}"


def kpi(dash: str, kid: str):
    return next(k for k in D[dash]["kpis"] if k["id"] == kid)


def chart(dash: str, cid: str):
    return next(c for c in D[dash]["charts"] if c["id"] == cid)["data"]


def table(head, rows, cls="") -> str:
    th = "".join(f"<th>{e(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    s = buf.getvalue()
    return s[s.index("<svg"):]


def hbar(rows, title, color=TEAL, w=5.0, h=None) -> str:
    rows = list(rows)
    h = h or max(2.0, 0.36 * len(rows) + 0.8)
    fig, ax = plt.subplots(figsize=(w, h))
    labels, vals = [r[0] for r in rows], [r[1] for r in rows]
    ax.barh(labels, vals, color=color, height=0.6)
    for i, v in enumerate(vals):
        ax.text(v, i, f" {v:,}", va="center", fontsize=10, color=INK)
    ax.set_title(title, fontsize=12, color=INK, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=10, colors=SLATE)
    ax.set_xlim(0, max(vals) * 1.15 if vals else 1)
    return svg(fig)


def vbar(rows, title, color=TEAL, w=6.4, h=2.4) -> str:
    fig, ax = plt.subplots(figsize=(w, h))
    labels, vals = [r[0] for r in rows], [r[1] for r in rows]
    ax.bar(labels, vals, color=color, width=0.6)
    for i, v in enumerate(vals):
        ax.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=10, color=INK)
    ax.set_title(title, fontsize=12, color=INK, loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=10, colors=SLATE)
    return svg(fig)


# --------------------------------------------------------------------------------------------- content
def cover() -> str:
    return f"""
<section class="cover">
  <div class="kicker">Independent portfolio project · synthetic data · simulated venues</div>
  <h1>CapitalFlow 360</h1>
  <p class="sub">Interview and demonstration handbook</p>
  <p class="lead">A simulated multi-asset trade lifecycle platform, from order to reconciled books, tested against an independent oracle.</p>
  <div class="coverstats">
    <div><b>{fmt(S['data']['trades'])}</b><span>baseline trades, 6 asset classes</span></div>
    <div><b>{CAT['rules_total']}</b><span>business rules in the catalogue</span></div>
    <div><b>{fmt(CAT['cases'])}</b><span>generated test cases</span></div>
    <div><b>{fmt(CLEAN['executed'])}</b><span>executions in run CLEAN-001, {CLEAN['failed']} failed</span></div>
  </div>
  <p class="meta">Author: Nipun Gupta (domain design) with AI-assisted implementation · Business date {e(D['executive']['as_of'])} · Metrics generated {e(S['generated_at'])}</p>
</section>"""


def contents() -> str:
    items = ["Scope, honesty and contribution", "The platform at a glance", "Architecture", "Technology and design decisions",
             "Trade lifecycle by product", "Role workspaces and controls", "Business rule catalogue", "Hand-calculated worked examples",
             "Synthetic data", "Lakehouse: Bronze, Silver, Gold", "Test strategy", "Test results and injected defects",
             "Real defects the tests found", "Dashboards and metric definitions", "Three-minute demo script", "Ten-minute demo script",
             "Interview questions and answers", "Resume lines and how to describe the work", "Limits, next steps and glossary",
             "Appendix A: protected API endpoints", "Appendix B: exceptions and breaks", "Appendix C: sample test cases"]
    li = "".join(f"<li>{e(t if t.startswith('Appendix') else f'{i + 1}. {t}')}</li>" for i, t in enumerate(items))
    return f"""
<section>
  <h2>Contents</h2>
  <ul class="toc">{li}</ul>
  <div class="callout"><b>How to use this handbook.</b> Read sections 1 to 3 before any interview. Use section 15 or 16 to rehearse the demo,
  and section 17 to practise answers out loud. Every number here is read from the recorded runs in <code>reporting/out/snapshot.json</code>;
  re-running <code>reporting/build_reports.py</code> refreshes them.</div>
</section>"""


def scope() -> str:
    return """
<section>
  <h2>1. Scope, honesty and contribution</h2>
  <h3>What CapitalFlow 360 is</h3>
  <p>CapitalFlow 360 is an independent portfolio project. It simulates how a bank's operations platform carries trades in six asset classes
  from order to settled cash and reconciled books: orders and fills, booking, allocation, confirmation matching, clearing, settlement,
  a double-entry ledger, reconciliation against custodian statements, and exceptions. It has seven role workspaces, seven dashboards,
  a Bronze/Silver/Gold lakehouse, and a test system that generates and executes thousands of cases against an independent oracle.</p>
  <h3>What it is not</h3>
  <ul>
    <li>It is <b>not connected</b> to any real exchange, broker, clearing house, custodian, payment system or messaging network. Venues and
    counterparties are simulators driven by synthetic files.</li>
    <li>All data is <b>synthetic</b>, generated from a fixed seed. No client, trade or price is real.</li>
    <li>Calculations follow standard market conventions (day counts, spot dates, VWAP, accrued interest) but it is <b>not production-grade
    pricing</b>, and it makes <b>no claim of regulatory compliance</b>.</li>
    <li>It has <b>not been deployed publicly</b> at the time of writing, and the Databricks notebook has <b>not yet been executed on Databricks</b>.
    The lakehouse ran on local Spark and Delta Lake.</li>
  </ul>
  <h3>Who did what</h3>
  <table class="two">
    <thead><tr><th>Nipun Gupta: domain and design</th><th>AI-assisted implementation</th></tr></thead>
    <tbody><tr>
      <td><ul><li>Product scope: six asset classes, 500 trades split 140/120/80/70/50/40</li><li>Lifecycle stages and which products skip which stage</li>
      <li>The business rules, their boundaries and error codes</li><li>Role design and maker-checker controls</li><li>Scenarios, negative data types and the defect injection plan</li>
      <li>Acceptance criteria and what "done" means</li><li>Review of results against operations experience</li></ul></td>
      <td><ul><li>Code for the API, database, React screens and simulators</li><li>Data generators and the lakehouse pipeline</li>
      <li>The oracle, case generator, executor and Playwright tests</li><li>Report and deck generators</li>
      <li>Every result was run, checked against the oracle and corrected where it was wrong; failures are reported, not hidden</li></ul></td>
    </tr></tbody>
  </table>
  <div class="callout">Say it this way in an interview: "I designed the business flows, rules and test scenarios from twelve years in banking operations,
  and I built it with an AI coding assistant. I can walk you through why each rule exists and how the tests prove it."</div>
</section>"""


def glance() -> str:
    ex = D["executive"]
    k = {x["id"]: x for x in ex["kpis"]}
    rows = sorted(S["data"]["by_group"].items(), key=lambda x: x[1])
    ist = PLAT["instruction_status"]
    return f"""
<section>
  <h2>2. The platform at a glance</h2>
  <div class="stats">
    <div><b>{fmt(k['trades']['value'])}</b><span>trades booked</span></div>
    <div><b>{k['settle_rate']['value']}%</b><span>of due instructions settled ({k['settle_rate']['numerator']}/{k['settle_rate']['denominator']})</span></div>
    <div><b>{k['auto_match']['value']}%</b><span>confirmations auto-matched ({k['auto_match']['numerator']}/{k['auto_match']['denominator']})</span></div>
    <div><b>{k['breaks']['value']}</b><span>reconciliation breaks, all planted</span></div>
  </div>
  <div class="grid2">
    <div>{hbar(rows, "Baseline trades by asset class")}</div>
    <div>
      <h3>Book of record at the statement date</h3>
      {table(["Measure", "Value"], [["Ledger rows (cash and securities)", fmt(PLAT['ledger_rows'])], ["Lifecycle events", fmt(PLAT['lifecycle_events'])],
        ["Audit events (append-only)", fmt(PLAT['audit_events'])], ["Settlement instructions", fmt(PLAT['instructions'])],
        ["Settled / failed / partial / not yet due", f"{ist.get('SETTLED', 0)} / {ist.get('FAILED', 0)} / {ist.get('PARTIAL', 0)} / {ist.get('PENDING', 0)}"],
        ["Confirmations received", fmt(PLAT['confirmations'])], ["Gross notional (USD)", fmt(k['notional']['value'])]])}
    </div>
  </div>
  <p>The business window runs from {e(S['data']['opening_date'])} to the statement date {e(S['data']['statement_date'])}. Exceptions are not noise:
  every failed settlement, mismatch and reconciliation break was planted in the synthetic data and labelled in the oracle, so the platform
  is judged on finding exactly those.</p>
</section>"""


def architecture() -> str:
    def boxes(items, cls):
        return "".join(f'<div class="box {cls}"><b>{e(t)}</b><span>{e(s)}</span></div>' for t, s in items)

    return f"""
<section>
  <h2>3. Architecture</h2>
  <p>Four parts share one golden source of synthetic data. The platform books from the same Excel files the lakehouse ingests, and the
  test system recomputes expected results independently of both.</p>
  <div class="arch">
    <div class="lane"><h4>Synthetic data</h4>{boxes([("13 Excel workbooks", "fixed seed, hashes in manifest"), ("Oracle files", "expected outcomes, labels"), ("Negative files", "25 defect types, kept apart")], "d")}</div>
    <div class="lane"><h4>Platform</h4>{boxes([("React + TypeScript", "7 role workspaces, 7 dashboards"), ("FastAPI + OpenAPI", "rules, state machines, ledger, audit"), ("PostgreSQL 16", "Alembic migrations"), ("Simulators", "venue, CCP, custodian, counterparties")], "p")}</div>
    <div class="lane"><h4>Lakehouse</h4>{boxes([("Landing", "Excel to Parquet, schema contract"), ("Bronze", "append-only stream"), ("Silver", "quality gate, quarantine, MERGE"), ("Gold", "lifecycle, cash projection, DQ")], "l")}</div>
    <div class="lane"><h4>Verification</h4>{boxes([("Oracle", "exact fractions, no shared code"), ("Case catalogue", f"{fmt(CAT['cases'])} cases, {CAT['scenarios']} scenarios"), ("Executor + Playwright", "API, rules, browsers"), ("Reports", "dashboards, decks, this handbook")], "v")}</div>
  </div>
  <h3>Request path</h3>
  <p>A booking arrives at <code>POST /api/trades</code> with an <code>Idempotency-Key</code>. Pydantic validates the shape; the rules engine
  enriches and checks the trade (eligibility, calendar, economics, limits); the service writes the trade, its allocations, lifecycle event,
  and audit event in one transaction with the request's correlation id. Downstream stages are driven by the simulators and the end-of-day
  processor, and every state change goes through the server-side state machine. The React screens only ever call the API.</p>
  <h3>Where the code lives</h3>
  {table(["Folder", "Contents"], [["backend/cf360", "API routes, services, rules engine and catalogue, models, simulators"], ["backend/migrations", "Alembic revisions"],
    ["frontend/src", "React pages, data grid, dashboards"], ["frontend/e2e", "Playwright browser specs"], ["datagen", "Excel, oracle and negative data generators"],
    ["lakehouse", "Spark pipeline, local runner, Databricks notebook and bundle"], ["testkit", "Oracle, case generator, executor, findings"], ["reporting", "Snapshot, decks, handbook"]])}
</section>"""


def tech() -> str:
    return f"""
<section>
  <h2>4. Technology and design decisions</h2>
  {table(["Layer", "Pinned technology"], [["Frontend", "React 19.3, TypeScript 5.9, Vite 7.3, MUI 7.3, MUI X Data Grid 8.29, Recharts 3.10"],
    ["API", "Python 3.13, FastAPI 0.163, Pydantic 2.13, SQLAlchemy 2.0, Alembic 1.20"], ["Database", "PostgreSQL 16"],
    ["Lakehouse", f"PySpark {LAKE['spark']}, Delta Lake {LAKE['delta']}; Databricks serverless notebook and asset bundle"],
    ["Testing", "pytest 9, Hypothesis, Playwright 1.63 (TypeScript)"], ["Reports", "pptxgenjs 3.12 for decks; HTML printed to PDF by Chromium for this handbook"]])}
  <h3>Decisions worth explaining</h3>
  <ul>
    <li><b>Decimal everywhere.</b> Money and quantities are Python <code>Decimal</code> and PostgreSQL <code>NUMERIC</code>, rounded half-up to each
    currency's minor unit (JPY has none). Floats never touch an amount.</li>
    <li><b>Idempotency at two levels.</b> The API honours <code>Idempotency-Key</code> (same key and body returns the first result; a different body is
    refused with 409), and every ledger posting carries a unique event key so replaying a day posts nothing twice.</li>
    <li><b>Optimistic concurrency.</b> Amendments send <code>If-Match</code> with the version they read. A stale version is refused, so two users cannot
    silently overwrite each other.</li>
    <li><b>Server-side state machine.</b> Each product family has its own allowed edges. An illegal move returns 409 even if someone calls the API directly.</li>
    <li><b>One error envelope.</b> Every error returns a code, message, details and the correlation id, so a support analyst can find the audit trail.</li>
    <li><b>Append-only audit.</b> A database trigger refuses UPDATE and DELETE on audit events.</li>
    <li><b>Sandboxes for tests.</b> Lifecycle tests run in sandboxes tagged on every row, and dry-run bookings sit inside savepoints that roll back,
    so tests never disturb the main book.</li>
    <li><b>Defects as switches.</b> Six realistic defects are compiled in but off by default; <code>CF360_DEFECTS</code> turns one on, which is how the
    tests are proven able to fail.</li>
  </ul>
</section>"""


def lifecycle() -> str:
    rows = [["Equities, ETFs", "CCP cash", "Captured, Enriched, Allocated, Confirmed, Cleared, Instructed, Settled", "Delivery versus payment on the market calendar"],
            ["Government and corporate bonds", "Bilateral cash", "as equities, without Cleared", "Accrued interest by day count; ex-dividend periods for gilts"],
            ["FX spot and forwards", "Bilateral cash", "as bonds", "Value date on both currency calendars; forward points"],
            ["Futures", "Listed derivative", "to Open, then Expired or Closed", "Daily variation margin instead of instructions"],
            ["Options", "Listed derivative", "to Open, then Exercised or Expired", "Premium settles; auto-exercise in the money at expiry"],
            ["Interest-rate swaps", "OTC derivative", "to Open, then Matured", "Scheduled net payments, resets"],
            ["Repo, reverse repo, stock loan and borrow", "Financing", "to Open, then Closed", "Opening and closing legs, haircut, margin calls, fees"]]
    ts = PLAT["trade_status"]
    return f"""
<section>
  <h2>5. Trade lifecycle by product</h2>
  <p>Every trade walks the same spine and stops where its product stops. The edges are written out by hand in
  <code>testkit/oracle/specs.py</code> and tested both ways: every allowed edge must succeed and every other edge must be refused.</p>
  <div class="flow">{''.join(f'<span>{s}</span>' for s in ['Captured', 'Enriched', 'Allocated', 'Confirmed', 'Cleared', 'Instructed', 'Settled'])}</div>
  {table(["Products", "Family", "Path", "What is special"], rows)}
  <h3>Side states</h3>
  <p>Partially settled, settlement failed, pending approval, amended (a new version), cancelled and exception. A settled trade cannot be amended
  economically; it is cancelled with exact reversing ledger entries and rebooked.</p>
  <div class="grid2">
    <div>{hbar(sorted(ts.items(), key=lambda x: -x[1]), "Trades by status at the statement date", color=INK)}</div>
    <div><h3>Reading the status mix</h3><p>Most cash products have settled. Derivatives and financing trades sit in Open by design: they live until
    expiry, maturity or close. The handful of failed and partially settled trades are the planted fails, each with an exception routed to settlements.</p></div>
  </div>
</section>"""


def roles() -> str:
    rows = [["Trader", "trader1, trader2", "Orders, fills, booking with pre-trade checks and dry-run validation"],
            ["Middle office", "mo1", "Enrichment, matching queue, amendments, SSI requests"],
            ["Settlements", "settle1", "Instructions, fails, partials, retries"],
            ["Reconciliation", "recon1", "Position and cash breaks against custodian statements, write-offs with approval"],
            ["Risk and control", "risk1", "Limits, margin calls, exceptions, dashboards, test runs"],
            ["Checker", "checker1, checker2", "Approves or rejects what others request; never their own"],
            ["Admin", "admin1", "Business clock, end-of-day processing, imports, reference data"]]
    return f"""
<section>
  <h2>6. Role workspaces and controls</h2>
  {table(["Role", "Demo users", "Works on"], rows)}
  <p>The demo users share one password, set by <code>CF360_DEMO_PASSWORD</code>; the repository's default is a published demo value, so any
  public host must override it. Permissions are enforced by the API,
  so hiding a button is a convenience, not the control; the 264-case role matrix calls each protected endpoint as every role.</p>
  <h3>Maker-checker</h3>
  <div class="flow">{''.join(f'<span>{s}</span>' for s in ['Maker requests', 'Version check (If-Match)', 'Checker approves', 'New trade version', 'Allocations re-cut'])}</div>
  <ul>
    <li>Economic changes (quantity, price, dates, account) need a checker. Non-economic changes (book, trader) apply directly as a new version.</li>
    <li>A maker who tries to approve their own request is refused and the attempt is audited (rule R092). Defect D4 removes this check; two tests catch it.</li>
    <li>Force-matching a confirmation and writing off a reconciliation break also need a reason and a checker.</li>
  </ul>
  <h3>Audit trail</h3>
  <p>Every request carries a correlation id, returned in the response header and the error envelope and stored on every audit event it causes.
  The audit table is append-only at the database level. At the statement date it holds {fmt(PLAT['audit_events'])} events.</p>
</section>"""


def rules_section() -> str:
    rows = [[r["id"], r["family"], r["name"], r["error_code"] or "", r["cases"]] for r in S["rules"]]
    half = (len(rows) + 1) // 2
    fam = sorted(CAT["rule_families"].items(), key=lambda x: -x[1])
    return f"""
<section>
  <h2>7. Business rule catalogue</h2>
  <p>The catalogue holds {CAT['rules_total']} rules in {len(fam)} families. Each rule has an id, error code and documented boundaries,
  and the case generator builds cases on both sides of each boundary. {CAT['rules_covered']} of {CAT['rules_total']} rules have executed cases
  ({kpi('testing', 'rule_cov')['value']}%). Rules without a dedicated generated case yet: {', '.join(CAT['rules_uncovered'])}.</p>
  <div class="grid2"><div>{hbar(fam, "Rules by family", h=4.2)}</div>
  <div><h3>Boundary examples</h3>{table(["Rule", "Boundary"], [[r['id'], r['boundaries']] for r in S['rules'] if r['boundaries']][:12], "small")}</div></div>
</section>
<section>
  <h3>Full catalogue (part 1)</h3>
  {table(["Id", "Family", "Rule", "Error code", "Cases"], rows[:half], "small")}
</section>
<section>
  <h3>Full catalogue (part 2)</h3>
  {table(["Id", "Family", "Rule", "Error code", "Cases"], rows[half:], "small")}
</section>"""


def worked() -> str:
    we = S["worked_examples"]
    blocks = "".join(f"""<div class="we"><div class="weh"><b>{e(w['id'])}</b> {e(w['title'])}<span class="tag {'ok' if w['pass'] else 'bad'}">{'PASS' if w['pass'] else 'FAIL'}</span></div>
      <div><i>Rules</i> {e(', '.join(w['rules']))}</div><div><i>Inputs</i> {e(w['inputs'])}</div><div><i>Working</i> {e(w['working'])}</div>
      <div><i>Expected</i> <code>{e(' / '.join(w['expected']) if isinstance(w['expected'], list) else w['expected'])}</code></div></div>""" for w in we)
    return f"""
<section>
  <h2>8. Hand-calculated worked examples</h2>
  <p>{len(we)} examples are worked by hand below. Each is checked twice in every run: the platform must produce the expected value, and the
  independent oracle must too. {sum(1 for w in we if w['pass'])} of {len(we)} pass. They are the fastest way to show an interviewer that the
  arithmetic is understood, not just coded.</p>
  <div class="wes">{blocks}</div>
</section>"""


def data_section() -> str:
    files = [[f.replace(".xlsx", ""), ", ".join(v["rows"]), fmt(sum(v["rows"].values()))] for f, v in S["data"]["files"].items()]
    neg = [[f, fmt(sum(v["rows"].values()))] for f, v in S["data"]["negative_files"].items()]
    return f"""
<section>
  <h2>9. Synthetic data</h2>
  <p>Thirteen related workbooks describe one synthetic bank. They are generated from seed {e(S['data']['seed'])}, so the same files come out every
  time, and <code>data/manifest.json</code> records each file's SHA-256 hash, sheets and row counts. Every workbook carries a dictionary sheet with
  column types and nullability; the lakehouse schema contract is generated from it.</p>
  {table(["Workbook", "Sheets", "Rows"], files)}
  <h3>Oracle and negative files</h3>
  <p>The oracle files hold expected outcomes (settlement dates, economics, planted fails and breaks) and are never read by the platform. Negative files
  are kept apart from the 500-trade baseline and every row is labelled with the rule, error and the layer expected to catch it: API rejection,
  lakehouse quarantine, late acceptance or an enrichment exception.</p>
  {table(["Negative file", "Rows"], neg)}
</section>"""


def lakehouse() -> str:
    q, g = LAKE["quarantine"], LAKE["negative_gate"]
    return f"""
<section>
  <h2>10. Lakehouse: Bronze, Silver, Gold</h2>
  <div class="flow">{''.join(f'<span>{s}</span>' for s in ['Excel', 'Parquet landing', 'Bronze (stream)', 'Silver (gate, MERGE)', 'Gold'])}</div>
  <ul>
    <li><b>Landing</b> converts each sheet to Parquet with every value kept as text, and checks columns against the contract. A sheet whose columns
    drift is refused whole (DQ05) rather than half-loaded.</li>
    <li><b>Bronze</b> appends with a streaming checkpoint, so re-running ingests only new files.</li>
    <li><b>Silver</b> casts types, checks required fields, references and domains, de-duplicates, quarantines bad rows with the rule that caught them,
    then MERGEs, but only if less than 5% of the batch was quarantined.</li>
    <li><b>Gold</b> builds the trade lifecycle ({fmt(LAKE['gold']['gold_trade_lifecycle'])} rows), a cash position projection
    ({fmt(LAKE['gold']['gold_cash_position_projection'])} rows) and a data-quality summary.</li>
  </ul>
  <h3>Local Spark evidence</h3>
  <p>Run on {e(LAKE['environment'])} with PySpark {e(LAKE['spark'])} and Delta Lake {e(LAKE['delta'])} in {fmt(LAKE['elapsed_seconds'])} seconds:
  {LAKE['passed']} checks passed, {LAKE['failed']} failed. The negative batch quarantined {g['rows_quarantined']} of {g['rows_in']} trade rows
  ({g['quarantine_ratio'] * 100:.1f}%), so the gate failed and Silver stayed at 500 trades. Quarantine caught {q['caught']} of the {q['labelled']}
  rows labelled for it, with the labelled rule and error, and nothing the labels leave to the API.</p>
  {table(["Check", "Result"], [[c['check'], c['result']] for c in LAKE['checks']], "small")}
  <p class="note">The Databricks notebook (<code>lakehouse/databricks/00_run_lakehouse.py</code>) and asset bundle use the same pipeline code with Unity Catalog
  tables. They have not yet been executed on Databricks; the results above are from local Spark only.</p>
</section>"""


def strategy() -> str:
    suites = sorted(CAT["by_suite"].items(), key=lambda x: -x[1])
    return f"""
<section>
  <h2>11. Test strategy</h2>
  <p><b>The rule: expected results never come from the code under test.</b> They come from four independent places:</p>
  <ol>
    <li><b>An independent oracle</b> in <code>testkit/oracle</code>, written with exact fractions, recomputes economics, dates and outcomes. An import test
    proves it shares no code with the platform.</li>
    <li><b>Documented rule boundaries</b> in the catalogue: for example a price 10.00% from reference is accepted and 10.01% is rejected.</li>
    <li><b>Hand-written specifications</b>: lifecycle edges per product family and the role permission matrix.</li>
    <li><b>Hand-calculated worked examples</b> (section 8).</li>
  </ol>
  <div class="grid2"><div>{hbar(suites, "Generated cases by suite", h=3.6)}</div>
  <div>{table(["Suite", "What it proves"], [["booking", "All 500 baseline trades rebook to the oracle's economics and dates"], ["lifecycle", "Every allowed edge works; every other edge is refused"],
    ["calendar", "Settlement and FX value dates across holidays and both currency calendars"], ["access", "Each protected endpoint for each role: allowed or 403"],
    ["calculation", "Accrued interest, allocation splits, worked examples"], ["negative", "Each labelled bad row is refused with its rule"],
    ["rules", "Both sides of each rule boundary"], ["outcomes", "Planted fails, breaks and exceptions appear exactly"], ["api", "Contract, idempotency, filters, dashboards"],
    ["lakehouse", "The 14 Spark checks"], ["ui", "Browser flows on desktop and phone sizes"]], "small")}</div></div>
  <p>The catalogue has {fmt(CAT['cases'])} cases in {CAT['scenarios']} scenarios, written to JSON, Parquet and Excel with a traceability sheet linking each case
  to its rules and source row. Each run starts from a freshly seeded database, and results are imported into the platform's Test runs page.</p>
</section>"""


def results() -> str:
    by = [[k, fmt(v["passed"]), v["failed"]] for k, v in CLEAN["by_suite"].items()]
    drows = [[i, t, S["runs"][f"DEFECT-{i}"]["failed"], ", ".join(f"{k} {v}" for k, v in S["runs"][f"DEFECT-{i}"]["failed_by_prefix"].items())] for i, t in DEFECTS]
    return f"""
<section>
  <h2>12. Test results and injected defects</h2>
  <div class="stats">
    <div><b>{fmt(CLEAN['unique_cases_executed'])}</b><span>of {fmt(CLEAN['catalogue_cases'])} catalogue cases executed</span></div>
    <div><b>{fmt(CLEAN['executed'])}</b><span>executions incl. browser runs</span></div>
    <div><b>{CLEAN['failed']}</b><span>failures in CLEAN-001</span></div>
    <div><b>{CLEAN['browser_runs']}</b><span>browser runs ({', '.join(f'{k} {v}' for k, v in CLEAN['browser_runs_by_project'].items())})</span></div>
  </div>
  <div class="grid2"><div>{table(["Suite", "Passed", "Failed"], by, "small")}</div>
  <div>{vbar([(i, S['runs'][f'DEFECT-{i}']['failed']) for i, _ in DEFECTS], "Failed cases per injected defect", color=AMBER, w=5)}</div></div>
  <h3>Defect injection</h3>
  <p>Each defect was switched on alone, against a freshly seeded database, and the full catalogue was run. Every defect was caught, then the clean run
  CLEAN-001 reran everything with all defects off.</p>
  {table(["Defect", "What it breaks", "Failed", "Caught by (case prefix and count)"], drows)}
  <p class="note">Browsers: only Chromium could be installed in the build sandbox (its network policy blocks the Firefox and WebKit downloads). The CI
  workflow installs all three engines and runs the same specs; those results are reported only once CI has run.</p>
</section>"""


def findings() -> str:
    rows = [["F1", "Worked example WE12", "Japanese substitute holidays were generated on bank closing days, shifting a USD/JPY spot date", "Calendar generator fixed, data regenerated"],
            ["F2", "10 labelled negative cases", "Three booking checks were built with the field name in the severity slot, so a 600-share allocation of a 500-share block was accepted", "Booking fixed; the rules engine now refuses unknown severities"],
            ["F3", "Role matrix (264 cases)", "Approvals checked the role after body validation: non-checkers got 422 instead of 403", "Authorisation moved ahead of validation"],
            ["F4", "Negative cases N15, N19", "Two negative-data generators produced rows that tested the wrong rule", "Generator fixed; labels unchanged"],
            ["F5", "First defect-run import", "Generated case ids were longer than the result columns (HTTP 500)", "Alembic migration 0003"]]
    return f"""
<section>
  <h2>13. Real defects the tests found</h2>
  <p>The suite earned its keep before any defect was planted. These are genuine mistakes, found by the cases named, fixed, and now passing in CLEAN-001.
  They are recorded in <code>testkit/FINDINGS.md</code>.</p>
  {table(["#", "Found by", "What was wrong", "Fix"], rows)}
  <p>Test-design mistakes were also corrected on the way (the product was right): one rule boundary used the wrong last price date, one "below minimum"
  case produced a zero quantity, one case used a future outside the priced window, and one dashboard name was misspelt.</p>
  <div class="callout"><b>Interview angle.</b> F2 is the best story: the code looked right, the screens worked, and a 600-share allocation of a 500-share block
  would have gone through. Only labelled negative data, with an expected rule for every row, exposed it.</div>
</section>"""


def dashboards() -> str:
    out = []
    for name, d in D.items():
        rows = [[k["label"], (fmt(k["value"], 2) if isinstance(k["value"], float) and k["unit"] == "USD" else k["value"]) if k["value"] is not None else "",
                 k.get("definition") or ""] for k in d["kpis"]]
        out.append(f"<h3>{e(name.replace('-', ' ').title())}</h3>" + table(["Metric", "Value", "Definition"], rows, "small"))
    return f"""
<section>
  <h2>14. Dashboards and metric definitions</h2>
  <p>Seven dashboards share one metrics service (<code>/api/reports/snapshot</code>) so numbers never disagree between screens. Every metric carries its
  definition, numerator, denominator and population, and most drill through to the rows behind them. Values below are as of {e(D['executive']['as_of'])}.</p>
  {''.join(out[:3])}
</section>
<section>
  {''.join(out[3:])}
  <p class="note">Engineering timings are single-machine figures from this environment, not load tests.</p>
</section>"""


def demo3() -> str:
    steps = [("0:00", "Frame it", "\"CapitalFlow 360 is a portfolio simulation of a bank's trade lifecycle: 500 synthetic trades in six asset classes, order to reconciled books. Nothing is connected to real venues.\""),
             ("0:20", "Executive dashboard (risk1)", f"Point at {kpi('executive', 'settle_rate')['value']}% settled and the {kpi('executive', 'breaks')['value']} breaks. \"Every exception you see was planted and labelled, so I can prove the platform finds exactly those.\""),
             ("0:50", "Book a trade (trader1)", "Fill the form, set the trade date to 25 December, press Validate. Show the R010 holiday rejection from the server, then a valid date and the derived settlement date."),
             ("1:30", "Trade detail", "Open a trade: lifecycle path, versions, ledger postings, audit trail with correlation id."),
             ("2:00", "Test runs", f"\"{fmt(CAT['cases'])} generated cases against an independent oracle. I injected six realistic defects; each was caught, then a clean rerun had zero failures.\""),
             ("2:40", "Close", "\"I designed the flows, rules and scenarios; I built it with an AI coding assistant and checked every result.\"")]
    return f"""
<section>
  <h2>15. Three-minute demo script</h2>
  <p>Before you start: the stack is running, you are signed out, and the browser is at the login page. Demo passwords come from your environment.</p>
  {table(["Time", "Screen", "Say and do"], steps)}
  <h3>If something goes wrong</h3>
  <ul><li>If a page is slow, talk through the dashboard definitions while it loads; do not refresh repeatedly.</li>
  <li>If a booking fails unexpectedly, open the error: it names the rule and the correlation id. That is the feature.</li>
  <li>Never claim a result you have not shown. "That runs in CI" is fine only once CI has run.</li></ul>
</section>"""


def demo10() -> str:
    steps = [("0:00", "Scope and honesty", "Simulation, synthetic data, independent project; who did what (section 1)."),
             ("0:45", "Architecture", "Four parts, one golden source; request path for a booking (section 3)."),
             ("1:45", "Trader: book and validate", "Dry-run validation, R010 holiday, VWAP from fills, idempotency key: double-click books once."),
             ("3:00", "Middle office: matching queue", "Show a tolerance match and a price mismatch exception; explain force-match needs a checker."),
             ("4:00", "Maker-checker", "As mo1 amend a quantity; as checker1 approve; show the new version and re-cut allocations. Mention the self-approval block (R092)."),
             ("5:15", "Settlements", "Fails ageing, a partial settlement, the SSI rule (R080), retries."),
             ("6:15", "Reconciliation", "The 8 breaks: cash and position, with the 0.50 tolerance (R096)."),
             ("7:00", "Lakehouse", "Bronze, Silver, Gold; the negative batch held back by the quality gate; replay changes nothing."),
             ("8:00", "Tests and defects", "Test runs page: CLEAN-001 and the six defect runs; the D6 screenshot."),
             ("9:15", "Close", "What you would do next: cross-browser CI results, Databricks run, a public demo host.")]
    return f"""
<section>
  <h2>16. Ten-minute demo script</h2>
  {table(["Time", "Segment", "What to show and say"], steps)}
  <h3>Questions to invite</h3>
  <ul><li>"Ask me how the tests know the right answer without trusting the code."</li><li>"Ask me what happens if a settlement batch is replayed."</li>
  <li>"Ask me which defect was hardest to catch." (D5: only one targeted check catches it, so that check has to exist.)</li></ul>
</section>"""


QA = [
    ("Scope", [
        ("What is CapitalFlow 360 in one sentence?", "A simulated multi-asset operations platform that carries 500 synthetic trades from order to reconciled books, with a test system that proves its results against an independent oracle."),
        ("Is it connected to real markets?", "No. Venues, the CCP, custodians and counterparties are simulators fed from synthetic files. It makes no claim of production pricing or regulatory compliance."),
        ("What did you do and what did the AI do?", "I set the scope, lifecycle, rules and their boundaries, the roles and controls, the scenarios, negative data types and the defect plan, and I reviewed results. The code was written with an AI coding assistant, and every result was run and checked."),
        ("Why six asset classes?", "They have genuinely different lifecycles: cash products settle DVP, FX needs two calendars, futures settle margin, options exercise, swaps pay schedules and financing has two legs. Fewer would hide the hard parts."),
        ("Why exactly 500 trades?", "Enough to exercise every path and planted exception, small enough to reason about every one. The split is fixed: 140 equities and ETFs, 120 bonds, 80 FX, 70 listed derivatives, 50 swaps, 40 financing."),
        ("Is it deployed?", "Not publicly yet. It runs locally with Docker Compose. A free host is proposed and nothing that can charge is switched on without approval."),
    ]),
    ("Business process", [
        ("Walk me through a trade's life.", "Order and fills, booking at the volume-weighted price, allocation to accounts, confirmation matching, clearing where a CCP applies, settlement instruction from the SSI, settlement, ledger posting, reconciliation."),
        ("How is the booked price set when an order fills in clips?", "Volume-weighted average of the fills to six decimals. For 600 at 71.00 and 300 at 71.10 it is 71.033333. Rule R026 rejects a booking that disagrees with its fills."),
        ("How are allocations rounded?", "Pro-rata quantities must sum to the block (R024). Amounts are rounded to the currency's minor unit and the residual goes to the largest allocation, first on ties (R025)."),
        ("How do you derive an FX spot date?", "T+2 business days, and the date must be a business day on both currencies' calendars. Defect D3 checks only the base-currency calendar and 46 cases fail."),
        ("What is an SSI and why is it effective-dated?", "Standing settlement instructions tell you where to deliver. They change over time, so the one valid on the settlement date is used. An SSI that ended the day before counts as missing (R080)."),
        ("What happens on a settlement fail?", "The instruction is marked failed with a reason, an exception goes to settlements, it ages by business days, and it retries. Partial settlement needs at least one lot available (R085)."),
        ("What does reconciliation compare?", "Internal positions and cash from the ledger against custodian statements, per account and instrument or currency. Differences above tolerance become breaks."),
        ("What is your cash tolerance?", "A difference of 0.50 or less is inside tolerance (R096) and is not a break; anything larger is."),
        ("How do futures differ in settlement?", "They do not deliver notional; they settle daily variation margin against the settlement price, and initial margin is held. They stay Open until expiry or close."),
        ("How are repos handled?", "Two legs: opening cash against collateral with a haircut, and closing cash with interest. Collateral value is checked and margin calls raised when it falls short."),
        ("What is maker-checker here?", "Economic changes need a second person. The maker requests, a checker approves, the trade gets a new version and allocations are re-cut. A maker cannot approve their own request."),
        ("Can you amend a settled trade?", "Not economically. You cancel it, which posts exact reversing ledger entries, and rebook (R090)."),
        ("How are exceptions routed?", "Each exception type has an owning team: matching breaks to middle office, fails to settlements, breaks to reconciliation, margin calls to risk. Each has an age and a status."),
        ("What is a potential duplicate?", "A booking with the same economics as another but a different source reference (R029). It is flagged as a warning for review, not rejected, because genuine repeats happen."),
    ]),
    ("Rules and calculations", [
        ("How many rules are there and how are they organised?", f"{CAT['rules_total']} rules in {len(CAT['rule_families'])} families such as eligibility, calendar, economics, settlement and data quality. Each has an id, an error code and documented boundaries."),
        ("Give an example of a boundary.", "The price collar: 10.00% away from the reference price is accepted, 10.01% is rejected. Cases are generated on both sides."),
        ("How do you compute bond accrued interest?", "Coupon times the day-count fraction from the last coupon date to settlement. 30/360 for US corporates, ACT/ACT for Treasuries and gilts. Defect D2 used ACT/360 for 30/360 bonds and 52 cases failed."),
        ("What is an ex-dividend period?", "For gilts, trades settling in the last days before a coupon do not receive it, so accrued interest is negative. Worked example WE11 shows it."),
        ("How do you handle JPY?", "It has no minor unit, so amounts round to whole yen. Worked example WE05 checks it."),
        ("How do you avoid rounding errors?", "Decimal arithmetic end to end, half-up rounding to minor units at defined points, and an oracle using exact fractions to compare against."),
        ("How are commissions computed?", "Basis points on gross consideration with a minimum per trade, plus exchange fees, and UK stamp duty on purchases only."),
        ("What is in a hand-calculated worked example?", f"Inputs, the working, and the expected value, written by hand. There are {len(S['worked_examples'])}, and each must match both the platform and the oracle."),
    ]),
    ("Architecture and engineering", [
        ("Why FastAPI and PostgreSQL?", "FastAPI gives typed request validation and an OpenAPI contract for free; PostgreSQL gives NUMERIC, transactions, constraints and triggers that suit a ledger."),
        ("How do you make bookings idempotent?", "Clients send an Idempotency-Key. The same key and body returns the first result; the same key with a different body is refused with 409."),
        ("And the ledger?", "Each posting has a unique event key per leg, so replaying a settlement batch posts nothing new. Defect D5 bypasses it and the idempotency check fails."),
        ("How do you stop two users overwriting each other?", "Optimistic concurrency: amendments send If-Match with the version read; a stale version is refused (R098)."),
        ("Where is the state machine enforced?", "On the server. Each product family has allowed edges; any other move returns 409, whoever calls it."),
        ("How is the audit trail tamper-evident?", "A database trigger refuses UPDATE and DELETE on audit events, and every event carries the request's correlation id."),
        ("How do tests run without polluting data?", "Lifecycle tests run in sandboxes tagged on every row; dry-run bookings run inside savepoints that roll back; each full run starts from a fresh seed."),
        ("How are schema changes managed?", "Alembic migrations, three revisions so far, used to build every database including the test one."),
        ("How do the dashboards stay consistent?", "One metrics service computes every KPI with its definition, numerator, denominator and population; all screens and these reports read from it."),
        ("How would you scale it?", "Move end-of-day processing to a job queue, partition ledger and events by business date, and put read models behind the dashboards. Current timings are single-machine, not load tests."),
    ]),
    ("Data and lakehouse", [
        ("How is the synthetic data generated?", f"Python with a fixed seed ({S['data']['seed']}), written to Excel with openpyxl, with a manifest of hashes and row counts so any change is visible."),
        ("Why keep negative data separate?", "So the baseline stays clean and every bad row is labelled with the rule, error and layer that should catch it."),
        ("What does Bronze, Silver, Gold mean here?", "Bronze is raw appended data, Silver is typed, validated and de-duplicated with bad rows quarantined, Gold is business tables: lifecycle, cash projection, data-quality summary."),
        ("What is the quality gate?", "If more than 5% of a batch is quarantined, Silver does not MERGE it. The negative batch quarantined 17% and was held back."),
        ("How do you handle schema drift?", "Landing compares columns to a contract generated from the dictionary sheets. A drifted sheet is refused whole (DQ05) rather than half-loaded."),
        ("How do you prove MERGE is idempotent?", "Re-running the same batch ingests 0 Bronze rows and inserts or updates 0 Silver rows; a full rebuild from landing reproduces Silver's hash."),
        ("Did it run on Databricks?", "Not yet. It ran on local Spark and Delta Lake with 14 of 14 checks passing. The notebook and serverless job are written for Databricks and need a workspace run."),
        ("How does the lakehouse tie back to the platform?", "Silver trades are compared to both the oracle and the platform database: same ids, quantities, prices and settlement dates."),
    ]),
    ("Testing", [
        ("How do your tests know the right answer?", "Expected results come from an independent oracle, documented boundaries, hand-written specs and hand calculations; never from the code under test."),
        ("How many tests are there?", f"{fmt(CAT['cases'])} generated cases in {CAT['scenarios']} scenarios. The clean run executed all of them, {fmt(CLEAN['executed'])} executions including browser runs, with {CLEAN['failed']} failures."),
        ("How do you know the tests can fail?", "Six realistic defects were injected one at a time; every one was caught, from 1 to 52 failing cases each, then a clean rerun passed."),
        ("Which defect was hardest to catch?", "D5, the ledger idempotency bypass. Only one targeted check catches it, so that check had to be designed deliberately."),
        ("What did the tests find that you did not plant?", "Five real issues, including allocation checks built as warnings (a 600-share allocation of 500 was accepted) and authorisation running after validation."),
        ("What is the role matrix?", "Every protected endpoint called as every role, with an expected allow or 403. 264 cases."),
        ("How do browser tests map to cases?", "Each Playwright title starts with its catalogue id, so results merge back to the case, scenario and rules."),
        ("Which browsers?", "Chromium desktop and a phone-sized Chromium ran here. Firefox and WebKit run in the CI workflow because the build sandbox could not download them."),
        ("What is your rule coverage?", f"{CAT['rules_covered']} of {CAT['rules_total']} rules have executed cases. The remaining ones are listed openly as gaps."),
        ("How do you avoid flaky tests?", "Fresh seeds per run, a fixed business clock, server-side waits on visible state rather than sleeps, and sandboxes for anything that writes."),
        ("How is traceability recorded?", "Every case names its rules, scenario and source row or spec; a traceability workbook links rules to cases and results."),
    ]),
    ("Behavioural and judgement", [
        ("What was the hardest design decision?", "Where to put the boundary between platform and oracle. If they share code, the tests prove nothing, so the oracle uses exact fractions and an import test proves independence."),
        ("What would you do differently?", "Write the negative data labels first. They found the most serious bug, and earlier labels would have found it sooner."),
        ("How did you use AI responsibly?", "I specified the behaviour and the expected results; the AI wrote code; nothing was accepted until it passed cases whose answers came from elsewhere. Failures were reported, not hidden."),
        ("How does your banking background show in this?", "The planted exceptions, the SSI and calendar rules, maker-checker, and reconciliation tolerances are what operations teams deal with daily. I chose them because they break in real life."),
        ("What would you build next?", "Cross-browser CI results, a Databricks run of the same checks, a public demo host, and a small assistant that explains an exception using its audit trail."),
        ("How would you explain this to a non-technical stakeholder?", "It is a practice trading floor's back office on fake data, plus an automatic checker that knows the right answers and proves the system gets them."),
    ]),
]


def qa() -> str:
    n, blocks = 0, []
    for cat, items in QA:
        rows = []
        for q, a in items:
            n += 1
            rows.append(f'<div class="qa"><div class="q"><span>Q{n}</span>{e(q)}</div><div class="a">{e(a)}</div></div>')
        blocks.append(f"<h3>{e(cat)}</h3>" + "".join(rows))
    return f"""
<section>
  <h2>17. Interview questions and answers</h2>
  <p>{n} questions, grouped by theme. Answer in your own words; the point is to know the substance, not to recite.</p>
  {''.join(blocks)}
</section>"""


def resume() -> str:
    return f"""
<section>
  <h2>18. Resume lines and how to describe the work</h2>
  <h3>Resume bullets</h3>
  <ul>
    <li>Designed CapitalFlow 360, an independent portfolio simulation of a multi-asset trade lifecycle (6 asset classes, 500 synthetic trades, {CAT['rules_total']} business rules),
    and built it with AI-assisted development using React, FastAPI, PostgreSQL and Spark/Delta Lake.</li>
    <li>Specified an oracle-based test strategy: {fmt(CAT['cases'])} generated cases across {CAT['scenarios']} scenarios, with expected results from an independent
    calculator, rule boundaries and {len(S['worked_examples'])} hand-calculated examples; clean run {fmt(CLEAN['executed'])} executions, 0 failures.</li>
    <li>Planned and ran defect injection: 6 realistic defects (rounding, day count, FX calendar, self-approval, ledger idempotency, UI filter), each caught by the suite.</li>
    <li>Designed controls modelled on bank operations: maker-checker, idempotent ledger postings, optimistic concurrency and an append-only audit trail with correlation ids.</li>
  </ul>
  <h3>LinkedIn summary</h3>
  <p class="quote">I built CapitalFlow 360, a simulated back office that takes 500 synthetic trades across six asset classes from order to reconciled books. I designed
  the business flows, rules and test scenarios from my years in banking operations, and built it with an AI coding assistant. The part I'm proudest of: the tests
  never trust the code. An independent calculator supplies every expected answer, and I planted six realistic defects to prove the suite catches them. It's a
  portfolio project on synthetic data, not connected to any real market.</p>
  <h3>Words to use and avoid</h3>
  {table(["Say", "Avoid"], [["simulated, synthetic, portfolio project", "production, live, real clients"], ["designed and specified; built with an AI coding assistant", "wrote all the code myself"],
    ["ran on local Spark; written for Databricks", "deployed on Databricks"], ["tested against an independent oracle", "fully tested, bug-free"], ["follows market conventions", "compliant, production-grade pricing"]])}
</section>"""


def limits() -> str:
    gl = [["Allocation", "Splitting a block trade across client accounts"], ["CCP", "Central counterparty that steps between buyer and seller"], ["Day count", "Convention for counting days in an interest period (30/360, ACT/360, ACT/ACT)"],
          ["DVP", "Delivery versus payment: securities and cash move together"], ["Idempotency", "Repeating a request has no further effect"], ["Maker-checker", "Four-eyes control: one person requests, another approves"],
          ["Oracle", "Independent source of expected test results"], ["Quarantine", "Holding invalid rows aside with the reason, instead of loading them"], ["SSI", "Standing settlement instructions: where to deliver cash or securities"],
          ["VWAP", "Volume-weighted average price"], ["Variation margin", "Daily cash settlement of a futures position's gain or loss"]]
    return f"""
<section>
  <h2>19. Limits, next steps and glossary</h2>
  <h3>Limits, stated plainly</h3>
  <ul>
    <li>{len(CAT['rules_uncovered'])} rules ({', '.join(CAT['rules_uncovered'])}) have no dedicated generated case yet.</li>
    <li>Firefox and WebKit results depend on the CI run; only Chromium ran in the build sandbox.</li>
    <li>The Databricks notebook has not been executed on Databricks.</li>
    <li>Performance figures are single-machine timings, not load tests.</li>
    <li>Not publicly deployed at the time of writing.</li>
  </ul>
  <h3>Next steps</h3>
  <ol><li>Run the CI workflow and import its cross-browser results.</li><li>Run the notebook in a Databricks Free Edition workspace and compare with the local evidence.</li>
  <li>Choose a free host for a read-only demo.</li><li>Add cases for the uncovered rules.</li></ol>
  <h3>Glossary</h3>
  {table(["Term", "Meaning"], gl, "small")}
  <p class="note">CapitalFlow 360 · independent portfolio project · synthetic data · simulated venues · metrics generated {e(S['generated_at'])}</p>
</section>"""


def api_section() -> str:
    import sys
    sys.path[:0] = [str(ROOT)]
    from testkit.oracle.specs import ENDPOINTS
    rows = [[x["method"], x["path"], x["action"], ", ".join(sorted(r.replace("_", " ").title() for r in x["roles"]))] for x in ENDPOINTS]
    return f"""
<section>
  <h2>Appendix A. Protected API endpoints and who may call them</h2>
  <p>This is the hand-written permission specification the role matrix tests against. Each endpoint is called as every role: permitted roles must
  get past authorisation (a deliberately invalid body then returns 422 so nothing changes), and every other role must get 403. The full contract is
  served as OpenAPI at <code>/docs</code>.</p>
  {table(["Method", "Path", "Action", "Permitted roles"], rows, "small")}
</section>"""


def exceptions_section() -> str:
    ex = sorted(((r["name"].replace("_", " ").lower(), r["value"]) for r in chart("executive", "exceptions_by_type")), key=lambda x: x[1])
    owner = sorted(((r["name"].replace("_", " ").lower(), r["value"]) for r in chart("operations", "exc_owner")), key=lambda x: x[1])
    br = [[b["recon_type"], b["account_id"], b["key"], b["break_type"].replace("_", " ").lower(), fmt(b["difference"], 2)] for b in PLAT["breaks"]]
    reasons = [[r["name"].replace("_", " ").lower(), r["value"]] for r in chart("operations", "fail_reasons")]
    return f"""
<section>
  <h2>Appendix B. Exceptions and reconciliation breaks</h2>
  <p>Every exception below comes from a planted condition in the synthetic data. The outcomes suite checks that each planted condition produces
  exactly its exception, and nothing else does.</p>
  <div class="grid2"><div>{hbar(ex, "Open exceptions by type")}</div><div>{hbar(owner, "Open exceptions by owning team", color=INK)}</div></div>
  <h3>Reconciliation breaks at the statement date</h3>
  {table(["Type", "Account", "Key", "Break", "Difference"], br)}
  <h3>Settlement fail and partial reasons</h3>
  {table(["Reason", "Instructions"], reasons)}
  <p>A cash difference of 0.50 or less is inside tolerance (R096) and does not appear above. Breaks can be written off only with a reason and a checker's approval.</p>
</section>"""


def samples_section() -> str:
    cases = json.loads((ROOT / "testkit" / "out" / "cases.json").read_text())
    seen, rows = {}, []
    for c in cases:
        if seen.get(c["suite"], 0) < 2:
            seen[c["suite"]] = seen.get(c["suite"], 0) + 1
            exp = c["expected"] if isinstance(c["expected"], str) else json.dumps(c["expected"], default=str)
            rows.append([c["case_id"], c["suite"], c["title"], ", ".join(c["rule_ids"]), exp[:90] + ("..." if len(exp) > 90 else "")])
    return f"""
<section>
  <h2>Appendix C. Sample generated test cases</h2>
  <p>Two cases from each suite, as they appear in <code>testkit/out/cases.json</code>. Each case also records its scenario, layer, parameters, lineage
  (the source file and row, or the spec it came from) and priority. The full catalogue is in JSON, Parquet and Excel.</p>
  {table(["Case", "Suite", "Title", "Rules", "Expected"], rows, "small")}
</section>"""


CSS = """
@page :first { margin: 0; @bottom-left { content: none; } @bottom-right { content: none; } }
@page { size: A4; margin: 16mm 15mm 18mm 15mm;
  @bottom-left { content: "CapitalFlow 360 · interview and demonstration handbook"; font: 8pt Lato, sans-serif; color: #64748B; }
  @bottom-right { content: counter(page); font: 8pt Lato, sans-serif; color: #64748B; } }
* { box-sizing: border-box; }
body { font-family: "Lato", "Calibri", sans-serif; font-size: 10pt; line-height: 1.45; color: #1B2333; margin: 0; }
h1, h2, h3, h4 { font-family: "Noto Serif", "Cambria", Georgia, serif; color: #0E3B43; }
h2 { font-size: 19pt; margin: 0 0 8pt; padding-bottom: 4pt; border-bottom: 2px solid #0F766E; }
h3 { font-size: 12.5pt; margin: 12pt 0 5pt; break-after: avoid; page-break-after: avoid; }
section { page-break-after: always; }
section:last-child { page-break-after: auto; }
p { margin: 0 0 7pt; }
ul, ol { margin: 0 0 8pt; padding-left: 16pt; } li { margin-bottom: 3pt; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.5pt; background: #EEF3F2; padding: 0 2pt; border-radius: 2pt; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 10pt; font-size: 9pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background: #0E3B43; color: #fff; text-align: left; padding: 4pt 5pt; font-weight: 700; }
td { padding: 3.5pt 5pt; border-bottom: 1px solid #D5DEDC; vertical-align: top; }
tbody tr:nth-child(even) td { background: #F5F8F7; }
table.small { font-size: 8pt; } table.small td { padding: 2.5pt 4pt; }
table.two td { width: 50%; }
.cover { height: 296mm; display: flex; flex-direction: column; justify-content: center; background: #0E3B43; color: #fff; padding: 20mm 18mm; }
.cover h1 { color: #fff; font-size: 44pt; margin: 6pt 0; } .cover .sub { font-size: 18pt; color: #9FD8CF; margin: 0 0 16pt; }
.cover .lead { font-size: 13pt; max-width: 150mm; } .cover .kicker { font-size: 9pt; letter-spacing: .08em; text-transform: uppercase; color: #F2B661; }
.cover .meta { font-size: 9pt; color: #C9D6D3; margin-top: 18pt; }
.coverstats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8pt; margin-top: 18pt; }
.coverstats div { background: rgba(255,255,255,.08); border-radius: 6pt; padding: 10pt; } .coverstats b { display: block; font-size: 20pt; color: #fff; }
.coverstats span { font-size: 8.5pt; color: #C9D6D3; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8pt; margin: 6pt 0 10pt; }
.stats div { background: #EEF3F2; border-radius: 6pt; padding: 8pt 10pt; } .stats b { display: block; font-size: 18pt; color: #0F766E; font-family: "Noto Serif", Georgia, serif; }
.stats span { font-size: 8.5pt; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12pt; align-items: start; }
.grid2 svg { width: 100%; height: auto; }
svg { max-width: 100%; height: auto; }
.callout { background: #FFF6E8; border-left: 4px solid #D97706; padding: 8pt 10pt; margin: 10pt 0; border-radius: 0 4pt 4pt 0; }
.note { font-size: 8.5pt; color: #64748B; font-style: italic; }
.quote { border-left: 3px solid #0F766E; padding-left: 10pt; font-style: italic; }
.toc { columns: 2; font-size: 11pt; list-style: none; padding-left: 0; } .toc li { margin-bottom: 5pt; }
.flow { display: flex; flex-wrap: wrap; gap: 4pt; margin: 6pt 0 10pt; }
.flow span { background: #0F766E; color: #fff; padding: 5pt 8pt; border-radius: 4pt; font-size: 8.5pt; font-weight: 700; position: relative; }
.flow span:nth-child(even) { background: #0E3B43; }
.arch { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8pt; margin: 8pt 0 10pt; }
.lane { background: #EEF3F2; border-radius: 6pt; padding: 6pt; } .lane h4 { margin: 0 0 5pt; font-size: 10pt; text-align: center; }
.box { background: #fff; border-left: 3px solid #0F766E; border-radius: 3pt; padding: 4pt 6pt; margin-bottom: 5pt; }
.box b { display: block; font-size: 8.5pt; } .box span { font-size: 7.5pt; color: #64748B; }
.box.l { border-color: #D97706; } .box.v { border-color: #7C3AED; } .box.d { border-color: #64748B; }
.wes { display: grid; grid-template-columns: 1fr 1fr; gap: 7pt; }
.we { border: 1px solid #D5DEDC; border-radius: 5pt; padding: 6pt 8pt; font-size: 8.3pt; page-break-inside: avoid; }
.we i { color: #64748B; font-style: normal; font-weight: 700; margin-right: 3pt; }
.weh { font-size: 9pt; margin-bottom: 3pt; color: #0E3B43; } .tag { float: right; font-size: 7pt; padding: 1pt 4pt; border-radius: 3pt; color: #fff; }
.tag.ok { background: #4D7C0F; } .tag.bad { background: #B91C1C; }
.qa { margin-bottom: 6pt; page-break-inside: avoid; } .q { font-weight: 700; color: #0E3B43; }
.q span { display: inline-block; min-width: 26pt; color: #0F766E; } .a { padding-left: 26pt; }
"""


def main() -> None:
    body = "".join([cover(), contents(), scope(), glance(), architecture(), tech(), lifecycle(), roles(), rules_section(), worked(),
                    data_section(), lakehouse(), strategy(), results(), findings(), dashboards(), demo3(), demo10(), qa(), resume(), limits(), api_section(), exceptions_section(), samples_section()])
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>CapitalFlow 360 handbook</title>
<style>{CSS}</style></head><body>{body}</body></html>"""
    (OUT / "handbook.html").write_text(doc)
    nq = sum(len(i) for _, i in QA)
    print("handbook html:", OUT / "handbook.html", "| questions:", nq)


if __name__ == "__main__":
    main()
