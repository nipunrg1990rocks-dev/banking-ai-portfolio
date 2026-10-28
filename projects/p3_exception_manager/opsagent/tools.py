"""Tools the agents call against Ledgerline's (synthetic) core banking systems.

Two kinds of tool:

* **Read tools** gather evidence. Agents may call them freely.
* **Action tools** change records (repair a payment, match a recon item, post to
  the ledger). Only the executor node may call them, only after the control
  gate and the required human approvals, and each action is idempotent: running
  the same action twice for the same exception does nothing the second time.

Every call is logged with its arguments, a short result and its duration, which
is what the app shows as the agent's tool trace.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from .data import BIC_DIRECTORY, FX_TO_USD, NOW, SANCTIONS_LIST, CoreBanking, iban_is_valid, iso

READ_TOOLS: dict[str, str] = {}
ACTION_TOOLS: dict[str, str] = {}


def read_tool(description: str):
    def wrap(fn: Callable) -> Callable:
        READ_TOOLS[fn.__name__] = description
        return fn
    return wrap


def action_tool(description: str):
    def wrap(fn: Callable) -> Callable:
        ACTION_TOOLS[fn.__name__] = description
        return fn
    return wrap


class ToolError(RuntimeError):
    pass


def _norm(name: str) -> str:
    drop = {"ltd", "llc", "plc", "inc", "gmbh", "the", "co", "sa", "bv", "ag"}
    return " ".join(w for w in "".join(c if c.isalnum() else " " for c in name.lower()).split() if w not in drop)


def name_similarity(a: str, b: str) -> float:
    """Blend of character similarity and token overlap, 0..1."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    char = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    token = len(ta & tb) / max(len(ta | tb), 1)
    # Partial token similarity catches transliteration ("Malenkoff" vs "Malenkov").
    fuzzy = sum(max(SequenceMatcher(None, x, y).ratio() for y in tb) for x in ta) / len(ta)
    return round(0.45 * char + 0.2 * token + 0.35 * fuzzy, 3)


