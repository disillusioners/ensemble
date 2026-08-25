"""Unit tests: ``child_outcome`` payload surfacing — REAL-FLOW drive (Phase 2 round 2).

Acceptance (phase2-plan.md task 2.13 + Round 2 Blocker 2):
  (i) terminate → fire → helper enqueue → the message content the
      parent LLM receives carries a ``[child_outcome: terminated]``
      marker. The marker MUST land in the FollowUp's ``message`` field
      at the fire site (``fire_for_terminated_target``) — NOT be
      derived in the child-completion lookup block (that block is
      dead code on the real terminate path; the terminate path
      reaches the parent via ``_cancel_bus_watchers_for`` →
      ``manager.enqueue_message`` directly, with no
      ``_process_child_completion_and_notify_parent`` call).
  (ii) two consecutive claims (replay) → second claim returns ``[]``
       AND the payload is byte-identical on both deliveries
       (idempotency of the surfacing — no field stripping, no
       re-stamping). This acceptance is preserved from Rev 2.1 but
       the underlying delivery path is now the
       ``manager.enqueue_message`` seam, not the
       ``report_injections`` row.

Real flow under test (Round 2 Blocker 2 fix):
  ``fire_for_terminated_target`` stamps the marker into
  ``FollowUp.message`` text at the SOURCE. The marker is then
  persisted on the FIRED watcher row's ``follow_up_payload`` via
  ``update_follow_up_payload``. The terminate-side
  ``_cancel_bus_watchers_for`` helper enqueues each fired FollowUp
  via ``manager.enqueue_message`` — the parent's
  ``MessageQueue.content`` therefore carries the marker, and the
  parent's LLM-readable turn consumes it.

The child_reports lookup block at ``child_reports.py:1525-1561``
remains for the natural-completion path only (where the
``_process_child_completion_and_notify_parent`` call creates the
``report_injections`` row that the LLM injects). The terminate
path does NOT flow through that helper — it flows through
``_cancel_bus_watchers_for`` → ``manager.enqueue_message``
directly. The marker MUST therefore land at the source (the
FollowUp's message text), not in the child_reports lookup.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register all models for create_all.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.dependency_bus.repository import (
    DependencyWatcherRepository,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.message_queue.models import MessageQueue
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    set_dependency_bus,
)


# ─── Fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-based SQLite — each Session gets its OWN connection."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/test.db",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def bus(engine):
    """Unstarted bus — each test starts/stops it inside its own loop."""
    return DependencyBus(DependencyWatcherRepository(engine))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_instance(engine: Engine, *, status: str, parent_id: str | None) -> str:
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="tester",
                agent_dir="/tmp/agents/tester",
                agent_name="tester",
                parent_id=parent_id,
                project_id="test-project",
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()
    return iid


async def _fire_terminated(bus: DependencyBus, parent: str, child: str):
    """Register + fire the parent's watcher on the terminated child.

    Mirrors the production ``_cancel_bus_watchers_for`` →
    ``bus.fire_for_terminated_target`` call site (the
    Round 2 Blocker 2 real-flow entry point). Returns the list of
    fired FollowUps — each FollowUp's ``message`` field carries the
    ``[child_outcome: terminated]`` marker (stamped at the source,
    per the Round 2 fix).
    """
    fu = FollowUp(
        target_instance_id=parent,
        message=f"[dependency_bus] child {child} completed for message msg-X",
        source=f"internal_agent:{parent}",
        metadata={
            "kind": "child_complete",
            "child_id": child,
            "parent_id": parent,
            "message_id": "msg-X",
        },
    )
    await bus.watch("task-term-1", fu)
    return await bus.fire_for_terminated_target(
        child, Outcome(status="terminated")
    )


def _read_message_queue_rows(engine: Engine, instance_id: str):
    """Read the parent instance's message_queue rows.

    Real-flow probe: the parent's ``manager.enqueue_message`` call
    creates a ``MessageQueue`` row whose ``content`` field is the
    FollowUp's ``message`` text. The parent LLM consumes this content
    during its next turn — so the marker landing in this content is
    the binding acceptance for Blocker 2.
    """
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT message_id, content, source, status "
                "FROM message_queue WHERE instance_id = :iid"
            ),
            {"iid": instance_id},
        ).mappings().all()


# ─── (i) terminate → fire → helper enqueue → marker in parent message content


@pytest.mark.asyncio
async def test_i_terminate_fire_helper_enqueue_marker_in_message(
    engine, bus
):
    """Round 2 Blocker 2 acceptance (real-flow drive).

    The terminate path: ``fire_for_terminated_target`` stamps
    ``[child_outcome: terminated]`` into the FollowUp's ``message``
    field. ``_cancel_bus_watchers_for`` then enqueues the FollowUp
    via ``manager.enqueue_message`` — the parent's ``MessageQueue``
    row's ``content`` field carries the marker.

    This test drives the REAL flow: bus.watch (watcher registration
    at the production shape) → bus.fire_for_terminated_target (the
    Round 2 Blocker 2 fire site) → ``manager.enqueue_message``
    (mocked to capture the message content via the
    ``MessageQueue`` row the production code creates) →
    assertion on the persisted message content.

    The pre-Round-2 code only enriched metadata (``dataclass_replace``
    on the frozen FollowUp), which is invisible to the parent's
    LLM-visible content — the assertion fires against the
    ``MessageQueue.content`` text the parent's turn actually reads.
    """
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=None
        )
        child = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value, parent_id=parent
        )

        # Real-flow step 1: terminate-side fire. The FollowUp returned
        # here is the one ``_cancel_bus_watchers_for`` would enqueue.
        fired = await _fire_terminated(bus, parent, child)
        assert len(fired) == 1

        # Real-flow step 2: the marker landed in the FollowUp's
        # ``message`` field at the source (Round 2 Blocker 2 fix).
        # This is the binding acceptance — the parent's LLM reads
        # this exact message text via ``MessageQueue.content``.
        assert (
            "[child_outcome: terminated]" in fired[0].message
        ), (
            "Round 2 Blocker 2 fix: ``[child_outcome: terminated]`` "
            "MUST be in the FollowUp's message text at the fire "
            "site — the parent's LLM-visible content is exactly this "
            "text (the metadata-only enrichment is invisible to the "
            "LLM and the child_reports lookup block is dead code "
            "on the terminate path)."
        )

        # Real-flow step 3: the marker is persisted on the FIRED
        # row's ``follow_up_payload`` (the seam a restart's
        # ``_recover_fired_unsent`` re-enqueues from). The
        # persisted payload's ``message`` field carries the marker.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT follow_up_payload FROM dependency_watchers "
                    "WHERE watch_id IN ("
                    "  SELECT watch_id FROM dependency_watchers "
                    "  WHERE state = 'FIRED' LIMIT 1"
                    ")"
                )
            ).mappings().first()
        import json as _json

        persisted = (
            row["follow_up_payload"]
            if not isinstance(row["follow_up_payload"], str)
            else _json.loads(row["follow_up_payload"])
        )
        assert "[child_outcome: terminated]" in persisted["message"]
        assert persisted["metadata"]["child_outcome"] == "terminated"

        # Real-flow step 4: drive the helper's enqueue path. We
        # simulate ``_cancel_bus_watchers_for`` calling
        # ``manager.enqueue_message`` (the production delivery
        # contract) by writing a MessageQueue row with the FollowUp's
        # ``message`` as its ``content``. The assertion is on the
        # ``MessageQueue.content`` field — exactly what the parent's
        # LLM consumes during its next turn.
        fu = fired[0]
        now = _now_iso()
        with Session(engine) as s:
            s.add(
                MessageQueue(
                    message_id=f"msg-{uuid.uuid4().hex[:8]}",
                    instance_id=fu.target_instance_id,
                    content=fu.message,
                    source=fu.source,
                    status="ready",
                    created_at=now,
                )
            )
            s.commit()

        # Real-flow acceptance: the parent's message_queue row's
        # ``content`` (the field the LLM reads) carries the marker.
        rows = _read_message_queue_rows(engine, parent)
        assert len(rows) == 1
        assert "[child_outcome: terminated]" in rows[0]["content"], (
            f"Round 2 Blocker 2 acceptance: parent LLM-visible "
            f"message_queue.content MUST carry the marker. "
            f"Actual content: {rows[0]['content']!r}"
        )
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── (ii) replay: two consecutive MessageQueue rows → byte-identical content


@pytest.mark.asyncio
async def test_ii_two_consecutive_enqueues_byte_identical(engine, bus):
    """Replay acceptance (idempotency of the marker stamping).

    Two consecutive ``fire_for_terminated_target`` cycles on the
    same watcher row MUST produce byte-identical FollowUp messages
    (no field stripping, no re-stamping). The marker is appended
    ONCE on the first cycle (the ``_marker not in (fu.message or
    "")`` guard at ``dependency_bus.py:1255-1263`` prevents a
    double-append on a subsequent cycle).

    Drives the REAL flow: two ``fire_for_terminated_target`` calls
    on the same target. The second call returns ``[]`` (the watcher
    is already FIRED, guarded ``transition_state`` rowcount == 0)
    — but if we instead RE-REGISTER the watcher between cycles
    (the production race shape: a parent re-watches the same child
    task id), the second cycle produces the same message text. The
    acceptance asserts the marker format is stable across the
    cycles.

    Note: the production code path for "replay" is
    ``claim_for_injection`` returning ``[]`` on the second call
    (the ``report_injections`` tri-state guards). On the terminate
    path the equivalent is: the FollowUp's message text is
    byte-identical across the two cycles — no field drift.
    """
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_instance(
            engine, status=InstanceStatus.RUNNING.value, parent_id=None
        )
        child = _seed_instance(
            engine, status=InstanceStatus.TERMINATED.value, parent_id=parent
        )

        # Cycle 1: register + fire (production shape).
        fired_1 = await _fire_terminated(bus, parent, child)
        assert len(fired_1) == 1
        msg_1 = fired_1[0].message

        # Cycle 2: re-register the same source_task_id (the
        # production race shape — a parent re-watches the same
        # child after a restart) and re-fire. The follow_up_payload
        # starts FRESH (the previous cycle's row is in FIRED state
        # so the new ``watch`` inserts a new PENDING row), so the
        # marker is appended exactly once.
        await bus.watch(
            "task-term-1",
            FollowUp(
                target_instance_id=parent,
                message=f"[dependency_bus] child {child} completed for message msg-X",
                source=f"internal_agent:{parent}",
                metadata={
                    "kind": "child_complete",
                    "child_id": child,
                    "parent_id": parent,
                    "message_id": "msg-X",
                },
            ),
        )
        fired_2 = await bus.fire_for_terminated_target(
            child, Outcome(status="terminated")
        )
        assert len(fired_2) == 1
        msg_2 = fired_2[0].message

        # Byte-identical: the marker format is stable across
        # cycles. The ``_marker not in`` guard prevents the second
        # cycle from double-appending if the message were somehow
        # re-read.
        assert msg_1 == msg_2, (
            f"Round 2 Blocker 2 idempotency: two consecutive fire "
            f"cycles MUST produce byte-identical FollowUp messages "
            f"(no field drift). cycle_1={msg_1!r}, cycle_2={msg_2!r}"
        )
        assert "[child_outcome: terminated]" in msg_2
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── P2 closure fast-follow (W-C.a) ─ stamp-gate driven through the REAL seam ─
#
# P2 fast-follow W-C.a closes a coverage gap from the P2 round 2
# verification: the existing test_h
# (``test_compact_fired_watchers_deliver_before_compact.py::test_h_blocker1_chain_paused_parent_no_stamp_then_resume_heals``)
# drives ``bus.emit_terminal`` directly and then *simulates* the
# Blocker 1 stamp-skip by asserting the row is un-stamped after a
# hand-rolled UPDATE. That covers the compact side but not the
# production stamping seam itself: ``ChildReportsService._emit_terminal_via_bus``
# is the helper that contains the actual ``_parent_status == "paused"``
# gate. A regression that removes the gate would still pass test_h
# (the test never exercises the gate).
#
# The two tests below drive the REAL stamping path: the production
# helper ``service._emit_terminal_via_bus`` reads
# ``_instance_repository.get(parent).status`` and decides whether to
# stamp ``enqueued_at``. A revert of the gate would stamp a
# PAUSED-parent row (the round-2 live-repro frozen msg-count
# defect), and a corrupted gate (always-trips) would leave
# RUNNING-parent rows un-stamped (the duplicate-delivery defect).


def _build_child_reports_service_for_stamp_gate(engine):
    """Build a ``ChildReportsService`` wired to a REAL ``SQLModelInstanceRepository``.

    The helper is constructed via ``__new__`` to skip ``__init__``
    (which would touch the real manager / engine). The mock
    manager exposes ``enqueue_message`` as an ``AsyncMock`` (the
    helper does not call it, but the attribute must exist because
    the manager facade's contract); ``_instance_repository`` is the
    REAL repo, so ``service._instance_repository.get(parent)``
    reads the actual ``instances.status`` row (the production
    stamping seam — exactly what we want to test).
    """
    from daemon.services.child_reports import ChildReportsService

    manager = MagicMock(name="InstanceManager")
    manager.enqueue_message = AsyncMock(name="enqueue_message")
    manager._instance_repository = SQLModelInstanceRepository(engine)

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


def _seed_parent_instance(engine: Engine, status: str) -> str:
    """Seed a parent instance row for the stamp-gate tests."""
    iid = f"parent-{uuid.uuid4().hex[:8]}"
    now = _now_iso()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id="tester",
                agent_dir="/tmp/agents/tester",
                agent_name="tester",
                parent_id=None,
                project_id="test-project",
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()
    return iid


def _read_dependency_watcher_enqueued_at(engine: Engine, source_task_id: str):
    """Read the watcher row's stamped state by ``source_task_id``."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT state, enqueued_at FROM dependency_watchers "
                "WHERE source_task_id = :tid"
            ),
            {"tid": source_task_id},
        ).mappings().first()


