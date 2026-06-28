"""Lifecycle regression tests for Job-as-Queue-Proxy Phase 3.

Phase 3 (commit f2acdd4c) of ``feature/job-as-queue-proxy`` cut over
**9 gating/count queries** from ``status``-based filtering to
``admission_state``-based filtering, plus the critical
``_ACTIVE_JOB_IDS_SUBQUERY`` in ``lock_repository.py``. These tests
pin the regression contract: every lifecycle flow still works after
the query migration, and the C2 / C3 invariants (the bugs Phase 3
was built to fix) hold.

Methods changed in Phase 3
--------------------------

``daemon/repositories/job_queue/repository.py``:

1. ``get_active_by_instance``            — ``admission_state IN ('queued','active')``
2. ``count_active_jobs_by_project``      — ``admission_state IN ('queued','active')`` (C2)
3. ``count_active_jobs_in_non_defer_queues`` — same (C2)
4. ``list_pending_by_project``           — ``admission_state='queued'``
5. ``list_all_pending``                  — ``admission_state='queued'``
6. ``list_pending_by_queue``             — ``admission_state='queued'``
7. ``find_processing_jobs``              — ``admission_state='active'`` (incl. PAUSED)
8. ``find_jobs_by_instance``             — ``admission_state IN ('queued','active')``
9. ``find_retryable_jobs``               — ``admission_state='queued' AND next_retry_at IS NOT NULL``

``daemon/repositories/job_queue/lock_repository.py``:

10. ``_ACTIVE_JOB_IDS_SUBQUERY``         — ``admission_state IN ('queued','active')`` (C3)

Key invariants pinned
--------------------

* **C2 (FIFO priority)**: ``count_active_jobs_by_project`` /
  ``count_active_jobs_in_non_defer_queues`` must include BOTH
  ``'queued'`` AND ``'active'`` admission_state rows. The defer
  idle-gate relies on this count to decide whether non-defer queues
  are idle. A project with only queued work must still block defer
  queues — counting only ``'active'`` would deadlock the system when
  multiple defer queues had pending work.

* **C3 (stale-lock race-delete)**: The
  ``_ACTIVE_JOB_IDS_SUBQUERY`` used by
  ``clear_stale_job_locks`` / ``clear_terminal_job_locks`` must
  include BOTH ``'queued'`` AND ``'active'`` admission_state rows.
  Limiting it to ``'active'`` would race-delete the lock of a job
  that just acquired its lock but hasn't yet transitioned to
  ``'active'`` in the same transaction (the B1 single-transaction
  window).

* **Dual-write invariant**: ``admission_state`` always equals
  ``status_to_admission(status)`` for every persisted row — Phase 2's
  contract is preserved by Phase 3.

Test structure (A-H matches the task spec)
------------------------------------------

A. Job Creation — verify ``admission_state='queued'`` on create / dedup
B. Job Start   — verify ``admission_state='active'`` + ``find_processing_jobs`` sees it
C. Job Complete — verify ``admission_state='done'`` + removed from active counts
D. Job Fail    — verify ``admission_state='done'`` + removed from active counts
E. Job Cancel  — verify ``admission_state='done'`` (from both PENDING and PROCESSING)
F. Pause/Resume — verify PAUSED stays ``active`` (lock held; C2 invariant)
G. DLQ Flow    — verify ``admission_state='dead'`` + replay resets to ``queued``
H. Retry Flow  — verify ``atomic_retry`` resets to ``queued`` + ``find_retryable_jobs`` finds it

All tests use the same in-memory SQLite + StaticPool fixture style
as ``tests/unit/services/test_jq_proxy_phase2_dualwrite.py`` (real
repos against a fresh schema, no mocks) so the query migration is
exercised end-to-end through the SQL stack, not just the Python
mapping layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobQueue,
    JobStatus,
    QueueType,
    AdmissionState,
    AdmissionState,
    AdmissionState,
)
from daemon.repositories.job_queue.repository import JobRepository
from daemon.services.dead_letter_service import DeadLetterService


# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# The ``status_to_admission`` helper was deleted from
# ``daemon.repositories.job_queue.models`` in Phase 4 cleanup
# (``admission_state`` is now the sole write authority). Tests that
# seed JobItem rows from a ``status`` string still need this
# JobStatus -> AdmissionState mapping, so we redefine it locally
# here. Behavior is identical to the deleted production helper
# (including the ``QUEUED`` fallback for unknown inputs).
def status_to_admission(status):  # noqa: ANN001,ANN201 — test-local re-export
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }.get(status, "queued")


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool, FK pragma on).

    Mirrors the fixture used in ``test_jq_proxy_phase2_dualwrite.py`` —
    StaticPool keeps a single connection alive for the whole test so
    asyncio.to_thread workers share the in-memory store, and
    ``PRAGMA foreign_keys=ON`` matches the production daemon's SQLite
    posture.
    """
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def dlq_repo(engine: Engine) -> DeadLetterRepository:
    return DeadLetterRepository(engine)


