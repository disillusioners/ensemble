"""PostgreSQL coverage for W1 job-scoped lock release.

Branch ``feature/fix-joblock-release-scope`` (@ e9fe08c5). W1 narrows
the F1/F5 same-commit lock release from INSTANCE scope to JOB scope at
the THREE repository sites in
``daemon/repositories/job_queue/repository.py``:

  1. ``finalize_mirror_job_at_completion``   (inline writer,  ~:2284)
  2. ``reconcile_terminal_message_mirrors``  (backstop,       ~:2548)
  3. ``force_finalize_orphan``               (orphan reaper,  ~:4087)

The contract pinned here against REAL PostgreSQL, under the PROD-shape
deferred constraint triggers (verbatim ``daemon/manager.py:5536-5541``
DDL — the narrower body including the ``job_type != 'message'``
conjunct): finalizing/reaping job A must release ONLY job A's lock. A
sibling job B's lock on the SAME instance MUST survive. A pre-W1
instance-scoped ``DELETE ... WHERE instance_id = :iid`` would drop both
locks — on PG that is the CRITICAL over-release finding this module
exists to catch.

The 4th W1 site (``job_feedback_observer.py:_finalize_job_db_sync``,
``job_id is None`` releases nothing) is NOT replicated here: wiring it
requires the full observer harness (``EventBus`` + ``JobQueueService``
+ real ``instance_manager`` + ``WriteGuardSession``). It stays covered
by the SQLite acceptance suite — recorded as a residual gap.

The ORM seeding needs the ``projects`` + ``job_queues`` FK parents
BEFORE ``JobItem`` inserts (same recipe as
``test_f1_inline_writer_pg.py``).

Run::

    uv run python -m pytest tests/postgres/test_joblock_release_scope_w1_pg.py \\
        -m postgres --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the
entire module cleanly when PostgreSQL is not reachable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all``
# (mirrors ``test_f1_inline_writer_pg.py``).
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

_PROJ = "w1-scope-proj"
_QUEUE = "w1-scope-queue"


# =============================================================================
# PROD-shape trigger DDL — verbatim from daemon/manager.py:5536-5541
# (copied from tests/postgres/test_f1_inline_writer_pg.py, NOT imported:
# test modules here intentionally duplicate the DDL so each pins the
# exact PROD shape it validates under).
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
    """Install the PROD-shape deferred constraint trigger suite."""
    with pg_engine.begin() as conn:
        for stmt in PROD_TRIGGER_DDL:
            conn.execute(text(stmt))
    yield


# =============================================================================
# Seeding helpers
# =============================================================================


def _ensure_project_and_queue(engine) -> None:
    """Seed the FK targets ``JobItem.project_id`` / ``.queue_id`` need."""
    with Session(engine) as session:
        if session.get(Project, _PROJ) is None:
            session.add(
                Project(
                    project_id=_PROJ,
                    name=_PROJ,
                    project_type="system",
                    status="active",
                    job_queue_paused=False,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        if session.get(JobQueue, _QUEUE) is None:
            session.add(
                JobQueue(
                    queue_id=_QUEUE,
                    project_id=_PROJ,
                    queue_name=_QUEUE,
                    queue_name_lower=_QUEUE,
                    queue_type=QueueType.FIFO.value,
                    concurrency_limit=2,
                    is_system=True,
                    is_paused=False,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        session.commit()


def _seed_instance(engine, *, status: str = InstanceStatus.RUNNING.value) -> str:
    instance_id = f"w1-scope-inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="w1-scope-agent",
                agent_dir="/tmp/w1-scope-agent",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_active_message_job(engine, *, instance_id: str, tag: str) -> JobItem:
    """Seed an ``admission_state='active'``, ``job_type='message'`` JobItem."""
    job_id = f"w1-scope-job-{tag}-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="w1-scope-agent",
            agent_dir="/tmp/w1-scope-agent",
            message=f"W1 job-scoped release sibling test ({tag})",
            source="api",
            project_id=_PROJ,
            queue_id=_QUEUE,
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


def _acquire_lock(
    engine, *, instance_id: str, job_id: str, tag: str, slot: int = 0,
) -> JobLock:
    """Insert a lock row while the linked JobItem is still ACTIVE —
    ``trg_job_locks_active_guard`` requires an active JobItem at COMMIT
    (fires on INSERT/UPDATE only). ``slot`` must differ per sibling:
    ``(project_id, queue_id, lock_slot)`` is UNIQUE on ``job_locks``."""
    lock_id = f"w1-scope-lock-{tag}-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        lock = JobLock(
            lock_id=lock_id,
            project_id=_PROJ,
            queue_id=_QUEUE,
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=slot,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(lock)
        session.commit()
        session.refresh(lock)
    return lock


def _seed_two_sibling_jobs_with_locks(
    engine, *, instance_status: str = InstanceStatus.RUNNING.value,
) -> tuple[str, JobItem, JobItem]:
    """One instance + two ACTIVE message jobs, each holding its OWN lock.

    Returns ``(instance_id, job_a, job_b)``. Pre-condition asserted by
    callers: exactly 2 locks on the instance before the writer runs.
    """
    _ensure_project_and_queue(engine)
    instance_id = _seed_instance(engine, status=instance_status)
    job_a = _seed_active_message_job(engine, instance_id=instance_id, tag="a")
    job_b = _seed_active_message_job(engine, instance_id=instance_id, tag="b")
    _acquire_lock(engine, instance_id=instance_id, job_id=job_a.job_id, tag="a", slot=0)
    _acquire_lock(engine, instance_id=instance_id, job_id=job_b.job_id, tag="b", slot=1)
    return instance_id, job_a, job_b


def _lock_rows(engine, instance_id: str) -> list[str]:
    """Return the sorted ``job_id``s currently holding locks."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT job_id FROM job_locks WHERE instance_id = :iid "
                "ORDER BY job_id"
            ),
            {"iid": instance_id},
        ).fetchall()
    return sorted(r[0] for r in rows)


