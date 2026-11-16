"""28 hand-calculated examples: production engine and independent oracle must both equal the hand result."""

import pytest

from testkit.worked_examples import EXAMPLES


@pytest.mark.parametrize("ex", EXAMPLES, ids=[e["id"] for e in EXAMPLES])
def test_worked_example(ex):
    assert str(ex["prod"]()) == ex["expected"], f"production engine: {ex['working']}"
    assert str(ex["oracle"]()) == ex["expected"], f"oracle: {ex['working']}"


def test_at_least_twenty_examples():
    assert len(EXAMPLES) >= 20
