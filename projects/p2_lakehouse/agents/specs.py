"""The three agents: what each is for, its instructions, its tools, and its offline playbook.

* Ask Ledgerline     plain-English questions -> SQL -> answer and chart
* AML Investigator   works one alert end to end and recommends ESCALATE or CLOSE
* Data Steward       investigates a blocked delivery and drafts the incident ticket

The system prompts are what Claude gets. The ``*_policy`` generators are the
offline planner's playbooks: the same tools, called in a fixed order, with
rule-based conclusions. They exist so the app works without an API key and so the
tests can run the whole agent loop for free.
"""

from __future__ import annotations

import re

from .engine import AgentContext, AgentSpec
from .tools import alert_window

HOUSE_RULES = """House rules:
- Use only facts returned by your tools. Never invent transaction ids, amounts, dates or people.
- You recommend; a human decides. Nothing you submit is actioned until a person approves it.
- All data is synthetic, for the fictional Ledgerline Bank. Customers are identified by id only.
- Be concise and specific. Quote numbers with units (USD, rows, bytes)."""

AML = AgentSpec(
    name="aml_investigator",
    title="AML Investigator",
    role="Works an AML alert end to end and recommends ESCALATE or CLOSE, with a cited case narrative.",
    system=f"""You are an AML investigation assistant in the financial-crime team of Ledgerline Bank.
Rule-based monitoring raised an alert. Investigate it the way a good level-1 investigator would:
1. Read the alert and the transactions that triggered it.
2. Understand the customer: segment, KYC risk rating and its history, PEP flag, accounts.
3. Look at the customer's wider activity around the alert date and compare it with peers in the same segment.
4. Check whether counterparties are shared with other customers (a network), and test the obvious legitimate explanations (for example a recurring business cycle with the same counterparties)
   as hard as the suspicious ones (for example repeated sub-threshold cash, new high-risk counterparties).
5. Decide: ESCALATE (refer for a suspicious activity report decision) or CLOSE (activity explained). Give a
   risk_score from 0 to 100; 50 or more means ESCALATE.
6. Call submit_case once, with a 120-250 word narrative that cites the transaction ids you relied on.

{HOUSE_RULES}""",
    tools=["get_alert", "get_transactions", "get_customer_profile", "get_customer_activity", "compare_to_peers",
           "find_counterparty_history", "counterparty_network", "run_sql"],
    submit_tool="submit_case",
)

STEWARD = AgentSpec(
    name="data_steward",
    title="Data Steward",
    role="Investigates a delivery the pipeline blocked, finds the root cause and drafts the ticket for the data owner.",
    system=f"""You are the on-call data steward for the Ledgerline Bank lakehouse.
A blocking data-quality control stopped a delivery from loading. Investigate it:
1. Find the failed step and the control that blocked it.
2. Inspect the delivered files against their manifest (sizes, checksums, line counts, last line).
3. Compare with previous healthy deliveries.
4. State the root cause with numbers: rows expected, complete rows received, rows missing.
5. State the downstream impact precisely: what did and did not change in Bronze, Silver and Gold, and what
   that means for the reports people use (are they wrong, or just not yet updated?).
6. Propose remediation. Never propose editing or back-filling data by hand; the fix must come from the source.
7. Draft a ticket for the source system's team, then call submit_incident_report once.

{HOUSE_RULES}""",
    tools=["get_pipeline_status", "get_dq_findings", "inspect_delivery", "get_delivery_history", "run_sql"],
    submit_tool="submit_incident_report",
)

ASK = AgentSpec(
    name="ask_ledgerline",
    title="Ask Ledgerline",
    role="Answers plain-English questions about the bank's data by writing and running SQL.",
    system=f"""You are Ask Ledgerline, a data analyst for Ledgerline Bank's lakehouse.
Answer the user's question from the data:
1. If you are unsure of table or column names, call list_tables first.
2. Write ONE read-only SQL query and run it with run_sql. If it errors, read the error, fix the query and retry.
3. Answer in two to four sentences with the key numbers. Suggest a chart if one helps (bar or line), using
   column names from your final query.
4. If the data cannot answer the question, say so plainly. Do not guess.
5. Call submit_answer once, including the final SQL.

{HOUSE_RULES}""",
    tools=["list_tables", "run_sql"],
    submit_tool="submit_answer",
    max_tool_calls=8,
)

SPECS = {s.name: s for s in (ASK, AML, STEWARD)}


