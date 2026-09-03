"""Targeted end-to-end probe of JobProcessor._defer_idle_check.

Confirms the audit-brief scenario flows through the actual Gate A
probe path (JobProcessor._defer_idle_check), not just the repository
predicates in isolation. The probe path is the one called in
production by the JobProcessor._process_next_job() loop.

The probe path semantics (per daemon/services/job_processor.py:213):

  1. Consult JobRepository.has_active_non_deferred_work(project_id).
     Truthy ⇒ return 1 (gate blocks).
  2. Otherwise consult TaskRepository.has_active_non_deferred_work(
     project_id). Truthy ⇒ return 1.
  3. Otherwise return 0 (gate says "idle" — defer queue may admit).

A 0 return means the defer queue would wrongly admit the next job.
"""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import SQLModel
from unittest.mock import MagicMock

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_processor import JobProcessor
from daemon.repositories import SQLModelProjectRepository


def _insert_instance(engine, *, instance_id, project_id, status):
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO instances
                (instance_id, agent_id, agent_dir, status, project_id,
                 created_at, updated_at, version)
            VALUES
                (:instance_id, 'developer', 'agents/developer', :status,
                 :project_id, :created_at, :updated_at, 1)
            """), {"instance_id": instance_id, "status": status,
                   "project_id": project_id, "created_at": now, "updated_at": now})


def _insert_queue(engine, *, queue_id, project_id, queue_type):
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO job_queues
                (queue_id, project_id, queue_name, queue_name_lower,
                 queue_type, concurrency_limit, is_system, is_paused,
                 description, created_at, updated_at)
            VALUES
                (:queue_id, :project_id, :queue_name, :queue_name_lower,
                 :queue_type, 1, 0, 0, NULL, :created_at, :updated_at)
            """), {"queue_id": queue_id, "project_id": project_id,
                   "queue_name": queue_id, "queue_name_lower": queue_id.lower(),
                   "queue_type": queue_type, "created_at": now, "updated_at": now})


def _insert_job_item(engine, *, job_id, instance_id, project_id, queue_id,
                     admission_state, job_type="message"):
    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(text(
            """
            INSERT INTO job_queue_items
                (job_id, agent_id, agent_dir, message, source,
                 project_id, queue_id, priority, admission_state,
                 created_at, instance_id, job_type, retry_count, metadata)
            VALUES
                (:job_id, 'developer', 'agents/developer', 'test', 'api',
                 :project_id, :queue_id, 0, :admission_state,
                 :created_at, :instance_id, :job_type, 0, '{}')
            """), {"job_id": job_id, "project_id": project_id,
                   "queue_id": queue_id, "admission_state": admission_state,
                   "created_at": now, "instance_id": instance_id,
                   "job_type": job_type})


