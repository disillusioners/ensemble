"""Phase 1 + Phase 4 Task-Job Reconciliation.

Phase 1 tests cover ``TaskRepository.reconcile_terminal_task`` — the
self-contained SYNC method that cancels an orphaned Task (paused/pending)
when its linked JobItem (via ``work_id``) is already terminal (done/dead).

Phase 4 tests cover ``count_bad_state_tasks`` and
``batch_reconcile_bad_state_tasks`` — the system-wide count and batch
reconciliation methods that feed the bad-state visibility + enhanced
cleanup flow.

The ``AND EXISTS`` JobItem terminal subquery is the core safety gate —
it prevents accidental cancellation of Tasks whose JobItem is still
active/queued (pause-first crash recovery).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


def _seed_engine():
    """Create an in-memory engine with all tables and return it."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_instance(session: Session, iid: str):
    session.add(
        Instance(
            instance_id=iid,
            agent_id="developer",
            agent_dir="/tmp",
            project_id="p",
            status=InstanceStatus.IDLE.value,
            version=1,
            instance_metadata={},
        )
    )


def _seed_jobitem(session: Session, work_id: str, iid: str, admission_state: str, queue_id: str = "system_parallel_queue"):
    session.add(
        JobItem(
            job_id=work_id,
            agent_id="developer",
            agent_dir="/tmp",
            message="msg",
            source="api",
            project_id="p",
            priority=5,
            job_metadata={},
            queue_id=queue_id,
            job_type="task",
            instance_id=iid,
            admission_state=admission_state,
        )
    )


def _seed_task(session: Session, work_id: str, iid: str, status: str):
    session.add(
        Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=iid,
            message_id=str(uuid.uuid4()),
            status=status,
            work_id=work_id,
            created_at=datetime.now(timezone.utc),
        )
    )


def _get_task_status(engine, work_id: str) -> str:
    with Session(engine) as session:
        task = session.exec(
            select(Task).where(Task.work_id == work_id)
        ).scalar_one()
        return task.status


def test_reconcile_paused_task_with_done_jobitem():
    """paused Task + done JobItem → Task cancelled, returns 1."""
    engine = _seed_engine()
    iid, work_id = f"inst-{uuid.uuid4()}", f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        _seed_jobitem(session, work_id, iid, AdmissionState.DONE.value)
        _seed_task(session, work_id, iid, TaskStatus.PAUSED.value)
        session.commit()

    repo = TaskRepository(engine)
    count = repo.reconcile_terminal_task(work_id)

    assert count == 1
    assert _get_task_status(engine, work_id) == TaskStatus.CANCELLED.value
    engine.dispose()


def test_reconcile_running_task_not_touched():
    """running Task + done JobItem → Task NOT touched, returns 0."""
    engine = _seed_engine()
    iid, work_id = f"inst-{uuid.uuid4()}", f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        _seed_jobitem(session, work_id, iid, AdmissionState.DONE.value)
        _seed_task(session, work_id, iid, TaskStatus.RUNNING.value)
        session.commit()

    repo = TaskRepository(engine)
    count = repo.reconcile_terminal_task(work_id)

    assert count == 0
    assert _get_task_status(engine, work_id) == TaskStatus.RUNNING.value
    engine.dispose()


def test_reconcile_paused_task_with_active_jobitem_not_touched():
    """paused Task + active JobItem → Task NOT touched (gate proof).

    Proves the ``AND EXISTS`` subquery blocks reconciliation when the
    JobItem is still active — this is what preserves pause-first crash
    recovery.
    """
    engine = _seed_engine()
    iid, work_id = f"inst-{uuid.uuid4()}", f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        _seed_jobitem(session, work_id, iid, AdmissionState.ACTIVE.value)
        _seed_task(session, work_id, iid, TaskStatus.PAUSED.value)
        session.commit()

    repo = TaskRepository(engine)
    count = repo.reconcile_terminal_task(work_id)

    assert count == 0
    assert _get_task_status(engine, work_id) == TaskStatus.PAUSED.value
    engine.dispose()


