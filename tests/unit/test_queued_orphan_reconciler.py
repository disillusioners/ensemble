"""Queued orphan cleanup is owned by the turn reconciler."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


def test_queued_orphan_reconciled_then_fresh_task_claims():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    iid, work_id = f"inst-{uuid.uuid4()}", f"work-{uuid.uuid4()}"
    with Session(engine) as session:
        session.add(Instance(instance_id=iid, agent_id="developer", agent_dir="/tmp", project_id="p", status=InstanceStatus.IDLE.value, version=1, instance_metadata={}))
        session.add(JobItem(job_id=work_id, agent_id="developer", agent_dir="/tmp", message="orphan", source="api", project_id="p", priority=5, job_metadata={}, queue_id="system_parallel_queue", job_type="task", instance_id=iid, admission_state=AdmissionState.QUEUED.value))
        session.commit()

    repo = TaskRepository(engine)
    result = repo.reconcile_turn_mirror(work_id)
    assert result["found"] is False
    with Session(engine) as session:
        orphan = session.exec(select(JobItem).where(JobItem.job_id == work_id)).scalar_one()
        assert orphan.admission_state == AdmissionState.DONE.value
        assert orphan.terminal_reason == "orphaned_no_task"
        fresh = Task(task_type=TaskType.PROCESS_MESSAGE.value, instance_id=iid, message_id=str(uuid.uuid4()), status=TaskStatus.PENDING.value, work_id=str(uuid.uuid4()), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        session.add(fresh); session.commit(); session.refresh(fresh)
        fresh_work_id = fresh.work_id

    claimed = repo.claim_pending_task("worker")
    assert claimed is not None
    assert claimed.work_id == fresh_work_id
    engine.dispose()