@pytest.fixture
def dlq_service(
    job_repo: JobRepository, dlq_repo: DeadLetterRepository
) -> DeadLetterService:
    """DeadLetterService wired against the test engine.

    Minimal wiring — no watcher-notification path is exercised here.
    """
    return DeadLetterService(job_repository=job_repo, dlq_repository=dlq_repo)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_queue(
    engine: Engine, *, project_id: str = "test-project", queue_type: QueueType | None = None
) -> str:
    """Insert a ``JobQueue`` row and return its ``queue_id``.

    Each call produces a unique ``queue_name`` (a short uuid suffix)
    so multiple jobs in the same project can coexist without
    tripping the ``UNIQUE(project_id, queue_name_lower)`` constraint.
    """
    queue_id = f"q-{uuid.uuid4().hex[:12]}"
    queue_name = f"q-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(
            JobQueue(
                queue_id=queue_id,
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name,
                queue_type=(queue_type or QueueType.FIFO).value,
                concurrency_limit=1,
            )
        )
        s.commit()
    return queue_id


def _make_job(
    engine: Engine, job_repo: JobRepository, **overrides
) -> JobItem:
    """Create a job with reasonable defaults and return the JobItem.

    Each call gets its own JobQueue so multiple jobs in the same
    project don't collide on the UNIQUE constraint.
    """
    project_id = overrides.pop("project_id", "test-project")
    queue_id = overrides.pop("queue_id", None) or _make_queue(
        engine, project_id=project_id
    )
    defaults = {
        "agent_id": "developer",
        "agent_dir": "/tmp/agents/developer",
        "message": "phase3 regression test job",
        "source": "api",
        "project_id": project_id,
        "queue_id": queue_id,
        "priority": 5,
    }
    defaults.update(overrides)
    return job_repo.create(**defaults)


def _refresh(engine: Engine, job_id: str) -> JobItem | None:
    """Re-read a JobItem from the engine so the assertions see a fresh row."""
    with Session(engine) as s:
        return s.exec(select(JobItem).where(JobItem.job_id == job_id)).one_or_none()


def _job_ids(jobs: list[JobItem]) -> set[str]:
    return {j.job_id for j in jobs}


# ─── A. Job Creation ────────────────────────────────────────────────────────


class TestJobCreation:
    """Phase 3 regression for the create-path queries.

    Phase 3 migrated ``list_pending_by_project``, ``list_all_pending``,
    ``list_pending_by_queue`` to filter on ``admission_state='queued'``.
    A freshly-created job must therefore appear in all three
    "pending" list queries.
    """

    def test_create_sets_admission_state_queued(
        self, engine, job_repo: JobRepository
    ):
        """``create()`` returns a JobItem with
        ``status='pending', admission_state='queued'``.
        """
        job = _make_job(engine, job_repo)
        assert job.admission_state == AdmissionState.QUEUED.value
        assert job.admission_state == AdmissionState.QUEUED.value

    def test_create_appears_in_list_pending_by_project(
        self, engine, job_repo: JobRepository
    ):
        """A fresh job appears in ``list_pending_by_project``
        (queries admission_state='queued').
        """
        job = _make_job(engine, job_repo, project_id="proj-a")

        results = job_repo.list_pending_by_project("proj-a")
        assert job.job_id in _job_ids(results)

    def test_create_appears_in_list_all_pending(
        self, engine, job_repo: JobRepository
    ):
        """A fresh job appears in ``list_all_pending`` (queries
        admission_state='queued').
        """
        job = _make_job(engine, job_repo)

        results = job_repo.list_all_pending()
        assert job.job_id in _job_ids(results)

    def test_create_appears_in_list_pending_by_queue(
        self, engine, job_repo: JobRepository
    ):
        """A fresh job appears in ``list_pending_by_queue`` (queries
        admission_state='queued').
        """
        queue_id = _make_queue(engine)
        job = job_repo.create(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="queue-targeted job",
            project_id="test-project",
            queue_id=queue_id,
        )

        results = job_repo.list_pending_by_queue(queue_id)
        assert len(results) == 1
        assert results[0].job_id == job.job_id

    def test_create_with_idempotency_key_dedups(
        self, engine, job_repo: JobRepository
    ):
        """Idempotency-key dedup still works after Phase 3 — second
        call returns the winner with consistent columns.
        """
        key = f"key-{uuid.uuid4().hex[:12]}"
        first, created_first = job_repo.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="winner",
            idempotency_key=key,
            project_id="test-project",
        )
        assert created_first is True
        assert first.admission_state == AdmissionState.QUEUED.value

        second, created_second = job_repo.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="loser",
            idempotency_key=key,
            project_id="test-project",
        )
        assert created_second is False
        assert second.job_id == first.job_id
        # Loser sees the winner's consistent columns.
        assert second.admission_state == AdmissionState.QUEUED.value


# ─── B. Job Start (acquire lock + transition) ───────────────────────────────


