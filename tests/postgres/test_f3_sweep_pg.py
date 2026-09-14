"""PostgreSQL coverage for F3 ``JobLockSweepService`` (``sweep_once``).

Gap-closing test for the 2026-09-14 joblock-leak fix. The SQLite
acceptance suite
(``tests/unit/job_queue/test_joblock_leak_fixes.py::TestJobLockSweepReclaimsTerminalJobLocks``)
covers the sweep but SQLite hosts no deferred constraint triggers;
this module pins the sweep against the REAL PostgreSQL trigger suite.

Trigger interplay for the sweep
--------------------------------

``JobLockSweepService.sweep_once`` delegates to
``LockRepository.clear_terminal_job_locks``, which issues a single
bulk ``DELETE FROM job_locks WHERE job_id NOT IN (SELECT job_id FROM
job_queue_items WHERE admission_state IN ('queued','active') AND
deleted_at IS NULL)``.

Critical for the trigger interplay:

  * ``trg_job_queue_items_active_lock_guard`` is
    AFTER INSERT/UPDATE on ``job_queue_items`` — the sweep touches
    no ``job_queue_items`` rows, so this trigger does NOT fire.
  * ``trg_job_locks_active_guard`` is AFTER INSERT/UPDATE on
    ``job_locks`` — the sweep DELETEs rows on ``job_locks`` and
    does NOT fire this trigger (DELETE is not in the trigger's
    declared event set).

Both triggers therefore are NO-OPs for the sweep path; the DELETE
commits cleanly even when the lock row would have violated the
``lock ⇒ active`` invariant at INSERT-time. The positive test
here models the STALE state (lock inserted while job was active,
then the job transitioned to ``done``) faithfully — exactly the
shape ``cleanup_terminal_job_locks`` is designed to reclaim.

Run with::

    .venv/bin/python -m pytest tests/postgres/test_f3_sweep_pg.py \\
        -v -m postgres --override-ini="addopts="
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
    JobQueue,
    QueueType,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.project.models import Project
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_lock_sweep import JobLockSweepService


pytestmark = pytest.mark.postgres


# =============================================================================
# PROD-shape trigger DDL — verbatim from daemon/manager.py:5536-5541
# =============================================================================
# Same PROD body as the F1 modules. See test_f1_inline_writer_pg.py
# for the rationale and the wider-test-side divergence note (J2 WARNING).
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
    project_id: str = "f3-sweep-proj",
    queue_id: str = "f3-sweep-queue",
) -> None:
    """Seed the FK targets ``JobItem.queue_id`` and ``.project_id``
    point at. Required because the ORM-level INSERT honors the
    FK constraints."""
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
    status: str = InstanceStatus.PAUSED.value,
) -> str:
    """Default status is PAUSED — mirrors the F4 contract that the
    sweep must reclaim locks for terminal jobs EVEN when the parent
    instance is parked."""
    instance_id = instance_id or f"f3-sweep-inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="f3-sweep-agent",
                agent_dir="/tmp/f3-sweep-agent",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_terminal_job_with_lock(
    engine,
    *,
    instance_id: str,
    job_id: str | None = None,
    admission_state: str = AdmissionState.DONE.value,
    job_type: str = "message",
) -> JobItem:
    """Seed a JobItem + a JobLock in the STALE shape the sweep
    targets.

    Faithful STALE state modeling (per the test plan): the lock
    was acquired while the job was ACTIVE (so
    ``trg_job_locks_active_guard`` let the lock INSERT pass), then
    the job transitioned to a terminal state WITHOUT releasing
    the lock atomically (the very gap F1 closes inline; the
    sweep is the F3 backstop that reclaims stragglers).

    Note: we cannot seed the lock with the job ALREADY in a
    terminal state — ``trg_job_locks_active_guard`` would RAISE
    at COMMIT (no matching active JobItem). The two-phase seed
    (insert lock while job is active, then transition job to
    terminal) faithfully models the wedge shape.
    """
    job_id = job_id or f"f3-sweep-job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        # Phase 1: ACTIVE job + lock (so the trigger lets the lock pass).
        job = JobItem(
            job_id=job_id,
            agent_id="f3-sweep-agent",
            agent_dir="/tmp/f3-sweep-agent",
            message="F3 sweep PG trigger interplay",
            source="api",
            project_id="f3-sweep-proj",
            queue_id="f3-sweep-queue",
            priority=5,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            instance_id=instance_id,
            job_type=job_type,
            retry_count=0,
            version=1,
        )
        lock = JobLock(
            lock_id=f"f3-sweep-lock-{uuid.uuid4().hex[:8]}",
            project_id="f3-sweep-proj",
            queue_id="f3-sweep-queue",
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=0,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(job)
        session.add(lock)
        session.commit()
        session.refresh(job)

        # Phase 2: transition the job to terminal WITHOUT releasing
        # the lock (raw UPDATE; the F1 atomic same-commit release is
        # the inline fix; here we simulate the wedge it closes).
        # Use ``JobRepository.finalize_mirror_job_at_completion`` to
        # transition ``active → done`` for a message-type job AND
        # release the lock atomically — but then INSERT a new lock
        # row directly so the wedge shape lands. Alternatively, we
        # can do a raw UPDATE that bypasses F1 entirely.
        # Simplest faithful modeling: raw UPDATE to 'done' (no lock
        # release) so the lock is now orphaned relative to the job.
        if admission_state != AdmissionState.ACTIVE.value:
            row = session.get(JobItem, job_id)
            row.admission_state = admission_state
            row.terminal_reason = "completed"
            session.add(row)
            session.commit()
            session.refresh(row)
    return job


def _seed_active_job_with_lock(
    engine,
    *,
    instance_id: str,
    job_id: str | None = None,
    job_type: str = "message",
) -> JobItem:
    """Seed an ACTIVE job + matching lock (the negative-case shape).

    The sweep's NOT-IN subquery excludes this job, so the lock
    MUST survive the sweep.
    """
    job_id = job_id or f"f3-sweep-active-job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="f3-sweep-agent",
            agent_dir="/tmp/f3-sweep-agent",
            message="F3 sweep negative (active job survives)",
            source="api",
            project_id="f3-sweep-proj",
            queue_id="f3-sweep-queue",
            priority=5,
            admission_state=AdmissionState.ACTIVE.value,
            created_at=datetime.now(timezone.utc).isoformat(),
            instance_id=instance_id,
            job_type=job_type,
            retry_count=0,
            version=1,
        )
        lock = JobLock(
            lock_id=f"f3-sweep-active-lock-{uuid.uuid4().hex[:8]}",
            project_id="f3-sweep-proj",
            queue_id="f3-sweep-queue",
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=0,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(job)
        session.add(lock)
        session.commit()
        session.refresh(job)
    return job


def test_f3_sweep_releases_paused_instance_terminal_lock_under_prod_triggers(
    pg_engine,
) -> None:
    """F3 + F4 alignment: paused instance + DONE job + lock →
    sweep reclaims under the PROD deferred constraint triggers.

    The trigger interplay is the headline assertion: the sweep
    issues a single bulk DELETE on ``job_locks`` — the
    ``trg_job_locks_active_guard`` trigger does NOT fire (it's
    AFTER INSERT/UPDATE only), so the DELETE commits cleanly
    even though the lock row would have violated the
    ``lock ⇒ active`` invariant at INSERT-time. This pins the
    same contract on PG that the SQLite acceptance suite pins
    on the file-backed SQLite engine.
    """
    instance_id = _seed_instance(
        pg_engine, status=InstanceStatus.PAUSED.value,
    )
    _ensure_project_and_queue(pg_engine)
    _seed_terminal_job_with_lock(
        pg_engine,
        instance_id=instance_id,
        admission_state=AdmissionState.DONE.value,
    )

    # Pre-condition: lock is held for the terminal job.
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 1

    # Wire a real ``JobLockSweepService`` against the PG engine.
    lock_repo = LockRepository(pg_engine)
    lock_manager = JobLockManager(lock_repo=lock_repo)
    sweep = JobLockSweepService(
        job_lock_manager=lock_manager,
        interval_seconds=1,  # unused — sweep_once is called directly
    )

    cleared = asyncio.run(sweep.sweep_once())

    assert cleared == 1, (
        f"sweep_once must delete exactly the 1 orphaned lock row, "
        f"got {cleared}"
    )
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 0, (
            "F3 sweep: lock for terminal job MUST be reclaimed under "
            "the PROD triggers. The DELETE is a no-op for "
            "trg_job_locks_active_guard (AFTER INSERT/UPDATE only), "
            "so the bulk DELETE commits cleanly."
        )


def test_f3_sweep_active_job_lock_survives_under_prod_triggers(pg_engine) -> None:
    """Negative: ACTIVE job + lock → sweep MUST NOT delete the lock.

    The sweep's ``WHERE job_id NOT IN (active job ids)`` predicate
    excludes locks whose job is still ``admission_state IN
    ('queued','active') AND deleted_at IS NULL``. A live lock on
    an active job is therefore not touched. The
    ``trg_job_locks_active_guard`` trigger is satisfied at lock
    INSERT-time because the job is active at COMMIT; the sweep's
    DELETE does not change that and does not fire the trigger.
    """
    instance_id = _seed_instance(
        pg_engine, status=InstanceStatus.RUNNING.value,
    )
    _ensure_project_and_queue(pg_engine)
    _seed_active_job_with_lock(pg_engine, instance_id=instance_id)

    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 1

    lock_repo = LockRepository(pg_engine)
    lock_manager = JobLockManager(lock_repo=lock_repo)
    sweep = JobLockSweepService(
        job_lock_manager=lock_manager,
        interval_seconds=1,
    )

    cleared = asyncio.run(sweep.sweep_once())

    assert cleared == 0, (
        "sweep_once MUST NOT delete locks for active jobs (the NOT IN "
        "subquery excludes them)"
    )
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 1, (
            "active-job lock must survive the sweep — the trigger "
            "interplay does not change this contract (DELETE is not "
            "in the trigger's declared event set)"
        )


def test_f3_sweep_releases_dead_job_lock_under_prod_triggers(pg_engine) -> None:
    """F3 + dead-state: DEAD job + lock → sweep reclaims.

    ``AdmissionState.DEAD`` (transitioned via the dead-letter
    pathway or a forced terminator) is also a terminal admission
    state. The sweep's NOT-IN subquery excludes ``queued`` and
    ``active`` only; ``done`` and ``dead`` are both terminal and
    both excluded, so the lock is reclaimed.
    """
    instance_id = _seed_instance(
        pg_engine, status=InstanceStatus.ERROR.value,
    )
    _ensure_project_and_queue(pg_engine)
    _seed_terminal_job_with_lock(
        pg_engine,
        instance_id=instance_id,
        admission_state=AdmissionState.DEAD.value,
    )

    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 1

    lock_repo = LockRepository(pg_engine)
    lock_manager = JobLockManager(lock_repo=lock_repo)
    sweep = JobLockSweepService(
        job_lock_manager=lock_manager,
        interval_seconds=1,
    )

    cleared = asyncio.run(sweep.sweep_once())

    assert cleared == 1
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 0
