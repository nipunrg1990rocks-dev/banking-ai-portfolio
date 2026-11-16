// Builds the four CapitalFlow 360 decks from reporting/out/snapshot.json (one snapshot, so every number agrees).
//   node reporting/decks.js            (after: python reporting/build_snapshot.py)
const path = require("path");
const fs = require("fs");
const pptxgen = require("pptxgenjs");
const { applyTheme } = require("./vendor/apply_theme.js");

const OUT = path.join(__dirname, "out");
const ROOT = path.join(__dirname, "..");
const S = JSON.parse(fs.readFileSync(path.join(OUT, "snapshot.json"), "utf8"));
const THEME = {
  name: "CapitalFlow", headFontFace: "Cambria", bodyFontFace: "Calibri",
  colors: { dk1: "1B2333", lt1: "FFFFFF", dk2: "0E3B43", lt2: "EEF3F2", accent1: "0F766E", accent2: "D97706", accent3: "64748B",
            accent4: "B91C1C", accent5: "4D7C0F", accent6: "7C3AED", hlink: "0F766E", folHlink: "7C3AED" },
};
const HEX = THEME.colors;
const FOOT = "CapitalFlow 360 · independent portfolio project · synthetic data, simulated venues";

// ------------------------------------------------------------------------------ snapshot helpers
const D = S.dashboards;
const kpi = (dash, id) => (D[dash].kpis.find((k) => k.id === id) || {}).value;
const chart = (dash, id) => (D[dash].charts.find((c) => c.id === id) || { data: [] }).data;
const run = (id) => S.runs[id];
const clean = run("CLEAN-001");
const ci = Object.values(S.ci || {}).filter((r) => !r.failed && r.browser_runs).sort((a, b) => b.browser_runs - a.browser_runs)[0];
const dep = S.deployment;
const hostText = dep ? `Live on ${dep.host_short}: ${dep.url.replace("https://", "")}` : "Vercel + Neon free tiers (no card); not live yet";
const ciBrowsers = ci ? `${ci.browser_runs} runs in GitHub Actions run ${ci.run_id} on Chromium, Firefox and WebKit, 0 failed` : "Firefox and WebKit set up for CI, results pending";
const fmt = (n, dp = 0) => Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const DEFECTS = [
  ["D1", "Allocation residual cent dropped"], ["D2", "30/360 bonds accrue on ACT/360"], ["D3", "FX spot ignores quote-currency calendar"],
  ["D4", "Maker can approve own request"], ["D5", "Ledger idempotency key bypassed"], ["D6", "Blotter ignores currency filter"],
];

// ------------------------------------------------------------------------------ deck scaffolding
function newDeck(title) {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5 in
  pres.title = title;
  pres.author = "Nipun Gupta (design) with AI-assisted implementation";
  pres.company = "Independent portfolio project";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  const C = pres.SchemeColor;
  pres.defineSlideMaster({
    title: "TITLE", background: { color: C.text2 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.2, w: 11.7, h: 1.5, fontSize: 44, bold: true, color: C.background1, valign: "bottom" }, text: "" } },
      { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 3.9, w: 11.7, h: 1.4, fontSize: 20, color: C.background2, valign: "top" }, text: "" } },
      { text: { text: FOOT, options: { x: 0.8, y: 6.7, w: 11.7, h: 0.4, fontSize: 11, color: C.background2 } } },
    ],
  });
  pres.defineSlideMaster({
    title: "SECTION", background: { color: C.accent1 },
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.6, w: 11.7, h: 1.2, fontSize: 40, bold: true, color: C.background1 }, text: "" } },
      { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 3.9, w: 11.7, h: 1.0, fontSize: 18, color: C.background1 }, text: "" } },
    ],
    slideNumber: { x: 12.3, y: 6.9, w: 0.6, h: 0.35, fontSize: 10, color: C.background1 },
  });
  pres.defineSlideMaster({
    title: "CONTENT", background: { color: C.background1 },
    margin: [0.5, 0.6, 0.7, 0.6],
    objects: [
      { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.1, h: 0.9, fontSize: 30, bold: true, color: C.text2, valign: "middle" }, text: "" } },
      { text: { text: FOOT, options: { x: 0.6, y: 6.95, w: 10.5, h: 0.35, fontSize: 10, color: C.accent3 } } },
    ],
    slideNumber: { x: 12.3, y: 6.95, w: 0.6, h: 0.35, fontSize: 10, color: C.accent3 },
  });
  return { pres, C };
}

function ensureSection(d, sec) {
  d.sections = d.sections || new Set();
  if (!d.sections.has(sec)) { d.pres.addSection({ title: sec }); d.sections.add(sec); }
}

function content(d, sec, title, notes) {
  ensureSection(d, sec);
  const s = d.pres.addSlide({ masterName: "CONTENT", sectionTitle: sec });
  s.addText(title, { placeholder: "title" });
  if (notes) s.addNotes(notes);
  return s;
}

function section(d, sec, title, sub, notes) {
  ensureSection(d, sec);
  const s = d.pres.addSlide({ masterName: "SECTION", sectionTitle: sec });
  s.addText(title, { placeholder: "title" });
  if (sub) s.addText(sub, { placeholder: "body" });
  if (notes) s.addNotes(notes);
  return s;
}

function titleSlide(d, title, sub, notes) {
  ensureSection(d, "Opening");
  const s = d.pres.addSlide({ masterName: "TITLE", sectionTitle: "Opening" });
  s.addText(title, { placeholder: "title" });
  s.addText(sub, { placeholder: "body" });
  if (notes) s.addNotes(notes);
  return s;
}

// stat cards: [{v, l}]
function stats(d, s, items, y = 1.5, h = 1.55) {
  const C = d.C;
  const gap = 0.3, w = (12.1 - gap * (items.length - 1)) / items.length;
  items.forEach((it, i) => {
    const x = 0.6 + i * (w + gap);
    s.addShape(d.pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: C.background2 }, line: { color: C.background2 }, rectRadius: 0.08, objectName: `stat-${i}` });
    s.addText(String(it.v), { x: x + 0.15, y: y + 0.12, w: w - 0.3, h: h * 0.55, fontSize: items.length > 4 ? 30 : 36, bold: true, color: it.c || C.accent1, isTextBox: true, margin: 0, fontFace: THEME.headFontFace });
    s.addText(it.l, { x: x + 0.15, y: y + h * 0.62, w: w - 0.3, h: h * 0.34, fontSize: 12, color: C.text1, isTextBox: true, margin: 0, valign: "top" });
  });
}

