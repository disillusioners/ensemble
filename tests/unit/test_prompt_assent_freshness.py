"""The freshness bound (prompt-assent Phase 3 — task 3.4's pinned legs).

D-6 / J-2: the factor-2 window must be FRESH — ``stamped_at`` within
the configured bound (default 600 s) of the tool call, IN ADDITION to
the window's ``expires_at``. Absent/unparseable ⇒ STALE, never valid.
The bound applies to MINT and APPROVE alike (K-2).

The gate implementation is P2-owned (O-E); this file PINS it — the
plan's language: *"this phase specifies and pins it"*.
"""

from __future__ import annotations

import pytest

from daemon.tools.prompt_assent import (
    KILL_SWITCH_ENV,
    PROMPT_ASSENT_WINDOW_FRESHNESS_S,
)
from tests.helpers.prompt_assent_flow import (
    build_flow,
    decide,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    RELAY_INSTANCE_ID,
    build_window,
)


class TestTheFreshnessBound:
    async def test_a_stale_window_refuses_mint_and_approve_alike(
        self, monkeypatch
    ) -> None:
        """K-2/D-6 — the bound applies to BOTH verbs: a 1200 s-old stamp
        (bound 600 s) refuses mint AND approve with the same code."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        stale = build_window(
            age_s=PROMPT_ASSENT_WINDOW_FRESHNESS_S + 600, ttl_s=7200
        )
        flow = build_flow(window=stale)
        mint = await mint_for(flow)
        assert "reason=user-origin-window-stale" in mint, mint
        assert flow.token_repo.find_live(flow.version_id) is None

        # The approve side: minted inside a fresh window, then the NEXT
        # human turn is stale.
        flow2 = build_flow()
        mint2 = await mint_for(flow2)
        token = mint2.split("token: ", 1)[1].splitlines()[0]
        flow2.manager._user_origin_windows[RELAY_INSTANCE_ID] = build_window(
            age_s=PROMPT_ASSENT_WINDOW_FRESHNESS_S + 1, ttl_s=7200
        )
        echo(flow2, token)
        result = await decide(flow2, token=token)
        assert "reason=user-origin-window-stale" in result, result
        assert flow2.repo.get_version(flow2.version_id).state == "pending_approval"

    async def test_an_absent_stamp_is_stale_never_valid(
        self, monkeypatch
    ) -> None:
        """Fail-closed: a window whose ``stamped_at`` is MISSING is
        stale — never valid."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        window = build_window()
        window.pop("stamped_at")
        flow = build_flow(window=window)
        result = await mint_for(flow)
        assert "reason=user-origin-window-stale" in result, result

    async def test_an_unparseable_stamp_is_stale_never_valid(
        self, monkeypatch
    ) -> None:
        """Fail-closed: an UNPARSEABLE ``stamped_at`` is stale — never
        valid (mirrors D-6's absent-stamp rule)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        window = build_window()
        window["stamped_at"] = "not-a-timestamp"
        flow = build_flow(window=window)
        result = await mint_for(flow)
        assert "reason=user-origin-window-stale" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None

    async def test_an_unparseable_window_expiry_is_expired_never_valid(
        self, monkeypatch
    ) -> None:
        """The EXPIRY leg is equally fail-closed: an unparseable
        ``expires_at`` is expired — never valid."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        window = build_window()
        window["expires_at"] = "garbage"
        flow = build_flow(window=window)
        result = await mint_for(flow)
        assert "reason=user-origin-window-expired" in result, result

    async def test_inside_the_bound_is_fresh(self, monkeypatch) -> None:
        """The positive leg — a stamp inside the bound (599 s) passes
        the freshness leg.

        FLAKE REPAIR (phase 4, disclosed): the TTL rides the STAMP in
        ``build_window`` (``expires = stamped + ttl``), so a 599 s
        backdate at the default 600 s ttl left a ONE-SECOND expiry
        margin — under load the mint landed after the expiry and the
        positive leg flapped (observed twice in the phase-4 pack runs).
        The ttl is widened to ``bound + backdate`` so the expiry stays a
        full bound away: the cell still pins exactly what it pinned
        (inside the freshness bound ⇒ NOT stale), with a margin that
        cannot be consumed by scheduler jitter.
        """
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        fresh = build_window(
            age_s=PROMPT_ASSENT_WINDOW_FRESHNESS_S - 1,
            ttl_s=PROMPT_ASSENT_WINDOW_FRESHNESS_S
            + (PROMPT_ASSENT_WINDOW_FRESHNESS_S - 1),
        )
        flow = build_flow(window=fresh)
        mint = await mint_for(flow)
        assert "reason=user-origin-window-stale" not in mint, mint
        assert "prompt_assent mint" in mint, mint
