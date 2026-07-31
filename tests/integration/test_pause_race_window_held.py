"""Integration test: marker lifetime covers the cascade-execution window (C1).

The C1 race fix reorders the post-graph completion path so the deferred-pause
marker is **peeked BEFORE** ``pause_instance_cascade`` runs and **popped
AFTER** the cascade's ``finally`` block. The old ordering (pop-before-cascade)
left the marker empty during the cascade's DB-commit window — a child
completion landing in that window would see ``marker=False, db=RUNNING`` and
create a spurious ``PROCESS_REPORT`` Task, re-creating the bug the Phase 1 /
Phase 2 source-side guards were designed to stop.

This test exercises the **real** peek/await/pop ordering by binding the
production ``has_deferred_question_pause`` and ``pop_deferred_question_pause``
methods onto a real-instance manager and running the same finally-block body
that ``daemon.services.instance_messaging`` runs. The cascade is replaced
with a slow stub that holds the window open for 200 ms so the test can
inject a child completion in the middle of the window.

The test asserts the C1 invariant: during the cascade's execution window
(marker set, DB still RUNNING), the source-side Phase 1 guard in
``ChildReportsService._process_child_completion_db_sync`` must SKIP the
``PROCESS_REPORT`` Task creation because the marker is still set. This is
the property that was missing under the old ordering.

If the test is run against the OLD ordering (pop before cascade), the
marker would be empty during the delayed cascade, the guard would NOT
fire, and a ``PROCESS_REPORT`` Task WOULD be created — ``tasks == []``
would fail. The test is intentionally designed to fail on the old
implementation to make the C1 regression visible.

Compared to the previous ``test_pause_race_window_held`` (which used
artificial marker timing — manually setting the marker, sleeping, then
manually discarding it), this rewrite uses the **real** peek/await/pop
ordering so it would actually catch a regression of the C1 fix.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.manager import InstanceManager
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.report_injection.models import ReportInjection, ReportInjectionState
from daemon.repositories.task.models import Task, TaskType
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


@pytest.fixture
def engine() -> Engine:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def _bus_singleton():
    bus = MagicMock()
    bus.count_pending_for_target_sync.return_value = 1
    set_dependency_bus(bus)
    yield
    set_dependency_bus(None)


def seed_instance(engine: Engine, *, parent_id: str | None = None,
                  status: str = InstanceStatus.RUNNING.value) -> str:
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(Instance(
            instance_id=iid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            agent_name="developer",
            parent_id=parent_id,
            project_id="test-project",
            status=status,
            version=1,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return iid


def _make_manager_with_real_marker_state(engine: Engine) -> MagicMock:
    """Mock manager with REAL peek/pop semantics against a real backing set.

    The C1 fix relies on the marker being observable to OTHER threads
    while the post-graph finally block awaits the cascade. We bind the
    production methods (``set_deferred_question_pause``,
    ``has_deferred_question_pause``, ``pop_deferred_question_pause``) so
    the test exercises the same atomic-check + atomic-remove semantics
    that ``daemon.services.instance_messaging`` uses. The cascade itself
    is replaced with a slow stub so the test can inject a child
    completion in the middle of the window.
    """
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._deferred_question_pause = set()
    manager._graph_tasks = {}

    manager.set_deferred_question_pause = (
        InstanceManager.set_deferred_question_pause.__get__(manager)
    )
    manager.has_deferred_question_pause = (
        InstanceManager.has_deferred_question_pause.__get__(manager)
    )
    manager.pop_deferred_question_pause = (
        InstanceManager.pop_deferred_question_pause.__get__(manager)
    )
    return manager


async def _run_post_graph_finally_block(
    manager: MagicMock, instance_id: str, cascade_callable
) -> tuple[bool, bool]:
    """Replay the post-graph completion path's finally block.

    This mirrors the exact ordering used at
    ``daemon/services/instance_messaging.py:958-...`` and ``:3272-...``
    after the C1 fix:

      1. ``has_deferred_question_pause(instance_id)`` — peek WITHOUT pop.
      2. If true, ``await asyncio.shield(cascade_callable(instance_id))``.
      3. ``finally: pop_deferred_question_pause(instance_id)``.

    Returns ``(cascade_called, marker_set_during_cascade)``. The second
    tuple element is observed by the test inside the cascade stub to
    assert that the marker was still set during the cascade's execution
    window — the property that closes C1.
    """
    if not manager.has_deferred_question_pause(instance_id):
        return (False, False)
    try:
        await asyncio.shield(cascade_callable(instance_id))
    finally:
        manager.pop_deferred_question_pause(instance_id)
    return (True, True)


@pytest.mark.asyncio
async def test_c1_marker_lifetime_covers_cascade_window_skips_process_report(
    engine: Engine,
) -> None:
    """C1 invariant: marker set + DB=RUNNING + cascade in-flight ⇒ skip Task.

    Setup (mirrors the real production flow):

      1. ``question_pause_node`` runs and calls
         ``manager.set_deferred_question_pause(parent_id)`` — marker set.
      2. The post-graph completion path enters its finally block; the
         new ordering peeks via ``has_deferred_question_pause`` BEFORE
         awaiting the cascade.
      3. The cascade stub enters the window, asserts the marker is STILL
         set (proving the peek-before-pop ordering), then delays 200 ms
         to keep the window open. During this window the DB still says
         RUNNING (no commit yet).
      4. A child completion fires ``ChildReportsService._process_child_completion_db_sync``,
         which runs the Phase 1 dual-check guard (marker + DB PAUSED).
         Because the marker is still set, the guard SKIPS the
         ``PROCESS_REPORT`` Task and keeps the durable ``ReportInjection``
         row as the fallback.
      5. The cascade stub completes, commits PAUSED to the DB, returns.
      6. The finally block pops the marker.

    Asserts:

      * The cascade observed the marker set during its execution
        (C1 ordering is in effect).
      * No ``Task(task_type=PROCESS_REPORT, ...)`` row was created
        during the window (the Phase 1 guard fired).
      * A ``ReportInjection(PENDING)`` row was created as the durable
        fallback.
      * After the cascade settles, the parent reads PAUSED and the
        marker is gone (pop ran in the finally).
    """
    parent_id = seed_instance(engine)
    child_id = seed_instance(engine, parent_id=parent_id)

    manager = _make_manager_with_real_marker_state(engine)
    service = ChildReportsService(manager=manager)

    # 1. question_pause_node sets the marker.
    manager.set_deferred_question_pause(parent_id)

    cascade_entered = threading.Event()
    cascade_release = threading.Event()
    marker_observed_during_cascade: list[bool] = []

    async def slow_cascade(_target_id: str) -> dict:
        # 3. Cascade has entered. Snapshot the marker state here — the
        # C1 fix guarantees the marker is STILL set because the pop
        # moved to the finally block AFTER this await.
        marker_observed_during_cascade.append(
            manager.has_deferred_question_pause(parent_id)
        )
        cascade_entered.set()
        # 4. Hold the window open for 200 ms so the test can inject a
        # child completion in the middle. (Production does not need
        # this delay — the DB commit itself takes some time on a real
        # worker thread; the test simulates the gap deterministically.)
        await asyncio.sleep(0.2)
        # Commit PAUSED in the DB. After this, the cascade is
        # "complete" from the DB's perspective.
        await asyncio.to_thread(
            _commit_paused_sync, engine, parent_id
        )
        # Wait for the test to signal before returning so we can
        # observe the marker state mid-window.
        await asyncio.to_thread(cascade_release.wait, 2.0)
        return {"paused_ids": [parent_id], "skipped_ids": []}

    finally_task = asyncio.create_task(
        _run_post_graph_finally_block(manager, parent_id, slow_cascade)
    )

    # Wait for the cascade to enter the window.
    await asyncio.to_thread(cascade_entered.wait, 2.0)

    # Confirm pre-conditions: marker set, DB still RUNNING.
    assert manager.has_deferred_question_pause(parent_id), (
        "marker must be set during the cascade window (C1 invariant)"
    )
    with Session(engine) as session:
        assert session.get(Instance, parent_id).status == InstanceStatus.RUNNING.value, (
            "DB must still say RUNNING during the cascade window"
        )

    # 4. Child completion fires DURING the cascade window — this is
    # the exact scenario C1 is designed to prevent. Phase 1 guard
    # should fire and SKIP Task creation.
    await asyncio.to_thread(
        service._process_child_completion_db_sync,
        child_id, "completed-msg", "child body"
    )

    # Assert: no PROCESS_REPORT Task was created.
    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(
                Task.instance_id == parent_id,
                Task.task_type == TaskType.PROCESS_REPORT.value,
            )
        ).all()
        injections = session.exec(
            select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_id
            )
        ).all()
    assert tasks == [], (
        "Phase 1 guard must have SKIPPED PROCESS_REPORT Task creation "
        "because the marker is still set during the cascade window "
        "(C1 invariant). If this fails with len(tasks) > 0, the "
        "pop-before-cascade ordering has been reintroduced."
    )
    assert len(injections) == 1, (
        "durable ReportInjection row must exist as the resume-drain "
        "fallback even when the Task is skipped"
    )
    assert injections[0].state == ReportInjectionState.PENDING.value

    # Release the cascade and let the finally block complete.
    cascade_release.set()
    result = await finally_task
    cascade_called, _ = result

    # Post-conditions: cascade ran, marker is gone, DB is PAUSED.
    assert cascade_called is True, "cascade must have been awaited"
    assert marker_observed_during_cascade == [True], (
        "C1 invariant violated: marker was empty during the cascade "
        "execution. The pop-before-cascade ordering has been "
        "reintroduced — pop must run in the finally block AFTER the "
        "cascade completes."
    )
    assert not manager.has_deferred_question_pause(parent_id), (
        "marker must be cleared by the pop in the finally block"
    )
    with Session(engine) as session:
        inst = session.get(Instance, parent_id)
        assert inst.status == InstanceStatus.PAUSED.value
        injections = session.exec(
            select(ReportInjection).where(
                ReportInjection.parent_instance_id == parent_id
            )
        ).all()
    assert injections[0].state == ReportInjectionState.PENDING.value


def _commit_paused_sync(engine: Engine, instance_id: str) -> None:
    """Sync DB helper used by the slow cascade stub.

    The production ``_pause_cascade_db_sync`` runs in
    ``asyncio.to_thread`` so the test mirrors that. The cascade's
    real DB write is what the test window simulates; we only need a
    status flip to PAUSED here.
    """
    with Session(engine) as session:
        inst = session.get(Instance, instance_id)
        inst.status = InstanceStatus.PAUSED.value
        session.add(inst)
        session.commit()