def test_reconcile_pending_task_with_dead_jobitem():
    """pending Task + dead JobItem → Task cancelled, returns 1."""
    engine = _seed_engine()
    iid, work_id = f"inst-{uuid.uuid4()}", f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        _seed_jobitem(session, work_id, iid, AdmissionState.DEAD.value)
        _seed_task(session, work_id, iid, TaskStatus.PENDING.value)
        session.commit()

    repo = TaskRepository(engine)
    count = repo.reconcile_terminal_task(work_id)

    assert count == 1
    assert _get_task_status(engine, work_id) == TaskStatus.CANCELLED.value
    engine.dispose()



# ============================================================
# Phase 4: count_bad_state_tasks
# ============================================================


def test_count_bad_state_tasks_includes_stuck_only():
    """2 paused tasks with terminal JobItems + 1 running task with done JobItem → count == 2."""
    engine = _seed_engine()
    iid = f"inst-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        # Two paused tasks with done JobItems → bad state
        for _ in range(2):
            wid = f"work-{uuid.uuid4()}"
            _seed_jobitem(session, wid, iid, AdmissionState.DONE.value)
            _seed_task(session, wid, iid, TaskStatus.PAUSED.value)
        # One running task with a done JobItem → NOT bad state (status != paused/pending)
        wid_run = f"work-{uuid.uuid4()}"
        _seed_jobitem(session, wid_run, iid, AdmissionState.DONE.value)
        _seed_task(session, wid_run, iid, TaskStatus.RUNNING.value)
        session.commit()

    repo = TaskRepository(engine)
    count = repo.count_bad_state_tasks()
    assert count == 2
    engine.dispose()


def test_count_bad_state_tasks_per_queue():
    """Tasks in different queues; filter by queue_id isolates the count."""
    engine = _seed_engine()
    iid = f"inst-{uuid.uuid4()}"
    queue_a, queue_b = "queue-a", "queue-b"
    with Session(engine) as session:
        _seed_instance(session, iid)
        # Queue A: 2 paused + done
        for _ in range(2):
            wid = f"work-{uuid.uuid4()}"
            _seed_jobitem(session, wid, iid, AdmissionState.DONE.value, queue_id=queue_a)
            _seed_task(session, wid, iid, TaskStatus.PAUSED.value)
        # Queue B: 1 pending + dead
        wid_b = f"work-{uuid.uuid4()}"
        _seed_jobitem(session, wid_b, iid, AdmissionState.DEAD.value, queue_id=queue_b)
        _seed_task(session, wid_b, iid, TaskStatus.PENDING.value)
        session.commit()

    repo = TaskRepository(engine)
    assert repo.count_bad_state_tasks(queue_id=queue_a) == 2
    assert repo.count_bad_state_tasks(queue_id=queue_b) == 1
    # No filter → system-wide count
    assert repo.count_bad_state_tasks() == 3
    engine.dispose()


def test_count_bad_state_tasks_excludes_active_jobitems():
    """Paused task + active JobItem → count == 0 (gate proof)."""
    engine = _seed_engine()
    iid, work_id = f"inst-{uuid.uuid4()}", f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        _seed_jobitem(session, work_id, iid, AdmissionState.ACTIVE.value)
        _seed_task(session, work_id, iid, TaskStatus.PAUSED.value)
        session.commit()

    repo = TaskRepository(engine)
    assert repo.count_bad_state_tasks() == 0
    engine.dispose()


def test_count_bad_state_tasks_empty():
    """No bad-state tasks → count == 0."""
    engine = _seed_engine()
    iid = f"inst-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        # A completed task (not bad state)
        wid = f"work-{uuid.uuid4()}"
        _seed_jobitem(session, wid, iid, AdmissionState.DONE.value)
        _seed_task(session, wid, iid, TaskStatus.COMPLETED.value)
        session.commit()

    repo = TaskRepository(engine)
    assert repo.count_bad_state_tasks() == 0
    engine.dispose()


# ============================================================
# Phase 4: batch_reconcile_bad_state_tasks
# ============================================================


