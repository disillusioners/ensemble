"""Tests for H15 transaction atomicity and M10 orphan cleanup in JobFeedbackObserver.

H15 Fix: ``_finalize_job`` consolidates the 5-step terminal cascade into a SINGLE
WriteGuardSession transaction. Previously, steps ran across 4 separate transactions:

  1. ``atomic_transition`` — job PROCESSING → COMPLETED/FAILED
  2. ``notify_watchers`` — [JOB_EVENT] notifications (outbox, safe to be async)
  3. ``_finalize_instance`` — instance status + commit
  4. ``release_by_instance`` — delete job_locks rows

Partial failure (e.g., ``release_by_instance`` after ``atomic_transition``) left
the queue slot leaked permanently. H15 moves 1+3+4 into a single WriteGuardSession
under ``_finalize_job_db_sync``, so either all three commit or none do.

M10 Fix: ``_trigger_next_job`` had an orphan gap. If ``enqueue_message`` failed
AFTER ``spawn_instance_with_mcp`` succeeded, the spawned instance was left in IDLE
with no queued message — unreachable by any worker. The fix terminates the
orphaned instance before marking the job FAILED.

Run with::

    pytest tests/test_finalize_job_h15.py -v --tb=short
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobItem, JobStatus
from daemon.repositories.job_queue.models import JobLock
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_state_machine import InvalidTransitionError
from daemon.write_pause_guard import WritePauseGuard


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3 fixture: DependencyBus is the SOLE completion authority (ADR-011).
# The legacy ``use_legacy_waiting_for_cascade`` kill switch was removed in
# Phase 3, so ``_finalize_job_db_sync`` now raises ``RuntimeError`` when the
# bus is None (A9 hard error). Wire a mock bus globally; tests configure the
# pending count via ``set_bus_pending(n)`` before exercising the code path
# under test.
# ──────────────────────────────────────────────────────────────────────────────
_BUS_PENDING = [0]


@pytest.fixture(autouse=True)
def _wire_bus_mock():
    bus_mock = MagicMock()
    bus_mock.count_pending_for_target_sync = lambda iid: _BUS_PENDING[0]
    set_dependency_bus(bus_mock)
    yield
    set_dependency_bus(None)
    _BUS_PENDING[0] = 0


def set_bus_pending(n: int) -> None:
    """Set the pending correlation count the mocked bus will return."""
    _BUS_PENDING[0] = n


# ─── Shared fixtures & helpers ──────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
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


def seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    instance_id: str,
    project_id: str = "test-project",
    status: str = JobStatus.PROCESSING.value,
) -> JobItem:
    """Insert a JobItem row. Returns the JobItem."""
    jid = job_id or f"job-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        item = JobItem(
            job_id=jid,
            agent_id="coder",
            agent_dir="/tmp/agent",
            message="test job",
            source="api",
            job_type="task",
            status=status,
            instance_id=instance_id,
            project_id=project_id,
        )
        s.add(item)
        s.commit()
        s.refresh(item)
    return item


def seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "coder",
    parent_id: str | None = None,
    version: int = 1,
) -> str:
    """Insert an Instance row. Returns the instance_id."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_dir="/tmp/agent",
            parent_id=parent_id,
            status=status,
            version=version,
            instance_metadata={},
            children="[]",
        )
        s.add(inst)
        s.commit()
    return iid


def seed_lock(
    engine: Engine,
    *,
    instance_id: str,
    project_id: str = "test-project",
    queue_id: str = "default",
) -> str:
    """Insert a JobLock row. Returns the lock_id."""
    lid = f"lock-{uuid.uuid4().hex[:8]}"
    with Session(engine) as s:
        lock = JobLock(
            lock_id=lid,
            project_id=project_id,
            queue_id=queue_id,
            job_id=f"job-{uuid.uuid4().hex[:8]}",
            instance_id=instance_id,
            lock_slot=0,
        )
        s.add(lock)
        s.commit()
    return lid


def get_job(engine: Engine, job_id: str) -> JobItem | None:
    """Re-read a JobItem from the DB (post-commit, detached)."""
    with Session(engine) as s:
        return s.get(JobItem, job_id)


def get_instance(engine: Engine, instance_id: str) -> Instance | None:
    """Re-read an Instance from the DB (post-commit, detached)."""
    with Session(engine) as s:
        return s.get(Instance, instance_id)


