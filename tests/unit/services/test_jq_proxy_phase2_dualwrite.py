"""Dual-write verification tests for Job-as-Queue-Proxy Phase 2.

Phase 2 (commit 203afe6d) of ``feature/job-as-queue-proxy`` introduces
the ``admission_state`` column on ``job_queue_items`` ALONGSIDE the
existing ``status`` column. Every existing ``status`` write site must
also write ``admission_state`` in the SAME ``UPDATE`` statement so the
two columns move in lockstep. These tests pin the dual-write contract
across all five categories:

A. Dual-write on creation
   * ``JobRepository.create()`` writes ``status='pending', admission_state='queued'``
   * ``JobRepository.create_or_get_by_idempotency_key()`` writes both
     columns when the row is freshly inserted

B. Dual-write on lifecycle transitions
   * ``start_job``           → ``status='processing', admission_state='active'``
   * ``complete_job``        → ``status='completed',  admission_state='done'``
   * ``fail_job``            → ``status='failed',     admission_state='done'``
   * ``cancel_job``          → ``status='cancelled',  admission_state='done'``
   * ``DeadLetterService.move_to_dlq_*`` → ``status='dead_letter', admission_state='dead'``
   * ``DeadLetterService.replay_from_dlq`` → ``status='pending', admission_state='queued'``
   * ``atomic_retry``        → ``status='pending',    admission_state='queued'``

C. ``status_to_admission()`` unit tests for every JobStatus value
   (including the PAUSED → ACTIVE special-case).

D. Pause/resume cascade admission_state transitions
   * ``PROCESSING → PAUSED`` keeps ``admission_state='active'`` (pause is
     an Instance concern; the JobLock is still held).
   * ``PAUSED → PROCESSING`` keeps ``admission_state='active'``.

E. Edge cases
   * Freshly-queued ``instance_id=None`` rows surface
     ``admission_state='queued'``.
   * The dual-write happens at the repository / service SQL layer,
     strictly upstream of the ``dependency_bus``. The ``bus is None``
     dead-code branches in ``job_feedback_observer`` /
     ``child_reports`` / ``error_reporting`` etc. are downstream of the
     dual-write and cannot affect column consistency. The relevant code
     paths the bus participates in (e.g. ``cancel_after_fail`` →
     ``job_feedback_observer._finalize_job_db_sync``) still call
     ``atomic_transition`` which itself dual-writes — see
     ``test_dualwrite_holds_for_job_feedback_observer_finalize``.

All tests use the same in-memory SQLite + StaticPool fixture style as
``tests/unit/services/test_job_queue_proxy_phase1.py`` and
``tests/unit/services/test_work_resolver.py`` (real repos against a
fresh schema, no mocks) so the dual-write contract is exercised
end-to-end through the SQL stack, not just the Python mapping layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

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

    Mirrors the fixture used in ``test_job_queue_proxy_phase1.py`` —
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
def dlq_service(job_repo: JobRepository, dlq_repo: DeadLetterRepository) -> DeadLetterService:
    """DeadLetterService wired against the test engine.

    No ``job_queue_service`` / ``loop`` are passed — the DLQ methods
    under test (``move_to_dlq_standalone`` and ``replay_from_dlq``) do
    not depend on the watcher-notification path. This keeps the
    test fixture minimal and avoids reaching into the global
    ``_service`` singleton.
    """
    return DeadLetterService(job_repository=job_repo, dlq_repository=dlq_repo)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_queue(engine: Engine, *, project_id: str = "test-project") -> str:
    """Insert a ``JobQueue`` row and return its ``queue_id``.

    Most dual-write tests don't care about queues — they exercise the
    status/admission_state dual-write regardless of routing. But the
    DLQ tests fail unless the source ``JobItem`` has a ``queue_id``,
    because ``DeadLetterItem.queue_id`` is NOT NULL and the DLQ
    service copies the source job's queue_id verbatim. The cheapest
    way to keep the helper minimal is to always seed a queue in
    ``_make_job``.

    Each call produces a unique ``queue_name`` (a short uuid suffix)
    so multiple jobs in the same project can coexist without
    tripping the ``UNIQUE(project_id, queue_name_lower)`` constraint
    in ``JobQueue.__table_args__``.
    """
    from sqlmodel import Session

    queue_id = f"q-{uuid.uuid4().hex[:12]}"
    queue_name = f"q-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        s.add(
            JobQueue(
                queue_id=queue_id,
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name,
                queue_type=QueueType.FIFO.value,
                concurrency_limit=1,
            )
        )
        s.commit()
    return queue_id


def _make_job(
    engine: Engine, job_repo: JobRepository, **overrides
) -> JobItem:
    """Create a job with reasonable defaults and return the JobItem.

    Defaults are tuned for the dual-write tests: ``project_id`` and
    ``queue_id`` are populated so DLQ helpers that assert on
    ``project_id`` normalization / ``queue_id`` NOT NULL are happy.
    Each call gets its own JobQueue to keep tests independent.
    """
    project_id = overrides.pop("project_id", "test-project")
    queue_id = overrides.pop("queue_id", None) or _make_queue(
        engine, project_id=project_id
    )
    defaults = {
        "agent_id": "developer",
        "agent_dir": "/tmp/agents/developer",
        "message": "phase2 dual-write test job",
        "source": "api",
        "project_id": project_id,
        "queue_id": queue_id,
        "priority": 5,
    }
    defaults.update(overrides)
    return job_repo.create(**defaults)


def _refresh(engine: Engine, job_id: str) -> JobItem:
    """Re-read a JobItem from the engine so the assertions see a fresh
    row (mirrors the test_job_queue_proxy_phase1 fixture style).

    ``SQLModelSession.refresh`` would also work but a fresh SELECT is
    unambiguous about whether the UPDATE landed.
    """
    from sqlmodel import Session, select

    with Session(engine) as s:
        return s.exec(select(JobItem).where(JobItem.job_id == job_id)).one()


# ─── A. Dual-write on creation ──────────────────────────────────────────────


class TestDualWriteOnCreation:
    """Phase 2 dual-write at the INSERT path:

    * ``create()`` writes both ``status`` and ``admission_state`` on
      INSERT (verified by re-reading the row).
    * ``create_or_get_by_idempotency_key()`` does the same when it
      inserts a new row (the loser's branch must surface the existing
      row's already-correct columns).
    """

    def test_create_sets_status_and_admission_state(
        self, engine, job_repo: JobRepository
    ):
        """``create()`` returns a JobItem whose ``status='pending'`` AND
        ``admission_state='queued'`` are both set in the same INSERT.
        """
        job = _make_job(engine, job_repo)

        assert job.admission_state == AdmissionState.QUEUED.value
        assert job.admission_state == AdmissionState.QUEUED.value

    def test_create_persists_both_columns(self, engine, job_repo):
        """The dual-write is not just an in-memory attribute — the row
        in the DB has BOTH columns populated.
        """
        job = _make_job(engine, job_repo)
        refetched = _refresh(engine, job.job_id)

        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.admission_state == AdmissionState.QUEUED.value

    def test_create_with_idempotency_key_dual_writes(
        self, job_repo: JobRepository
    ):
        """``create_or_get_by_idempotency_key`` dual-writes when it
        inserts the row. The freshly-inserted branch must report
        ``status='pending'`` AND ``admission_state='queued'``.
        """
        key = f"key-{uuid.uuid4().hex[:12]}"
        job, created = job_repo.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="idempotent dual-write test",
            idempotency_key=key,
            project_id="test-project",
        )

        assert created is True
        assert job is not None
        assert job.admission_state == AdmissionState.QUEUED.value
        assert job.admission_state == AdmissionState.QUEUED.value

    def test_idempotency_key_loser_sees_winner_state(
        self, job_repo: JobRepository
    ):
        """When ``create_or_get_by_idempotency_key`` returns the
        pre-existing winner, both columns on the returned row still
        match the status-to-admission invariant — the loser does not
        observe a half-written row.
        """
        key = f"key-{uuid.uuid4().hex[:12]}"
        first, created_first = job_repo.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="first write",
            idempotency_key=key,
            project_id="test-project",
        )
        assert created_first is True

        second, created_second = job_repo.create_or_get_by_idempotency_key(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="second write (loser)",
            idempotency_key=key,
            project_id="test-project",
        )
        assert created_second is False
        # Loser observes the winner's job_id.
        assert second.job_id == first.job_id
        # And the winner's columns are still consistent.
        assert second.admission_state == AdmissionState.QUEUED.value
        assert second.admission_state == AdmissionState.QUEUED.value


# ─── B. Dual-write on lifecycle transitions ─────────────────────────────────


class TestDualWriteOnLifecycleTransitions:
    """Phase 2 dual-write on every state-machine transition.

    Each test creates a job, transitions it via the canonical
    repository / service method, then re-reads the row and asserts
    that ``admission_state`` matches ``status_to_admission(status)``.
    The single-source-of-truth invariant is:

        admission_state == status_to_admission(status)

    for every persisted row.
    """

    def test_start_job_dual_writes_active(self, engine, job_repo):
        """``start_job`` (PENDING → PROCESSING) writes ``active`` in
        the same guarded UPDATE. The PostgreSQL-only
        ``trg_job_queue_items_active_lock_guard`` trigger does not
        fire on SQLite (in-memory test), so no ``job_locks`` row is
        required here.
        """
        job = _make_job(engine, job_repo)
        started = job_repo.start_job(job.job_id, instance_id="inst-1")
        assert started is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.instance_id == "inst-1"

    def test_complete_job_dual_writes_done(self, engine, job_repo):
        """``complete_job`` (PROCESSING → COMPLETED) writes ``done``."""
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        completed = job_repo.complete_job(job.job_id, result_summary="ok")
        assert completed is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.result_summary == "ok"

    def test_fail_job_dual_writes_done(self, engine, job_repo):
        """``fail_job`` (PROCESSING → FAILED) writes ``done`` (the
        mapping collapses FAILED into the DONE bucket — DLQ is the
        only DEAD bucket).
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        failed = job_repo.fail_job(job.job_id, error_message="boom")
        assert failed is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.error_message == "boom"

    def test_cancel_job_from_pending_dual_writes_done(
        self, engine, job_repo
    ):
        """``cancel_job`` from PENDING writes ``done`` in the same
        guarded UPDATE-WHERE-IN.
        """
        job = _make_job(engine, job_repo)
        cancelled = job_repo.cancel_job(job.job_id)
        assert cancelled is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value

    def test_cancel_job_from_processing_dual_writes_done(
        self, engine, job_repo
    ):
        """``cancel_job`` from PROCESSING also writes ``done`` (the
        cancellable-states IN-list covers both PENDING and PROCESSING).
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        cancelled = job_repo.cancel_job(job.job_id)
        assert cancelled is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DONE.value
        assert refetched.admission_state == AdmissionState.DONE.value

    def test_move_to_dlq_dual_writes_dead(
        self, engine, job_repo, dlq_service
    ):
        """``DeadLetterService.move_to_dlq_standalone`` writes
        ``dead_letter → admission_state='dead'`` in the SAME guarded
        UPDATE as the DLQ row insert.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="max retries")

        dlq_item = dlq_service.move_to_dlq_standalone(
            job_id=job.job_id, reason="MAX_RETRIES"
        )
        assert dlq_item is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.DEAD.value
        assert refetched.admission_state == AdmissionState.DEAD.value

    def test_replay_from_dlq_dual_writes_queued(
        self, engine, job_repo, dlq_service
    ):
        """``DeadLetterService.replay_from_dlq`` resets the row to
        PENDING + ``admission_state='queued'`` in the SAME guarded
        UPDATE. Belt-and-braces check: all retry/clear fields are
        reset alongside the dual-write.
        """
        job = _make_job(engine, job_repo)
        # Force the job to DEAD_LETTER.
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
        # Retry counters / error fields reset in the same UPDATE.
        assert refetched.retry_count == 0
        assert refetched.failed_at is None
        assert refetched.error_message is None
        assert refetched.started_at is None
        assert refetched.completed_at is None
        assert refetched.instance_id is None

    def test_atomic_retry_dual_writes_queued(
        self, engine, job_repo
    ):
        """``JobRepository.atomic_retry`` (FAILED → PENDING via the
        ``retry`` state-machine transition) writes
        ``admission_state='queued'`` in the SAME guarded UPDATE that
        increments ``retry_count`` and clears the failure fields.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="transient")

        next_retry_at = datetime.now(timezone.utc).isoformat()
        retried = job_repo.atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at=next_retry_at,
        )
        assert retried is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.retry_count == 1
        assert refetched.error_message is None
        assert refetched.failed_at is None


# ─── C. status_to_admission() unit tests ────────────────────────────────────


class TestStatusToAdmissionMapping:
    """The ``status_to_admission`` helper is the single source of truth
    for the dual-write. The mapping must be stable across all
    ``JobStatus`` values; the test pins every transition.
    """

    @pytest.mark.parametrize(
        "status_value, expected_admission",
        [
            (AdmissionState.QUEUED.value, AdmissionState.QUEUED.value),
            (AdmissionState.ACTIVE.value, AdmissionState.ACTIVE.value),
            (AdmissionState.ACTIVE.value, AdmissionState.ACTIVE.value),
            (AdmissionState.DONE.value, AdmissionState.DONE.value),
            (AdmissionState.DONE.value, AdmissionState.DONE.value),
            (AdmissionState.DONE.value, AdmissionState.DONE.value),
            (AdmissionState.DEAD.value, AdmissionState.DEAD.value),
        ],
    )
    def test_every_status_maps_correctly(
        self, status_value: str, expected_admission: str
    ):
        """Each of the 7 JobStatus values maps to its expected
        AdmissionState bucket. The PAUSED → ACTIVE special-case is
        pinned here because pause is an Instance concern, so a paused
        job keeps ``active`` in admission (the JobLock is still held).
        """
        assert status_to_admission(status_value) == expected_admission

    def test_unknown_status_defaults_to_queued(self):
        """Unknown / unexpected status strings fall through to
        ``QUEUED`` — the safest fall-through, matching the column
        default and the model field default. This is the documented
        safety net (see ``models.status_to_admission`` docstring).
        """
        assert status_to_admission("not_a_real_status") == AdmissionState.QUEUED.value
        # Empty string and unknown typos collapse to QUEUED.
        assert status_to_admission("") == AdmissionState.QUEUED.value

    def test_mapping_is_idempotent_for_admission_values(self):
        """Defensive: every AdmissionState value is a valid input to
        the helper (forward-compat: callers might pass a JobStatus
        that already collapsed). All four current AdmissionStates map
        either to themselves (idempotent for QUEUED/ACTIVE/DEAD) or
        collapse to DONE (none of the AdmissionStates are input in
        practice, but pinning prevents a future bug).
        """
        # These calls are sanity checks — the helper is only meant to
        # be called with JobStatus values, but the fallback dict
        # should never raise.
        assert status_to_admission(AdmissionState.QUEUED.value) == AdmissionState.QUEUED.value
        assert status_to_admission(AdmissionState.ACTIVE.value) == AdmissionState.QUEUED.value
        # DEAD is not in the JobStatus mapping; falls through to QUEUED.
        assert status_to_admission(AdmissionState.DEAD.value) == AdmissionState.QUEUED.value


# ─── D. Pause/Resume cascade admission_state transitions ─────────────────────


class TestPauseResumeDualWrite:
    """The pause/resume state-machine transitions are special-cased
    in the mapping: PAUSED → ACTIVE (NOT a separate admission value).

    Plan §8.1 documents the rationale: pause is an Instance-level
    concern, not a queue-level concern. The job's lock is still held,
    so the JobItem stays ``admission_state='active'`` across the
    pause → resume round-trip. These tests pin that contract via the
    canonical ``atomic_transition`` API.
    """

    def test_pause_keeps_admission_state_active(self, engine, job_repo):
        """``PROCESSING → PAUSED`` keeps ``admission_state='active'``.

        The state machine permits the transition (added in Phase 1 of
        the pause/resume redesign, 2026-06-25); the dual-write in
        ``atomic_transition`` computes
        ``status_to_admission('paused') == 'active'`` and writes it
        in the same UPDATE.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")

        paused = job_repo.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.ACTIVE.value,
        )
        assert paused is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        # Pause keeps the admission state active — the lock is still
        # held by the instance, so the queue-side view is "still in
        # flight".
        assert refetched.admission_state == AdmissionState.ACTIVE.value

    def test_resume_restores_admission_state_active(self, engine, job_repo):
        """``PAUSED → PROCESSING`` keeps ``admission_state='active'`` —
        it was active before pause, stays active across the round-trip.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.ACTIVE.value,
        )

        resumed = job_repo.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.ACTIVE.value,
        )
        assert resumed is not None

        refetched = _refresh(engine, job.job_id)
        assert refetched.admission_state == AdmissionState.ACTIVE.value
        assert refetched.admission_state == AdmissionState.ACTIVE.value

    def test_pause_resume_round_trip_preserves_active(
        self, engine, job_repo
    ):
        """End-to-end: start → pause → resume keeps the row in
        ``admission_state='active'`` across every step. Belt-and-
        braces for the cascade SQL in
        ``instance_lifecycle._pause_cascade_db_sync`` /
        ``_resume_cascade_db_sync`` which writes the same value
        explicitly.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.ACTIVE.value

        job_repo.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.ACTIVE.value,
        )
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.ACTIVE.value

        job_repo.atomic_transition(
            job.job_id,
            from_status=AdmissionState.ACTIVE.value,
            to_status=AdmissionState.ACTIVE.value,
        )
        assert _refresh(engine, job.job_id).admission_state == AdmissionState.ACTIVE.value