# ----------------------------------------------------------------- AML playbook
def _usd(x) -> str:
    return f"{float(x or 0):,.2f} USD"


def aml_policy(ctx: AgentContext):
    alert_id = ctx.context["alert_id"]
    yield ("think", "Start from the alert: which rule fired, on which customer, and which transactions triggered it.")
    alert = yield ("tool", "get_alert", {"alert_id": alert_id})
    if "error" in alert:
        return
    cid, rule, ids = alert["customer_id"], alert["rule_code"], alert["txn_ids"]
    yield ("think", f"{rule} on {cid}. Pull the {len(ids)} triggering transactions to check amounts, channels and "
                    "counterparties.")
    txns = (yield ("tool", "get_transactions", {"txn_ids": ids}))["transactions"]
    yield ("think", "Who is the customer? Segment, KYC rating and its history, PEP flag, accounts.")
    prof = yield ("tool", "get_customer_profile", {"customer_id": cid})
    p = prof["profile"]
    start, end = alert_window(alert["alert_date"])
    yield ("think", f"Look at everything the customer did from {start} to {end}, not just the flagged items.")
    activity = yield ("tool", "get_customer_activity", {"customer_id": cid, "start_date": start, "end_date": end})
    yield ("think", f"Is this unusual for a {p['segment']} customer? Compare with peers.")
    peers = yield ("tool", "compare_to_peers", {"customer_id": cid})

    flags, mitigating, evidence, score = [], [], list(ids), 0
    if rule == "STRUCTURING":
        score = 65
        flags.append(f"{len(txns)} cash deposits on {str(alert['alert_date'])[:10]}, each between 9,000 and 9,999.99 "
                     f"USD, totalling {_usd(alert['amount_usd'])}: just under the 10,000 USD reporting threshold")
        yield ("think", "Structuring is about a pattern. Has the customer made sub-threshold cash deposits on other "
                        "days too?")
        days = yield ("tool", "run_sql", {"query": (
            "SELECT txn_date, COUNT(*) AS deposits, ROUND(SUM(amount_usd), 2) AS total_usd FROM transactions "
            f"WHERE customer_id = '{cid}' AND txn_type = 'CASH_DEPOSIT' AND amount_usd BETWEEN 9000 AND 9999.99 "
            "GROUP BY txn_date ORDER BY txn_date")})
        if days.get("row_count", 0) > 1:
            score += 15
            flags.append(f"The pattern repeats: sub-threshold cash deposits on {days['row_count']} separate days")
        if all(t["channel"] == "BRANCH" for t in txns):
            flags.append("All deposits made in branch, in cash, on the same day")
        cash = peers["metrics"]["cash_usd_30d"]
        if cash["percentile"] >= 90:
            score += 5
            flags.append(f"30-day cash of {_usd(cash['customer'])} is above the {p['segment']} 90th percentile "
                         f"({_usd(cash['segment_p90'])})")
    elif rule == "HIGH_RISK_JURISDICTION":
        score = 60
        t = txns[0]
        flags.append(f"{t['txn_type']} of {_usd(t['amount_usd'])} with {t['counterparty_name']} in "
                     f"{t['counterparty_country']}, a HIGH risk jurisdiction")
        yield ("think", f"Is {t['counterparty_name']} a known, recurring counterparty, or new?")
        hist = yield ("tool", "find_counterparty_history", {"customer_id": cid,
                                                            "counterparty_name": t["counterparty_name"]})
        if hist["count"] <= 1:
            score += 10
            flags.append("First and only payment to this counterparty")
        else:
            mitigating.append(f"{hist['count']} payments with this counterparty in the data")
        yield ("think", f"Does anyone else at the bank pay {t['counterparty_name']}? Several unrelated customers "
                        "paying one offshore party is a network red flag.")
        network = yield ("tool", "counterparty_network", {"counterparty_name": t["counterparty_name"]})
        others = [c for c in network["customers"] if c["customer_id"] != cid]
        if others:
            score += 10
            flags.append(f"{len(others)} other customer(s) also paid {t['counterparty_name']}: "
                         + ", ".join(f"{c['customer_id']} ({_usd(c['total_usd'])})" for c in others[:5]))
        out = peers["metrics"]["outflow_usd_30d"]
        if out["percentile"] >= 90:
            score += 5
            flags.append(f"30-day outflow of {_usd(out['customer'])} is above the {p['segment']} 90th percentile")
    elif rule == "RAPID_MOVEMENT":
        wire_in = next(t for t in txns if t["txn_type"] == "WIRE_IN")
        wires_out = [t for t in txns if t["txn_type"] == "WIRE_OUT"]
        out_total = sum(float(t["amount_usd"]) for t in wires_out)
        ratio = out_total / float(wire_in["amount_usd"])
        flags.append(f"{_usd(wire_in['amount_usd'])} received from {wire_in['counterparty_name']} and "
                     f"{ratio:.0%} of it ({_usd(out_total)}) wired out within 48 hours")
        yield ("think", "Pass-through can be layering, or a normal business cycle. Check whether the same payer and "
                        "payee appear before.")
        prior_in = yield ("tool", "find_counterparty_history", {"customer_id": cid,
                                                                "counterparty_name": wire_in["counterparty_name"]})
        prior_out = yield ("tool", "find_counterparty_history", {"customer_id": cid,
                                                                 "counterparty_name": wires_out[0]["counterparty_name"]})
        earlier_in = [t for t in prior_in["transactions"] if t["txn_id"] != wire_in["txn_id"]]
        earlier_out = [t for t in prior_out["transactions"] if t["txn_id"] not in {w["txn_id"] for w in wires_out}]
        domestic = all(t["counterparty_risk_tier"] != "HIGH" and t["counterparty_country"] == p["country"]
                       for t in txns)
        established = (earlier_in and earlier_out and p["segment"] in ("CORPORATE", "SME")
                       and p["kyc_risk_rating"] == "LOW" and domestic)
        if established:
            score = 25
            e_in, e_out = earlier_in[-1], earlier_out[-1]
            evidence += [e_in["txn_id"], e_out["txn_id"]]
            mitigating += [
                f"Same cycle a month earlier: {_usd(e_in['amount_usd'])} in from {wire_in['counterparty_name']} on "
                f"{str(e_in['txn_ts'])[:10]} ({e_in['txn_id']}), {_usd(e_out['amount_usd'])} out to "
                f"{wires_out[0]['counterparty_name']} on {str(e_out['txn_ts'])[:10]} ({e_out['txn_id']})",
                f"{p['segment']} customer with a LOW KYC rating; both counterparties are domestic and standard risk",
                f"The outbound payee ({wires_out[0]['counterparty_name']}) is consistent with payroll; the September "
                "cycle stayed just under the 50,000 USD rule threshold, which is why only October alerted",
            ]
        else:
            score = 65
            if not earlier_in:
                flags.append("No earlier payments from this payer")
            if not domestic:
                flags.append("At least one leg involves a foreign or higher-risk counterparty")
    if p.get("kyc_risk_rating") == "HIGH" or str(p.get("pep_flag")).lower() == "true":
        score += 10
        flags.append(f"Customer KYC rating {p.get('kyc_risk_rating')}, PEP flag {p.get('pep_flag')}")
    else:
        mitigating.append(f"KYC risk rating {p.get('kyc_risk_rating')}, not a PEP")
    if len(prof["kyc_history"]) > 1:
        mitigating.append("KYC profile changed during the period; see the history rows")
    score = max(0, min(100, score))
    rec = "ESCALATE" if score >= 50 else "CLOSE"
    totals = ", ".join(f"{r['txn_type']} {r['direction'].lower()} {r['txns']} ({_usd(r['total_usd'])})"
                       for r in activity["by_type"][:4])
    headline = (f"{'Escalate' if rec == 'ESCALATE' else 'Close'}: {rule.replace('_', ' ').lower()} alert on {cid} "
                f"({p['segment']}, {p['kyc_risk_rating']} risk), score {score}/100.")
    narrative = (
        f"Alert {alert['alert_id'][:12]} ({rule}) was raised on {str(alert['alert_date'])[:10]} for customer {cid}, "
        f"a {p['segment']} customer in {p['country']} with a {p['kyc_risk_rating']} KYC rating. The triggering "
        f"transactions were {', '.join(ids)}. "
        + " ".join(f"{f}." for f in flags)
        + f" Activity from {start} to {end}: {totals or 'none'}. "
        + (" ".join(f"{m}." for m in mitigating) + " " if mitigating else "")
        + ("On balance the activity is unexplained and fits the typology, so I recommend escalation for a "
           "suspicious activity report decision." if rec == "ESCALATE" else
           "On balance the activity is explained by an established, documented business cycle, so I recommend "
           "closing the alert with this rationale and reviewing the rule threshold for this pattern.")
    )
    next_steps = (["Investigator to review the case and decide on a suspicious activity report",
                   "Request source of funds / business rationale from the relationship manager",
                   "Place the customer on enhanced monitoring pending the decision"] if rec == "ESCALATE" else
                  ["Close the alert with this rationale (investigator sign-off)",
                   "Record the payroll cycle in the customer's expected-activity profile",
                   "Raise a rule-tuning request: exclude verified recurring payroll cycles from RAPID_MOVEMENT"])
    yield ("think", f"Score {score}/100, so the recommendation is {rec}. Write the case up for the investigator.")
    yield ("tool", "submit_case", {"alert_id": alert["alert_id"], "recommendation": rec, "risk_score": score,
                                   "headline": headline, "narrative": narrative, "red_flags": flags,
                                   "mitigating_factors": mitigating, "evidence_txn_ids": evidence,
                                   "next_steps": next_steps})