def count_locks(engine: Engine, instance_id: str) -> int:
    """Count active job_locks for an instance."""
    from sqlmodel import select
    with Session(engine) as s:
        locks = s.exec(select(JobLock).where(JobLock.instance_id == instance_id)).all()
        return len(list(locks))


def make_observer(
    engine: Engine,
    *,
    live_hub: Any | None = None,
    events_service: Any | None = None,
    get_last_message_returns: str | None = "agent response",
    lock_repo_returns: int = 0,
    real_job_repo: bool = False,
) -> tuple[JobFeedbackObserver, dict[str, Any]]:
    """Build a ``JobFeedbackObserver`` with a real engine + mocked side deps.

    The instance_manager uses the REAL engine so that ``_finalize_job_db_sync``
    (which calls ``Session(engine)`` internally) exercises end-to-end DB writes.
    Side-effect dependencies (notify_watchers, SSE hub, CR, events) are mocked.

    Args:
        real_job_repo: If True, use a real ``JobRepository`` over the engine
            instead of a ``MagicMock``. Required for tests that verify the
            W3 fail-safe atomic_transition actually writes to the DB.
    """
    guard = WritePauseGuard()

    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = guard
    manager.is_write_paused = False

    hub = live_hub if live_hub is not None else MagicMock(name="LiveHub")
    hub.stream_status_change = AsyncMock()
    manager._live_hub = hub

    events = events_service if events_service is not None else MagicMock(name="Events")
    events._publish_instance_lifecycle_event = AsyncMock()
    manager._events_service = events

    manager._get_last_assistant_message_raw = AsyncMock(
        return_value=get_last_message_returns
    )

    mock_jqs = MagicMock()
    mock_jqs.notify_watchers = AsyncMock(return_value=0)
    mock_jqs._get_next_job = AsyncMock(return_value=None)  # no next job
    # CRITICAL: handle_correlation_complete calls get_job_by_instance first.
    # Tests that use this helper must either pre-seed the lookup OR override
    # the mock — see individual tests that set it explicitly.
    mock_jqs.get_job_by_instance = AsyncMock(return_value=None)

    mock_lock_repo = MagicMock()
    mock_lock_repo.release_by_instance = MagicMock(return_value=lock_repo_returns)

    if real_job_repo:
        from daemon.repositories.job_queue import JobRepository
        job_repo = JobRepository(engine)
    else:
        job_repo = MagicMock()

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=mock_jqs,
        job_repo=job_repo,
        lock_repo=mock_lock_repo,
        project_repo=MagicMock(),
        instance_manager=manager,
    )

    return observer, {
        "instance_manager": manager,
        "job_queue_service": mock_jqs,
        "job_repo": job_repo,
        "live_hub": hub,
        "events_service": events,
        "write_guard": guard,
    }


@contextmanager
def patched_completion_registry():
    """Patch the CompletionRegistry singleton for tests."""
    mock_registry = MagicMock(name="CompletionRegistry")
    mock_registry.complete = MagicMock(return_value=True)
    with patch(
        "daemon.services.completion_registry.get_completion_registry",
        return_value=mock_registry,
    ) as patched:
        yield mock_registry, patched


# ═════════════════════════════════════════════════════════════════════════════════
# H15.1 — Happy path: job, instance, and lock are all updated atomically
# ═════════════════════════════════════════════════════════════════════════════════


class TestH15AtomicityHappyPath:
    """H15: All three DB writes (job, instance, lock) commit together."""

    @pytest.mark.asyncio
    async def test_job_instance_lock_all_commit_together_completed(self, engine):
        """COMPLETED path: job→COMPLETED, instance→COMPLETED, lock deleted."""
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = seed_job(engine, instance_id=instance_id)
        lock_id = seed_lock(engine, instance_id=instance_id)

        assert get_job(engine, job.job_id).status == JobStatus.PROCESSING.value
        assert get_instance(engine, instance_id).status == InstanceStatus.RUNNING.value
        assert count_locks(engine, instance_id) == 1

        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job)
        with patched_completion_registry():
            await observer.handle_correlation_complete(instance_id, "completed")

        # Job transitioned to COMPLETED.
        assert get_job(engine, job.job_id).status == JobStatus.COMPLETED.value
        # Instance transitioned to COMPLETED.
        assert get_instance(engine, instance_id).status == InstanceStatus.COMPLETED.value
        # Lock was deleted (inlined in the sync helper's WriteGuardSession).
        assert count_locks(engine, instance_id) == 0

        # notify_watchers fired for the COMPLETED status.
        mocks["job_queue_service"].notify_watchers.assert_awaited_once()
        call = mocks["job_queue_service"].notify_watchers.call_args
        assert call.args[0] == job.job_id
        assert call.args[1] == "completed"

        # SSE / CompletionRegistry / lifecycle event fired.
        mocks["live_hub"].stream_status_change.assert_awaited_once()
        mocks["events_service"]._publish_instance_lifecycle_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_job_instance_lock_all_commit_together_error(self, engine):
        """ERROR path: job→FAILED, instance→ERROR, lock deleted."""
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = seed_job(engine, instance_id=instance_id, status=JobStatus.PROCESSING.value)
        lock_id = seed_lock(engine, instance_id=instance_id)

        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job)
        with patched_completion_registry():
            await observer.handle_correlation_complete(instance_id, "error")

        assert get_job(engine, job.job_id).status == JobStatus.FAILED.value
        assert get_instance(engine, instance_id).status == InstanceStatus.ERROR.value
        assert count_locks(engine, instance_id) == 0

        mocks["job_queue_service"].notify_watchers.assert_awaited_once()
        call = mocks["job_queue_service"].notify_watchers.call_args
        assert call.args[1] == "failed"