// cards grid: [{h, t}] with columns
function cards(d, s, items, { x = 0.6, y = 1.5, w = 12.1, cols = 3, h = 1.6, gap = 0.3, size = 14 } = {}) {
  const C = d.C;
  const cw = (w - gap * (cols - 1)) / cols;
  items.forEach((it, i) => {
    const cx = x + (i % cols) * (cw + gap), cy = y + Math.floor(i / cols) * (h + gap);
    s.addShape(d.pres.shapes.ROUNDED_RECTANGLE, { x: cx, y: cy, w: cw, h, fill: { color: it.fill || C.background2 }, line: { color: it.fill || C.background2 }, rectRadius: 0.06, objectName: `card-${i}` });
    s.addShape(d.pres.shapes.OVAL, { x: cx + 0.2, y: cy + 0.2, w: 0.42, h: 0.42, fill: { color: it.dot || C.accent1 }, line: { color: it.dot || C.accent1 }, objectName: `dot-${i}` });
    s.addText(it.n || String(i + 1), { x: cx + 0.2, y: cy + 0.2, w: 0.42, h: 0.42, fontSize: 12, bold: true, color: C.background1, align: "center", valign: "middle", margin: 0, isTextBox: true });
    s.addText(it.h, { x: cx + 0.75, y: cy + 0.16, w: cw - 0.95, h: 0.5, fontSize: size + 2, bold: true, color: C.text2, valign: "middle", margin: 0, isTextBox: true });
    s.addText(it.t, { x: cx + 0.2, y: cy + 0.75, w: cw - 0.4, h: h - 0.9, fontSize: size, color: C.text1, valign: "top", margin: 0, isTextBox: true });
  });
}

// horizontal process flow
function flow(d, s, steps, { y = 1.7, h = 1.1, size = 13 } = {}) {
  const C = d.C;
  const gap = 0.35, w = (12.1 - gap * (steps.length - 1)) / steps.length;
  steps.forEach((st, i) => {
    const x = 0.6 + i * (w + gap);
    s.addShape(d.pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: i % 2 ? C.text2 : C.accent1 }, line: { color: i % 2 ? C.text2 : C.accent1 }, rectRadius: 0.08, objectName: `step-${i}` });
    s.addText(st, { x: x + 0.08, y, w: w - 0.16, h, fontSize: size, bold: true, color: C.background1, align: "center", valign: "middle", margin: 0, isTextBox: true });
    if (i < steps.length - 1) s.addShape(d.pres.shapes.RIGHT_ARROW, { x: x + w + 0.05, y: y + h / 2 - 0.14, w: gap - 0.1, h: 0.28, fill: { color: C.accent2 }, line: { color: C.accent2 }, objectName: `arrow-${i}` });
  });
}

function bullets(d, s, lines, { x = 0.6, y = 1.5, w = 6, h = 4.8, size = 16 } = {}) {
  s.addText(lines.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < lines.length - 1, paraSpaceAfter: 8 } })),
    { x, y, w, h, fontSize: size, color: d.C.text1, valign: "top", isTextBox: true });
}

function note(d, s, text, { x = 0.6, y = 6.25, w = 12.1, h = 0.55 } = {}) {
  s.addText(text, { x, y, w, h, fontSize: 12, italic: true, color: d.C.accent3, valign: "middle", isTextBox: true, margin: 0 });
}

const AX = { catAxisLabelColor: HEX.accent3, valAxisLabelColor: HEX.accent3, catAxisLabelFontSize: 11, valAxisLabelFontSize: 10,
  catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt", dataLabelFontFace: "+mn-lt", titleFontFace: "+mn-lt", legendFontFace: "+mn-lt",
  dataLabelFontSize: 10, dataLabelColor: HEX.dk1, titleFontSize: 14, titleColor: HEX.dk2, legendFontSize: 11 };
function bar(d, s, title, rows, o = {}) {
  const opts = { x: 0.6, y: 1.5, w: 6.5, h: 4.7, barDir: "bar", showTitle: true, title, showValue: true, dataLabelPosition: "outEnd",
    chartColors: [HEX.accent1], valGridLine: { color: "E2E8F0", size: 0.5 }, catGridLine: { style: "none" }, showLegend: false,
    dataLabelFormatCode: o.fmt || "#,##0", valAxisLabelFormatCode: o.fmt || "#,##0", ...AX, ...o };
  delete opts.fmt;
  s.addChart(d.pres.charts.BAR, [{ name: title, labels: rows.map((r) => r[0]), values: rows.map((r) => r[1]) }], opts);
}
function doughnut(d, s, title, rows, o = {}) {
  s.addChart(d.pres.charts.DOUGHNUT, [{ name: title, labels: rows.map((r) => r[0]), values: rows.map((r) => r[1]) }], {
    x: 0.6, y: 1.5, w: 6, h: 4.7, showTitle: true, title, showPercent: false, showValue: true, holeSize: 55, showLegend: true, legendPos: "r",
    chartColors: [HEX.accent1, HEX.accent2, HEX.dk2, HEX.accent3, HEX.accent5, HEX.accent6, HEX.accent4], ...AX, dataLabelColor: "FFFFFF", ...o });
}
function stacked(d, s, title, labels, series, o = {}) {
  s.addChart(d.pres.charts.BAR, series.map(([n, v]) => ({ name: n, labels, values: v })), {
    x: 0.6, y: 1.5, w: 12.1, h: 4.6, barDir: "col", barGrouping: "stacked", showTitle: true, title, showLegend: true, legendPos: "b",
    chartColors: [HEX.accent1, HEX.accent2, HEX.accent4], valGridLine: { color: "E2E8F0", size: 0.5 }, catGridLine: { style: "none" }, ...AX, ...o });
}
function table(d, s, head, rows, { x = 0.6, y = 1.5, w = 12.1, colW, size = 12 } = {}) {
  const C = d.C;
  const hdr = head.map((h) => ({ text: h, options: { bold: true, color: C.background1, fill: { color: C.text2 } } }));
  s.addTable([hdr, ...rows.map((r, i) => r.map((c) => ({ text: String(c), options: { fill: { color: i % 2 ? C.background1 : C.background2 } } })))],
    { x, y, w, colW, fontSize: size, color: C.text1, border: { type: "solid", pt: 0.5, color: "D5DEDC" }, valign: "middle", margin: 0.06 });
}