# ------------------------------------------------------------ steward playbook
def steward_policy(ctx: AgentContext):
    yield ("think", "Check the latest pipeline run for failed steps.")
    status = yield ("tool", "get_pipeline_status", {})
    incident = status.get("incident") or {}
    batch = incident.get("batch_id") or (status["failed_steps"][0]["batch_id"] if status["failed_steps"] else None)
    if not batch:
        return
    yield ("think", f"Batch {batch} was blocked. Which control failed, and by how much?")
    findings = yield ("tool", "get_dq_findings", {"batch_id": batch})
    yield ("think", "A count mismatch at Bronze means the files differ from what the source promised. Inspect the "
                    "files against the manifest.")
    ev = yield ("tool", "inspect_delivery", {"batch_id": batch})
    yield ("think", "Compare with earlier deliveries to see what a healthy batch looks like.")
    history = yield ("tool", "get_delivery_history", {})

    txn = ev["files"]["transactions.jsonl"]
    expected = ev["manifest_record_counts"]["transactions"]
    complete = txn["lines"] - (0 if txn.get("last_line_is_valid_json", True) else 1)
    missing = expected - complete
    short = txn["expected_bytes"] - txn["actual_bytes"]
    truncated = (not txn["checksum_matches"]) and short > 0 and not txn.get("last_line_is_valid_json", True)
    category = "TRUNCATED_FILE" if truncated else "OTHER"
    others_ok = [n for n, f in ev["files"].items() if f.get("checksum_matches")]
    healthy = [d for d in history["deliveries"] if d["checksum_matches"]]
    failing = findings["failing"][0] if findings["failing"] else {}
    root = (f"transactions.jsonl in batch {batch} is {txn['actual_bytes']:,} bytes against {txn['expected_bytes']:,} "
            f"promised by the manifest ({short:,} bytes, {short / txn['expected_bytes']:.1%}, missing) and its "
            f"SHA-256 does not match. The file ends mid-record (the last line is not valid JSON), so the transfer "
            f"stopped part-way: {complete:,} complete records of {expected:,} arrived and {missing:,} are missing.")
    versions_before = incident.get("versions_before", {})
    versions_after = incident.get("versions_after", incident.get("versions_now", {}))
    unchanged = [t for t in ("bronze.transactions", "silver.transactions", "gold.daily_transaction_summary")
                 if t in versions_after and versions_before.get(t) == versions_after.get(t)]
    landed = incident.get("rows_landed_in_bronze", {})
    impact = (f"The Bronze record-count control blocked the load, so no transaction from batch {batch} reached any "
              f"table: {', '.join(unchanged) or 'Bronze, Silver and Gold transaction tables'} are unchanged (same "
              "Delta versions before and after). Reports are not wrong; they are one batch out of date (they stop at "
              f"the previous delivery). "
              + (f"Note: the batch's customers ({landed.get('customers', 0)} rows) and accounts "
                 f"({landed.get('accounts', 0)} rows) files were valid and did land in Bronze before the transactions "
                 "file was blocked; Silver has not consumed them." if landed.get("customers") else ""))
    yield ("think", f"Root cause: {category}. {missing:,} records missing. Draft the ticket for the source team.")
    yield ("tool", "submit_incident_report", {
        "batch_id": batch,
        "severity": "SEV2",
        "root_cause_category": category,
        "root_cause": root,
        "rows_expected": expected,
        "rows_received_complete": complete,
        "rows_missing": missing,
        "downstream_impact": impact,
        "evidence": [
            f"Control {failing.get('rule_name', 'bronze_record_count_matches_manifest')}: "
            f"{failing.get('description', '')}".strip(),
            f"File size {txn['actual_bytes']:,} vs manifest {txn['expected_bytes']:,} bytes; checksum mismatch",
            f"Last line incomplete: ...{txn['last_line_tail'][-50:]}",
            f"Other files in the batch match their checksums: {', '.join(others_ok)}",
            f"{len(healthy)} earlier deliveries matched their manifests exactly",
        ],
        "remediation": [
            f"Source team to re-send batch {batch} in full with the same manifest; verify SHA-256 before loading",
            f"Re-run Bronze, then Silver and Gold for batch {batch}; the load is idempotent, so the customers and "
            "accounts already in Bronze are skipped, not duplicated",
            "Do not patch or back-fill the missing records by hand",
            "Pipeline improvement: check file size and SHA-256 against the manifest before any table is written, so "
            "a partial batch never lands in Bronze",
        ],
        "ticket": {
            "assignee_team": "LEDGERLINE_CORE file transfer (source system owner)",
            "title": f"Batch {batch} transactions file truncated in transfer: {missing:,} of {expected:,} records "
                     "missing, please re-send",
            "body": (f"The {ev['source_system']} delivery for business date {ev['business_date']} (batch {batch}) "
                     f"was blocked by the lakehouse's Bronze control. transactions.jsonl arrived with "
                     f"{txn['actual_bytes']:,} of {txn['expected_bytes']:,} bytes and ends mid-record; "
                     f"{complete:,} of {expected:,} records are complete. Please re-send the batch with its manifest. "
                     "No data has been published from this batch; downstream reports are one batch behind until "
                     "it is re-delivered."),
        },
    })


