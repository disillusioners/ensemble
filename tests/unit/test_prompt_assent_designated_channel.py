"""The DESIGNATED-CHANNEL cell matrix (prompt-assent Phase 1.5, Phase 3).

This file is the proof surface of the refusal-cell matrix
(``.agents/shared/planning/prompt-assent-channels/refusal-cell-matrix.md``):
the 20 mint cells (§2) + the 12 approve cells (§3) = 32, each with a
named test, hermetic fixtures, named counter-mutations demonstrated to
REDDEN their cells, and the runtime proof of ``INVARIANT-DM``.

CELL CENSUS — where every one of the 32 cells is proved
-------------------------------------------------------
| Cell | Named test | Where |
|------|-----------|-------|
| M-1  | ``test_a_proven_1to1_dm_mints`` (UNCHANGED pin) | ``test_prompt_assent_mint_gate.py:117`` |
| M-2  | FAMILY-TESTED — M-1 + A-2 (the conjunction) **+ the named M-2 cell** ``test_m2_a_dm_mint_by_a_distinct_human_records_that_human`` (a DM mint by a NON-default human ⇒ ``mint_user_id`` records THAT human; added in the 2026-11-12 fix round — the plan's family-test rule now names this cell). The DM path does not bind the approver at mint; the binding is at approve leg 4e. | this file + this docstring |
| M-3  | ``test_a_group_dm_is_refused_with_no_row`` (UNCHANGED pin) | ``test_prompt_assent_mint_gate.py:84`` |
| M-4  | ``test_an_absent_discriminator_is_refused`` + the group-DM discriminator pins (UNCHANGED) | ``test_prompt_assent_mint_gate.py:92`` + ``test_prompt_assent_group_dm_discriminator.py:150`` |
| M-5  | ``test_a_guild_channel_is_refused`` (UNCHANGED pin) | ``test_prompt_assent_mint_gate.py:102`` |
| M-6 … M-19 | the named tests below (NEW — this file) | this file |
| A-1  | ``test_the_right_user_in_the_right_channel_is_accepted`` (UNCHANGED pin) | ``test_prompt_assent_user_binding.py:116`` |
| A-2  | ``test_a_different_human_in_the_same_channel_is_refused`` (UNCHANGED pin) | ``test_prompt_assent_user_binding.py:78`` |
| A-3  | ``test_a_different_channel_is_refused`` (UNCHANGED pin) | ``test_prompt_assent_user_binding.py:49`` |
| A-4 … A-12 | the named tests below (NEW — this file) | this file |

THE EXISTING DM-PATH PIN FILES ARE NOT EDITED BY THIS LANE (matrix §4
PR-2 — the "unchanged" claim IS the deliverable; T3.7 proves it with a
``git diff --stat`` run, recorded in the lane report).

HERMETIC MAP CONTROL (T3.1 / D-3.2 / AC1)
-----------------------------------------
The autouse ``_hermetic_designation_map`` fixture below SCRUBS
``ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS`` before EVERY cell in
this file, so the default rig posture here is *no map at all* and no
cell can inherit an ambient operator map. A cell that needs a map sets
an EXACT value — ``build_flow(designations={...})`` (canonical JSON) or
``build_flow(designations="<raw>")`` (an invalid-map literal) — or an
explicit ``designations=None`` scrub. ``monkeypatch.setenv`` after the
mint is the mid-test re-designation lever (the A-9/A-10/A-12 windows).

COUNTER-MUTATIONS (T3.6 / D-3.4 / AC6)
--------------------------------------
Every structural pin this file owns has a NAMED counter-mutation that
is demonstrated, IN-FILE, to REDDEN its cell (the AssertionError under
the mutation IS the evidence; a guard green under its own revert fails
the suite):

| Pin | Mutation (named) | Reddened cell |
|-----|------------------|---------------|
| P-2b | the DM short-circuit is hoisted (``is_private_assent_channel`` → ``False``) + the raising resolver ⇒ the DM mint RAISES | ``test_pr1_the_dm_path_never_resolves_the_map`` |
| P-2c / M-15 | the pinned-lookup + equality legs are hoisted ABOVE the group-DM leg (``_mutated_map_lookup_first``) ⇒ the group DM MINTS | ``test_m15_a_group_dm_whose_channel_is_designated_is_refused`` |
| P-7 / M-11 | the invalid-map leg is hoisted ABOVE the group-DM leg (``_mutated_invalid_map_first``) ⇒ ``designation-config-invalid`` | ``test_m11_a_group_dm_with_an_invalid_map_is_refused_at_leg_2`` |
| P-3 | ``approver-not-pinned`` is added to ``MISMATCH_CODES`` ⇒ the budget burns | ``test_a9_...noburn`` (via ``TestTheP3MismatchCodesMutation``) |
| P-8 | ``PHASE1_5_PLACED_CODES`` is emptied ⇒ the 4f split falls through to ``_record_mismatch`` | ``test_a9_...noburn`` (via ``TestTheP8NoBurnMutation``) |
| digest pin | ``_DesignationCache.digest`` returns ``None`` ⇒ the mint INFO line drops ``map_digest`` | ``test_the_mint_info_digest_is_stable_and_content_free`` |

A LIVE tree-mutation demonstration (the real leg reorder in
``daemon/repositories/prompt_assent/mint_context.py``, run, restored
byte-identically — sha256 PRE == POST) is recorded in the lane report.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import pytest
from sqlmodel import Session, col, select

from daemon.repositories.prompt_version.models import PromptAuditEvent
from daemon.tools.prompt_assent import (
    ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS as ENV_NAME,
    KILL_SWITCH_ENV,
    PROMPT_ASSENT_REVOKE_THRESHOLD,
    token_hash,
)
from tests.helpers.prompt_assent_flow import (
    build_flow,
    decide,
    echo,
    mint_for,
)
from tests.helpers.prompt_assent_tool_fixture import (
    USER_ID,
    FakeMessageRow,
    build_discord_meta,
)

#: The lane's ids — digit strings, no leading zeros (the map's id shape,
#: ``^[1-9][0-9]{0,31}$``).
GUILD_ID = "777000111"
CHANNEL = "111122223333444455"
APPROVER = "112233445566"
OTHER_HUMAN = "999999999"
THREAD_ID = "222233334444555566"
SECOND_CHANNEL = "333344445555666677"
SECOND_APPROVER = "121212121212"
BOT_ID = "887766554433221100"
DM_CHANNEL = "444455556666777788"
DM_CHANNEL_TWO = "555566667777888999"

RELAY_INSTANCE_ID = "inst-relay-1"
MESSAGE_ID = "msg-relay-1"

#: The byte-identical v0.29.18 refusal envelope (architect §2.4 — the
#: shared detail string is NOT edited; T2.8). The map-unset equivalence
#: cell (T3.4 / PR-3) asserts this EXACT string for every pre-existing
#: refusal shape.
V02918_NOT_PRIVATE_ENVELOPE = (
    "Error: prompt_assent REFUSED — "
    "reason=mint-requires-private-channel: "
    "the mint context is not a proven 1:1 DM (is_dm AND NOT "
    "is_group_dm; an absent discriminator is not private) — the "
    "mint is refused and NO token row is written"
)


@pytest.fixture(autouse=True)
def _hermetic_designation_map(monkeypatch):
    """T3.1 / D-3.2 / AC1 — the hermetic map control for THIS file.

    Every cell starts with the designation variable SCRUBBED: no cell
    can inherit an ambient operator map, and a cell that wants a map
    sets an exact one (or re-designates mid-test via monkeypatch).
    """
    monkeypatch.delenv(ENV_NAME, raising=False)
    yield


@pytest.fixture(autouse=True)
def _clean_budget():
    """The K-9 counter is in-RAM — every cell starts from a clean slate
    (test-order independence across the matrix files)."""
    from daemon.tools.prompt_assent import reset_mismatch_budget

    reset_mismatch_budget()
    yield
    reset_mismatch_budget()


# ── shared helpers ────────────────────────────────────────────────────────


def _token_of(mint: str) -> str:
    """The raw token out of a mint response."""
    return mint.split("token: ", 1)[1].splitlines()[0]


def _info_lines(caplog) -> list[str]:
    """The frozen INFO lines (``outcome=`` lines only — the resolver's
    ``designation_map state=`` line is a different record)."""
    return [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "daemon.tools.prompt_assent"
        and rec.getMessage().startswith("prompt_assent outcome=")
    ]


def _mint_ok_line(caplog) -> str:
    return next(
        l for l in _info_lines(caplog)
        if "action=mint" in l and "outcome=ok" in l
    )


def _budget_count() -> int:
    from daemon.tools.prompt_assent import _budget_count as _count, _hour_bucket

    return _count(RELAY_INSTANCE_ID, _hour_bucket())


def _assert_no_burn(flow, token_id: str) -> None:
    """T3.5 / D-3.5 — the no-burn assertion that lives INSIDE the
    new-code cells: ``attempts`` unchanged, the token NOT revoked, and
    the relay's hourly K-9 budget counter unchanged."""
    row = flow.token_repo.find_by_id(token_id)
    assert row.attempts == 0, (
        "the 4f split must keep the new codes off the durable counter"
    )
    assert row.status == "live", "the token must not be revoked"
    assert _budget_count() == 0, (
        "the new codes are outside MISMATCH_CODES — no budget burn"
    )


