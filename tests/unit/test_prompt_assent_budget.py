"""The K-9 hourly mismatch budget (prompt-assent Phase 3 — D-3b).

The budget is IN-RAM, keyed by ``(relay_instance_id, hour-bucket)``:
the 11th mismatch within the hour by ONE relay instance is refused
``mismatch-budget-exceeded``; a different relay instance has its own
budget; a successful approval never resets it; it is NOT persisted
(the DURABLE leg is the PG per-token ``attempts`` + lockout).

The mechanism is P2-owned (O-E); this file PINS the ruled semantics.
"""

from __future__ import annotations

import pytest

from daemon.tools.prompt_assent import (
    KILL_SWITCH_ENV,
    PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET,
    _MISMATCH_BUDGET,
    _budget_count,
    _hour_bucket,
    reset_mismatch_budget,
)
from tests.helpers.prompt_assent_flow import (
    build_flow,
    decide,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    RELAY_INSTANCE_ID,
    build_tools,
)


@pytest.fixture(autouse=True)
def _clean_budget():
    reset_mismatch_budget()
    yield
    reset_mismatch_budget()


class TestBudgetExhaustion:
    async def test_the_eleventh_mismatch_is_refused(self, monkeypatch) -> None:
        """The budget cell's shape: a presented token that matches NO
        row (``token-mismatch`` — the no-row mismatch shape, where the
        durable counter has nothing to count and the in-RAM budget is
        THE counter)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint
        unknown = "CONFIRM-ZZZZZZZZ"
        echo(flow, unknown)
        for i in range(PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET):
            result = await decide(flow, token=unknown)
            assert "reason=token-mismatch" in result, (i, result)
        overflow = await decide(flow, token=unknown)
        assert "reason=mismatch-budget-exceeded" in overflow, overflow
        assert _budget_count(RELAY_INSTANCE_ID, _hour_bucket()) == (
            PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET
        ), "the exhausted budget consumed the LAST mismatch verdict too"

    async def test_a_different_relay_instance_has_its_own_budget(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        await mint_for(flow)
        unknown = "CONFIRM-ZZZZZZZZ"
        echo(flow, unknown)
        for _ in range(PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET):
            await decide(flow, token=unknown)
        # A second relay instance — its OWN window, its OWN counter.
        from tests.helpers.prompt_assent_tool_fixture import build_window

        flow.manager._user_origin_windows["inst-relay-2"] = build_window()
        other = build_tools(flow.manager, instance_id="inst-relay-2")
        result = await other["prompt_assent"].ainvoke(
            {
                "action": "approve",
                "version_id": flow.version_id,
                "token": unknown,
                "user_confirmed": True,
            }
        )
        assert "reason=token-mismatch" in result, result
        assert "mismatch-budget-exceeded" not in result

    async def test_a_success_does_not_reset_the_budget(
        self, monkeypatch
    ) -> None:
        """O-F — only mismatches count; a successful approval does not
        clear the counter."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        await mint_for(flow)
        unknown = "CONFIRM-ZZZZZZZZ"
        echo(flow, unknown)
        for _ in range(3):
            await decide(flow, token=unknown)
        assert _budget_count(RELAY_INSTANCE_ID, _hour_bucket()) == 3

        # A SECOND item, approved legitimately by the right human.
        flow2 = build_flow(engine=flow.engine)
        mint2 = await mint_for(flow2)
        token2 = mint2.split("token: ", 1)[1].splitlines()[0]
        echo(flow2, token2)
        ok = await decide(flow2, token=token2)
        assert "prompt_assent approve — OK" in ok, ok
        assert _budget_count(RELAY_INSTANCE_ID, _hour_bucket()) == 3, (
            "the success must NOT reset the counter"
        )

    async def test_the_counter_rolls_over_with_the_hour_bucket(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        bucket = _hour_bucket()
        assert _budget_count(RELAY_INSTANCE_ID, bucket) == 0
        for _ in range(3):
            _MISMATCH_BUDGET[(RELAY_INSTANCE_ID, bucket)] = _MISMATCH_BUDGET.get(
                (RELAY_INSTANCE_ID, bucket), 0
            ) + 1
        assert _budget_count(RELAY_INSTANCE_ID, bucket) == 3
        # The NEXT bucket is untouched (the rollover resets per bucket).
        assert _budget_count(RELAY_INSTANCE_ID, bucket + 1) == 0

    def test_the_budget_is_in_ram_only(self) -> None:
        """D-3b — the budget is a module-level dict; nothing durable
        backs it (the token table has no budget column)."""
        assert isinstance(_MISMATCH_BUDGET, dict)
        from daemon.repositories.prompt_assent.models import (
            PromptAssentToken,
        )

        columns = set(PromptAssentToken.__table__.columns.keys())
        assert not any("budget" in c or "mismatch" in c for c in columns)
