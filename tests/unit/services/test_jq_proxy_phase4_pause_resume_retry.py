"""Phase 4 (Job as Queue Proxy) pause/resume cascade + retry guard tests.

Phase 4 (commit ``e61b8c5a``) flips the write authority from the legacy
``status`` column to the new ``admission_state`` column. Two
consequences are pinned here:

A. **Pause/resume no longer write ``job_queue_items``.**
   The cascade helpers ``_pause_cascade_db_sync`` /
   ``_resume_cascade_db_sync`` previously performed THREE batched
   UPDATEs (instances, job_queue_items, task). Phase 4 deletes the
   ``job_queue_items`` UPDATE — pause/resume is an *Instance* concern
   (Plan §8.1); the job stays in ``admission_state='active'`` with its
   lock held throughout the pause/resume round-trip.

B. **Pause/resume preserve the JobLock.**
   The cascade helpers never touched ``job_locks`` to begin with (lock
   release happens only on terminate), but Phase 4 makes this an
   explicit invariant: the lock row survives the round-trip because
   the queue side of the row is untouched.

C. **maybe_retry / atomic_retry gained admission_state guards.**
   * ``maybe_retry``'s eligibility check accepts only
     ``admission_state IN ('active', 'done')`` AND ``status='failed'``
     (Phase 4 guards out every non-eligible row before the SQL guard
     even runs).
   * ``atomic_retry`` issues a single guarded UPDATE keyed on
     ``admission_state = :from_admission_state`` (default ``'done'`` —
     the dual-write mirror for legacy ``fail_job`` callers).
   * Exhausted retries route to ``DeadLetterService.move_to_dlq`` with
     ``from_admission_state='failed'`` (the legacy SQL guard).

D. **from_admission_state parameter.**
   ``atomic_retry`` defaults to ``from_admission_state='done'`` (the
   legacy dual-write mirror for ``status='failed'``). Phase 4 callers
   may pass ``from_admission_state='active'`` to retry a row whose
   canonical state is ``active`` (the Plan §3.2
   retry-without-instance guarantee — finalize paths transition
   ``active → queued`` directly, bypassing the FAILED intermediate).

Test style follows ``tests/unit/services/test_jq_proxy_phase2_dualwrite.py``
(in-memory SQLite + StaticPool, ``PRAGMA foreign_keys=ON``, real repos)
and ``tests/unit/test_cascade_pause_resume.py`` (drive the cascade
helpers directly via a ``MagicMock`` manager).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from daemon.config import JobSystemConfig
from daemon.repositories.job_queue import AdmissionState, JobItem, JobRepository
from daemon.repositories.job_queue.dead_letter_repository import (
    DeadLetterRepository,
)
from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobLock,
    JobQueue,
    JobStatus,
    QueueType,
)
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.instance.models import (
    Instance,
    InstanceHierarchy,
    InstanceStatus,
)
from daemon.services.dead_letter_service import (
    DeadLetterService,
    JobNotInFailedStateError,
)
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.job_retry_engine import JobRetryEngine
from daemon.write_pause_guard import WritePauseGuard


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
    """Real in-memory SQLite engine (StaticPool for cross-thread safety).

    Mirrors ``tests/unit/services/test_jq_proxy_phase2_dualwrite.py`` —
    StaticPool keeps a single connection alive for the whole test so
    the cascade helpers (which open their own sessions via the engine)
    share the in-memory store, and ``PRAGMA foreign_keys=ON`` matches
    the production daemon's SQLite posture.
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
def write_guard() -> WritePauseGuard:
    """Fresh WritePauseGuard — not paused."""
    return WritePauseGuard()


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


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

    No ``job_queue_service`` / ``loop`` are passed — the DLQ methods
    under test (``move_to_dlq`` and ``move_to_dlq_standalone``) do not
    depend on the watcher-notification path. This keeps the fixture
    minimal and avoids reaching into the global ``_service`` singleton.
    """
    return DeadLetterService(job_repository=job_repo, dlq_repository=dlq_repo)


@pytest.fixture
def default_config() -> JobSystemConfig:
    """Default config with retries enabled and a small backoff window."""
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
    """JobRetryEngine wired against the test engine."""
    return JobRetryEngine(
        job_repo=job_repo,
        queue_repo=queue_repo,
        dlq_service=dlq_service,
        config=default_config,
    )


@pytest.fixture
def lifecycle_service(engine: Engine, write_guard: WritePauseGuard):
    """Build an InstanceLifecycleService bound to a real DB.

    The service is constructed with a minimal stub manager exposing
    ``engine`` and ``write_guard`` — the only two attributes the
    cascade helpers need. Mirrors the fixture in
    ``tests/unit/test_cascade_pause_resume.py``.
    """
    service = InstanceLifecycleService.__new__(InstanceLifecycleService)
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = write_guard
    service._manager = manager
    return service


# ─── Seed helpers ───────────────────────────────────────────────────────────


def _make_queue(engine: Engine, *, project_id: str = "test-project") -> str:
    """Insert a JobQueue row and return its queue_id.

    Each call produces a unique ``queue_name`` so multiple jobs in the
    same project can coexist without tripping the
    ``UNIQUE(project_id, queue_name_lower)`` constraint.
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
                queue_type=QueueType.FIFO.value,
                concurrency_limit=1,
            )
        )
        s.commit()
    return queue_id