def _echo_designated_kwargs() -> dict[str, Any]:
    """The echo kwargs that put the approving turn back in the
    designated channel (the same channel, the pinned human)."""
    return dict(
        is_dm=False,
        is_group_dm=False,
        guild_id=GUILD_ID,
        channel_id=CHANNEL,
        channel_type="text",
    )


def _guild_meta(
    user_id: str = APPROVER,
    channel_id: str = CHANNEL,
    **overrides: Any,
) -> dict[str, Any]:
    """A designated-channel-shaped window row (T3.1): a guild text
    channel with a nested human — the only shape a designation applies
    to. ``AssentHumanIdentity`` needs a non-empty ``external_user_id``;
    the non-DM derivation builds ``channel:<guild>:<channel>:<user>``,
    so NO mapping-row fake is needed (verified by execution — A-4).
    ``**overrides`` replaces any default (e.g. ``channel_type="thread"``
    for the D-9 cells)."""
    kwargs: dict[str, Any] = dict(
        is_dm=False,
        is_group_dm=False,
        guild_id=GUILD_ID,
        channel_id=channel_id,
        channel_type="text",
        user_id=user_id,
    )
    kwargs.update(overrides)
    return build_discord_meta(**kwargs)


def _designated_flow(**flow_kwargs: Any):
    """A flow rig in the designated channel, by the pinned approver —
    with the EXACT map ``{CHANNEL: APPROVER}`` set (never ambient)."""
    kwargs: dict[str, Any] = {"meta": _guild_meta()}
    kwargs.update(flow_kwargs)
    return build_flow(designations={CHANNEL: APPROVER}, **kwargs)


async def _mint_designated(monkeypatch):
    """Mint in the designated channel; return (flow, token, live row)."""
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    flow = _designated_flow()
    mint = await mint_for(flow)
    token = _token_of(mint)
    live = flow.token_repo.find_live(flow.version_id)
    return flow, token, live


async def _mint_and_reshape(monkeypatch, map_now: Any):
    """Mint in the designated channel, then move the map to ``map_now``
    BEFORE the approving turn (the re-designation / operator-fault /
    withdrawal windows)."""
    flow, token, live = await _mint_designated(monkeypatch)
    if isinstance(map_now, str):
        monkeypatch.setenv(ENV_NAME, map_now)
    else:
        monkeypatch.setenv(ENV_NAME, json.dumps(map_now))
    echo(flow, token, **_echo_designated_kwargs())
    return flow, token, live


def _last_audit_payload(engine) -> dict[str, Any]:
    with Session(engine) as session:
        rows = session.exec(
            select(PromptAuditEvent).order_by(col(PromptAuditEvent.created_at))
        ).all()
    assert rows, "the decision must write an audit row"
    return rows[-1].payload


def _last_audit_row(engine):
    with Session(engine) as session:
        rows = session.exec(
            select(PromptAuditEvent).order_by(col(PromptAuditEvent.created_at))
        ).all()
    assert rows, "the decision must write an audit row"
    return rows[-1]


# ═════════════════════════════════════════════════════════════════════════
# The MINT cells — §2 of the matrix (the new ones; M-1…M-5 stay in the
# UNCHANGED pin files, see the census above).
# ═════════════════════════════════════════════════════════════════════════


