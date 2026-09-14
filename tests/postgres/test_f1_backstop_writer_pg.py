"""PostgreSQL coverage for F1 backstop writer (``reconcile_terminal_message_mirrors``).

Gap-closing test for the 2026-09-14 joblock-leak fix. The SQLite
acceptance suite covers the backstop writer but SQLite hosts no
deferred constraint triggers; this module pins the same contract
against the REAL PostgreSQL trigger suite installed by
``EnsembleManager._ensure_postgres_columns`` (``daemon/manager.py:5536-5541``).

Why the trigger shape matters here
----------------------------------

The backstop writer (``reconcile_terminal_message_mirrors``) is the
F-1 recovery seam for missed inline mirror finalization: a message
JobItem whose linked Task is terminal but whose ``admission_state``
is still ``active`` (because the process crashed between the Task
commit and the inline ``done`` write). The backstop does the same
UPDATE + DELETE pair as the inline writer — just discovered by
periodic scan instead of by an event-time hook.

The PROD trigger function body (manager.py:5536) is::

    IF NEW.admission_state = 'active' AND NEW.job_type != 'message' THEN
        IF NOT EXISTS (SELECT 1 FROM job_locks WHERE instance_id = NEW.instance_id) THEN
            RAISE EXCEPTION ...
        END IF;
    END IF;

The backstop transitions ``active → done`` for a message-type
JobItem; at COMMIT time ``NEW.admission_state='done'`` so the
outer IF is FALSE — the trigger is a no-op for the UPDATE. The
same-commit lock DELETE is also a no-op for
``trg_job_locks_active_guard`` (which fires only on
INSERT/UPDATE).

Run with::

    .venv/bin/python -m pytest tests/postgres/test_f1_backstop_writer_pg.py \\
        -v -m postgres --override-ini="addopts="
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
    JobQueue,
    QueueType,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.project.models import Project
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository


pytestmark = pytest.mark.postgres


# =============================================================================
# PROD-shape trigger DDL — verbatim from daemon/manager.py:5536-5541
# =============================================================================
# Same PROD body as test_f1_inline_writer_pg.py. See that module's
# docstring for the rationale and the wider-test-side divergence
# note (J2 WARNING). Re-declared here rather than imported so each
# file is self-contained — the DDL is short and the divergence note
# is per-module scope discipline.
# =============================================================================
PROD_TRIGGER_DDL: tuple[str, ...] = (
    (
        "CREATE OR REPLACE FUNCTION job_queue_items_active_lock_guard() "
        "RETURNS TRIGGER AS $$ "
        "BEGIN "
        "  IF NEW.admission_state = 'active' AND NEW.job_type != 'message' THEN "
        "    IF NOT EXISTS (SELECT 1 FROM job_locks WHERE instance_id = NEW.instance_id) THEN "
        "      RAISE EXCEPTION "
        "        'admission_state=active requires a job_locks row (instance_id=%)', "
        "        NEW.instance_id "
        "        USING ERRCODE = 'integrity_constraint_violation'; "
        "    END IF; "
        "  END IF; "
        "  RETURN NEW; "
        "END; "
        "$$ LANGUAGE plpgsql"
    ),
    (
        "CREATE OR REPLACE FUNCTION job_locks_active_guard() "
        "RETURNS TRIGGER AS $$ "
        "BEGIN "
        "  IF NOT EXISTS ("
        "    SELECT 1 FROM job_queue_items "
        "    WHERE instance_id = NEW.instance_id "
        "      AND admission_state = 'active' "
        "      AND deleted_at IS NULL"
        "  ) THEN "
        "    RAISE EXCEPTION "
        "      'job_locks row requires admission_state=active (instance_id=%)', "
        "      NEW.instance_id "
        "      USING ERRCODE = 'integrity_constraint_violation'; "
        "  END IF; "
        "  RETURN NEW; "
        "END; "
        "$$ LANGUAGE plpgsql"
    ),
    "DROP TRIGGER IF EXISTS trg_job_queue_items_active_lock_guard ON job_queue_items",
    "DROP TRIGGER IF EXISTS trg_job_locks_active_guard ON job_locks",
    (
        "CREATE CONSTRAINT TRIGGER trg_job_queue_items_active_lock_guard "
        "AFTER INSERT OR UPDATE OF admission_state ON job_queue_items "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION job_queue_items_active_lock_guard()"
    ),
    (
        "CREATE CONSTRAINT TRIGGER trg_job_locks_active_guard "
        "AFTER INSERT OR UPDATE ON job_locks "
        "DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION job_locks_active_guard()"
    ),
)


@pytest.fixture(scope="module", autouse=True)
def _install_prod_triggers(pg_engine):
    with pg_engine.begin() as conn:
        for stmt in PROD_TRIGGER_DDL:
            conn.execute(text(stmt))
    yield


def _lock_count(conn, instance_id: str) -> int:
    return (
        conn.execute(
            text(
                "SELECT COUNT(*) FROM job_locks WHERE instance_id = :iid"
            ),
            {"iid": instance_id},
        ).scalar()
        or 0
    )


def _ensure_project_and_queue(
    engine,
    *,
    project_id: str = "f1-back-proj",
    queue_id: str = "f1-back-queue",
) -> None:
    """Seed the FK targets ``JobItem.queue_id`` and ``.project_id``
    point at. Required because the ORM-level INSERT honors the
    FK constraints (raw-SQL test fixtures in
    ``test_orphan_reaper_pg.py`` insert via raw text and bypass
    the FK via the same transaction)."""
    with Session(engine) as session:
        existing_proj = session.get(Project, project_id)
        if existing_proj is None:
            session.add(
                Project(
                    project_id=project_id,
                    name=project_id,
                    project_type="system",
                    status="active",
                    job_queue_paused=False,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        existing_queue = session.get(JobQueue, queue_id)
        if existing_queue is None:
            session.add(
                JobQueue(
                    queue_id=queue_id,
                    project_id=project_id,
                    queue_name=queue_id,
                    queue_name_lower=queue_id,
                    queue_type=QueueType.FIFO.value,
                    concurrency_limit=1,
                    is_system=True,
                    is_paused=False,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        session.commit()


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    instance_id = instance_id or f"f1-back-inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="f1-back-agent",
                agent_dir="/tmp/f1-back-agent",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_active_message_job(
    engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
) -> JobItem:
    job_id = job_id or f"f1-back-job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="f1-back-agent",
            agent_dir="/tmp/f1-back-agent",
            message="F1 backstop writer PG trigger interplay",
            source="api",
            project_id="f1-back-proj",
            queue_id="f1-back-queue",
            priority=5,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            instance_id=instance_id,
            job_type="message",
            retry_count=0,
            version=1,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
    return job


def _seed_terminal_task(
    engine,
    *,
    work_id: str,
    instance_id: str | None = None,
    status: str = TaskStatus.COMPLETED.value,
) -> Task:
    """Seed a Task in a terminal status. ``work_id`` == ``job_id``
    so ``TaskRepository.get_by_work_id`` finds it via the message-
    mirror linkage (Fix A linkage contract: ``JobItem.job_id`` ==
    ``Task.work_id``)."""
    with Session(engine) as session:
        task = Task(
            work_id=work_id,
            instance_id=instance_id,
            task_type="process_message",
            status=status,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
    return task


def _acquire_lock_for_active_message_job(
    engine,
    *,
    instance_id: str,
    job_id: str,
) -> JobLock:
    """Insert a lock while the linked JobItem is still ACTIVE so
    ``trg_job_locks_active_guard`` lets the lock INSERT pass at
    COMMIT (the trigger requires an admission_state='active'
    JobItem)."""
    lock_id = f"f1-back-lock-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        lock = JobLock(
            lock_id=lock_id,
            project_id="f1-back-proj",
            queue_id="f1-back-queue",
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=0,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(lock)
        session.commit()
        session.refresh(lock)
    return lock


def test_f1_backstop_terminal_task_mirror_releases_lock_under_prod_triggers(
    pg_engine,
) -> None:
    """F1 backstop: ACTIVE message mirror + terminal linked Task +
    lock → backstop reconciles AND releases the lock under the PROD
    deferred constraint triggers.

    Pinning contract:

      * Seed ACTIVE message JobItem + COMPLETED Task (work_id ==
        job_id) + lock (inserted while job is active).
      * Call ``reconcile_terminal_message_mirrors`` — the scan
        finds the mirror (job_type='message', admission_state in
        {queued, active, paused}, deleted_at IS NULL); the Task
        is terminal so the candidate is processed.
      * The per-row UPDATE transitions ``active → done`` (PROD
        trigger outer IF is FALSE because NEW.admission_state=
        'done') AND the lock DELETE rides the same
        ``session.commit()`` (PROD trigger on job_locks fires
        only on INSERT/UPDATE, so the DELETE is a no-op).
      * Post-conditions: JobItem is ``done`` with
        ``terminal_reason='completed'``; lock count for the
        instance is ZERO; the backstop returns 1 reconciled
        snapshot.
    """
    instance_id = _seed_instance(pg_engine)
    _ensure_project_and_queue(pg_engine)
    job = _seed_active_message_job(pg_engine, instance_id=instance_id)
    _seed_terminal_task(
        pg_engine, work_id=job.job_id, instance_id=instance_id,
        status=TaskStatus.COMPLETED.value,
    )
    _acquire_lock_for_active_message_job(
        pg_engine, instance_id=instance_id, job_id=job.job_id,
    )

    # Pre-condition: lock is held before the backstop runs.
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 1

    job_repo = JobRepository(pg_engine)
    task_repo = TaskRepository(pg_engine)
    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert len(reaped) == 1, (
        "backstop must reconcile exactly 1 mirror (the seeded "
        "ACTIVE message mirror with a terminal linked Task)"
    )
    assert reaped[0].job_id == job.job_id
    assert reaped[0].admission_state == AdmissionState.DONE.value
    assert reaped[0].terminal_reason == "completed"

    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 0, (
            "F1 backstop fix: lock MUST be released in the same per-row "
            "commit as the mirror transition. The lock DELETE is a no-op "
            "for trg_job_locks_active_guard (which fires only on "
            "INSERT/UPDATE), so it must commit cleanly under the PROD "
            "triggers."
        )


def test_f1_backstop_paused_instance_terminal_task_releases_lock(
    pg_engine,
) -> None:
    """F1 backstop on a paused instance: the 4h+ wedge shape from the
    2026-09-14 prod incident.

    A paused instance + ACTIVE JobItem + CANCELLED linked Task +
    lock. The backstop is the recovery seam the F1 fix added; under
    the PROD triggers it must still reconcile + release the lock.
    """
    instance_id = _seed_instance(
        pg_engine, status=InstanceStatus.PAUSED.value,
    )
    _ensure_project_and_queue(pg_engine)
    job = _seed_active_message_job(pg_engine, instance_id=instance_id)
    _seed_terminal_task(
        pg_engine, work_id=job.job_id, instance_id=instance_id,
        status=TaskStatus.CANCELLED.value,
    )
    _acquire_lock_for_active_message_job(
        pg_engine, instance_id=instance_id, job_id=job.job_id,
    )

    job_repo = JobRepository(pg_engine)
    task_repo = TaskRepository(pg_engine)
    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert len(reaped) == 1
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 0, (
            "F4 alignment: paused instance + finalized mirror → lock "
            "released by the backstop under the PROD triggers."
        )


def test_f1_backstop_live_task_mirror_does_not_release_lock(pg_engine) -> None:
    """Negative: a mirror whose linked Task is STILL live must NOT be
    reconciled, and the lock must stay.

    The backstop is guarded by terminal task status — a RUNNING
    Task leaves the JobItem alone, the lock alone, and the
    ``job_locks_active_guard`` trigger is satisfied because the
    job remains ``admission_state='active'``.
    """
    instance_id = _seed_instance(pg_engine)
    _ensure_project_and_queue(pg_engine)
    job = _seed_active_message_job(pg_engine, instance_id=instance_id)
    _seed_terminal_task(
        pg_engine, work_id=job.job_id, instance_id=instance_id,
        status=TaskStatus.RUNNING.value,
    )
    _acquire_lock_for_active_message_job(
        pg_engine, instance_id=instance_id, job_id=job.job_id,
    )

    job_repo = JobRepository(pg_engine)
    task_repo = TaskRepository(pg_engine)
    reaped = job_repo.reconcile_terminal_message_mirrors(
        task_repository=task_repo,
    )

    assert reaped == [], (
        "backstop must skip mirrors whose linked Task is still live"
    )
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 1, (
            "live-Task mirror: the lock must stay — the backstop did "
            "not process this row, so the lock release never fires"
        )