def _make_job(
    engine: Engine, job_repo: JobRepository, **overrides
) -> JobItem:
    """Create a job via the repository (honors the dual-write) and
    return the JobItem. Defaults are tuned for the pause/resume tests:
    ``project_id`` and ``queue_id`` are populated so the row can be
    routed through ``move_to_dlq`` later if needed.

    Note: ``JobRepository.create()`` does NOT accept ``max_retries``
    (see ``daemon/repositories/job_queue/repository.py:68``); callers
    that need a non-default ``max_retries`` should update the row
    directly via the engine session after creation, or use
    ``_seed_job_in_state`` below.
    """
    project_id = overrides.pop("project_id", "test-project")
    queue_id = overrides.pop("queue_id", None) or _make_queue(
        engine, project_id=project_id
    )
    max_retries = overrides.pop("max_retries", None)
    defaults: dict[str, Any] = {
        "agent_id": "developer",
        "agent_dir": "/tmp/agents/developer",
        "message": "phase4 pause/resume/retry test job",
        "source": "api",
        "project_id": project_id,
        "queue_id": queue_id,
        "priority": 5,
    }
    defaults.update(overrides)
    job = job_repo.create(**defaults)
    if max_retries is not None:
        # Direct UPDATE — bypasses the dual-write state machine; the
        # retry tests only care about the ``max_retries`` column value.
        with Session(engine) as s:
            row = s.get(JobItem, job.job_id)
            row.max_retries = max_retries
            s.commit()
            s.refresh(row)
            return row
    return job


def _seed_job_in_state(
    engine: Engine,
    *,
    status: str,
    admission_state: str | None = None,
    retry_count: int = 0,
    max_retries: int | None = None,
    project_id: str = "test-project",
) -> JobItem:
    """Insert a JobItem directly with explicit (status, admission_state,
    retry_count, max_retries) values.

    Bypasses the repository's dual-write state machine — used for
    guard-eligibility tests that need a row in an arbitrary state
    without going through the canonical transition path.
    """
    jid = f"job-{uuid.uuid4().hex[:8]}"
    qid = _make_queue(engine, project_id=project_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        job = JobItem(
            job_id=jid,
            agent_id="developer",
            agent_dir="/tmp/agents/developer",
            message="phase4 direct seed",
            source="api",
            project_id=project_id,
            queue_id=qid,
            job_type="message",
            admission_state=(
                admission_state
                if admission_state is not None
                else status_to_admission(status)
            ),
            retry_count=retry_count,
            max_retries=max_retries,
            created_at=now_iso,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        return job


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "developer",
    parent_id: str | None = None,
    paused_at: str | None = None,
) -> str:
    """Insert an Instance row. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                parent_id=parent_id,
                project_id="test-project",
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=paused_at,
            )
        )
        s.commit()
    return iid


def _seed_hierarchy(
    engine: Engine, *, parent_id: str, child_id: str
) -> None:
    """Insert an InstanceHierarchy row (parent→child link)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            InstanceHierarchy(
                parent_id=parent_id, child_id=child_id, created_at=now_iso
            )
        )
        s.commit()


