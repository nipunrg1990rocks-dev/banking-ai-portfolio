"""Investigation and resolution playbooks, one per exception category.

Each playbook is what an experienced operations analyst would do, written down:

* ``investigate`` calls read tools and turns their output into **findings**,
  each tied to the tool that produced it (the evidence an approver sees).
* ``plan`` proposes **one** next action with its arguments, a rationale, the
  manual steps around it, and whether it is *eligible* for straight-through
  processing. Eligibility is only a request: the control gate decides.

The playbooks are deterministic, so the same exception always gets the same
proposal and the evaluation can check them.
"""

from __future__ import annotations

from typing import Any

from .tools import Toolbox

Facts = dict[str, Any]


def _finding(tool: str, text: str, severity: str = "info") -> dict:
    return {"tool": tool, "text": text, "severity": severity}


def _plan(action: str, args: dict, summary: str, rationale: list[str], steps: list[str], *,
          auto_eligible: bool = False, auto_reason: str = "", confidence: str = "High",
          moves_money: bool = False) -> dict:
    return {"action": action, "action_args": args, "summary": summary, "rationale": [r for r in rationale if r],
            "steps": steps,
            "auto_eligible": auto_eligible, "auto_reason": auto_reason, "confidence": confidence,
            "moves_money": moves_money}


# --------------------------------------------------------------- payment repair
def investigate_payment_repair(exc: dict, tb: Toolbox) -> tuple[Facts, list[dict]]:
    p = tb.call("get_payment", payment_id=exc["refs"]["payment_id"])
    iban = tb.call("validate_iban", iban=p.get("beneficiary_iban")) if p.get("beneficiary_iban") else None
    bic = tb.call("lookup_bic", bic=p.get("beneficiary_bic"))
    ssi = tb.call("lookup_beneficiary_ssi", beneficiary=p["beneficiary"])
    f = []
    if iban is not None:
        f.append(_finding("validate_iban", f"IBAN on the instruction {p['beneficiary_iban']}: {iban['reason']}.",
                          "info" if iban["valid"] else "high"))
    where = f"is in the directory ({bic['institution']})." if bic["found"] else "is NOT in the directory."
    f.append(_finding("lookup_bic", f"BIC {p['beneficiary_bic']} {where}", "info" if bic["found"] else "high"))
    if ssi["found"]:
        f.append(_finding("lookup_beneficiary_ssi", f"Verified SSI on file for {p['beneficiary']} (verified "
                          f"{ssi['verified_on']}, call-back confirmed): IBAN {ssi['iban'] or ssi['account_no']}, "
                          f"BIC {ssi['bic']}."))
    else:
        f.append(_finding("lookup_beneficiary_ssi",
                          f"No verified SSI for '{p['beneficiary']}'. Closest name on file is '{ssi['closest_name']}' "
                          f"(similarity {ssi['closest_score']:.2f}), not a safe match.", "high"))
    return {"payment": p, "iban": iban, "bic": bic, "ssi": ssi}, f


def plan_payment_repair(exc: dict, facts: Facts) -> dict:
    p, ssi, iban, bic = facts["payment"], facts["ssi"], facts["iban"], facts["bic"]
    if not ssi["found"]:
        return _plan(
            "create_work_item",
            {"queue": "Payments Repair", "summary": f"Obtain verified beneficiary details for {p['payment_id']} by "
                                                    f"call-back to the customer's registered number"},
            "No verified beneficiary details on file: do not repair from the ticket text. Call the customer back.",
            ["Changing payee details from an unverified source is a classic authorised-push-payment / "
             "business-email-compromise fraud pattern.",
             "The agent will not invent or guess account details."],
            ["Call back the customer on the number held on file (not one given in the request).",
             "Add the beneficiary to the SSI master after verification.", "Resubmit the payment."],
            confidence="High")
    new_iban = ssi["iban"] if iban is not None and not iban["valid"] else None
    new_bic = ssi["bic"] if not bic["found"] else None
    changes = []
    if new_iban:
        changes.append(f"IBAN {p['beneficiary_iban']} -> {new_iban}")
    if new_bic:
        changes.append(f"BIC {p['beneficiary_bic']} -> {new_bic}")
    return _plan(
        "repair_and_resubmit_payment", {"payment_id": p["payment_id"], "iban": new_iban, "bic": new_bic},
        f"Repair {p['payment_id']} from the verified SSI ({'; '.join(changes)}) and resubmit.",
        [f"The rejected field fails validation, and a verified SSI for {p['beneficiary']} exists.",
         "The correction comes from golden-source master data, not from free text."],
        ["Resubmit to the payment engine.", "Confirm value date with the customer if cut-off is missed."],
        auto_reason="Changes to beneficiary details always need a human (payment-fraud control).", moves_money=True)


