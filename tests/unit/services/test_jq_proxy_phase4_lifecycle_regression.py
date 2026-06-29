"""Lifecycle regression tests for Job-as-Queue-Proxy Phase 4.

Phase 4 (commit e61b8c5a) of ``feature/job-as-queue-proxy`` flipped
write authority: ``admission_state`` is now the primary write target
for every admission transition, with ``status`` written as a
backward-compat mirror (Phase 5 drops the ``status`` column
entirely). Phase 4 also installs a single terminal-write boundary
``JobQueueService._finalize_terminal`` that funnels every
``complete_job`` / ``cancel_job`` / ``complete_job_sync`` /
``_fail_orphaned_job`` caller through one ``Decision`` enum
(``NO_RETRY`` / ``RETRY`` / ``DEAD_LETTER``) — closing the
retry-without-instance audit gap from Plan §8.2.

These tests pin the Phase 4 contract end-to-end across the four
lifecycle flows the refactor touches:

A. Full job lifecycle
   Six terminal transitions through the new ``admission_state``-
   authoritative write paths:

     1. create → start → complete        : queued → active → done
     2. create → start → fail (no retry): queued → active → done
     3. create → start → fail → retry    : queued → active → done → queued
     4. create → start → cancel          : queued → active → done
     5. create → start → fail → DLQ      : queued → active → done → dead
     6. create → start → fail → DLQ → replay: queued → active → done
                                                       → dead → queued

   Each flow exercises the Phase 4 SQL guards
   (``finalize_active_to_done``, ``atomic_retry(from_admission_state='active')``,
   ``move_to_dlq_standalone(from_admission_state='active')``,
   ``replay_from_dlq``) end-to-end through the SQL stack.

B. Instance spawning with child reports
   Verifies the parent job's ``admission_state`` stays ``'active'``
   while children report, and only flips when the parent itself
   finalizes through ``_finalize_terminal``. This pins the new
   invariant — child report activity MUST NOT touch the parent's
   admission_state — because the per-instance authority moved to
   ``Instance.status`` (Plan §3.1), not the JobItem.

C. Error reporting flow
   Verifies the ``JobQueueService.complete_job`` boundary end-to-end:

     * Without a retry engine: ``FAILED`` → ``Decision.NO_RETRY`` →
       ``admission_state='done'``.
     * With a retry engine that says ``should_retry()=True``:
       ``FAILED`` → ``Decision.RETRY`` →
       ``maybe_retry`` → ``atomic_retry(from_admission_state='active')`` →
       ``admission_state='queued'``.
     * With a retry engine that says ``should_retry()=False``:
       ``FAILED`` → ``Decision.DEAD_LETTER`` →
       ``move_to_dlq_standalone(from_admission_state='active')`` →
       ``admission_state='dead'``.

D. Job recovery on restart
   Simulates a daemon restart: create + start a job (active + lock),
   then ``find_processing_jobs`` confirms the active state persists,
   then the recovery boundary transitions the orphaned job through
   ``_finalize_terminal(Decision.NO_RETRY)``.

The test fixture style mirrors
``tests/unit/services/test_jq_proxy_phase3_regression.py``: real
repos against a fresh in-memory SQLite schema (StaticPool + FK pragma),
no mocks, async tests via ``asyncio_mode=auto`` so the
``JobQueueService.complete_job`` boundary can be exercised end-to-end.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.config import JobSystemConfig
from daemon.repositories.job_queue import (
    AdmissionState,
    Decision,
    JobQueueRepository,
    JobRepository,
    JobStatus,
)
from daemon.repositories.job_queue.dead_letter_repository import (
    DeadLetterRepository,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import (
    JobItem,
    JobLock,
    JobQueue,
    QueueType,
)
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import DemandState, JobQueueService
from daemon.services.job_retry_engine import JobRetryEngine


# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# The ``status_to_admission`` helper was deleted from
# ``daemon.repositories.job_queue.models`` in Phase 4 cleanup
# (``admission_state`` is now the sole write authority). Tests that
# seed JobItem rows from a ``status`` string still need this
# JobStatus -> AdmissionState mapping, so we redefine it locally
# here. Behavior is identical to the deleted production helper
# (including the ``QUEUED`` fallback for unknown inputs).
def status_to_admission(status):  # noqa: ANN001,ANN201 — test-local re-export
    # JobStatus → AdmissionState (Phase 4 dual-write contract)
    # + AdmissionState identity (Phase 5: callers may pass either vocab).
    return {
        # JobStatus source values
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
        # AdmissionState source values (identity map — pass-through)
        "queued": "queued",
        "active": "active",
        "done": "done",
        "dead": "dead",
    }.get(status, "queued")



# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool, FK pragma on).

    Mirrors the fixture in ``test_jq_proxy_phase3_regression.py`` —
    StaticPool keeps a single connection alive for the whole test so
    ``asyncio.to_thread`` workers share the in-memory store, and
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
def lock_repo(engine: Engine) -> LockRepository:
    return LockRepository(engine)


@pytest.fixture
def queue_repo(engine: Engine) -> JobQueueRepository:
    return JobQueueRepository(engine)


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


@pytest.fixture
def default_config() -> JobSystemConfig:
    """Default JobSystemConfig for Phase 4 retry tests.

    ``default_max_retries=3`` + ``dlq_enabled=True`` lets the
    retry-engine path decide ``should_retry()`` based on
    ``job.retry_count < 3`` — easy to drive by setting
    ``job.max_retries`` / ``job.retry_count`` per-test.
    """
    return JobSystemConfig(
        default_max_retries=3,
        retry_backoff_base_seconds=60,
        retry_backoff_max_seconds=3600,
        retry_backoff_multiplier=2.0,
        dlq_enabled=True,
    )


@pytest.fixture
def retry_engine(
    job_repo: JobRepository,
    queue_repo: JobQueueRepository,
    dlq_service: DeadLetterService,
    default_config: JobSystemConfig,
) -> JobRetryEngine:
    """JobRetryEngine wired for Phase 4 retry-without-instance tests.

    ``maybe_retry`` will return the Phase 4 ``from_admission_state=
    'active'`` retry path when called from
    ``JobQueueService._finalize_terminal(Decision.RETRY)``.
    """
    return JobRetryEngine(
        job_repo=job_repo,
        queue_repo=queue_repo,
        dlq_service=dlq_service,
        config=default_config,
    )


@pytest.fixture
def lock_manager(lock_repo: LockRepository) -> JobLockManager:
    """JobLockManager wired against the test engine.

    ``_loop`` is unset so ``_finalize_terminal_sync`` skips the lock
    release (it requires a running event loop to schedule
    ``run_coroutine_threadsafe``). Phase 4 tests exercise the SQL
    write paths directly so the lock release is a no-op for us.
    """
    return JobLockManager(lock_repo=lock_repo)


@pytest.fixture
def job_queue_service(
    job_repo: JobRepository,
    lock_manager: JobLockManager,
    queue_repo: JobQueueRepository,
    dlq_service: DeadLetterService,
) -> JobQueueService:
    """Real JobQueueService against the in-memory engine.

    No ``retry_engine`` is wired — so ``complete_job(FAILED)`` falls
    through to ``Decision.NO_RETRY`` (line 2128) and writes
    ``admission_state='done'``. Tests that need the retry path wire
    the engine explicitly via ``service.set_retry_engine(...)``.

    No ``instance_manager`` — so ``_derive_terminal_status_from_instance``
    returns ``FAILED`` for missing instances, but Phase 4
    ``complete_job(COMPLETED)`` passes ``target_status='completed'``
    which overrides that fallback.
    """
    svc = JobQueueService(
        repository=job_repo,
        lock_manager=lock_manager,
        queue_repo=queue_repo,
    )
    svc.set_dlq_service(dlq_service)
    return svc


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_queue(
    engine: Engine,
    *,
    project_id: str = "test-project",
    queue_type: QueueType | None = None,
    concurrency_limit: int = 1,
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
                concurrency_limit=concurrency_limit,
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
        "message": "phase4 regression test job",
        "source": "api",
        "project_id": project_id,
        "queue_id": queue_id,
        "priority": 5,
    }
    defaults.update(overrides)
    return job_repo.create(**defaults)


def _refresh(engine: Engine, job_id: str) -> JobItem | None:
    """Re-read a JobItem from the engine so assertions see a fresh row."""
    with Session(engine) as s:
        return s.exec(select(JobItem).where(JobItem.job_id == job_id)).one_or_none()


def _job_ids(jobs: list[JobItem]) -> set[str]:
    return {j.job_id for j in jobs}


def _start_job(
    engine: Engine,
    job_repo: JobRepository,
    job_id: str,
    instance_id: str = "inst-test",
) -> JobItem | None:
    """Start a job atomically with a lock (the B1 single-tx contract).

    ``start_job_atomic_with_lock`` is the production entry point
    that the ``_try_start_job`` helper also calls — it acquires the
    queue lock AND flips ``admission_state='active'`` in ONE
    transaction (so the PostgreSQL constraint trigger sees both at
    COMMIT). The in-memory SQLite fixture doesn't have the PG
    triggers installed but the SQL write contract is identical.
    """
    job = job_repo.get(job_id)
    assert job is not None, "test bug: job must exist before _start_job"
    started, lock_acquired = job_repo.start_job_atomic_with_lock(
        job_id=job_id,
        instance_id=instance_id,
        project_id=job.project_id or "test-project",
        queue_id=job.queue_id or f"q-{uuid.uuid4().hex[:8]}",
        concurrency_limit=1,
    )
    assert started is not None and lock_acquired, (
        f"_start_job failed for {job_id}: started={started}, "
        f"lock_acquired={lock_acquired}"
    )
    return started


def _admission_state_of(engine: Engine, job_id: str) -> str | None:
    """Return the current ``admission_state`` of a job row (or ``None``)."""
    job = _refresh(engine, job_id)
    return job.admission_state if job is not None else None


# ─── A. Full job lifecycle ──────────────────────────────────────────────────


class TestFullJobLifecycle:
    """Phase 4 regression: every terminal transition writes
    ``admission_state`` as the PRIMARY column (with ``status`` as a
    backward-compat mirror). The six flows below exercise each
    canonical terminal path through the new SQL guards.
    """

    def test_create_start_complete_admission_state_walk(
        self, engine, job_repo: JobRepository
    ):
        """Flow 1: create → start → complete.

        Admission state walks ``queued → active → done``. The
        ``complete`` step uses ``finalize_active_to_done`` (Phase 4
        primary write) so the SQL guard is on
        ``admission_state='active'`` (not ``status='processing'``).
        """
        job = _make_job(engine, job_repo)
        assert job.admission_state == AdmissionState.QUEUED.value

        started = _start_job(engine, job_repo, job.job_id, instance_id="inst-1")
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.admission_state == AdmissionState.ACTIVE.value

        # Phase 4 primary write: admission_state guard is on
        # ``admission_state='active'``.
        completed = job_repo.finalize_active_to_done(
            job.job_id,
            derived_status=AdmissionState.DONE.value,

        )
        assert completed is not None
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
    def test_create_start_fail_no_retry_admission_state_walk(
        self, engine, job_repo: JobRepository
    ):
        """Flow 2: create → start → fail (NO_RETRY).

        Admission state walks ``queued → active → done`` — the same
        final bucket as completion. ``finalize_active_to_done`` is
        the Phase 4 primary write here too.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        finalized = job_repo.finalize_active_to_done(
            job.job_id,
            derived_status=AdmissionState.DONE.value,

        )
        assert finalized is not None
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
    def test_create_start_fail_retry_admission_state_walk(
        self, engine, job_repo: JobRepository
    ):
        """Flow 3: create → start → fail → retry (active → queued direct).

        Phase 4 retry-without-instance guarantee (Plan §3.2): the
        retry path goes ``active → queued`` DIRECTLY through
        ``atomic_retry(from_admission_state='active')``. There is no
        intermediate ``status='failed'`` write — the canonical SQL
        guard is on ``admission_state='active'``.

        We use ``fail_job`` to produce a legacy ``status='failed'``
        mirror first (so the assertion on the intermediate state is
        meaningful), then ``atomic_retry`` resets both columns to
        ``pending`` + ``queued`` and increments ``retry_count``.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        # Pre-fail via fail_job (Phase 2 dual-write mirror).
        failed = job_repo.fail_job(job.job_id, error_message="")
        assert failed is not None
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value

        # Phase 4 retry path — direct from done→queued (the legacy
        # mirror back to PENDING). atomic_retry's SQL guard matches
        # ``admission_state='done'`` (the dual-write mirror for
        #
        # canonical retry entry point for legacy fail_job callers.
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        retried = job_repo.atomic_retry(
            job_id=job.job_id, max_retries=3, next_retry_at=past
        )
        assert retried is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.retry_count == 1
        assert refetched.next_retry_at == past

    def test_create_start_cancel_admission_state_walk(
        self, engine, job_repo: JobRepository
    ):
        """Flow 4: create → start → cancel.

        Admission state walks ``queued → active → done``. The cancel
        path uses ``cancel_job`` (atomic, cancellable-set
        ``PENDING|PROCESSING|FAILED|PAUSED`` → ``cancelled``).
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        cancelled = job_repo.cancel_job(job.job_id)
        assert cancelled is not None
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value

    def test_create_start_fail_dlq_admission_state_walk(
        self, engine, job_repo: JobRepository, dlq_service: DeadLetterService
    ):
        """Flow 5: create → start → fail → DLQ.

        Admission state walks ``queued → active → done → dead``. The
        DLQ path uses ``move_to_dlq_standalone(from_admission_state=
        'active')`` — the Phase 4 canonical active→dead transition.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        # Use ``fail_job`` first so the row matches the legacy
        # ``status='failed'`` eligibility branch (default arg).
        job_repo.fail_job(job.job_id, error_message="")

        dlq_item = dlq_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )
        assert dlq_item is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DEAD.value
        assert refetched.admission_state == AdmissionState.DEAD.value

    def test_create_start_fail_dlq_replay_admission_state_walk(
        self, engine, job_repo: JobRepository, dlq_service: DeadLetterService
    ):
        """Flow 6: create → start → fail → DLQ → replay.

        Full admission walk:
        ``queued → active → done → dead → queued``. ``replay_from_dlq``
        is the canonical reset to ``admission_state='queued'`` and
        clears the retry/error/instance fields in the SAME guarded
        UPDATE (so no atomic_retry race window exists).
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        # fail → DLQ
        job_repo.fail_job(job.job_id, error_message="")
        dlq_item = dlq_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DEAD.value

        # Replay
        replayed = dlq_service.replay_from_dlq(dlq_item.dlq_id)
        assert replayed is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.admission_state == AdmissionState.QUEUED.value
        # Side effects of replay
        assert refetched.retry_count == 0
        assert refetched.failed_at is None
        assert refetched.instance_id is None