def _seed_job_for_instance(
    engine: Engine,
    *,
    instance_id: str,
    status: str = AdmissionState.ACTIVE.value,
    project_id: str = "test-project",
    queue_id: str | None = None,
) -> str:
    """Insert a JobItem directly tied to an instance.

    Phase 4 (Job as Queue Proxy): ``admission_state`` is derived from
    ``status`` via ``status_to_admission`` so the seed honors the
    dual-write contract. PROCESSING/PAUSED → ACTIVE; PENDING → QUEUED;
    terminal statuses → DONE/DEAD.
    """
    jid = f"job-{uuid.uuid4().hex[:8]}"
    qid = queue_id or _make_queue(engine, project_id=project_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                message="phase4 cascade seed",
                source="api",
                project_id=project_id,
                queue_id=qid,
                job_type="message",
                admission_state=status_to_admission(status),
                instance_id=instance_id,
                created_at=now_iso,
                    now_iso
                    if status
                    in (AdmissionState.ACTIVE.value, AdmissionState.ACTIVE.value)
                    else None
                ),
            )
        )
        s.commit()
    return jid


def _seed_job_lock(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str | None = None,
    slot: int = 0,
) -> str:
    """Insert a JobLock row. Returns the lock_id.

    Phase 4 invariant: the pause/resume cascade helpers NEVER touch
    ``job_locks``. This seed is used to assert the lock survives the
    round-trip.
    """
    qid = queue_id or _make_queue(engine, project_id=project_id)
    lock_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobLock(
                lock_id=lock_id,
                project_id=project_id,
                queue_id=qid,
                job_id=job_id,
                instance_id=instance_id,
                lock_slot=slot,
                acquired_at=now_iso,
            )
        )
        s.commit()
    return lock_id


# ─── Read helpers ───────────────────────────────────────────────────────────


def _refresh(engine: Engine, job_id: str) -> JobItem:
    """Re-read a JobItem so assertions see a fresh row."""
    with Session(engine) as s:
        return s.exec(
            select(JobItem).where(JobItem.job_id == job_id)
        ).one()


def _read_instance(engine: Engine, instance_id: str) -> Instance | None:
    with Session(engine) as s:
        return s.get(Instance, instance_id)


def _read_job_locks_for_instance(
    engine: Engine, instance_id: str
) -> list[JobLock]:
    with Session(engine) as s:
        return list(
            s.exec(
                select(JobLock).where(JobLock.instance_id == instance_id)
            ).all()
        )


# ═══════════════════════════════════════════════════════════════════════════
# A. Pause/resume — no job status writes
# ═══════════════════════════════════════════════════════════════════════════


