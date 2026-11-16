"""Agent group chat P6 — the GC PURGE sweeper (R2).

WHAT IT PROVES (the sweeper's decision layer — the purge transaction
itself is the P1 ``RoomRepository.purge_room``, already pinned):

* **P-close** — a CLOSED room whose ``closed_at`` is older than 14d
  is purged; one closed 13d ago is NOT (the boundary is pinned on
  BOTH sides).
* **P-idle** — an ACTIVE room idle for more than 72h is purged ONLY
  when EVERY participant is terminal. The participant set is
  ``members(state=active) ∪ owner ∪ owner-ancestry chain`` and
  ``waiting`` IS alive (the shared non-terminal set).
* **The cursor guard** — a room holding a TAGGED row above some
  participant's DELIVERED cursor is SKIPPED (the message is still
  owed). The guard applies to P-idle ONLY.
* **The dry run** — the FIRST sweep of a deployment purges NOTHING
  and reports its candidates; the next sweep purges. The flag is
  durable (the shared meta KV), so a restart does not silently
  re-arm the dry run forever.
* **The purge outcome** — the purged room is NONEXISTENT afterwards
  (R2) and the ONE ``room_purged`` event carries ids + counts only.

The harness is the shared room harness (file-backed SQLite, real
repository, real service facade).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session, select

from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.room.models import ChatRoom
from daemon.repositories.room.service import RoomChatService
from daemon.services.room_gc import (
    ROOM_GC_CLOSE_AGE_DAYS,
    ROOM_GC_IDLE_AGE_HOURS,
    RoomGcSweeper,
)
from tests.helpers.room_harness import (
    join_member,
    make_room,
    make_room_engine,
    seed_instance,
)


@pytest.fixture
def engine(tmp_path):
    eng = make_room_engine(tmp_path, "room_gc_sweeper.db")
    yield eng
    eng.dispose()


@pytest.fixture
def service(engine) -> RoomChatService:
    return RoomChatService(engine)


def _iso(days: float = 0.0, hours: float = 0.0) -> str:
    """An ISO stamp ``days``/``hours`` in the PAST."""
    moment = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return moment.isoformat()


def _set_room_times(
    engine,
    room_id: str,
    *,
    state: str | None = None,
    closed_at: str | None = None,
    last_post_at: str | None = None,
    created_at: str | None = None,
) -> None:
    """Force the lifecycle columns (the create path stamps 'now')."""
    with Session(engine) as s:
        room = s.get(ChatRoom, room_id)
        assert room is not None
        if state is not None:
            room.state = state
        if closed_at is not None:
            room.closed_at = closed_at
        if last_post_at is not None:
            room.last_post_at = last_post_at
        if created_at is not None:
            room.created_at = created_at
        s.commit()


class _FakeKv:
    """A minimal shared-meta-KV stub (durable flag storage)."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.fail_read = False
        self.fail_write = False

    def get_all_as_dict(self, context_key: str) -> dict[str, Any]:
        if self.fail_read:
            raise RuntimeError("kv read boom")
        return dict(self.values.get(context_key, {}))

    def set_many(self, context_key: str, kvs: dict[str, Any]) -> list[Any]:
        if self.fail_write:
            raise RuntimeError("kv write boom")
        self.values.setdefault(context_key, {}).update(kvs)
        return []


def _manager(engine, service: RoomChatService) -> SimpleNamespace:
    """A manager stub carrying the seams the sweeper touches."""
    return SimpleNamespace(_room_chat_service=service, _engine=engine)


def _sweeper(
    manager: SimpleNamespace, kv: _FakeKv | None = None
) -> RoomGcSweeper:
    return RoomGcSweeper(
        manager=manager, kv_repo=kv, kv_context_key="room_gc_test"
    )


# ---------------------------------------------------------------------------
# P-close
# ---------------------------------------------------------------------------


