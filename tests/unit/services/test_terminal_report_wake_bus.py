"""Regression pin: bus/watcher wake FIRES on terminal emission (the 30588
scenario) — Debug Phase 4 fix #1, terminal-report wake.

Incident 7807e521 (diagnosed 2026-09-07): a parent registered its
DependencyBus watchers keyed on the child's ``process_message`` task ids
(30529 / 30585 / 30638 — one per parent→child send, resolved via
``_task_repo.get_by_message`` at parent-send time by
``_register_child_completion_watcher``). The child reached its terminal
graph turn on a DIFFERENT task id (30588 — a ``process_report`` task),
and the task-keyed ``bus.emit_terminal(task_id="30588")`` matched ZERO
PENDING watchers — the bus-side wake was a no-op for that key.

On the completion path the corrective (parent, child)-pair-keyed emit
(``ChildReportsService._emit_terminal_for_child_instance_via_bus`` →
``DependencyBus.emit_terminal_for_child_instance``) is what actually
fires those watchers: it matches on
``(target_instance_id, follow_up_payload.metadata.child_id)``
regardless of which task id was the terminal one. This file pins that
behavior through the REAL service + bus + repository (no mocks on the
delivery path):

1. The task-keyed emit with the terminal ``process_report`` task id
   fires NOTHING (the mismatch is real — documented, not regressed).
2. The corrective pair-keyed emit fires ALL the parent's PENDING
   watchers keyed on the (never-emitted) ``process_message`` task ids.
3. Exactly-once at the bus layer: a duplicate emit pair is a no-op
   (guarded ``WHERE state = 'PENDING'`` transitions).
4. The parent's pending count drops to 0 — the precondition
   ``JobFeedbackObserver._process_event`` checks when it finalizes the
   parent's job (finalization rides the PROCESS_REPORT task delivery,
   it is NOT driven by the bus — see the corrected log text in
   ``child_reports._update_parent_on_child_complete``).
5. FIRED rows carry ``fired_at`` and the C1 ``enqueued_at`` dedup stamp
   so a restart's ``_recover_fired_unsent`` will not re-deliver them.

Fixture: file-backed SQLite at ``tmp_path`` with NullPool,
``PRAGMA journal_mode=WAL``, ``PRAGMA busy_timeout=10000`` (project
Testing & QC conventions — StaticPool + WriteGuardSession is
forbidden). The bus singleton is swapped in for the test and restored
in a finally block.
"""

from __future__ import annotations

import asyncio
import types

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, select

import daemon.repositories.dependency_bus.models  # noqa: F401 — table registration