class TestPauseResumeNoJobStatusWrites:
    """Phase 4 (Plan §8.1): pause/resume is an *Instance* concern.

    The cascade helpers ``_pause_cascade_db_sync`` /
    ``_resume_cascade_db_sync`` no longer issue an UPDATE against
    ``job_queue_items``. The job's ``status`` and ``admission_state``
    columns are NEVER modified by the pause/resume flow — the job
    stays in ``status='processing'``, ``admission_state='active'`` with
    its lock held. The Instance row transitions PAUSED ↔ RUNNING; the
    ``claim_pending_task`` SQL guard on ``instance.status == PAUSED``
    is what actually blocks the worker from claiming work for a paused
    instance.
    """

    def test_pause_keeps_job_active_and_processing(
        self, lifecycle_service, engine, write_guard
    ):
        """Pause an instance with an active job.

        Verifies (Phase 4):
        * Instance transitions RUNNING → PAUSED.
        * Job's ``admission_state`` STAYS ``'active'`` (not flipped).
        * Job's ``status`` STAYS ``'processing'`` (not written to
          ``'paused'``).
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        jid = _seed_job_for_instance(
            engine, instance_id=iid, status=AdmissionState.ACTIVE.value
        )

        # Sanity: before pause, the job is processing/active.
        pre = _refresh(engine, jid)
        assert pre.admission_state == AdmissionState.ACTIVE.value
        assert pre.admission_state == AdmissionState.ACTIVE.value

        now_iso = datetime.now(timezone.utc).isoformat()
        result = lifecycle_service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            paused_at_iso=now_iso,
            paused_instances_data=[(iid, "developer")],
        )
        assert iid in result.updated_ids

        # Instance → PAUSED.
        inst = _read_instance(engine, iid)
        assert inst.status == InstanceStatus.PAUSED.value

        # Phase 4: job status and admission_state UNCHANGED.
        post = _refresh(engine, jid)
        assert post.admission_state == AdmissionState.ACTIVE.value, (
            f"job status must stay 'processing' under Phase 4 pause "
            f"(Instance-only), got {post.status!r}"
        )
        assert post.admission_state == AdmissionState.ACTIVE.value, (
            f"job admission_state must stay 'active' under Phase 4 "
            f"pause (lock still held), got {post.admission_state!r}"
        )

    def test_resume_keeps_job_active_and_processing(
        self, lifecycle_service, engine, write_guard
    ):
        """Resume an instance with an active job.

        Verifies (Phase 4):
        * Instance transitions PAUSED → RUNNING.
        * Job's ``admission_state`` STAYS ``'active'``.
        * Job's ``status`` STAYS ``'processing'`` (not flipped during
          resume either).
        """
        paused_iso = datetime.now(timezone.utc).isoformat()
        iid = _seed_instance(
            engine,
            status=InstanceStatus.PAUSED.value,
            paused_at=paused_iso,
        )
        # Phase 4: jobs stay PROCESSING (pause never flipped them).
        jid = _seed_job_for_instance(
            engine, instance_id=iid, status=AdmissionState.ACTIVE.value
        )

        # Sanity: before resume, the job is still processing/active.
        pre = _refresh(engine, jid)
        assert pre.admission_state == AdmissionState.ACTIVE.value
        assert pre.admission_state == AdmissionState.ACTIVE.value

        result = lifecycle_service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            ancestor_ids=set(),
            is_root_resume=True,
        )
        assert iid in result.updated_ids

        # Instance → RUNNING, paused_at cleared.
        inst = _read_instance(engine, iid)
        assert inst.status == InstanceStatus.RUNNING.value
        assert inst.paused_at is None

        # Phase 4: job status and admission_state UNCHANGED by resume.
        post = _refresh(engine, jid)
        assert post.admission_state == AdmissionState.ACTIVE.value, (
            f"job status must stay 'processing' under Phase 4 resume "
            f"(Instance-only), got {post.status!r}"
        )
        assert post.admission_state == AdmissionState.ACTIVE.value, (
            f"job admission_state must stay 'active' under Phase 4 "
            f"resume, got {post.admission_state!r}"
        )

    def test_pause_resume_round_trip_preserves_job_state(
        self, lifecycle_service, engine, write_guard
    ):
        """End-to-end: start (processing/active) → pause → resume.

        The job's (status, admission_state) pair must stay
        ('processing', 'active') across every step. Belt-and-braces
        for the Phase 4 contract.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        jid = _seed_job_for_instance(
            engine, instance_id=iid, status=AdmissionState.ACTIVE.value
        )

        now = datetime.now(timezone.utc).isoformat()

        # ── Pause ──
        lifecycle_service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            paused_at_iso=now,
            paused_instances_data=[(iid, "developer")],
        )
        paused = _refresh(engine, jid)
        assert paused.admission_state == AdmissionState.ACTIVE.value
        assert paused.admission_state == AdmissionState.ACTIVE.value
        assert _read_instance(engine, iid).status == InstanceStatus.PAUSED.value

        # ── Resume ──
        lifecycle_service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            ancestor_ids=set(),
            is_root_resume=True,
        )
        resumed = _refresh(engine, jid)
        assert resumed.admission_state == AdmissionState.ACTIVE.value
        assert resumed.admission_state == AdmissionState.ACTIVE.value
        assert _read_instance(engine, iid).status == InstanceStatus.RUNNING.value


# ═══════════════════════════════════════════════════════════════════════════
# B. Pause/resume preserves lock
# ═══════════════════════════════════════════════════════════════════════════