def _job_state(engine, job_id: str) -> tuple[str | None, str | None]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT admission_state, terminal_reason "
                "FROM job_queue_items WHERE job_id = :job_id"
            ),
            {"job_id": job_id},
        ).fetchone()
    return (row[0], row[1]) if row else (None, None)


# =============================================================================
# W1 contract: the finalized job's OWN lock is released; the sibling's
# lock on the SAME instance survives.
# =============================================================================


def test_sibling_survives_inline_writer_finalize_pg(pg_engine) -> None:
    """Site 1 — ``finalize_mirror_job_at_completion`` (inline writer).

    Finalizing job A must delete ONLY job A's lock (job-scoped W1
    release). Under a pre-W1 instance-scoped DELETE, job B's lock would
    vanish with it — a CRITICAL over-release on PG.
    """
    instance_id, job_a, job_b = _seed_two_sibling_jobs_with_locks(pg_engine)

    # Pre-condition: both locks held.
    assert _lock_rows(pg_engine, instance_id) == sorted(
        [job_a.job_id, job_b.job_id]
    ), "PRECONDITION: both sibling locks must be held before finalize"

    repo = JobRepository(pg_engine)
    finalized = repo.finalize_mirror_job_at_completion(job_a.job_id)

    assert finalized is not None, (
        "inline writer must transition the message JobItem under the "
        "PROD triggers (done-transition makes the outer IF FALSE)"
    )
    assert finalized.admission_state == AdmissionState.DONE.value
    assert finalized.terminal_reason == "completed"

    # W1 core assertion: job A's own lock GONE, sibling job B's lock
    # STILL PRESENT, same instance.
    assert _lock_rows(pg_engine, instance_id) == [job_b.job_id], (
        "W1 job-scoped release: ONLY job A's lock may be released by "
        "finalize_mirror_job_at_completion. A missing job B row = "
        "CRITICAL over-release (instance-scoped DELETE regressed)."
    )
    assert _job_state(pg_engine, job_a.job_id) == (
        AdmissionState.DONE.value, "completed",
    )
    # The sibling job itself must be untouched — still active, still live.
    assert _job_state(pg_engine, job_b.job_id) == (
        AdmissionState.ACTIVE.value, None,
    )


