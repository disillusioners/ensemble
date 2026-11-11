"""The MINT gate cells (prompt-assent Phase 3 — architect §4 table 2).

K-2: a mint WITHOUT a live fresh user-origin window writes NO token
row — and does NOT supersede a prior live token. K-3: a group DM (or
any non-private channel, or an absent discriminator) is refused
``mint-requires-private-channel`` with NO token row.

P2's lane file pins the gate's reachability; this file pins the two
matrix cells' NO-SIDE-EFFECT properties end-to-end.
"""

from __future__ import annotations

from daemon.tools.prompt_assent import KILL_SWITCH_ENV
from tests.helpers.prompt_assent_flow import (
    build_flow,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    build_discord_meta,
    build_window,
)


class TestMintWithoutAUserOriginWindow:
    async def test_no_window_writes_no_row(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(window=build_window())
        flow.manager._user_origin_windows = {}
        result = await mint_for(flow)
        assert "reason=no-user-origin-turn" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None

    async def test_a_prior_live_token_is_not_superseded(
        self, monkeypatch
    ) -> None:
        """The K-2 cell's sharpest assertion: a mint refused for want of
        a window must NOT touch the EXISTING live token (no supersede
        flip, no revoke, no attempt-counter motion)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        prior = flow.token_repo.find_live(flow.version_id)
        assert prior is not None and prior.status == "live"

        # The next mint arrives WITHOUT a user-origin window.
        flow.manager._user_origin_windows = {}
        second = await mint_for(flow)
        assert "reason=no-user-origin-turn" in second, second

        after = flow.token_repo.find_live(flow.version_id)
        assert after is not None
        assert after.token_id == prior.token_id
        assert after.status == "live"
        assert after.attempts == prior.attempts
        superseded_rows = [
            r
            for r in (flow.token_repo.find_by_id(prior.token_id),)
            if r is not None
        ]
        assert superseded_rows[0].superseded_at is None

    async def test_a_stale_window_writes_no_row(self, monkeypatch) -> None:
        """The stale-window variant of the K-2 cell (freshness is part
        of the mint gate)."""
        from tests.unit.test_prompt_assent_freshness import (
            PROMPT_ASSENT_WINDOW_FRESHNESS_S,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            window=build_window(
                age_s=PROMPT_ASSENT_WINDOW_FRESHNESS_S + 30, ttl_s=7200
            )
        )
        result = await mint_for(flow)
        assert "reason=user-origin-window-stale" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None


class TestMintInANonPrivateChannel:
    async def test_a_group_dm_is_refused_with_no_row(self, monkeypatch) -> None:
        """K-3 — ``is_dm AND is_group_dm`` is NOT private."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(meta=build_discord_meta(is_dm=True, is_group_dm=True))
        result = await mint_for(flow)
        assert "reason=mint-requires-private-channel" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None

    async def test_an_absent_discriminator_is_refused(self, monkeypatch) -> None:
        """K-3 fail-closed — an ABSENT ``is_group_dm`` ⇒ NOT private."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        meta = build_discord_meta()
        meta.pop("is_group_dm")
        flow = build_flow(meta=meta)
        result = await mint_for(flow)
        assert "reason=mint-requires-private-channel" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None

    async def test_a_guild_channel_is_refused(self, monkeypatch) -> None:
        """K-3 — a guild channel is not a DM at all."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            meta=build_discord_meta(
                is_dm=False,
                is_group_dm=False,
                guild_id="987654",
                channel_type="text",
            )
        )
        result = await mint_for(flow)
        assert "reason=mint-requires-private-channel" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None

    async def test_a_proven_1to1_dm_mints(self, monkeypatch) -> None:
        """The positive leg — ``is_dm=True AND is_group_dm=False`` is
        the ONLY private shape."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        assert flow.token_repo.find_live(flow.version_id) is not None
