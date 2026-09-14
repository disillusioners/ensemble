"""PostgreSQL coverage for F1 inline writer (``finalize_mirror_job_at_completion``).

Gap-closing test for the 2026-09-14 joblock-leak fix (branch
``feature/fix-joblock-leak``). The SQLite acceptance suite
(``tests/unit/job_queue/test_joblock_leak_fixes.py``) covers the
inline writer but SQLite hosts no deferred constraint triggers.
This module pins the same contract against the REAL PostgreSQL
trigger suite installed by ``EnsembleManager._ensure_postgres_columns``
(``daemon/manager.py:5536-5541``).

Why the trigger shape matters here
----------------------------------

The PROD trigger function body (manager.py:5536) is::

    IF NEW.admission_state = 'active' AND NEW.job_type != 'message' THEN
        IF NOT EXISTS (SELECT 1 FROM job_locks WHERE instance_id = NEW.instance_id) THEN
            RAISE EXCEPTION ...
        END IF;
    END IF;

i.e. the outer IF guards on BOTH ``admission_state='active'`` AND
``job_type != 'message'``. The Fix B inline writer transitions
``active → done`` for a ``message``-type JobItem, so at COMMIT time
``NEW.admission_state='done'`` — the IF is FALSE, the trigger is a
no-op, the UPDATE commits cleanly. The same-commit lock DELETE then
fires ``trg_job_locks_active_guard`` (AFTER INSERT/UPDATE only,
NOT DELETE), which is also a no-op for a DELETE.

Both ``PHASE2_INSTALL_STATEMENTS`` from
``tests/postgres/test_jq_proxy_phase2_constraints.py`` and the
inline DDL in manager.py produce triggers with the SAME names; the
test-side body is WIDER (drops the ``job_type != 'message'``
conjunct — known J2 WARNING divergence). For prod-shape validation
this module installs the PROD DDL verbatim so the deferred
constraint fires EXACTLY as the production database will fire it.

Run with::

    .venv/bin/python -m pytest tests/postgres/test_f1_inline_writer_pg.py \\
        -v -m postgres --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips
the entire module cleanly when PostgreSQL is not reachable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all``
# mirrors the harness in ``tests/job_queue/test_orphan_reaper.py``.
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


pytestmark = pytest.mark.postgres


# =============================================================================
# PROD-shape trigger DDL — verbatim from daemon/manager.py:5536-5541
# =============================================================================
#
# The narrower PROD trigger (with the ``job_type != 'message'``
# conjunct) is what the production database runs. Installing it here
# means the trigger fires EXACTLY as it does in prod, including the
# ``message`` carve-out the test-side PHASE2_INSTALL_STATEMENTS
# lacks. Both DDL bodies share trigger NAMES
# (``trg_job_queue_items_active_lock_guard``,
# ``trg_job_locks_active_guard``); the test-side version is wider.
#
# Critical: do NOT import PHASE2_INSTALL_STATEMENTS here — that would
# install the WIDER trigger and silently weaken the assertion. The
# whole point of this module is to validate under the PROD shape.
# =============================================================================
PROD_TRIGGER_DDL: tuple[str, ...] = (
    # Trigger function 1: PROD body — outer IF includes the
    # ``job_type != 'message'`` conjunct (the test-side DDL drops
    # this and is therefore wider / less faithful).
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
    # Trigger function 2: identical to the test-side body
    # (no ``job_type`` guard exists in either shape for the
    # ``lock ⇒ active`` direction — only the job_queue_items guard
    # is widened on the test side).
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
    # Idempotent install: DROP + CREATE CONSTRAINT TRIGGER (no
    # OR REPLACE form exists for CONSTRAINT TRIGGER).
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
    """Install the PROD-shape constraint trigger suite.

    The DDL above is taken verbatim from
    ``daemon/manager.py:5536-5541`` (``_ensure_postgres_columns``
    block, lines starting at ``# Trigger function 1``). Naming the
    fixture clearly distinguishes this module from
    ``test_orphan_reaper_pg.py`` (which imports
    ``PHASE2_INSTALL_STATEMENTS`` and therefore installs the wider
    test-side body).
    """
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
    project_id: str = "f1-inline-proj",
    queue_id: str = "f1-inline-queue",
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
    instance_id = instance_id or f"f1-inline-inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="f1-inline-agent",
                agent_dir="/tmp/f1-inline-agent",
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
    """Seed an ``admission_state='active'``, ``job_type='message'`` JobItem.

    The PROD trigger fires only when ``job_type != 'message'``,
    so message-type active jobs do NOT require a lock row at the
    UPDATE seam. To exercise the lock-release path, this helper
    pairs with ``_acquire_lock_for_active_message_job`` which
    inserts the lock WHILE the job is still active (so the
    ``trg_job_locks_active_guard`` trigger lets the lock INSERT
    pass at the COMMIT boundary).
    """
    job_id = job_id or f"f1-inline-job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="f1-inline-agent",
            agent_dir="/tmp/f1-inline-agent",
            message="F1 inline writer PG trigger interplay",
            source="api",
            project_id="f1-inline-proj",
            queue_id="f1-inline-queue",
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


def _acquire_lock_for_active_message_job(
    engine,
    *,
    instance_id: str,
    job_id: str,
) -> JobLock:
    """Insert a lock row while the linked JobItem is still ACTIVE.

    The trigger ``trg_job_locks_active_guard`` enforces that any
    ``job_locks`` row must point at an ``admission_state='active'``
    JobItem. Seeding the lock AFTER the JobItem is already 'done'
    would RAISE at COMMIT; this helper mirrors the natural
    acquire-while-active flow that ``JobLockManager.acquire_queue_lock``
    follows in production.
    """
    lock_id = f"f1-inline-lock-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        lock = JobLock(
            lock_id=lock_id,
            project_id="f1-inline-proj",
            queue_id="f1-inline-queue",
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=0,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(lock)
        session.commit()
        session.refresh(lock)
    return lock


def test_f1_inline_writer_active_message_releases_lock_under_prod_triggers(
    pg_engine,
) -> None:
    """F1 inline writer: ``active → done`` UPDATE + lock DELETE
    commit cleanly under the PROD-shape deferred constraint triggers.

    Pinning contract:

      * The producer seeds an ACTIVE message-type JobItem +
        matching JobLock (the lock INSERT passes the
        ``trg_job_locks_active_guard`` because the job is still
        active at COMMIT time).
      * ``JobRepository.finalize_mirror_job_at_completion(job_id)``
        transitions ``active → done`` (the trigger's outer IF
        becomes FALSE because ``NEW.admission_state='done'`` —
        the trigger is a NO-OP for the UPDATE) and DELETEs the
        lock in the same ``session.commit()`` (the
        ``trg_job_locks_active_guard`` fires only on INSERT/UPDATE,
        so the DELETE is also a no-op for the trigger).
      * Post-conditions: JobItem is ``done`` with
        ``terminal_reason='completed'``; lock count for the
        instance is ZERO. Pre-fix this would be 1 (lock orphan).
    """
    instance_id = _seed_instance(pg_engine)
    _ensure_project_and_queue(pg_engine)
    job = _seed_active_message_job(
        pg_engine, instance_id=instance_id,
    )
    _acquire_lock_for_active_message_job(
        pg_engine, instance_id=instance_id, job_id=job.job_id,
    )

    # Pre-condition: lock is held before the fix runs.
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 1, (
            "PRECONDITION: instance must hold a job_locks row before "
            "finalize_mirror_job_at_completion runs"
        )

    repo = JobRepository(pg_engine)
    finalized = repo.finalize_mirror_job_at_completion(job.job_id)

    # Post-condition: JobItem transitioned AND lock released under the
    # PROD-shape deferred constraint triggers at COMMIT.
    assert finalized is not None, (
        "finalize_mirror_job_at_completion returned None — under the "
        "PROD triggers the UPDATE must commit (the outer IF on "
        "'admission_state=active AND job_type!=message' is FALSE for "
        "the done transition of a message job)"
    )
    assert finalized.admission_state == AdmissionState.DONE.value
    assert finalized.terminal_reason == "completed"

    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 0, (
            "F1 fix: lock MUST be released in the same commit as the "
            "JobItem transition under the PROD triggers. The DELETE on "
            "job_locks is a no-op for trg_job_locks_active_guard (which "
            "fires only on INSERT/UPDATE), so it must commit cleanly."
        )
        # Re-read the JobItem to confirm terminal_reason persisted.
        job_after = conn.execute(
            text(
                "SELECT admission_state, terminal_reason "
                "FROM job_queue_items WHERE job_id = :job_id"
            ),
            {"job_id": job.job_id},
        ).fetchone()
    assert job_after is not None
    assert job_after[0] == AdmissionState.DONE.value
    assert job_after[1] == "completed"


def test_f1_inline_writer_paused_instance_releases_lock_under_prod_triggers(
    pg_engine,
) -> None:
    """F1 inline writer on a paused instance: the 2026-09-14 prod wedge.

    The exact 8h45m orphan shape: instance parked PAUSED + active
    JobItem + lock for that instance. ``finalize_mirror_job_at_completion``
    must transition the JobItem AND release the lock under the
    PROD triggers. The instance-status pause is irrelevant to the
    trigger (the trigger inspects only the new row's
    ``admission_state`` / ``job_type`` and the
    ``job_locks`` matching row's existence).
    """
    instance_id = _seed_instance(
        pg_engine, status=InstanceStatus.PAUSED.value,
    )
    _ensure_project_and_queue(pg_engine)
    job = _seed_active_message_job(
        pg_engine, instance_id=instance_id,
    )
    _acquire_lock_for_active_message_job(
        pg_engine, instance_id=instance_id, job_id=job.job_id,
    )

    repo = JobRepository(pg_engine)
    finalized = repo.finalize_mirror_job_at_completion(job.job_id)

    assert finalized is not None
    assert finalized.admission_state == AdmissionState.DONE.value
    with pg_engine.connect() as conn:
        assert _lock_count(conn, instance_id) == 0, (
            "F1 fix on a paused instance: the lock MUST be released in "
            "the same commit as the JobItem transition. Pre-fix this "
            "was the prod 8h45m orphan — the fix's atomic same-commit "
            "release closes the gap regardless of instance status."
        )