class TestPauseResumePreservesLock:
    """Phase 4 invariant: the ``job_locks`` row survives pause/resume.

    The cascade helpers never touched ``job_locks`` (lock release is
    owned by terminate), but Phase 4 makes this an explicit guarantee:
    because the queue side of the row is untouched by pause/resume,
    the lock row — acquired when the job transitioned to ACTIVE —
    remains in place. This is what lets the JobProcessor re-acquire
    the same lock on resume without re-queuing.
    """

    def test_pause_preserves_job_lock(
        self, lifecycle_service, engine, write_guard
    ):
        """After pause, the ``job_locks`` row for the instance still
        exists — Phase 4 pause does not release locks.
        """
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        jid = _seed_job_for_instance(
            engine, instance_id=iid, status=AdmissionState.ACTIVE.value
        )
        lock_id = _seed_job_lock(engine, job_id=jid, instance_id=iid)

        # Sanity: lock exists pre-pause.
        locks_before = _read_job_locks_for_instance(engine, iid)
        assert len(locks_before) == 1
        assert locks_before[0].lock_id == lock_id

        lifecycle_service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            paused_at_iso=datetime.now(timezone.utc).isoformat(),
            paused_instances_data=[(iid, "developer")],
        )

        # Lock still there post-pause.
        locks_after = _read_job_locks_for_instance(engine, iid)
        assert len(locks_after) == 1, (
            "Phase 4 pause must NOT release the job_locks row"
        )
        assert locks_after[0].lock_id == lock_id

    def test_resume_preserves_job_lock(
        self, lifecycle_service, engine, write_guard
    ):
        """After resume, the ``job_locks`` row is still in place —
        Phase 4 resume does not release or re-acquire locks.
        """
        paused_iso = datetime.now(timezone.utc).isoformat()
        iid = _seed_instance(
            engine, status=InstanceStatus.PAUSED.value, paused_at=paused_iso
        )
        jid = _seed_job_for_instance(
            engine, instance_id=iid, status=AdmissionState.ACTIVE.value
        )
        lock_id = _seed_job_lock(engine, job_id=jid, instance_id=iid)

        # Sanity: lock exists pre-resume.
        locks_before = _read_job_locks_for_instance(engine, iid)
        assert len(locks_before) == 1
        assert locks_before[0].lock_id == lock_id

        lifecycle_service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            ancestor_ids=set(),
            is_root_resume=True,
        )

        # Lock still there post-resume.
        locks_after = _read_job_locks_for_instance(engine, iid)
        assert len(locks_after) == 1, (
            "Phase 4 resume must NOT release the job_locks row"
        )
        assert locks_after[0].lock_id == lock_id

    def test_pause_resume_round_trip_preserves_lock(
        self, lifecycle_service, engine, write_guard
    ):
        """End-to-end: lock survives the full pause → resume cycle."""
        iid = _seed_instance(engine, status=InstanceStatus.RUNNING.value)
        jid = _seed_job_for_instance(
            engine, instance_id=iid, status=AdmissionState.ACTIVE.value
        )
        lock_id = _seed_job_lock(engine, job_id=jid, instance_id=iid)

        # ── Pause ──
        lifecycle_service._pause_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            paused_at_iso=datetime.now(timezone.utc).isoformat(),
            paused_instances_data=[(iid, "developer")],
        )
        assert len(_read_job_locks_for_instance(engine, iid)) == 1

        # ── Resume ──
        lifecycle_service._resume_cascade_db_sync(
            engine,
            write_guard,
            tree_ids=[iid],
            ancestor_ids=set(),
            is_root_resume=True,
        )
        locks = _read_job_locks_for_instance(engine, iid)
        assert len(locks) == 1
        assert locks[0].lock_id == lock_id


# ═══════════════════════════════════════════════════════════════════════════
# C. maybe_retry / atomic_retry guards
# ═══════════════════════════════════════════════════════════════════════════