# --------------------------------------------------------------- nostro break
def investigate_nostro(exc: dict, tb: Toolbox) -> tuple[Facts, list[dict]]:
    item = tb.call("get_nostro_item", item_id=exc["refs"]["nostro_item_id"])
    m = tb.call("find_statement_matches", item_id=item["item_id"])
    f = [_finding("get_nostro_item", f"Ledger item {item['reference']} USD {item['amount']:,.2f}, value "
                  f"{item['value_date']}, status {item['status']}.")]
    if m["candidates"]:
        c = m["candidates"][0]
        f.append(_finding("find_statement_matches",
                          f"Best statement candidate {c['statement_item_id']}: amount difference "
                          f"{c['amount_diff']:+,.2f}, reference '{c['reference']}' (similarity "
                          f"{c['reference_score']:.2f}), {c['days_apart']} day(s) apart.",
                          "info" if c["exact_amount"] and c["reference_score"] >= 0.8 else "medium"))
    else:
        f.append(_finding("find_statement_matches", "No statement item within tolerance and date window.", "high"))
    return {"item": item, "candidates": m["candidates"]}, f


def plan_nostro(exc: dict, facts: Facts) -> dict:
    item, cands = facts["item"], facts["candidates"]
    if not cands:
        return _plan(
            "create_work_item", {"queue": "Nostro Investigations",
                                 "summary": f"Send MT199 enquiry to Harbor Trust NY for {item['reference']}"},
            "No counterpart on the statement: open an investigation with the correspondent.",
            ["Nothing on the correspondent statement matches on amount or reference within 5 days."],
            ["Send an MT199 free-format enquiry.", "Check whether the payment was ever released by our side."])
    c = cands[0]
    if c["exact_amount"] and c["reference_score"] >= 0.8 and c["days_apart"] <= 3:
        return _plan(
            "match_nostro_items", {"ledger_item_id": item["item_id"], "statement_item_id": c["statement_item_id"]},
            f"Timing difference: match {item['item_id']} to {c['statement_item_id']} and close the break.",
            ["Same amount to the cent.", f"Reference similarity {c['reference_score']:.2f}.",
             f"Value dates {c['days_apart']} day(s) apart, a normal settlement timing difference."],
            ["None; the match is reversible by un-matching in the recon tool."],
            auto_eligible=True, auto_reason="Exact-amount timing difference with a matching reference.")
    if not c["exact_amount"]:
        return _plan(
            "match_nostro_items", {"ledger_item_id": item["item_id"], "statement_item_id": c["statement_item_id"]},
            f"Match with a {c['amount_diff']:+,.2f} difference and claim the shortfall from the correspondent.",
            [f"Reference matches ({c['reference_score']:.2f}) but the amount differs by {c['amount_diff']:+,.2f}, "
             "typical of an unagreed correspondent fee."],
            ["Post the difference to correspondent charges suspense.", "Claim the fee back under the SLA."],
            confidence="Medium")
    return _plan(
        "match_nostro_items", {"ledger_item_id": item["item_id"], "statement_item_id": c["statement_item_id"]},
        f"Probable match to {c['statement_item_id']} on amount only; reference does not corroborate.",
        ["Amount and date fit but the statement reference is unusable, so the match needs a human eye."],
        ["Confirm with the payment's original instruction before matching."], confidence="Medium")