async function save(d, file) {
  const p = path.join(OUT, file);
  await d.pres.writeFile({ fileName: p });
  await applyTheme(p, THEME);
  console.log("wrote", p);
}

// ==================================================================================== 1. executive summary
async function executive() {
  const d = newDeck("CapitalFlow 360: executive summary");
  const groups = Object.entries(S.data.by_group);
  titleSlide(d, "CapitalFlow 360", "A simulated multi-asset trade lifecycle platform, tested against an independent oracle\nExecutive summary · as of " + D.executive.as_of,
    "Two-minute opener: what this is, and that it is a portfolio simulation on synthetic data, not a production system.");

  let s = content(d, "Summary", "One platform takes 500 trades from order to settled cash", "Headline numbers come straight from the platform at the statement date.");
  stats(d, s, [
    { v: fmt(kpi("executive", "trades")), l: "baseline trades across 6 asset classes" },
    { v: kpi("executive", "settle_rate") + "%", l: "of due settlements settled" },
    { v: kpi("executive", "auto_match") + "%", l: "confirmations matched automatically" },
    { v: fmt(clean.unique_cases_executed), l: "test cases executed, 0 failures" },
  ]);
  cards(d, s, [
    { h: "What it does", t: "Orders, fills, booking, allocation, matching, clearing, settlement, ledger, reconciliation and exceptions, behind 7 role workspaces." },
    { h: "How it is proven", t: `Every expected result comes from an independent oracle or a documented rule boundary, never from the code under test.` },
    { h: "What it is not", t: "No real venues, custodians or payments, no production pricing and no regulatory claim. Synthetic data only." },
  ], { y: 3.4, h: 2.2 });

  s = content(d, "Summary", "How a trade moves through the platform", "Walk the main path once; each box is a screen in the app.");
  flow(d, s, ["Order and fill", "Book and validate", "Allocate", "Confirm and match", "Clear", "Settle and post", "Reconcile"], { y: 1.7, h: 1.2, size: 13 });
  cards(d, s, [
    { h: "Seven role workspaces", t: "Trader, middle office, settlements, reconciliation, risk, checker and admin each see their own queues and actions." },
    { h: "Controls built in", t: "Maker-checker approvals, idempotent posting, version checks and an append-only audit trail with correlation ids." },
    { h: "Simulated outside world", t: "Venues, CCP, custodians and counterparties are simulators fed from synthetic files. No real connectivity." },
  ], { y: 3.3, h: 2.3, size: 13 });

  s = content(d, "Summary", "The book covers six asset classes in deliberate proportions", "Split fixed by the brief: 140/120/80/70/50/40.");
  bar(d, s, "Baseline trades by asset class", groups, { w: 7.2 });
  cards(d, s, [
    { h: "Cash products", t: "Equities, ETFs and bonds settle delivery-versus-payment on market calendars." },
    { h: "FX and derivatives", t: "FX spot and forwards, futures with daily margin, options with exercise, swaps with resets." },
    { h: "Financing", t: "Repo, reverse repo and securities lending with collateral and fees." },
  ], { x: 8.1, w: 4.6, cols: 1, h: 1.45, gap: 0.2, size: 13 });

  s = content(d, "Operations", "Settlement runs clean, and the exceptions are the planted ones", "Fails and breaks are seeded on purpose and labelled in the oracle.");
  const ist = S.platform.instruction_status;
  doughnut(d, s, "Settlement instructions by status", [["Settled", ist.SETTLED || 0], ["Not yet due", ist.PENDING || 0], ["Failed", ist.FAILED || 0], ["Partial", ist.PARTIAL || 0]],
    { w: 8.2, chartColors: [HEX.accent1, HEX.accent3, HEX.accent4, HEX.accent2] });
  // right column stats
  [["Open exceptions", kpi("executive", "open_exceptions")], ["Recon breaks", kpi("executive", "breaks")], ["On-time settlement", kpi("executive", "on_time") + "%"]].forEach(([l, v], i) => {
    s.addShape(d.pres.shapes.ROUNDED_RECTANGLE, { x: 9.1, y: 1.5 + i * 1.55, w: 3.6, h: 1.35, fill: { color: d.C.background2 }, line: { color: d.C.background2 }, rectRadius: 0.08, objectName: `side-${i}` });
    s.addText(String(v), { x: 9.3, y: 1.6 + i * 1.55, w: 3.2, h: 0.7, fontSize: 30, bold: true, color: d.C.accent1, margin: 0, isTextBox: true, fontFace: THEME.headFontFace });
    s.addText(l, { x: 9.3, y: 2.3 + i * 1.55, w: 3.2, h: 0.4, fontSize: 12, color: d.C.text1, margin: 0, isTextBox: true });
  });

  s = content(d, "Controls", "Six injected defects, six caught, then a clean rerun", "Each defect switched on alone against a freshly seeded database.");
  table(d, s, ["Defect", "What it breaks", "Cases failed"], DEFECTS.map(([id, t]) => [id, t, run(`DEFECT-${id}`).failed]), { w: 7.0, colW: [1.0, 4.6, 1.4], size: 13 });
  bar(d, s, "Failed cases per injected defect", DEFECTS.map(([id]) => [id, run(`DEFECT-${id}`).failed]), { x: 7.9, w: 4.8, h: 4.4, chartColors: [HEX.accent2] });
  note(d, s, `Clean rerun CLEAN-001: ${fmt(clean.executed)} executions, ${fmt(clean.failed)} failures.`);

  s = content(d, "Controls", "Testing found real bugs before any defect was planted", "Honest story for interviews: the suite earned its keep.");
  cards(d, s, [
    { h: "Allocation checks ignored", t: "A 600-share allocation of a 500-share block was accepted: three checks were built as warnings by mistake. Found by 10 labelled negative cases.", dot: d.C.accent4 },
    { h: "Authorisation after validation", t: "Non-checkers got 422 instead of 403 on approvals. Found by the 264-case role matrix.", dot: d.C.accent4 },
    { h: "Calendar and test data", t: "A Japanese holiday rule and two negative-data generators were wrong. Found by a hand-calculated example and labelled cases.", dot: d.C.accent2 },
  ], { h: 2.6 });
  note(d, s, "Every fix is in the repository with the case that found it (testkit/FINDINGS.md).", { y: 4.6 });

  s = content(d, "Scope", "Built and verified locally; cloud steps listed honestly", "Do not overclaim: say exactly what ran where.");
  table(d, s, ["Item", "Status"], [
    ["Platform, rules, ledger, 7 dashboards", "Built and running locally (FastAPI + PostgreSQL + React)"],
    ["Lakehouse Bronze/Silver/Gold", `Ran on local Spark ${S.lakehouse.spark} + Delta ${S.lakehouse.delta}: ${S.lakehouse.passed}/${S.lakehouse.passed + S.lakehouse.failed} checks pass`],
    ["Databricks notebook and job", "Written for serverless; not yet run on Databricks"],
    ["Browser tests", ciBrowsers],
    ["Public hosting", hostText],
  ], { colW: [4.6, 7.5], size: 14 });
  cards(d, s, [
    { h: "Domain and design (Nipun Gupta)", t: "Business flows, product scope, the rule set and its boundaries, scenarios, the defect plan and acceptance criteria." },
    { h: "AI-assisted implementation", t: "Code, generators, tests and reports written with an AI coding assistant, then run, checked and corrected against the oracle." },
  ], { y: 4.55, cols: 2, h: 1.55, size: 13 });

  s = content(d, "Close", "Next steps", "Close on actions.");
  flow(d, s, ["Run the Databricks notebook", "Cover the 5 untested rules", "Keep the demo live", "Share the showcase"], { y: 1.7, h: 1.2, size: 16 });
  cards(d, s, [
    { h: "Review and merge", t: "CI already runs the tests and the browser suite on Chromium, Firefox and WebKit on every push." },
    { h: "Databricks", t: "Import the notebook to a Free Edition workspace and run the same 14 checks." },
    { h: "Hosting", t: "Vercel Hobby for the app and Neon Free for the database: free tiers, no card." },
    { h: "Showcase", t: "Portfolio page, PDF handbook and decks for interviews." },
  ], { y: 3.3, cols: 4, h: 2.3, size: 13 });
  note(d, s, "Domain design, rules and scenarios: Nipun Gupta. Implementation: AI-assisted, reviewed and tested as described.", { y: 6.0, h: 0.6 });
  await save(d, "CF360_Executive_Summary.pptx");
}