async def test_p_close_purges_a_room_closed_more_than_fourteen_days_ago(
    engine, service
):
    seed_instance(engine, "owner-1")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="old-room"
    )
    _set_room_times(
        engine,
        room_id,
        state="closed",
        closed_at=_iso(days=ROOM_GC_CLOSE_AGE_DAYS + 1),
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["candidates"][0]["room_id"] == room_id
    assert summary["candidates"][0]["trigger"] == "p_close"
    assert summary["purged"] == [room_id]
    # R2: the room is nonexistent afterwards.
    assert service._repo.get_room(room_id) is None


async def test_p_close_keeps_a_room_closed_inside_the_window(engine, service):
    seed_instance(engine, "owner-1")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="recent-room"
    )
    _set_room_times(
        engine,
        room_id,
        state="closed",
        closed_at=_iso(days=ROOM_GC_CLOSE_AGE_DAYS - 1),
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["candidates"] == []
    assert summary["purged"] == []
    assert service._repo.get_room(room_id) is not None


# ---------------------------------------------------------------------------
# P-idle — the liveness map
# ---------------------------------------------------------------------------


async def test_p_idle_purges_when_every_participant_is_terminal(engine, service):
    seed_instance(engine, "owner-1", status="completed")
    seed_instance(engine, "child-1", parent_id="owner-1", status="terminated")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="idle-room"
    )
    join_member(engine, room_id, "child-1", "worker-a")
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["purged"] == [room_id]
    assert summary["candidates"][0]["trigger"] == "p_idle"


@pytest.mark.parametrize(
    "status",
    ["idle", "running", "waiting", "paused", "queued", "waiting_children"],
)
async def test_p_idle_keeps_a_room_with_one_living_participant(
    engine, service, status
):
    """``waiting`` IS alive — every non-terminal status keeps the room."""
    seed_instance(engine, "owner-1", status="completed")
    seed_instance(engine, "child-1", parent_id="owner-1", status=status)
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="live-room"
    )
    join_member(engine, room_id, "child-1", "worker-a")
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["purged"] == []
    assert any(
        skip["room_id"] == room_id
        and skip["reason"].startswith("live-participants:")
        for skip in summary["skipped"]
    ), summary["skipped"]
    assert service._repo.get_room(room_id) is not None


async def test_p_idle_keeps_a_room_whose_owner_ancestor_is_alive(engine, service):
    """The participant set includes the OWNER-ANCESTRY chain."""
    seed_instance(engine, "root-1", status="running")
    seed_instance(engine, "owner-1", parent_id="root-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="root-1", owner="owner-1", name="ancestor-room"
    )
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["purged"] == []
    assert service._repo.get_room(room_id) is not None


async def test_p_idle_keeps_a_room_that_was_active_recently(engine, service):
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="fresh-room"
    )
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=1)
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["candidates"] == []
    assert service._repo.get_room(room_id) is not None


# ---------------------------------------------------------------------------
# The cursor guard
# ---------------------------------------------------------------------------


async def test_p_idle_skips_a_room_holding_an_undelivered_tagged_row(
    engine, service
):
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="tagged-room"
    )
    service._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="ping",
        tagged_members=["lead"],
    )
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["purged"] == []
    assert any(
        skip["room_id"] == room_id
        and skip["reason"] == "undelivered-tagged-rows"
        for skip in summary["skipped"]
    ), summary["skipped"]
    assert service._repo.get_room(room_id) is not None


async def test_p_idle_purges_once_the_tagged_row_was_delivered(engine, service):
    """The guard is about the DELIVERED cursor — advance it and the
    room becomes eligible again."""
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="delivered-room"
    )
    service._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="ping",
        tagged_members=["lead"],
    )
    service._repo.advance_delivered_cursor(room_id, "owner-1", 1)
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["purged"] == [room_id]


async def test_p_close_is_not_guarded_by_the_cursor_guard(engine, service):
    """The cursor guard is permitted on P-idle ONLY — a closed room is
    purged whole, tagged rows or not."""
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="closed-tagged"
    )
    service._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="ping",
        tagged_members=["lead"],
    )
    _set_room_times(
        engine,
        room_id,
        state="closed",
        closed_at=_iso(days=ROOM_GC_CLOSE_AGE_DAYS + 1),
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["purged"] == [room_id]


# ---------------------------------------------------------------------------
# The dry run (first)
# ---------------------------------------------------------------------------


async def test_the_first_sweep_is_a_dry_run_and_the_second_purges(engine, service):
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="dry-room"
    )
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    kv = _FakeKv()
    sweeper = _sweeper(_manager(engine, service), kv)

    await sweeper.execute()
    # The dry run reported the candidate and purged NOTHING.
    assert service._repo.get_room(room_id) is not None
    assert kv.values["room_gc_test"]["dry_run_completed"] is True

    await sweeper.execute()
    assert service._repo.get_room(room_id) is None


