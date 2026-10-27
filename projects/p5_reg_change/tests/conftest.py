import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent.parent.parent)]

import pytest  # noqa: E402

from regradar.agents import run_assessment  # noqa: E402
from regradar.corpus import get_pair  # noqa: E402
from regradar.report import assess_all  # noqa: E402


@pytest.fixture(scope="session")
def all_assessments():
    """Read-only: one offline assessment per circular."""
    return assess_all()


@pytest.fixture
def fc14():
    return run_assessment(get_pair("NPCA-FC-2026-14"))


class FakeLLM:
    """Scripted stand-in for DatabricksLLM: returns queued replies and records what it was sent."""

    label = "Fake · test-model"

    def __init__(self, replies=None, chat_replies=None, fail=False):
        from regradar.llm import Usage

        self.replies = list(replies or [])
        self.chat_replies = list(chat_replies or [])
        self.fail = fail
        self.sent = []
        self.usage = Usage()

    def complete(self, system, user, max_tokens=1200):
        from regradar.llm import LLMError

        self.sent.append((system, user))
        if self.fail:
            raise LLMError("endpoint down")
        reply = self.replies.pop(0) if self.replies else '{"status": "COVERED", "policy_ref": null, "rationale": ""}'
        return reply(user) if callable(reply) else reply

    def chat(self, messages, tools=None, max_tokens=1200, temperature=0.0):
        self.sent.append(messages)
        return self.chat_replies.pop(0)


@pytest.fixture
def fake_llm():
    return FakeLLM
