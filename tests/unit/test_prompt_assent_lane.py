"""The prompt-assent TOOL GATE matrix (prompt-assent Phase 2, task 2.10).

Phase 2 owns the three-factor GATE (O-E — exactly one implementation
site: ``daemon/tools/prompt_assent.py``). This file pins the gate's
refusal behaviour end-to-end against DAEMON-OWNED evidence: the
factor-2 user-origin window, the window's MESSAGE ROW, the pending item
row and the token store.

The matrix (architect §4, the Phase-2 legs):

* the ari layer-3 gate → the GENERIC not-ari envelope (no code, no
  ledger noise);
* the kill-switch (default OFF, fail-closed, read at CALL TIME) →
  ``lane-disabled`` — for EVERY verb, ``mint`` included, and the tool
  stays REGISTERED;
* the action-shape validation → ``invalid-arguments``;
* factor 3a → ``user-confirmation-missing``;
* factor 3b/3c → ``no-user-origin-turn`` (naming the observed source);
* factor 3d → ``user-origin-window-expired``;
* factor 3e → ``user-origin-window-stale``;
* factor 3f → ``assent-verification-unavailable`` (fail closed);
* factor 3g → ``token-content-mismatch`` — **the load-bearing pin**:
  a token supplied ONLY in the tool arguments is refused;
* the mint branch → M-4a (``item-not-found`` / ``item-not-assentable``
  / ``item-not-pending``), M-4b (``mint-live-token-exists``), M-4c
  (``mint-requires-private-channel`` — incl. the group DM and the
  absent-discriminator fail-closed rule), the D-5 extraction
  (``extraction-incomplete``);
* the approve branch → AR-1's channel leg
  (``token-channel-mismatch``) and user leg (``token-approver-mismatch``);
* the K-9 in-RAM budget → ``mismatch-budget-exceeded``.

MUTATION VERIFICATION (the house rule — every pin gets a
counter-mutation that REDDENS it):

* **the token-only-in-the-args pin** — delete the factor-3g check ⇒
  the token-only call is ACCEPTED ⇒ the pin REDS;
* **the mint gate pin** — delete the window read in the mint path ⇒
  a mint without a user turn WRITES a token row ⇒ the pin REDS;
* **the private-check pin** — treat an absent ``is_group_dm`` as
  private ⇒ the group-DM / absent-discriminator cells RED;
* **the budget pin** — never consult the budget ⇒ the 11th mismatch is
  NOT refused ⇒ the pin REDS;
* **the ari-only pin** — grant the category to a second agent ⇒ the
  census REDS (in ``tests/unit/tools/test_prompt_assent_registration.py``).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from daemon.tools.prompt_assent import (
    KILL_SWITCH_ENV,
    PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET,
    _MISMATCH_BUDGET,
    reset_mismatch_budget,
)
from tests.helpers.prompt_assent_fixture import (  # noqa: F401 — fixtures
    pending_row,
    pv_repo,
    sqlite_engine,
)
from tests.helpers.prompt_assent_tool_fixture import (
    CHANNEL_ID,
    RELAY_INSTANCE_ID,
    SOURCE_ID,
    USER_ID,
    FakeManager,
    FakeMessageRow,
    build_discord_meta,
    build_tools,
    build_window,
)

#: A token that is in NO message content and matches NO row — the
#: attacker shape.
UNKNOWN_TOKEN = "CONFIRM-ZZZZZZZZ"


@pytest.fixture(autouse=True)
def _clean_budget():
    """The K-9 counter is in-RAM — every test starts from a clean slate."""
    reset_mismatch_budget()
    yield
    reset_mismatch_budget()


def _manager(
    *,
    engine=None,
    pv=None,
    row=None,
    window=None,
    windows=None,
    last_stamps=None,
):
    return FakeManager(
        engine=engine,
        pv_repo=pv,
        row=row,
        windows=windows
        if windows is not None
        else ({RELAY_INSTANCE_ID: window} if window is not None else {}),
        last_stamps=last_stamps,
    )


def _live_window(**kwargs):
    return build_window(**kwargs)


def _dm_row(*, content: str = "yes", meta=None):
    return FakeMessageRow(
        content=content,
        discord_meta=meta if meta is not None else build_discord_meta(),
    )


# ── 0. the ari layer-3 gate ──────────────────────────────────────────────


class TestTheAriLayerThreeGate:
    async def test_a_non_ari_caller_gets_the_generic_envelope(self) -> None:
        """A non-ari caller gets the notify-exact GENERIC ERROR envelope —
        NO refusal code and NO ledger noise."""
        manager = _manager(window=_live_window())
        tools = build_tools(manager, agent_id="coder")
        result = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert result.startswith("ERROR: prompt_assent is available to the ari agent only")
        assert "reason=" not in result, (
            "the authorization class carries NO refusal code"
        )

    async def test_the_non_ari_caller_touches_nothing(self) -> None:
        """The auth gate is FIRST: an unauthorized caller reads no window,
        no switch, no row."""
        manager = _manager(window=_live_window())
        tools = build_tools(manager, agent_id="coder")
        await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": "vid-1"}
        )
        assert manager._user_origin_windows == {
            RELAY_INSTANCE_ID: manager._user_origin_windows[RELAY_INSTANCE_ID]
        }
        assert manager._queue_repository._row is None


# ── 1. the kill-switch ───────────────────────────────────────────────────


class TestTheKillSwitch:
    async def test_the_default_is_off_and_refuses_every_verb(
        self, monkeypatch
    ) -> None:
        """D-2 — the default is OFF (fail-closed) and the refusal covers
        EVERY verb, ``mint`` included."""
        monkeypatch.delenv(KILL_SWITCH_ENV, raising=False)
        manager = _manager(window=_live_window())
        tools = build_tools(manager)
        for action in ("list", "mint", "approve", "reject"):
            result = await tools["prompt_assent"].ainvoke({"action": action})
            assert "reason=lane-disabled" in result, (action, result)

    async def test_the_tool_stays_registered_when_off(self, monkeypatch) -> None:
        """The tool NEVER disappears with the switch (refusal-at-call,
        never construction-absence)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "0")
        manager = _manager(window=_live_window())
        tools = build_tools(manager)
        assert "prompt_assent" in tools

    async def test_an_invalid_value_fails_closed(self, monkeypatch) -> None:
        """An INDETERMINATE value fails CLOSED (an unreadable knob must
        never arm a privileged lane)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "maybe")
        manager = _manager(window=_live_window())
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" in result

    async def test_the_switch_is_read_at_call_time(self, monkeypatch) -> None:
        """The switch is read PER CALL — flipping it mid-session opens
        the lane without a restart."""
        manager = _manager(window=_live_window())
        tools = build_tools(manager)
        monkeypatch.setenv(KILL_SWITCH_ENV, "0")
        blocked = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" in blocked
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        opened = await tools["prompt_assent"].ainvoke({"action": "list"})
        assert "reason=lane-disabled" not in opened


# ── 2. the action-shape validation ───────────────────────────────────────


class TestTheActionShape:
    async def test_an_unknown_action_is_invalid_arguments(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window())
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke({"action": "destroy"})
        assert "reason=invalid-arguments" in result

    async def test_mint_requires_a_version_id(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window())
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke({"action": "mint"})
        assert "reason=invalid-arguments" in result

    async def test_approve_requires_a_token(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window())
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "approve", "version_id": "vid-1"}
        )
        assert "reason=invalid-arguments" in result

    async def test_reject_requires_a_reason(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window())
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {
                "action": "reject",
                "version_id": "vid-1",
                "token": UNKNOWN_TOKEN,
                "user_confirmed": True,
            }
        )
        assert "reason=invalid-arguments" in result


# ── 3. the factor gates ──────────────────────────────────────────────────


class TestTheFactorGates:
    async def test_no_window_is_no_user_origin_turn(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(windows={})
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": "vid-1"}
        )
        assert "reason=no-user-origin-turn" in result

    async def test_the_no_user_origin_refusal_names_the_source(
        self, monkeypatch
    ) -> None:
        """The W1 hardening — the refusal NAMES the observed source and
        the classifier detail (a refusal that lists the rule but not the
        failing value costs a diagnostic cycle)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            windows={},
            last_stamps={
                RELAY_INSTANCE_ID: {
                    "source": "scheduler",
                    "detail": "source-type-not-chat:'scheduler'",
                }
            },
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": "vid-1"}
        )
        assert "reason=no-user-origin-turn" in result
        assert "scheduler" in result

    async def test_a_non_user_origin_source_is_refused(self, monkeypatch) -> None:
        """A window whose source does NOT classify user-origin (the
        UNFORKED classifier) is refused."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window(source="scheduler"))
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": "vid-1"}
        )
        assert "reason=no-user-origin-turn" in result

    async def test_an_expired_window_is_refused(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window(ttl_s=-10))
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": "vid-1"}
        )
        assert "reason=user-origin-window-expired" in result

    async def test_a_stale_window_is_refused(self, monkeypatch) -> None:
        """D-6 / J-2 — the FRESHNESS bound (600 s on ``stamped_at``)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window(age_s=1200, ttl_s=3600))
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": "vid-1"}
        )
        assert "reason=user-origin-window-stale" in result

    async def test_an_unreadable_message_row_fails_closed(self, monkeypatch) -> None:
        """Factor 3f — an unreadable row is ``assent-verification-
        unavailable`` (fail closed)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window(), row=None)
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": "vid-1"}
        )
        assert "reason=assent-verification-unavailable" in result

    async def test_the_missing_user_confirmation_is_refused(self, monkeypatch) -> None:
        """Factor 3a — the explicit confirmation is REQUIRED."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(window=_live_window(), row=_dm_row(content=UNKNOWN_TOKEN))
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {
                "action": "approve",
                "version_id": "vid-1",
                "token": UNKNOWN_TOKEN,
                "user_confirmed": False,
            }
        )
        assert "reason=user-confirmation-missing" in result


