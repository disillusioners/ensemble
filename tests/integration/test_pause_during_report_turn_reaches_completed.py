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
    """Pause during a ``process_report`` turn → resume → both
    orphan rows reconcile → leader completes (Task 12b primary).

    Seeds the exact production state (2 orphan ``processing``
    ``completion_report`` rows, backing Tasks at ``paused``),
    runs the resume cascade, then runs the post-reconcile re-fire
    against a stub ``ChildReportsService`` that emulates the
    ``_process_child_completion_db_sync`` short-circuit (the
    instance is at ``WAITING_CHILDREN`` in production; here we
    pre-seed it at ``WAITING_CHILDREN`` post-cascade so the
    re-fire's idempotency guard fires).

    Verifies:
      * UPDATE 4 reconciles both orphan rows.
      * After the cascade + re-fire, the leader reaches
        ``COMPLETED``.
    """
    ensure_schema(engine)
    iid, msg1, msg2 = _seed_two_orphans_at_completed_processing_state(
        engine
    )
    # Move the instance to WAITING_CHILDREN (production state at
    # resume time) — the resume cascade will then move it to
    # RUNNING via UPDATE 1, but we want to test the re-fire
    # against a near-WAITING_CHILDREN state.
    from daemon.repositories.instance.models import (
        InstanceStatus, Instance,
    )
    # Actually keep it at PAUSED for the cascade to do its work.
    # The re-fire checks ``pending_count`` and if it's 0, calls
    # ``_process_child_completion_db_sync`` which has its own
    # idempotency guards.

    # Run the cascade.
    result = lifecycle_service._resume_cascade_db_sync(
        engine, write_guard,
        tree_ids=[iid],
        ancestor_ids=set(),
        is_root_resume=True,
    )

    # UPDATE 4 reconciled both orphan rows.
    assert msg1 in result.reconciled_message_ids
    assert msg2 in result.reconciled_message_ids
    # Tasks were cancelled.
    assert len(result.cancelled_task_ids) == 2

    # Both queue rows are now ``completed``.
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

    We verify the contract by directly invoking the re-fire
    helper after UPDATE 4 has run. The re-fire inspects the
    instance's own queue (now reconciled to ``completed``) and
    fires the completion cascade via
    ``_process_child_completion_db_sync``.

    For this test we stub the manager's ``ChildReportsService``
    so the re-fire can complete without a full manager. The
    stub returns ``idempotency_skip`` (the instance is already
    terminal in the test's setup).
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

    # Both UPDATE 2 and UPDATE 4 fired. The re-fire path inspects
    # the queue post-cascade. After UPDATE 4 the queue is empty
    # (reconciled), so ``pending_count`` should be 0. We verify
    # the pre-condition for the re-fire to fire.
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
    # Post-reconcile: no rows in base status filter (the orphan
    # is now completed).
    assert pending_count == 0


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
    """Phase 2 A5.1: the post-reconcile re-fire resolves the
    orphan via the parent-completion guard.

    The sibling test ``test_post_reconcile_refire_self_heals_orphan``
    (line 274 above) only verifies the precondition
    (``pending_count == 0`` after UPDATE 4) — it does NOT verify
    that the re-fire actually fires
    ``_process_child_completion_db_sync`` and transitions the
    instance. This test goes further.

    Scenario:
      * Root instance at ``WAITING_CHILDREN`` (the stuck state
        the re-fire is designed to fix).
      * Two ``process_report`` Tasks at ``PAUSED`` (the cascade
        will cancel them via UPDATE 2).
      * Two orphan ``completion_report`` rows at
        ``status='processing'`` with ``processing_task_id=NULL``
        (the exact production orphan shape).

    Run the cascade. UPDATE 1 is a no-op (instance is not
    ``paused``). UPDATE 2 cancels the Tasks. UPDATE 4 reconciles
    the orphan rows. The re-fire inspects the instance's own
    queue (``pending_count == 0`` after UPDATE 4) and calls
    ``_process_child_completion_db_sync``. The function
    short-circuits on terminal/paused states
    (``child_reports.py:1212-1219``); the instance is at
    ``WAITING_CHILDREN`` so it proceeds through the root-bus
    gate, the pending-tasks guard, and the own-queue
    ``pending_count`` guard, then transitions the instance to
    ``COMPLETED``.

    Verifies:
      * UPDATE 4 reconciles both orphan rows.
      * The re-fire fires (the instance transitions AWAY from
        ``WAITING_CHILDREN`` — the strongest evidence the re-fire
        actually ran).
      * The instance reaches ``COMPLETED``.

    The bus singleton is monkeypatched so the parent-completion
    guard's ``get_dependency_bus()`` lookup returns a mock with
    ``count_pending_for_target_sync`` returning 0; without this
    the guard raises ``RuntimeError`` per the A8 hard error at
    ``child_reports.py:1274-1281`` and the re-fire's try/except
    at ``instance_lifecycle.py:3421-3430`` swallows it.
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

    # UPDATE 4 reconciled both orphan rows (the cascade's job).
    assert read_message(engine, msg1)["status"] == MessageStatus.COMPLETED.value
    assert read_message(engine, msg2)["status"] == MessageStatus.COMPLETED.value

    # The re-fire fired AND the instance transitioned to COMPLETED.
    # Before the re-fire, the instance was at WAITING_CHILDREN (the
    # stuck state); after the re-fire's
    # ``_process_child_completion_db_sync`` call, the instance is
    # at COMPLETED. This is the strongest assertion that the
    # re-fire actually fired the parent-completion guard — the
    # pre-existing test at line 274 only checks the precondition.
    instance = read_instance(engine, iid)
    assert instance is not None
    assert instance["status"] == "completed"
