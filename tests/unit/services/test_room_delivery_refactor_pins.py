"""The P5 tidier H1/H2 extraction pins — group chat P5.

The P3 tidier left two extraction items for P5 (the extraction itself
is a REFACTOR: the behavior must be byte-identical, and the existing
P3 pins must stay green — this module adds the STRUCTURAL pins that
make the extraction itself falsifiable).

H1 — ``_lane_b_enqueue`` was ~160 lines at five levels of nesting; it
is now a two-branch dispatcher over the two named seams
(``_reroute_after_cas_miss`` / ``_deliver_after_cas_success``).

H2 — the TERMINAL branch (lane c / c′) lived in THREE copies (the
tagged lane, the lane-(b) CAS-miss re-route, the boot pass), so the
pinned pair #2 invariant ("never parent-route at boot") was enforced
by an ``if at_boot:`` guard repeated three times — and A-P3-21/A-P3-24
already had to repair TWO of the three copies SEPARATELY. It now
lives in ONE implementation (``_terminal_outcome``), plus the paused
branch (``_park_paused``).

The ADDRESSED lane's terminal branch is deliberately NOT folded into
``_terminal_outcome``: it mints the SAME durable row but delivers the
(c′) PARENT NOTICE with a different shape (the excerpt-bearing notice
text + the notice event) — a different sequence, not a copy. The pin
below proves there is exactly ONE tagged-terminal implementation and
exactly TWO ``_mint_proposal`` call sites (one per distinct lane).

The mutation leg: removing the ``if at_boot:`` guard inside
``_terminal_outcome`` MUST redden the boot pair-#2 pin (the
"never parent-route at boot" guarantee) — the extracted
implementation is the ONLY carrier of the invariant, so the guard
cannot be lost by an edit that touches one copy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session

import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.room import RoomChatService, RoomRepository
from daemon.services.room_delivery import RoomDeliveryService
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    seed_instance,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = (
    REPO_ROOT / "daemon" / "services" / "room_delivery.py"
).read_text(encoding="utf-8")


# ── H1 — the lane-(b) split ────────────────────────────────────────────


class TestTheH1Split:
    def test_the_two_named_seams_exist(self) -> None:
        assert "async def _reroute_after_cas_miss(" in SOURCE
        assert "async def _deliver_after_cas_success(" in SOURCE

    def test_lane_b_is_a_two_branch_dispatcher(self) -> None:
        """The dispatcher body: the CAS, then ONE call per seam. The
        five-level nesting is gone."""
        start = SOURCE.index("    async def _lane_b_enqueue(")
        end = SOURCE.index("    async def _reroute_after_cas_miss(")
        body = SOURCE[start:end]
        assert body.count("await self._reroute_after_cas_miss(") == 1
        assert body.count("await self._deliver_after_cas_success(") == 1
        # The dispatcher holds no lane logic of its own.
        assert "set_injection" not in body, (
            "the injection logic belongs to the seams, not the dispatcher"
        )
        assert "enqueue_message" not in body
        assert "_mint_proposal" not in body
        assert len(body.splitlines()) < 60, (
            f"the dispatcher is {len(body.splitlines())} lines — the H1 "
            f"split did not land"
        )

    def test_the_seams_are_reachable_from_the_dispatcher(self) -> None:
        """Both seams are called from the dispatcher AND from the
        tagged lane's two descents."""
        assert SOURCE.count("await self._lane_b_enqueue(") == 2, (
            "the tagged lane reaches the lane-(b) family twice (the "
            "graphless-running downgrade + the idle family)"
        )


# ── H2 — the terminal + paused extraction ──────────────────────────────


class TestTheH2Extraction:
    def test_the_two_helpers_exist(self) -> None:
        assert "async def _terminal_outcome(" in SOURCE
        assert "async def _park_paused(" in SOURCE

    def test_the_tagged_terminal_branch_has_exactly_one_implementation(
        self,
    ) -> None:
        """The mint + admission + cursor-advance + at_boot sequence
        exists ONCE. The addressed lane keeps its own (different)
        sequence — so there are exactly TWO ``_mint_proposal`` call
        sites, one per distinct lane."""
        assert SOURCE.count("await self._mint_proposal(") == 2, (
            "the mint must have exactly two call sites: the tagged "
            "terminal branch (inside _terminal_outcome) and the "
            "addressed terminal branch (the c′ notice lane)"
        )
        terminal_start = SOURCE.index("    async def _terminal_outcome(")
        terminal_end = SOURCE.index("    async def _park_paused(")
        assert "await self._mint_proposal(" in SOURCE[terminal_start:terminal_end]

    def test_the_at_boot_guard_lives_in_exactly_one_place(self) -> None:
        """PINNED PAIR #2 is enforced by ONE ``if at_boot:`` guard."""
        code_guards = [
            line
            for line in SOURCE.splitlines()
            if line.strip() == "if at_boot:"
        ]
        assert len(code_guards) == 1, (
            f"the boot gate must be written once, found {len(code_guards)} "
            f"— three copies were what A-P3-21/A-P3-24 had to repair "
            f"separately"
        )

    def test_the_parked_branch_lives_in_exactly_one_place(self) -> None:
        assert SOURCE.count("await self._park_paused(") == 2, (
            "both paused callers (the tagged lane + the CAS-miss "
            "re-route) go through the ONE helper"
        )
        # The audit emission of the parked outcome lives in the helper
        # only — the other occurrences are the ADDRESSED lane's own
        # (non-escalating) paused branch + the boot outcome counters.
        helper_start = SOURCE.index("    async def _park_paused(")
        helper_end = SOURCE.index("    async def _lane_b_enqueue(")
        assert SOURCE[helper_start:helper_end].count('"parked_paused"') == 2, (
            "the helper emits the audit payload + returns the label"
        )