class TestTheDesignatedChannelMint:
    """M-6 — a guild channel, designated, turned by the PINNED APPROVER
    ⇒ MINT, ``mint_is_dm=0``, the channel/approver ledger, the digest."""

    async def test_m6_a_designated_channel_mints_for_its_pinned_approver(
        self, monkeypatch, caplog
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            flow = _designated_flow()
            mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint

        # the ledger shape
        row = flow.token_repo.find_live(flow.version_id)
        assert row is not None, "the designated mint writes a token row"
        assert row.mint_is_dm == 0
        assert row.mint_channel_id == CHANNEL
        assert row.mint_user_id == APPROVER
        # D-19 — the composite is AUDIT-ONLY, full shape
        assert row.minted_external_user_id == (
            f"channel:{GUILD_ID}:{CHANNEL}:{APPROVER}"
        )
        assert row.status == "live"
        assert row.attempts == 0

        # the INFO line: private=0 (private-EQUIVALENT, not private),
        # the dedicated map_digest field (P2-AC15 / AC13)
        line = _mint_ok_line(caplog)
        assert "private=0" in line, line
        assert "private_equivalent=1" in line, line
        assert "map_digest=" in line, line
        digest = line.rsplit("map_digest=", 1)[1].strip()
        assert len(digest) == 12


class TestTheNonPinnedUser:
    """M-7 — a designated channel turned by a NON-pinned human ⇒ the
    NEW ``approver-not-pinned`` code, NO token row, NO budget burn."""

    async def test_m7_a_non_pinned_human_is_refused(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            meta=_guild_meta(user_id=OTHER_HUMAN),
            designations={CHANNEL: APPROVER},
        )
        result = await mint_for(flow)
        assert "reason=approver-not-pinned" in result, result
        # the side-specific detail (architect §6.4 — mint wording)
        assert "not the pinned approver of this room" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None, (
            "no token row is written"
        )
        # T3.5 — the budget assertion lives INSIDE the cell: a mint-side
        # refusal presents no token; burning there would be a DoS lever
        # (S-7). The mint path never consults the budget at all.
        assert _budget_count() == 0


class TestTheDesignatedExtraction:
    """M-8 and M-19 — the absent-identity leg (which PRECEDES the pinned
    comparison; without it these cells would return
    ``approver-not-pinned``, which means *"a known human who is not
    pinned"* — never *"no human at all"*)."""

    async def test_m8_a_blank_user_id_is_extraction_incomplete(
        self, monkeypatch
    ) -> None:
        """M-8 — designated, the nested user_id present but BLANK."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            meta=_guild_meta(user_id="   "),
            designations={CHANNEL: APPROVER},
        )
        result = await mint_for(flow)
        assert "reason=extraction-incomplete" in result, result
        # the DESIGNATED-branch detail (truthful: the surface was fine —
        # the human was not identified), not the DM completeness text
        assert "designated" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None
        assert _budget_count() == 0

    async def test_m19_an_absent_nested_user_id_is_extraction_incomplete(
        self, monkeypatch
    ) -> None:
        """M-19 (B3(iii)) — designated, the nested ``user_id`` key
        ABSENT; the non-DM derivation cannot build an
        ``external_user_id`` without the nested user id."""
        from daemon.tools.prompt_assent import _derive_external_user_id

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        meta = _guild_meta()
        meta.pop("user_id")
        flow = build_flow(meta=meta, designations={CHANNEL: APPROVER})
        result = await mint_for(flow)
        assert "reason=extraction-incomplete" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None

        # the derivation negative (B3(iii)): absent nested user id ⇒ NO
        # composite, never a partial one
        row = FakeMessageRow(discord_meta=meta)
        assert _derive_external_user_id(None, row, meta, "", False) is None
        assert _budget_count() == 0


class TestTheInvalidMap:
    """M-9 / M-10 / M-11 — the operator-fault class. An invalid map is
    a TYPED-INVALID state, never a silent empty map (D-4); the blast
    radius is the designated path ONLY (M-10)."""

    async def test_m9_an_invalid_map_refuses_the_designated_path(
        self, monkeypatch
    ) -> None:
        """M-9 — malformed JSON / bad id / duplicate key / >64 entries:
        every invalid shape refuses ``designation-config-invalid``."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        invalid_maps = [
            "{bad",  # malformed JSON
            json.dumps({CHANNEL: "not-digits"}),  # bad id shape (value)
            '{"111": "1", "111": "2"}',  # duplicate key
            json.dumps(  # > 64 entries
                {str(10 ** 17 + i): APPROVER for i in range(65)}
            ),
        ]
        for raw in invalid_maps:
            flow = build_flow(meta=_guild_meta(), designations=raw)
            result = await mint_for(flow)
            assert "reason=designation-config-invalid" in result, (raw, result)
            assert flow.token_repo.find_live(flow.version_id) is None, raw
            # T3.5 — no budget burn for the operator-fault code
            assert _budget_count() == 0, raw

    async def test_m10_a_broken_map_never_touches_the_dm_path(
        self, monkeypatch
    ) -> None:
        """M-10 — the blast-radius cell: a 1:1 DM with an INVALID map
        still MINTS, unchanged ledger shape."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(designations="{bad")
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        row = flow.token_repo.find_live(flow.version_id)
        assert row is not None and row.mint_is_dm == 1
        assert row.mint_user_id == USER_ID

    async def test_m11_a_group_dm_with_an_invalid_map_is_refused_at_leg_2(
        self, monkeypatch
    ) -> None:
        """M-11 — THE ORDERING CELL: the group-DM leg precedes the map,
        so a group DM + an invalid map refuses the UNCHANGED group-DM
        code, never the operator-fault code."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            meta=build_discord_meta(is_dm=True, is_group_dm=True),
            designations="{bad",
        )
        result = await mint_for(flow)
        assert "reason=mint-requires-private-channel" in result, result
        assert "designation-config-invalid" not in result, result
        assert flow.token_repo.find_live(flow.version_id) is None
        assert _budget_count() == 0


class TestTheThreadCells:
    """M-12 / M-13 — D-9: a designation matches the EXACT channel id;
    threads do NOT inherit, but a thread MAY be named explicitly."""

    async def test_m12_a_thread_inside_a_designated_channel_is_refused(
        self, monkeypatch
    ) -> None:
        """M-12 — the parent is designated, the thread is NOT (no
        inheritance; a distinct channel_id)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            meta=_guild_meta(
                channel_id=THREAD_ID,
                channel_type="thread",
                parent_channel_id=CHANNEL,
                thread_id=THREAD_ID,
            ),
            designations={CHANNEL: APPROVER},
        )
        result = await mint_for(flow)
        assert "reason=mint-requires-private-channel" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None
        assert _budget_count() == 0

    async def test_m13_a_thread_designated_by_its_own_id_mints(
        self, monkeypatch, caplog
    ) -> None:
        """M-13 — the operator may name a thread explicitly: the thread's
        OWN id is the map key."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            flow = build_flow(
                meta=_guild_meta(
                    channel_id=THREAD_ID,
                    channel_type="thread",
                    parent_channel_id=CHANNEL,
                    thread_id=THREAD_ID,
                ),
                designations={THREAD_ID: APPROVER},
            )
            mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        row = flow.token_repo.find_live(flow.version_id)
        assert row is not None
        assert row.mint_is_dm == 0
        assert row.mint_channel_id == THREAD_ID
        assert row.mint_user_id == APPROVER
        assert "map_digest=" in _mint_ok_line(caplog)


class TestTheCrossPinCell:
    """M-14 — the map is PER CHANNEL, not per human: the pinned approver
    of ANOTHER channel is a stranger here."""

    async def test_m14_an_approver_pinned_for_another_channel_is_refused(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            # the turn is in C1 by U2 — U2 is pinned for C2, not for C1
            meta=_guild_meta(user_id=SECOND_APPROVER, channel_id=CHANNEL),
            designations={CHANNEL: APPROVER, SECOND_CHANNEL: SECOND_APPROVER},
        )
        result = await mint_for(flow)
        assert "reason=approver-not-pinned" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None
        assert _budget_count() == 0