# --------------------------------------------------------------- duplicates
def investigate_duplicate(exc: dict, tb: Toolbox) -> tuple[Facts, list[dict]]:
    p = tb.call("get_payment", payment_id=exc["refs"]["payment_id"])
    sim = tb.call("find_similar_payments", payment_id=p["payment_id"])
    f = []
    for m in sim["matches"]:
        f.append(_finding("find_similar_payments",
                          f"{m['payment_id']} ({m['status']}) has the same debtor, beneficiary and amount, "
                          f"{m['hours_apart']}h apart; reference {'IDENTICAL' if m['same_reference'] else 'different'} "
                          f"('{m['reference']}' vs '{p['reference']}').",
                          "high" if m["same_reference"] else "medium"))
    if not sim["matches"]:
        f.append(_finding("find_similar_payments", "No similar payment found in the last 24 hours."))
    return {"payment": p, "matches": sim["matches"]}, f


def plan_duplicate(exc: dict, facts: Facts) -> dict:
    p = facts["payment"]
    dup = [m for m in facts["matches"] if m["same_reference"] and m["status"] in ("SETTLED", "RELEASED")]
    if dup:
        return _plan(
            "cancel_payment", {"payment_id": p["payment_id"], "reason": f"Duplicate of {dup[0]['payment_id']}"},
            f"Cancel {p['payment_id']}: it duplicates {dup[0]['payment_id']}, which already settled.",
            ["Same debtor, beneficiary, amount and invoice reference within hours."],
            ["Tell the customer the second instruction was cancelled as a duplicate."], moves_money=True)
    return _plan(
        "release_payment", {"payment_id": p["payment_id"]},
        f"Release {p['payment_id']}: the earlier payment has a different invoice reference.",
        ["Same amount and beneficiary, but a different reference indicates a separate invoice."],
        ["Optional: confirm with the customer if the amount is unusual for this beneficiary."],
        confidence="Medium", moves_money=True)


# --------------------------------------------------------------- sanctions
STRONG_MATCH = 0.85


def investigate_sanctions(exc: dict, tb: Toolbox) -> tuple[Facts, list[dict]]:
    p = tb.call("get_payment", payment_id=exc["refs"]["payment_id"])
    s = tb.call("screen_name", name=p["beneficiary"], country=p.get("beneficiary_country"),
                dob=p.get("beneficiary_dob"))
    f = []
    for h in s["hits"][:3]:
        f.append(_finding("screen_name",
                          f"Listed party {h['listed_name']} ({h['list_id']}): name score {h['name_score']:.2f}, "
                          f"DOB {h['dob_check']}, country {h['country_check']}; overall strength "
                          f"{h['match_strength']:.2f}.", "high" if h["match_strength"] >= STRONG_MATCH else "medium"))
    if not s["hits"]:
        f.append(_finding("screen_name", "Re-screening found no list entry above the 0.60 name threshold."))
    return {"payment": p, "hits": s["hits"]}, f


def plan_sanctions(exc: dict, facts: Facts) -> dict:
    p, hits = facts["payment"], facts["hits"]
    top = hits[0] if hits else None
    if top and top["match_strength"] >= STRONG_MATCH:
        return _plan(
            "keep_payment_blocked", {"payment_id": p["payment_id"],
                                     "disposition": f"Probable true match to {top['list_id']}; escalate to MLRO"},
            f"Keep {p['payment_id']} blocked and escalate to the MLRO: probable true match to {top['listed_name']}.",
            [f"Strength {top['match_strength']:.2f} >= {STRONG_MATCH}: name, and DOB/country where available, "
             "corroborate."],
            ["MLRO decides on reporting to the authorities.", "Do not tip off the customer."])
    return _plan(
        "release_payment", {"payment_id": p["payment_id"]},
        f"Likely false positive: a Compliance Officer may release {p['payment_id']} after review.",
        [f"Best hit strength {top['match_strength']:.2f} is below {STRONG_MATCH}" if top else "No hit on re-screen",
         "Secondary identifiers (DOB / country) do not corroborate the name match." if top else ""],
        ["Compliance records the false-positive rationale.", "Consider a good-guy list entry if it recurs."],
        confidence="Medium", moves_money=True)