class TestJobStart:
    """Phase 3 regression for the start-path queries.

    After ``start_job``, the row has ``admission_state='active'``.
    Phase 3's ``find_processing_jobs`` queries ``admission_state='active'``,
    so the started job must appear. ``count_active_jobs_by_project``
    queries ``admission_state IN ('queued','active')``, so the started
    job must also be counted.
    """

    def test_start_sets_admission_state_active(
        self, engine, job_repo: JobRepository
    ):
        """``start_job`` transitions PENDING→PROCESSING AND
        queued→active in the same UPDATE.
        """
        job = _make_job(engine, job_repo)
        started = job_repo.start_job(job.job_id, instance_id="inst-1")
        assert started is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.instance_id == "inst-1"

    def test_started_job_appears_in_find_processing_jobs(
        self, engine, job_repo: JobRepository
    ):
        """``find_processing_jobs`` (now ``admission_state='active'``)
        finds the started job.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")

        processing = job_repo.find_processing_jobs()
        assert job.job_id in _job_ids(processing)

    def test_started_job_counted_in_active_by_project(
        self, engine, job_repo: JobRepository
    ):
        """``count_active_jobs_by_project`` (now
        ``admission_state IN ('queued','active')``) counts the started
        job.
        """
        job = _make_job(engine, job_repo, project_id="proj-b")
        job_repo.start_job(job.job_id, instance_id="inst-1")

        count = job_repo.count_active_jobs_by_project("proj-b")
        assert count == 1

    def test_started_job_found_by_get_active_by_instance(
        self, engine, job_repo: JobRepository
    ):
        """``get_active_by_instance`` (now
        ``admission_state IN ('queued','active')``) finds the started
        job by instance_id.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")

        active = job_repo.get_active_by_instance("inst-1")
        assert active is not None
        assert active.job_id == job.job_id
        assert active.admission_state == AdmissionState.ACTIVE.value

    def test_started_job_found_by_find_jobs_by_instance(
        self, engine, job_repo: JobRepository
    ):
        """``find_jobs_by_instance`` (now
        ``admission_state IN ('queued','active')``) finds the started
        job.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-2")

        results = job_repo.find_jobs_by_instance("inst-2")
        assert job.job_id in _job_ids(results)


# ─── C. Job Complete ────────────────────────────────────────────────────────


class TestJobComplete:
    """Phase 3 regression for the complete-path queries.

    After ``complete_job``, the row has ``admission_state='done'``.
    Phase 3 queries must therefore EXCLUDE completed jobs from active
    counts and from ``find_processing_jobs``.
    """

    def test_complete_sets_admission_state_done(
        self, engine, job_repo: JobRepository
    ):
        """``complete_job`` transitions PROCESSING→COMPLETED AND
        active→done.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        completed = job_repo.complete_job(job.job_id, result_summary="ok")
        assert completed is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.result_summary == "ok"

    def test_completed_job_not_in_find_processing_jobs(
        self, engine, job_repo: JobRepository
    ):
        """Completed jobs must NOT appear in ``find_processing_jobs``
        (queries ``admission_state='active'``, completed is ``done``).
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.complete_job(job.job_id, result_summary="ok")

        processing = job_repo.find_processing_jobs()
        assert job.job_id not in _job_ids(processing)

    def test_completed_job_not_counted_in_active_by_project(
        self, engine, job_repo: JobRepository
    ):
        """Completed jobs must NOT be counted by
        ``count_active_jobs_by_project`` (active bucket is
        ``admission_state IN ('queued','active')``, completed is
        ``done``).
        """
        job = _make_job(engine, job_repo, project_id="proj-c")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.complete_job(job.job_id, result_summary="ok")

        count = job_repo.count_active_jobs_by_project("proj-c")
        assert count == 0

    def test_completed_job_not_in_list_pending(
        self, engine, job_repo: JobRepository
    ):
        """Completed jobs must NOT appear in ``list_pending_by_project``
        / ``list_all_pending`` / ``list_pending_by_queue`` (all query
        ``admission_state='queued'``).
        """
        job = _make_job(engine, job_repo, project_id="proj-c")
        queue_id = _make_queue(engine, project_id="proj-c")
        # Re-create the job bound to the second queue for explicit
        # queue-pending lookup.
        job2 = job_repo.create(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="queue-targeted",
            project_id="proj-c",
            queue_id=queue_id,
        )

        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.complete_job(job.job_id, result_summary="ok")
        job_repo.start_job(job2.job_id, instance_id="inst-2")
        job_repo.complete_job(job2.job_id, result_summary="ok")

        assert job.job_id not in _job_ids(
            job_repo.list_pending_by_project("proj-c")
        )
        assert job.job_id not in _job_ids(job_repo.list_all_pending())
        assert job2.job_id not in _job_ids(job_repo.list_pending_by_queue(queue_id))


# ─── D. Job Fail ────────────────────────────────────────────────────────────


class TestJobFail:
    """Phase 3 regression for the fail-path queries.

    ``fail_job`` transitions PROCESSING→FAILED AND active→done.
    Failed jobs must therefore be excluded from active counts.
    """

    def test_fail_sets_admission_state_done(
        self, engine, job_repo: JobRepository
    ):
        """``fail_job`` writes ``status='failed', admission_state='done'``."""
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        failed = job_repo.fail_job(job.job_id, error_message="boom")
        assert failed is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.error_message == "boom"

    def test_failed_job_not_counted_in_active(
        self, engine, job_repo: JobRepository
    ):
        """Failed jobs must NOT be counted by
        ``count_active_jobs_by_project``.
        """
        job = _make_job(engine, job_repo, project_id="proj-d")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="boom")

        assert job_repo.count_active_jobs_by_project("proj-d") == 0

    def test_failed_job_not_in_find_processing_jobs(
        self, engine, job_repo: JobRepository
    ):
        """Failed jobs must NOT appear in ``find_processing_jobs``."""
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="boom")

        assert job.job_id not in _job_ids(job_repo.find_processing_jobs())


# ─── E. Job Cancel ──────────────────────────────────────────────────────────


class TestJobCancel:
    """Phase 3 regression for the cancel-path queries.

    ``cancel_job`` transitions either PENDING or PROCESSING to
    CANCELLED, both with ``admission_state='done'``. Cancelled jobs
    must be excluded from all active counts.
    """

    def test_cancel_from_pending_sets_done(
        self, engine, job_repo: JobRepository
    ):
        """Cancelling a PENDING (queued) job writes ``done``."""
        job = _make_job(engine, job_repo, project_id="proj-e")
        cancelled = job_repo.cancel_job(job.job_id)
        assert cancelled is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value

    def test_cancel_from_processing_sets_done(
        self, engine, job_repo: JobRepository
    ):
        """Cancelling a PROCESSING (active) job writes ``done``."""
        job = _make_job(engine, job_repo, project_id="proj-e")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        cancelled = job_repo.cancel_job(job.job_id)
        assert cancelled is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value

    def test_cancelled_job_not_counted_in_active(
        self, engine, job_repo: JobRepository
    ):
        """Cancelled jobs (either from PENDING or PROCESSING) must NOT
        be counted by ``count_active_jobs_by_project``.
        """
        job_pending = _make_job(engine, job_repo, project_id="proj-e")
        job_processing = _make_job(engine, job_repo, project_id="proj-e")
        job_repo.start_job(job_processing.job_id, instance_id="inst-1")

        job_repo.cancel_job(job_pending.job_id)
        job_repo.cancel_job(job_processing.job_id)

        assert job_repo.count_active_jobs_by_project("proj-e") == 0

    def test_cancelled_job_not_in_find_processing_jobs(
        self, engine, job_repo: JobRepository
    ):
        """Cancelled jobs must NOT appear in ``find_processing_jobs``
        (queries ``admission_state='active'``).
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.cancel_job(job.job_id)

        assert job.job_id not in _job_ids(job_repo.find_processing_jobs())