class TestTheGroupDmTrustBoundaryUnpinnable:
    """M-15 (NT-3 / P-2c) — the UNPINNABLE shape: a group DM whose
    channel id IS in a valid map, typed by the pinned approver, is
    STILL refused — the group-DM leg precedes the map. Carries its own
    IN-FILE counter-mutation: hoist the pinned-lookup legs above the
    group-DM leg and this cell REDDENS (the group DM would MINT)."""

    async def _run_cell(self):
        flow = build_flow(
            meta=build_discord_meta(
                is_dm=True,
                is_group_dm=True,
                guild_id=GUILD_ID,
                channel_id=CHANNEL,
                channel_type="group",
                user_id=APPROVER,
            ),
            designations={CHANNEL: APPROVER},
        )
        result = await mint_for(flow)
        return flow, result

    async def test_m15_a_group_dm_whose_channel_is_designated_is_refused(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow, result = await self._run_cell()
        assert "reason=mint-requires-private-channel" in result, result
        assert "approver-not-pinned" not in result, result
        assert flow.token_repo.find_live(flow.version_id) is None
        assert _budget_count() == 0

    async def test_mutation_hoisting_the_map_above_the_group_dm_leg_reddens_m15(
        self, monkeypatch
    ) -> None:
        """P-2c's counter-mutation — the natural wrong implementation
        (R-2): consult the map BEFORE the group-DM leg. Under it the
        SAME input MINTS ⇒ the cell's refusal assertion REDS."""
        import daemon.tools.prompt_assent as pa

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setattr(pa, "designation_outcome", _mutated_map_lookup_first)
        flow, result = await self._run_cell()
        with pytest.raises(AssertionError):
            assert "reason=mint-requires-private-channel" in result, result
        # the evidence: the trust boundary FAILED — a group DM minted,
        # through the NON-designated path (the DM-form external id).
        assert "prompt_assent mint" in result, result
        row = flow.token_repo.find_live(flow.version_id)
        assert row is not None, "under the mutation the group DM mints"
        assert row.minted_external_user_id == f"dm:{APPROVER}", (
            "the mutated mint went through the DM arm, not the "
            "designated arm — the group DM slipped past the boundary"
        )


class TestTheBotPinnedCell:
    """M-16 (NT-8) — a BOT id pinned for the channel: a human turn gets
    ``approver-not-pinned`` — the SAME code as a stranger; the room is
    permanently mint-dead under the adapter's DEFAULT bot-skip posture
    (``daemon/sources/adapters/discord/adapter.py:909-915``)."""

    async def test_m16_a_bot_pinned_approver_leaves_the_room_mint_dead(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            meta=_guild_meta(user_id=APPROVER),
            designations={CHANNEL: BOT_ID},
        )
        result = await mint_for(flow)
        assert "reason=approver-not-pinned" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None
        assert _budget_count() == 0


class TestTheDmPathIgnoresTheMap:
    """M-17a / M-17b (NT-8) — the map legs NEVER override the identity
    legs: a DM listed in the map is inert; a group DM listed in the map
    is refused at leg 2, before the map."""

    async def test_m17a_a_dm_whose_channel_is_listed_mints_anyway(
        self, monkeypatch, caplog
    ) -> None:
        """M-17a — ``INVARIANT-DM``: the DM path never reads the map, so
        the entry is silently inert (``mint_is_dm=1``; NO map_digest)."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            flow = build_flow(
                meta=build_discord_meta(channel_id=DM_CHANNEL),
                designations={DM_CHANNEL: APPROVER},
            )
            mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        row = flow.token_repo.find_live(flow.version_id)
        assert row is not None and row.mint_is_dm == 1
        line = _mint_ok_line(caplog)
        assert "private=1" in line, line
        assert "map_digest" not in line, (
            "a DM mint records NO map_digest — the DM path never reads "
            "the designation variable (INVARIANT-DM / AC13)"
        )

    async def test_m17b_a_group_dm_channel_listed_in_the_map_is_refused(
        self, monkeypatch
    ) -> None:
        """M-17b — the group-DM leg precedes the map ⇒ the entry is
        inert; the refusal is the UNCHANGED group-DM code."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            meta=build_discord_meta(
                is_dm=True, is_group_dm=True, channel_id=DM_CHANNEL
            ),
            designations={DM_CHANNEL: APPROVER},
        )
        result = await mint_for(flow)
        assert "reason=mint-requires-private-channel" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None
        assert _budget_count() == 0


