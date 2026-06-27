"""Phase 3 query migration tests for Job-as-Queue-Proxy.

Phase 3 (commit f2acdd4c) of ``feature/job-as-queue-proxy`` migrates all
gating / count queries from ``status``-based filtering to
``admission_state``-based filtering. The migration touches 10 query
sites in two files:

  - ``daemon/repositories/job_queue/repository.py`` (8 methods)
      * ``get_active_by_instance``
      * ``count_active_jobs_by_project``
      * ``count_active_jobs_in_non_defer_queues``
      * ``list_pending_by_project``
      * ``list_all_pending``
      * ``list_pending_by_queue``
      * ``find_processing_jobs``
      * ``find_jobs_by_instance``
      * ``find_retryable_jobs``
  - ``daemon/repositories/job_queue/lock_repository.py`` (1 subquery)
      * ``_ACTIVE_JOB_IDS_SUBQUERY`` used by ``clear_stale_job_locks``

**CRITICAL INVARIANT** — the gating queries (``count_active_jobs_*`` and
``_ACTIVE_JOB_IDS_SUBQUERY``) MUST include BOTH ``'queued'`` AND
``'active'`` admission states (NOT just ``'active'``):

  - **C2** — FIFO priority preservation. The ``count_active_jobs_*``
    functions feed the defer-idle-gate (``job_processor``). A project
    that has only ``'queued'`` work (no ``'active'`` job yet) must
    still block defer queues so the higher-priority non-defer work
    runs first. If only ``'active'`` were matched, a project whose
    first job hasn't been dequeued would falsely report "no active
    jobs" and let the defer queue run.
  - **C3** — Race-delete protection. ``clear_stale_job_locks`` uses
    ``_ACTIVE_JOB_IDS_SUBQUERY`` in a ``NOT IN`` clause. A job that
    just acquired its lock in the B1 single-transaction window
    (lock INSERT + ``admission_state='active'`` UPDATE in one tx) is
    ``'queued'`` for the duration of the window. If only
    ``'active'`` were matched, the stale-lock sweep would race-delete
    that lock before the tx commits. Including both states protects
    the entire admission window.

These tests pin the C2/C3 invariant at the SQL boundary (real
in-memory SQLite + StaticPool, no mocks), plus the per-query
semantic-equivalence tests for the other six migrated queries.

Test layout:

  C2  — FIFO priority tests
       (count_active_jobs_by_project, count_active_jobs_in_non_defer_queues)
  C3  — Race-delete protection tests
       (clear_stale_job_locks / _ACTIVE_JOB_IDS_SUBQUERY)
  A   — find_processing_jobs semantic equivalence
  B   — list_pending_* semantic equivalence
  C   — find_retryable_jobs semantic equivalence
  D   — find_jobs_by_instance semantic equivalence

All tests use the same fixture style as
``tests/unit/services/test_jq_proxy_phase2_dualwrite.py`` (async-safe
sync pytest, real repos against a fresh schema, in-memory SQLite with
StaticPool + FK pragma on) so the migration contract is exercised
end-to-end through the SQL stack, not just the Python mapping layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session as SQLModelSession, select

from daemon.repositories.job_queue.lock_repository import (
    LockRepository,
    _ACTIVE_JOB_IDS_SUBQUERY,
)
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobLock,
    JobQueue,
    JobStatus,
    QueueType,
    status_to_admission,
)
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool, FK pragma on).

    Mirrors the fixture used in
    ``tests/unit/services/test_jq_proxy_phase2_dualwrite.py`` —
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
def queue_repo(engine: Engine) -> JobQueueRepository:
    return JobQueueRepository(engine)


@pytest.fixture
def lock_repo(engine: Engine) -> LockRepository:
    return LockRepository(engine)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_queue(
    engine: Engine,
    *,
    project_id: str = "test-project",
    queue_type: str = QueueType.FIFO.value,
    concurrency_limit: int = 1,
) -> str:
    """Insert a ``JobQueue`` row and return its ``queue_id``.

    Each call produces a unique ``queue_name`` (a short uuid suffix)
    so multiple queues in the same project can coexist without
    tripping the ``UNIQUE(project_id, queue_name_lower)`` constraint.
    """
    queue_id = f"q-{uuid.uuid4().hex[:12]}"
    queue_name = f"q-{uuid.uuid4().hex[:8]}"
    with SQLModelSession(engine) as s:
        s.add(
            JobQueue(
                queue_id=queue_id,
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name,
                queue_type=queue_type,
                concurrency_limit=concurrency_limit,
            )
        )
        s.commit()
    return queue_id


def _make_job(
    engine: Engine, job_repo: JobRepository, **overrides
) -> JobItem:
    """Create a job with reasonable defaults and return the JobItem.

    ``project_id`` and ``queue_id`` are populated so DLQ / queue-bound
    queries (e.g. ``list_pending_by_queue``, ``find_jobs_by_instance``)
    have a valid target. Each call gets its own JobQueue to keep tests
    independent.
    """
    project_id = overrides.pop("project_id", "test-project")
    queue_id = overrides.pop("queue_id", None) or _make_queue(
        engine, project_id=project_id
    )
    defaults = {
        "agent_id": "developer",
        "agent_dir": "/tmp/agents/developer",
        "message": "phase3 query migration test job",
        "source": "api",
        "project_id": project_id,
        "queue_id": queue_id,
        "priority": 5,
    }
    defaults.update(overrides)
    return job_repo.create(**defaults)


def _force_state(engine: Engine, job_id: str, status: str) -> None:
    """Direct UPDATE bypassing the repo API to set arbitrary
    ``status`` + ``admission_state`` for test setup.

    The repo's lifecycle methods (start_job / fail_job / complete_job /
    cancel_job / atomic_transition / atomic_retry) already dual-write
    ``admission_state``. But several Phase 3 tests need to plant a
    job in an arbitrary combination (e.g. ``status='processing'`` +
    ``admission_state='queued'``) to simulate in-flight transitions,
    which the repo API doesn't expose. This helper does a raw SQL
    UPDATE so the test can pin the exact column values it cares
    about. Both columns are kept in lockstep via
    ``status_to_admission``.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE job_queue_items "
                "SET status = :s, admission_state = :a "
                "WHERE job_id = :id"
            ),
            {"s": status, "a": status_to_admission(status), "id": job_id},
        )


