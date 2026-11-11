"""The prompt-assent REFUSAL VOCABULARY + the content-free INFO line
(prompt-assent Phase 2, task 2.10).

Three house properties are pinned here:

1. **every refusal is a TYPED CODE in a house envelope** — the REFUSED
   shape (``Error: prompt_assent REFUSED — reason=<code>: <detail>``)
   and the FAILED shape (``Error: prompt_assent FAILED — reason=<code>:
   <detail>``); a refusal is NEVER auto-retried — it is relayed
   verbatim;
2. **the not-ari envelope carries NO refusal code** (the authorization
   class deliberately carries neither a code nor ledger noise);
3. **the ONE frozen INFO line is CONTENT-FREE** — counts and flags only,
   never persona, never diff content, never the raw or grouped token.

The eight codes THIS phase places each get a trigger proof (the lane
matrix in ``tests/unit/test_prompt_assent_lane.py`` pins the trigger
itself; this file pins that the code is REACHABLE and that the envelope
around it is the house one).
"""

from __future__ import annotations

import logging

import pytest

from daemon.tools.prompt_assent import (
    MISMATCH_CODES,
    PHASE2_PLACED_CODES,
    PROMPT_ASSENT_INTERNAL_ERROR,
    REFUSAL_TAXONOMY,
)
from tests.helpers.prompt_assent_tool_fixture import (
    RELAY_INSTANCE_ID,
    FakeManager,
    build_discord_meta,
    build_tools,
    build_window,
)

UNKNOWN_TOKEN = "CONFIRM-ZZZZZZZZ"


def _manager(*, window=None, row=None):
    return FakeManager(
        row=row,
        windows=(
            {RELAY_INSTANCE_ID: window} if window is not None else {}
        ),
    )


def _dm_row(*, content: str = "yes", meta=None):
    from tests.helpers.prompt_assent_tool_fixture import FakeMessageRow

    return FakeMessageRow(
        content=content,
        discord_meta=meta if meta is not None else build_discord_meta(),
    )


class TestTheEnvelopeShapes:
    async def test_a_refusal_carries_the_refused_envelope(self, monkeypatch) -> None:
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "0")
        manager = _manager()
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert result.startswith("Error: prompt_assent REFUSED — reason=")
        assert "reason=lane-disabled" in result

    async def test_the_not_ari_envelope_carries_no_code(self, monkeypatch) -> None:
        """The authorization class carries NO refusal code."""
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager()
        tools = build_tools(manager, agent_id="leader")
        result = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert result.startswith("ERROR: prompt_assent is available to the ari agent only")
        assert "reason=" not in result
        for code in REFUSAL_TAXONOMY:
            assert code not in result, code

    async def test_the_internal_error_uses_the_failed_envelope(
        self, monkeypatch
    ) -> None:
        """The FAILED envelope carries the internal-error code (outside
        the taxonomy — the taxonomy codes are all refusals)."""
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=build_window(), row=_dm_row())
        tools = build_tools(manager)
        # No prompt-version repository on the manager ⇒ the list surface
        # cannot resolve its rows ⇒ the internal-error path.
        result = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert result.startswith("Error: prompt_assent FAILED — reason=")
        assert PROMPT_ASSENT_INTERNAL_ERROR in result
        assert PROMPT_ASSENT_INTERNAL_ERROR not in REFUSAL_TAXONOMY


class TestThePlacedCodes:
    def test_the_mismatch_codes_are_a_subset_of_the_taxonomy(self) -> None:
        assert MISMATCH_CODES <= REFUSAL_TAXONOMY
        assert "token-content-mismatch" in MISMATCH_CODES
        assert "token-channel-mismatch" in MISMATCH_CODES
        assert "token-approver-mismatch" in MISMATCH_CODES

    def test_the_non_mismatch_placed_codes(self) -> None:
        """The placed codes that are NOT mismatch-class — they never
        consume the K-9 budget."""
        assert PHASE2_PLACED_CODES - MISMATCH_CODES == {
            "invalid-arguments",
            "mint-requires-private-channel",
            "mint-live-token-exists",
            "extraction-incomplete",
            "mismatch-budget-exceeded",
        }


class TestTheContentFreeInfoLine:
    async def test_the_info_line_carries_no_content(self, monkeypatch, caplog) -> None:
        """The ONE frozen INFO line — counts and flags ONLY. No persona,
        no diff, no token."""
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=build_window(), row=_dm_row(content="secret sauce"))
        tools = build_tools(manager)
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            await tools["prompt_assent"].ainvoke(
                {"action": "mint", "version_id": "vid-1"}
            )
        lines = [
            rec.getMessage()
            for rec in caplog.records
            if rec.name == "daemon.tools.prompt_assent"
        ]
        assert lines, "the INFO line must fire on every terminal path"
        for line in lines:
            assert line.startswith("prompt_assent outcome="), line
            assert "secret sauce" not in line
            assert "CONFIRM-" not in line, "the token never reaches the log"
            assert "count=" in line
            assert "flags=" in line

    async def test_the_info_line_fires_on_a_refusal_too(
        self, monkeypatch, caplog
    ) -> None:
        from daemon.tools.prompt_assent import KILL_SWITCH_ENV

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=None)
        tools = build_tools(manager)
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            await tools["prompt_assent"].ainvoke(
                {"action": "mint", "version_id": "vid-1"}
            )
        lines = [
            rec.getMessage()
            for rec in caplog.records
            if rec.name == "daemon.tools.prompt_assent"
        ]
        assert lines
        assert any("outcome=refused" in line for line in lines)