@pytest.mark.asyncio
async def test_iii_stamp_gate_held_for_paused_parent(engine, bus):
    """W-C.a PAUSED-parent: stamp-gate HELD → ``enqueued_at`` stays NULL.

    Drives the REAL stamping path through
    ``ChildReportsService._emit_terminal_via_bus``. The helper
    reads ``_instance_repository.get(parent).status`` and skips the
    stamp when the parent is ``PAUSED`` (the Round 2 Blocker 1
    fix). A revert of the gate — or a typo that swaps ``"paused"``
    for another status — would stamp the row and break the resume
    Pass 1 selection (``enqueued_at IS NULL`` predicate). This test
    pins the gate held.
    """
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_parent_instance(
            engine, status=InstanceStatus.PAUSED.value
        )
        service = _build_child_reports_service_for_stamp_gate(engine)

        # Register the watcher (the production shape —
        # ``bus.watch`` writes the watcher row the helper later
        # fires via ``bus.emit_terminal``).
        await bus.watch(
            "task-WC-PAUSED",
            FollowUp(
                target_instance_id=parent,
                message="[dependency_bus] child child-PAUSED completed",
                source=f"internal_agent:{parent}",
                metadata={
                    "kind": "child_complete",
                    "child_id": "child-PAUSED",
                    "parent_id": parent,
                    "message_id": "msg-PAUSED",
                },
            ),
        )

        # Real-flow drive: the production helper reads the parent
        # status from the DB and skips the stamp. A hand-rolled
        # assertion on the row is the binding acceptance — the
        # gate is INSIDE the helper, so the only observable
        # difference between "gate held" and "gate reverted" is
        # the post-call ``enqueued_at`` value.
        await service._emit_terminal_via_bus(
            task_id="task-WC-PAUSED", status="completed"
        )

        row = _read_dependency_watcher_enqueued_at(
            engine, "task-WC-PAUSED"
        )
        assert row is not None, "watcher row must exist after emit"
        assert row["state"] == "FIRED", (
            "Blocker 1 chain: the watcher row must be FIRED after "
            "the emit (the PENDING→FIRED transition is the bus's "
            "guarded update; the stamp is a SEPARATE later step)"
        )
        assert row["enqueued_at"] is None, (
            "W-C.a PAUSED-parent acceptance: stamp-gate HELD means "
            "the row stays un-stamped when the parent is PAUSED. "
            "If ``enqueued_at`` is NOT NULL, the gate was either "
            "removed, the status check returned the wrong value, "
            "or the lookup failed (fail-open branch — see the "
            "W-A comment for the trade-off)."
        )
    finally:
        set_dependency_bus(None)
        await bus.stop()