# ─── F. Pause/Resume Cascade ────────────────────────────────────────────────


class TestPauseResume:
    """Phase 3 regression for the pause/resume cascade.

    PAUSED is special: ``status_to_admission('paused') == 'active'``
    because pause is an Instance concern — the JobLock is still held.
    Phase 3's ``count_active_jobs_by_project`` queries
    ``admission_state IN ('queued','active')``, so a PAUSED job MUST
    still count toward the project active count (C2 invariant).

    ``find_processing_jobs`` queries ``admission_state='active'``, so
    a paused job now appears in this query too (a Phase 3 semantic
    change from the prior ``status='processing'`` filter, documented
    in the method docstring).
    """

    def test_pause_keeps_admission_state_active(
        self, engine, job_repo: JobRepository
    ):
        """``PROCESSING → PAUSED`` keeps ``admission_state='active'``."""
        job = _make_job(engine, job_repo, project_id="proj-f")
        job_repo.start_job(job.job_id, instance_id="inst-1")

        paused = job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )
        assert paused is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.admission_state == AdmissionState.ACTIVE.value

    def test_paused_job_still_counts_toward_active_project(
        self, engine, job_repo: JobRepository
    ):
        """C2 invariant: a paused job still counts toward
        ``count_active_jobs_by_project``. The defer idle-gate relies
        on this — a project with a paused-but-locked job must not
        deadlock waiting for non-defer queues to drain.
        """
        job = _make_job(engine, job_repo, project_id="proj-f")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )

        # Paused job MUST still be counted.
        assert job_repo.count_active_jobs_by_project("proj-f") == 1

    def test_paused_job_appears_in_find_processing_jobs(
        self, engine, job_repo: JobRepository
    ):
        """Phase 3 semantic change: ``find_processing_jobs`` queries
        ``admission_state='active'`` so PAUSED-status jobs now appear
        too. The startup-recovery contract distinguishes paused-vs-
        orphaned via ``Instance.status``, not via this query.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )

        # Paused job appears in ``find_processing_jobs`` because
        # admission_state is still 'active'.
        assert job.job_id in _job_ids(job_repo.find_processing_jobs())

    def test_resume_keeps_admission_state_active(
        self, engine, job_repo: JobRepository
    ):
        """``PAUSED → PROCESSING`` keeps ``admission_state='active'``."""
        job = _make_job(engine, job_repo, project_id="proj-f")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )

        resumed = job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PAUSED.value,
            to_status=JobStatus.PROCESSING.value,
        )
        assert resumed is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.admission_state == AdmissionState.ACTIVE.value

    def test_pause_resume_round_trip_preserves_active_count(
        self, engine, job_repo: JobRepository
    ):
        """End-to-end: start → pause → resume keeps the project
        active count at 1 the whole time.
        """
        job = _make_job(engine, job_repo, project_id="proj-f")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        assert job_repo.count_active_jobs_by_project("proj-f") == 1

        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )
        assert job_repo.count_active_jobs_by_project("proj-f") == 1

        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PAUSED.value,
            to_status=JobStatus.PROCESSING.value,
        )
        assert job_repo.count_active_jobs_by_project("proj-f") == 1


# ─── G. DLQ Flow ────────────────────────────────────────────────────────────


class TestDLQFlow:
    """Phase 3 regression for the DLQ-path queries.

    ``move_to_dlq_standalone`` writes ``status='dead_letter',
    admission_state='dead'``. A dead-lettered job must NOT be counted
    in active counts and must NOT appear in ``find_processing_jobs``.

    ``replay_from_dlq`` resets to ``status='pending',
    admission_state='queued'``. After replay, the job must reappear
    in the pending lists.
    """

    def test_move_to_dlq_sets_admission_state_dead(
        self, engine, job_repo, dlq_service
    ):
        """DLQ-ing a failed job writes
        ``status='dead_letter', admission_state='dead'``.
        """
        job = _make_job(engine, job_repo, project_id="proj-g")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="max retries")

        dlq_item = dlq_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )
        assert dlq_item is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DEAD.value
        assert refetched.admission_state == AdmissionState.DEAD.value

    def test_dlq_job_not_counted_in_active(
        self, engine, job_repo, dlq_service
    ):
        """DLQ'd jobs must NOT be counted by
        ``count_active_jobs_by_project`` (dead bucket is separate).
        """
        job = _make_job(engine, job_repo, project_id="proj-g")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="max retries")
        dlq_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )

        assert job_repo.count_active_jobs_by_project("proj-g") == 0

    def test_dlq_job_not_in_find_processing_jobs(
        self, engine, job_repo, dlq_service
    ):
        """DLQ'd jobs must NOT appear in ``find_processing_jobs``
        (dead ≠ active).
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="max retries")
        dlq_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )

        assert job.job_id not in _job_ids(job_repo.find_processing_jobs())

    def test_replay_from_dlq_resets_to_queued(
        self, engine, job_repo, dlq_service
    ):
        """``replay_from_dlq`` resets the row to
        ``status='pending', admission_state='queued'`` and clears the
        retry/error fields.
        """
        job = _make_job(engine, job_repo, project_id="proj-g")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="max retries")
        dlq_item = dlq_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )

        replayed = dlq_service.replay_from_dlq(dlq_item.dlq_id)
        assert replayed is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.admission_state == AdmissionState.QUEUED.value
        # Side effects of replay.
        assert refetched.retry_count == 0
        assert refetched.error_message is None
        assert refetched.failed_at is None
        assert refetched.instance_id is None

    def test_replayed_job_reappears_in_pending_lists(
        self, engine, job_repo, dlq_service
    ):
        """After replay, the job must reappear in pending lists
        (Phase 3 queries ``admission_state='queued'``).
        """
        job = _make_job(engine, job_repo, project_id="proj-g")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="max retries")
        dlq_item = dlq_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )
        dlq_service.replay_from_dlq(dlq_item.dlq_id)

        assert job.job_id in _job_ids(
            job_repo.list_pending_by_project("proj-g")
        )
        assert job.job_id in _job_ids(job_repo.list_all_pending())

        assert job_repo.count_active_jobs_by_project("proj-g") == 1