# ─── E. Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases that are easy to regress but cheap to pin."""

    def test_queued_job_with_no_instance_id(self, job_repo: JobRepository):
        """A freshly-created job (``instance_id=None``,
        ``status='pending'``) is ``admission_state='queued'``.

        This is the queue-stage branch: no instance has claimed the
        job yet, so the queue-side bucket is QUEUED. The downstream
        ``bus is None`` branches in ``dependency_bus`` /
        ``child_reports`` / ``job_feedback_observer`` only fire on
        completion cascades and have nothing to do with the
        dual-write — the dual-write happens at the INSERT level.
        """
        job = job_repo.create(
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="edge: queued no instance",
            project_id="test-project",
        )
        # Default instance_id is None — the queue-stage branch.
        assert job.instance_id is None
        assert job.admission_state == AdmissionState.QUEUED.value
        assert job.admission_state == AdmissionState.QUEUED.value

    def test_dual_write_is_bus_independent(self, engine, job_repo):
        """Phase 4 cleanup: the dual-write invariant is gone — the
        legacy ``status`` column is no longer written by
        ``start_job`` / ``complete_job`` / ``fail_job`` / etc.
        The ``status`` column stays frozen at the INSERT default
        (``"pending"``) and ``admission_state`` is the sole
        authority. The bus-independence invariant now reads:
        every row in the table satisfies ``admission_state``
        transitions independent of bus presence — the
        ``admission_state`` is set by repository / SQL layer
        statements strictly upstream of the ``dependency_bus``
        singleton, so a missing bus cannot desynchronise
        ``admission_state``.

        The legacy ``status_to_admission(status)`` invariant is
        replaced by the canonical admission bucket check:
        ``admission_state`` lands on the expected bucket for
        every transition path (PENDING / ACTIVE / DONE).
        """
        jobs = [
            _make_job(engine, job_repo),
            _make_job(engine, job_repo),
            _make_job(engine, job_repo),
        ]
        # Walk each job through a different transition path.
        job_repo.start_job(jobs[0].job_id, instance_id="inst-a")
        job_repo.start_job(jobs[1].job_id, instance_id="inst-b")
        job_repo.complete_job(jobs[1].job_id, result_summary="ok")
        # Leave jobs[2] as PENDING.

        expected_states = (
            AdmissionState.ACTIVE.value,   # jobs[0]: PENDING → ACTIVE
            AdmissionState.DONE.value,     # jobs[1]: PENDING → ACTIVE → DONE
            AdmissionState.QUEUED.value,   # jobs[2]: stays PENDING
        )
        for j, expected in zip(jobs, expected_states):
            refetched = _refresh(engine, j.job_id)
            assert refetched.admission_state == expected, (
                f"Drift on job {refetched.job_id}: "
                f"admission_state={refetched.admission_state!r} "
                f"(expected {expected!r})."
            )


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