# --------------------------------------------------------------- KYC
def investigate_kyc(exc: dict, tb: Toolbox) -> tuple[Facts, list[dict]]:
    p = tb.call("get_payment", payment_id=exc["refs"]["payment_id"])
    c = tb.call("get_customer", customer_id=exc["refs"]["customer_id"])
    f = [_finding("get_customer", f"{c['name']}: KYC status {c['kyc_status']}, review was due {c['kyc_review_due']}, "
                  f"risk rating {c['risk_rating']}, RM {c['relationship_manager']}.", "high"),
         _finding("get_payment", f"{p['payment_id']} {p['currency']} {p['amount']:,.2f} to {p['beneficiary']} "
                  f"is {p['status']}.")]
    return {"payment": p, "customer": c}, f


def plan_kyc(exc: dict, facts: Facts) -> dict:
    c, p = facts["customer"], facts["payment"]
    return _plan(
        "request_kyc_refresh", {"customer_id": c["customer_id"]},
        f"Start the KYC periodic review for {c['name']} now; {p['payment_id']} stays blocked until it completes.",
        ["Policy: no outbound payments while KYC is expired.",
         f"Risk rating {c['risk_rating']}; the RM is {c['relationship_manager']}."],
        ["RM contacts the customer for refreshed documents.", "Compliance releases the payment after the review."])


# --------------------------------------------------------------- settlement
def investigate_settlement(exc: dict, tb: Toolbox) -> tuple[Facts, list[dict]]:
    t = tb.call("get_trade", trade_id=exc["refs"]["trade_id"])
    pen = tb.call("estimate_settlement_penalty", trade_id=t["trade_id"])
    f = [_finding("get_trade", f"{t['side']} {t['quantity']:,} {t['isin']} vs {t['currency']} {t['cash_amount']:,.2f} "
                  f"with {t['counterparty']}; fail reason {t['fail_reason']}.", "medium"),
         _finding("estimate_settlement_penalty", f"Illustrative penalty {pen['currency']} {pen['daily_penalty']:,.2f}"
                  f"/day; {pen['days_failed']} day(s) failed, about {pen['currency']} {pen['accrued']:,.2f} accrued.",
                  "high" if pen["days_failed"] >= 3 else "medium")]
    return {"trade": t, "penalty": pen}, f


def plan_settlement(exc: dict, facts: Facts) -> dict:
    t = facts["trade"]
    if t["fail_reason"] == "SSI_MISMATCH":
        return _plan("reinstruct_settlement", {"trade_id": t["trade_id"], "instruction":
                     "Re-instruct using counterparty SSI confirmed from the golden source"},
                     "Re-instruct with the counterparty's confirmed SSI.",
                     ["Instructions do not match at the CSD; the fix is on the static data side."],
                     ["Confirm the SSI with the counterparty's operations team.", "Fix static data at source."])
    if t["fail_reason"] == "INSUFFICIENT_SECURITIES":
        return _plan("reinstruct_settlement", {"trade_id": t["trade_id"], "instruction":
                     "Allow partial settlement and borrow the shortfall via the securities lending desk"},
                     "Enable partial settlement and borrow the shortfall to stop penalties.",
                     ["Penalties accrue daily; partial settlement plus a borrow is the standard remedy."],
                     ["Book the borrow with the SLB desk.", "Monitor for buy-in risk."])
    return _plan("create_work_item", {"queue": "Treasury Funding", "summary":
                 f"Fund {t['currency']} {t['cash_amount']:,.2f} shortfall for {t['trade_id']}"},
                 "Cash shortfall: ask Treasury to fund the settlement account.",
                 ["The fail is on our cash side; operations cannot fix it alone."],
                 ["Treasury funds the account.", "Re-instruct once funded."])


# --------------------------------------------------------------- GL suspense
def investigate_suspense(exc: dict, tb: Toolbox) -> tuple[Facts, list[dict]]:
    s = tb.call("get_suspense_item", suspense_id=exc["refs"]["suspense_id"])
    hits = tb.call("search_payments_by_reference", reference=s["reference"])["matches"]
    f = [_finding("get_suspense_item", f"GBP {s['amount']:,.2f} in {s['gl_account']} for {s['age_days']} days; "
                  f"narrative '{s['narrative']}'.", "high" if s["age_days"] > 30 else "info")]
    account = None
    inbound = [p for p in hits if p.get("direction") == "INBOUND" and p.get("credit_account")]
    if len(inbound) == 1 and inbound[0]["amount"] == s["amount"]:
        account = tb.call("get_account", account_id=inbound[0]["credit_account"])
        f.append(_finding("search_payments_by_reference",
                          f"One inbound payment {inbound[0]['payment_id']} with the same reference and amount, "
                          f"intended for account {account['account_id']} ({inbound[0]['beneficiary']})."))
    else:
        f.append(_finding("search_payments_by_reference",
                          f"{len(hits)} payment(s) found for reference '{s['reference']}'; no unique owner.", "high"))
    return {"suspense": s, "account": account}, f