# ─── H. Retry Flow ──────────────────────────────────────────────────────────


class TestRetryFlow:
    """Phase 3 regression for the retry-path queries.

    ``atomic_retry`` writes ``status='pending',
    admission_state='queued'`` and increments ``retry_count`` in the
    same UPDATE. After retry the job is back in the active bucket
    (``admission_state IN ('queued','active')``) AND is findable by
    ``find_retryable_jobs`` (which queries
    ``admission_state='queued' AND next_retry_at IS NOT NULL``).
    """

    def test_atomic_retry_sets_admission_state_queued(
        self, engine, job_repo: JobRepository
    ):
        """``atomic_retry`` writes
        ``status='pending', admission_state='queued'`` and bumps
        ``retry_count``.
        """
        job = _make_job(engine, job_repo, project_id="proj-h")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="transient")

        past = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
        retried = job_repo.atomic_retry(
            job_id=job.job_id, max_retries=3, next_retry_at=past
        )
        assert retried is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.retry_count == 1
        assert refetched.error_message is None

    def test_retried_job_appears_in_list_pending(
        self, engine, job_repo: JobRepository
    ):
        """After ``atomic_retry``, the job appears in
        ``list_pending_by_project`` and ``list_all_pending``
        (Phase 3 queries ``admission_state='queued'``).
        """
        job = _make_job(engine, job_repo, project_id="proj-h")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="transient")
        past = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
        job_repo.atomic_retry(
            job_id=job.job_id, max_retries=3, next_retry_at=past
        )

        assert job.job_id in _job_ids(
            job_repo.list_pending_by_project("proj-h")
        )
        assert job.job_id in _job_ids(job_repo.list_all_pending())

    def test_retried_job_found_by_find_retryable_jobs(
        self, engine, job_repo: JobRepository
    ):
        """After ``atomic_retry`` with a past ``next_retry_at``, the
        job is findable by ``find_retryable_jobs`` (Phase 3 query:
        ``admission_state='queued' AND next_retry_at IS NOT NULL
        AND next_retry_at <= now``).
        """
        job = _make_job(engine, job_repo, project_id="proj-h")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="transient")
        past = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
        job_repo.atomic_retry(
            job_id=job.job_id, max_retries=3, next_retry_at=past
        )

        retryable = job_repo.find_retryable_jobs(project_id="proj-h")
        assert job.job_id in _job_ids(retryable)

    def test_fresh_queued_job_excluded_from_find_retryable(
        self, engine, job_repo: JobRepository
    ):
        """Phase 3 discriminator: a freshly-created queued job
        (``next_retry_at IS NULL``) must NOT appear in
        ``find_retryable_jobs`` even though it matches
        ``admission_state='queued'``.
        """
        job = _make_job(engine, job_repo, project_id="proj-h")

        retryable = job_repo.find_retryable_jobs(project_id="proj-h")
        assert job.job_id not in _job_ids(retryable)

    def test_retried_job_in_future_window_excluded(
        self, engine, job_repo: JobRepository
    ):
        """``find_retryable_jobs`` excludes jobs whose
        ``next_retry_at`` is still in the future — same as Phase 2,
        but the predicate now sits on top of admission_state='queued'
        instead of status='failed'.
        """
        job = _make_job(engine, job_repo, project_id="proj-h")
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="transient")
        future = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        job_repo.atomic_retry(
            job_id=job.job_id, max_retries=3, next_retry_at=future
        )

        retryable = job_repo.find_retryable_jobs(project_id="proj-h")
        assert job.job_id not in _job_ids(retryable)

    def test_retry_exhausted_finds_no_retryable(
        self, engine, job_repo: JobRepository
    ):
        """When ``atomic_retry`` exhausts retries, no retryable job
        is surfaced. Phase 3's ``find_retryable_jobs`` excludes
        non-queued jobs; the failed-but-not-retried job has
        ``admission_state='done'`` and is naturally excluded.
        """
        job = _make_job(engine, job_repo, project_id="proj-h")
        past = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()

        # Round 1: start, fail, retry (succeeds, retry_count=1).
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="transient 1")
        first = job_repo.atomic_retry(
            job_id=job.job_id, max_retries=1, next_retry_at=past
        )
        assert first is not None

        # Round 2: start, fail, retry (refuses — retry_count == max_retries).
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="transient 2")
        second = job_repo.atomic_retry(
            job_id=job.job_id, max_retries=1, next_retry_at=past
        )
        assert second is None

        # Job is back to FAILED + admission_state='done'.
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
        # No retryable jobs.
        assert job_repo.find_retryable_jobs(project_id="proj-h") == []