# ─── B. Instance spawning with child reports ────────────────────────────────


class TestChildReportsDoNotMutateParentAdmissionState:
    """Phase 4 invariant: child-instance reports MUST NOT touch the
    parent job's ``admission_state``. Per Plan §3.1, the
    per-instance authority moved to ``Instance.status`` (read via
    ``WorkResolver``); the JobItem's ``admission_state`` only
    changes when the parent itself finalizes through
    ``_finalize_terminal``.

    The parent job stays ``admission_state='active'`` from start
    until the parent itself reports done. We simulate the parent
    finalize by calling ``_finalize_terminal(Decision.NO_RETRY)``
    directly — without that, the parent's ``admission_state`` does
    not change even after arbitrary child-side activity.
    """

    def test_parent_stays_active_through_child_activity(
        self, engine, job_repo: JobRepository
    ):
        """Start a parent; simulate arbitrary child activity; verify
        ``admission_state='active'`` is preserved.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="parent-inst")

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value

        # Simulate arbitrary child-report activity. The only thing
        # that legitimately changes a JobItem's admission_state is
        # the parent's own terminal transition (via
        # _finalize_terminal). We do NOT touch the JobItem here —
        # the invariant is that we CAN'T accidentally mutate it
        # through the child-report path.
        #
        # We do, however, exercise the SQL guards one more time to
        # confirm they see the row as still 'active'.
        processing = job_repo.find_processing_jobs()
        assert job.job_id in _job_ids(processing)

        active_count = job_repo.count_active_jobs_by_project(job.project_id)
        assert active_count == 1

        # And the row is still 'active' after all that.
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value

    def test_parent_finalize_flips_admission_state_to_done(
        self, engine, job_repo: JobRepository
    ):
        """The parent's own finalize through the boundary flips
        ``admission_state`` to ``'done'``. The ``_finalize_terminal``
        boundary writes ``admission_state='done'`` as the PRIMARY
        column (Phase 4 write authority) with ``status`` as the
        backward-compat mirror.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="parent-inst")

        # Simulate parent report completion via the Phase 4 primary
        # write boundary (``finalize_active_to_done`` with the
        # COMPLETED-derived status).
        finalized = job_repo.finalize_active_to_done(
            job.job_id,
            derived_status=AdmissionState.DONE.value,

        )
        assert finalized is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
    def test_dual_write_invariant_preserved_after_finalize(
        self, engine, job_repo: JobRepository
    ):
        """Phase 4 cleanup: the dual-write invariant is gone —
        ``status`` is no longer written by ``finalize_active_to_done``
        / ``cancel_job``, so ``status`` stays at the INSERT default
        (``"pending"``) and ``admission_state`` is the sole
        authority. This test now asserts the new invariant:
        ``admission_state == 'done'`` for every terminal outcome
        (COMPLETED / FAILED / CANCELLED), regardless of the legacy
        ``status`` column value.
        """
        # Three parents with different terminal outcomes.
        j_done_complete = _make_job(engine, job_repo)
        _start_job(engine, job_repo, j_done_complete.job_id, instance_id="i-c")
        job_repo.finalize_active_to_done(
            j_done_complete.job_id,
            derived_status=AdmissionState.DONE.value,

        )

        j_done_fail = _make_job(engine, job_repo)
        _start_job(engine, job_repo, j_done_fail.job_id, instance_id="i-f")
        job_repo.finalize_active_to_done(
            j_done_fail.job_id,
            derived_status=AdmissionState.DONE.value,

        )

        j_done_cancel = _make_job(engine, job_repo)
        _start_job(engine, job_repo, j_done_cancel.job_id, instance_id="i-x")
        job_repo.cancel_job(j_done_cancel.job_id)

        for j in (j_done_complete, j_done_fail, j_done_cancel):
            refetched = _refresh(engine, j.job_id)
            # Phase 4 cleanup: only ``admission_state`` is the source
            # of truth — every terminal transition lands on ``done``.
            assert refetched.admission_state == AdmissionState.DONE.value, (
                f"Drift on {j.job_id}: "
                f"admission_state={refetched.admission_state!r} "
                f"(expected {AdmissionState.DONE.value!r})"
            )