async def test_the_dry_run_flag_is_durable_across_sweeper_instances(
    engine, service
):
    """A fresh sweeper (a daemon restart) reads the persisted flag — the
    dry run is not silently re-armed forever."""
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="durable-room"
    )
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    kv = _FakeKv()
    await _sweeper(_manager(engine, service), kv).execute()
    assert service._repo.get_room(room_id) is not None
    # A NEW sweeper instance (the restart) sees the durable flag.
    await _sweeper(_manager(engine, service), kv).execute()
    assert service._repo.get_room(room_id) is None


async def test_a_failing_kv_leaves_the_sweep_in_the_dry_run(engine, service):
    """A KV fault degrades fail-safe: the sweep stays a dry run."""
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="kv-room"
    )
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    kv = _FakeKv()
    kv.fail_read = True
    kv.fail_write = True
    await _sweeper(_manager(engine, service), kv).execute()
    assert service._repo.get_room(room_id) is not None


async def test_an_explicit_dry_run_sweep_purges_nothing(engine, service):
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="explicit-room"
    )
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=True)
    assert len(summary["candidates"]) == 1
    assert summary["purged"] == []
    assert service._repo.get_room(room_id) is not None


# ---------------------------------------------------------------------------
# The purge outcome (R2)
# ---------------------------------------------------------------------------


async def test_the_purge_emits_one_content_free_room_purged_event(engine, service):
    seed_instance(engine, "owner-1", status="completed")
    room_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="event-room"
    )
    service._repo.append_message(
        room_id=room_id,
        author_instance_id="owner-1",
        author_name="lead",
        text_body="secret milestone text",
    )
    _set_room_times(
        engine, room_id, state="active", last_post_at=_iso(hours=100)
    )
    await _sweeper(_manager(engine, service)).sweep(dry_run=False)

    with Session(engine) as s:
        events = s.exec(
            select(Event).where(
                Event.kind == EventKind.ROOM_PURGED.value
            )
        ).all()
    assert len(events) == 1
    data = json.loads(events[0].data)
    assert data["room_id"] == room_id
    assert data["messages_purged"] == 1
    assert "secret milestone text" not in events[0].data


async def test_the_sweeper_is_a_no_op_without_the_room_service(engine):
    manager = SimpleNamespace(_room_chat_service=None)
    summary = await _sweeper(manager).sweep(dry_run=False)
    assert summary["unavailable"] is True
    assert summary["purged"] == []


# ---------------------------------------------------------------------------
# Per-room purge-failure isolation (P6 tidier T7)
# ---------------------------------------------------------------------------


async def test_one_room_purge_failure_lands_in_the_errors_channel(
    engine, service, monkeypatch
):
    """Two candidates; ``purge_room`` raises for EXACTLY one of them.

    The sweep must still purge the OTHER room (one room's failure never
    stops the rest — the fail-open posture) and report the failure on
    the ``errors`` channel carrying the room_id/trigger/error keys.
    """
    seed_instance(engine, "owner-1")
    boom_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="boom-room"
    )
    other_id = make_room(
        service._repo, tree_root="owner-1", owner="owner-1", name="other-room"
    )
    for room_id in (boom_id, other_id):
        _set_room_times(
            engine,
            room_id,
            state="closed",
            closed_at=_iso(days=ROOM_GC_CLOSE_AGE_DAYS + 1),
        )

    real_purge = service.purge_room

    async def _purge_or_boom(room_id, reason=None):
        if room_id == boom_id:
            raise RuntimeError("purge boom")
        return await real_purge(room_id, reason=reason)

    monkeypatch.setattr(service, "purge_room", _purge_or_boom)

    summary = await _sweeper(_manager(engine, service)).sweep(dry_run=False)
    assert summary["purged"] == [other_id]
    assert len(summary["errors"]) == 1
    error = summary["errors"][0]
    assert error["room_id"] == boom_id
    assert error["trigger"] == "p_close"
    assert "purge boom" in error["error"]
    # The failed room still exists — its purge failed, no partial state.
    assert service._repo.get_room(boom_id) is not None