# ═════════════════════════════════════════════════════════════════════════════════
# H15.2 — Instance already terminal: job still transitions, dispatcher fires
# ═════════════════════════════════════════════════════════════════════════════════


class TestH15InstanceAlreadyTerminal:
    """Instance already in a terminal status when H15 writes run.

    The job still transitions (independent of instance state). The sync helper
    returns ``instance_was_terminal=True`` — dispatcher fires SSE/CR/lifecycle
    anyway (idempotent re-publish; CR.complete is safe to call twice).
    """

    @pytest.mark.asyncio
    async def test_instance_already_completed_job_still_transitions(self, engine):
        """Instance already COMPLETED: job→COMPLETED, dispatcher SKIPPED.

        When the instance was already terminal before our write, the
        dispatcher is intentionally skipped (``instance_was_terminal=True``)
        to avoid double-signaling ``CompletionRegistry`` and re-firing the
        SSE status_change event. Whoever set the instance terminal first
        (CM-disabled inline cascade or a prior callback) already fired
        those effects.
        """
        instance_id = seed_instance(engine, status=InstanceStatus.COMPLETED.value)
        job = seed_job(engine, instance_id=instance_id)
        lock_id = seed_lock(engine, instance_id=instance_id)

        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job)
        with patched_completion_registry() as (mock_registry, _):
            await observer.handle_correlation_complete(instance_id, "completed")

        # Job still transitions (independent of instance state).
        assert get_job(engine, job.job_id).status == JobStatus.COMPLETED.value
        # Lock still released.
        assert count_locks(engine, instance_id) == 0
        # Instance status is still COMPLETED (already terminal → write skipped).
        assert get_instance(engine, instance_id).status == InstanceStatus.COMPLETED.value
        # Dispatcher NOT fired — avoid double-signaling.
        mocks["live_hub"].stream_status_change.assert_not_called()
        mock_registry.complete.assert_not_called()
        mocks["events_service"]._publish_instance_lifecycle_event.assert_not_called()
        # But notify_watchers still fires (job is the watched entity).
        mocks["job_queue_service"].notify_watchers.assert_awaited_once()


# ═════════════════════════════════════════════════════════════════════════════════
# H15.3 — Instance missing: job still transitions, dispatcher skipped
# ═════════════════════════════════════════════════════════════════════════════════


class TestH15InstanceMissing:
    """Instance row missing when H15 writes run.

    The job still transitions (job and instance state are independent). The
    dispatcher is skipped because ``instance_was_terminal=True`` signals "no
    consumer to notify".
    """

    @pytest.mark.asyncio
    async def test_instance_missing_job_still_transitions(self, engine):
        """Instance missing: job→COMPLETED, no SSE/CR/lifecycle (no consumer)."""
        # No instance seeded — row does not exist.
        instance_id = f"ghost-{uuid.uuid4().hex[:8]}"
        job = seed_job(engine, instance_id=instance_id)
        lock_id = seed_lock(engine, instance_id=instance_id)

        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job)
        with patched_completion_registry() as (mock_registry, _):
            await observer.handle_correlation_complete(instance_id, "completed")

        # Job still transitions.
        assert get_job(engine, job.job_id).status == JobStatus.COMPLETED.value
        # Lock still released.
        assert count_locks(engine, instance_id) == 0
        # No SSE/CR/lifecycle (no instance to notify).
        mocks["live_hub"].stream_status_change.assert_not_called()
        mock_registry.complete.assert_not_called()
        mocks["events_service"]._publish_instance_lifecycle_event.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════════