@dataclass
class Toolbox:
    bank: CoreBanking
    calls: list[dict] = field(default_factory=list)
    action_ledger: dict[str, dict] = field(default_factory=dict)

    # ------------------------------------------------------------- plumbing
    def call(self, tool: str, /, **kwargs: Any) -> dict:
        if tool not in READ_TOOLS:
            raise ToolError(f"{tool} is not a read tool")
        return self._run(tool, kwargs)

    def act(self, tool: str, /, idempotency_key: str, **kwargs: Any) -> dict:
        if tool not in ACTION_TOOLS:
            raise ToolError(f"{tool} is not an action tool")
        if idempotency_key in self.action_ledger:
            prior = self.action_ledger[idempotency_key]
            return {**prior, "idempotent_replay": True}
        result = self._run(tool, kwargs)
        self.action_ledger[idempotency_key] = {**result, "action": tool, "at": iso(NOW)}
        return result

    def _run(self, name: str, kwargs: dict) -> dict:
        started = time.perf_counter()
        try:
            result = getattr(self, name)(**kwargs)
            ok = True
        except ToolError as exc:
            result, ok = {"error": str(exc)}, False
        self.calls.append({"tool": name, "kind": "action" if name in ACTION_TOOLS else "read", "args": kwargs,
                           "ok": ok, "result": result, "ms": round((time.perf_counter() - started) * 1000, 2)})
        return result

    def _get(self, table: dict, key: str, what: str) -> dict:
        if key not in table:
            raise ToolError(f"{what} {key} not found")
        return table[key]

    # ----------------------------------------------------------- read tools
    @read_tool("Fetch a payment instruction and its current status.")
    def get_payment(self, payment_id: str) -> dict:
        return dict(self._get(self.bank.payments, payment_id, "Payment"))

    @read_tool("Fetch customer profile: segment, tier, risk rating and KYC status.")
    def get_customer(self, customer_id: str) -> dict:
        return dict(self._get(self.bank.customers, customer_id, "Customer"))

    @read_tool("Fetch an account: balance, overdraft limit and status.")
    def get_account(self, account_id: str) -> dict:
        return dict(self._get(self.bank.accounts, account_id, "Account"))

    @read_tool("Validate an IBAN's structure and ISO 13616 mod-97 check digits.")
    def validate_iban(self, iban: str | None) -> dict:
        if not iban:
            return {"iban": iban, "valid": False, "reason": "no IBAN on the instruction"}
        valid = iban_is_valid(iban)
        return {"iban": iban, "valid": valid, "reason": "checksum ok" if valid else "mod-97 checksum fails"}

    @read_tool("Look up a BIC in the bank identifier directory.")
    def lookup_bic(self, bic: str | None) -> dict:
        found = BIC_DIRECTORY.get(bic or "")
        return {"bic": bic, "found": bool(found), "institution": found}

    @read_tool("Look up the verified standing settlement instructions (SSI) held for a beneficiary.")
    def lookup_beneficiary_ssi(self, beneficiary: str) -> dict:
        ssi = self.bank.ssi.get(beneficiary)
        if not ssi:
            best = max(self.bank.ssi, key=lambda n: name_similarity(n, beneficiary))
            return {"found": False, "closest_name": best, "closest_score": name_similarity(best, beneficiary)}
        return {"found": True, **ssi}

    @read_tool("Find other payments with the same debtor, beneficiary and amount within 24 hours.")
    def find_similar_payments(self, payment_id: str) -> dict:
        p = self._get(self.bank.payments, payment_id, "Payment")
        t0 = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
        matches = []
        for q in self.bank.payments.values():
            if q["payment_id"] == payment_id:
                continue
            if (q["debtor_account"], q["beneficiary"], q["amount"], q["currency"]) != (
                    p["debtor_account"], p["beneficiary"], p["amount"], p["currency"]):
                continue
            tq = datetime.fromisoformat(q["created_at"].replace("Z", "+00:00"))
            hours = abs((t0 - tq).total_seconds()) / 3600
            if hours <= 24:
                matches.append({"payment_id": q["payment_id"], "status": q["status"], "reference": q["reference"],
                                "same_reference": q["reference"] == p["reference"], "hours_apart": round(hours, 2)})
        return {"payment_id": payment_id, "reference": p["reference"], "matches": matches}

    @read_tool("Screen a name against the sanctions list with fuzzy matching, DOB and country checks.")
    def screen_name(self, name: str, country: str | None = None, dob: str | None = None) -> dict:
        hits = []
        for entry in SANCTIONS_LIST:
            score = max(name_similarity(name, n) for n in [entry["name"], *entry["aliases"]])
            if score < 0.6:
                continue
            dob_check = "not available" if not (dob and entry["dob"]) else (
                "match" if dob == entry["dob"] else "mismatch")
            country_check = "match" if country and country == entry["country"] else "mismatch"
            strength = score + (0.15 if dob_check == "match" else -0.25 if dob_check == "mismatch" else 0) + (
                0.1 if country_check == "match" else -0.1)
            hits.append({"list_id": entry["list_id"], "listed_name": entry["name"], "program": entry["program"],
                         "name_score": score, "dob_check": dob_check, "country_check": country_check,
                         "match_strength": round(strength, 3)})
        hits.sort(key=lambda h: h["match_strength"], reverse=True)
        return {"screened_name": name, "hits": hits}

    @read_tool("Fetch a nostro ledger item.")
    def get_nostro_item(self, item_id: str) -> dict:
        return dict(self._get(self.bank.nostro_ledger, item_id, "Nostro ledger item"))

    @read_tool("Find candidate correspondent-statement items for a ledger item (amount, reference, date window).")
    def find_statement_matches(self, item_id: str, amount_tolerance: float = 0.005, days: int = 5) -> dict:
        led = self._get(self.bank.nostro_ledger, item_id, "Nostro ledger item")
        ld = date.fromisoformat(led["value_date"])
        out = []
        for s in self.bank.nostro_statement.values():
            if s["status"] != "UNMATCHED":
                continue
            diff = round(s["amount"] - led["amount"], 2)
            gap = abs((date.fromisoformat(s["value_date"]) - ld).days)
            ref_score = name_similarity(s["reference"], led["reference"])
            if gap <= days and (abs(diff) <= led["amount"] * amount_tolerance or ref_score >= 0.8):
                out.append({"statement_item_id": s["item_id"], "amount": s["amount"], "amount_diff": diff,
                            "reference": s["reference"], "reference_score": ref_score, "days_apart": gap,
                            "exact_amount": diff == 0})
        out.sort(key=lambda m: (not m["exact_amount"], -m["reference_score"], m["days_apart"]))
        return {"ledger_item": item_id, "candidates": out}

    @read_tool("Fetch a securities trade and its settlement status.")
    def get_trade(self, trade_id: str) -> dict:
        return dict(self._get(self.bank.trades, trade_id, "Trade"))

    @read_tool("Estimate the daily CSDR-style cash penalty for a failed trade (illustrative rates).")
    def estimate_settlement_penalty(self, trade_id: str) -> dict:
        t = self._get(self.bank.trades, trade_id, "Trade")
        bps = {"Bond": 0.30, "Equity": 1.00}[t["security_type"]]
        if t["fail_reason"] == "INSUFFICIENT_CASH":
            bps = 0.25
        daily = round(t["cash_amount"] * bps / 10_000, 2)
        days = (NOW.date() - date.fromisoformat(t["intended_settle_date"])).days
        return {"trade_id": trade_id, "daily_penalty": daily, "currency": t["currency"], "days_failed": days,
                "accrued": round(daily * days, 2), "rate_bps": bps,
                "note": "illustrative rates for a fictional CSD, not regulatory values"}

    @read_tool("Fetch a GL suspense item.")
    def get_suspense_item(self, suspense_id: str) -> dict:
        return dict(self._get(self.bank.suspense, suspense_id, "Suspense item"))

    @read_tool("Search payments by exact reference.")
    def search_payments_by_reference(self, reference: str) -> dict:
        hits = [dict(p) for p in self.bank.payments.values() if p["reference"] == reference]
        return {"reference": reference, "matches": hits}

    @read_tool("Convert an amount to USD at the bank's daily rate.")
    def fx_to_usd(self, amount: float, currency: str) -> dict:
        rate = FX_TO_USD[currency]
        return {"amount": amount, "currency": currency, "rate": rate, "usd": round(amount * rate, 2)}

    # --------------------------------------------------------- action tools
    @action_tool("Correct beneficiary details from verified SSI and resubmit the payment.")
    def repair_and_resubmit_payment(self, payment_id: str, iban: str | None, bic: str | None) -> dict:
        p = self._get(self.bank.payments, payment_id, "Payment")
        before = {"beneficiary_iban": p["beneficiary_iban"], "beneficiary_bic": p["beneficiary_bic"]}
        if iban:
            p["beneficiary_iban"] = iban
        if bic:
            p["beneficiary_bic"] = bic
        p["status"] = "RESUBMITTED"
        p["reason_code"] = None
        return {"payment_id": payment_id, "before": before,
                "after": {"beneficiary_iban": p["beneficiary_iban"], "beneficiary_bic": p["beneficiary_bic"]},
                "status": p["status"]}

    @action_tool("Cancel a held payment.")
    def cancel_payment(self, payment_id: str, reason: str) -> dict:
        p = self._get(self.bank.payments, payment_id, "Payment")
        p["status"] = "CANCELLED"
        p["cancel_reason"] = reason
        return {"payment_id": payment_id, "status": "CANCELLED", "reason": reason}

    @action_tool("Release a held payment for onward processing.")
    def release_payment(self, payment_id: str) -> dict:
        p = self._get(self.bank.payments, payment_id, "Payment")
        p["status"] = "RELEASED"
        return {"payment_id": payment_id, "status": "RELEASED"}

    @action_tool("Keep a payment blocked and record the disposition.")
    def keep_payment_blocked(self, payment_id: str, disposition: str) -> dict:
        p = self._get(self.bank.payments, payment_id, "Payment")
        p["status"] = "BLOCKED"
        p["disposition"] = disposition
        return {"payment_id": payment_id, "status": "BLOCKED", "disposition": disposition}

    @action_tool("Match a nostro ledger item to a statement item and close the break.")
    def match_nostro_items(self, ledger_item_id: str, statement_item_id: str) -> dict:
        led = self._get(self.bank.nostro_ledger, ledger_item_id, "Nostro ledger item")
        stm = self._get(self.bank.nostro_statement, statement_item_id, "Nostro statement item")
        if stm["status"] != "UNMATCHED":
            raise ToolError(f"{statement_item_id} is already matched")
        led["status"] = stm["status"] = "MATCHED"
        led["matched_to"], stm["matched_to"] = statement_item_id, ledger_item_id
        return {"ledger_item_id": ledger_item_id, "statement_item_id": statement_item_id, "status": "MATCHED"}

    @action_tool("Clear a suspense item to a customer account (double entry).")
    def clear_suspense_to_account(self, suspense_id: str, account_id: str) -> dict:
        s = self._get(self.bank.suspense, suspense_id, "Suspense item")
        a = self._get(self.bank.accounts, account_id, "Account")
        if s["status"] != "OPEN":
            raise ToolError(f"{suspense_id} is not open")
        s["status"] = "CLEARED"
        s["cleared_to"] = account_id
        a["balance"] = round(a["balance"] + s["amount"], 2)
        return {"suspense_id": suspense_id, "journal": [
            {"dr": s["gl_account"], "cr": f"customer {account_id}", "amount": s["amount"], "currency": s["currency"]}],
            "status": "CLEARED"}

    @action_tool("Open a task in an operations work queue (no money movement).")
    def create_work_item(self, queue: str, summary: str) -> dict:
        n = sum(1 for v in self.action_ledger.values() if v.get("action") == "create_work_item") + 1
        return {"queue": queue, "work_item_id": f"WI-{queue[:3].upper()}-{n:04d}", "summary": summary}

    @action_tool("Re-instruct a failed settlement with a corrected or alternative instruction.")
    def reinstruct_settlement(self, trade_id: str, instruction: str) -> dict:
        t = self._get(self.bank.trades, trade_id, "Trade")
        t["status"] = "REINSTRUCTED"
        t["reinstruction"] = instruction
        return {"trade_id": trade_id, "status": "REINSTRUCTED", "instruction": instruction}

    @action_tool("Record a credit decision on a limit excess (pay or return).")
    def record_credit_decision(self, account_id: str, decision: str, expiry_days: int = 0) -> dict:
        a = self._get(self.bank.accounts, account_id, "Account")
        a["credit_decision"] = {"decision": decision, "expiry_days": expiry_days}
        return {"account_id": account_id, "decision": decision, "expiry_days": expiry_days}

    @action_tool("Trigger a KYC periodic review and customer outreach.")
    def request_kyc_refresh(self, customer_id: str) -> dict:
        c = self._get(self.bank.customers, customer_id, "Customer")
        c["kyc_status"] = "Review in progress"
        return {"customer_id": customer_id, "kyc_status": c["kyc_status"]}