# ── the behavioral re-aim (the extracted code still behaves) ───────────


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    eng = make_room_engine(tmp_path, "room_delivery_refactor.db")
    yield eng
    eng.dispose()


def _manager():
    manager = MagicMock()
    manager.has_live_graph_task = MagicMock(return_value=True)
    manager.set_injection = MagicMock()
    manager.enqueue_message = AsyncMock(return_value=MagicMock(queued=True))
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    return manager


def test_the_extracted_terminal_branch_never_routes_the_parent_at_boot(engine):
    """The extracted implementation carries pinned pair #2: at boot the
    terminal branch is MINT-ONLY — the parent is never injected /
    enqueued."""
    repo = RoomRepository(engine)
    svc = RoomChatService(engine)
    owner = "owner-1"
    seed_instance(engine, owner, None, "idle")
    room_id = make_room(repo, tree_root=owner, owner=owner, owner_member_name="lead")
    seed_instance(engine, "child-1", owner, "completed")
    join_member(engine, room_id, "child-1", "worker")

    manager = _manager()
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=owner, author_name="lead",
            text_body="ping", tagged_members=["worker"], addressed_members=[],
        )
        room = await svc.get_room(room_id)
        member = await svc.get_member(room_id, "child-1")
        return await delivery._terminal_outcome(
            room_id=room_id,
            post=post,
            member=member,
            trigger="tag_terminal",
            parent_iid=room["owner_instance_id"],
            at_boot=True,
        )

    outcome = asyncio.run(run())
    assert outcome == "proposal", outcome
    # The parent was NEVER routed to (pinned pair #2).
    assert manager.set_injection.call_count == 0
    assert manager.enqueue_message.call_count == 0
    # The durable proposal row IS there (the mint-only half).
    kinds = [
        m["kind"]
        for m in repo.list_messages(
            room_id, caller_instance_id=owner, after_seq=0, limit=50
        )
    ]
    assert "task_proposal" in kinds, kinds


def test_the_extracted_terminal_branch_routes_the_parent_outside_boot(engine):
    """The complement: OUTSIDE boot the same helper routes the proposal
    to the parent (the behavior is unchanged by the extraction)."""
    repo = RoomRepository(engine)
    svc = RoomChatService(engine)
    owner = "owner-1"
    seed_instance(engine, owner, None, "idle")
    room_id = make_room(repo, tree_root=owner, owner=owner, owner_member_name="lead")
    seed_instance(engine, "child-1", owner, "completed")
    join_member(engine, room_id, "child-1", "worker")

    manager = _manager()
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        post = await svc.append_message(
            room_id=room_id, author_instance_id=owner, author_name="lead",
            text_body="ping", tagged_members=["worker"], addressed_members=[],
        )
        room = await svc.get_room(room_id)
        member = await svc.get_member(room_id, "child-1")
        return await delivery._terminal_outcome(
            room_id=room_id,
            post=post,
            member=member,
            trigger="tag_terminal",
            parent_iid=room["owner_instance_id"],
            at_boot=False,
        )

    outcome = asyncio.run(run())
    assert outcome == "proposal", outcome
    # The parent is IDLE here → the parent-target matrix's lane (b):
    # the proposal is ENQUEUED to the parent (never revived).
    assert manager.enqueue_message.call_count == 1, (
        "outside boot the parent IS routed (the proposal notice)"
    )
    assert manager.set_injection.call_count == 0


def test_the_extracted_parked_branch_emits_the_audit_event(engine):
    """The paused branch: park + audit, one implementation."""
    repo = RoomRepository(engine)
    svc = RoomChatService(engine)
    owner = "owner-1"
    seed_instance(engine, owner, None, "idle")
    room_id = make_room(repo, tree_root=owner, owner=owner, owner_member_name="lead")
    seed_instance(engine, "child-1", owner, "paused")
    join_member(engine, room_id, "child-1", "worker")

    emitted: list[tuple] = []
    svc.emit_room_event = AsyncMock(
        side_effect=lambda iid, kind, data: emitted.append((iid, kind, data))
    )
    manager = _manager()
    delivery = RoomDeliveryService(manager, svc)

    async def run():
        return await delivery._park_paused(
            member_iid="child-1", room_id=room_id, seq=7, member_name="worker"
        )

    outcome = asyncio.run(run())
    assert outcome == "parked_paused", outcome
    assert len(emitted) == 1, emitted
    _iid, kind, data = emitted[0]
    assert kind == "room_tag_delivery"
    assert data["outcome"] == "parked_paused"
    assert data["seq"] == 7