# H15.4 — C1 TOCTOU abort: sync returns skip=True, nothing commits
# ═════════════════════════════════════════════════════════════════════════════════


class TestH15C1Abort:
    """CM ``get_pending_count > 0`` during C1 re-check → sync returns skip=True.

    The re-check is inside ``_finalize_job_db_sync`` (Phase 2 invariant: no
    await between re-check and UPDATE). When CM returns > 0, the helper returns
    ``skip=True`` and the caller returns without firing any side effects.
    """

    @pytest.fixture(autouse=True)
    def _seed_and_bus(self, engine):
        """Seed instance + job + lock, set up bus with pending correlation."""
        from daemon.services.dependency_bus import set_dependency_bus

        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = seed_job(engine, instance_id=instance_id)
        seed_lock(engine, instance_id=instance_id)

        # Wire a MagicMock bus that simulates a pending correlation.
        # The C1 re-check reads ``_pending`` (a Python dict, GIL-protected),
        # so this MagicMock-based test verifies the re-check branch fires
        # and the helper returns skip=True.
        bus = MagicMock()
        bus.count_pending_for_target_sync = MagicMock(return_value=1)  # 1 pending → abort

        self.instance_id = instance_id
        self.job_id = job.job_id
        self.job = job
        self._bus = bus
        set_dependency_bus(bus)
        yield
        set_dependency_bus(None)

    @pytest.mark.asyncio
    async def test_cm_pending_aborts_terminal_transition(self, engine):
        """CM pending > 0 → no job/instance/lock changes, no side effects."""
        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=self.job)
        with patched_completion_registry():
            await observer.handle_correlation_complete(self.instance_id, "completed")

        # Nothing changed.
        assert get_job(engine, self.job_id).status == JobStatus.PROCESSING.value
        assert get_instance(engine, self.instance_id).status == InstanceStatus.RUNNING.value
        assert count_locks(engine, self.instance_id) == 1  # lock NOT released

        # No side effects fired.
        mocks["job_queue_service"].notify_watchers.assert_not_called()
        mocks["live_hub"].stream_status_change.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════════
# H15.5 — InvalidTransitionError: sync raises, caller skips silently
# ═════════════════════════════════════════════════════════════════════════════════


class TestH15InvalidTransition:
    """``_finalize_job_db_sync`` raises ``InvalidTransitionError`` → idempotency.

    This happens when another actor (e.g., terminate_instance) already transitioned
    the job before our UPDATE's WHERE clause matched. The exception is caught by
    the caller, logged at DEBUG, and returns silently. No side effects fire.
    """

    @pytest.mark.asyncio
    async def test_concurrent_transition_raises_idempotency(self, engine):
        """Race: job already transitioned → InvalidTransitionError, no side effects."""
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = seed_job(engine, instance_id=instance_id)
        lock_id = seed_lock(engine, instance_id=instance_id)

        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job)

        # Patch the sync helper to simulate concurrent transition.
        # NOTE: must be sync (NOT async) because production calls it via
        # ``asyncio.to_thread`` which runs sync functions on a worker thread.
        # An async function here would return a coroutine instead of a
        # _FinalizeJobResult.
        def fake_sync(*args, **kwargs):
            raise InvalidTransitionError(
                job_id=job.job_id,
                from_status=JobStatus.COMPLETED.value,  # already COMPLETED
                to_status=JobStatus.COMPLETED.value,
            )

        observer._finalize_job_db_sync = fake_sync

        with patched_completion_registry():
            await observer.handle_correlation_complete(instance_id, "completed")

        # Nothing changed (no new write).
        assert get_job(engine, job.job_id).status == JobStatus.PROCESSING.value
        assert count_locks(engine, instance_id) == 1  # lock still held

        # No side effects.
        mocks["job_queue_service"].notify_watchers.assert_not_called()
        mocks["live_hub"].stream_status_change.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════════
# H15.6 — W3 fail-safe: sync raises generic Exception → fail-safe FAILED
# ═════════════════════════════════════════════════════════════════════════════════