// ==================================================================================== 2. business process walkthrough
async function business() {
  const d = newDeck("CapitalFlow 360: business process walkthrough");
  titleSlide(d, "Trade lifecycle walkthrough", "From order to reconciled books, one stage at a time\nFor operations and business analysis audiences");

  let s = content(d, "Overview", "Seven role workspaces share one golden source", "Each role sees its own queues; permissions are enforced on the server.");
  cards(d, s, [
    { h: "Trader", t: "Orders, fills, booking with pre-trade checks." }, { h: "Middle office", t: "Enrichment, matching, amendments, SSIs." },
    { h: "Settlements", t: "Instructions, fails, partials, retries." }, { h: "Reconciliation", t: "Position and cash breaks, write-offs." },
    { h: "Risk and control", t: "Limits, margin calls, exceptions." }, { h: "Checker", t: "Approves what others request (maker-checker)." },
  ], { h: 2.0, size: 15 });
  note(d, s, "Admin manages the clock, processing and imports. 9 demo users in total.", { y: 6.0 });

  s = content(d, "Overview", "Every trade walks one spine to its own ending");
  flow(d, s, ["Captured", "Enriched", "Allocated", "Confirmed", "Cleared", "Instructed", "Settled"], { y: 1.7, h: 1.0, size: 14 });
  table(d, s, ["Product", "Ends at", "Skips"], [
    ["Equities, ETFs", "Settled", "-"], ["Bonds, FX", "Settled", "Cleared (bilateral)"], ["Futures", "Open, then Expired or Closed", "Instructed (daily margin)"],
    ["Options", "Open, then Expired or Exercised", "-"], ["Swaps", "Open, then Matured", "Instructed"], ["Repo, sec lending", "Open, then Closed", "Cleared"],
  ], { y: 3.1, colW: [3.4, 4.6, 4.1], size: 14 });
  note(d, s, "Each edge is a server-side state transition; anything not listed is refused and audited.", { y: 6.0 });

  const fo = D.executive;
  s = content(d, "Front office", "Orders fill in clips and book at the volume-weighted price", "Worked example WE: 600 @ 71.00 and 300 @ 71.10.");
  cards(d, s, [
    { h: "Pre-trade checks", t: "Client active, KYC valid, product permitted, price within collar, limit headroom, before the order reaches the simulated venue." },
    { h: "Fills", t: "Partial fills accumulate. The order stays open until filled or the remainder is cancelled." },
    { h: "Booking", t: "Quantity = sum of fills. Price = VWAP to 6 dp: (600 × 71.00 + 300 × 71.10) / 900 = 71.033333." },
  ], { h: 3.3, size: 15 });
  note(d, s, "Rule R026 rejects a booking whose quantity or price disagrees with the fills.", { y: 5.2 });

  s = content(d, "Booking", "Booking runs 40+ rules and explains every rejection", "Rejections carry rule id, error code and field.");
  const fam = Object.entries(S.catalogue.rule_families).sort((a, b) => b[1] - a[1]);
  bar(d, s, "Rules by family", fam, { w: 6.6 });
  bullets(d, s, ["Rejected bookings are stored with their rule ids and payload", "Dry-run validation shows the server's settlement date and economics without writing anything",
    "Idempotency keys stop a double click from booking twice", `${S.catalogue.rules_total} rules in the catalogue; ${S.catalogue.rules_covered} have executed test cases`], { x: 7.5, w: 5.2, size: 15 });

  s = content(d, "Booking", "Allocations split the block to the cent", "Residual goes to the largest allocation, first on ties.");
  const we = S.worked_examples.find((w) => w.rules.includes("R025")) || S.worked_examples[0];
  cards(d, s, [
    { h: "Rule", t: "Allocation quantities must equal the block (R024). Amounts are pro-rata, rounded to the currency's minor unit (R025)." },
    { h: "Worked example " + we.id, t: we.title + ": " + we.working },
    { h: "Why it matters", t: `Dropping the residual cent (defect D1) made allocations stop summing to the block. ${run("DEFECT-D1").failed} cases failed.` },
  ], { h: 3.6, size: 15 });
  note(d, s, "The oracle repeats the split with exact fractions; platform and oracle must agree to the cent.", { y: 5.4 });

  s = content(d, "Matching", "Confirmations match within tolerance or raise a break", "Tolerance: 1.00 in the settlement currency, 100 for JPY.");
  bar(d, s, "Confirmation matching outcomes", chart("operations", "matching").map((r) => [r.name.replaceAll("_", " ").toLowerCase(), r.value]).reverse(), { w: 6.2 });
  cards(d, s, [
    { h: "Mismatch", t: "Amount, price or settlement date breaks open an exception for middle office." },
    { h: "Missing", t: "No confirmation by the cut-off raises a missing-confirmation exception." },
    { h: "Force match", t: "Needs a reason and a checker's approval." },
  ], { x: 7.0, w: 5.7, cols: 1, h: 1.35, gap: 0.2, size: 13 });

  s = content(d, "Clearing and settlement", "Instructions come from effective-dated SSIs");
  flow(d, s, ["Novation at CCP", "SSI lookup by date", "Instruction to custodian", "Settlement confirmation", "Ledger posting"], { y: 1.7, h: 1.2, size: 14 });
  bullets(d, s, ["An SSI that ended the day before settlement counts as missing (R080)", "Missing SSIs open an enrichment exception; an approved SSI request re-enriches stuck trades",
    "Interface messages are sequenced: duplicates ignored, gaps parked and released in order (R094)"], { y: 3.3, w: 12.1, h: 2.5, size: 16 });

  s = content(d, "Clearing and settlement", "Fails age, partials settle what is available");
  bar(d, s, "Fails and partials by age (business days)", chart("operations", "fail_ageing").map((r) => [r.name, r.value]), { w: 6.2, barDir: "col", dataLabelPosition: "outEnd" });
  const st = S.platform.instruction_status;
  [["Settled", st.SETTLED], ["Partial", st.PARTIAL], ["Failed", st.FAILED], ["Pending (not due)", st.PENDING]].forEach(([l, v], i) => {
    const x = 7.2 + (i % 2) * 2.8, y = 1.6 + Math.floor(i / 2) * 1.6;
    s.addShape(d.pres.shapes.ROUNDED_RECTANGLE, { x, y, w: 2.6, h: 1.4, fill: { color: d.C.background2 }, line: { color: d.C.background2 }, rectRadius: 0.08, objectName: `ins-${i}` });
    s.addText(String(v), { x: x + 0.15, y: y + 0.1, w: 2.3, h: 0.75, fontSize: 30, bold: true, color: d.C.accent1, margin: 0, isTextBox: true, fontFace: THEME.headFontFace });
    s.addText(l, { x: x + 0.15, y: y + 0.85, w: 2.3, h: 0.4, fontSize: 12, color: d.C.text1, margin: 0, isTextBox: true });
  });
  note(d, s, "Partial settlement needs at least one lot available (R085); otherwise the instruction fails.", { x: 7.2, y: 4.9, w: 5.5, h: 0.8 });

  s = content(d, "Books", "A double-entry ledger is the book of record", "Every posting balances per currency or instrument.");
  stats(d, s, [{ v: fmt(S.platform.ledger_rows), l: "ledger rows (cash and securities)" }, { v: fmt(S.platform.lifecycle_events), l: "lifecycle events" },
    { v: fmt(S.platform.audit_events), l: "append-only audit events" }]);
  cards(d, s, [
    { h: "Idempotent", t: "Each posting has an event key; replaying a day writes nothing new." },
    { h: "Reversible", t: "Cancelling a settled trade posts exact reversals that net to zero." },
    { h: "Tamper-evident", t: "The database blocks UPDATE and DELETE on audit events." },
  ], { y: 3.4, h: 1.7 });

  s = content(d, "Books", "Reconciliation finds exactly the planted breaks");
  table(d, s, ["Type", "Account", "Key", "Break", "Difference"], S.platform.breaks.map((b) => [b.recon_type, b.account_id, b.key, b.break_type.replaceAll("_", " ").toLowerCase(), fmt(b.difference, 2)]),
    { colW: [1.8, 1.8, 1.6, 3.3, 3.6], size: 13 });
  note(d, s, "A EUR difference of 0.50 or less is inside tolerance and is not a break (R096).", { y: 5.6 });

  s = content(d, "Exceptions", "Exceptions route to the team that owns the fix");
  bar(d, s, "Open exceptions by owner", chart("operations", "exc_owner").map((r) => [r.name.replaceAll("_", " ").toLowerCase(), r.value]), { w: 6.2 });
  bar(d, s, "Open exceptions by type", chart("executive", "exceptions_by_type").map((r) => [r.name.replaceAll("_", " ").toLowerCase(), r.value]), { x: 6.9, w: 5.8, chartColors: [HEX.dk2] });

  s = content(d, "Controls", "Maker-checker guards every economic change");
  flow(d, s, ["Maker requests", "Version check (If-Match)", "Checker approves", "New trade version", "Allocations re-cut"], { y: 1.7, h: 1.1, size: 14 });
  bullets(d, s, ["Non-economic changes (book, trader) apply directly as a new version", "The maker can never approve their own request; the attempt is audited (R092)",
    "Settled trades cannot be amended economically: cancel and rebook instead (R090)", "A stale version number is refused, so two people cannot overwrite each other (R098)"], { y: 3.2, w: 12.1, h: 3, size: 16 });

  s = content(d, "Products", "Each asset class brings its own economics");
  cards(d, s, [
    { h: "Equities", t: "Commission with minimum, exchange fee, UK stamp duty on buys." }, { h: "Bonds", t: "Accrued interest by day count, dirty price, ex-dividend gilts." },
    { h: "FX", t: "Spot and forward value dates on both currency calendars, points." }, { h: "Futures and options", t: "Initial and variation margin, premium, auto-exercise." },
    { h: "Swaps", t: "Fixed and floating schedules, resets, net payments." }, { h: "Financing", t: "Repo cash with haircut, interest, margin calls, lending fees." },
  ], { h: 2.1, size: 15 });

  s = content(d, "Close", "What a reviewer can try in five minutes");
  flow(d, s, ["Sign in as trader", "Book on a holiday: see R010", "Open a trade's lifecycle", "Approve as checker", "Check a recon break"], { y: 1.7, h: 1.3, size: 14 });
  cards(d, s, [
    { h: "Validate first", t: "Dry-run shows the derived settlement date and fees before anything is written." },
    { h: "Follow one trade", t: "The trade page shows its lifecycle path, versions, ledger postings and audit trail." },
    { h: "See the controls", t: "A checker can approve another user's amendment, never their own." },
  ], { y: 3.4, h: 2.2, size: 14 });
  note(d, s, "All data is synthetic; venues, CCPs, custodians and payments are simulated.", { y: 6.0 });
  await save(d, "CF360_Business_Process_Walkthrough.pptx");
}

