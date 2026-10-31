"""Shared helpers for the zombie-active close pin family (wi-78967dba).

Mirrors the f-arm seeder conventions from
``tests/job_queue/test_orphan_active_job_recovery.py`` (raw-SQL
seeders, real repositories, real ``JobRecoveryService``) so the
family harness stays uniform. Imported by the
``test_zombie_active_close_*`` packs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import text

from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_recovery_service import JobRecoveryService


def _aged(hours: float) -> datetime:
    # NAIVE-UTC bind (the tz discipline, daemon/services/
    # timestamps.py): aware binds render '+00:00' suffixes that make
    # SQLite lexicographic comparisons against naive-bound cutoffs
    # fragile.
    return (
        datetime.now(timezone.utc).replace(tzinfo=None)
        - timedelta(hours=hours)
    )


def insert_zombie_fixture(
    engine,
    *,
    verdict: str = "completed",
    age_hours: float = 3.0,
    instance_status: str = "running",
    instance_id: str | None = None,
    queue_suffix: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    job_type: str = "task",
    with_lock: bool = True,
    extra_task_status: str | None = None,
) -> dict:
    """Seed ONE zombie-ACTIVE fixture variant; returns the refs.

    Shape (Phase-0 PD-0.2): task-kind ``active`` JobItem + work_id
    Task in ``verdict`` terminal status + a NON-TERMINAL instance
    owning the job's instance_id + a model-faithful ``job_locks``
    row. Timestamps aged ``age_hours`` (default 3h > the 7200s
    bound).
    """
    tag = uuid.uuid4().hex[:8]
    iid = instance_id or f"inst-zc-{verdict}-{tag}"
    jid = f"job-zc-{verdict}-{tag}"
    qid = f"queue-zc-{queue_suffix or verdict}-{tag}"
    aged = _aged(age_hours)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, version, parent_id,
                     last_activity_at)
                VALUES
                    (:instance_id, 'developer', 'agents/developer',
                     :status, 'test-project',
                     :created_at, :updated_at, 1, NULL, :last_activity_at)
                """
            ),
            {
                "instance_id": iid,
                "status": instance_status,
                "created_at": aged,
                "updated_at": aged,
                "last_activity_at": aged,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO job_queues
                    (queue_id, project_id, queue_name, queue_name_lower,
                     queue_type, concurrency_limit, is_system, is_paused,
                     created_at, updated_at)
                VALUES
                    (:queue_id, 'test-project', :queue_name,
                     :queue_name_lower, 'fifo', 1, False, False,
                     :created_at, :updated_at)
                """
            ),
            {
                "queue_id": qid,
                "queue_name": qid,
                "queue_name_lower": qid,
                "created_at": aged,
                "updated_at": aged,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO job_queue_items
                    (job_id, agent_id, agent_dir, message, source,
                     project_id, queue_id, priority, admission_state,
                     created_at, instance_id, job_type, retry_count,
                     metadata)
                VALUES
                    (:job_id, 'developer', 'agents/developer', 'hi', 'api',
                     'test-project', :queue_id, 0, :admission_state,
                     :created_at, :instance_id, :job_type, 0, '{}')
                """
            ),
            {
                "job_id": jid,
                "queue_id": qid,
                "admission_state": admission_state,
                "created_at": aged,
                "instance_id": iid,
                "job_type": job_type,
            },
        )
        # ``datetime`` BINDS (not pre-rendered ISO strings) so the
        # SQLite column rendering matches the repository method's
        # datetime-bound cutoff comparison (the tz-naive-bind
        # discipline, daemon/services/timestamps.py).
        conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, message_id, status,
                     retry_count, created_at, cancel_requested,
                     retry_scheduled, work_id, is_deferred, is_background,
                     completed_at, started_at)
                VALUES
                    ('process_message', :instance_id, NULL, :status,
                     0, :created_at, False, False, :work_id, False, False,
                     :completed_at, :started_at)
                """
            ),
            {
                "instance_id": iid,
                "status": verdict,
                "created_at": aged,
                "work_id": jid,
                "completed_at": aged,
                "started_at": aged,
            },
        )
        if extra_task_status is not None:
            # A SECOND task on the instance (retry-child /
            # fresh-dispatch / PAUSED shapes for the negative pack).
            conn.execute(
                text(
                    """
                    INSERT INTO task
                        (task_type, instance_id, message_id, status,
                         retry_count, created_at, cancel_requested,
                         retry_scheduled, work_id, is_deferred,
                         is_background, completed_at, started_at)
                    VALUES
                        ('process_message', :instance_id, NULL, :status,
                         0, :created_at, False, False, :work_id2,
                         False, False, NULL, NULL)
                    """
                ),
                {
                    "instance_id": iid,
                    "status": extra_task_status,
                    "created_at": _aged(0.01),
                    "work_id2": f"job-zc-extra-{tag}",
                },
            )
        if with_lock:
            conn.execute(
                text(
                    """
                    INSERT INTO job_locks
                        (lock_id, project_id, queue_id, job_id,
                         instance_id, lock_slot, acquired_at)
                    VALUES
                        (:lock_id, 'test-project', :queue_id, :job_id,
                         :instance_id, 0, :acquired_at)
                    """
                ),
                {
                    "lock_id": f"lock-{jid}",
                    "queue_id": qid,
                    "job_id": jid,
                    "instance_id": iid,
                    "acquired_at": aged,
                },
            )
    return {"job_id": jid, "instance_id": iid, "queue_id": qid}


def read_job_state(engine, job_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state, terminal_reason, instance_id "
                "FROM job_queue_items WHERE job_id = :j"
            ),
            {"j": job_id},
        ).first()
        lock_count = conn.execute(
            text("SELECT COUNT(*) FROM job_locks WHERE job_id = :j"),
            {"j": job_id},
        ).scalar()
    if row is None:
        return {"exists": False}
    return {
        "exists": True,
        "admission_state": row[0],
        "terminal_reason": row[1],
        "instance_id": row[2],
        "lock_rows": int(lock_count),
    }


def make_recovery_service(
    engine,
    *,
    job_queue_service=None,
    with_task_repository: bool = True,
) -> JobRecoveryService:
    return JobRecoveryService(
        job_repository=JobRepository(engine),
        lock_repository=LockRepository(engine),
        instance_repository=SQLModelInstanceRepository(engine=engine),
        job_queue_service=job_queue_service,
        task_repository=TaskRepository(engine) if with_task_repository else None,
    )


def make_bus_mock(pending: list | None = None) -> MagicMock:
    """Mock dependency bus with EXPLICIT pending_watchers (the
    conftest MagicMock leaves it unset → the gate reads
    'unavailable'; these packs need the AVAILABLE-0 shape)."""
    bus = MagicMock()
    bus.pending_watchers = AsyncMock(return_value=list(pending or []))
    bus.count_pending_for_target = AsyncMock(return_value=0)
    bus.count_pending_for_target_sync = MagicMock(return_value=0)
    return bus


def make_jqs_mock() -> MagicMock:
    """JobQueueService mock with AsyncMock notify_watchers."""
    m = MagicMock()
    m.notify_watchers = AsyncMock(return_value=0)
    return m
