"""Behavior matrix for the simplified active-JobItem predicate."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
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
    seed_task(engine, iid, TaskStatus.PENDING.value)
    repo = TaskRepository(engine)
    busy = repo.has_pending_tasks_blocked_by_busy_instance()
    claimed = repo.claim_pending_task("worker")
    assert busy is blocks
    assert (claimed is None) is blocks


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
