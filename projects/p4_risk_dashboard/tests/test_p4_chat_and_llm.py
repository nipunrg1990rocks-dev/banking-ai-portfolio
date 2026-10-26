"""Conversational Q&A: the offline router, and Claude live mode with a fake client."""

from types import SimpleNamespace

import pytest

from riskdash.chat import EXAMPLES, RiskChat, extract_amount
from riskdash.llm import MAX_TOOL_ROUNDS, ClaudeRiskAgent, grounding_check, tool_definitions
from riskdash.synthetic import AS_OF
from riskdash.tools import TOOL_NAMES, RiskTools


@pytest.fixture(scope="module")
def chat(agent, briefing):
    return RiskChat(agent, briefing)


@pytest.mark.parametrize("question,intent,metric_id", [
    ("What are the top risks this week?", "summary", None),
    ("Why did reconciliation breaks go up?", "why", "RECON_OPEN_BREAKS"),
    ("When will the AML alert KRI get worse?", "forecast", "KRI_AML_ALERTS_OVER_30D"),
    ("What if we add 3 handlers to Payments Ops?", "whatif", "EXC_BACKLOG"),
    ("What if another USD 300m of deposits leaves?", "whatif", "KRI_LCR_PCT"),
    ("what if we lose 2m on a fraud", "whatif", "KRI_OPLOSS_YTD_USD_M"),
    ("Why is the data-quality score amber?", "why", "DQ_HEALTH_SCORE"),
    ("how is CET1?", "status", "KRI_CET1_PCT"),
    ("what is the trend in the sla breach rate", "trend", "EXC_SLA_BREACH_PCT"),
    ("show me the open recon breaks", "list", "RECON_OPEN_BREAKS"),
])
def test_chat_routes_questions(chat, question, intent, metric_id):
    a = chat.ask(question)
    assert (a.intent, a.metric_id) == (intent, metric_id)
    assert len(a.text) > 40


def test_every_example_question_gets_an_answer(chat):
    for q in EXAMPLES:
        assert "could not tell" not in chat.ask(q).text


def test_why_answer_cites_the_root_cause(chat):
    text = chat.ask("Why did reconciliation breaks go up?").text
    assert "CHG-2026-1043" in text and "NOSTRO_EUR" in text


def test_what_if_uses_the_amount_asked(chat):
    a = chat.ask("What if another USD 300m of deposits leaves?")
    assert a.data["what_if"]["outflow_usd_m"] == 300


def test_unknown_question_offers_examples(chat):
    text = chat.ask("banana").text
    assert "could not tell" in text and EXAMPLES[0] in text


@pytest.mark.parametrize("text,expected", [("lose 2m", 2.0), ("USD 300m", 300.0), ("add 3 handlers", 3.0),
                                           ("1.5 million", 1.5), ("no number", None)])
def test_extract_amount(text, expected):
    assert extract_amount(text) == expected


# ------------------------------------------------------------- grounding
def test_grounding_accepts_numbers_from_tools():
    results = [{"value": 117.6, "limit": 110.0, "ratio": 0.0256}]
    ok, bad = grounding_check("LCR is 117.6% against a 110% limit; 2.56% of reviews are late.", results)
    assert ok, bad


def test_grounding_rejects_invented_numbers():
    ok, bad = grounding_check("LCR is 117.6% and will hit 104.2% next week.", [{"value": 117.6}])
    assert not ok and bad == ["104.2"]


def test_grounding_ignores_dates_ids_and_small_counts():
    ok, bad = grounding_check("Since 2026-10-05, CHG-2026-1043 and RAS-004 s.6 apply; two of 3 teams.", [{}])
    assert ok, bad


# --------------------------------------------------------- Claude loop
def _block(**kw):
    return SimpleNamespace(**kw)


def _response(stop, content):
    return SimpleNamespace(stop_reason=stop, content=content, usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class FakeClient:
    """Plays back scripted model turns and records every request."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.turns.pop(0)


def test_tool_definitions_cover_every_tool():
    names = [t["name"] for t in tool_definitions()]
    assert sorted(names) == sorted(TOOL_NAMES)
    for t in tool_definitions():
        assert t["input_schema"]["type"] == "object"


def test_claude_loop_runs_tools_and_grounds_the_answer(store):
    client = FakeClient([
        _response("tool_use", [_block(type="tool_use", id="t1", name="get_metric",
                                      input={"metric_id": "KRI_LCR_PCT"})]),
        _response("end_turn", [_block(type="text", text="LCR is 117.6%, below the 120% early-warning trigger.")]),
    ])
    agent = ClaudeRiskAgent(RiskTools(store, AS_OF), client=client, model="test-model")
    answer = agent.ask("How is liquidity?")
    assert answer.grounded and answer.tool_calls == 1 and answer.text.startswith("LCR is 117.6%")
    assert answer.input_tokens == 200
    first = client.requests[0]
    assert first["model"] == "test-model" and "2026-10-09" in first["system"]
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t1" and "117.6" in tool_result["content"]


def test_claude_answer_with_an_invented_number_is_flagged(store):
    client = FakeClient([
        _response("tool_use", [_block(type="tool_use", id="t1", name="get_metric",
                                      input={"metric_id": "KRI_LCR_PCT"})]),
        _response("end_turn", [_block(type="text", text="LCR is 117.6% and will be 98.3% by Friday.")]),
    ])
    answer = ClaudeRiskAgent(RiskTools(store, AS_OF), client=client, model="m").ask("Liquidity?")
    assert not answer.grounded and "98.3" in answer.unsupported


def test_claude_loop_reports_bad_tool_calls_and_stops(store):
    bad = _response("tool_use", [_block(type="tool_use", id="x", name="delete_everything", input={})])
    client = FakeClient([bad] * (MAX_TOOL_ROUNDS + 1))
    answer = ClaudeRiskAgent(RiskTools(store, AS_OF), client=client, model="m").ask("Do something bad")
    assert answer.error and not answer.grounded
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and "unknown tool" in result["content"]


def test_claude_refusal_is_not_shown_as_an_answer(store):
    client = FakeClient([_response("refusal", [])])
    answer = ClaudeRiskAgent(RiskTools(store, AS_OF), client=client, model="m").ask("?")
    assert answer.error and answer.text == ""
