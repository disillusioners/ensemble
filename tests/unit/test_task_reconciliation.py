"""Phase 1 Task-Job Reconciliation.

Tests ``TaskRepository.reconcile_terminal_task`` — the self-contained
SYNC method that cancels an orphaned Task (paused/pending) when its
linked JobItem (via ``work_id``) is already terminal (done/dead).

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


def _seed_jobitem(session: Session, work_id: str, iid: str, admission_state: str):
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
            queue_id="system_parallel_queue",
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