from daemon.repositories.dependency_bus import (
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import (
    DependencyBus,
    FollowUp,
    Outcome,
    get_dependency_bus,
    set_dependency_bus,
)

# Task ids mirroring the incident shape: watchers keyed on the two
# process_message task ids known at parent-send time; the child's
# terminal turn ran on a process_report task id that NO watcher keyed.
PARENT_ID = "parent-aaaaaaaa"
CHILD_ID = "child-bbbbbbbb"
WATCHER_TASK_IDS = ["30529", "30638"]
TERMINAL_REPORT_TASK_ID = "30588"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite: NullPool + WAL + busy_timeout=10000."""
    db_path = tmp_path / "terminal_wake_bus.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def watcher_repo(engine: Engine) -> DependencyWatcherRepository:
    return DependencyWatcherRepository(engine)


@pytest.fixture
async def bus(watcher_repo: DependencyWatcherRepository):
    b = DependencyBus(watcher_repo)
    await b.start()
    set_dependency_bus(b)
    try:
        yield b
    finally:
        set_dependency_bus(None)
        await b.stop()


class _InstanceRepoStub:
    """Stub for ``manager._instance_repository`` — returns a live parent.

    ``_emit_terminal_via_bus``'s C1 stamp loop reads the parent's status
    to gate the ``enqueued_at`` stamp (paused parents must stay
    un-stamped for resume Pass 1). This stub returns a RUNNING parent so
    the normal-path stamp executes.
    """

    def get(self, instance_id: str):
        return types.SimpleNamespace(instance_id=instance_id, status="running")


@pytest.fixture
def manager_stub():
    return types.SimpleNamespace(
        _instance_repository=_InstanceRepoStub(),
    )


@pytest.fixture
def service(manager_stub) -> ChildReportsService:
    return ChildReportsService(manager_stub)


async def _register_watchers(bus: DependencyBus) -> None:
    """Register watchers exactly as ``_register_child_completion_watcher``
    does: one per parent→child send, keyed on the child's
    ``process_message`` task id, with ``metadata.child_id`` stamped for
    the corrective pair-keyed matcher."""
    for task_id in WATCHER_TASK_IDS:
        await bus.watch(
            source_task_id=task_id,
            follow_up=FollowUp(
                target_instance_id=PARENT_ID,
                message=(
                    f"[dependency_bus] child {CHILD_ID} "
                    f"completed for message msg-{task_id}"
                ),
                source=f"internal_agent:{PARENT_ID}",
                metadata={
                    "kind": "child_complete",
                    "child_id": CHILD_ID,
                    "parent_id": PARENT_ID,
                    "message_id": f"msg-{task_id}",
                },
            ),
        )


def _parent_watcher_rows(engine: Engine) -> list[DependencyWatcher]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(DependencyWatcher).where(
                    DependencyWatcher.target_instance_id == PARENT_ID
                )
            )
        )


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminal_report_task_id_fires_parent_watchers_via_pair_emit(
    bus: DependencyBus, service: ChildReportsService, engine: Engine
):
    """The 30588 regression: the terminal emission (whose task id matches
    NO watcher key) must still fire the parent's watchers.

    Drives the production ``regular_child_completed`` post-commit emit
    sequence — the task-keyed emit followed by the corrective
    (parent, child)-pair-keyed emit — against the real bus + repository.
    """
    await _register_watchers(bus)

    # Sanity: both watchers are PENDING and keyed on the process_message
    # task ids — none on the terminal process_report task id.
    rows = _parent_watcher_rows(engine)
    assert len(rows) == 2
    assert all(r.state == DependencyWatcherState.PENDING.value for r in rows)
    assert {r.source_task_id for r in rows} == set(WATCHER_TASK_IDS)
    assert TERMINAL_REPORT_TASK_ID not in {r.source_task_id for r in rows}

    # 1) Task-keyed emit with the terminal task id — matches NOTHING.
    #    This is the documented 30588 no-op: the watcher keys were
    #    resolved at parent-send time, the terminal turn ran on a
    #    process_report task.
    fired_task_keyed = await service._emit_terminal_via_bus(
        task_id=TERMINAL_REPORT_TASK_ID,
        status="completed",
        summary="regular child completed",
    )
    assert fired_task_keyed == [], (
        "task-keyed emit with the terminal process_report task id must "
        "match no watcher (documents the mismatch — the corrective "
        "pair-keyed emit below is what fires them)"
    )

    # 2) Corrective (parent, child)-pair-keyed emit — fires BOTH
    #    watchers regardless of which task id was the terminal one.
    fired_pair = await service._emit_terminal_for_child_instance_via_bus(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_ID,
        status="completed",
        summary="regular child completed (corrective multi-turn emit)",
    )
    assert len(fired_pair) == 2, (
        "the parent's watchers must FIRE on the terminal emission even "
        "when keyed on task ids != the terminal process_report task id "
        "(the 7807e521 / 30588 wake)"
    )
    assert all(fu.target_instance_id == PARENT_ID for fu in fired_pair)
    assert all(fu.metadata.get("child_id") == CHILD_ID for fu in fired_pair)

    # 3) Durable state: all watcher rows FIRED, stamped for delivery.
    rows = _parent_watcher_rows(engine)
    assert {r.state for r in rows} == {DependencyWatcherState.FIRED.value}
    assert all(r.fired_at is not None for r in rows)
    assert all(r.enqueued_at is not None for r in rows), (
        "FIRED rows must carry the C1 enqueued_at dedup stamp so a "
        "restart's _recover_fired_unsent does not re-deliver them"
    )

    # 4) The finalize precondition: the parent's bus pending count is 0.
    assert await bus.count_pending_for_target(PARENT_ID) == 0


@pytest.mark.asyncio
async def test_duplicate_terminal_emission_is_exactly_once(
    bus: DependencyBus, service: ChildReportsService, engine: Engine
):
    """Re-firing the same terminal emission must not double-fire.

    The guarded ``WHERE state = 'PENDING'`` transition is the
    exactly-once primitive: a second emit pair (e.g. a later
    ``regular_child_completed`` turn, or a race with the task-keyed
    emit) sees rowcount 0 and returns no FollowUps.
    """
    await _register_watchers(bus)

    first_pair = await service._emit_terminal_for_child_instance_via_bus(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_ID,
        status="completed",
        summary="first emit",
    )
    assert len(first_pair) == 2

    # Duplicate emission — both shapes.
    dup_task_keyed = await service._emit_terminal_via_bus(
        task_id=WATCHER_TASK_IDS[0],
        status="completed",
        summary="duplicate task-keyed emit",
    )
    dup_pair = await service._emit_terminal_for_child_instance_via_bus(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_ID,
        status="completed",
        summary="duplicate pair emit",
    )
    assert dup_task_keyed == []
    assert dup_pair == []

    rows = _parent_watcher_rows(engine)
    assert len(rows) == 2
    assert {r.state for r in rows} == {DependencyWatcherState.FIRED.value}
    # Still stamped exactly once each (no exception, no dup rows).
    assert all(r.enqueued_at is not None for r in rows)


@pytest.mark.asyncio
async def test_emitted_outcome_is_status_agnostic_and_sibling_children_unaffected(
    bus: DependencyBus, service: ChildReportsService, engine: Engine
):
    """Terminal emission fires regardless of outcome, and only fires
    watchers whose metadata.child_id matches the terminating child — a
    sibling child's watchers stay PENDING (their wake must not be
    stolen)."""
    sibling_child_id = "child-cccccccc"
    sibling_task_id = "30700"
    await _register_watchers(bus)
    await bus.watch(
        source_task_id=sibling_task_id,
        follow_up=FollowUp(
            target_instance_id=PARENT_ID,
            message=f"[dependency_bus] child {sibling_child_id} completed",
            source=f"internal_agent:{PARENT_ID}",
            metadata={
                "kind": "child_complete",
                "child_id": sibling_child_id,
                "parent_id": PARENT_ID,
                "message_id": f"msg-{sibling_task_id}",
            },
        ),
    )

    # Child C terminates on the terminal report task id.
    await service._emit_terminal_via_bus(
        task_id=TERMINAL_REPORT_TASK_ID,
        status="error",
        error="child errored after final report",
        summary="child errored",
    )
    await service._emit_terminal_for_child_instance_via_bus(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_ID,
        status="error",
        error="child errored after final report",
        summary="child errored (corrective multi-turn emit)",
    )

    rows = _parent_watcher_rows(engine)
    by_child = {}
    for r in rows:
        payload_child = (r.follow_up_payload or {}).get("metadata", {}).get(
            "child_id"
        )
        by_child.setdefault(payload_child, []).append(r.state)

    assert set(by_child[CHILD_ID]) == {DependencyWatcherState.FIRED.value}, (
        "the terminating child's watchers fire on the error outcome too"
    )
    assert by_child[sibling_child_id] == [
        DependencyWatcherState.PENDING.value
    ], (
        "a sibling child's watcher must stay PENDING — the pair-keyed "
        "matcher is scoped to (parent, child)"
    )
    # The parent still waits on exactly the sibling.
    assert await bus.count_pending_for_target(PARENT_ID) == 1


@pytest.mark.asyncio
async def test_bus_singleton_absent_is_fail_safe(
    manager_stub, monkeypatch: pytest.MonkeyPatch
):
    """With the bus singleton missing, the emit helpers return empty lists
    (fail-safe — wiring failure must not break the child-completion
    path)."""
    assert get_dependency_bus() is None
    service = ChildReportsService(manager_stub)

    fired = await service._emit_terminal_via_bus(
        task_id=TERMINAL_REPORT_TASK_ID,
        status="completed",
        summary="no bus",
    )
    assert fired == []

    fired_pair = await service._emit_terminal_for_child_instance_via_bus(
        parent_instance_id=PARENT_ID,
        child_instance_id=CHILD_ID,
        status="completed",
        summary="no bus",
    )
    assert fired_pair == []
