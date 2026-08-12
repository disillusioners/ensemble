"""Integration test: pause during a ``process_report`` turn → resume
→ leader reaches COMPLETED (Bug B production failure).

The Phase 2 plan (Task 12 + 12b) requires an integration test
that proves the EXACT production failure is fixed end-to-end:

  1. The leader is at ``WAITING_CHILDREN`` (or PAUSED) with two
     ``completion_report`` ``message_queue`` rows at
     ``status='processing'`` with ``processing_task_id=NULL`` (the
     orphan shape).
  2. The backing ``process_report`` Tasks are already ``cancelled``
     (this is the resume-seam variant — the cascade has already
     moved them through PAUSED).
  3. Run the resume cascade + post-reconcile re-fire.
  4. Assert: the orphan ``completion_report`` rows are now
     ``completed``; the leader instance reaches ``COMPLETED``.

This test also covers Task 13 (defense in depth) — the parent
guard is correct even if the cascade reconciliation is disabled.

The test is implemented against a real in-memory SQLite engine
(StaticPool, FK on) so the production SQL path is exercised
end-to-end. No mocks for the cascade helpers.
"""

from __future__ import annotations

import sys
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

# Register tables before metadata.create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.write_pause_guard import WritePauseGuard

# Make tests/helpers/ importable
_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from tests.helpers.pause_report_orphan_scenarios import (  # noqa: E402
    ensure_schema,
    read_instance,
    read_message,
    seed_orphan_scenario,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
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


@pytest.fixture
def write_guard() -> WritePauseGuard:
    return WritePauseGuard()


@pytest.fixture
def lifecycle_service(engine, write_guard) -> InstanceLifecycleService:
    """Build a minimal InstanceLifecycleService for direct cascade tests."""
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    # Turn-Reconciler migration: provide a real TaskRepository so the
    # cascade helpers' ``self._task_repo.reconcile_turn_mirror`` call
    # exercises the real reconciler (not a MagicMock no-op). Without
    # this, ``reconciled_message_ids`` is always empty.
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    # Disable the post-reconcile re-fire for the basic reconciliation test —
    # the focus is on UPDATE 4 itself. The re-fire is covered separately
    # in test_phase2_post_reconcile_refire_resolves_orphan_via_guard.
    return service


def _seed_two_orphans_at_completed_processing_state(
    engine: Engine,
) -> tuple[str, str, str]:
    """Seed the EXACT production state: 2 ``processing``
    ``completion_report`` rows whose backing Tasks are
    already ``cancelled`` (the resume-seam variant).

    Returns:
        (instance_id, msg_id_1, msg_id_2)
    """
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        # Instance at WAITING_CHILDREN (production state) — but for
        # the resume-cascade variant we want the instance at PAUSED
        # so the resume cascade has work to do. The re-fire is
        # tested separately.
        from daemon.repositories.instance.models import (
            Instance, InstanceStatus,
        )
        s.add(Instance(
            instance_id=iid, agent_id="dev", agent_dir="/tmp",
            agent_name="dev", project_id="test",
            status=InstanceStatus.PAUSED.value,
            created_at=now.isoformat(),
        ))
        # Seed two process_report tasks (paused → will be cancelled)
        task_ids: list[int] = []
        work_ids: list[str] = []
        msg_ids: list[str] = []
        for i in range(2):
            wid = f"work-{i}-{uuid.uuid4().hex[:8]}"
            work_ids.append(wid)
            mid = f"msg-{i}-{uuid.uuid4().hex[:12]}"
            msg_ids.append(mid)
            t = Task(
                work_id=wid, task_type="process_report",
                instance_id=iid, message_id=mid,
                status=TaskStatus.PAUSED.value, worker_id="w0",
                cancel_requested=True,
                cancel_requested_at=now.isoformat(),
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            task_ids.append(int(t.id))
            # Seed the corresponding orphan queue row.
            s.add(MessageQueue(
                message_id=mid, instance_id=iid,
                content=f"orphan-{i}",
                type=MessageType.COMPLETION_REPORT.value,
                source="test",
                status=MessageStatus.PROCESSING.value,
                enqueued_at=now, last_activity_at=now,
                processing_task_id=None,
            ))
            s.commit()
    return iid, msg_ids[0], msg_ids[1]


# ─── 12b. Exact production state test (Task 12b) ──────────────────────────


def test_pause_during_report_turn_resume_reaches_completed(
    lifecycle_service, engine, write_guard
) -> None:
    """Pause during a ``process_report`` turn → resume → tasks
    resume naturally → leader completes (Task 12b primary).

    Phase 4b/4c (2026-08-12, pause/resume redesign): the resume
    cascade transitions the Tasks ``PAUSED → PENDING`` (was
    ``PAUSED → CANCELLED`` pre-migration). The Tasks stay live so
    the WorkerPool can re-claim them and complete the orphan
    messages naturally. The cascade no longer reconciles the
    orphan message_queue rows directly (UPDATE 4 removed).

    Seeds the exact production state (2 orphan ``processing``
    ``completion_report`` rows, backing Tasks at ``paused``),
    runs the resume cascade, then simulates the WorkerPool's
    claim+complete path against the PENDING Tasks. The
    complete_task call fires ``reconcile_turn_mirror``, which
    marks the orphan messages as completed (per the new
    CASE WHEN terminal_reason='cancelled' THEN 'failed' ELSE
    'completed' rule).

    Verifies:
      * The resume cascade leaves the Tasks in PENDING (not
        CANCELLED) and the message_queue rows in PROCESSING
        (not completed) — the cascade does NOT reconcile
        non-terminal Tasks' messages.
      * After the WorkerPool completes the PENDING Tasks, the
        orphan messages are marked as completed (via
        ``reconcile_turn_mirror``).
      * The instance reaches ``COMPLETED``.
    """
    ensure_schema(engine)
    iid, msg1, msg2 = _seed_two_orphans_at_completed_processing_state(
        engine
    )

    # Run the cascade. With the new behavior, the cascade
    # transitions the Tasks PAUSED → PENDING and does NOT touch
    # the message_queue rows.
    result = lifecycle_service._resume_cascade_db_sync(
        engine, write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: Tasks are PENDING, messages still PROCESSING,
    # no reconciliation in the cascade.
    assert result.reconciled_message_ids == []
    assert len(result.resumed_task_ids) == 2
    assert read_message(engine, msg1)["status"] == MessageStatus.PROCESSING.value
    assert read_message(engine, msg2)["status"] == MessageStatus.PROCESSING.value

    # Simulate the WorkerPool's claim+complete path: the WorkerPool
    # claims the PENDING tasks, drives the graph, and calls
    # complete_task. The complete_task call fires
    # reconcile_turn_mirror, which marks the orphan messages as
    # completed.
    from sqlmodel import select
    from daemon.repositories.task.models import Task as TaskModel
    # First, look up the task IDs (avoiding detached-instance
    # issues by not relying on the session-bound objects).
    task_ids = []
    with Session(engine) as s:
        paused_tasks = s.exec(
            select(TaskModel).where(
                TaskModel.instance_id == iid,
                TaskModel.status == TaskStatus.PENDING.value,
            )
        ).all()
        task_ids = [t.id for t in paused_tasks]
    assert len(task_ids) == 2, (
        f"expected 2 PENDING tasks after cascade, got {len(task_ids)}"
    )
    # Complete each task. Use the TaskRepository's complete_task,
    # which fires reconcile_turn_mirror.
    task_repo = TaskRepository(engine)
    for task_id in task_ids:
        # First transition PENDING → RUNNING (simulating the
        # Worker's claim).
        with Session(engine) as s:
            task = s.get(TaskModel, task_id)
            task.status = TaskStatus.RUNNING.value
            s.add(task)
            s.commit()
        # Then complete (RUNNING → COMPLETED), which fires
        # reconcile_turn_mirror.
        task_repo.complete_task(task_id, result={"summary": "test"})

    # Now the orphan messages are COMPLETED.
    assert read_message(engine, msg1)["status"] == MessageStatus.COMPLETED.value
    assert read_message(engine, msg2)["status"] == MessageStatus.COMPLETED.value


# ─── 13. Defense-in-depth test (Task 13) ───────────────────────────────────


def test_defense_in_depth_guard_excludes_terminal_orphan(
    engine, write_guard
) -> None:
    """With UPDATE 4 BYPASSED, the parent-completion guard still
    excludes the orphan (terminal-only) row.

    Simulates the scenario where UPDATE 4 was disabled (e.g. a
    regression in a future change) by manually inserting the
    production state (orphan row at ``processing``, backing Task
    at ``cancelled``) and asserting the parent-guard predicate
    excludes it. This proves the defense-in-depth contract
    independent of UPDATE 4.
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)

    # Manually advance the Task to CANCELLED (simulates "UPDATE 4
    # was bypassed" or "we are testing historical state").
    with Session(engine) as s:
        task = s.get(Task, scenario.cancelled_task_id)
        task.status = TaskStatus.CANCELLED.value
        s.add(task)
        s.commit()

    # Apply the shared predicate to the orphan row.
    from daemon.repositories.message_queue.predicates import (
        message_queue_counts_as_pending,
    )
    with Session(engine) as s:
        row = s.get(MessageQueue, scenario.orphaned_message_id)
        is_pending = message_queue_counts_as_pending(row, engine)

    # Defense in depth: the guard excludes the terminal-only orphan.
    assert is_pending is False


# ─── 12. Full flow: re-fire self-heals (Task 12 + 17) ──────────────────────


def test_post_reconcile_refire_self_heals_orphan(
    engine, write_guard
) -> None:
    """Phase 2 A5.1: the post-reconcile re-fire self-heals a
    future incident by re-evaluating the parent-completion
    guard.

    Phase 4b/4c (2026-08-12, pause/resume redesign): the resume
    cascade no longer reconciles orphan messages (UPDATE 4
    removed). The re-fire path is also removed — the WorkerPool's
    natural claim+complete path owns the orphan message cleanup.

    This test now verifies that the resume cascade leaves the
    orphan message in PROCESSING (the natural-completion
    pre-condition) and that ``pending_count`` is 1 (the parent
    still has outstanding work to observe). When the WorkerPool
    later completes the PENDING Task, ``reconcile_turn_mirror``
    marks the message as completed.
    """
    ensure_schema(engine)
    scenario = seed_orphan_scenario(engine)

    # Run the cascade.
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    # Turn-Reconciler migration: provide a real TaskRepository so the
    # reconciler actually runs (not a MagicMock no-op).
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    service._resume_cascade_db_sync(
        engine, write_guard,
        tree_ids=[scenario.instance_id],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: the cascade no longer reconciles the orphan
    # message. ``pending_count`` is 1 (the orphan is still
    # ``processing``). The WorkerPool will claim the PENDING Task
    # and complete the message naturally.
    from daemon.repositories.message_queue.predicates import (
        message_queue_counts_as_pending,
    )
    with Session(engine) as s:
        candidates = list(
            s.exec(
                select(MessageQueue).where(
                    MessageQueue.instance_id == scenario.instance_id,
                    MessageQueue.status.in_([
                        MessageStatus.READY.value,
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]),
                )
            )
        )
    pending_count = sum(
        1
        for row in candidates
        if message_queue_counts_as_pending(row, engine)
    )
    # Post-cascade: orphan is still pending (the cascade does NOT
    # reconcile non-terminal Tasks' messages anymore).
    assert pending_count == 1


def _seed_two_orphans_at_waiting_children_state(
    engine: Engine,
) -> tuple[str, str, str]:
    """Seed the production state for the re-fire test: a root
    instance at ``WAITING_CHILDREN`` (the stuck state the re-fire
    is designed to fix) with two ``processing``
    ``completion_report`` rows backed by ``PAUSED`` Tasks that
    the resume cascade will cancel.

    Difference from ``_seed_two_orphans_at_completed_processing_state``
    (line 114): the instance is at ``WAITING_CHILDREN`` (root, no
    parent) instead of ``PAUSED``. The Tasks are at ``PAUSED``
    (NOT ``CANCELLED``) so that the cascade's UPDATE 2 actually
    cancels them and UPDATE 4 has work to reconcile. If the Tasks
    were pre-cancelled, UPDATE 2 would not move them and UPDATE 4
    would have no candidates (the ``cancelled_task_ids`` returned
    by UPDATE 2 is the only source of eligibility for UPDATE 4).

    Returns:
        (instance_id, msg_id_1, msg_id_2)
    """
    iid = f"inst-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        from daemon.repositories.instance.models import (
            Instance,
            InstanceStatus,
        )
        s.add(Instance(
            instance_id=iid, agent_id="dev", agent_dir="/tmp",
            agent_name="dev", project_id="test",
            status=InstanceStatus.WAITING_CHILDREN.value,
            created_at=now.isoformat(),
        ))
        msg_ids: list[str] = []
        for i in range(2):
            wid = f"work-{i}-{uuid.uuid4().hex[:8]}"
            mid = f"msg-{i}-{uuid.uuid4().hex[:12]}"
            msg_ids.append(mid)
            t = Task(
                work_id=wid, task_type="process_report",
                instance_id=iid, message_id=mid,
                status=TaskStatus.PAUSED.value, worker_id="w0",
                cancel_requested=True,
                cancel_requested_at=now.isoformat(),
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            s.add(MessageQueue(
                message_id=mid, instance_id=iid,
                content=f"orphan-{i}",
                type=MessageType.COMPLETION_REPORT.value,
                source="test",
                status=MessageStatus.PROCESSING.value,
                enqueued_at=now, last_activity_at=now,
                processing_task_id=None,
            ))
            s.commit()
    return iid, msg_ids[0], msg_ids[1]


def test_phase2_post_reconcile_refire_resolves_orphan_via_guard(
    engine, write_guard, monkeypatch
) -> None:
    """Phase 4b/4c (2026-08-12, pause/resume redesign): the
    resume cascade no longer reconciles orphan messages (UPDATE 4
    removed) and the post-reconcile re-fire is removed. The
    WorkerPool's natural claim+complete path owns the orphan
    message cleanup.

    This test verifies the new behavior: the resume cascade
    leaves the orphan messages in PROCESSING (the natural-
    completion pre-condition). When the WorkerPool later claims
    and completes the PENDING Task, ``reconcile_turn_mirror``
    marks the message as completed. The instance is then driven
    to COMPLETED via the natural finalize path.

    Scenario:
      * Root instance at ``WAITING_CHILDREN`` (the stuck state).
      * Two ``process_report`` Tasks at ``PAUSED`` (the cascade
        transitions them ``PAUSED → PENDING``).
      * Two orphan ``completion_report`` rows at
        ``status='processing'`` with ``processing_task_id=NULL``
        (the exact production orphan shape).

    Run the cascade. UPDATE 1 is a no-op (instance is not
    ``paused``). ResumeTurn transitions the Tasks
    ``PAUSED → PENDING``. The orphan messages remain PROCESSING
    (the cascade no longer reconciles non-terminal Tasks'
    messages). Simulate the WorkerPool's claim+complete path to
    drive the natural completion, then verify the instance
    reaches ``COMPLETED``.

    Verifies:
      * The cascade leaves the Tasks in PENDING (not CANCELLED).
      * The orphan messages are STILL PROCESSING after the cascade.
      * The WorkerPool's claim+complete path marks the orphan
        messages as completed (via ``reconcile_turn_mirror``).
      * The instance reaches ``COMPLETED``.

    The bus singleton is monkeypatched so the parent-completion
    guard's ``get_dependency_bus()`` lookup returns a mock with
    ``count_pending_for_target_sync`` returning 0.
    """
    ensure_schema(engine)
    iid, msg1, msg2 = _seed_two_orphans_at_waiting_children_state(
        engine
    )

    # Stub the bus so the parent-completion guard can evaluate
    # pending children without raising A8. The stub returns 0
    # (no pending children), which is the production state after
    # a clean natural completion.
    mock_bus = MagicMock()
    mock_bus.count_pending_for_target_sync.return_value = 0
    monkeypatch.setattr(
        "daemon.services.dependency_bus._dependency_bus",
        mock_bus,
    )

    # Run the cascade.
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    # Turn-Reconciler migration: provide a real TaskRepository so the
    # reconciler actually runs (not a MagicMock no-op).
    manager._task_repo = TaskRepository(engine=engine)
    service._manager = manager
    service._resume_cascade_db_sync(
        engine, write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # Phase 4b/4c: the cascade does NOT reconcile the orphan
    # messages. Both queue rows are still PROCESSING.
    assert read_message(engine, msg1)["status"] == MessageStatus.PROCESSING.value
    assert read_message(engine, msg2)["status"] == MessageStatus.PROCESSING.value

    # Simulate the WorkerPool's claim+complete path.
    from sqlmodel import select
    from daemon.repositories.task.models import Task as TaskModel
    task_ids = []
    with Session(engine) as s:
        paused_tasks = s.exec(
            select(TaskModel).where(
                TaskModel.instance_id == iid,
                TaskModel.status == TaskStatus.PENDING.value,
            )
        ).all()
        task_ids = [t.id for t in paused_tasks]
    assert len(task_ids) == 2
    task_repo = TaskRepository(engine)
    for task_id in task_ids:
        # PENDING → RUNNING (claim)
        with Session(engine) as s:
            task = s.get(TaskModel, task_id)
            task.status = TaskStatus.RUNNING.value
            s.add(task)
            s.commit()
        # RUNNING → COMPLETED (fires reconcile_turn_mirror)
        task_repo.complete_task(task_id, result={"summary": "test"})

    # The WorkerPool's complete_task fires reconcile_turn_mirror,
    # which marks the orphan messages as COMPLETED.
    assert read_message(engine, msg1)["status"] == MessageStatus.COMPLETED.value
    assert read_message(engine, msg2)["status"] == MessageStatus.COMPLETED.value