# ------------------------------------------------------------- Ask playbook
ASK_INTENTS = [
    {"title": "Top customers by cross-border volume", "keywords": {"cross", "border", "international", "foreign"},
     "sql": "SELECT customer_id, segment, kyc_risk_rating, cross_border_usd_30d FROM customer_360 "
            "ORDER BY cross_border_usd_30d DESC LIMIT 10", "chart": {"type": "bar", "x": "customer_id",
                                                                    "y": "cross_border_usd_30d"}},
    {"title": "Cash deposits just under the 10,000 USD threshold", "keywords": {"cash", "threshold", "10000", "10k",
                                                                               "structuring", "deposit", "deposits"},
     "sql": "SELECT customer_id, txn_date, COUNT(*) AS deposits, ROUND(SUM(amount_usd), 2) AS total_usd "
            "FROM transactions WHERE txn_type = 'CASH_DEPOSIT' AND amount_usd BETWEEN 9000 AND 9999.99 "
            "GROUP BY customer_id, txn_date ORDER BY deposits DESC, total_usd DESC",
     "chart": {"type": "bar", "x": "customer_id", "y": "total_usd"}},
    {"title": "Monthly volume by segment", "keywords": {"segment", "segments", "monthly", "month", "volume"},
     "sql": "SELECT month, segment, txn_count, volume_usd FROM segment_kpis ORDER BY month, volume_usd DESC",
     "chart": {"type": "bar", "x": "segment", "y": "volume_usd"}},
    {"title": "Why records were quarantined", "keywords": {"quarantine", "quarantined", "rejected", "bad",
                                                           "quality", "dq"},
     "sql": "SELECT _dq_failed_rules AS rule, COUNT(*) AS records FROM quarantine GROUP BY _dq_failed_rules "
            "ORDER BY records DESC", "chart": {"type": "bar", "x": "rule", "y": "records"}},
    {"title": "Customers whose KYC risk rating changed", "keywords": {"kyc", "rating", "changed", "change",
                                                                      "history", "scd"},
     "sql": "SELECT customer_id, kyc_risk_rating, effective_from, effective_to, is_current FROM customer_history "
            "ORDER BY customer_id, effective_from", "chart": {"type": "none", "x": "", "y": ""}},
    {"title": "Transactions with high-risk jurisdictions", "keywords": {"high", "risk", "jurisdiction",
                                                                        "jurisdictions", "country", "countries"},
     "sql": "SELECT counterparty_country, COUNT(*) AS txns, ROUND(SUM(amount_usd), 2) AS total_usd FROM transactions "
            "WHERE counterparty_risk_tier = 'HIGH' GROUP BY counterparty_country ORDER BY total_usd DESC",
     "chart": {"type": "bar", "x": "counterparty_country", "y": "total_usd"}},
    {"title": "Daily transaction volume", "keywords": {"daily", "day", "trend", "per", "over", "time"},
     "sql": "SELECT txn_date, SUM(txn_count) AS txns, ROUND(SUM(total_amount_usd), 2) AS total_usd "
            "FROM daily_summary GROUP BY txn_date ORDER BY txn_date", "chart": {"type": "line", "x": "txn_date",
                                                                              "y": "total_usd"}},
    {"title": "AML alerts by rule", "keywords": {"alert", "alerts", "aml", "suspicious"},
     "sql": "SELECT rule_code, priority, COUNT(*) AS alerts, ROUND(SUM(amount_usd), 2) AS amount_usd "
            "FROM aml_alerts GROUP BY rule_code, priority ORDER BY alerts DESC",
     "chart": {"type": "bar", "x": "rule_code", "y": "alerts"}},
    {"title": "Largest wires", "keywords": {"largest", "biggest", "wire", "wires", "top"},
     "sql": "SELECT txn_id, customer_id, txn_type, amount_usd, counterparty_country FROM transactions "
            "WHERE txn_type IN ('WIRE_IN', 'WIRE_OUT') ORDER BY amount_usd DESC LIMIT 10",
     "chart": {"type": "bar", "x": "txn_id", "y": "amount_usd"}},
    {"title": "Volume by channel", "keywords": {"channel", "channels", "branch", "online", "mobile", "atm"},
     "sql": "SELECT channel, COUNT(*) AS txns, ROUND(SUM(amount_usd), 2) AS total_usd FROM transactions "
            "GROUP BY channel ORDER BY total_usd DESC", "chart": {"type": "bar", "x": "channel", "y": "total_usd"}},
]