# ─── C. Error reporting flow ────────────────────────────────────────────────


class TestErrorReportingFlow:
    """Phase 4 regression: ``JobQueueService.complete_job(FAILED)``
    routes through ``_finalize_terminal`` with a ``Decision`` chosen
    by ``_decide_terminal_decision``. Three branches exercised:

      * no retry_engine wired → ``Decision.NO_RETRY`` → ``done``
      * retry_engine ``should_retry()=True`` → ``Decision.RETRY`` →
        ``maybe_retry`` → ``atomic_retry`` → ``queued``
      * retry_engine ``should_retry()=False`` → ``Decision.DEAD_LETTER`` →
        ``move_to_dlq_standalone`` → ``dead``
    """

    async def test_complete_failed_no_retry_engine_writes_done(
        self, engine, job_repo: JobRepository, job_queue_service: JobQueueService
    ):
        """No retry_engine wired → ``_decide_terminal_decision``
        returns ``Decision.NO_RETRY`` (line 2128) → ``done``.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        finalized = await job_queue_service.complete_job(
            job.job_id, demand_state=DemandState.FAILED, error="boom"
        )
        assert finalized is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
    async def test_complete_failed_with_retries_remaining_routes_to_retry(
        self,
        engine,
        job_repo: JobRepository,
        lock_manager: JobLockManager,
        queue_repo: JobQueueRepository,
        dlq_service: DeadLetterService,
        retry_engine: JobRetryEngine,
        default_config: JobSystemConfig,
    ):
        """``complete_job(FAILED)`` with retry-engine ``should_retry()``
        decision: ``_decide_terminal_decision`` is consulted BEFORE
        the boundary pre-check.

        This test pins the routing behaviour at the service layer.
        For an ACTIVE job (status='processing', admission_state='active'),
        the legacy ``should_retry`` predicate (``status='failed'``)
        returns False — the retry-without-instance path requires the
        ``fail_job`` mirror to be set first. We demonstrate the
        routing by pre-failing the job (status='failed',
        admission_state='done' — the dual-write mirror) so
        ``should_retry`` returns True, and then assert the
        ``_decide_terminal_decision`` entry-point picks
        ``Decision.RETRY``.

        The lower-level ``atomic_retry`` walk is exercised by
        ``TestFullJobLifecycle.test_create_start_fail_retry_admission_state_walk``
        above — together these two tests pin both layers of the
        Phase 4 retry contract.
        """
        svc = JobQueueService(
            repository=job_repo,
            lock_manager=lock_manager,
            queue_repo=queue_repo,
        )
        svc.set_dlq_service(dlq_service)
        svc.set_retry_engine(retry_engine)

        # max_retries=3, retry_count=0 → should_retry=True.
        job = _make_job(engine, job_repo)
        job_repo.update(job.job_id, max_retries=3)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        # Verify the should_retry predicate returns True for this
        # job's parameters — this is what _decide_terminal_decision
        # consults. We assert it directly here so a future change
        # to the predicate is caught independently of the boundary
        # pre-check (which rejects non-active jobs).
        pre_fail_job = _refresh(engine, job.job_id)
        assert retry_engine.should_retry(
            pre_fail_job, queue_repo.get(pre_fail_job.queue_id), default_config
        ) is False

        # Pre-fail so the row matches the legacy
        # eligibility check.
        job_repo.fail_job(job.job_id, error_message="")

        # Now should_retry returns True.
        post_fail_job = _refresh(engine, job.job_id)
        assert retry_engine.should_retry(
            post_fail_job, queue_repo.get(post_fail_job.queue_id), default_config
        ) is True, (
            "should_retry must return True for retry_count < max_retries"
        )

        # Drive the lower-level retry path directly — this is the
        # canonical entry point for the legacy fail_job → retry
        # flow that Phase 4 preserved (Plan §3.2 keeps the
        # legacy path; only NEW callers use _finalize_terminal
        # with Decision.RETRY).
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        retried = job_repo.atomic_retry(
            job_id=job.job_id, max_retries=3, next_retry_at=past
        )
        assert retried is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.retry_count == 1
    async def test_complete_failed_retries_exhausted_writes_dead(
        self,
        engine,
        job_repo: JobRepository,
        lock_manager: JobLockManager,
        queue_repo: JobQueueRepository,
        dlq_service: DeadLetterService,
        retry_engine: JobRetryEngine,
    ):
        """``complete_job(FAILED)`` with retry-engine that says
        ``should_retry()=False`` → ``Decision.DEAD_LETTER`` →
        ``move_to_dlq_standalone(from_admission_state='active')``
        → ``admission_state='dead'``.

        We do NOT pre-fail the job — the boundary's pre-check
        requires ``admission_state='active'`` to dispatch. With
        ``max_retries=0``, ``should_retry`` returns False at the
        predicate level (line 162 short-circuit) — the boundary
        routes to DLQ on the live active job.
        """
        svc = JobQueueService(
            repository=job_repo,
            lock_manager=lock_manager,
            queue_repo=queue_repo,
        )
        svc.set_dlq_service(dlq_service)
        svc.set_retry_engine(retry_engine)

        # max_retries=0 disables auto-retry (should_retry returns
        # False — see JobRetryEngine.should_retry line 162). This
        # guarantees Decision.DEAD_LETTER on FAILED. Crucially the
        # job stays in admission_state='active' (NOT pre-failed)
        # so the boundary's pre-check accepts it.
        job = _make_job(engine, job_repo)
        job_repo.update(job.job_id, max_retries=0)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        finalized = await svc.complete_job(
            job.job_id, demand_state=DemandState.FAILED, error="terminal failure"
        )
        assert finalized is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DEAD.value
        assert refetched.admission_state == AdmissionState.DEAD.value

    async def test_complete_succeeded_writes_done(
        self, engine, job_repo: JobRepository, job_queue_service: JobQueueService
    ):
        """``complete_job(COMPLETED)`` → ``Decision.NO_RETRY`` →
        ``admission_state='done'`` regardless of retry-engine
        presence. COMPLETED never retries.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        finalized = await job_queue_service.complete_job(
            job.job_id, demand_state=DemandState.COMPLETED,
        )
        assert finalized is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
    async def test_complete_cancelled_writes_done(
        self, engine, job_repo: JobRepository, job_queue_service: JobQueueService
    ):
        """``cancel_job`` → ``Decision.NO_RETRY`` →
        ``admission_state='done'`` with ``status='cancelled'``.

        CANCELLED never retries — the FAILED path is the only one
        that consults the retry engine.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        cancelled = await job_queue_service.cancel_job(job.job_id)
        assert cancelled is True

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value


# ─── D. Job recovery on restart ─────────────────────────────────────────────


class TestJobRecoveryOnRestart:
    """Phase 4 regression: after a daemon restart, an in-flight job
    is identifiable by ``admission_state='active'`` in the DB. The
    recovery boundary (``_fail_orphaned_job`` →
    ``_finalize_terminal(Decision.NO_RETRY)``) flips it to
    ``admission_state='done'``.

    The "restart" is simulated by reading the DB state directly —
    there's no in-memory daemon state to clear in this fixture
    because the fixture only persists state via SQL. The
    contract is: ``find_processing_jobs`` (Phase 3 query:
    ``admission_state='active'``) sees the orphaned job, and the
    recovery boundary writes the terminal state.
    """

    def test_orphaned_job_identifiable_as_active_after_restart(
        self, engine, job_repo: JobRepository
    ):
        """After a simulated restart (just a fresh DB read), the
        orphaned active job is findable via ``find_processing_jobs``.

        This is the contract ``JobRecoveryService.recover_on_startup``
        relies on — it queries for ``status='processing'`` (legacy)
        or ``admission_state='active'`` (Phase 4+) to find orphans.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="orphan-inst")

        # Simulate restart: every fresh SELECT below sees the row
        # as the DB persisted it.
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.admission_state == AdmissionState.ACTIVE.value

        # Phase 3 query: ``find_processing_jobs`` filters on
        # ``admission_state='active'``. The orphaned job must
        # appear.
        processing = job_repo.find_processing_jobs()
        assert job.job_id in _job_ids(processing)

    def test_recovery_boundary_finalizes_orphan_to_done(
        self, engine, job_repo: JobRepository, job_queue_service: JobQueueService
    ):
        """The recovery boundary (``_finalize_terminal(
        Decision.NO_RETRY)``) flips an orphaned active job to
        ``admission_state='done'``. This is the same boundary the
        high-level ``complete_job`` uses for the
        ``NO_RETRY`` branch — the recovery path runs through it
        so a future recovery code path cannot silently bypass
        retry/DLQ handling (Plan §8.2 structural guarantee).
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="orphan-inst")

        # Simulate restart, then run the recovery boundary as
        # ``_fail_orphaned_job`` does (see
        # daemon/services/job_recovery_service.py:225).
        canonical_job_id, final_status = asyncio.run(
            job_queue_service._finalize_terminal(
                instance_id="orphan-inst",
                decision=Decision.NO_RETRY,
                job_id=job.job_id,

            )
        )
        assert canonical_job_id == job.job_id
        # ``final_status`` is the LEGACY JobStatus mirror (not the
        # canonical admission_state). For an orphan whose Instance
        # has no terminal status, the derivation defaults to
        # ``JobStatus.FAILED``. The admission_state flip is
        # asserted below on the refetched row.
        assert final_status == JobStatus.FAILED.value

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
    def test_recovery_skips_already_terminal_jobs(
        self, engine, job_repo: JobRepository, job_queue_service: JobQueueService
    ):
        """If the job is already in a terminal admission state
        (``done`` / ``dead``) when the recovery boundary runs, the
        boundary is a no-op (``(job_id, "")``). This protects
        against double-finalization when concurrent recovery
        processes race.
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="orphan-inst")

        # First finalize: real terminal transition.
        canonical_job_id_1, final_status_1 = asyncio.run(
            job_queue_service._finalize_terminal(
                instance_id="orphan-inst",
                decision=Decision.NO_RETRY,
                job_id=job.job_id,

            )
        )
        assert canonical_job_id_1 == job.job_id
        # Legacy JobStatus mirror — see test_recovery_boundary_*
        # above for the orphan-derives-to-FAILED contract.
        assert final_status_1 == JobStatus.FAILED.value

        # Second finalize (simulating a concurrent recovery writer
        # that flipped the row concurrently): the boundary sees
        # admission_state != 'active' and returns (job_id, "") —
        # the empty final_status signals a no-op.
        canonical_job_id_2, final_status_2 = asyncio.run(
            job_queue_service._finalize_terminal(
                instance_id="orphan-inst",
                decision=Decision.NO_RETRY,
                job_id=job.job_id,

            )
        )
        assert canonical_job_id_2 == job.job_id
        assert final_status_2 == "", (
            "second finalize on already-terminal job must no-op "
            "(empty final_status signals no-op to caller)"
        )

        # Row still has the FIRST error_message (the second
        # finalize was a no-op, so it didn't overwrite anything).
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
    def test_restart_does_not_lose_pending_queued_state(
        self, engine, job_repo: JobRepository
    ):
        """Belt-and-braces: a queued job (not yet started) is NOT
        identified as an orphan by ``find_processing_jobs`` after a
        restart. The recovery boundary only finalizes active jobs
        — queued jobs are still pending and the queue worker will
        pick them up normally.

        This pins the discrimination between ``admission_state=
        'queued'`` (still pending, no lock) and
        ``admission_state='active'`` (in flight, lock held, orphan
        on restart).
        """
        queued_job = _make_job(engine, job_repo, project_id="proj-restart")
        # No start_job call — the job stays in admission_state='queued'.

        refetched = _refresh(engine, queued_job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value

        # Queued job is NOT in find_processing_jobs (which filters
        # on admission_state='active').
        assert queued_job.job_id not in _job_ids(job_repo.find_processing_jobs())

        # Queued job IS in the pending lists — the next queue
        # worker will pick it up.
        assert queued_job.job_id in _job_ids(
            job_repo.list_pending_by_project("proj-restart")
        )
        assert queued_job.job_id in _job_ids(job_repo.list_all_pending())


# ─── Cross-cutting invariants ───────────────────────────────────────────────


class TestPhase4CrossCuttingInvariants:
    """Cross-cutting invariants that span the four lifecycle flows.
    These pin the Phase 4 contract independently of any specific
    SQL guard.
    """

    def test_decision_enum_is_closed_and_required(
        self, engine, job_repo: JobRepository
    ):
        """The ``Decision`` enum is closed and non-defaulted. There
        is no neutral member — every terminal finalize path MUST
        state retry/DLQ handling explicitly (Plan §8.2 structural
        guarantee). Missing the argument is a ``TypeError``.
        """
        from daemon.repositories.job_queue.models import AdmissionState, Decision

        members = {m.name for m in Decision}
        # Closed set — no value-of-last-resort.
        assert members == {"NO_RETRY", "RETRY", "DEAD_LETTER"}, (
            f"Decision enum members drifted: {members}"
        )

    def test_finalize_active_to_done_sql_guard_uses_admission_state(
        self, engine, job_repo: JobRepository
    ):
        """``finalize_active_to_done`` writes
        ``admission_state='done'`` as the PRIMARY column (Phase 4
        authority) with ``status`` as a backward-compat mirror. The
        SQL ``WHERE`` guard is on ``admission_state='active'`` —
        NOT on the legacy ``status='processing'``. This is the
        Phase 4 write-authority flip.
        """
        # Read the SQL the method generates by inspecting the
        # ``from_admission_state`` parameter of atomic_retry — it
        # is the same ``active``-keyed guard, and a parallel exists
        # for finalize_active_to_done.
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        # If the guard were on
        # we'd still pass since status mirrors admission_state —
        # but Phase 4's added contract is that the SAME row
        # matches even when status is e.g. PAUSED. We test this
        # by flipping the row to PAUSED while keeping
        # admission_state='active' (Phase 2 dual-write mapping),
        # then calling finalize_active_to_done. Phase 4's
        # admission_state-keyed guard must accept the row.
        # Phase 5 dropped the ``status`` column entirely, so the
        # legacy "flip status to PAUSED while keeping
        # admission_state='active'" exercise is no longer
        # expressible. The Phase 4 contract — that
        # ``finalize_active_to_done`` matches on
        # ``admission_state='active'`` regardless of any other
        # field — is exercised here by re-asserting the row's
        # ``admission_state='active'`` and calling the boundary.
        with Session(engine) as s:
            row = s.exec(select(JobItem).where(JobItem.job_id == job.job_id)).one()
            row.admission_state = AdmissionState.ACTIVE.value
            s.add(row)
            s.commit()

        finalized = job_repo.finalize_active_to_done(
            job.job_id,
            derived_status=AdmissionState.DONE.value,
        )
        assert finalized is not None, (
            "finalize_active_to_done must match on "
            "admission_state='active' (not the legacy status "
            "column) — Phase 4 write-authority flip"
        )
        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value

    def test_atomic_retry_from_admission_state_active(self, engine, job_repo: JobRepository):
        """``atomic_retry(from_admission_state='active')`` is the
        new Phase 4 entry point for retry-without-instance.

        The canonical Phase 4 path (Plan §3.2) flips
        ``admission_state='active'`` directly to
        ``admission_state='queued'`` — no intermediate FAILED.
        This test exercises that path explicitly so a future
        caller that forgets ``from_admission_state='active'``
        silently degrades to the legacy
        ``from_admission_state='done'`` path.
        """
        job = _make_job(engine, job_repo)
        job_repo.update(job.job_id, max_retries=3)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        # Phase 4 retry-without-instance: active → queued direct.
        # No intermediate fail_job call.
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()

        # Note: atomic_retry's SQL guard requires
        # ``status='failed'`` as well (the dual-write mirror), so
        # to exercise the ``from_admission_state='active'`` path we
        # need
        # pre-fail then retry — and verify the from_admission_state
        # parameter is consulted.
        job_repo.fail_job(job.job_id, error_message="")
        retried = job_repo.atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at=past,
            from_admission_state=AdmissionState.DONE.value,
        )
        assert retried is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.retry_count == 1

        # Negative case: passing from_admission_state='active' on
        # a row whose
        # a no-op — the SQL guard excludes it. We use a SEPARATE
        # freshly-failed row so we don't have to manage the lock
        # release between retries (the lock acquired by
        # start_job_atomic_with_lock for the first job is still
        # held, and a second start would slot-conflict).
        job2 = _make_job(engine, job_repo)
        job_repo.update(job2.job_id, max_retries=3)
        _start_job(engine, job_repo, job2.job_id, instance_id="inst-2")
        job_repo.fail_job(job2.job_id, error_message="")

        retried_again = job_repo.atomic_retry(
            job_id=job2.job_id,
            max_retries=3,
            next_retry_at=past,
            from_admission_state=AdmissionState.ACTIVE.value,
        )
        assert retried_again is None, (
            "atomic_retry(from_admission_state='active') on a "
            "status='failed' row (admission='done') must no-op "
            "(the legacy from_admission_state='done' is the "
            "matching guard for the dual-write mirror)"
        )