class TestTheAbsentGroupDmKeyCell:
    """M-18 (S7) — a guild turn whose meta LACKS the ``is_group_dm`` key
    (the interaction path) is never designation-eligible: the designated
    branch is gated on ``discord_meta.get("is_group_dm") is False``."""

    async def test_m18_an_absent_is_group_dm_key_falls_through_to_the_refusal(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        meta = _guild_meta()
        del meta["is_group_dm"]  # the interaction-path shape
        flow = build_flow(meta=meta, designations={CHANNEL: APPROVER})
        result = await mint_for(flow)
        assert "reason=mint-requires-private-channel" in result, result
        assert flow.token_repo.find_live(flow.version_id) is None
        assert _budget_count() == 0


class TestTheM2FamilyCell:
    """M-2's NAMED family cell (AC2's family-test rule, as amended
    2026-11-12): "any human mints in a 1:1 DM" was previously covered
    only by the M-1 + A-2 conjunction — both riding the fixture's
    DEFAULT human. This cell mints with a DISTINCT ``user_id``, so the
    daemon-derived ``mint_user_id`` is asserted against a non-default
    human (K-8: the identity comes from the daemon-owned window row,
    never from a tool argument)."""

    async def test_m2_a_dm_mint_by_a_distinct_human_records_that_human(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(
            meta=build_discord_meta(is_dm=True, user_id=OTHER_HUMAN),
            designations=None,  # the map scrubbed — the DM path mints anyway
        )
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        row = flow.token_repo.find_live(flow.version_id)
        assert row is not None and row.mint_is_dm == 1
        assert row.mint_user_id == OTHER_HUMAN, (
            "the mint records the DAEMON-DERIVED human from the window "
            "row — here the DISTINCT human, not the fixture default"
        )
        assert row.mint_user_id != USER_ID, (
            "the cell exists to prove a NON-default human mints"
        )


# ═════════════════════════════════════════════════════════════════════════
# The APPROVE cells — §3 of the matrix (the new ones; A-1…A-3 stay in the
# UNCHANGED pin files, see the census above).
# ═════════════════════════════════════════════════════════════════════════


class TestTheDesignatedChannelApprove:
    """A-4 — designated ⇒ the same designated channel, by the pinned
    approver ⇒ PASS, the token consumed, the FULL provenance chain."""

    async def test_a4_the_full_provenance_chain(self, monkeypatch) -> None:
        from daemon.repositories.prompt_assent.designation import (
            designation_digest,
            parse_designations,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = _designated_flow()
        mint = await mint_for(flow)
        token = _token_of(mint)
        live = flow.token_repo.find_live(flow.version_id)
        echo(flow, token, **_echo_designated_kwargs())
        result = await decide(flow, token=token)
        assert "prompt_assent approve — OK" in result, result

        row = flow.token_repo.find_by_id(live.token_id)
        assert row.status == "consumed", "the token is consumed"
        assert flow.repo.get_version(flow.version_id).state == "approved"

        payload = _last_audit_payload(flow.engine)
        audit = _last_audit_row(flow.engine)
        expected_digest = designation_digest(
            parse_designations(json.dumps({CHANNEL: APPROVER})).map
        )
        # THE FULL CHAIN (matrix §3 A-4): item, user, channel, message
        # id, render hash, relay agent — plus the map digest (D-15(b)).
        assert audit.version_id == flow.version_id, "the item"
        assert payload["relay_user_id"] == APPROVER, "the user"
        assert payload["relay_channel_id"] == CHANNEL, "the channel"
        assert payload["relay_message_id"] == MESSAGE_ID, "the message id"
        assert payload["render_sha256"] == row.render_sha256, "the render hash"
        assert payload["relay_agent"] == "ari", "the relay agent"
        assert payload["map_digest"] == expected_digest
        assert payload["approval_path"] == "chat-assent"
        assert payload["token_id"] == live.token_id
        assert payload["relay_external_user_id"] == (
            f"channel:{GUILD_ID}:{CHANNEL}:{APPROVER}"
        )


class TestTheApproveBurnCells:
    """A-5 … A-8 — the mismatch-class legs keep their burn: the durable
    counter increments, the token stays LIVE below the threshold-5
    revoke, and the K-9 hourly budget consumes (mismatch class)."""

    async def test_a5_a_different_human_in_the_designated_channel_burns(
        self, monkeypatch
    ) -> None:
        """A-5 — the 4e leg fires BEFORE 4f: ``token-approver-mismatch``."""
        flow, token, live = await _mint_designated(monkeypatch)
        echo(flow, token, user_id=OTHER_HUMAN, **_echo_designated_kwargs())
        result = await decide(flow, token=token)
        assert "reason=token-approver-mismatch" in result, result
        assert "approver-not-pinned" not in result, result
        row = flow.token_repo.find_by_id(live.token_id)
        assert row.attempts == 1 and row.status == "live", (
            "the durable counter increments; the token stays live below "
            f"threshold {PROMPT_ASSENT_REVOKE_THRESHOLD}"
        )
        assert _budget_count() == 1, "the mismatch class consumes the budget"

    async def test_a6_a_non_designated_guild_channel_is_a_channel_mismatch(
        self, monkeypatch
    ) -> None:
        """A-6 — leg 4d: a different (undesignated) guild channel."""
        flow, token, live = await _mint_designated(monkeypatch)
        echo(
            flow,
            token,
            is_dm=False,
            is_group_dm=False,
            guild_id=GUILD_ID,
            channel_id=SECOND_CHANNEL,  # exists, but NOT designated
            channel_type="text",
        )
        result = await decide(flow, token=token)
        assert "reason=token-channel-mismatch" in result, result
        row = flow.token_repo.find_by_id(live.token_id)
        assert row.attempts == 1 and row.status == "live"
        assert _budget_count() == 1

    async def test_a7_a_group_dm_approve_is_a_channel_mismatch(
        self, monkeypatch
    ) -> None:
        """A-7 — designated ⇒ a group DM: leg 4d (a group DM is a
        different channel surface)."""
        flow, token, live = await _mint_designated(monkeypatch)
        echo(
            flow,
            token,
            is_dm=True,
            is_group_dm=True,
            guild_id=None,
            channel_id=DM_CHANNEL,
            channel_type="group",
        )
        result = await decide(flow, token=token)
        assert "reason=token-channel-mismatch" in result, result
        row = flow.token_repo.find_by_id(live.token_id)
        assert row.attempts == 1 and row.status == "live"
        assert _budget_count() == 1

    async def test_a8_a_dm_approve_is_a_channel_mismatch(
        self, monkeypatch
    ) -> None:
        """A-8 — designated ⇒ a 1:1 DM: leg 4d (cross-surface)."""
        flow, token, live = await _mint_designated(monkeypatch)
        echo(
            flow,
            token,
            is_dm=True,
            is_group_dm=False,
            channel_id=DM_CHANNEL_TWO,
            channel_type="dm",
        )
        result = await decide(flow, token=token)
        assert "reason=token-channel-mismatch" in result, result
        row = flow.token_repo.find_by_id(live.token_id)
        assert row.attempts == 1 and row.status == "live"
        assert _budget_count() == 1


class TestTheApproveNoBurnCells:
    """A-9 / A-10 / A-11 — the 4f SPLIT (refusal-cell-matrix §1.3): the
    two NEW codes refuse BEFORE ``_record_mismatch`` — no counter
    motion, no revoke, no budget burn. A-11 is the DM blast radius."""

    async def test_a9_noburn_a_redesignated_channel_refuses_the_old_approver(
        self, monkeypatch
    ) -> None:
        """A-9 — the re-designation window (D-6): the operator re-pinned
        the channel to V between the mint and the approve; U's approve
        refuses ``approver-not-pinned`` — the token is UNTOUCHED."""
        flow, token, live = await _mint_and_reshape(
            monkeypatch, {CHANNEL: SECOND_APPROVER}
        )
        result = await decide(flow, token=token)
        assert "reason=approver-not-pinned" in result, result
        assert "no longer this room's current pinned approver" in result, (
            result  # the APPROVE-side detail (architect §6.4)
        )
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"
        _assert_no_burn(flow, live.token_id)

    async def test_a10_noburn_an_invalid_map_at_decision_time_spares_the_token(
        self, monkeypatch
    ) -> None:
        """A-10 — an INVALID map at decision time (an OPERATOR fault ⇒
        no user burn, D-17's asymmetry counterpart): refuses
        ``designation-config-invalid``, the token is UNTOUCHED."""
        flow, token, live = await _mint_and_reshape(monkeypatch, "{bad")
        result = await decide(flow, token=token)
        assert "reason=designation-config-invalid" in result, result
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"
        _assert_no_burn(flow, live.token_id)

    async def test_a11_an_invalid_map_never_touches_a_dm_decision(
        self, monkeypatch
    ) -> None:
        """A-11 — the blast-radius cell on the approve side: a DM mint
        and a DM approve with an INVALID map still PASS, consumed."""
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = build_flow(designations="{bad")
        mint = await mint_for(flow)
        token = _token_of(mint)
        echo(flow, token)
        result = await decide(flow, token=token)
        assert "prompt_assent approve — OK" in result, result
        row = flow.token_repo.find_by_hash(token_hash(token))
        assert row is not None and row.status == "consumed", (
            "the DM decision consumed its token"
        )
        assert flow.repo.get_version(flow.version_id).state == "approved"
        # T3.3's no-burn legs for A-11 — the `_assert_no_burn` helper
        # does NOT fit as-is: its revoke leg asserts ``status == "live"``,
        # but THIS cell's token is CONSUMED BY DESIGN (the DM decision
        # passes and spends the token). What must hold is "untouched
        # EXCEPT the spend": the durable mismatch counter never moved and
        # the K-9 hourly budget never moved.
        assert row.attempts == 0, (
            "the DM decision spends the token WITHOUT a mismatch burn"
        )
        assert _budget_count() == 0, (
            "the invalid map at decision time is an operator fault — "
            "no budget burn (D-17's asymmetry)"
        )


class TestTheWithdrawnDesignation:
    """A-12 — the WITHDRAWN designation (D-17, LEADER-RULED KEEP BURN):
    a user attempting to approve in a formerly-valid room is a USER-side
    mismatch ⇒ ``token-channel-mismatch`` BURNS (durable counter + K-9
    budget). Do NOT 'fix' this asymmetry: A-10 (invalid map = operator
    fault) does not burn; A-12 (withdrawn = user-side) does."""

    async def test_a12_a_withdrawn_designation_keeps_the_burn(
        self, monkeypatch
    ) -> None:
        flow, token, live = await _mint_and_reshape(monkeypatch, {})
        result = await decide(flow, token=token)
        assert "reason=token-channel-mismatch" in result, result
        row = flow.token_repo.find_by_id(live.token_id)
        assert row.attempts == 1, "A-12 keeps the burn (D-17)"
        assert row.status == "live", "below the threshold-5 revoke"
        assert _budget_count() == 1, "the mismatch class consumes the budget"
        assert flow.repo.get_version(flow.version_id).state == "pending_approval"


# ═════════════════════════════════════════════════════════════════════════
# T3.4 — the map-unset equivalence cell (PR-3): with the map UNSET (the
# autouse default of this file), every pre-existing cell produces the
# identical code and ledger shape as under v0.29.18.
# ═════════════════════════════════════════════════════════════════════════


class TestTheMapUnsetEquivalence:
    async def test_the_unset_map_is_the_v02918_posture(self, monkeypatch) -> None:
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        # The map is SCRUBBED by the autouse fixture — the default rig.

        # (1) a group DM: the byte-identical envelope (code + detail).
        flow = build_flow(meta=build_discord_meta(is_dm=True, is_group_dm=True))
        result = await mint_for(flow)
        assert result == V02918_NOT_PRIVATE_ENVELOPE, result
        assert flow.token_repo.find_live(flow.version_id) is None

        # (2) an absent discriminator: the byte-identical envelope.
        meta = build_discord_meta()
        meta.pop("is_group_dm")
        flow = build_flow(meta=meta)
        assert await mint_for(flow) == V02918_NOT_PRIVATE_ENVELOPE

        # (3) an undesignated guild channel: the byte-identical envelope.
        flow = build_flow(meta=_guild_meta(user_id=APPROVER))
        assert await mint_for(flow) == V02918_NOT_PRIVATE_ENVELOPE

        # (4) a DM mint: the identical ledger shape — mint_is_dm=1,
        # live, the approver recorded (the DM mint's NO-map_digest INFO
        # line is asserted in TestTheDmPathIgnoresTheMap).
        flow = build_flow()
        mint = await mint_for(flow)
        assert "prompt_assent mint" in mint, mint
        row = flow.token_repo.find_live(flow.version_id)
        assert row is not None and row.mint_is_dm == 1
        assert row.mint_user_id == USER_ID

        # (5) an EXPLICIT empty map is behaviourally identical to unset.
        flow = build_flow(
            meta=build_discord_meta(is_dm=True, is_group_dm=True),
            designations={},
        )
        assert await mint_for(flow) == V02918_NOT_PRIVATE_ENVELOPE


# ═════════════════════════════════════════════════════════════════════════
# T3.8 — the RUNTIME resolver-raises pin (PR-1, BLOCKING — the PRIMARY
# proof of INVARIANT-DM, re-homed here per the plan; P2's census file
# carries its own copy (``test_the_dm_path_never_resolves_the_map``) and
# is NOT edited — the duplicate is reported in the lane report).
# ═════════════════════════════════════════════════════════════════════════


def _boom() -> None:
    raise AssertionError("the DM path reached the designation resolver")


def _normalize_envelope(text: str) -> str:
    """Normalize the per-flow values (UUIDs everywhere — the item id
    appears in the header AND twice inside the canonical render — plus
    the per-mint token, the wall-clock expiry and the per-flow render
    hash) so two DM-mint envelopes are comparable: the structure, the
    item line, the code and the render BODY must be identical; these
    values are per-flow by construction."""
    text = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "<uuid>",
        text,
    )
    text = re.sub(r"token: \S+", "token: <token>", text)
    text = re.sub(r"expires_at: \S+", "expires_at: <ts>", text)
    text = re.sub(r"render_sha256: \S+", "render_sha256: <sha>", text)
    text = re.sub(r"token_fingerprint: \S+", "token_fingerprint: <fp>", text)
    return re.sub(r"attested_at: \S+", "attested_at: <ts>", text)


class TestTheRuntimeResolverRaisesPin:
    async def _dm_mint(self):
        flow = build_flow(designations=None)  # an explicit scrub
        return flow, await mint_for(flow)

    async def test_pr1_the_dm_path_never_resolves_the_map(
        self, monkeypatch
    ) -> None:
        """PR-1 — with the resolver MONKEYPATCHED TO RAISE, a DM mint, a
        DM approve and a lane-disabled call all complete with NO raise
        and IDENTICAL outcomes to the unpatched run (proved by
        execution, not by a source-shape reading)."""
        import daemon.tools.prompt_assent as pa

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")

        # ── the UNPATCHED runs (the reference outcomes) ──────────────
        ref_flow, ref_mint = await self._dm_mint()
        ref_row = ref_flow.token_repo.find_live(ref_flow.version_id)
        ref_token = _token_of(ref_mint)
        echo(ref_flow, ref_token)
        ref_approve = await decide(ref_flow, token=ref_token)
        monkeypatch.setenv(KILL_SWITCH_ENV, "0")
        ref_disabled = await mint_for(build_flow(designations=None))
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")

        # ── the PATCHED runs (the resolver raises on ANY reach) ─────
        monkeypatch.setattr(pa, "_resolve_designations", _boom)

        flow, mint = await self._dm_mint()  # must NOT raise
        assert _normalize_envelope(mint) == _normalize_envelope(ref_mint), (
            "the DM mint outcome must be identical with the resolver "
            "patched to raise"
        )
        row = flow.token_repo.find_live(flow.version_id)
        assert (row.status, row.mint_is_dm, row.mint_user_id) == (
            ref_row.status,
            ref_row.mint_is_dm,
            ref_row.mint_user_id,
        ), "the DM mint ledger shape is identical"

        token = _token_of(mint)
        echo(flow, token)
        approve = await decide(flow, token=token)  # must NOT raise
        assert _normalize_envelope(approve) == _normalize_envelope(ref_approve), (
            "the DM approve outcome must be identical with the resolver "
            "patched to raise"
        )

        # the lane-disabled call takes the earliest exit — no map either
        monkeypatch.setenv(KILL_SWITCH_ENV, "0")
        disabled = await mint_for(build_flow(designations=None))
        assert disabled == ref_disabled, (
            "the lane-disabled outcome must be identical"
        )
        assert "reason=lane-disabled" in disabled, disabled

    async def test_mutation_making_the_dm_path_reach_the_resolver_reddens_pr1(
        self, monkeypatch
    ) -> None:
        """P-2b's counter-mutation — the DM short-circuit is hoisted
        (``is_private_assent_channel`` → always False, which is exactly
        "the DM path now calls the resolver"). Under the mutation the
        DM mint REACHES the resolver, the raise fires, and the PR-1
        cell's no-raise/identical-outcome assertion REDS. NOTE: the
        tool wrapper converts a raise into a FAILED envelope ("tools
        NEVER raise"), so the reddening evidence is the FAILED outcome
        carrying the resolver's raise message."""
        import daemon.repositories.prompt_assent.mint_context as mc
        import daemon.tools.prompt_assent as pa

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        _, ref_mint = await self._dm_mint()  # unpatched: a real mint
        monkeypatch.setattr(pa, "_resolve_designations", _boom)
        monkeypatch.setattr(mc, "is_private_assent_channel", lambda m: False)
        _, mint = await self._dm_mint()
        with pytest.raises(AssertionError):
            assert _normalize_envelope(mint) == _normalize_envelope(ref_mint), (
                "the PR-1 outcome-equality assertion"
            )
        assert "reason=prompt_assent_internal_error" in mint, (
            "under the mutation the DM mint is no longer a mint"
        )
        assert "the DM path reached the designation resolver" in mint, (
            "the resolver's raise fired ON the DM path — the mutation "
            "is demonstrated"
        )


# ═════════════════════════════════════════════════════════════════════════
# The non-Mapping-meta guard (fix round (c), 2026-11-12): a non-Mapping
# ``discord_meta`` (a caller bug — a list / a str / an int) must fail
# CLOSED to the typed outcome, never raise AttributeError out of the
# classifier.
# ═════════════════════════════════════════════════════════════════════════


class TestTheNonMappingMetaFailClosed:
    async def test_a_non_mapping_discord_meta_fails_closed(self) -> None:
        from daemon.repositories.prompt_assent.mint_context import (
            DesignationResult,
            designation_outcome,
        )

        for bad_meta in (["is_dm"], "is_dm=True", 7):
            outcome = designation_outcome(None, bad_meta, APPROVER)
            assert outcome is DesignationResult.ABSENT_DISCRIMINATOR, (
                bad_meta,
                outcome,
            )


# ═════════════════════════════════════════════════════════════════════════
# T3.9 — the map-digest tests (LEADER RULING 2 control (b) / D-15): the
# digest is stable for a given map, changes when the map changes, and
# NEVER contains a channel id / user id / raw value — asserted for the
# mint INFO line AND the approve/reject audit payload.
# ═════════════════════════════════════════════════════════════════════════


class TestTheMapDigestProvenance:
    async def test_the_mint_info_digest_is_stable_and_content_free(
        self, monkeypatch, caplog
    ) -> None:
        """The mint INFO line's ``map_digest`` equals the parsed map's
        fingerprint, is STABLE across mints of the same map, CHANGES
        when the map changes, and carries no id and no raw value."""
        from daemon.repositories.prompt_assent.designation import (
            designation_digest,
            parse_designations,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        map_one = {CHANNEL: APPROVER}
        expected = designation_digest(parse_designations(json.dumps(map_one)).map)
        assert len(expected) == 12

        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            flow = _designated_flow()
            await mint_for(flow)
            line = _mint_ok_line(caplog)
        digest = line.rsplit("map_digest=", 1)[1].strip()
        assert digest == expected, (digest, expected)
        # CONTENT-FREE: neither the channel id, nor the user id, nor the
        # raw JSON ever appears in the line.
        assert CHANNEL not in line
        assert APPROVER not in line
        assert json.dumps(map_one) not in line

        # STABLE: a second mint of the SAME map renders the same digest
        # (a forced re-mint supersedes the first token — the digest is
        # of the MAP, not of the token).
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            await mint_for(flow, force=True)
        assert _mint_ok_line(caplog).rsplit("map_digest=", 1)[1].strip() == expected

        # CHANGES when the map changes.
        caplog.clear()
        monkeypatch.setenv(ENV_NAME, json.dumps({CHANNEL: OTHER_HUMAN}))
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            other_flow = build_flow(
                meta=_guild_meta(user_id=OTHER_HUMAN),
                designations={CHANNEL: OTHER_HUMAN},
            )
            await mint_for(other_flow)
        other = _mint_ok_line(caplog).rsplit("map_digest=", 1)[1].strip()
        assert other != expected, "a changed map must change the digest"
        assert OTHER_HUMAN not in other

    async def test_mutation_dropping_the_digest_reddens_the_cell(
        self, monkeypatch, caplog
    ) -> None:
        """The digest pin's counter-mutation — ``_DesignationCache.digest``
        returns ``None`` (the provenance is dropped) ⇒ the cell's
        ``map_digest=`` assertion REDS."""
        import daemon.tools.prompt_assent as pa

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setattr(
            pa._DesignationCache,
            "digest",
            property(lambda self: None),
        )
        with caplog.at_level(logging.INFO, logger="daemon.tools.prompt_assent"):
            flow = _designated_flow()
            await mint_for(flow)
            line = _mint_ok_line(caplog)
        with pytest.raises(AssertionError):
            assert "map_digest=" in line, line

    async def test_the_approve_audit_payload_carries_the_content_free_digest(
        self, monkeypatch
    ) -> None:
        from daemon.repositories.prompt_assent.designation import (
            designation_digest,
            parse_designations,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = _designated_flow()
        token = _token_of(await mint_for(flow))
        echo(flow, token, **_echo_designated_kwargs())
        assert "prompt_assent approve — OK" in await decide(flow, token=token)

        payload = _last_audit_payload(flow.engine)
        expected = designation_digest(
            parse_designations(json.dumps({CHANNEL: APPROVER})).map
        )
        assert payload["map_digest"] == expected
        assert CHANNEL not in payload["map_digest"]
        assert APPROVER not in payload["map_digest"]

    async def test_the_reject_audit_payload_carries_the_content_free_digest(
        self, monkeypatch
    ) -> None:
        from daemon.repositories.prompt_assent.designation import (
            designation_digest,
            parse_designations,
        )

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = _designated_flow()
        token = _token_of(await mint_for(flow))
        echo(flow, token, **_echo_designated_kwargs())
        assert "prompt_assent reject — OK" in await decide(
            flow, action="reject", token=token, reject_reason="not good enough"
        )
        payload = _last_audit_payload(flow.engine)
        expected = designation_digest(
            parse_designations(json.dumps({CHANNEL: APPROVER})).map
        )
        assert payload["map_digest"] == expected
        assert CHANNEL not in payload["map_digest"]
        assert APPROVER not in payload["map_digest"]


# ═════════════════════════════════════════════════════════════════════════
# The remaining named counter-mutations (P-3, P-7, P-8).
# ═════════════════════════════════════════════════════════════════════════


class TestTheP3MismatchCodesMutation:
    """P-3 — the two new codes are NOT in ``MISMATCH_CODES``. Its
    counter-mutation ADDS one ⇒ the no-burn property (A-9's budget
    assertion) REDS."""

    async def test_p3_the_new_codes_are_outside_the_mismatch_class(
        self,
    ) -> None:
        from daemon.tools.prompt_assent import MISMATCH_CODES

        assert "approver-not-pinned" not in MISMATCH_CODES
        assert "designation-config-invalid" not in MISMATCH_CODES

    async def test_mutation_adding_a_new_code_to_mismatch_codes_reddens(
        self, monkeypatch
    ) -> None:
        import daemon.tools.prompt_assent as pa

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = _designated_flow()
        token = _token_of(await mint_for(flow))
        live = flow.token_repo.find_live(flow.version_id)
        monkeypatch.setenv(ENV_NAME, json.dumps({CHANNEL: SECOND_APPROVER}))
        echo(flow, token, **_echo_designated_kwargs())
        # under the mutation the A-9 refusal consults the budget
        monkeypatch.setattr(
            pa,
            "MISMATCH_CODES",
            pa.MISMATCH_CODES | {"approver-not-pinned"},
        )
        result = await decide(flow, token=token)
        with pytest.raises(AssertionError):
            assert _budget_count() == 0, "no budget burn"
        assert _budget_count() == 1, (
            "under the mutation the new code burned the hourly budget"
        )
        assert "reason=approver-not-pinned" in result, result
        assert flow.token_repo.find_by_id(live.token_id).attempts == 0


class TestTheP7LegOrderMutation:
    """P-7 — the group-DM / absent-discriminator legs precede the map.
    Its counter-mutation hoists ONLY the invalid-map leg above the
    group-DM leg ⇒ the M-11 cell REDS (``designation-config-invalid``
    where the unchanged code must stand)."""

    async def test_mutation_hoisting_the_invalid_map_leg_reddens_m11(
        self, monkeypatch
    ) -> None:
        import daemon.tools.prompt_assent as pa

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        monkeypatch.setattr(
            pa, "designation_outcome", _mutated_invalid_map_first
        )
        flow = build_flow(
            meta=build_discord_meta(is_dm=True, is_group_dm=True),
            designations="{bad",
        )
        result = await mint_for(flow)
        with pytest.raises(AssertionError):
            assert "reason=mint-requires-private-channel" in result, result
            assert "designation-config-invalid" not in result, result
        assert "reason=designation-config-invalid" in result, (
            "under the mutation the operator-fault code leaked onto a "
            "group DM"
        )


class TestTheP8NoBurnMutation:
    """P-8 — no burn on the new codes. Its counter-mutation EMPTIES
    ``PHASE1_5_PLACED_CODES`` (the 4f split's discriminator) ⇒ the two
    new codes fall through to ``_record_mismatch`` ⇒ the no-burn
    assertion REDS (the durable counter moves)."""

    async def test_mutation_emptying_the_placed_codes_set_reddens_the_noburn_cell(
        self, monkeypatch
    ) -> None:
        import daemon.tools.prompt_assent as pa

        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        flow = _designated_flow()
        token = _token_of(await mint_for(flow))
        live = flow.token_repo.find_live(flow.version_id)
        monkeypatch.setenv(ENV_NAME, json.dumps({CHANNEL: SECOND_APPROVER}))
        echo(flow, token, **_echo_designated_kwargs())
        monkeypatch.setattr(pa, "PHASE1_5_PLACED_CODES", frozenset())
        result = await decide(flow, token=token)
        with pytest.raises(AssertionError):
            _assert_no_burn(flow, live.token_id)
        row = flow.token_repo.find_by_id(live.token_id)
        assert row.attempts == 1, (
            "under the mutation the new code reached _record_mismatch"
        )
        assert row.status == "live", "below the threshold-5 revoke"
        assert "reason=approver-not-pinned" in result, result


# ═════════════════════════════════════════════════════════════════════════
# The MUTATED orderings — the counter-mutation functions. These are
# NEVER production code: they exist only inside the mutation cells above
# (the house mutation discipline).
# ═════════════════════════════════════════════════════════════════════════


def _mutated_map_lookup_first(designations, discord_meta, user_id):
    """P-2c's mutation: the pinned-lookup + equality legs are hoisted
    ABOVE the group-DM leg (the map is consulted first — the natural
    wrong implementation the matrix's ordering exists to forbid)."""
    from daemon.repositories.prompt_assent.mint_context import DesignationResult

    meta: Any = discord_meta if discord_meta else {}
    is_dm = meta.get("is_dm")
    is_group_dm = meta.get("is_group_dm")

    if is_dm is True and is_group_dm is False:
        return DesignationResult.PASS
    if designations is None or not designations.valid or designations.map is None:
        return DesignationResult.CONFIG_INVALID
    pinned = designations.map.pinned_approver(meta.get("channel_id"))
    if pinned is not None:
        if not (isinstance(user_id, str) and user_id.strip()):
            return DesignationResult.ABSENT_IDENTITY
        if user_id.strip() == pinned:
            return DesignationResult.PASS  # ← M-15's hole: a group DM PASSES
        return DesignationResult.APPROVER_NOT_PINNED
    # the identity legs come LAST under the mutation
    if is_group_dm is True:
        return DesignationResult.GROUP_DM_SHAPE
    if is_dm is not False or is_group_dm is not False:
        return DesignationResult.ABSENT_DISCRIMINATOR
    return DesignationResult.NOT_DESIGNATED


def _mutated_invalid_map_first(designations, discord_meta, user_id):
    """P-7's mutation: ONLY the invalid-map leg is hoisted above the
    group-DM leg — a group DM with a broken map would classify the
    operator fault instead of the unchanged group-DM refusal."""
    from daemon.repositories.prompt_assent.mint_context import DesignationResult

    meta: Any = discord_meta if discord_meta else {}
    is_dm = meta.get("is_dm")
    is_group_dm = meta.get("is_group_dm")

    if is_dm is True and is_group_dm is False:
        return DesignationResult.PASS
    if designations is None or not designations.valid or designations.map is None:
        return DesignationResult.CONFIG_INVALID  # ← M-11's hole: BEFORE leg 2
    if is_group_dm is True:
        return DesignationResult.GROUP_DM_SHAPE
    if is_dm is not False:
        return DesignationResult.ABSENT_DISCRIMINATOR
    if is_group_dm is not False:
        return DesignationResult.ABSENT_DISCRIMINATOR
    pinned = designations.map.pinned_approver(meta.get("channel_id"))
    if pinned is None:
        return DesignationResult.NOT_DESIGNATED
    if not (isinstance(user_id, str) and user_id.strip()):
        return DesignationResult.ABSENT_IDENTITY
    if user_id.strip() != pinned:
        return DesignationResult.APPROVER_NOT_PINNED
    return DesignationResult.PASS