class TestH15W3FailSafe:
    """Sync helper raises generic Exception → W3 fail-safe fires.

    The CM has already deleted ``_pending[parent_id]`` — the callback won't fire
    again. Without the fail-safe, the job would sit in PROCESSING forever. W3
    attempts a fail-safe ``atomic_transition`` to FAILED so the queue can advance.
    """

    @pytest.mark.asyncio
    async def test_sync_raises_fail_safe_transitions_to_failed(self, engine):
        """Generic exception from sync → W3 fires, job→FAILED, no side effects."""
        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = seed_job(engine, instance_id=instance_id)
        lock_id = seed_lock(engine, instance_id=instance_id)

        # Use a REAL JobRepository so the W3 fail-safe atomic_transition
        # actually writes to the DB.
        observer, mocks = make_observer(engine, real_job_repo=True)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job)

        # Patch sync helper to raise (sync, NOT async — see comment above).
        #
        # Phase 3 update: the W3 fail-safe catches generic ``Exception``
        # (e.g. ``OSError``, ``sqlalchemy.exc.DBAPIError``) but
        # ``RuntimeError`` is re-raised to preserve the A8 hard-error
        # invariant (CM=None / config errors must NOT be silently
        # converted to per-job FAILED). Use ``OSError`` here — it
        # represents an unexpected DB / IO failure that the W3
        # fail-safe is designed to recover from.
        def fake_sync(*args, **kwargs):
            raise OSError("Simulated sync failure")

        observer._finalize_job_db_sync = fake_sync

        with patched_completion_registry():
            await observer.handle_correlation_complete(instance_id, "completed")

        # Job was transitioned to FAILED by the fail-safe.
        assert get_job(engine, job.job_id).status == JobStatus.FAILED.value
        # Lock was NOT released by the fail-safe (W3 only transitions the job).
        assert count_locks(engine, instance_id) == 1

        # No post-commit side effects fired (the exception path skips them).
        mocks["job_queue_service"].notify_watchers.assert_not_called()
        mocks["live_hub"].stream_status_change.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════════
# M10 — Orphan cleanup: enqueue_message fails → terminate_instance called
# ═════════════════════════════════════════════════════════════════════════════════


class TestM10OrphanCleanup:
    """M10 fix: ``enqueue_message`` fails after ``spawn_instance_with_mcp``.

    Without M10, the spawned instance was orphaned (IDLE, no message, unreachable).
    With M10, ``terminate_instance`` is called on the orphaned instance before
    the job is marked FAILED, cleaning up the DB row + in-memory entry.
    """

    @pytest.fixture(autouse=True)
    def _seed_job_and_instance(self, engine):
        """Seed a completed job whose project has a next pending job."""
        self.completed_instance_id = seed_instance(
            engine, status=InstanceStatus.COMPLETED.value
        )
        self.completed_job = seed_job(
            engine,
            instance_id=self.completed_instance_id,
            status=JobStatus.PROCESSING.value,
        )
        # Next pending job in the same project.
        self.next_job = seed_job(
            engine,
            instance_id=None,
            project_id=self.completed_job.project_id,
            status=JobStatus.PENDING.value,
        )

    @pytest.mark.asyncio
    async def test_enqueue_failure_terminates_orphaned_instance(self, engine):
        """enqueue_message fails → terminate_instance called before job FAILED."""
        instance_id = seed_instance(
            engine,
            instance_id=self.next_job.job_id.replace("job-", "inst-"),
            status=InstanceStatus.IDLE.value,
        )

        # Pre-seed the "next" job to be PENDING.
        with Session(engine) as s:
            next_job_row = s.get(JobItem, self.next_job.job_id)
            next_job_row.instance_id = instance_id
            s.commit()

        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(
            return_value=self.completed_job
        )
        mocks["job_queue_service"]._get_next_job = AsyncMock(return_value=self.next_job)
        mocks["job_queue_service"].start_job = AsyncMock(
            return_value=JobItem(
                job_id=self.next_job.job_id,
                agent_id="coder",
                agent_dir="/tmp/agent",
                message="next job",
                source="api",
                job_type="task",
                status=JobStatus.PROCESSING.value,
                instance_id=instance_id,
                project_id=self.next_job.project_id,
            )
        )
        mocks["job_queue_service"].complete_job = AsyncMock(return_value=None)

        # spawn succeeds, enqueue FAILS.
        mocks["instance_manager"].spawn_instance_with_mcp = AsyncMock(
            return_value=instance_id
        )
        mocks["instance_manager"].enqueue_message = AsyncMock(
            side_effect=RuntimeError("enqueue failed")
        )
        mocks["instance_manager"].terminate_instance = AsyncMock(return_value=True)

        await observer.handle_correlation_complete(
            self.completed_instance_id, "completed"
        )

        # terminate_instance was called on the orphaned instance (M10 fix).
        mocks["instance_manager"].terminate_instance.assert_awaited_once_with(
            instance_id=instance_id
        )
        # Job was marked FAILED (after cleanup).
        mocks["job_queue_service"].complete_job.assert_awaited_once()
        call = mocks["job_queue_service"].complete_job.call_args
        assert call.kwargs.get("demand_state") is not None

    @pytest.mark.asyncio
    async def test_spawn_failure_does_not_call_terminate_instance(self, engine):
        """spawn_instance_with_mcp fails → job FAILED, no terminate call needed."""
        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(
            return_value=self.completed_job
        )

        next_job_item = JobItem(
            job_id=self.next_job.job_id,
            agent_id="coder",
            agent_dir="/tmp/agent",
            message="next job",
            source="api",
            job_type="task",
            status=JobStatus.PENDING.value,
            instance_id=None,
            project_id=self.next_job.project_id,
        )

        mocks["job_queue_service"]._get_next_job = AsyncMock(return_value=next_job_item)
        mocks["job_queue_service"].start_job = AsyncMock(return_value=next_job_item)
        mocks["job_queue_service"].complete_job = AsyncMock(return_value=None)

        # spawn FAILS → no instance to orphan.
        mocks["instance_manager"].spawn_instance_with_mcp = AsyncMock(
            side_effect=RuntimeError("spawn failed")
        )
        mocks["instance_manager"].terminate_instance = AsyncMock(return_value=True)

        await observer.handle_correlation_complete(
            self.completed_instance_id, "completed"
        )

        # terminate_instance was NOT called (no orphan exists).
        mocks["instance_manager"].terminate_instance.assert_not_called()
        # Job was still marked FAILED.
        mocks["job_queue_service"].complete_job.assert_awaited_once()