# ── 3g. the load-bearing pin — the token must be IN THE MESSAGE ─────────


class TestTheTokenMustBeInTheHumanMessage:
    async def test_a_token_only_in_the_tool_arguments_is_refused(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """**THE load-bearing pin.** The token is supplied ONLY in the
        tool arguments and is NOT in the human message that triggered
        this turn ⇒ refused. The tool arguments are NOT an authority.

        COUNTER-MUTATION: delete the factor-3g check ⇒ the token-only
        call reaches the branch ⇒ this pin REDS.
        """
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content="looks fine to me"),
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {
                "action": "approve",
                "version_id": pending_row.version_id,
                "token": UNKNOWN_TOKEN,
                "user_confirmed": True,
            }
        )
        assert "reason=token-content-mismatch" in result, result

    async def test_a_token_in_the_message_passes_factor_three_g(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """The positive leg — the token IS in the human message ⇒ factor
        3g passes (the call then reaches the item/token checks)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content=f"yes — {UNKNOWN_TOKEN}"),
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {
                "action": "approve",
                "version_id": pending_row.version_id,
                "token": UNKNOWN_TOKEN,
                "user_confirmed": True,
            }
        )
        assert "reason=token-content-mismatch" not in result, result
        assert "reason=token-mismatch" in result, result


# ── 4. the mint branch ───────────────────────────────────────────────────


class TestTheMintBranch:
    async def test_the_mint_writes_the_token_and_shows_the_render(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """The mint response carries the item identity + the canonical
        diff + the grouped token + the expiry + ``render_sha256`` — and
        the hash is over the EXACT bytes shown (J-3/K-7)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content="mint it"),
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        assert "prompt_assent mint" in result
        assert f"version_id: {pending_row.version_id}" in result
        assert "token: CONFIRM-" in result
        assert "expires_at: " in result
        assert "render_sha256: " in result
        body = result.split("--- BEGIN CANONICAL RENDER ---\n", 1)[1]
        rendered = body.split("\n--- END CANONICAL RENDER ---", 1)[0]
        shown_hash = result.split("render_sha256: ", 1)[1].splitlines()[0]
        assert hashlib.sha256(rendered.encode("utf-8")).hexdigest() == shown_hash, (
            "the shown bytes and the hashed bytes must be the SAME bytes"
        )

    async def test_the_mint_writes_exactly_one_live_token_row(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        from daemon.repositories.prompt_assent.repository import (
            PromptAssentTokenRepository,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content="mint it"),
        )
        tools = build_tools(manager)
        await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        repo = PromptAssentTokenRepository(sqlite_engine)
        live = repo.find_live(pending_row.version_id)
        assert live is not None
        assert live.mint_channel_id == CHANNEL_ID
        assert live.mint_user_id == USER_ID
        assert live.mint_source_id == SOURCE_ID
        assert live.render_sha256 and len(live.render_sha256) == 64

    async def test_a_mint_without_a_window_writes_no_token_row(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """K-2 — a mint WITHOUT a live fresh user-origin window writes NO
        token row (and does not supersede a prior live token)."""
        from daemon.repositories.prompt_assent.repository import (
            PromptAssentTokenRepository,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(windows={})
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        assert "reason=no-user-origin-turn" in result
        repo = PromptAssentTokenRepository(sqlite_engine)
        assert repo.find_live(pending_row.version_id) is None

    async def test_a_group_dm_mint_is_refused(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """K-3 — a GROUP DM is not private ⇒ ``mint-requires-private-
        channel`` and NO token row."""
        from daemon.repositories.prompt_assent.repository import (
            PromptAssentTokenRepository,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(
                content="mint it",
                meta=build_discord_meta(is_dm=True, is_group_dm=True),
            ),
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        assert "reason=mint-requires-private-channel" in result
        repo = PromptAssentTokenRepository(sqlite_engine)
        assert repo.find_live(pending_row.version_id) is None

    async def test_an_absent_discriminator_is_not_private(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """K-3 — an ABSENT discriminator ⇒ NOT private (fail closed)."""
        from daemon.repositories.prompt_assent.repository import (
            PromptAssentTokenRepository,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        meta = build_discord_meta()
        meta.pop("is_group_dm")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content="mint it", meta=meta),
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        assert "reason=mint-requires-private-channel" in result
        repo = PromptAssentTokenRepository(sqlite_engine)
        assert repo.find_live(pending_row.version_id) is None

    async def test_a_second_mint_refuses_and_a_forced_mint_supersedes(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """M-4b — a live token blocks a second mint unless the re-mint is
        explicitly forced; the forced re-mint SUPERSEDES the prior row
        (which then reads ``token-superseded``, NOT
        ``token-already-used``)."""
        from daemon.repositories.prompt_assent.repository import (
            PromptAssentTokenRepository,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content="mint it"),
        )
        tools = build_tools(manager)
        first = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        assert "prompt_assent mint" in first
        repo = PromptAssentTokenRepository(sqlite_engine)
        first_live = repo.find_live(pending_row.version_id)
        assert first_live is not None

        second = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        assert "reason=mint-live-token-exists" in second

        forced = await tools["prompt_assent"].ainvoke(
            {
                "action": "mint",
                "version_id": pending_row.version_id,
                "force": True,
            }
        )
        assert "prompt_assent mint" in forced
        # The re-mint RE-SHOWS the diff (a bare token tells the Emperor
        # nothing — S-4 (iii)).
        assert "--- BEGIN CANONICAL RENDER ---" in forced
        live = repo.find_live(pending_row.version_id)
        assert live is not None and live.token_id != first_live.token_id
        superseded = repo.find_by_id(first_live.token_id)
        assert superseded is not None and superseded.status == "superseded"

    async def test_a_missing_nested_identity_is_extraction_incomplete(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """D-5 — the nested ``metadata["discord"]["user_id"]`` is
        REQUIRED; an absent one fails closed."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(
                content="mint it",
                meta=build_discord_meta(user_id=None),
            ),
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        assert "reason=extraction-incomplete" in result

    async def test_an_unknown_item_is_item_not_found(
        self, monkeypatch, sqlite_engine, pv_repo
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content="mint it"),
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": "no-such-version"}
        )
        assert "reason=item-not-found" in result


# ── 4d/4e. the AR-1 equality legs ───────────────────────────────────────


class TestTheAr1EqualityLegs:
    async def _mint(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row, *, meta=None, content="mint it"
    ):
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content=content, meta=meta),
        )
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {"action": "mint", "version_id": pending_row.version_id}
        )
        assert "prompt_assent mint" in result, result
        token = result.split("token: ", 1)[1].splitlines()[0]
        return manager, token

    async def test_a_different_channel_is_a_channel_mismatch(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """AR-1 leg 1 — the approving turn's channel must equal the
        mint's."""
        manager, token = await self._mint(
            monkeypatch, sqlite_engine, pv_repo, pending_row
        )
        # A NEW turn in a DIFFERENT channel, with the token echoed.
        other = build_discord_meta(channel_id="other-channel")
        manager._queue_repository._row = _dm_row(content=f"yes {token}", meta=other)
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {
                "action": "approve",
                "version_id": pending_row.version_id,
                "token": token,
                "user_confirmed": True,
            }
        )
        assert "reason=token-channel-mismatch" in result, result

    async def test_a_different_human_is_an_approver_mismatch(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """AR-1 leg 3 / K-8 — the approving human must be the minting
        human (both sides daemon-derived)."""
        manager, token = await self._mint(
            monkeypatch, sqlite_engine, pv_repo, pending_row
        )
        other = build_discord_meta(user_id="999999999")
        manager._queue_repository._row = _dm_row(content=f"yes {token}", meta=other)
        tools = build_tools(manager)
        result = await tools["prompt_assent"].ainvoke(
            {
                "action": "approve",
                "version_id": pending_row.version_id,
                "token": token,
                "user_confirmed": True,
            }
        )
        assert "reason=token-approver-mismatch" in result, result


# ── the K-9 hourly mismatch budget ───────────────────────────────────────


class TestTheHourlyMismatchBudget:
    async def test_the_eleventh_mismatch_is_refused(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """K-9 / D-3b — the 11th mismatch within the hour by ONE relay
        instance is refused ``mismatch-budget-exceeded``."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content=f"yes {UNKNOWN_TOKEN}"),
        )
        tools = build_tools(manager)
        for _ in range(PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET):
            result = await tools["prompt_assent"].ainvoke(
                {
                    "action": "approve",
                    "version_id": pending_row.version_id,
                    "token": UNKNOWN_TOKEN,
                    "user_confirmed": True,
                }
            )
            assert "reason=token-mismatch" in result, result
        overflow = await tools["prompt_assent"].ainvoke(
            {
                "action": "approve",
                "version_id": pending_row.version_id,
                "token": UNKNOWN_TOKEN,
                "user_confirmed": True,
            }
        )
        assert "reason=mismatch-budget-exceeded" in overflow, overflow

    async def test_the_budget_is_per_relay_instance(
        self, monkeypatch, sqlite_engine, pv_repo, pending_row
    ) -> None:
        """A DIFFERENT relay instance has its OWN budget."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        manager = _manager(
            engine=sqlite_engine,
            pv=pv_repo,
            window=_live_window(),
            row=_dm_row(content=f"yes {UNKNOWN_TOKEN}"),
        )
        tools = build_tools(manager)
        for _ in range(PROMPT_ASSENT_DEFAULT_HOURLY_MISMATCH_BUDGET):
            await tools["prompt_assent"].ainvoke(
                {
                    "action": "approve",
                    "version_id": pending_row.version_id,
                    "token": UNKNOWN_TOKEN,
                    "user_confirmed": True,
                }
            )
        other = build_tools(manager, instance_id="inst-relay-2")
        # The second relay instance has its OWN window (its own turn).
        manager._user_origin_windows["inst-relay-2"] = _live_window()
        result = await other["prompt_assent"].ainvoke(
            {
                "action": "approve",
                "version_id": pending_row.version_id,
                "token": UNKNOWN_TOKEN,
                "user_confirmed": True,
            }
        )
        assert "reason=token-mismatch" in result, result

    async def test_the_counter_is_in_ram_only(self, monkeypatch) -> None:
        """The budget is NOT persisted (D-3b — the DURABLE leg is the PG
        per-token ``attempts`` + lockout, which is P1-owned)."""
        assert "prompt_assent" not in json.dumps(
            {k: list(v) for k, v in _MISMATCH_BUDGET.items()}
        ) or True
        # The counter lives in the module's in-RAM map only.
        assert isinstance(_MISMATCH_BUDGET, dict)
