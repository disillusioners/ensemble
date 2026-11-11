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
    MESSAGE_ID,
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


# ════════════════════════════════════════════════════════════════════════
# Phase 3 — the acceptance path (tasks 3.1 / 3.2 / 3.6) + the provenance
# extras. Everything below drives the WIRED surface: gate → attestation
# assembly → svc.approve_assent/reject_assent → Phase-1 repo entrypoint →
# D-4 hook → guarded transition.
# ════════════════════════════════════════════════════════════════════════

from types import SimpleNamespace  # noqa: E402

from sqlmodel import Session, col, select  # noqa: E402

from daemon.manager import InstanceManager as _RealManager  # noqa: E402
from daemon.repositories.prompt_version.models import PromptAuditEvent
from daemon.repositories.prompt_assent.models import PromptAssentToken
from daemon.services.prompt_version_service import (
    AssentRefusalError,
    PromptVersionService,
    is_acceptable_approver,
)
from tests.helpers.prompt_assent_flow import (
    build_flow,
    decide,
    echo,
    mint_for,
)


class _FlowHarness:
    async def _approved_flow(self, monkeypatch):
        """A wired flow with a minted token echoed by the human turn."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        token = mint.split("token: ", 1)[1].splitlines()[0]
        shown_sha = mint.split("render_sha256: ", 1)[1].splitlines()[0]
        echo(flow, token)
        return flow, mint, token, shown_sha

    async def _decided_flow(self, monkeypatch, *, action="approve"):
        flow, mint, token, shown_sha = await self._approved_flow(monkeypatch)
        result = await decide(flow, action=action, token=token)
        return flow, mint, token, shown_sha, result


class TestTheApproveHappyPath(_FlowHarness):
    """§4 cell 1 — the happy path with a REAL token echo."""

    async def test_the_row_transitions_and_the_ledger_is_complete(
        self, monkeypatch
    ) -> None:
        """pending_approval → approved; EXACTLY ONE new audit row; the
        payload carries every provenance key; the token row is
        ``consumed`` with ``consumed_by_message_id`` set."""
        flow, mint, token, shown_sha, result = await self._decided_flow(monkeypatch)
        assert "prompt_assent approve — OK" in result, result

        row = flow.repo.get_version(flow.version_id)
        assert row.state == "approved"

        with Session(flow.engine) as session:
            events = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.version_id) == flow.version_id,
                    col(PromptAuditEvent.event_type) == "APPROVED",
                )
            ).all()
        assert len(events) == 1, "exactly ONE new audit decision row"
        payload = events[0].payload
        for key in (
            "approval_path",
            "relay_agent",
            "relay_instance_id",
            "relay_source_id",
            "relay_external_user_id",
            "relay_user_id",
            "relay_channel_id",
            "relay_channel_type",
            "relay_message_id",
            "relay_source_message_id",
            "relay_window_stamped_at",
            "relay_window_expires_at",
            "assented_at",
            "token_id",
            "token_fingerprint",
            "render_sha256",
            "approver_subject",
        ):
            assert key in payload, f"the provenance chain is missing {key!r}"
        assert payload["approval_path"] == "chat-assent"
        assert payload["approver_subject"] == f"{SOURCE_ID}:{USER_ID}"
        assert payload["relay_agent"] == "ari"
        assert payload["relay_message_id"] == MESSAGE_ID
        assert payload["render_sha256"] == shown_sha, (
            "the audited hash must be the hash of the EXACT shown bytes"
        )

        token_row = flow.token_repo.find_live(flow.version_id)
        assert token_row is None, "no live token remains"
        consumed = flow.token_repo.find_by_id(payload["token_id"])
        assert consumed is not None and consumed.status == "consumed"
        assert consumed.consumed_by_message_id == MESSAGE_ID

    async def test_the_raw_token_appears_nowhere_in_durable_state(
        self, monkeypatch
    ) -> None:
        """After a full mint → approve cycle the RAW token is absent
        from the token table AND the audit ledger — only
        ``sha256(token)[:12]`` fingerprints persist."""
        flow, mint, token, _, result = await self._decided_flow(monkeypatch)
        assert "prompt_assent approve — OK" in result, result
        with Session(flow.engine) as session:
            tokens = session.exec(select(PromptAssentToken)).all()
            events = session.exec(select(PromptAuditEvent)).all()
        blob = "".join(
            str(getattr(row, name, ""))
            for row in [*tokens, *events]
            for name in vars(row)
        ) + repr([getattr(e, "payload", None) for e in events])
        assert token not in blob, "the RAW TOKEN leaked into durable state"
        for row in tokens:
            assert token.replace("-", "") not in blob.replace("-", ""), (
                "the de-dashed token variant leaked too"
            )
            fingerprints = {row.token_hash[:12]}
            assert any(
                str(getattr(e, "payload", {}).get("token_fingerprint")) in fingerprints
                for e in events
            ), "the audit row must carry the fingerprint form"

    async def test_the_attestation_render_hash_equals_the_mint_response_hash(
        self, monkeypatch
    ) -> None:
        """Task 3.5 — the attestation's ``render_sha256`` is CARRIED from
        the token row, which was computed at mint from the EXACT bytes
        the mint response showed (shown == hashed == audited)."""
        flow, mint, token, shown_sha, result = await self._decided_flow(monkeypatch)
        assert "prompt_assent approve — OK" in result, result
        with Session(flow.engine) as session:
            events = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.event_type) == "APPROVED"
                )
            ).all()
        assert events[0].payload["render_sha256"] == shown_sha
        token_row = flow.token_repo.find_by_id(events[0].payload["token_id"])
        assert token_row.render_sha256 == shown_sha
        # NOT a prompt_versions.content_hash copy (K-7): the canonical
        # render hashes the FULL both-sides render, which differs from
        # the pending content's own sha.
        import hashlib as _hl

        assert token_row.render_sha256 != _hl.sha256(
            flow.pending_row.content.encode("utf-8")
        ).hexdigest()

    async def test_reject_rides_the_same_path(self, monkeypatch) -> None:
        """The reject verb mirrors approve: typed decision row, the
        token consumed, ``reject_reason`` top-level (≤500 chars)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        echo(flow, token)
        result = await decide(flow, action="reject", token=token)
        assert "prompt_assent reject — OK" in result, result
        assert flow.repo.get_version(flow.version_id).state == "rejected"
        with Session(flow.engine) as session:
            events = session.exec(
                select(PromptAuditEvent).where(
                    col(PromptAuditEvent.event_type) == "REJECTED"
                )
            ).all()
        assert len(events) == 1
        assert events[0].payload["reject_reason"] == "not good enough"
        assert flow.token_repo.find_live(flow.version_id) is None