# ═════════════════════════════════════════════════════════════════════════════════
# H15 Thread-safety: _finalize_job_db_sync runs off the event loop
# ═════════════════════════════════════════════════════════════════════════════════


class TestH15ThreadOffload:
    """H15 sync helper must run off the event loop via ``asyncio.to_thread``."""

    @pytest.mark.asyncio
    async def test_finalize_job_db_sync_runs_off_loop_thread(self, engine):
        """The sync helper executes on a worker thread, not the event-loop thread."""
        import threading

        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = seed_job(engine, instance_id=instance_id)
        lock_id = seed_lock(engine, instance_id=instance_id)

        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job)

        thread_ids: list[int] = []

        original_sync = observer._finalize_job_db_sync

        def spy_sync(*args, **kwargs):
            thread_ids.append(threading.get_ident())
            return original_sync(*args, **kwargs)

        observer._finalize_job_db_sync = spy_sync

        with patched_completion_registry():
            await observer.handle_correlation_complete(instance_id, "completed")

        loop_thread = threading.get_ident()
        assert thread_ids, "_finalize_job_db_sync was never called"
        assert all(tid != loop_thread for tid in thread_ids), (
            f"FIX MISSING: _finalize_job_db_sync ran on the event-loop "
            f"thread (tid={loop_thread}); it MUST run in a worker thread "
            f"via asyncio.to_thread so its WriteGuardSession + commit "
            f"cannot wedge the loop under SQLite WAL contention."
        )

    @pytest.mark.asyncio
    async def test_finalize_job_db_sync_via_to_thread(self, engine):
        """``asyncio.to_thread`` must be called with ``_finalize_job_db_sync``."""
        import threading

        instance_id = seed_instance(engine, status=InstanceStatus.RUNNING.value)
        job = seed_job(engine, instance_id=instance_id)
        lock_id = seed_lock(engine, instance_id=instance_id)

        observer, mocks = make_observer(engine)
        mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job)

        func_names: list[str] = []
        original_to_thread = __import__("asyncio").to_thread

        async def spying_to_thread(func, *args, **kwargs):
            func_names.append(getattr(func, "__name__", repr(func)))
            return await original_to_thread(func, *args, **kwargs)

        with patch("daemon.services.job_feedback_observer.asyncio.to_thread", spying_to_thread):
            with patched_completion_registry():
                await observer.handle_correlation_complete(instance_id, "completed")

        assert "_finalize_job_db_sync" in func_names, (
            f"asyncio.to_thread was not invoked with _finalize_job_db_sync; "
            f"calls were: {func_names}"
        )