def test_batch_reconcile_bad_state_tasks_transitions_to_cancelled():
    """2 paused + done → returns 2, tasks are CANCELLED."""
    engine = _seed_engine()
    iid = f"inst-{uuid.uuid4()}"
    work_ids = []
    with Session(engine) as session:
        _seed_instance(session, iid)
        for _ in range(2):
            wid = f"work-{uuid.uuid4()}"
            work_ids.append(wid)
            _seed_jobitem(session, wid, iid, AdmissionState.DONE.value)
            _seed_task(session, wid, iid, TaskStatus.PAUSED.value)
        session.commit()

    repo = TaskRepository(engine)
    count = repo.batch_reconcile_bad_state_tasks()

    assert count == 2
    for wid in work_ids:
        assert _get_task_status(engine, wid) == TaskStatus.CANCELLED.value
    engine.dispose()


def test_batch_reconcile_bad_state_tasks_is_idempotent():
    """Second call returns 0 (all already reconciled)."""
    engine = _seed_engine()
    iid, work_id = f"inst-{uuid.uuid4()}", f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        _seed_jobitem(session, work_id, iid, AdmissionState.DEAD.value)
        _seed_task(session, work_id, iid, TaskStatus.PENDING.value)
        session.commit()

    repo = TaskRepository(engine)
    first = repo.batch_reconcile_bad_state_tasks()
    second = repo.batch_reconcile_bad_state_tasks()

    assert first == 1
    assert second == 0
    engine.dispose()


def test_batch_reconcile_bad_state_tasks_excludes_running():
    """Running tasks are NOT touched by the batch reconcile."""
    engine = _seed_engine()
    iid = f"inst-{uuid.uuid4()}"
    wid_run = f"work-{uuid.uuid4()}"
    wid_paused = f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        # Running task with terminal JobItem → must NOT be cancelled
        _seed_jobitem(session, wid_run, iid, AdmissionState.DONE.value)
        _seed_task(session, wid_run, iid, TaskStatus.RUNNING.value)
        # Paused task with terminal JobItem → should be cancelled
        _seed_jobitem(session, wid_paused, iid, AdmissionState.DONE.value)
        _seed_task(session, wid_paused, iid, TaskStatus.PAUSED.value)
        session.commit()

    repo = TaskRepository(engine)
    count = repo.batch_reconcile_bad_state_tasks()

    assert count == 1
    assert _get_task_status(engine, wid_run) == TaskStatus.RUNNING.value
    assert _get_task_status(engine, wid_paused) == TaskStatus.CANCELLED.value
    engine.dispose()


def test_batch_reconcile_bad_state_tasks_per_queue():
    """Per-queue filter only reconciles tasks in that queue."""
    engine = _seed_engine()
    iid = f"inst-{uuid.uuid4()}"
    queue_a, queue_b = "queue-a", "queue-b"
    wid_a = f"work-{uuid.uuid4()}"
    wid_b = f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        _seed_instance(session, iid)
        _seed_jobitem(session, wid_a, iid, AdmissionState.DONE.value, queue_id=queue_a)
        _seed_task(session, wid_a, iid, TaskStatus.PAUSED.value)
        _seed_jobitem(session, wid_b, iid, AdmissionState.DONE.value, queue_id=queue_b)
        _seed_task(session, wid_b, iid, TaskStatus.PAUSED.value)
        session.commit()

    repo = TaskRepository(engine)
    count = repo.batch_reconcile_bad_state_tasks(queue_id=queue_a)
    assert count == 1
    # Only queue A's task should be cancelled
    assert _get_task_status(engine, wid_a) == TaskStatus.CANCELLED.value
    assert _get_task_status(engine, wid_b) == TaskStatus.PAUSED.value
    engine.dispose()


# ============================================================
# Phase 4 Task 21: Cleanup invariant — reconciled_bad_state excluded
# ============================================================


def test_job_cleanup_response_invariant_with_reconciled_bad_state():
    """JobCleanupResponse with reconciled_bad_state>0 satisfies the total invariant.

    Validates that reconciled_bad_state is EXCLUDED from total_processed:
    total_processed == cancelled_queued + cancelled_active, regardless of
    the reconciled_bad_state value.
    """
    from daemon.routers.schemas import JobCleanupResponse

    resp = JobCleanupResponse(
        cancelled_queued=10,
        cancelled_active=3,
        orphaned_reaped=1,
        reconciled_bad_state=2,
        total_processed=13,
    )
    assert resp.total_processed == 13
    assert resp.reconciled_bad_state == 2