def match_intent(question: str) -> dict | None:
    words = set(re.findall(r"[a-z0-9]+", question.lower()))
    scored = sorted(((len(words & i["keywords"]), n) for n, i in enumerate(ASK_INTENTS)), reverse=True)
    best, idx = scored[0]
    return ASK_INTENTS[idx] if best >= 1 else None


def _describe(rows: list[dict], total: int) -> str:
    if not rows:
        return "The query returned no rows."
    first = ", ".join(f"{k} {v}" for k, v in rows[0].items())
    return f"{total} row{'s' if total != 1 else ''}. First row: {first}."


def ask_policy(ctx: AgentContext):
    question = ctx.context.get("question") or ctx.goal
    intent = match_intent(question)
    if intent is None:
        yield ("think", "No playbook matches this question. The offline planner only knows a fixed set of question "
                        "types; a live LLM is needed for anything else.")
        yield ("tool", "submit_answer", {
            "answer": "In offline mode I can answer these kinds of question: "
                      + "; ".join(i["title"].lower() for i in ASK_INTENTS)
                      + ". Switch on live mode (Claude) to ask anything else.",
            "sql": "", "chart": {"type": "none", "x": "", "y": ""}})
        return
    sql = intent["sql"]
    top_n = re.search(r"\b(?:top\s+)?(\d{1,3})\s+(?:customers|accounts|counterpart|rules|countries)", question, re.I)
    if top_n and re.search(r"\bLIMIT \d+$", sql):
        sql = re.sub(r"\bLIMIT \d+$", f"LIMIT {int(top_n.group(1))}", sql)  # "which 5 customers" -> LIMIT 5
    yield ("think", f"This matches the playbook '{intent['title']}'. Run its query.")
    result = yield ("tool", "run_sql", {"query": sql})
    answer = f"{intent['title']}: {_describe(result.get('rows', []), result.get('row_count', 0))}"
    yield ("tool", "submit_answer", {"answer": answer, "sql": sql, "chart": intent["chart"]})


POLICIES = {"aml_investigator": aml_policy, "data_steward": steward_policy, "ask_ledgerline": ask_policy}


def goal_for(agent: str, **kw) -> str:
    if agent == "aml_investigator":
        return f"Investigate AML alert {kw['alert_id']} and recommend ESCALATE or CLOSE."
    if agent == "data_steward":
        return ("The overnight pipeline run blocked a delivery. Investigate the incident, find the root cause and "
                "prepare the incident report and the ticket for the data owner.")
    return kw["question"]