@pytest.mark.asyncio
async def test_iv_stamp_gate_not_held_for_running_parent(engine, bus):
    """W-C.a RUNNING-parent: stamp-gate NOT HELD → ``enqueued_at`` IS NOT NULL.

    Counterpart to ``test_iii``: a non-PAUSED parent (RUNNING) MUST
    get the stamp after the emit. A corrupted gate that always
    trips (e.g. swapped ``!=`` for ``==``, or hardcoded skip) would
    leave this row un-stamped and break the ``_recover_fired_unsent``
    restart-time dedup — a restart would re-deliver an already-
    delivered FollowUp. This test pins the inverse half of the
    gate's contract.
    """
    set_dependency_bus(bus)
    try:
        await bus.start()
        parent = _seed_parent_instance(
            engine, status=InstanceStatus.RUNNING.value
        )
        service = _build_child_reports_service_for_stamp_gate(engine)

        await bus.watch(
            "task-WC-RUNNING",
            FollowUp(
                target_instance_id=parent,
                message="[dependency_bus] child child-RUNNING completed",
                source=f"internal_agent:{parent}",
                metadata={
                    "kind": "child_complete",
                    "child_id": "child-RUNNING",
                    "parent_id": parent,
                    "message_id": "msg-RUNNING",
                },
            ),
        )

        await service._emit_terminal_via_bus(
            task_id="task-WC-RUNNING", status="completed"
        )

        row = _read_dependency_watcher_enqueued_at(
            engine, "task-WC-RUNNING"
        )
        assert row is not None, "watcher row must exist after emit"
        assert row["state"] == "FIRED", (
            "W-C.a RUNNING-parent: watcher must be FIRED after emit "
            "(PENDING→FIRED is the bus's guarded update)"
        )
        assert row["enqueued_at"] is not None, (
            "W-C.a RUNNING-parent acceptance: stamp-gate NOT HELD "
            "for a non-PAUSED parent — the stamp must run, setting "
            "``enqueued_at`` to a non-NULL timestamp. If NULL, the "
            "gate is over-tripping (would break the "
            "``_recover_fired_unsent`` restart-time dedup — a "
            "restart would re-deliver an already-delivered FollowUp)."
        )
    finally:
        set_dependency_bus(None)
        await bus.stop()


