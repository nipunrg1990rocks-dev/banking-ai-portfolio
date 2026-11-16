"""API behaviour: authentication, permissions, idempotency, optimistic locking, maker-checker,
state machine, interface sequencing, sandboxes and replay idempotency."""

import uuid

import pytest

pytestmark = pytest.mark.db


def trade_body(client, auth, **over):
    src = client.get("/api/trades/TRD00010", headers=auth("mo1")).json()["trade"]
    body = {k: src[k] for k in ("product_type", "instrument_id", "client_id", "account_id", "counterparty_id", "side", "quantity",
                                "price", "settle_ccy")}
    body.update(trade_date="2026-11-06", source_trade_ref="T-" + uuid.uuid4().hex[:10], book="EQ-FLOW")
    body.update(over)
    return body


def test_login_and_permissions(client, auth):
    assert client.get("/api/trades").status_code == 401
    assert client.post("/api/auth/login", json={"username": "trader1", "password": "wrong"}).status_code == 401
    r = client.post("/api/trades/TRD00010/transition", headers=auth("trader1"), json={"target": "SETTLED"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "E_FORBIDDEN"
    me = client.get("/api/auth/me", headers=auth("checker1")).json()
    assert me["role"] == "CHECKER" and "approval.decide" in me["permissions"]


def test_error_envelope_has_correlation_id(client, auth):
    r = client.get("/api/trades/NOPE", headers={**auth("mo1"), "X-Correlation-ID": "corr-test-1"})
    assert r.status_code == 404
    assert r.json()["error"]["correlation_id"] == "corr-test-1"
    assert r.headers["X-Correlation-ID"] == "corr-test-1"


def test_blotter_filters_sort_paginate_and_columns(client, auth):
    r = client.get("/api/trades", headers=auth("trader1"),
                   params={"currency": "JPY", "page_size": 5, "columns": "trade_id,settle_ccy", "sort": "trade_id"}).json()
    assert r["total"] > 5 and len(r["items"]) == 5
    assert all(set(i) == {"trade_id", "settle_ccy"} and i["settle_ccy"] == "JPY" for i in r["items"])
    assert [i["trade_id"] for i in r["items"]] == sorted(i["trade_id"] for i in r["items"])
    assert client.get("/api/trades", headers=auth("trader1"), params={"sort": "nope"}).status_code == 400


def test_export_csv_and_xlsx(client, auth):
    csv = client.get("/api/trades/export", headers=auth("trader1"), params={"product_group": "FX", "columns": "trade_id,status"})
    assert csv.status_code == 200 and csv.text.splitlines()[0] == "trade_id,status"
    x = client.get("/api/trades/export", headers=auth("trader1"), params={"format": "xlsx"})
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_schema_validation_and_rule_rejection(client, auth):
    r = client.post("/api/trades", headers=auth("trader1"), json={"product_type": "EQUITY"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "E_SCHEMA"
    r = client.post("/api/trades", headers=auth("trader1"), json=trade_body(client, auth, trade_date="2026-11-07"))
    assert r.status_code == 422
    assert r.json()["error"]["details"]["issues"][0]["rule_id"] == "R010"


def test_idempotency_key_replay_and_conflict(client, auth):
    body = trade_body(client, auth)
    h = {**auth("trader1"), "Idempotency-Key": uuid.uuid4().hex}
    a = client.post("/api/trades", headers=h, json=body)
    b = client.post("/api/trades", headers=h, json=body)
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["trade"]["trade_id"] == b.json()["trade"]["trade_id"] and b.headers["Idempotent-Replay"] == "true"
    c = client.post("/api/trades", headers=h, json={**body, "quantity": "300"})
    assert c.status_code == 409 and c.json()["error"]["code"] == "E_IDEMPOTENCY_CONFLICT"


def test_amendment_versioning_and_maker_checker(client, auth):
    t = client.post("/api/trades", headers=auth("trader1"), json=trade_body(client, auth)).json()["trade"]
    tid = t["trade_id"]
    assert client.patch(f"/api/trades/{tid}", headers=auth("mo1"), json={"changes": {"book": "X"}, "reason": "no version"}).status_code == 428
    r = client.patch(f"/api/trades/{tid}", headers={**auth("mo1"), "If-Match": "1"},
                     json={"changes": {"quantity": "300", "book": "EQ-HEDGE"}, "reason": "client corrected size"})
    assert r.status_code == 200 and r.json()["version"] == 2
    stale = client.patch(f"/api/trades/{tid}", headers={**auth("mo1"), "If-Match": "1"}, json={"changes": {"book": "Y"}, "reason": "stale"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "E_STALE_VERSION"
    apr = r.json()["approval_id"]
    self_ = client.post(f"/api/approvals/{apr}/decision", headers=auth("mo1"), json={"approve": True})
    assert self_.status_code == 403 and self_.json()["error"]["code"] == "E_SELF_APPROVAL"
    blocked = client.get("/api/audit", headers=auth("risk1"), params={"action": "APPROVAL_BLOCKED_SELF", "entity_id": apr}).json()
    assert blocked["total"] == 1
    ok = client.post(f"/api/approvals/{apr}/decision", headers=auth("checker1"), json={"approve": True, "note": "ok"})
    assert ok.status_code == 200 and ok.json()["result"]["version"] == 3
    d = client.get(f"/api/trades/{tid}", headers=auth("mo1")).json()
    assert d["trade"]["quantity"].startswith("300")
    assert [v["change_type"] for v in d["versions"]] == ["NEW", "NON_ECONOMIC_AMEND", "ECONOMIC_AMEND"]
    assert {a["status"] for a in d["allocations"]} == {"ACTIVE", "SUPERSEDED"}
    again = client.post(f"/api/approvals/{apr}/decision", headers=auth("checker2"), json={"approve": True})
    assert again.status_code == 409


def test_cancel_and_rebook_reverses_postings(client, auth):
    t = client.post("/api/trades", headers=auth("trader1"), json=trade_body(client, auth)).json()["trade"]
    r = client.post(f"/api/trades/{t['trade_id']}/cancel", headers=auth("trader1"), json={"reason": "booked in error",
                                                                                          "rebook": {"quantity": "100"}})
    assert r.status_code == 202
    d = client.post(f"/api/approvals/{r.json()['approval_id']}/decision", headers=auth("checker2"), json={"approve": True}).json()
    new = d["result"]["rebooked_trade_id"]
    assert client.get(f"/api/trades/{t['trade_id']}", headers=auth("mo1")).json()["trade"]["status"] == "CANCELLED"
    assert client.get(f"/api/trades/{new}", headers=auth("mo1")).json()["trade"]["rebook_of"] == t["trade_id"]


def test_cancel_settled_trade_posts_exact_reversals(client, auth):
    settled = client.get("/api/trades", headers=auth("mo1"), params={"status": "SETTLED", "product_type": "EQUITY",
                                                                    "page_size": 1}).json()["items"][0]["trade_id"]
    r = client.post(f"/api/trades/{settled}/cancel", headers=auth("mo1"), json={"reason": "wrong account; cancel and rebook (R090)"})
    out = client.post(f"/api/approvals/{r.json()['approval_id']}/decision", headers=auth("checker1"), json={"approve": True}).json()
    assert out["result"]["reversed_postings"] > 0
    ledger = client.get(f"/api/trades/{settled}", headers=auth("mo1")).json()["ledger"]
    totals: dict = {}
    for e in ledger:
        if ":" not in e["ledger_account"]:
            totals[(e["ledger_account"], e["key"])] = totals.get((e["ledger_account"], e["key"]), 0) + float(e["amount"])
    assert all(abs(v) < 1e-9 for v in totals.values()), totals


def test_economic_amendment_blocked_after_settlement(client, auth):
    tid = client.get("/api/trades", headers=auth("mo1"), params={"status": "SETTLED", "page_size": 1, "sort": "trade_id"}).json()["items"][0]
    r = client.patch(f"/api/trades/{tid['trade_id']}", headers={**auth("mo1"), "If-Match": str(tid["version"])},
                     json={"changes": {"price": "1"}, "reason": "try"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "E_AMEND_AFTER_SETTLEMENT"


def test_state_machine_rejects_invalid_transition(client, auth):
    tid = client.get("/api/queues/matching", headers=auth("mo1")).json()["items"][0]["trade_id"]
    r = client.post(f"/api/trades/{tid}/transition", headers=auth("mo1"), json={"target": "SETTLED"})
    assert r.status_code == 409 and r.json()["error"]["details"]["rule_id"] == "R099"


def test_interface_duplicates_and_gaps(client, auth):
    sysname = "CCP"
    base = 10_000 + uuid.uuid4().int % 1000 * 10
    h = auth("mo1")
    first = client.post(f"/api/interfaces/{sysname}/messages", headers=h, json={"message_id": f"M{base}-a", "message_type": "CLEARING_ACK",
                                                                               "sequence_no": 1}).json()
    exp = first["next_expected"]
    msgs = [(f"M{base}-3", exp + 2), (f"M{base}-1", exp + 0), (f"M{base}-2", exp + 1), (f"M{base}-2", exp + 1)]
    res = [client.post(f"/api/interfaces/{sysname}/messages", headers=h,
                       json={"message_id": i, "message_type": "CLEARING_ACK", "sequence_no": n}).json() for i, n in msgs]
    assert [r["status"] for r in res] == ["PARKED", "PROCESSED", "PROCESSED", "IGNORED_DUPLICATE"]
    assert res[2]["processed"] == [f"M{base}-2", f"M{base}-3"]


def test_order_pretrade_fill_and_book(client, auth):
    src = client.get("/api/trades/TRD00010", headers=auth("mo1")).json()["trade"]
    o = {"client_id": src["client_id"], "account_id": src["account_id"], "instrument_id": src["instrument_id"],
         "product_type": "EQUITY", "side": "BUY", "order_qty": "1000"}
    r = client.post("/api/orders", headers=auth("trader1"), json={**o, "simulate_fills": [{"qty": "600", "price": "71.00"},
                                                                                          {"qty": "300", "price": "71.10"}]})
    assert r.status_code == 201 and r.json()["order"]["status"] == "PARTIALLY_FILLED"
    oid = r.json()["order"]["order_id"]
    b = client.post(f"/api/orders/{oid}/book", headers=auth("trader1"), json={"counterparty_id": src["counterparty_id"]})
    assert b.status_code == 201, b.text
    assert b.json()["filled_qty"].startswith("900") and b.json()["vwap"] == "71.033333"
    rej = client.post("/api/orders", headers=auth("trader1"), json={**o, "order_type": "LIMIT", "limit_price": "95"}).json()
    assert rej["order"]["status"] == "REJECTED" and rej["pretrade"][0]["rule_id"] == "R008"


def test_sandbox_lifecycle_end_to_end(client, auth):
    sb = client.post("/api/test-support/sandboxes", headers=auth("admin1"), json={}).json()["sandbox_id"]
    body = trade_body(client, auth, trade_date="2026-10-01")
    t = client.post(f"/api/test-support/sandboxes/{sb}/trades", headers=auth("trader1"), json=body).json()["trade"]
    client.post(f"/api/test-support/sandboxes/{sb}/confirmations", headers=auth("mo1"), json={"trade_id": t["trade_id"]})
    client.post(f"/api/test-support/sandboxes/{sb}/process", headers=auth("admin1"), params={"through": "2026-10-10", "start": "2026-10-01"})
    st = client.get(f"/api/test-support/sandboxes/{sb}", headers=auth("admin1")).json()
    assert st["trades"][0]["status"] == "SETTLED"
    assert {i["status"] for i in st["instructions"]} == {"SETTLED"}
    assert client.delete(f"/api/test-support/sandboxes/{sb}", headers=auth("admin1")).json()["deleted_trades"] == 1
    main = client.get("/api/trades", headers=auth("mo1"), params={"q": t["trade_id"]}).json()
    assert main["total"] == 0  # sandbox data never shows in the main blotter


def test_replay_is_idempotent(client, auth):
    r = client.post("/api/admin/replay", headers=auth("admin1"), json={"business_date": "2026-11-06"}).json()
    assert r["new_postings"] == 0 and r["idempotent"] is True


def test_dashboards_have_definitions_and_drilldowns(client, auth):
    names = client.get("/api/dashboards", headers=auth("risk1")).json()["dashboards"]
    assert len(names) == 7
    for n in names:
        d = client.get(f"/api/dashboards/{n}", headers=auth("risk1"))
        assert d.status_code == 200, n
        for k in d.json().get("kpis", []):
            assert k["definition"], (n, k["id"])