def test_sibling_survives_backstop_reconcile_pg(pg_engine) -> None:
    """Site 2 — ``reconcile_terminal_message_mirrors`` (backstop).

    The scan sees BOTH siblings (both active message mirrors); only
    job A has a terminal Task behind it, so ONLY job A is reconciled —
    and ONLY job A's lock may be released. Job B (no Task) must be
    skipped entirely, its lock intact.
    """
    instance_id, job_a, job_b = _seed_two_sibling_jobs_with_locks(pg_engine)

    # Terminal Task driving job A only (Fix A linkage: Task.work_id
    # == JobItem.job_id). Job B has NO Task → deliberately skipped.
    with Session(pg_engine) as session:
        session.add(
            Task(
                work_id=job_a.job_id,
                instance_id=instance_id,
                status=TaskStatus.COMPLETED.value,
            )
        )
        session.commit()

    assert _lock_rows(pg_engine, instance_id) == sorted(
        [job_a.job_id, job_b.job_id]
    ), "PRECONDITION: both sibling locks must be held before reconcile"

    repo = JobRepository(pg_engine)
    reconciled = repo.reconcile_terminal_message_mirrors(
        task_repository=TaskRepository(pg_engine),
    )

    assert [r.job_id for r in reconciled] == [job_a.job_id], (
        "backstop must reconcile ONLY the job whose driving Task is "
        "terminal — the Task-less sibling must be skipped"
    )
    assert _lock_rows(pg_engine, instance_id) == [job_b.job_id], (
        "W1 job-scoped release: ONLY job A's lock may be released by "
        "reconcile_terminal_message_mirrors. A missing job B row = "
        "CRITICAL over-release (instance-scoped DELETE regressed)."
    )
    assert _job_state(pg_engine, job_a.job_id) == (
        AdmissionState.DONE.value, "completed",
    )
    assert _job_state(pg_engine, job_b.job_id) == (
        AdmissionState.ACTIVE.value, None,
    )


def test_orphan_reap_releases_only_own_lock_pg(pg_engine) -> None:
    """Site 3 — ``force_finalize_orphan`` (orphan reaper).

    Orphan shape: instance already terminal ('completed'), job A still
    ACTIVE with its lock. Reaping job A must release ONLY job A's lock;
    the sibling job B's lock on the same instance MUST survive (job B
    is itself an orphan-in-waiting — reaping A must not consume B).
    """
    instance_id, job_a, job_b = _seed_two_sibling_jobs_with_locks(
        pg_engine, instance_status=InstanceStatus.COMPLETED.value,
    )

    assert _lock_rows(pg_engine, instance_id) == sorted(
        [job_a.job_id, job_b.job_id]
    ), "PRECONDITION: both sibling locks must be held before reap"

    repo = JobRepository(pg_engine)
    reaped = repo.force_finalize_orphan(
        job_a.job_id, terminal_reason="orphaned",
    )

    assert reaped is not None, (
        "force_finalize_orphan must reap the ACTIVE orphan row"
    )
    assert reaped.admission_state == AdmissionState.DONE.value
    assert reaped.terminal_reason == "orphaned"

    assert _lock_rows(pg_engine, instance_id) == [job_b.job_id], (
        "W1 job-scoped release: ONLY the reaped orphan job A's lock may "
        "be deleted by force_finalize_orphan. A missing job B row = "
        "CRITICAL over-release (instance-scoped DELETE regressed)."
    )
    assert _job_state(pg_engine, job_a.job_id) == (
        AdmissionState.DONE.value, "orphaned",
    )
    assert _job_state(pg_engine, job_b.job_id) == (
        AdmissionState.ACTIVE.value, None,
    )

