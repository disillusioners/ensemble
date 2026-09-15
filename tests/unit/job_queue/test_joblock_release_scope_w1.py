"""W1 pins: job-scoped lock release (branch feature/fix-joblock-release-scope).

Council-flagged on merge b5100215: the F1/F5 atomic lock release was
INSTANCE-scoped — finalizing job J of instance X deleted ALL of X's
``job_locks`` rows, including a sibling concurrent job's lock (still
active → lane concurrency limit silently violated, ``active⇒lock``
invariant broken) or a post-revive successor's lock. Bounded
(transient over-admission, not a leak/deadlock) but it contradicts the
repo doctrine documented at
``daemon/services/job_queue_service.py`` (Step 3 lock release: "scope
lock release to the specific ``(project_id, queue_id, job_id)`` triple,
NOT the whole instance").

These pins hold the fix against a real file-backed SQLite engine so
each scenario produces a genuine SQL state, not a mock:

  (a) a sibling job's lock SURVIVES a Fix-B finalize of its
      co-instance job — covered for BOTH the inline writer
      (``finalize_mirror_job_at_completion``) AND the terminal-mirror
      backstop (``reconcile_terminal_message_mirrors``);
  (b) a post-revive successor's lock SURVIVES a late finalize of the
      old job (both writers);
  (c) ``force_finalize_orphan`` (F5) releases ONLY the orphan's own
      lock.

Each pin asserts BOTH directions: the finalized job's OWN lock is gone
(the release still fires — guards against a vacuous pass where the
release stopped working entirely) AND the co-instance job's lock is
still present (the over-release is gone).

Worktree regression proof discipline: these same tests against the
b5100215 base (instance-scoped release) MUST FAIL — at base the
instance-wide DELETE also removes the co-instance lock, so the
"survives" assertion trips. See the exact-failure runbook in
``test_joblock_leak_fixes.py``.

Engine convention (Testing & QC): file-backed SQLite under ``tmp_path``
+ ``NullPool`` + ``journal_mode=WAL`` + ``busy_timeout=10000`` — no
StaticPool, no WriteGuardSession.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.event import listens_for
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all`` —
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue.models import AdmissionState, JobItem, JobLock
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository


# ─────────────────────────────────────────────────────────────────────
# Fixtures — file-backed SQLite engine per test (NullPool + WAL)
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    The conventions recipe: a real file (schema persists across
    NullPool connections), no shared connection, WAL + a generous
    busy timeout. Each test gets its own ``tmp_path`` file.
    """
    db_path = tmp_path / f"w1-release-scope-{uuid.uuid4().hex[:8]}.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def task_repo(engine) -> TaskRepository:
    return TaskRepository(engine)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _seed_instance(
    engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_dir="/tmp",
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_message_job(
    engine,
    *,
    job_id: str | None = None,
    instance_id: str | None = None,
    admission_state: str = AdmissionState.ACTIVE.value,
    project_id: str = "p1",
    queue_id: str = "q1",
) -> JobItem:
    job_id = job_id or f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        job = JobItem(
            job_id=job_id,
            agent_id="test",
            agent_dir="/tmp",
            message="m",
            source="api",
            project_id=project_id,
            queue_id=queue_id,
            priority=1,
            admission_state=admission_state,
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


def _seed_task(
    engine,
    *,
    work_id: str,
    instance_id: str | None = None,
    status: str = TaskStatus.RUNNING.value,
) -> Task:
    """Seed a ``Task`` row with model-default timestamps (explicit ISO
    strings cause a SQLite DATETIME datatype-mismatch error on
    file-backed engines)."""
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


def _acquire_lock(
    engine,
    *,
    instance_id: str,
    job_id: str,
    project_id: str = "p1",
    queue_id: str = "q1",
    lock_slot: int = 0,
) -> JobLock:
    with Session(engine) as session:
        lock = JobLock(
            lock_id=f"lock-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            queue_id=queue_id,
            job_id=job_id,
            instance_id=instance_id,
            lock_slot=lock_slot,
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(lock)
        session.commit()
        session.refresh(lock)
    return lock


def _lock_count_for_job(engine, job_id: str) -> int:
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM job_locks "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            ).scalar()
            or 0
        )


def _job_admission_state(engine, job_id: str) -> str:
    with engine.begin() as conn:
        return (
            conn.execute(
                text(
                    "SELECT admission_state FROM job_queue_items "
                    "WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            ).scalar()
            or ""
        )


def _assert_own_lock_released_and_coinstance_lock_survives(
    engine,
    *,
    finalized_job: JobItem,
    surviving_job: JobItem,
) -> None:
    """Both directions of the W1 pin, shared by every test below."""
    # Direction 1: the finalized job's OWN lock is gone (the release
    # still fires — a vacuous "release stopped working" pass is a
    # failure too).
    assert _lock_count_for_job(engine, finalized_job.job_id) == 0, (
        "W1: the finalized job's OWN lock must still release "
        "same-commit (F1 stays dead)."
    )
    assert (
        _job_admission_state(engine, finalized_job.job_id)
        == AdmissionState.DONE.value
    ), "W1: the finalized job must transition to done."

    # Direction 2: the co-instance job's lock SURVIVES (the
    # over-release is gone).
    assert _lock_count_for_job(engine, surviving_job.job_id) == 1, (
        "W1: the co-instance job's lock MUST survive the finalize — "
        "instance-scoped release deleted it (over-admission past the "
        "lane concurrency limit)."
    )
    assert (
        _job_admission_state(engine, surviving_job.job_id)
        == AdmissionState.ACTIVE.value
    ), "W1: the co-instance job must remain ACTIVE (untouched)."


# ─────────────────────────────────────────────────────────────────────
# (a) Sibling job's lock survives a Fix-B finalize of its co-instance job
# ─────────────────────────────────────────────────────────────────────


class TestSiblingLockSurvivesFinalize:
    """A sibling concurrent job's lock on the SAME instance must
    survive a finalize of the co-instance job. Pre-fix
    (instance-scoped DELETE): the sibling's lock was deleted too →
    ``active`` job with no lock → lane concurrency limit silently
    violated."""

    def test_sibling_lock_survives_inline_writer_finalize(
        self, engine, job_repo
    ):
        """Inline writer (``finalize_mirror_job_at_completion``):
        sibling on a DIFFERENT queue (the doctrine's "locks held by
        sibling jobs (other queues, other jobs)")."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        sibling = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q2",
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id,
                      queue_id="q1", lock_slot=0)
        _acquire_lock(engine, instance_id=instance_id, job_id=sibling.job_id,
                      queue_id="q2", lock_slot=0)

        assert _lock_count_for_job(engine, sibling.job_id) == 1, (
            "PRECONDITION: sibling lock present before finalize"
        )

        finalized = job_repo.finalize_mirror_job_at_completion(job.job_id)

        assert finalized is not None
        _assert_own_lock_released_and_coinstance_lock_survives(
            engine, finalized_job=job, surviving_job=sibling,
        )

    def test_sibling_lock_survives_backstop_reconcile(
        self, engine, job_repo, task_repo
    ):
        """Terminal-mirror backstop
        (``reconcile_terminal_message_mirrors``): the reconciled
        job's sibling (no terminal Task of its own → not a reconcile
        candidate) keeps its lock."""
        instance_id = _seed_instance(engine)
        job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        sibling = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q2",
        )
        _seed_task(
            engine,
            work_id=job.job_id,
            instance_id=instance_id,
            status=TaskStatus.COMPLETED.value,
        )
        # The sibling has NO Task row → the backstop skips it.
        _acquire_lock(engine, instance_id=instance_id, job_id=job.job_id,
                      queue_id="q1", lock_slot=0)
        _acquire_lock(engine, instance_id=instance_id, job_id=sibling.job_id,
                      queue_id="q2", lock_slot=0)

        reaped = job_repo.reconcile_terminal_message_mirrors(
            task_repository=task_repo,
        )

        assert len(reaped) == 1
        assert reaped[0].job_id == job.job_id
        _assert_own_lock_released_and_coinstance_lock_survives(
            engine, finalized_job=job, surviving_job=sibling,
        )


# ─────────────────────────────────────────────────────────────────────
# (b) Post-revive successor's lock survives a late finalize of the old job
# ─────────────────────────────────────────────────────────────────────


class TestSuccessorLockSurvivesLateFinalize:
    """A post-revive successor job's lock on the SAME instance must
    survive a LATE finalize of the old job. Shape: instance X finished
    old job J1 (its finalize is delayed), revived, and re-dispatched
    as successor J2 — X now holds the successor's lock. The late J1
    finalize must not delete it. Pre-fix: it did."""

    def test_successor_lock_survives_late_inline_writer_finalize(
        self, engine, job_repo
    ):
        """Inline writer: successor re-acquired on the SAME queue (a
        second slot — both locks coexist pre-finalize)."""
        instance_id = _seed_instance(engine)
        old_job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        successor = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=old_job.job_id,
                      queue_id="q1", lock_slot=0)
        _acquire_lock(engine, instance_id=instance_id, job_id=successor.job_id,
                      queue_id="q1", lock_slot=1)

        finalized = job_repo.finalize_mirror_job_at_completion(old_job.job_id)

        assert finalized is not None
        _assert_own_lock_released_and_coinstance_lock_survives(
            engine, finalized_job=old_job, surviving_job=successor,
        )

    def test_successor_lock_survives_late_backstop_reconcile(
        self, engine, job_repo, task_repo
    ):
        """Backstop: old job's Task went terminal while the successor
        is mid-flight; the reconcile must not touch the successor's
        lock."""
        instance_id = _seed_instance(engine)
        old_job = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        successor = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        _seed_task(
            engine,
            work_id=old_job.job_id,
            instance_id=instance_id,
            status=TaskStatus.CANCELLED.value,
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=old_job.job_id,
                      queue_id="q1", lock_slot=0)
        _acquire_lock(engine, instance_id=instance_id, job_id=successor.job_id,
                      queue_id="q1", lock_slot=1)

        reaped = job_repo.reconcile_terminal_message_mirrors(
            task_repository=task_repo,
        )

        assert len(reaped) == 1
        assert reaped[0].job_id == old_job.job_id
        _assert_own_lock_released_and_coinstance_lock_survives(
            engine, finalized_job=old_job, surviving_job=successor,
        )


# ─────────────────────────────────────────────────────────────────────
# (c) F5 ``force_finalize_orphan`` releases ONLY the orphan's own lock
# ─────────────────────────────────────────────────────────────────────


class TestForceFinalizeOrphanScopesToOwnLock:
    """The orphan reaper (``force_finalize_orphan``) must release the
    orphan job's OWN lock and leave a co-instance job's lock intact.
    Pre-fix: the instance-scoped DELETE removed every lock for the
    instance."""

    def test_orphan_reap_releases_only_orphan_lock(self, engine, job_repo):
        instance_id = _seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        orphan = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q1",
        )
        survivor = _seed_message_job(
            engine, instance_id=instance_id, admission_state="active",
            queue_id="q2",
        )
        _acquire_lock(engine, instance_id=instance_id, job_id=orphan.job_id,
                      queue_id="q1", lock_slot=0)
        _acquire_lock(engine, instance_id=instance_id, job_id=survivor.job_id,
                      queue_id="q2", lock_slot=0)

        reaped = job_repo.force_finalize_orphan(orphan.job_id, "cancelled")

        assert reaped is not None
        assert reaped.admission_state == AdmissionState.DONE.value
        _assert_own_lock_released_and_coinstance_lock_survives(
            engine, finalized_job=orphan, surviving_job=survivor,
        )