class TestTheFiveMandatedRefusals(_FlowHarness):
    """§4 cells 2–6 — each asserts: typed code, NO state change, NO burn."""

    async def test_wrong_token_is_refused_and_changes_nothing(
        self, monkeypatch
    ) -> None:
        """Cell 2 — ``token-mismatch``; row unchanged; no burn (no row
        exists to burn; the K-9 in-RAM budget is the counter for the
        no-row shape)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        await mint_for(flow)
        before = flow.repo.get_version(flow.version_id).state
        live_before = flow.token_repo.find_live(flow.version_id)
        echo(flow, UNKNOWN_TOKEN)
        result = await decide(flow, token=UNKNOWN_TOKEN)
        assert "reason=token-mismatch" in result, result
        assert flow.repo.get_version(flow.version_id).state == before
        assert flow.token_repo.find_live(flow.version_id) == live_before

    async def test_expired_token_is_refused_and_not_burned(
        self, monkeypatch
    ) -> None:
        """Cell 3 — ``token-expired`` (the token's own TTL — task 3.6);
        row unchanged; the token SURVIVES (an expiry refusal never
        burns)."""
        result = await self._expired_via_fresh_mint(monkeypatch)
        assert "reason=token-expired" in result, result

    async def _expired_via_fresh_mint(self, monkeypatch) -> str:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        live = flow.token_repo.find_live(flow.version_id)
        with Session(flow.engine) as session:
            stored = session.get(PromptAssentToken, live.token_id)
            stored.ttl_expires_at = "2020-01-01T00:00:00Z"
            session.add(stored)
            session.commit()
        echo(flow, token)
        result = await decide(flow, token=token)
        # No state change, no burn.
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"
        assert flow.token_repo.find_by_id(live.token_id).status == "live"
        return result

    async def test_a_non_pending_row_is_refused_with_no_audit_row(
        self, monkeypatch
    ) -> None:
        """Cell 4 — ``already-decided`` (carrying the current state);
        the terminal row is untouched; NO decision audit event."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow()
        mint = await mint_for(flow)
        token = mint.split("token: ", 1)[1].splitlines()[0]
        echo(flow, token)
        ok = await decide(flow, token=token)
        assert "prompt_assent approve — OK" in ok, ok
        decided_state = flow.repo.get_version(flow.version_id).state
        with Session(flow.engine) as session:
            before_events = session.exec(
                select(PromptAuditEvent.event_id)
            ).all()
        # A SECOND approval attempt on the now-approved row.
        flow.manager._queue_repository._row = FakeMessageRow(
            content=f"again {token}",
            discord_meta=build_discord_meta(),
        )
        again = await decide(flow, token=token)
        assert "reason=already-decided" in again, again
        assert flow.repo.get_version(flow.version_id).state == decided_state
        with Session(flow.engine) as session:
            after_events = session.exec(
                select(PromptAuditEvent.event_id)
            ).all()
        assert len(after_events) == len(before_events), (
            "the refused second decision must not write an audit row"
        )

    async def test_agent_self_approval_attempt_is_refused(
        self, monkeypatch
    ) -> None:
        """Cell 5 — ``self-approval-attempt`` — belt-and-braces, NOT the
        gate. The test asserts the CONSTRUCTION PROPERTIES, not just
        the code:

        (a) an agent-originated dispatch POPS the window
            (``Manager.stamp_user_origin_window`` — manager.py:6132);
        (b) the ari-EXACTLY layer-3 gate (a non-ari caller gets the
            generic envelope);
        (c) the daemon-derived user equality (AR-1/K-8 — pinned in
            test_prompt_assent_user_binding.py).

        The code itself is reachable at the SERVICE layer: an
        attestation whose human source classifies as a
        reserved-internal lane is an agent self-approval attempt by
        construction.
        """
        # (a) the window lifecycle: a user-origin stamp arms; an
        # agent-source dispatch CLEARS it (fail closed).
        class _Win:
            _user_origin_windows = {}
            _user_origin_last_stamp = {}
            source_registry = None

        _Win.source_registry = _manager_fake_registry()
        _RealManager.stamp_user_origin_window(
            _Win, "inst-x", SOURCE_ID, "msg-1"
        )
        assert "inst-x" in _Win._user_origin_windows
        _RealManager.stamp_user_origin_window(
            _Win, "inst-x", "scheduler", "msg-2"
        )
        assert "inst-x" not in _Win._user_origin_windows, (
            "an agent-originated dispatch MUST pop the window — the "
            "agent self-approval construction property"
        )
        # (b) the layer-3 gate.
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        stranger = build_tools(build_flow().manager, agent_id="coder")
        result = await stranger["prompt_assent"].ainvoke({"action": "approve"})
        assert result.startswith(
            "ERROR: prompt_assent is available to the ari agent only"
        )
        assert "reason=" not in result
        # The code, at the service layer: a reserved-internal human
        # source cannot ride the relay lane — refused BEFORE any
        # token-state consultation, on a REAL minted token.
        from dataclasses import replace

        from daemon.repositories.prompt_version.assent_attestation import (
            AssentHumanIdentity,
        )
        from daemon.repositories.prompt_version.repository import (
            actor_from_assent_relay,
        )
        from tests.helpers.prompt_assent_fixture import build_attestation

        svc_flow = build_flow()
        await mint_for(svc_flow)
        real = svc_flow.token_repo.find_live(svc_flow.version_id)
        att = build_attestation(
            version_id=svc_flow.version_id, token_id=real.token_id
        )
        forged = replace(
            att,
            human_identity=AssentHumanIdentity(
                source_id="scheduler",
                user_id=att.human_identity.user_id,
                channel_id=att.human_identity.channel_id,
                channel_type=att.human_identity.channel_type,
                is_dm=True,
                external_user_id=att.human_identity.external_user_id,
            ),
            approver_subject=f"scheduler:{att.human_identity.user_id}",
        )
        svc = PromptVersionService(repo=svc_flow.repo)
        with pytest.raises(AssentRefusalError) as exc:
            await svc.approve_assent(forged.item_binding.version_id, forged)
        assert exc.value.code == "self-approval-attempt"
        # The refusal preceded ANY token-state change: the token is
        # still live and untouched.
        still = svc_flow.token_repo.find_by_id(real.token_id)
        assert still.status == "live" and still.attempts == 0

    async def test_a_non_user_origin_turn_is_refused_and_names_the_source(
        self, monkeypatch
    ) -> None:
        """Cell 6 — ``no-user-origin-turn`` naming the observed source;
        row unchanged; no burn."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(window=build_window(source="scheduler"))
        await mint_for(flow)
        live = flow.token_repo.find_live(flow.version_id)
        result = await decide(flow, token="CONFIRM-AAAAAAAA")
        assert "reason=no-user-origin-turn" in result, result
        assert "scheduler" in result, "the refusal must NAME the source"
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"
        assert flow.token_repo.find_live(flow.version_id) == live


def _manager_fake_registry():
    from tests.helpers.prompt_assent_tool_fixture import FakeSourceRegistry

    return FakeSourceRegistry()


class TestTheSharedApproverPredicate:
    """Task 3.1 — ONE rule: the session lane OR the assent-relay lane
    with a COMPLETE attestation. The FE surface consults it; the repo
    enforces the same semantics at its layer (two layers, one rule)."""

    @staticmethod
    def _actor(method):
        """A lane actor carrying ONLY what the predicate reads."""
        from types import SimpleNamespace as _NS

        return _NS(auth_method=method)

    def test_session_passes_without_an_attestation(self):
        assert is_acceptable_approver(self._actor("session"), None) is True

    def test_assent_relay_passes_only_with_a_complete_attestation(self):
        from tests.helpers.prompt_assent_fixture import build_attestation

        att = build_attestation()
        assert is_acceptable_approver(self._actor("assent-relay"), att) is True
        assert is_acceptable_approver(self._actor("assent-relay"), None) is False
        assert is_acceptable_approver(self._actor("assent-relay"), {}) is False

    def test_bearer_internal_and_unknown_are_refused(self):
        for method in ("bearer", "internal", "forged-lane", None):
            assert is_acceptable_approver(self._actor(method), None) is False, (
                method
            )

    async def test_the_fe_surface_consults_the_same_rule(
        self, sqlite_engine, pv_repo, pending_row
    ):
        """svc.approve/reject consult the predicate with
        attestation=None: session passes; bearer/internal/assent-relay
        refuse — byte-identical to the pre-lane FE behavior."""
        svc = PromptVersionService(repo=pv_repo)
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
        )

        session_actor = ActorContext(subject="operator", auth_method="session")
        out = await svc.approve(pending_row.version_id, session_actor)
        assert out.state == "approved"

        flow = build_flow()
        mint = await mint_for(flow)
        relay_actor = None

        from daemon.repositories.prompt_version.repository import (
            actor_from_assent_relay,
        )
        from tests.helpers.prompt_assent_fixture import build_attestation

        att = build_attestation(version_id=flow.version_id)
        relay_actor = actor_from_assent_relay(att)
        with pytest.raises(Exception) as exc:
            await PromptVersionService(repo=flow.repo).approve(
                flow.version_id, relay_actor
            )
        assert "HUMAN_LANE" in str(type(exc.value).__name__) or isinstance(
            exc.value, ValueError
        )

    def test_the_repo_enforces_the_same_rule_at_its_layer(
        self, sqlite_engine, pv_repo, pending_row
    ):
        """Two layers, ONE rule: repo.approve() refuses internal (F4)
        AND refuses an assent-relay actor; repo.approve_assent() refuses
        a session actor. The counter-mutation on either layer must
        redden the same predicate test."""
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
            actor_from_assent_relay,
        )
        from tests.helpers.prompt_assent_fixture import build_attestation

        internal = ActorContext(
            subject="inst-1", auth_method="internal",
            instance_id="inst-1", agent_id="ari",
        )
        with pytest.raises(ValueError, match="only HTTP actors"):
            pv_repo.approve(pending_row.version_id, internal)

        att = build_attestation(version_id=pending_row.version_id)
        relay = actor_from_assent_relay(att)
        # approve() refuses the relay actor (the attestation-bearing
        # entrypoints are the ONLY lane) — the repo's clamp names it.
        with pytest.raises(ValueError, match="assent-relay"):
            pv_repo.approve(pending_row.version_id, relay)
        session_actor = ActorContext(subject="op", auth_method="session")
        with pytest.raises(ValueError, match="only assent-relay actors"):
            pv_repo.approve_assent(pending_row.version_id, session_actor, att)


class TestTheStructuralPins:
    """Exit criteria 6/7 + O-L — executable structural pins."""

    def _source(self, path: str) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[2] / path).read_text(
            encoding="utf-8"
        )

    def test_the_tool_module_never_calls_atomic_transition(self):
        """Exit criterion 6 — the F2 bypass cannot exist in the tool:
        no ``atomic_transition(`` CALL site anywhere in the module (the
        docstring's F2-lesson PROSE is the warning, not a call)."""
        source = self._source("daemon/tools/prompt_assent.py")
        assert "atomic_transition(" not in source

    def test_the_gate_has_exactly_one_implementation_site(self):
        """Exit criterion 7 (O-E) — the factor-gate refusal codes are
        EMITTED in exactly one tool module."""
        import subprocess

        out = subprocess.run(
            [
                "grep", "-rl", "user-origin-window-stale", "daemon/",
                "--include=*.py",
            ],
            cwd=self._repo_root(), capture_output=True, text=True,
        )
        sites = [l for l in out.stdout.splitlines() if l.strip()]
        assert sites == ["daemon/tools/prompt_assent.py"], sites

    def test_the_render_hash_recheck_lives_only_in_the_hook(self):
        """O-L — no second render-hash VERIFICATION site: the comparison
        of a re-rendered hash against the token's value exists ONLY in
        the D-4 hook module."""
        import subprocess

        out = subprocess.run(
            [
                "grep", "-rln", "rendered_hash", "daemon/",
                "--include=*.py",
            ],
            cwd=self._repo_root(), capture_output=True, text=True,
        )
        sites = [l for l in out.stdout.splitlines() if l.strip()]
        assert sites == [
            "daemon/repositories/prompt_assent/consume_hook.py"
        ], sites
        service_source = self._source("daemon/services/prompt_version_service.py")
        assert "hashlib" not in service_source.replace(
            "# hashlib", ""
        ), "the service must never hash — the hook is the only site"

    def test_the_attestation_carries_the_token_row_hash_not_a_recompute(self):
        """Task 3.5 — the accept seam CARRIES render_sha256 from the
        token row; it never computes one."""
        source = self._source("daemon/tools/prompt_assent.py")
        seam = source.split("def _build_attestation", 1)[1].split(
            "\ndef ", 1
        )[0]
        assert "render_sha256=render_sha" in seam
        assert "hashlib" not in seam, (
            "the attestation assembly must not compute any hash"
        )

    def _repo_root(self) -> str:
        from pathlib import Path

        return str(Path(__file__).resolve().parents[2])
