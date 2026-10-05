"""Behavior matrix for the simplified active-JobItem predicate."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobLock
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def seed_instance(engine, status=InstanceStatus.IDLE.value):
    iid = f"inst-{uuid.uuid4()}"
    with Session(engine) as session:
        session.add(Instance(instance_id=iid, agent_id="developer", agent_dir="/tmp", project_id="p", status=status, version=1, instance_metadata={}))
        session.commit()
    return iid


def seed_task(engine, iid, status, work_id=None, message_id=None):
    task = Task(task_type=TaskType.PROCESS_MESSAGE.value, instance_id=iid, message_id=message_id or str(uuid.uuid4()), status=status, work_id=work_id or str(uuid.uuid4()), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    with Session(engine) as session:
        session.add(task); session.commit(); session.refresh(task)
    return task


def seed_job(engine, iid, work_id, admission):
    job = JobItem(job_id=work_id, agent_id="developer", agent_dir="/tmp", message="x", source="api", project_id="p", priority=5, job_metadata={}, queue_id="system_parallel_queue", job_type="task", instance_id=iid, admission_state=admission)
    with Session(engine) as session:
        session.add(job); session.commit()


def _insert_job_locks_for_active(engine, *, work_id: str, iid: str) -> None:
    """Seed a paired ``job_locks`` row for an active JobItem.

    Production contract (the reconciler invariant at
    task/repository.py:2205-2218, gated by the PG
    ``trg_job_locks_active_guard`` trigger): an
    ``admission_state='active'`` JobItem MUST have a paired
    ``job_locks`` row (the active-guard invariant). The matrix
    test predated the precondition; this helper pairs the active
    JobItem with the lock so the fixture matches production.

    The slot is 0 (any slot is fine — only the existence of a
    row matters for the active-guard). The ``(project_id,
    queue_id, lock_slot)`` UNIQUE constraint allows the row to
    coexist with sibling rows from other fixtures on the same
    queue.
    """
    from datetime import datetime as _dt, timezone as _tz

    with Session(engine) as session:
        session.add(
            JobLock(
                lock_id=str(uuid.uuid4()),
                project_id="p",
                queue_id="system_parallel_queue",
                job_id=work_id,
                instance_id=iid,
                lock_slot=0,
                acquired_at=_dt.now(_tz.utc).isoformat(),
            )
        )
        session.commit()


@pytest.mark.parametrize("admission", [AdmissionState.ACTIVE.value, AdmissionState.QUEUED.value])
@pytest.mark.parametrize("backing_status,blocks", [
    (None, False),
    (TaskStatus.PENDING.value, True),
    (TaskStatus.RUNNING.value, True),
    (TaskStatus.PAUSED.value, True),
    (TaskStatus.COMPLETED.value, False),
    (TaskStatus.FAILED.value, False),
    (TaskStatus.CANCELLED.value, False),
])
def test_jobitem_task_status_matrix(engine, admission, backing_status, blocks):
    iid = seed_instance(engine)
    work_id = str(uuid.uuid4())
    if backing_status is not None:
        seed_task(engine, iid, backing_status, work_id=work_id)
    seed_job(engine, iid, work_id, admission)
    # JobItem admission_state='active' requires a paired job_locks
    # row (the PG-side ``trg_job_locks_active_guard`` trigger; the
    # reconciler invariant at task/repository.py:2205-2218 enforces
    # the same precondition on SQLite — the task repository's
    # claim path will trip the WARNING if the lock is missing).
    # The matrix test predated the precondition; the seed_job
    # helper would emit a Reconciler WARNING on the active row.
    # Pair the active JobItem with a job_locks row so the
    # fixture matches the production precondition.
    if admission == AdmissionState.ACTIVE.value:
        _insert_job_locks_for_active(engine, work_id=work_id, iid=iid)
    # The "candidate" task — what the test was implicitly
    # claiming should be blocked when blocks=True. Its work_id
    # is captured so the assertion can verify it is NOT the
    # task returned by ``claim_pending_task`` (the original
    # assertion ``(claimed is None) is blocks`` was too strict
    # because when ``backing_status=PENDING`` the backing task
    # itself is claimable — the cross-system guard has a self-
    # deadlock exclusion that excludes only the candidate's id
    # from the EXISTS subquery; the backing is itself eligible
    # until a worker claims it).
    candidate = seed_task(engine, iid, TaskStatus.PENDING.value)
    candidate_work_id = candidate.work_id
    repo = TaskRepository(engine)
    busy = repo.has_pending_tasks_blocked_by_busy_instance()
    claimed = repo.claim_pending_task("worker")
    assert busy is blocks
    # ``blocks=True`` ⇒ OTHER candidates for this instance are
    # blocked by the cross-system guard. The backing task
    # itself may be claimable when ``backing_status=PENDING``
    # (the cross-system guard has a self-deadlock exclusion
    # that excludes only the candidate's own id from the
    # EXISTS subquery; the backing is itself eligible until
    # a worker claims it). The original assertion
    # ``(claimed is None) is blocks`` was too strict for that
    # case — relaxed: ``blocks=True`` ⇒ the claimed task is
    # either None (no eligible task) or the backing task;
    # ``blocks=False`` ⇒ the claimed task exists and is the
    # candidate (the second seeded task).
    if blocks:
        assert claimed is None or claimed.work_id == work_id, (
            f"blocks=True ⇒ the claim must be either None or "
            f"the backing task (work_id={work_id}); got "
            f"claimed.work_id="
            f"{claimed.work_id if claimed else None}"
        )
    else:
        assert claimed is not None, (
            f"blocks=False ⇒ a candidate exists and must be "
            f"claimable; got claimed={claimed}"
        )
        assert claimed.work_id == candidate_work_id, (
            f"blocks=False ⇒ the claimed task must be the "
            f"candidate (second seeded task, work_id="
            f"{candidate_work_id}); got claimed.work_id="
            f"{claimed.work_id}"
        )


@pytest.mark.parametrize("admission", [AdmissionState.ACTIVE.value, AdmissionState.QUEUED.value])
def test_waiting_children_lifts_jobitem_block(engine, admission):
    iid = seed_instance(engine, InstanceStatus.WAITING_CHILDREN.value)
    backing = seed_task(engine, iid, TaskStatus.PAUSED.value)
    seed_job(engine, iid, backing.work_id, admission)
    seed_task(engine, iid, TaskStatus.PROCESS_REPORT.value if False else TaskStatus.PENDING.value, work_id=str(uuid.uuid4()))
    repo = TaskRepository(engine)
    assert repo.has_pending_tasks_blocked_by_busy_instance() is False
    # The explicit pause/instance gates may still govern claims; this assertion
    # targets the cross-system busy probe's retained exception.


def test_retry_child_different_work_id_is_not_blocked_by_cancelled_parent(engine):
    iid = seed_instance(engine)
    message_id = str(uuid.uuid4())
    parent = seed_task(engine, iid, TaskStatus.CANCELLED.value, message_id=message_id)
    seed_job(engine, iid, parent.work_id, AdmissionState.ACTIVE.value)
    retry = seed_task(engine, iid, TaskStatus.PENDING.value, message_id=message_id)
    repo = TaskRepository(engine)
    assert repo.has_pending_tasks_blocked_by_busy_instance() is False
    assert repo.claim_pending_task("worker").work_id == retry.work_id


def test_multiple_jobitems_any_inflight_backing_task_blocks(engine):
    iid = seed_instance(engine)
    terminal = seed_task(engine, iid, TaskStatus.COMPLETED.value)
    seed_job(engine, iid, terminal.work_id, AdmissionState.ACTIVE.value)
    live = seed_task(engine, iid, TaskStatus.PAUSED.value)
    seed_job(engine, iid, live.work_id, AdmissionState.ACTIVE.value)
    seed_task(engine, iid, TaskStatus.PENDING.value)
    repo = TaskRepository(engine)
    assert repo.has_pending_tasks_blocked_by_busy_instance() is True
    assert repo.claim_pending_task("worker") is None