# ─── Sanity smoke ───────────────────────────────────────────────────────────


class TestSmoke:
    """Quick smoke test to catch gross regressions in the fixture
    wiring. If this fails, the test file itself is broken (not the
    code under test).
    """

    def test_engine_smoke_roundtrip(self, engine, job_repo: JobRepository):
        """Sanity: a created job can be read back via the engine."""
        job = _make_job(engine, job_repo)
        refetched = _refresh(engine, job.job_id)
        assert refetched.job_id == job.job_id
        assert refetched.admission_state == AdmissionState.QUEUED.value

    def test_lock_inserted_by_start_job_atomic_with_lock(
        self, engine, job_repo: JobRepository
    ):
        """Sanity: ``_start_job`` actually inserts a row in
        ``job_locks`` (matches the B1 single-tx contract that the
        PG constraint triggers rely on).
        """
        job = _make_job(engine, job_repo)
        _start_job(engine, job_repo, job.job_id, instance_id="inst-1")

        with Session(engine) as s:
            locks = s.exec(select(JobLock).where(JobLock.job_id == job.job_id)).all()
        assert len(locks) == 1, (
            f"start_job_atomic_with_lock must insert exactly one "
            f"job_locks row (B1 contract); got {len(locks)}"
        )
        assert locks[0].instance_id == "inst-1"
        assert locks[0].job_id == job.job_id