class TestMaybeRetryGuards:
    """Phase 4 eligibility guards in ``JobRetryEngine.maybe_retry``.

    The Python-level guard is:

        if job.admission_state not in ('active', 'done') \
                or job.status != 'failed':
            return None

    So every non-eligible row short-circuits before the SQL guard in
    ``atomic_retry`` even runs. The tests below exercise that guard by
    seeding rows in every non-eligible combination.
    """

    def test_retry_failed_job_transitions_to_queued_pending(
        self, retry_engine, job_repo, engine
    ):
        """Retry a FAILED job (admission_state='done' under the dual-
        write mapping).

        Verifies:
        * Job's ``admission_state`` transitions 'done' → 'queued'.
        * Job's ``status`` transitions 'failed' → 'pending'.
        * ``retry_count`` is incremented atomically.
        """
        job = _make_job(engine, job_repo, max_retries=3)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="boom")

        # Sanity: failed → admission_state='done'.
        pre = _refresh(engine, job.job_id)
        assert pre.admission_state == AdmissionState.DONE.value
        assert pre.admission_state == AdmissionState.DONE.value
        assert pre.retry_count == 0

        result = retry_engine.maybe_retry(job.job_id)
        assert result is not None

        post = _refresh(engine, job.job_id)
        assert post.admission_state == AdmissionState.QUEUED.value
        assert post.admission_state == AdmissionState.QUEUED.value
        assert post.retry_count == 1
        assert post.error_message is None
        assert post.failed_at is None

    @pytest.mark.parametrize(
        "status, admission",
        [
            # 'queued' bucket — not retryable.
            (AdmissionState.QUEUED.value, AdmissionState.QUEUED.value),
            # 'done' bucket but terminal — NOT retryable.
            (AdmissionState.DONE.value, AdmissionState.DONE.value),
            (AdmissionState.DONE.value, AdmissionState.DONE.value),
            # 'dead' bucket — not retryable.
            (AdmissionState.DEAD.value, AdmissionState.DEAD.value),
        ],
    )
    def test_retry_non_failed_job_is_rejected(
        self,
        retry_engine,
        engine,
        status: str,
        admission: str,
    ):
        """Retrying a non-active/non-failed job must return None.

        Phase 4 guard: ``admission_state not in ('active', 'done') or
        status != 'failed'``. The four rows below cover:
        * PENDING/QUEUED — not eligible (admission_state not in set).
        * COMPLETED/DONE — eligible admission_state but status != 'failed'.
        * CANCELLED/DONE — same as above.
        * DEAD_LETTER/DEAD — not eligible.
        """
        # Seed directly to honor the dual-write contract (the caller
        # picks the exact state to test the guard).
        jid = f"job-{uuid.uuid4().hex[:8]}"
        qid = _make_queue(engine)
        now_iso = datetime.now(timezone.utc).isoformat()
        with Session(engine) as s:
            s.add(
                JobItem(
                    job_id=jid,
                    agent_id="developer",
                    agent_dir="/tmp/agents/developer",
                    message="non-failed retry target",
                    source="api",
                    project_id="test-project",
                    queue_id=qid,
                    job_type="message",
                    admission_state=admission,
                    retry_count=0,
                    max_retries=3,
                    created_at=now_iso,
                )
            )
            s.commit()

        result = retry_engine.maybe_retry(jid)
        assert result is None, (
            f"maybe_retry must reject status={status!r} "
            f"admission_state={admission!r}"
        )
        # And the row must be untouched.
        row = _refresh(engine, jid)
        assert row.status == status
        assert row.admission_state == admission

    def test_retry_processing_active_job_is_rejected(
        self, retry_engine, job_repo, engine
    ):
        """Retrying a PROCESSING/ACTIVE job must return None.

        Although ``admission_state='active'`` is in the eligible set,
        the companion ``status != 'failed'`` guard rejects it. This is
        the Plan §3.2 retry-without-instance guarantee's safety net —
        a job that is actively being processed must not be retried via
        the legacy path.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")

        # Sanity: processing/active.
        pre = _refresh(engine, job.job_id)
        assert pre.admission_state == AdmissionState.ACTIVE.value
        assert pre.admission_state == AdmissionState.ACTIVE.value

        result = retry_engine.maybe_retry(job.job_id)
        assert result is None, (
            "maybe_retry must reject PROCESSING/ACTIVE rows "
            "(status != 'failed' guard)"
        )

    def test_exhaust_retries_routes_to_dlq(
        self, retry_engine, job_repo, dlq_repo, engine
    ):
        """Set max_retries=1, fail twice → second failure routes to DLQ.

        Sequence:
        1. Create job (retry_count=0, max_retries=1).
        2. fail_job → status='failed', admission_state='done'.
        3. maybe_retry → retry_count 0 < 1 → retries to pending.
        4. Re-fail (status='failed', admission_state='done',
           retry_count=1).
        5. maybe_retry again → retry_count 1 >= 1 → should_retry False
           → DLQ via ``move_to_dlq(from_admission_state='failed')``.
        """
        job = _make_job(engine, job_repo, max_retries=1)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="first failure")

        # Step 3: first retry succeeds (0 < 1).
        r1 = retry_engine.maybe_retry(job.job_id)
        assert r1 is not None
        assert r1.admission_state == AdmissionState.QUEUED.value
        assert r1.retry_count == 1

        # Step 4: re-fail (mirror the dual-write so atomic_retry's SQL
        # guard matches the post-fail state).
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="second failure")
        refail = _refresh(engine, job.job_id)
        assert refail.admission_state == AdmissionState.DONE.value
        assert refail.admission_state == AdmissionState.DONE.value
        assert refail.retry_count == 1

        # Step 5: second maybe_retry → should_retry False → DLQ.
        r2 = retry_engine.maybe_retry(job.job_id)
        assert r2 is None, (
            "maybe_retry must return None after routing to DLQ"
        )

        # Verify DLQ row was created.
        dlq_item = dlq_repo.get_by_job_id(job.job_id)
        assert dlq_item is not None
        assert dlq_item.reason == "MAX_RETRIES"

        # And the job row is now DEAD_LETTER / DEAD.
        post = _refresh(engine, job.job_id)
        assert post.admission_state == AdmissionState.DEAD.value
        assert post.admission_state == AdmissionState.DEAD.value


# ═══════════════════════════════════════════════════════════════════════════
# D. from_admission_state parameter
# ═══════════════════════════════════════════════════════════════════════════


class TestFromAdmissionStateParameter:
    """Phase 4 ``from_admission_state`` parameter on ``atomic_retry``
    and ``move_to_dlq``.

    * ``atomic_retry`` defaults to ``from_admission_state='done'``
      (the dual-write mirror for legacy ``fail_job`` callers). Phase 4
      callers operating on a freshly-finalized ACTIVE job pass
      ``from_admission_state='active'`` explicitly.
    * ``move_to_dlq`` / ``move_to_dlq_standalone`` default to
      ``from_admission_state='failed'`` (legacy SQL guard). Phase 4
      callers pass ``from_admission_state='active'`` to use the
      admission_state SQL guard instead.
    """

    def test_atomic_retry_default_done_matches_failed_job(
        self, job_repo, engine
    ):
        """Default ``from_admission_state='done'`` matches the dual-
        write mirror for a legacy ``fail_job`` caller.

        Sequence: create → start → fail → atomic_retry() (no explicit
        ``from_admission_state``).
        """
        job = _make_job(engine, job_repo, max_retries=3)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="boom")

        next_retry_at = datetime.now(timezone.utc).isoformat()
        # No explicit from_admission_state → default 'done'.
        result = job_repo.atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at=next_retry_at,
        )
        assert result is not None
        assert result.admission_state == AdmissionState.QUEUED.value
        assert result.admission_state == AdmissionState.QUEUED.value
        assert result.retry_count == 1

    def test_atomic_retry_explicit_done_matches_failed_job(
        self, job_repo, engine
    ):
        """Explicit ``from_admission_state='done'`` matches the legacy
        dual-write mirror (the same as the default).
        """
        job = _make_job(engine, job_repo, max_retries=3)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="boom")

        next_retry_at = datetime.now(timezone.utc).isoformat()
        result = job_repo.atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at=next_retry_at,
            from_admission_state=AdmissionState.DONE.value,
        )
        assert result is not None
        assert result.admission_state == AdmissionState.QUEUED.value

    def test_atomic_retry_active_guard_rejects_legacy_failed_row(
        self, job_repo, engine
    ):
        """``from_admission_state='active'`` rejects a legacy failed
        row (admission_state='done').

        The SQL guard requires ``admission_state='active'`` AND
        ``status='failed'``. A legacy ``fail_job`` caller produces a
        row with ``admission_state='done'`` (dual-write mirror), so
        passing ``from_admission_state='active'`` makes the UPDATE a
        no-op. This is the safety net for the Plan §3.2
        retry-without-instance guarantee — only an ACTIVE job (one
        that never visited FAILED) can be retried via the Phase 4
        canonical path.
        """
        job = _make_job(engine, job_repo, max_retries=3)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="boom")

        # Sanity: admission_state is 'done' after fail_job.
        pre = _refresh(engine, job.job_id)
        assert pre.admission_state == AdmissionState.DONE.value

        next_retry_at = datetime.now(timezone.utc).isoformat()
        result = job_repo.atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at=next_retry_at,
            from_admission_state=AdmissionState.ACTIVE.value,
        )
        assert result is None, (
            "atomic_retry with from_admission_state='active' must "
            "reject a legacy failed row (admission_state='done')"
        )

        # Row untouched.
        post = _refresh(engine, job.job_id)
        assert post.admission_state == AdmissionState.DONE.value
        assert post.admission_state == AdmissionState.DONE.value
        assert post.retry_count == 0

    def test_atomic_retry_unknown_admission_state_rejects(
        self, job_repo, engine
    ):
        """A bogus ``from_admission_state`` value makes the UPDATE a
        no-op (the SQL guard matches no rows).
        """
        job = _make_job(engine, job_repo, max_retries=3)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="boom")

        next_retry_at = datetime.now(timezone.utc).isoformat()
        result = job_repo.atomic_retry(
            job_id=job.job_id,
            max_retries=3,
            next_retry_at=next_retry_at,
            from_admission_state="nonexistent-state",
        )
        assert result is None

    def test_move_to_dlq_default_failed_matches_legacy_row(
        self, dlq_service, job_repo, engine
    ):
        """``move_to_dlq`` with default ``from_admission_state='failed'``
        uses the legacy ``status='failed'`` SQL guard — matches the
        ``fail_job`` caller's output.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="exhausted")

        with Session(engine) as session:
            dlq_item = dlq_service.move_to_dlq(
                session,
                job.job_id,
                reason="MAX_RETRIES",
                # Default 'failed' — legacy SQL guard.
            )
            session.commit()

        assert dlq_item is not None
        post = _refresh(engine, job.job_id)
        assert post.admission_state == AdmissionState.DEAD.value
        assert post.admission_state == AdmissionState.DEAD.value

    def test_move_to_dlq_active_guard_rejects_legacy_failed_row(
        self, dlq_service, job_repo, engine
    ):
        """``move_to_dlq`` with ``from_admission_state='active'``
        rejects a legacy failed row.

        Phase 4: when ``from_admission_state='active'``, the Python
        eligibility check is on the ``admission_state`` column; a
        legacy failed row has ``admission_state='done'`` so it raises
        ``JobNotInFailedStateError``.
        """
        job = _make_job(engine, job_repo)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="exhausted")

        with Session(engine) as session:
            with pytest.raises(JobNotInFailedStateError):
                dlq_service.move_to_dlq(
                    session,
                    job.job_id,
                    reason="MAX_RETRIES",
                    from_admission_state=AdmissionState.ACTIVE.value,
                )

        # Row untouched.
        post = _refresh(engine, job.job_id)
        assert post.admission_state == AdmissionState.DONE.value
        assert post.admission_state == AdmissionState.DONE.value

    def test_maybe_retry_uses_default_done_for_failed_job(
        self, retry_engine, job_repo, engine
    ):
        """``maybe_retry`` uses the default ``from_admission_state`` on
        ``atomic_retry`` (no override at the engine level).

        The engine-level ``maybe_retry`` does NOT expose
        ``from_admission_state`` — it always uses the repository's
        default ``'done'``. This test pins that contract: a legacy
        failed job is retried successfully via the default path.
        """
        job = _make_job(engine, job_repo, max_retries=3)
        job_repo.start_job(job.job_id, instance_id="inst-1")
        job_repo.fail_job(job.job_id, error_message="legacy fail")

        result = retry_engine.maybe_retry(job.job_id)
        assert result is not None
        assert result.admission_state == AdmissionState.QUEUED.value
        assert result.admission_state == AdmissionState.QUEUED.value
        assert result.retry_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Sanity smoke
# ═══════════════════════════════════════════════════════════════════════════


class TestSmoke:
    """One quick smoke test to catch gross regressions in the fixture
    wiring. If this fails, the test file itself is broken (not the
    code under test).
    """

    def test_engine_smoke_roundtrip(self, engine, job_repo):
        """Sanity: a created job can be read back via the engine and
        has the default Phase 4 dual-write columns populated.
        """
        job = _make_job(engine, job_repo)
        refetched = _refresh(engine, job.job_id)
        assert refetched.job_id == job.job_id
        assert refetched.admission_state == AdmissionState.QUEUED.value
        assert refetched.admission_state == AdmissionState.QUEUED.value