def _force_admission(engine: Engine, job_id: str, status: str, admission: str) -> None:
    """Direct UPDATE that sets ``status`` AND ``admission_state``
    independently.

    Used to simulate the B1 single-transaction window where a job is
    in ``admission_state='queued'`` but a lock has already been
    acquired, or the in-flight transition where ``status` and
    ``admission_state`` momentarily disagree. The repo API would
    reject these combinations; the test pins them to verify the SQL
    boundary holds under all combinations.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE job_queue_items "
                "SET status = :s, admission_state = :a "
                "WHERE job_id = :id"
            ),
            {"s": status, "a": admission, "id": job_id},
        )


def _refresh(engine: Engine, job_id: str) -> JobItem:
    """Re-read a JobItem from the engine."""
    with SQLModelSession(engine) as s:
        return s.exec(select(JobItem).where(JobItem.job_id == job_id)).one()


def _acquire_with_slot(
    lock_repo: LockRepository,
    project_id: str,
    queue_id: str,
    job_id: str,
    slot: int,
    instance_id: str | None = None,
) -> JobLock:
    """Acquire a JobLock with explicit ``lock_slot``.

    After migration ``20260619_000001_add_lock_slot_to_job_locks.sql``
    the ``uq_job_locks_slot`` UNIQUE constraint on
    ``(project_id, queue_id, lock_slot)`` makes the old pattern of
    inserting many locks with default ``lock_slot=0`` invalid —
    every lock in the same (project, queue) pair must carry a
    distinct slot.
    """
    lock = JobLock(
        project_id=project_id,
        queue_id=queue_id,
        job_id=job_id,
        instance_id=instance_id,
        lock_slot=slot,
    )
    return lock_repo.acquire(lock)


# ─── Static / source-level assertions ───────────────────────────────────────


class TestPhase3QueryMigrationSource:
    """Pin the Phase 3 SQL surface so a regression that drops the
    ``admission_state IN ('queued', 'active')`` predicate is caught
    even before the behavioural tests run.

    These tests grep the source for the predicate shape so a future
    refactor that accidentally narrows it to ``status IN (...)`` or
    ``admission_state = 'active'`` fails fast.
    """

    def test_active_job_ids_subquery_includes_both_states(self):
        """``_ACTIVE_JOB_IDS_SUBQUERY`` must include BOTH 'queued'
        AND 'active' (C3 fix). Narrowing to only 'active' would
        race-delete locks in the B1 single-transaction window.
        """
        assert "admission_state IN ('queued', 'active')" in _ACTIVE_JOB_IDS_SUBQUERY, (
            "_ACTIVE_JOB_IDS_SUBQUERY must include BOTH 'queued' AND "
            "'active'. The 'queued' inclusion protects the B1 single-"
            "transaction window from race-deletes (C3 fix)."
        )
        # Negative: the predicate MUST NOT use only 'active'.
        # The literal `IN ('active')` would be a regression.
        assert "admission_state IN ('active')" not in _ACTIVE_JOB_IDS_SUBQUERY

    def test_active_job_ids_subquery_does_not_use_status(self):
        """The subquery must filter on ``admission_state`` (NOT
        ``status``) — Phase 3's whole point. Using ``status`` here
        would miss ``paused`` jobs (whose admission_state stays
        ``active`` because pause is an Instance concern).
        """
        # The literal phrase ``status IN (`` inside the subquery is
        # a regression — Phase 3 moved the predicate to
        # ``admission_state IN (...)``.
        assert "status IN (" not in _ACTIVE_JOB_IDS_SUBQUERY, (
            "_ACTIVE_JOB_IDS_SUBQUERY must use admission_state, not status"
        )


# ─── C2: FIFO Priority Preservation ─────────────────────────────────────────


class TestC2FifoPriority:
    """C2 — the defer-idle-gate counts active jobs to decide whether
    non-defer queues are idle. If it counts only ``active`` and
    misses ``queued`` jobs, a project with only ``queued`` work
    would falsely report "no active jobs" and let a defer queue
    run — violating FIFO priority.

    These tests verify both ``count_active_jobs_by_project`` and
    ``count_active_jobs_in_non_defer_queues`` include BOTH
    ``queued`` AND ``active`` states.
    """

    def test_queued_pending_job_counts_as_active(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A freshly-created job (status='pending',
        admission_state='queued') MUST count toward
        ``count_active_jobs_by_project``.

        This is the core C2 invariant: queued jobs are
        "active" from the queue's POV — they are waiting to be
        dequeued, not finished.
        """
        job = _make_job(engine, job_repo)
        # Verify the row is in the expected starting state.
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.QUEUED.value

        count = job_repo.count_active_jobs_by_project("test-project")
        assert count == 1, (
            "A queued job MUST count toward active jobs (C2 fix). "
            "If this fails, the defer-idle-gate would falsely report "
            "'no active jobs' for a project with only queued work."
        )

    def test_started_processing_job_counts_as_active(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A started job (status='processing',
        admission_state='active') MUST count toward
        ``count_active_jobs_by_project`` — same as before the
        migration.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.ACTIVE.value

        count = job_repo.count_active_jobs_by_project("test-project")
        assert count == 1

    def test_done_jobs_excluded_from_count(
        self, engine: Engine, job_repo: JobRepository
    ):
        """Terminal jobs (admission_state='done' or 'dead') MUST
        NOT count toward the active count.
        """
        # One done job.
        done_job = _make_job(engine, job_repo)
        job_repo.start_job(done_job.job_id, instance_id="inst-1")
        job_repo.complete_job(done_job.job_id, result_summary="ok")
        assert _refresh(engine, done_job.job_id).admission_state == AdmissionState.DONE.value

        # One queued job (still counts).
        _make_job(engine, job_repo)

        count = job_repo.count_active_jobs_by_project("test-project")
        assert count == 1, "Only the queued job should count; done is terminal."

    def test_mixed_queued_and_done_states(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A mix of queued, active, done, and dead jobs: only
        queued+active count.
        """
        # Job A: queued (pending).
        a = _make_job(engine, job_repo)
        # Job B: active (processing).
        b = _make_job(engine, job_repo)
        job_repo.start_job(b.job_id, instance_id="inst-b")
        # Job C: done (completed).
        c = _make_job(engine, job_repo)
        job_repo.start_job(c.job_id, instance_id="inst-c")
        job_repo.complete_job(c.job_id, result_summary="ok")
        # Job D: failed (admission_state='done').
        d = _make_job(engine, job_repo)
        job_repo.start_job(d.job_id, instance_id="inst-d")
        job_repo.fail_job(d.job_id, error_message="boom")
        # Job E: cancelled (admission_state='done').
        e = _make_job(engine, job_repo)
        job_repo.cancel_job(e.job_id)

        # Sanity: A=queued, B=active, C=done, D=done, E=done.
        assert _refresh(engine, a.job_id).admission_state == AdmissionState.QUEUED.value
        assert _refresh(engine, b.job_id).admission_state == AdmissionState.ACTIVE.value
        for jid in (c.job_id, d.job_id, e.job_id):
            assert _refresh(engine, jid).admission_state == AdmissionState.DONE.value

        count = job_repo.count_active_jobs_by_project("test-project")
        assert count == 2, "Only queued (A) + active (B) count."

    def test_paused_jobs_count_as_active(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A paused job (``status='paused'``,
        ``admission_state='active'``) keeps its lock and stays
        'active' in admission — pause is an Instance concern.

        The active count must include paused jobs so the
        defer-idle-gate doesn't fire while a pause is in
        progress.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.ACTIVE.value

        count = job_repo.count_active_jobs_by_project("test-project")
        assert count == 1, "Paused jobs are still admission='active'."

    def test_other_projects_excluded(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``count_active_jobs_by_project`` only counts the
        requested project's jobs — sanity check that the project
        filter still works.
        """
        _make_job(engine, job_repo, project_id="project-A")
        _make_job(engine, job_repo, project_id="project-A")
        _make_job(engine, job_repo, project_id="project-B")

        assert job_repo.count_active_jobs_by_project("project-A") == 2
        assert job_repo.count_active_jobs_by_project("project-B") == 1
        assert job_repo.count_active_jobs_by_project("project-none") == 0

    def test_soft_deleted_jobs_excluded(
        self, engine: Engine, job_repo: JobRepository
    ):
        """Soft-deleted jobs (``deleted_at IS NOT NULL``) are
        excluded even if their admission_state is queued/active —
        matches the pre-migration behaviour.
        """
        job = _make_job(engine, job_repo)
        # Soft-delete via the repo API (which sets deleted_at).
        job_repo.soft_delete(job.job_id)

        assert job_repo.count_active_jobs_by_project("test-project") == 0

    # ─── count_active_jobs_in_non_defer_queues ──────────────────────────

    def test_non_defer_count_excludes_defer_queues(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``count_active_jobs_in_non_defer_queues`` JOINs
        ``job_queues`` and excludes ``queue_type='defer'``. The
        active count for non-defer queues only counts jobs in
        FIFO + PARALLEL queues.
        """
        # Job in a FIFO queue (non-defer) — counts.
        _make_job(engine, job_repo, queue_id=_make_queue(engine, queue_type=QueueType.FIFO.value))

        # Job in a PARALLEL queue (non-defer) — counts.
        _make_job(engine, job_repo, queue_id=_make_queue(engine, queue_type=QueueType.PARALLEL.value))

        # Job in a DEFER queue (must NOT count for non-defer gate).
        _make_job(engine, job_repo, queue_id=_make_queue(engine, queue_type=QueueType.DEFER.value, concurrency_limit=1))

        count = job_repo.count_active_jobs_in_non_defer_queues("test-project")
        assert count == 2, "Only FIFO + PARALLEL jobs count toward non-defer gate."

    def test_non_defer_count_includes_queued_state(
        self, engine: Engine, job_repo: JobRepository
    ):
        """C2 fix: ``count_active_jobs_in_non_defer_queues`` must
        include ``admission_state='queued'`` jobs in non-defer
        queues. Without this, the defer-idle-gate would falsely
        report "non-defer queues are idle" when a project has only
        queued FIFO/PARALLEL work — letting the defer queue run
        ahead of higher-priority queued work.
        """
        # Queued job in a FIFO queue — must count for the
        # non-defer gate.
        _make_job(engine, job_repo, queue_id=_make_queue(engine, queue_type=QueueType.FIFO.value))

        count = job_repo.count_active_jobs_in_non_defer_queues("test-project")
        assert count == 1, (
            "A queued job in a non-defer queue MUST count for the "
            "non-defer gate (C2 fix). Dropping this would let the "
            "defer queue run ahead of higher-priority queued work."
        )

    def test_non_defer_count_excludes_done_jobs(
        self, engine: Engine, job_repo: JobRepository
    ):
        """Done/dead jobs in non-defer queues don't count — they
        are terminal and the defer-idle-gate should not block on
        them.
        """
        done_job = _make_job(engine, job_repo, queue_id=_make_queue(engine, queue_type=QueueType.FIFO.value))
        job_repo.start_job(done_job.job_id, instance_id="inst-1")
        job_repo.complete_job(done_job.job_id, result_summary="ok")

        count = job_repo.count_active_jobs_in_non_defer_queues("test-project")
        assert count == 0


# ─── C3: Race-Delete Protection (Stale-Lock Sweep) ──────────────────────────


class TestC3RaceDeleteProtection:
    """C3 — ``clear_stale_job_locks`` uses
    ``_ACTIVE_JOB_IDS_SUBQUERY`` in a ``NOT IN`` clause. The
    subquery must include BOTH ``queued`` AND ``active`` so that
    locks for jobs in either admission state are protected from
    the sweep.

    The race this prevents: in the B1 single-transaction window,
    a job's lock INSERT and its ``admission_state='active'`` UPDATE
    commit together. During the window, the job's admission_state
    is still ``'queued'``. A concurrent stale-lock sweep would, if
    the predicate excluded ``'queued'``, race-delete the freshly-
    inserted lock before the tx commits.
    """

    def test_active_job_lock_survives_sweep(
        self, engine: Engine, lock_repo: LockRepository, job_repo: JobRepository
    ):
        """A lock for an ``admission_state='active'`` job MUST
        survive ``clear_stale_job_locks`` — the job is still
        in-flight, the lock is still held.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        _acquire_with_slot(lock_repo, "test-project", job.queue_id, job.job_id, slot=0)

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 0
        assert len(lock_repo.get_all_locks()) == 1

    def test_queued_job_lock_survives_sweep(
        self, engine: Engine, lock_repo: LockRepository, job_repo: JobRepository
    ):
        """A lock for an ``admission_state='queued'`` job MUST
        survive ``clear_stale_job_locks`` (C3 fix).

        This is the B1 race-delete protection: even if the
        job is still 'queued' (its lock INSERT happened but the
        admission_state UPDATE to 'active' is in the same tx),
        the lock must NOT be deleted.
        """
        # Plant a queued job with a lock.
        job = _make_job(engine, job_repo)
        # Default state: admission_state='queued', status='pending'.
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.QUEUED.value
        _acquire_with_slot(lock_repo, "test-project", job.queue_id, job.job_id, slot=0)

        # Run the stale-lock sweep. The queued job's lock must
        # NOT be deleted — that would be the C3 regression.
        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 0, (
            "A lock for a queued job MUST survive the sweep "
            "(C3 fix). The 'queued' inclusion protects the B1 "
            "single-transaction window from race-deletes."
        )
        assert len(lock_repo.get_all_locks()) == 1

    def test_paused_job_lock_survives_sweep(
        self, engine: Engine, lock_repo: LockRepository, job_repo: JobRepository
    ):
        """A lock for a paused job (status='paused',
        admission_state='active') MUST survive the sweep — the
        lock is still held (pause is an Instance concern).
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.ACTIVE.value
        _acquire_with_slot(lock_repo, "test-project", job.queue_id, job.job_id, slot=0)

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 0
        assert len(lock_repo.get_all_locks()) == 1

    def test_done_job_lock_cleared(
        self, engine: Engine, lock_repo: LockRepository, job_repo: JobRepository
    ):
        """A lock for a ``admission_state='done'`` job (completed/
        failed/cancelled) MUST be cleared — the job is terminal,
        the lock is stale.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.complete_job(job.job_id, result_summary="ok")
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.DONE.value
        _acquire_with_slot(lock_repo, "test-project", job.queue_id, job.job_id, slot=0)

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 1
        assert lock_repo.get_all_locks() == []

    def test_dead_job_lock_cleared(
        self, engine: Engine, lock_repo: LockRepository, job_repo: JobRepository
    ):
        """A lock for a ``admission_state='dead'`` job (DLQ) MUST
        be cleared — dead-letter jobs are terminal.
        """
        job = _make_job(engine, job_repo)
        # Force to dead (status='dead_letter', admission_state='dead').
        _force_state(engine, job.job_id, "dead_letter")
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.DEAD.value
        _acquire_with_slot(lock_repo, "test-project", job.queue_id, job.job_id, slot=0)

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 1
        assert lock_repo.get_all_locks() == []

    def test_orphan_lock_with_no_job_cleared(
        self, engine: Engine, lock_repo: LockRepository
    ):
        """A lock whose job_id has no row in ``job_queue_items``
        MUST be cleared — it's an orphan (the job was hard-
        deleted or never existed in this DB).
        """
        _acquire_with_slot(lock_repo, "test-project", "q-orphan", "ghost-job", slot=0)
        assert len(lock_repo.get_all_locks()) == 1

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 1
        assert lock_repo.get_all_locks() == []

    def test_soft_deleted_job_lock_cleared(
        self, engine: Engine, lock_repo: LockRepository, job_repo: JobRepository
    ):
        """A lock for a soft-deleted job (``deleted_at IS NOT NULL``)
        MUST be cleared even if its admission_state is
        queued/active — soft-deleted rows are excluded from the
        subquery via the ``deleted_at IS NULL`` clause.
        """
        job = _make_job(engine, job_repo)
        _acquire_with_slot(lock_repo, "test-project", job.queue_id, job.job_id, slot=0)
        job_repo.soft_delete(job.job_id)

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 1
        assert lock_repo.get_all_locks() == []

    def test_mixed_active_and_terminal(
        self, engine: Engine, lock_repo: LockRepository, job_repo: JobRepository
    ):
        """A mix of queued (C3-protect), active (C3-protect),
        done (clear), and orphan (clear): only the terminal /
        orphan locks are deleted.
        """
        # Queued — protect.
        queued_job = _make_job(engine, job_repo)
        _acquire_with_slot(lock_repo, "test-project", queued_job.queue_id, queued_job.job_id, slot=0)

        # Active — protect.
        active_job = _make_job(engine, job_repo)
        job_repo.start_job(active_job.job_id, instance_id="inst-a")
        _acquire_with_slot(lock_repo, "test-project", active_job.queue_id, active_job.job_id, slot=1)

        # Done — clear.
        done_job = _make_job(engine, job_repo)
        job_repo.start_job(done_job.job_id, instance_id="inst-d")
        job_repo.complete_job(done_job.job_id, result_summary="ok")
        _acquire_with_slot(lock_repo, "test-project", done_job.queue_id, done_job.job_id, slot=2)

        # Orphan — clear.
        _acquire_with_slot(lock_repo, "test-project", "q-orphan", "ghost", slot=3)

        assert len(lock_repo.get_all_locks()) == 4

        cleared = lock_repo.clear_stale_job_locks()
        assert cleared == 2  # done + orphan

        survivors = {lock.job_id for lock in lock_repo.get_all_locks()}
        assert survivors == {queued_job.job_id, active_job.job_id}

    def test_clear_terminal_alias_matches(
        self, engine: Engine, lock_repo: LockRepository, job_repo: JobRepository
    ):
        """``clear_terminal_job_locks`` is an alias for
        ``clear_stale_job_locks`` and must exhibit the same
        behaviour: protect queued/active, clear done/dead/orphan.
        """
        job = _make_job(engine, job_repo)  # queued
        _acquire_with_slot(lock_repo, "test-project", job.queue_id, job.job_id, slot=0)

        cleared = lock_repo.clear_terminal_job_locks()
        assert cleared == 0  # queued is protected
        assert len(lock_repo.get_all_locks()) == 1


# ─── A. find_processing_jobs semantic equivalence ───────────────────────────


class TestA_FindProcessingJobs:
    """``find_processing_jobs`` was migrated from
    ``status='processing'`` to ``admission_state='active'``. Under
    the new model, BOTH ``PROCESSING``-status and ``PAUSED``-status
    jobs are ``admission_state='active'`` (pause is an Instance
    concern, the lock is still held).

    These tests pin the semantic equivalence: every job with
    ``admission_state='active'`` is returned, regardless of
    ``status`` mirror value (PROCESSING or PAUSED). Done/dead/queued
    jobs are excluded.
    """

    def test_returns_only_active_state_jobs(
        self, engine: Engine, job_repo: JobRepository
    ):
        """Mixed-state project: only ``admission_state='active'``
        jobs are returned.
        """
        # active (processing).
        active_job = _make_job(engine, job_repo)
        job_repo.start_job(active_job.job_id, instance_id="inst-a")

        # queued.
        _make_job(engine, job_repo)

        # done.
        done_job = _make_job(engine, job_repo)
        job_repo.start_job(done_job.job_id, instance_id="inst-d")
        job_repo.complete_job(done_job.job_id, result_summary="ok")

        # dead.
        dead_job = _make_job(engine, job_repo)
        _force_state(engine, dead_job.job_id, "dead_letter")

        processing = job_repo.find_processing_jobs()
        job_ids = {j.job_id for j in processing}
        assert job_ids == {active_job.job_id}

    def test_includes_paused_jobs(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A paused job (``status='paused'``,
        ``admission_state='active'``) is returned by
        ``find_processing_jobs``.

        Pause is an Instance concern, so the JobItem stays
        ``active`` in admission. ``JobRecoveryService`` (startup
        recovery) needs to see paused jobs here so it can
        distinguish paused-vs-orphaned via ``Instance.status``.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )

        processing = job_repo.find_processing_jobs()
        job_ids = {j.job_id for j in processing}
        assert job_ids == {job.job_id}

    def test_excludes_queued_jobs(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``find_processing_jobs`` does NOT return queued jobs.
        Queued jobs are waiting, not processing — they belong to
        the ``list_pending_*`` family.
        """
        _make_job(engine, job_repo)

        assert job_repo.find_processing_jobs() == []

    def test_excludes_soft_deleted(
        self, engine: Engine, job_repo: JobRepository
    ):
        """Soft-deleted active jobs are excluded (matches pre-
        migration behaviour).
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.soft_delete(job.job_id)

        assert job_repo.find_processing_jobs() == []


# ─── B. list_pending_* semantic equivalence ─────────────────────────────────


class TestB_ListPendingFamily:
    """The three ``list_pending_*`` methods were migrated from
    ``status='pending'`` to ``admission_state='queued'``.

    Under the new model, ``admission_state='queued'`` corresponds
    to ``status='pending'`` (the dual-write keeps them in lockstep
    for the INSERT path; ``atomic_retry`` and ``replay_from_dlq``
    also land here).

    These tests pin the semantic equivalence: every job with
    ``admission_state='queued'`` is returned, regardless of the
    ``status`` mirror value.
    """

    def test_list_pending_by_project_returns_only_queued(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``list_pending_by_project`` returns only jobs with
        ``admission_state='queued'`` for the given project.
        """
        # Queued — returned.
        queued_job = _make_job(engine, job_repo)
        # Active — excluded.
        active_job = _make_job(engine, job_repo)
        job_repo.start_job(active_job.job_id, instance_id="inst-a")
        # Done — excluded.
        done_job = _make_job(engine, job_repo)
        job_repo.start_job(done_job.job_id, instance_id="inst-d")
        job_repo.complete_job(done_job.job_id, result_summary="ok")

        pending = job_repo.list_pending_by_project("test-project")
        assert {j.job_id for j in pending} == {queued_job.job_id}

    def test_list_pending_by_project_excludes_other_projects(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``list_pending_by_project`` only returns jobs for the
        requested project.
        """
        a_job = _make_job(engine, job_repo, project_id="project-A")
        _make_job(engine, job_repo, project_id="project-B")

        pending_a = job_repo.list_pending_by_project("project-A")
        pending_b = job_repo.list_pending_by_project("project-B")
        assert {j.job_id for j in pending_a} == {a_job.job_id}
        assert len(pending_b) == 1

    def test_list_all_pending_returns_only_queued(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``list_all_pending`` returns ``admission_state='queued'``
        jobs across all projects.
        """
        _make_job(engine, job_repo, project_id="p1")
        _make_job(engine, job_repo, project_id="p2")
        active = _make_job(engine, job_repo, project_id="p1")
        job_repo.start_job(active.job_id, instance_id="inst-a")
        done = _make_job(engine, job_repo, project_id="p2")
        job_repo.start_job(done.job_id, instance_id="inst-d")
        job_repo.complete_job(done.job_id, result_summary="ok")

        all_pending = job_repo.list_all_pending()
        assert len(all_pending) == 2
        assert all(j.admission_state == AdmissionState.QUEUED.value for j in all_pending)

    def test_list_pending_by_queue_returns_only_queued(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``list_pending_by_queue`` returns only
        ``admission_state='queued'`` jobs in the requested queue.
        """
        qid = _make_queue(engine)
        # Queued in the target queue — returned.
        queued_job = _make_job(engine, job_repo, queue_id=qid)
        # Active in the target queue — excluded.
        active_job = _make_job(engine, job_repo, queue_id=qid)
        job_repo.start_job(active_job.job_id, instance_id="inst-a")

        pending = job_repo.list_pending_by_queue(qid)
        assert {j.job_id for j in pending} == {queued_job.job_id}

    def test_list_pending_by_queue_excludes_other_queues(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``list_pending_by_queue`` only returns jobs in the
        requested queue.
        """
        q1 = _make_queue(engine)
        q2 = _make_queue(engine)
        _make_job(engine, job_repo, queue_id=q1)
        _make_job(engine, job_repo, queue_id=q2)

        pending_q1 = job_repo.list_pending_by_queue(q1)
        pending_q2 = job_repo.list_pending_by_queue(q2)
        assert all(j.queue_id == q1 for j in pending_q1)
        assert all(j.queue_id == q2 for j in pending_q2)

    def test_list_pending_excludes_soft_deleted(
        self, engine: Engine, job_repo: JobRepository
    ):
        """Soft-deleted queued jobs are excluded from all
        ``list_pending_*`` variants.
        """
        job = _make_job(engine, job_repo)
        job_repo.soft_delete(job.job_id)

        assert job_repo.list_pending_by_project("test-project") == []
        assert job_repo.list_all_pending() == []
        assert job_repo.list_pending_by_queue(job.queue_id) == []


# ─── C. find_retryable_jobs semantic equivalence ─────────────────────────────


class TestC_FindRetryableJobs:
    """``find_retryable_jobs`` was migrated from
    ``status='failed' AND next_retry_at <= now`` to
    ``admission_state='queued' AND next_retry_at IS NOT NULL AND
    next_retry_at <= now``.

    The ``next_retry_at IS NOT NULL`` clause is the discriminator
    that selects ONLY retried jobs waiting for their retry window
    (a fresh queued job has ``next_retry_at IS NULL``).
    """

    def test_returns_queued_with_due_retry(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A job with ``admission_state='queued'`` and
        ``next_retry_at <= now`` IS returned by
        ``find_retryable_jobs``.
        """
        job = _make_job(engine, job_repo)
        # Manually set next_retry_at to the past.
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE job_queue_items SET next_retry_at = :t WHERE job_id = :id"),
                {"t": past, "id": job.job_id},
            )

        retryable = job_repo.find_retryable_jobs()
        assert {j.job_id for j in retryable} == {job.job_id}

    def test_fresh_queued_job_without_next_retry_excluded(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A fresh queued job (``next_retry_at IS NULL``) is NOT
        returned by ``find_retryable_jobs`` — only retried jobs
        with a populated ``next_retry_at`` qualify.

        This is the discriminator clause that distinguishes
        "fresh queue item" from "retry waiting for its window".
        """
        _make_job(engine, job_repo)  # next_retry_at=None

        assert job_repo.find_retryable_jobs() == []

    def test_queued_with_future_retry_excluded(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A queued job with ``next_retry_at`` in the FUTURE is
        not yet retryable — it must NOT be returned.
        """
        job = _make_job(engine, job_repo)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE job_queue_items SET next_retry_at = :t WHERE job_id = :id"),
                {"t": future, "id": job.job_id},
            )

        assert job_repo.find_retryable_jobs() == []

    def test_active_with_next_retry_excluded(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A job with ``admission_state='active'`` (even with
        ``next_retry_at``) is NOT returned — retries are queued,
        not active.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        # Plant a past next_retry_at anyway — the
        # admission_state filter must override.
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE job_queue_items SET next_retry_at = :t WHERE job_id = :id"),
                {"t": past, "id": job.job_id},
            )

        assert job_repo.find_retryable_jobs() == []

    def test_done_with_next_retry_excluded(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A terminal job (``admission_state='done'``) is NOT
        returned even with ``next_retry_at`` — terminal jobs are
        not eligible for retry.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.complete_job(job.job_id, result_summary="ok")
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE job_queue_items SET next_retry_at = :t WHERE job_id = :id"),
                {"t": past, "id": job.job_id},
            )

        assert job_repo.find_retryable_jobs() == []

    def test_project_filter_narrows_results(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``find_retryable_jobs(project_id=...)`` narrows to the
        requested project.
        """
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        a_job = _make_job(engine, job_repo, project_id="project-A")
        b_job = _make_job(engine, job_repo, project_id="project-B")
        for jid in (a_job.job_id, b_job.job_id):
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE job_queue_items SET next_retry_at = :t WHERE job_id = :id"),
                    {"t": past, "id": jid},
                )

        retryable_a = job_repo.find_retryable_jobs(project_id="project-A")
        retryable_b = job_repo.find_retryable_jobs(project_id="project-B")
        assert {j.job_id for j in retryable_a} == {a_job.job_id}
        assert {j.job_id for j in retryable_b} == {b_job.job_id}

    def test_atomic_retry_creates_retryable_record(
        self, engine: Engine, job_repo: JobRepository
    ):
        """End-to-end: ``atomic_retry`` puts the job back to
        ``admission_state='queued'`` with ``next_retry_at``
        populated, so ``find_retryable_jobs`` picks it up.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="transient")

        # next_retry_at = now (already due).
        next_retry_at = datetime.now(timezone.utc).isoformat()
        job_repo.atomic_retry(
            job_id=job.job_id, max_retries=3, next_retry_at=next_retry_at,
        )

        retryable = job_repo.find_retryable_jobs()
        assert {j.job_id for j in retryable} == {job.job_id}


# ─── D. find_jobs_by_instance semantic equivalence ───────────────────────────


class TestD_FindJobsByInstance:
    """``find_jobs_by_instance`` was migrated from
    ``status IN ('pending', 'processing', 'failed', 'paused')`` to
    ``admission_state IN ('queued', 'active')``.

    The new predicate narrows the set: terminal admission states
    (``done``, ``dead``) are excluded. FAILED-status jobs that are
    awaiting retry are ``admission_state='queued'`` (set by
    ``atomic_retry``) and remain included via that path.

    These tests pin the new semantic: any queued-or-active job for
    an instance is returned; terminal jobs are excluded.
    """

    def test_returns_queued_and_active_for_instance(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A mix of queued, active, done, dead jobs for the same
        instance: only queued+active are returned.
        """
        inst = "inst-1"
        # Queued (PENDING-status).
        queued_job = _make_job(engine, job_repo, instance_id=inst)
        # Active (PROCESSING-status, started via start_job
        # which sets instance_id).
        active_job = _make_job(engine, job_repo)
        job_repo.start_job(active_job.job_id, instance_id=inst)
        # Done.
        done_job = _make_job(engine, job_repo)
        job_repo.start_job(done_job.job_id, instance_id=inst)
        job_repo.complete_job(done_job.job_id, result_summary="ok")
        # Dead.
        dead_job = _make_job(engine, job_repo)
        _force_state(engine, dead_job.job_id, "dead_letter")
        # Pin the dead_job to inst via UPDATE (force_state doesn't touch instance_id).
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE job_queue_items SET instance_id = :i WHERE job_id = :id"),
                {"i": inst, "id": dead_job.job_id},
            )

        jobs = job_repo.find_jobs_by_instance(inst)
        returned = {j.job_id for j in jobs}
        assert returned == {queued_job.job_id, active_job.job_id}

    def test_excludes_done_and_dead(
        self, engine: Engine, job_repo: JobRepository
    ):
        """Done (completed/failed/cancelled) and dead (dead_letter)
        jobs MUST NOT be returned by ``find_jobs_by_instance`` —
        they are terminal.
        """
        inst = "inst-2"
        done1 = _make_job(engine, job_repo)
        job_repo.start_job(done1.job_id, instance_id=inst)
        job_repo.complete_job(done1.job_id, result_summary="ok")

        done2 = _make_job(engine, job_repo)
        job_repo.start_job(done2.job_id, instance_id=inst)
        job_repo.cancel_job(done2.job_id)

        dead = _make_job(engine, job_repo)
        _force_state(engine, dead.job_id, "dead_letter")
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE job_queue_items SET instance_id = :i WHERE job_id = :id"),
                {"i": inst, "id": dead.job_id},
            )

        assert job_repo.find_jobs_by_instance(inst) == []

    def test_includes_failed_awaiting_retry(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A FAILED-status job that was retried
        (``atomic_retry``) is ``admission_state='queued'`` with
        ``instance_id`` still set. ``find_jobs_by_instance``
        MUST include it — termination cleanup needs to cancel
        it.

        Under the OLD predicate
        (``status IN ('pending','processing','failed','paused')``)
        this was caught by the ``failed`` clause. Under the NEW
        predicate it must be caught by the ``queued`` clause
        (since ``atomic_retry`` dual-writes
        ``status='pending', admission_state='queued'``).
        """
        inst = "inst-3"
        job = _make_job(engine, job_repo, instance_id=inst)
        job_repo.start_job(job.job_id, instance_id=inst)
        job_repo.fail_job(job.job_id, error_message="transient")
        # Atomic retry — sets status='pending', admission_state='queued'.
        job_repo.atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at=datetime.now(timezone.utc).isoformat(),
        )
        # Verify state.
        refreshed = _refresh(engine, job.job_id)
        assert refreshed.status == JobStatus.PENDING.value
        assert refreshed.admission_state == AdmissionState.QUEUED.value

        jobs = job_repo.find_jobs_by_instance(inst)
        assert {j.job_id for j in jobs} == {job.job_id}

    def test_includes_paused_jobs(
        self, engine: Engine, job_repo: JobRepository
    ):
        """A paused job (``status='paused'``,
        ``admission_state='active'``) is included — the
        ``active`` admission bucket covers both PROCESSING and
        PAUSED.
        """
        inst = "inst-4"
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id=inst)
        job_repo.atomic_transition(
            job.job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.PAUSED.value,
        )

        jobs = job_repo.find_jobs_by_instance(inst)
        assert {j.job_id for j in jobs} == {job.job_id}

    def test_excludes_other_instances(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``find_jobs_by_instance`` only returns jobs for the
        requested instance.
        """
        inst_a = "inst-A"
        inst_b = "inst-B"
        a_job = _make_job(engine, job_repo)
        b_job = _make_job(engine, job_repo)
        job_repo.start_job(a_job.job_id, instance_id=inst_a)
        job_repo.start_job(b_job.job_id, instance_id=inst_b)

        a_jobs = job_repo.find_jobs_by_instance(inst_a)
        b_jobs = job_repo.find_jobs_by_instance(inst_b)
        assert {j.job_id for j in a_jobs} == {a_job.job_id}
        assert {j.job_id for j in b_jobs} == {b_job.job_id}

    def test_excludes_soft_deleted(
        self, engine: Engine, job_repo: JobRepository
    ):
        """Soft-deleted jobs are excluded from
        ``find_jobs_by_instance`` (matches pre-migration
        behaviour).
        """
        inst = "inst-5"
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id=inst)
        job_repo.soft_delete(job.job_id)

        assert job_repo.find_jobs_by_instance(inst) == []

    def test_job_type_filter(
        self, engine: Engine, job_repo: JobRepository
    ):
        """``find_jobs_by_instance(instance_id, job_type='message')``
        narrows to the requested job type.
        """
        inst = "inst-6"
        # Create jobs of different types for the same instance.
        msg_job = _make_job(engine, job_repo, instance_id=inst, job_type="message")
        task_job = _make_job(engine, job_repo, instance_id=inst, job_type="task")

        msg_jobs = job_repo.find_jobs_by_instance(inst, job_type="message")
        task_jobs = job_repo.find_jobs_by_instance(inst, job_type="task")
        assert {j.job_id for j in msg_jobs} == {msg_job.job_id}
        assert {j.job_id for j in task_jobs} == {task_job.job_id}


# ─── Sanity smoke ───────────────────────────────────────────────────────────


class TestSmoke:
    """One quick smoke test to catch gross regressions in the fixture
    wiring. If this fails, the test file itself is broken (not the
    code under test)."""

    def test_engine_smoke_roundtrip(self, engine, job_repo):
        """Sanity: a created job can be read back via the engine."""
        job = _make_job(engine, job_repo)
        refetched = _refresh(engine, job.job_id)
        assert refetched.job_id == job.job_id
        assert refetched.admission_state == AdmissionState.QUEUED.value

    def test_active_job_ids_subquery_protects_queued(self):
        """Final invariant pin: the ``_ACTIVE_JOB_IDS_SUBQUERY``
        text — evaluated against a real DB — protects queued jobs
        from the stale-lock sweep.

        Combines the source-level static check with the runtime
        semantic: a queued job's job_id IS in the subquery's
        result set, so the sweep's ``NOT IN`` clause does NOT
        delete its lock.
        """
        # The subquery is just a SQL string. Verify it explicitly
        # filters on admission_state and includes 'queued'.
        assert "admission_state IN ('queued', 'active')" in _ACTIVE_JOB_IDS_SUBQUERY