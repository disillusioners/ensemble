"""The AR-1 binding cells (prompt-assent Phase 3 — tasks 3.4 legs).

* wrong user of the SAME 1:1 channel → ``token-approver-mismatch``
  (K-8 — daemon-derived ``mint_user_id`` equality, both sides);
* different channel → ``token-channel-mismatch`` (AR-1 leg 1);
* a channel match with a different user is ``token-approver-mismatch``
  — NEVER ``token-channel-mismatch`` (distinct codes);
* neither side is ever tool-supplied: the input schema carries NO
  identity field (the R3 analogue), and the durable counter increments
  on each mismatch verdict.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, col, select

from daemon.repositories.prompt_assent.models import PromptAssentToken
from daemon.tools.prompt_assent import (
    KILL_SWITCH_ENV,
    PROMPT_ASSENT_REVOKE_THRESHOLD,
)


@pytest.fixture(autouse=True)
def _clean_budget():
    """The K-9 counter is in-RAM — every test starts from a clean slate
    (test-order independence across the matrix files)."""
    from daemon.tools.prompt_assent import reset_mismatch_budget

    reset_mismatch_budget()
    yield
    reset_mismatch_budget()

from tests.helpers.prompt_assent_flow import (
    build_flow,
    decide,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    CHANNEL_ID,
    SOURCE_ID,
    USER_ID,
    FakeMessageRow,
    build_discord_meta,

)


class TestTheChannelLeg:
    async def test_a_different_channel_is_refused(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        # A DIFFERENT 1:1 channel — same user, token echoed.
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(channel_id="another-channel"),
        )
        result = await decide(flow, token=token)
        assert "reason=token-channel-mismatch" in result, result
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"
        after = flow.token_repo.find_by_id(live.token_id)
        assert after.status == "live", "a channel mismatch never burns"
        assert after.attempts == 1, "the DURABLE counter increments"

    async def test_a_divergent_source_is_also_a_channel_mismatch(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(),
            source="other-bot",
        )
        result = await decide(flow, token=token)
        assert "reason=token-channel-mismatch" in result, result


class TestTheUserLeg:
    async def test_a_different_human_in_the_same_channel_is_refused(
        self, monkeypatch
    ) -> None:
        """K-8 — the second occupant of the bound channel satisfies the
        channel + private + echo legs but NOT the user equality."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(user_id="999999999"),
        )
        result = await decide(flow, token=token)
        assert "reason=token-approver-mismatch" in result, result
        assert "reason=token-channel-mismatch" not in result, (
            "a channel match with a different user is an APPROVER "
            "mismatch — the codes are distinct"
        )
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"
        after = flow.token_repo.find_by_id(live.token_id)
        assert after.status == "live" and after.attempts == 1

    async def test_a_missing_approving_identity_is_extraction_incomplete(
        self, monkeypatch
    ) -> None:
        """D-5 — the nested identity is REQUIRED at approve too; a
        top-level ``user_id`` is never consulted."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        meta = build_discord_meta()
        meta.pop("user_id")
        meta["user_id_top_level_decoy"] = "1337"
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}", discord_meta=meta
        )
        result = await decide(flow, token=token)
        assert "reason=extraction-incomplete" in result, result

    async def test_the_right_user_in_the_right_channel_is_accepted(
        self, monkeypatch
    ) -> None:
        """The positive leg — the mint-witnessed human in the
        mint-witnessed channel approves."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        echo(flow, token)
        result = await decide(flow, token=token)
        assert "prompt_assent approve — OK" in result, result


class TestNeitherSideIsToolSupplied:
    def test_the_input_schema_carries_no_identity_field(self) -> None:
        """The R3 analogue — the tool's args schema has NO approver
        identity, source, channel, message id or user id field: the
        gate's identity is ALWAYS daemon-derived, so there is nothing
        to forge."""
        from tests.helpers.prompt_assent_tool_fixture import (
            RELAY_INSTANCE_ID,
            FakeManager,
            build_tools,
        )

        flow_manager = FakeManager()
        tools = build_tools(flow_manager)
        schema = tools["prompt_assent"].args_schema.model_fields
        forbidden = {
            "user_id", "source", "source_id", "channel", "channel_id",
            "message_id", "approver", "approver_subject", "guild",
            "guild_id", "attestation", "user_name", "external_user_id",
        }
        assert forbidden.isdisjoint(set(schema)), (
            f"identity-shaped tool args exist: {forbidden & set(schema)}"
        )
        # The full arg surface is exactly the ruled one.
        assert set(schema) == {
            "action", "version_id", "token", "user_confirmed",
            "reject_reason", "limit", "force",
        }

    def test_the_mint_context_never_reads_tool_args(self) -> None:
        """The mint-context derivation reads ONLY the window's message
        row — a structural pin over the derivation source."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2]
            / "daemon/tools/prompt_assent.py"
        ).read_text(encoding="utf-8")
        derive = source.split("def _derive_context", 1)[1].split(
            "\ndef ", 1
        )[0]
        # The derivation's inputs are the manager, the ROW and the
        # WINDOW — the tool-argument names never appear.
        for arg in ("version_id", "token", "reject_reason", "force"):
            assert arg not in derive, (
                f"the mint-context derivation reads the tool arg {arg!r}"
            )


class TestTheDurableCounterOnMismatch:
    async def test_the_lockout_fires_after_five_mismatches(
        self, monkeypatch
    ) -> None:
        """D-3/D-4 — the mismatch counter increments per verdict; at the
        threshold the live token is REVOKED (the lockout), and the next
        attempt reads ``token-locked``."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        # A different human in the SAME channel — every attempt is an
        # approver mismatch (the budget is not consulted for the FIRST
        # five: the in-RAM budget is 10).
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"yes {token}",
            discord_meta=build_discord_meta(user_id="999999999"),
        )
        for i in range(PROMPT_ASSENT_REVOKE_THRESHOLD):
            result = await decide(flow, token=token)
            assert "reason=token-approver-mismatch" in result, (i, result)
        revoked = flow.token_repo.find_by_id(live.token_id)
        assert revoked.status == "revoked", "the lockout revoked the token"
        assert revoked.attempts == PROMPT_ASSENT_REVOKE_THRESHOLD

        # The next attempt — even by the RIGHT human — reads the lockout.
        echo(flow, token)
        result = await decide(flow, token=token)
        assert "reason=token-locked" in result, result
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"