def plan_suspense(exc: dict, facts: Facts) -> dict:
    s, a = facts["suspense"], facts["account"]
    if a:
        return _plan(
            "clear_suspense_to_account", {"suspense_id": s["suspense_id"], "account_id": a["account_id"]},
            f"Clear {s['suspense_id']} to account {a['account_id']} (unique reference and amount match).",
            ["Exactly one inbound payment carries this reference and amount."],
            ["None; the journal is reversible."],
            auto_eligible=s["age_days"] <= 30,
            auto_reason="Unique exact reference-and-amount match on a recent item." if s["age_days"] <= 30
            else "", moves_money=True)
    return _plan(
        "create_work_item", {"queue": "GL Investigations", "summary": f"Trace remitter for {s['suspense_id']} via "
                                                                       f"the originating bank"},
        "Owner unknown: trace the remitter through the originating bank.",
        ["No payment reference links this credit to a customer."]
        + (["Aged over 90 days: unclaimed-funds policy applies."] if s["age_days"] > 90 else []),
        ["Request remitter details from the sending bank.", "Return funds if the owner cannot be found."])


# --------------------------------------------------------------- limit breach
def investigate_limit(exc: dict, tb: Toolbox) -> tuple[Facts, list[dict]]:
    a = tb.call("get_account", account_id=exc["refs"]["account_id"])
    c = tb.call("get_customer", customer_id=a["customer_id"])
    excess = round(-a["balance"] - a["overdraft_limit"], 2)
    pct = excess / a["overdraft_limit"] if a["overdraft_limit"] else 1.0
    f = [_finding("get_account", f"Balance GBP {a['balance']:,.2f} against limit GBP {a['overdraft_limit']:,.2f}: "
                  f"excess GBP {excess:,.2f} ({pct:.0%} of limit).", "high" if pct > 0.1 else "medium"),
         _finding("get_customer", f"{c['name']}: {c['segment']}, tier {c['tier']}, risk rating {c['risk_rating']}.")]
    return {"account": a, "customer": c, "excess": excess, "excess_pct": pct}, f


def plan_limit(exc: dict, facts: Facts) -> dict:
    a, c, pct = facts["account"], facts["customer"], facts["excess_pct"]
    if pct <= 0.05 and c["risk_rating"] != "High":
        return _plan("record_credit_decision", {"account_id": a["account_id"], "decision": "PAY (temporary excess)",
                                                "expiry_days": 7},
                     "Recommend paying the debits as a 7-day temporary excess.",
                     [f"Excess is {pct:.0%} of limit and the customer is not high risk."],
                     ["RM to agree a repayment date with the customer."], moves_money=True)
    return _plan("record_credit_decision", {"account_id": a["account_id"], "decision": "RETURN debits; refer to RM",
                                            "expiry_days": 0},
                 "Recommend returning the debits and referring to the RM.",
                 [f"Excess is {pct:.0%} of limit" + (" and the customer is rated high risk." if
                                                     c["risk_rating"] == "High" else ".")],
                 ["RM discusses a facility review with the customer."], confidence="Medium")


PLAYBOOKS = {
    "PAYMENT_REPAIR": (investigate_payment_repair, plan_payment_repair),
    "NOSTRO_BREAK": (investigate_nostro, plan_nostro),
    "DUPLICATE_PAYMENT": (investigate_duplicate, plan_duplicate),
    "SANCTIONS_HIT": (investigate_sanctions, plan_sanctions),
    "KYC_EXPIRED_BLOCK": (investigate_kyc, plan_kyc),
    "SETTLEMENT_FAIL": (investigate_settlement, plan_settlement),
    "GL_SUSPENSE": (investigate_suspense, plan_suspense),
    "LIMIT_BREACH": (investigate_limit, plan_limit),
}