@pytest.fixture
def fb_engine(tmp_path):
    db_path = tmp_path / "probe.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def test_defer_idle_check_probe_path_with_settled_mirror(tmp_path):
    """JobProcessor._defer_idle_check returns 0 on the audit scenario.

    Direct probe of the Gate A probe path. Constructs the audit-brief
    scenario, instantiates a JobProcessor with the real repository
    stack, and asserts the probe return value.
    """
    db_path = tmp_path / "probe.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)

    try:
        # Construct the scenario.
        _insert_instance(
            eng,
            instance_id="inst-probe",
            project_id="proj-probe",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            eng,
            queue_id="queue-probe-par",
            project_id="proj-probe",
            queue_type="parallel",
        )
        _insert_job_item(
            eng,
            job_id="job-probe-mirror",
            instance_id="inst-probe",
            project_id="proj-probe",
            queue_id="queue-probe-par",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # Build the JobProcessor with the real repository stack.
        job_repo = JobRepository(eng)
        queue_repo = JobQueueRepository(eng)
        task_repo = TaskRepository(eng)
        lock_manager = JobLockManager(lock_repo=MagicMock())

        # Instance manager mock with the real task_repo wired in.
        im = MagicMock()
        im._task_repo = task_repo

        # Project repo mock — _defer_idle_check doesn't consult it.
        proj_repo = MagicMock(spec=SQLModelProjectRepository)

        proc = JobProcessor(
            queue_service=MagicMock(_repository=job_repo),
            instance_manager=im,
            project_repo=proj_repo,
            queue_repo=queue_repo,
        )

        # Run the actual probe path.
        result = asyncio.get_event_loop().run_until_complete(
            proc._defer_idle_check("proj-probe")
        )

        # INTENDED: 1 (gate blocks — defer queue must wait).
        # ACTUAL: 0 (gate wrongly says "idle" — the bug).
        assert result == 1, (
            f"JobProcessor._defer_idle_check returned {result} on the "
            f"audit scenario (settled mirror + non-terminal instance). "
            f"Intended return is 1 (blocked). The post-settle defer-gate "
            f"window is REAL at the Gate A probe path."
        )
    finally:
        eng.dispose()


def _make_gate_b_service(eng):
    """Build a JobQueueService with the real repository stack for Gate B.

    Returns (service, queue_repo, job_repo, task_repo, project_id).
    """
    from daemon.services.job_queue_service import JobQueueService

    job_repo = JobRepository(eng)
    queue_repo = JobQueueRepository(eng)
    task_repo = TaskRepository(eng)
    lock_manager = JobLockManager(lock_repo=MagicMock())
    svc = JobQueueService(job_repo, lock_manager, queue_repo)
    im = MagicMock()
    im._task_repo = task_repo
    svc.set_instance_manager(im)
    return svc


def test_gate_b_select_next_eligible_with_settled_mirror(tmp_path):
    """JobQueueService._select_next_eligible_job returns the defer job
    on the audit scenario — the gate WRONGLY says the defer job is
    eligible while the parent mission is live.

    Direct probe of Gate B (called from the JobProcessor's
    ``_process_next_job`` loop and from the JobQueueService admission
    path). The defer job would be admitted under the audit-brief
    scenario — confirming the bug at the production gate path.
    """
    db_path = tmp_path / "probe_gate_b.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)

    try:
        # Construct the audit-brief scenario + a defer job pending.
        _insert_instance(
            eng,
            instance_id="inst-gate-b",
            project_id="proj-gate-b",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _insert_queue(
            eng,
            queue_id="queue-par-gate-b",
            project_id="proj-gate-b",
            queue_type="parallel",
        )
        _insert_queue(
            eng,
            queue_id="queue-defer-gate-b",
            project_id="proj-gate-b",
            queue_type="defer",
        )
        _insert_job_item(
            eng,
            job_id="job-mirror-gate-b",
            instance_id="inst-gate-b",
            project_id="proj-gate-b",
            queue_id="queue-par-gate-b",
            admission_state=AdmissionState.DONE.value,
            job_type="message",
        )

        # Pending defer job (the defer queue's candidate).
        from daemon.repositories.job_queue.models import JobItem
        defer_job = MagicMock(spec=JobItem)
        defer_job.job_id = "job-defer-pending"
        defer_job.queue_id = "queue-defer-gate-b"
        defer_job.project_id = "proj-gate-b"
        defer_job.priority = 5
        defer_job.created_at = "2026-01-01T00:00:00"

        svc = _make_gate_b_service(eng)

        # Run Gate B.
        result = asyncio.get_event_loop().run_until_complete(
            svc._select_next_eligible_job([defer_job], "proj-gate-b")
        )

        # INTENDED: None (Gate B blocks — defer queue must wait).
        # ACTUAL: defer_job returned (Gate B wrongly says eligible — bug).
        assert result is None, (
            f"JobQueueService._select_next_eligible_job returned "
            f"{result!r} (the defer job) on the audit scenario — "
            f"the defer job is wrongly ELIGIBLE while the parent "
            f"mission is live. The post-settle defer-gate window is "
            f"REAL at the Gate B path too."
        )
    finally:
        eng.dispose()