"""The agents: tools, guardrails, the engine loop, the Claude brain (with a fake client) and the evaluation.

These run the offline planner and a scripted fake of the Anthropic API, so they cost nothing and need no key.
"""

import json
from types import SimpleNamespace

import pytest
from agents import AuditLog, ClaudeBrain, LakehouseTools, SnapshotBackend, run_agent
from agents.evals import evaluate
from agents.tools import ToolError


@pytest.fixture(scope="module")
def backend():
    return SnapshotBackend()


@pytest.fixture(scope="module")
def tools(backend):
    return LakehouseTools(backend)


# ------------------------------------------------------------------ tools
def test_tools_reject_malformed_ids_and_writes(tools):
    with pytest.raises(ToolError):
        tools.get_customer_profile("C1' OR '1'='1")
    with pytest.raises(ToolError):
        tools.get_transactions(["T1; DROP TABLE transactions"])
    for bad in ("DROP TABLE transactions", "COPY transactions TO 'x.csv'", "SELECT 1; SELECT 2",
                "SELECT * FROM read_csv_auto('/etc/passwd')"):
        with pytest.raises(ToolError):
            tools.run_sql(bad)


def test_tool_results_are_small_and_have_no_contact_details(tools):
    out = tools.run_sql("SELECT * FROM transactions")
    assert out["row_count"] == 1000 and len(out["rows"]) == 25 and out["truncated"]
    profile = tools.get_customer_profile("C000212")["profile"]
    assert not {"email", "phone", "first_name", "last_name"} & set(profile)


def test_delivery_evidence_is_available_offline(tools):
    ev = tools.inspect_delivery("20261014")
    assert ev["files"]["transactions.jsonl"]["checksum_matches"] is False


# ----------------------------------------------------------------- engine
def test_engine_traces_audits_and_stops_at_the_submit_tool(backend):
    audit = AuditLog()
    alert_id = backend.sql("SELECT alert_id FROM aml_alerts WHERE rule_code = 'STRUCTURING' LIMIT 1").iloc[0, 0]
    run = run_agent("aml_investigator", backend, audit=audit, alert_id=alert_id)
    assert run.status == "completed" and run.output["recommendation"] == "ESCALATE"
    assert run.steps[-1].kind == "final"
    assert [s.tool for s in run.steps if s.kind == "tool"][-1] == "submit_case"
    events = audit.events(correlation_id=run.run_id)
    assert {e.event_type for e in events} == {"agent_run_started", "agent_tool_call", "agent_run_finished"}
    assert audit.verify_chain() == (True, None)


def test_agents_cannot_call_tools_outside_their_belt(backend):
    from agents import build_agent

    agent = build_agent("ask_ledgerline", backend)
    assert set(agent.tools) == {"list_tables", "run_sql", "submit_answer"}


# ------------------------------------------------------------- evaluation
def test_offline_planner_passes_the_answer_key(backend):
    results = {r.agent: r for r in evaluate(backend, agents=("aml_investigator", "data_steward"))}
    aml = results["aml_investigator"]
    assert aml.passed == len(aml.cases) == 8, aml.cases
    closes = [c for c in aml.cases if c["got"] == "CLOSE"]
    assert len(closes) == 1 and closes[0]["case"].startswith("RAPID_MOVEMENT")
    assert results["data_steward"].passed == 1, results["data_steward"].cases


def test_offline_ask_answers_known_questions_and_admits_the_rest(backend):
    known = run_agent("ask_ledgerline", backend, question="Show the daily transaction volume trend")
    assert known.output["chart"]["type"] == "line" and known.output["sql"]
    unknown = run_agent("ask_ledgerline", backend, question="What's the weather in Paris?")
    assert unknown.output["sql"] == "" and "offline mode" in unknown.output["answer"]


# ------------------------------------------------- Claude brain, faked API
class FakeMessages:
    """Replays a scripted conversation in the Anthropic response shape and records each request."""

    def __init__(self, turns):
        self.turns, self.requests = list(turns), []

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        blocks, stop = self.turns.pop(0)
        return SimpleNamespace(content=blocks, stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def _tool(id_, name, args):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


def _text(t):
    return SimpleNamespace(type="text", text=t)


def test_claude_brain_loop_recovers_from_a_sql_error(backend):
    fake = FakeMessages([
        ([_text("Let me count alerts."), _tool("t1", "run_sql", {"query": "SELECT rule FROM aml_alerts"})], "tool_use"),
        ([_text("Column is rule_code."),
          _tool("t2", "run_sql", {"query": "SELECT rule_code, COUNT(*) AS alerts FROM aml_alerts GROUP BY rule_code"})],
         "tool_use"),
        ([_tool("t3", "submit_answer", {"answer": "3 rules.", "sql": "SELECT rule_code, COUNT(*) AS alerts FROM "
                                        "aml_alerts GROUP BY rule_code", "chart": {"type": "bar", "x": "rule_code",
                                                                                  "y": "alerts"}})], "tool_use"),
    ])
    brain = ClaudeBrain(model="claude-opus-5-5", client=SimpleNamespace(beta=SimpleNamespace(messages=fake)))
    run = run_agent("ask_ledgerline", backend, brain, question="How many alerts per rule?")
    assert run.status == "completed" and run.output["answer"] == "3 rules."
    tool_steps = [s for s in run.steps if s.kind == "tool"]
    assert tool_steps[0].ok is False and "rule" in tool_steps[0].result["error"]
    assert tool_steps[1].ok is True
    # The failed call went back to the model as an error result, in the same request shape the API expects.
    second = fake.requests[1]["messages"][-1]["content"][0]
    assert second["is_error"] is True and second["tool_use_id"] == "t1"
    assert fake.requests[0]["tools"][0]["strict"] is True
    assert run.input_tokens == 3000 and run.cost_usd == pytest.approx(3 * (1000 * 4 + 200 * 20) / 1e6)


def test_claude_brain_is_stopped_by_the_step_limit(backend):
    loop = [([_tool(f"t{i}", "list_tables", {})], "tool_use") for i in range(20)]
    brain = ClaudeBrain(model="claude-opus-5-5", client=SimpleNamespace(beta=SimpleNamespace(
        messages=FakeMessages(loop))))
    run = run_agent("ask_ledgerline", backend, brain, question="loop forever")
    assert run.status == "step_limit" and run.tool_calls == 8


def test_recorded_runs_round_trip(backend):
    from agents.engine import AgentRun

    run = run_agent("data_steward", backend)
    again = AgentRun.from_dict(json.loads(json.dumps(run.to_dict())))
    assert again.output == run.output and len(again.steps) == len(run.steps)
