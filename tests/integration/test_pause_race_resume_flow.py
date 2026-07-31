"""Integration test: legitimate pause/resume flow remains unblocked.

False-positive regression test for the Phase 1 guard in
``ChildReportsService._process_child_completion_db_sync`` (see
``daemon/services/child_reports.py:_create_completion_report``).

The Phase 1 dual-check guard (marker + DB PAUSED) SKIPS the
``PROCESS_REPORT`` Task creation when the parent is mid-deferred-pause.
A *false positive* here would skip the Task when the parent is NOT
paused, losing a legitimate child report.

The test exercises the **full pause→resume cycle** end-to-end:

  1. ``question_pause_node`` sets the marker.
  2. The post-graph finally block runs the cascade (real
     ``_pause_cascade_db_sync`` writes PAUSED to the DB), with the
     **new C1 ordering** (peek → cascade → finally:pop).
  3. The cascade settles, the marker is cleared by the pop in the
     finally block, the parent reads PAUSED.
  4. **Resume** the parent back to RUNNING via the production
     ``_resume_cascade_db_sync``.
  5. A late child completion fires ``_process_child_completion_db_sync``
     — both checks (marker empty + DB running) should be False, so the
     ``PROCESS_REPORT`` Task is created normally (no false-positive
     skip).

Compared to the previous version (which manually set the marker,
manually flipped the DB status, and manually discarded the marker —
bypassing the real cascade and resume paths), this rewrite uses the
production ``_pause_cascade_db_sync`` and ``_resume_cascade_db_sync``
plus the real C1 peek/await/pop ordering so a regression in any of
the three would be caught.
"""

from __future__ import annotations

import asyncio
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
from daemon.repositories.task.models import Task, TaskType
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.instance_lifecycle import InstanceLifecycleService
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

    The C1 fix relies on the marker being observable to other code
    paths while the post-graph finally block awaits the cascade. We
    bind the production methods
    (``set_deferred_question_pause`` /
    ``has_deferred_question_pause`` /
    ``pop_deferred_question_pause``) so the test exercises the same
    atomic-check + atomic-remove semantics that
    ``daemon.services.instance_messaging`` uses.
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
) -> bool:
    """Replay the post-graph completion path's finally block.

    Mirrors the C1 ordering at
    ``daemon/services/instance_messaging.py:3272-...``:
    ``has`` (peek) → ``await shielded(cascade)`` → ``finally: pop``.
    """
    if not manager.has_deferred_question_pause(instance_id):
        return False
    try:
        await asyncio.shield(cascade_callable(instance_id))
    finally:
        manager.pop_deferred_question_pause(instance_id)
    return True


@pytest.mark.asyncio
async def test_resume_after_pause_admits_child_completion(engine):
    """Full pause→resume cycle + late child completion = no false positive.

    Sequence:

      1. ``question_pause_node`` sets the marker.
      2. The post-graph finally block peeks the marker, awaits the
         cascade, pops the marker in the finally block. The cascade
         uses the real ``_pause_cascade_db_sync`` to commit PAUSED.
      3. The cascade settles, the parent reads PAUSED, the marker is
         cleared.
      4. Resume the parent back to RUNNING via the production
         ``_resume_cascade_db_sync``.
      5. A late child completion fires
         ``_process_child_completion_db_sync`` — both checks
         (marker empty + DB running) should be False, so the
         ``PROCESS_REPORT`` Task is created normally (no false-positive
         skip).

    Asserts:

      * The cascade committed PAUSED to the DB.
      * The marker was cleared by the pop in the finally block.
      * The resume cascade flipped the DB back to RUNNING.
      * A late child completion creates a single PROCESS_REPORT
        Task (no false-positive skip).
    """
    parent_id = seed_instance(engine)
    child_id = seed_instance(engine, parent_id=parent_id)

    manager = _make_manager_with_real_marker_state(engine)
    service = ChildReportsService(manager=manager)
    lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
    lifecycle._manager = manager
    write_guard = WritePauseGuard()

    # 1. ``question_pause_node`` sets the marker.
    manager.set_deferred_question_pause(parent_id)

    # 2. The post-graph finally block runs the cascade using the
    #    real production ``_pause_cascade_db_sync``.
    async def run_cascade(_target_id: str) -> dict:
        return await asyncio.to_thread(
            lifecycle._pause_cascade_db_sync,
            engine,
            write_guard,
            tree_ids=[parent_id],
            paused_at_iso=datetime.now(timezone.utc).isoformat(),
            paused_instances_data=[(parent_id, "developer")],
        )

    finally_task = asyncio.create_task(
        _run_post_graph_finally_block(manager, parent_id, run_cascade)
    )
    await finally_task

    # 3. Cascade settled — parent is PAUSED, marker is cleared.
    with Session(engine) as session:
        inst = session.get(Instance, parent_id)
        assert inst.status == InstanceStatus.PAUSED.value
    assert not manager.has_deferred_question_pause(parent_id), (
        "marker must be cleared by the pop in the finally block"
    )

    # 4. Resume the parent back to RUNNING via the production
    #    ``_resume_cascade_db_sync``.
    await asyncio.to_thread(
        lifecycle._resume_cascade_db_sync,
        engine,
        write_guard,
        tree_ids=[parent_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )
    with Session(engine) as session:
        inst = session.get(Instance, parent_id)
        assert inst.status == InstanceStatus.RUNNING.value

    # 5. A late child completion now fires both checks (marker empty,
    #    DB running) and creates the PROCESS_REPORT task without
    #    false-positive skip.
    result = service._process_child_completion_db_sync(
        child_id, "completed-msg", "child body"
    )
    assert result.outcome == "regular_child_completed"

    with Session(engine) as session:
        tasks = session.exec(
            select(Task).where(
                Task.instance_id == parent_id,
                Task.task_type == TaskType.PROCESS_REPORT.value,
            )
        ).all()
    assert len(tasks) == 1, (
        "after the full pause→resume cycle, a late child completion "
        "must create exactly one PROCESS_REPORT Task (no false-positive "
        "skip from the Phase 1 guard)"
    )