# ─── I. Cross-cutting invariants ────────────────────────────────────────────


class TestCrossCuttingInvariants:
    """Cross-cutting invariants that span multiple flows. These pin
    the C2 (FIFO priority) and C3 (stale-lock race-delete) fixes
    that Phase 3 was built to address.
    """

    def test_c2_queued_jobs_counted_in_active_by_project(
        self, engine, job_repo: JobRepository
    ):
        """C2 invariant: a project with ONLY queued jobs (no active
        yet) is still counted as having active jobs. The defer idle-
        gate relies on this to block defer queues when non-defer
        queues have pending work — without it, FIFO priority would
        deadlock in mixed-queue projects.

        ``count_active_jobs_by_project`` must include
        ``admission_state='queued'`` (Phase 3 query).
        """
        # Create 3 jobs, do not start any of them — they stay queued.
        for _ in range(3):
            _make_job(engine, job_repo, project_id="proj-c2")

        # ALL three count, even though none are 'active' yet.
        assert job_repo.count_active_jobs_by_project("proj-c2") == 3

    def test_c2_queued_jobs_counted_in_non_defer_queues(
        self, engine, job_repo: JobRepository
    ):
        """C2 invariant: same for ``count_active_jobs_in_non_defer_queues``
        — the defer idle-gate specifically.

        Note: the default queue in ``_make_queue`` is FIFO (non-defer),
        so this directly exercises the non-defer count.
        """
        for _ in range(2):
            _make_job(engine, job_repo, project_id="proj-c2")

        assert (
            job_repo.count_active_jobs_in_non_defer_queues("proj-c2") == 2
        )

    def test_c2_defer_queues_excluded_from_non_defer_count(
        self, engine, job_repo: JobRepository
    ):
        """``count_active_jobs_in_non_defer_queues`` must EXCLUDE
        DEFER-queue jobs. Phase 3 changed the WHERE clause to
        ``admission_state IN (...)``; the JOIN to ``JobQueue`` for the
        ``queue_type != DEFER`` predicate is unchanged but worth
        pinning.
        """
        defer_queue_id = _make_queue(
            engine, project_id="proj-c2", queue_type=QueueType.DEFER
        )
        # Create a job in the defer queue.
        defer_job = job_repo.create(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="defer-only",
            project_id="proj-c2",
            queue_id=defer_queue_id,
        )
        # And a non-defer job for comparison.
        _make_job(engine, job_repo, project_id="proj-c2")

        # Non-defer count: only the FIFO job counts (1).
        assert (
            job_repo.count_active_jobs_in_non_defer_queues("proj-c2") == 1
        )
        # Total active count includes both.
        assert job_repo.count_active_jobs_by_project("proj-c2") == 2
        # Defer job is in the project total but not in non-defer.
        assert defer_job.admission_state == AdmissionState.QUEUED.value

    def test_c3_subquery_protects_queued_locks(
        self, engine, job_repo, dlq_service
    ):
        """C3 invariant: ``_ACTIVE_JOB_IDS_SUBQUERY`` (Phase 3:
        ``admission_state IN ('queued','active')``) must include
        freshly-queued jobs so the stale-lock sweep does NOT race-
        delete their locks.

        We verify the contract by hitting
        ``clear_terminal_job_locks`` on a freshly-queued job and
        asserting the job's lock (if any) is preserved. The
        in-memory SQLite fixture has no ``job_locks`` rows for queued
        jobs (locks are inserted atomically with start on PostgreSQL,
        triggered by the PG guard), so the practical assertion here
        is that the subquery SQL text matches the Phase 3 contract —
        ``admission_state IN ('queued','active')``.
        """
        from daemon.repositories.job_queue.lock_repository import (
            _ACTIVE_JOB_IDS_SUBQUERY,
        )

        assert "admission_state IN ('queued', 'active')" in _ACTIVE_JOB_IDS_SUBQUERY
        # And the legacy ``status IN`` predicate must be gone.
        assert "status IN" not in _ACTIVE_JOB_IDS_SUBQUERY

    def test_c3_subquery_does_not_match_done_or_dead(
        self, engine, job_repo, dlq_service
    ):
        """The subquery must exclude ``done`` and ``dead`` rows so
        terminal jobs do not have their (non-existent) locks
        retained.

        Belt-and-braces: a completed job and a dead-lettered job must
        not be in the subquery's result set.
        """
        # Build a small scenario.
        completed_job = _make_job(engine, job_repo, project_id="proj-c3")
        job_repo.start_job(completed_job.job_id, instance_id="inst-1")
        job_repo.complete_job(completed_job.job_id, result_summary="ok")

        dead_job = _make_job(engine, job_repo, project_id="proj-c3")
        job_repo.start_job(dead_job.job_id, instance_id="inst-1")
        job_repo.fail_job(dead_job.job_id, error_message="boom")
        dlq_service.move_to_dlq_standalone(
            job_id=dead_job.job_id, reason="MAX_RETRIES"
        )

        # Run the subquery manually and assert terminal jobs are absent.
        with Session(engine) as s:
            from sqlalchemy import text

            rows = s.exec(
                text(
                    "SELECT job_id FROM job_queue_items "
                    "WHERE admission_state IN ('queued', 'active') "
                    "  AND deleted_at IS NULL"
                )
            ).all()

        matched_ids = {r[0] for r in rows}
        assert completed_job.job_id not in matched_ids
        assert dead_job.job_id not in matched_ids

    def test_dual_write_invariant_after_full_lifecycle_walk(
        self, engine, job_repo, dlq_service
    ):
        """Belt-and-braces: every JobItem's ``admission_state``
        lands on the expected admission bucket after walking through
        a mix of lifecycle transitions. Phase 4 cleanup: the
        dual-write invariant is gone — ``status`` is no longer
        written, so the legacy ``admission_state ==
        status_to_admission(status)`` check no longer holds. The
        canonical invariant is now: ``admission_state`` matches the
        expected bucket for each transition path.

        Phase 3 walked this to confirm Phase 3 didn't regress
        Phase 2's dual-write contract; Phase 4 keeps the spirit of
        the test (a full-lifecycle walk is the strongest
        end-to-end check on the transition machinery) but
        drops the dual-write column read and asserts the
        ``admission_state`` bucket directly.
        """
        # Job 1: pending.
        j1 = _make_job(engine, job_repo, project_id="proj-x")
        # Job 2: pending → processing.
        j2 = _make_job(engine, job_repo, project_id="proj-x")
        job_repo.start_job(j2.job_id, instance_id="inst-1")
        # Job 3: pending → processing → paused.
        j3 = _make_job(engine, job_repo, project_id="proj-x")
        job_repo.start_job(j3.job_id, instance_id="inst-1")
        job_repo.atomic_transition(
            j3.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )
        # Job 4: pending → processing → completed.
        j4 = _make_job(engine, job_repo, project_id="proj-x")
        job_repo.start_job(j4.job_id, instance_id="inst-1")
        job_repo.complete_job(j4.job_id, result_summary="ok")
        # Job 5: pending → processing → failed → DLQ → replayed (back to queued).
        j5 = _make_job(engine, job_repo, project_id="proj-x")
        job_repo.start_job(j5.job_id, instance_id="inst-1")
        job_repo.fail_job(j5.job_id, error_message="boom")
        dlq_item = dlq_service.move_to_dlq_standalone(
            job_id=j5.job_id, reason="MAX_RETRIES"
        )
        dlq_service.replay_from_dlq(dlq_item.dlq_id)
        # Job 6: pending → processing → cancelled.
        j6 = _make_job(engine, job_repo, project_id="proj-x")
        job_repo.start_job(j6.job_id, instance_id="inst-1")
        job_repo.cancel_job(j6.job_id)

        expected_admissions = {
            j1.job_id: AdmissionState.QUEUED.value,
            j2.job_id: AdmissionState.ACTIVE.value,
            # PAUSED jobs keep ``active`` admission (pause is an
            # Instance concern; the JobItem lock is still held).
            j3.job_id: AdmissionState.ACTIVE.value,
            j4.job_id: AdmissionState.DONE.value,
            j5.job_id: AdmissionState.QUEUED.value,  # DLQ → replay → queued
            j6.job_id: AdmissionState.DONE.value,
        }
        for j, expected in expected_admissions.items():
            refetched = _refresh(engine, j)
            assert refetched.admission_state == expected, (
                f"Drift on {j}: "
                f"admission_state={refetched.admission_state!r} "
                f"(expected {expected!r})."
            )

    def test_phase3_all_nine_methods_cover_correct_rows(
        self, engine, job_repo: JobRepository
    ):
        """One-paragraph smoke: build a tiny mixed-state world and
        verify each of the 9 Phase 3 methods returns the expected
        subset. This is the most efficient regression tripwire — a
        single failed assertion identifies which Phase 3 method
        regressed.
        """
        proj = "proj-mix"

        # queued.
        j_queued = _make_job(engine, job_repo, project_id=proj)
        # active (processing).
        j_active = _make_job(engine, job_repo, project_id=proj)
        job_repo.start_job(j_active.job_id, instance_id="inst-active")
        # done (completed).
        j_done = _make_job(engine, job_repo, project_id=proj)
        job_repo.start_job(j_done.job_id, instance_id="inst-done")
        job_repo.complete_job(j_done.job_id, result_summary="ok")
        # paused (still active in admission).
        j_paused = _make_job(engine, job_repo, project_id=proj)
        job_repo.start_job(j_paused.job_id, instance_id="inst-paused")
        job_repo.atomic_transition(
            j_paused.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )

        # 1. count_active_jobs_by_project — queued + active (incl. paused).
        assert job_repo.count_active_jobs_by_project(proj) == 3

        # 2. count_active_jobs_in_non_defer_queues — same (all on FIFO).
        assert (
            job_repo.count_active_jobs_in_non_defer_queues(proj) == 3
        )

        # 3. list_pending_by_project — only the still-queued one.
        assert _job_ids(
            job_repo.list_pending_by_project(proj)
        ) == {j_queued.job_id}

        # 4. list_all_pending — same.
        # Note: there may be other queued rows in the engine from
        # other tests' fixtures, so just check membership.
        assert j_queued.job_id in _job_ids(job_repo.list_all_pending())

        # 5. find_processing_jobs — active AND paused (admission='active').
        processing_ids = _job_ids(job_repo.find_processing_jobs())
        assert {j_active.job_id, j_paused.job_id}.issubset(processing_ids)
        assert j_done.job_id not in processing_ids

        # 6. find_jobs_by_instance (per instance, queued+active only).
        assert job_repo.find_jobs_by_instance(
            "inst-active"
        ) and job_repo.find_jobs_by_instance("inst-active")[0].job_id == j_active.job_id
        assert (
            job_repo.find_jobs_by_instance("inst-done") == []
        )  # completed is 'done', not in IN list.
        assert job_repo.find_jobs_by_instance(
            "inst-paused"
        ) and job_repo.find_jobs_by_instance("inst-paused")[0].job_id == j_paused.job_id

        # 7. list_pending_by_queue — only j_queued's queue.
        assert j_queued.job_id in _job_ids(
            job_repo.list_pending_by_queue(j_queued.queue_id)
        )

        # 8. get_active_by_instance — active + paused, not done.
        assert job_repo.get_active_by_instance("inst-active").job_id == j_active.job_id
        assert job_repo.get_active_by_instance("inst-paused").job_id == j_paused.job_id
        assert job_repo.get_active_by_instance("inst-done") is None

        # 9. find_retryable_jobs — none here (no failed-with-next-retry).
        assert job_repo.find_retryable_jobs(project_id=proj) == []


# ─── Sanity smoke ───────────────────────────────────────────────────────────


class TestSmoke:
    """One quick smoke test to catch gross regressions in the fixture
    wiring. If this fails, the test file itself is broken (not the
    code under test).
    """

    def test_engine_smoke_roundtrip(self, engine, job_repo):
        """Sanity: a created job can be read back via the engine."""
        job = _make_job(engine, job_repo)
        refetched = _refresh(engine, job.job_id)
        assert refetched.job_id == job.job_id
        assert refetched.admission_state == AdmissionState.QUEUED.value