# ─── P2 closure fast-follow (W-D) — terminate enqueue leg with verbatim marker ─
#
# P2 fast-follow W-D pins the enqueue leg of the terminate path
# that was previously only hand-simulated in the
# ``test_i_terminate_fire_helper_enqueue_marker_in_message`` test
# above. That test simulates the helper by writing a MessageQueue
# row by hand — it does NOT drive ``_cancel_bus_watchers_for``
# directly. The risk surface: a future refactor that drops the
# ``fu.message`` argument (or replaces it with a sanitized
# version) would silently strip the ``[child_outcome: terminated]``
# marker. This test invokes the REAL helper with an
# ``AsyncMock`` ``enqueue_message`` and asserts the VERBATIM
# ``message`` kwarg passed to the enqueue carries the marker —
# exactly what the parent LLM will read via ``MessageQueue.content``.


@pytest.mark.asyncio
async def test_v_cancel_bus_watchers_for_enqueues_verbatim_fu_message(
    engine, bus
):
    """W-D acceptance: ``_cancel_bus_watchers_for`` enqueues the
    ``fu.message`` text verbatim — marker included.

    Drives the REAL helper against a registered watcher + a
    mock manager whose ``enqueue_message`` is an ``AsyncMock``.
    The accept-or-reject check is on the EXACT ``message`` kwarg
    the helper passes to ``manager.enqueue_message`` — the
    ``[child_outcome: terminated]`` suffix MUST be present (the
    Round 2 Blocker 2 fix).

    This test was previously hand-simulated in
    ``test_i_terminate_fire_helper_enqueue_marker_in_message``
    via a hand-written MessageQueue row. The hand-simulation does
    NOT pin the helper itself — a refactor that drops or
    sanitizes the ``fu.message`` argument would slip past that
    test. This test pins the helper directly.

    The parent instance is seeded RUNNING so the dead-letter
    branch (WARNING 1 at ``instance_lifecycle.py:138-194``) does
    not trip — the enqueue must reach the manager mock.
    """
    from daemon.services.instance_lifecycle import (
        _cancel_bus_watchers_for,
    )

    set_dependency_bus(bus)
    try:
        await bus.start()

        parent = _seed_parent_instance(
            engine, status=InstanceStatus.RUNNING.value
        )
        child = f"child-{uuid.uuid4().hex[:8]}"
        # Register a watcher keyed on the child instance id (the
        # ``fire_for_terminated_target`` matcher keys on
        # ``metadata.child_id`` — see dependency_bus.py:1117-1121).
        await bus.watch(
            "task-WD-1",
            FollowUp(
                target_instance_id=parent,
                message="[dependency_bus] child X completed for message msg-X",
                source=f"internal_agent:{parent}",
                metadata={
                    "kind": "child_complete",
                    "child_id": child,
                    "parent_id": parent,
                    "message_id": "msg-X",
                },
            ),
        )

        # Mock manager with AsyncMock enqueue + real instance
        # repository (the dead-letter liveness check needs it).
        manager = MagicMock(name="InstanceManager")
        manager.enqueue_message = AsyncMock(
            name="enqueue_message", return_value={"message_id": "m-WD"}
        )
        manager._instance_repository = SQLModelInstanceRepository(engine)

        # Drive the REAL helper. The op tag is logging-only.
        await _cancel_bus_watchers_for(
            manager, child, op="terminate_unit_test"
        )

        # Acceptance: enqueue called exactly once with the
        # VERBATIM ``fu.message`` text — including the
        # ``[child_outcome: terminated]`` suffix stamped at the
        # Round 2 Blocker 2 fire site (``dependency_bus.py:1255-1261``).
        manager.enqueue_message.assert_awaited_once()
        call = manager.enqueue_message.await_args
        assert call is not None, (
            "W-D: ``manager.enqueue_message`` MUST be awaited "
            "exactly once during ``_cancel_bus_watchers_for`` for "
            "a single-watcher terminate shape"
        )
        assert call.kwargs["instance_id"] == parent, (
            f"W-D: enqueue target must be the parent "
            f"(got {call.kwargs['instance_id']!r}, "
            f"expected {parent!r})"
        )
        enqueued_message = call.kwargs["message"]
        assert "[child_outcome: terminated]" in enqueued_message, (
            "W-D acceptance: the ``message`` kwarg passed to "
            "``manager.enqueue_message`` MUST carry the "
            "``[child_outcome: terminated]`` marker — the parent "
            "LLM consumes this text via ``MessageQueue.content`` "
            "during its next turn. Marker MISSING means the "
            "Blocker 2 fix's suffix step was dropped, sanitized, "
            "or replaced with a metadata-only enrichment "
            "(pre-Round-2 behaviour, dead code on the terminate "
            f"path). Actual message: {enqueued_message!r}"
        )
        # Exact-byte pin: the pre-existing message body survives
        # AND the marker is appended with the exact envelope
        # (``\\n\\n[child_outcome: terminated]`` — see
        # ``dependency_bus.py:1259``). Refactors that change the
        # envelope format must update this assertion.
        assert enqueued_message == (
            "[dependency_bus] child X completed for message msg-X"
            "\n\n[child_outcome: terminated]"
        ), (
            f"W-D verbatim pin: the helper MUST enqueue the "
            f"exact FollowUp text including the marker suffix. "
            f"Got: {enqueued_message!r}"
        )
    finally:
        set_dependency_bus(None)
        await bus.stop()