// ==================================================================================== 3. test strategy and results
async function testing() {
  const d = newDeck("CapitalFlow 360: test strategy and results");
  const cat = S.catalogue;
  titleSlide(d, "Test strategy and results", `${fmt(cat.cases)} generated cases, ${cat.scenarios} scenarios, six injected defects\nRun CLEAN-001 and defect runs D1 to D6`);

  let s = content(d, "Strategy", "Expected results never come from the code under test");
  cards(d, s, [
    { h: "Independent oracle", t: "A separate Python oracle (exact fractions) recomputes economics, dates and outcomes; an import test proves it shares no code with the platform." },
    { h: "Documented boundaries", t: "Rule cases come from the catalogue's boundaries: 10.00% collar accepted, 10.01% rejected." },
    { h: "Hand calculations", t: `${S.worked_examples.length} worked examples are calculated by hand and must match both the platform and the oracle.` },
    { h: "Written specs", t: "Lifecycle edges and the role matrix are written out by hand in testkit/oracle/specs.py." },
  ], { cols: 2, h: 2.0 });

  s = content(d, "Strategy", "The catalogue spans eleven suites");
  bar(d, s, "Cases by suite", Object.entries(cat.by_suite).sort((a, b) => b[1] - a[1]), { w: 7.2 });
  cards(d, s, [
    { h: "Lineage", t: "Every case names its source file, row or spec." }, { h: "Traceability", t: `${cat.rules_covered} of ${cat.rules_total} rules have executed cases.` },
    { h: "Formats", t: "JSON, Parquet and Excel, with a traceability sheet." },
  ], { x: 8.1, w: 4.6, cols: 1, h: 1.4, gap: 0.2, size: 13 });

  s = content(d, "Strategy", "Every run starts clean and leaves evidence");
  flow(d, s, ["Fresh seed", "Generate cases", "Execute (API, rules, oracle)", "Playwright browsers", "Merge and import", "Dashboards and decks"], { y: 1.7, h: 1.2, size: 13 });
  cards(d, s, [
    { h: "Repeatable", t: "Each run drops the schema, migrates and reloads the 500-trade baseline before the first case." },
    { h: "Recorded", t: "Results go to testkit/out/runs as JSON and into the platform's Test runs page with run ids." },
    { h: "Read-only to the book", t: "Lifecycle cases run in sandboxes; dry-run bookings sit inside savepoints that roll back." },
  ], { y: 3.3, h: 2.3, size: 14 });

  s = content(d, "Results", "The clean run executed every catalogue case");
  stats(d, s, [{ v: fmt(clean.unique_cases_executed), l: `of ${fmt(clean.catalogue_cases)} catalogue cases executed` }, { v: fmt(clean.executed), l: "executions incl. browser runs" },
    { v: fmt(clean.failed), l: "failures", c: d.C.accent5 }, { v: clean.scenarios, l: "scenarios exercised" }]);
  const suites = Object.entries(clean.by_suite).map(([k, v]) => [k, fmt(v.passed), v.failed]);
  const half = Math.ceil(suites.length / 2);
  table(d, s, ["Suite", "Passed", "Failed"], suites.slice(0, half), { y: 3.35, w: 5.9, colW: [3.1, 1.4, 1.4], size: 13 });
  table(d, s, ["Suite", "Passed", "Failed"], suites.slice(half), { x: 6.8, y: 3.35, w: 5.9, colW: [3.1, 1.4, 1.4], size: 13 });

  s = content(d, "Results", ci ? "Browser coverage: three engines in CI, zero failures" : "Browser coverage: what ran here and what CI runs");
  const br = ci || clean;
  bar(d, s, `Browser runs in ${br.run_id}`, Object.entries(br.browser_runs_by_project), { w: 6.2, chartColors: [HEX.dk2] });
  bullets(d, s, ci ? [`${ci.browser_runs} Playwright runs in GitHub Actions run ${ci.run_id}, ${ci.browser_failed} failed`, "Desktop Chromium, Firefox and WebKit, plus phone-sized Chromium and WebKit",
    `Same run: ${fmt(ci.executed)} executions in total, ${ci.failed} failed`, `Locally only Chromium could be installed: ${clean.browser_runs} runs in CLEAN-001`] :
    [`${clean.browser_runs} Playwright runs here: Chromium desktop and a Pixel-sized phone`, "Firefox and WebKit could not be downloaded in the build sandbox (network policy)",
    "The CI workflow installs all three engines and runs the same specs, plus iPhone-sized WebKit", "Results from CI are imported the same way; none are claimed until they run"], { x: 7.1, w: 5.6, size: 15 });

  s = content(d, "Defects", "Each injected defect was caught by the cases designed for it");
  table(d, s, ["Defect", "Breaks", "Failed", "Caught by"], DEFECTS.map(([id, t]) => [id, t, run(`DEFECT-${id}`).failed,
    Object.entries(run(`DEFECT-${id}`).failed_by_prefix).map(([k, v]) => `${k} ${v}`).join(", ")]), { colW: [1.0, 4.3, 1.1, 5.7], size: 12 });
  s.addImage({ path: path.join(ROOT, "testkit", "out", "evidence", "DEFECT-D6-blotter-filter-chromium.png"), x: 0.6, y: 4.15, w: 4.45, h: 2.5, altText: "Blotter filtered to JPY still showing EUR trades while defect D6 is on" });
  bullets(d, s, ["Evidence from run DEFECT-D6: the blotter is filtered to JPY, yet EUR trades still show, and the banner says the defect is on",
    "One defect at a time, on a freshly seeded database each run", "After the six defect runs, CLEAN-001 reran everything with zero failures"], { x: 5.5, y: 4.15, w: 7.2, h: 2.5, size: 15 });

  s = content(d, "Defects", "Failure counts show how wide each defect reaches");
  bar(d, s, "Failed cases per defect run", DEFECTS.map(([id]) => [id, run(`DEFECT-${id}`).failed]), { w: 7.0, barDir: "col", chartColors: [HEX.accent2] });
  cards(d, s, [
    { h: "Wide", t: "Calculation defects (D2, D3) fail dozens of re-bookings and calendar cases." },
    { h: "Narrow", t: "Control defects (D4, D5) fail one or two targeted checks, so those checks must exist." },
  ], { x: 7.9, w: 4.8, cols: 1, h: 2.0, size: 13 });

  s = content(d, "Findings", "Real defects the suite found while it was being built");
  table(d, s, ["#", "Found by", "Problem", "Fix"], [
    ["F1", "Worked example WE12", "Japanese substitute holiday on bank closing days", "Calendar generator"],
    ["F2", "10 labelled negative cases", "Allocation and fill checks treated as warnings", "Booking + guard on severity"],
    ["F3", "Role matrix (264 cases)", "Approvals authorised after body validation", "Dependency before validation"],
    ["F4", "Negative cases N15, N19", "Two negative-data generators wrong", "Generator; labels kept"],
    ["F5", "First defect-run import", "Result ids longer than columns", "Migration 0003"],
  ], { colW: [0.6, 3.2, 4.8, 3.5], size: 13 });

  s = content(d, "Suites", "Negative data: every labelled case lands on its rule");
  const neg = clean.by_suite.negative;
  stats(d, s, [{ v: neg.passed, l: "labelled negative cases passing" }, { v: 25, l: "negative defect types (N01 to N25)" }, { v: `${S.lakehouse.quarantine.caught}/${S.lakehouse.quarantine.labelled}`, l: "rows labelled for quarantine caught by the lakehouse" }]);
  bullets(d, s, ["Negative files are kept apart from the 500-trade baseline", "Labels name the rule, error and layer: API reject, quarantine, late acceptance or enrichment exception",
    "The same file runs through both the API and the lakehouse, and each layer catches its own share"], { y: 3.4, w: 12.1, h: 2.6 });

  s = content(d, "Suites", "Hand calculations anchor the arithmetic");
  table(d, s, ["Id", "Example", "Expected"], S.worked_examples.slice(0, 12).map((w) => [w.id, w.title, Array.isArray(w.expected) ? w.expected.join(" / ") : String(w.expected)]), { colW: [1.1, 7.6, 3.4], size: 12 });
  note(d, s, `${S.worked_examples.filter((w) => w.pass).length} of ${S.worked_examples.length} examples pass on both engines; all are printed in the handbook.`, { y: 6.1 });

  s = content(d, "Suites", "The lakehouse is tested on Spark, not assumed");
  table(d, s, ["Check", "Result"], S.lakehouse.checks.map((c) => [c.check, c.result]), { colW: [10.6, 1.5], size: 11 });

  s = content(d, "Close", "Limits, stated plainly");
  cards(d, s, [
    { h: "Rule gaps", t: `${cat.rules_uncovered.length} rules (${cat.rules_uncovered.join(", ")}) have no dedicated generated case yet.`, dot: d.C.accent2 },
    { h: "Browsers", t: ci ? "Firefox and WebKit ran in GitHub Actions only; the build sandbox could install Chromium alone." : "Firefox and WebKit results depend on the CI run; only Chromium ran in the build sandbox.", dot: d.C.accent2 },
    { h: "Databricks", t: "The notebook and job are written but not yet executed on Databricks.", dot: d.C.accent2 },
    { h: "Performance", t: "Timings are local single-machine figures, not load tests.", dot: d.C.accent2 },
    { h: "Simulation", t: "Synthetic data and simulated venues; no production pricing or regulatory claim.", dot: d.C.accent2 },
    { h: "Oracle scope", t: "The oracle covers economics, dates and outcomes; screen layout is checked by browser tests only.", dot: d.C.accent2 },
  ], { h: 2.1, size: 14 });
  await save(d, "CF360_Test_Strategy_and_Results.pptx");
}

