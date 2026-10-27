import json

import pytest

from regradar.llm import DatabricksLLM, LLMError, from_secrets, grounding_check, parse_json, review_finding


class FakeHTTP:
    def __init__(self, endpoints, replies):
        self.endpoints = endpoints
        self.replies = replies  # endpoint -> reply text or Exception
        self.calls = []

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        if url.endswith("/api/2.0/serving-endpoints"):
            return {"endpoints": [{"name": n} for n in self.endpoints]}
        name = url.split("/serving-endpoints/")[1].split("/")[0]
        reply = self.replies[name]
        if isinstance(reply, Exception):
            raise reply
        return {"choices": [{"message": {"content": reply}}], "usage": {"prompt_tokens": 12, "completion_tokens": 3}}


def test_discover_skips_failing_endpoints_and_hides_token():
    http = FakeHTTP(["databricks-meta-llama-3-3-70b-instruct", "databricks-claude-sonnet-4-5", "other"],
                    {"databricks-meta-llama-3-3-70b-instruct": LLMError("HTTP 403"),
                     "databricks-claude-sonnet-4-5": "OK"})
    llm = DatabricksLLM.discover("adb-1.cloud.databricks.com", "dapi-secret", http=http)
    assert llm.endpoint == "databricks-claude-sonnet-4-5"
    assert "dapi-secret" not in repr(llm)
    method, url, body = http.calls[-1]
    assert url == "https://adb-1.cloud.databricks.com/serving-endpoints/databricks-claude-sonnet-4-5/invocations"
    assert body["messages"][0]["role"] == "system" and body["temperature"] == 0.0
    assert llm.usage.calls == 1 and llm.usage.input_tokens == 12


def test_discover_reports_when_nothing_works():
    http = FakeHTTP(["databricks-gpt-oss-120b"], {"databricks-gpt-oss-120b": LLMError("HTTP 404")})
    with pytest.raises(LLMError, match="No usable"):
        DatabricksLLM.discover("h", "t", http=http)


def test_from_secrets_uses_environment(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://ws.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "t")
    monkeypatch.setenv("DATABRICKS_LLM_ENDPOINT", "my-endpoint")
    assert from_secrets().endpoint == "my-endpoint"


def test_parse_json_and_grounding():
    assert parse_json('Sure:\n```json\n{"status": "CONFLICT"}\n```') == {"status": "CONFLICT"}
    with pytest.raises(LLMError):
        parse_json("no json here")
    ok, bad = grounding_check("Policy says 30 days; rule says 15 days (NPCA-FC-2026-14 §4.1, 2027-03-01).",
                              ["within 15 calendar days", "no later than 30 calendar days"])
    assert ok and bad == []
    ok, bad = grounding_check("The deadline is 21 days.", ["within 15 days"])
    assert not ok and bad == ["21"]


def test_reviewer_agreement_disagreement_and_invalid(fc14, fake_llm):
    f = next(x for x in fc14.findings if x.provision.pid == "NPCA-FC-2026-14 §4.1")
    agree = review_finding(fake_llm([json.dumps({"status": "CONFLICT", "policy_ref": "AML-002 §4",
                                                 "rationale": "Policy allows 30 days; rule needs 15."})]), f)
    assert agree.agrees and agree.grounded and agree.policy_ref == "AML-002 §4"
    disagree = review_finding(fake_llm([json.dumps({"status": "COVERED", "policy_ref": "XYZ §9",
                                                    "rationale": "Fine within 99 days."})]), f)
    assert not disagree.agrees and disagree.policy_ref is None and disagree.unsupported == ["99"]
    bad = review_finding(fake_llm(['{"status": "MAYBE"}']), f)
    assert bad.error and bad.agrees
    down = review_finding(fake_llm(fail=True), f)
    assert down.error == "endpoint down"