// ==================================================================================== 4. architecture and data
async function architecture() {
  const d = newDeck("CapitalFlow 360: architecture and data");
  titleSlide(d, "Architecture and data", "React, FastAPI and PostgreSQL for the platform; Spark and Delta Lake for the lakehouse");

  let s = content(d, "Architecture", "Four parts, one golden source");
  cards(d, s, [
    { h: "Synthetic data", t: "13 Excel workbooks, fixed seed, oracle and negative files kept apart." },
    { h: "Platform", t: "FastAPI with OpenAPI, rules engine, state machines, ledger, PostgreSQL 16." },
    { h: "Lakehouse", t: "Excel to Parquet landing, Bronze, Silver with quarantine, Gold." },
    { h: "Verification", t: "Oracle, case catalogue, executor, Playwright, reports." },
  ], { cols: 4, h: 2.4, size: 13 });
  flow(d, s, ["Excel", "API booking", "PostgreSQL", "React UI", "Reports"], { y: 4.4, h: 0.9, size: 14 });

  s = content(d, "Architecture", "Pinned, current versions throughout");
  table(d, s, ["Layer", "Technology"], [
    ["Frontend", "React 19.3, TypeScript 5.9, Vite 7.3, MUI 7.3, MUI X Data Grid 8.29, Recharts 3.10"],
    ["API", "Python 3.13, FastAPI 0.163, Pydantic 2.13, SQLAlchemy 2.0, Alembic 1.20, PostgreSQL 16"],
    ["Lakehouse", `PySpark ${S.lakehouse.spark}, Delta Lake ${S.lakehouse.delta}; Databricks serverless notebook and bundle`],
    ["Testing", "pytest 9, Hypothesis, Playwright 1.63 (TypeScript)"], ["Reports", "pptxgenjs 3.12 decks, HTML to PDF via Chromium"],
  ], { colW: [2.2, 9.9], size: 15 });
  cards(d, s, [
    { h: "Exact pins", t: "Every dependency is pinned in requirements files and package.json, so a rebuild gets the same versions." },
    { h: "Free to run", t: "All open source; the only cloud piece is an optional Databricks Free Edition run." },
  ], { y: 4.5, cols: 2, h: 1.5, size: 14 });

  s = content(d, "Platform", "API design choices that matter in operations");
  cards(d, s, [
    { h: "Idempotency-Key", t: "Same key and body returns the original result; a different body is refused 409." },
    { h: "If-Match versions", t: "Amendments must name the version they read; stale writes are refused." },
    { h: "One error envelope", t: "code, message, details and the request's correlation id, on every error." },
    { h: "Server-side state", t: "Transitions are checked by the server's state machine, whatever the caller." },
    { h: "Sandboxes", t: "Tests run whole lifecycles in isolated sandboxes that never touch the main book." },
    { h: "Append-only audit", t: "A database trigger blocks UPDATE and DELETE on audit events." },
  ], { h: 2.1, size: 14 });

  s = content(d, "Data", "Thirteen related workbooks describe one synthetic bank");
  table(d, s, ["Workbook", "Rows"], Object.entries(S.data.files).map(([f, v]) => [f.replace(".xlsx", ""), String(Object.values(v.rows).reduce((a, b) => a + b, 0))]), { w: 4.0, size: 11 });
  cards(d, s, [
    { h: "Fixed seed", t: `Seed ${S.data.seed}: the same files every time, with hashes in the manifest.` },
    { h: "Dictionary sheets", t: "Every workbook carries column types and nullability; the lakehouse contract is built from them." },
    { h: "Window", t: `Opening ${S.data.opening_date}, statement date ${S.data.statement_date}.` },
  ], { x: 5.0, w: 7.7, cols: 1, h: 1.45, gap: 0.2, size: 13 });

  s = content(d, "Lakehouse", "Medallion layers with a quality gate before every MERGE");
  flow(d, s, ["Excel", "Parquet landing", "Bronze (stream)", "Silver (gate, MERGE)", "Gold"], { y: 1.7, h: 1.1, size: 14 });
  bullets(d, s, ["Landing keeps every value as text; a sheet whose columns drift is refused whole (DQ05)", "Bronze appends with a streaming checkpoint, so a re-run ingests only new files",
    "Silver casts, checks required fields, references and domains, de-duplicates, then MERGEs if under 5% is quarantined", "Gold builds trade lifecycle, cash projection and a data-quality summary"], { y: 3.2, w: 12.1, h: 3, size: 15 });

  s = content(d, "Lakehouse", "Silver holds exactly the baseline, and replays change nothing");
  const sc = Object.entries(S.lakehouse.silver_counts).sort((a, b) => b[1] - a[1]).slice(0, 10);
  bar(d, s, "Silver rows by table (top 10)", sc, { w: 7.0 });
  cards(d, s, [
    { h: "Replay", t: "Re-running a batch ingests 0 rows and updates 0 rows." },
    { h: "Rebuild", t: "Rebuilding from landing gives an identical Silver hash." },
    { h: "Gate", t: `The negative batch quarantines ${S.lakehouse.negative_gate.rows_quarantined} of ${S.lakehouse.negative_gate.rows_in} rows (${Math.round(S.lakehouse.negative_gate.quarantine_ratio * 100)}%) and is held back.` },
  ], { x: 7.9, w: 4.8, cols: 1, h: 1.4, gap: 0.2, size: 13 });

  s = content(d, "Lakehouse", "Databricks assets are ready, not yet run there");
  cards(d, s, [
    { h: "Notebook", t: "00_run_lakehouse.py: same code, Unity Catalog tables, a volume for landing and checkpoints." },
    { h: "Job", t: "databricks.yml asset bundle, serverless compute, no cluster to manage." },
    { h: "Status", t: "Not executed on Databricks from the build environment. Needs a workspace run by the owner.", dot: d.C.accent2 },
  ], { h: 2.4 });
  flow(d, s, ["Import the repo", "Run 00_run_lakehouse", "Check the 14 results", "Compare with local run"], { y: 4.3, h: 1.1, size: 14 });
  note(d, s, "Same checks as the local Spark run, so the two results can be compared side by side.", { y: 5.8 });

  const eng = D.engineering;
  s = content(d, "Engineering", "End-of-day processing stays fast on a laptop-sized run");
  const eod = chart("engineering", "eod");
  s.addChart(d.pres.charts.LINE, [{ name: "EOD duration (ms)", labels: eod.map((r) => r.name.slice(5)), values: eod.map((r) => r.value) }], {
    x: 0.6, y: 1.5, w: 8.0, h: 4.6, showTitle: true, title: "End-of-day run time per business day (ms)", chartColors: [HEX.accent1], lineSize: 2, lineDataSymbol: "none",
    valGridLine: { color: "E2E8F0", size: 0.5 }, catGridLine: { style: "none" }, showLegend: false, ...AX });
  [["EOD p95", kpi("engineering", "eod_p95") + " ms"], ["API p95", kpi("engineering", "api_p95") + " ms"], ["EOD runs", kpi("engineering", "eod_runs")]].forEach(([l, v], i) => {
    s.addShape(d.pres.shapes.ROUNDED_RECTANGLE, { x: 9.1, y: 1.5 + i * 1.55, w: 3.6, h: 1.35, fill: { color: d.C.background2 }, line: { color: d.C.background2 }, rectRadius: 0.08, objectName: `eng-${i}` });
    s.addText(String(v), { x: 9.3, y: 1.6 + i * 1.55, w: 3.2, h: 0.7, fontSize: 28, bold: true, color: d.C.accent1, margin: 0, isTextBox: true, fontFace: THEME.headFontFace });
    s.addText(l, { x: 9.3, y: 2.3 + i * 1.55, w: 3.2, h: 0.4, fontSize: 12, color: d.C.text1, margin: 0, isTextBox: true });
  });
  note(d, s, "Single-machine timings from this run; not a load test.");

  s = content(d, "Delivery", "Delivery and what remains");
  const has = (p) => fs.existsSync(path.join(ROOT, p));
  const migs = fs.readdirSync(path.join(ROOT, "backend", "migrations", "versions")).filter((f) => f.endsWith(".py")).length;
  table(d, s, ["Item", "State"], [
    ["Alembic migrations", `${migs} revisions, used to build every database`],
    ["Docker Compose", has("docker-compose.yml") ? "docker-compose.yml: database, API with built UI, one command" : "Not yet added"],
    ["Continuous integration", has("../../.github/workflows/p7-capitalflow360.yml") ? "GitHub Actions: pytest, executor and Playwright on three engines" : "Not yet added"],
    ["Public hosting", hostText],
    ["Databricks run", "Owner runs the notebook in their workspace"]], { colW: [4, 8.1], size: 15 });
  cards(d, s, [
    { h: "Domain and design", t: "Nipun Gupta: product scope, lifecycle, rules, scenarios, defect plan and acceptance criteria." },
    { h: "Implementation", t: "AI-assisted code, tests and reports; the metrics in these decks are read from recorded runs." },
  ], { y: 4.4, cols: 2, h: 1.6, size: 14 });
  await save(d, "CF360_Architecture_and_Data.pptx");
}

(async () => {
  await executive();
  await business();
  await testing();
  await architecture();
})().catch((e) => { console.error(e); process.exit(1); });
