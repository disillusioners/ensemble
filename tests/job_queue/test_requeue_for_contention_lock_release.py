"""Tests for ``MessageJobHandler._requeue_for_contention`` (M13 fix).

M13 — ``_requeue_for_contention`` must ALWAYS release the per-queue
lock, even when ``atomic_transition`` returns ``None`` (already
transitioned by another process) or raises. Pre-fix, an early
``return`` on ``result is None`` leaked the lock permanently: the
lock row had our ``job_id`` but no caller would ever clean it up.

These tests pin down the new contract:

  1. Happy path: transition succeeds, lock is released, dispatch
     bus is notified.
  2. Transition returns None (already transitioned by another
     process): lock is STILL released, dispatch bus is notified.
  3. Transition raises: lock is STILL released, exception is
     suppressed (logged but not propagated).
  4. Lock release itself raises: dispatch bus notify still runs;
     no exception escapes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.message_job_handler import MessageJobHandler
from daemon.services.job_queue_service import JobQueueService


# ── Test fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_manager():
    """Bare mock manager — we only need a ``_process_message_with_tracking``
    stub for handler construction; this test does not invoke ``handle``.
    """
    manager = MagicMock()
    manager._process_message_with_tracking = AsyncMock(
        return_value=MagicMock(content="ok", tool_calls=None)
    )
    return manager


@pytest.fixture
def make_handler(mock_manager, job_queue_service, repository):
    """Factory that builds a MessageJobHandler around a job_queue_service
    whose ``_lock_manager`` and ``_job_repo`` we can mutate per-test.
    """
    def _factory():
        return MessageJobHandler(
            manager=mock_manager,
            job_queue_service=job_queue_service,
            job_repository=repository,
        )
    return _factory


@pytest.fixture
def make_processing_message_job(repository, queue_repository_with_system_queues, sample_job_data):
    """Create a MESSAGE job in PROCESSING state. Returns the job.

    The caller is responsible for acquiring a queue lock for the job
    (so that ``_requeue_for_contention`` has something to release).
    """
    def _factory():
        # Resolve queue_id from the system queues pre-provisioned by
        # queue_repository_with_system_queues. ``sample_job_data``
        # only sets project_id, not queue_id, so we pick a known
        # system queue explicitly.
        sys_queue = queue_repository_with_system_queues.get_by_name(
            "test-project", "system_parallel_queue",
        )
        queue_id = sys_queue.queue_id if sys_queue else None

        job = repository.create(
            **sample_job_data,
            job_type="message",
            instance_id="test-instance-m13",
            queue_id=queue_id,
        )
        repository.start_job(job.job_id, "test-instance-m13")
        return job
    return _factory


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestRequeueForContentionReleasesLockOnHappyPath:
    """Sanity baseline: the happy path was already correct."""

    @pytest.mark.asyncio
    async def test_lock_released_after_successful_transition(
        self, make_handler, make_processing_message_job, job_queue_service, lock_repo
    ):
        handler = make_handler()
        job = make_processing_message_job()

        # Acquire the queue lock the same way the production code does.
        acquired = await job_queue_service._lock_manager.acquire_queue_lock(
            project_id=job.project_id,
            queue_id=job.queue_id,
            job_id=job.job_id,
            instance_id=job.instance_id,
            concurrency_limit=3,
        )
        assert acquired is True

        await handler._requeue_for_contention(job, "happy path test")

        # Lock for this job is gone.
        active = lock_repo.get_active_locks(job.project_id, job.queue_id)
        active_job_ids = {l.job_id for l in active}
        assert job.job_id not in active_job_ids

        # Job is back to PENDING.
        from daemon.repositories.job_queue.models import JobStatus
        updated = handler._job_repo.get(job.job_id)
        assert updated.status == JobStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_dispatch_bus_notified_after_successful_transition(
        self, make_handler, make_processing_message_job, job_queue_service
    ):
        handler = make_handler()
        job = make_processing_message_job()

        await job_queue_service._lock_manager.acquire_queue_lock(
            project_id=job.project_id, queue_id=job.queue_id,
            job_id=job.job_id, instance_id=job.instance_id,
            concurrency_limit=3,
        )

        # Wire a fake dispatch bus.
        bus = MagicMock()
        bus.notify_new_job = MagicMock()
        job_queue_service._dispatch_bus = bus

        await handler._requeue_for_contention(job, "happy path")

        bus.notify_new_job.assert_called_once_with(job.project_id)


class TestRequeueForContentionReleasesLockWhenTransitionIsNoOp:
    """M13 regression: if ``atomic_transition`` returns ``None`` (the
    job was already transitioned by another process), the lock must
    STILL be released. Pre-fix this early-returned and leaked the
    lock permanently.
    """

    @pytest.mark.asyncio
    async def test_lock_released_even_when_transition_returns_none(
        self, make_handler, make_processing_message_job, job_queue_service
    ):
        handler = make_handler()
        job = make_processing_message_job()

        await job_queue_service._lock_manager.acquire_queue_lock(
            project_id=job.project_id, queue_id=job.queue_id,
            job_id=job.job_id, instance_id=job.instance_id,
            concurrency_limit=3,
        )

        # Simulate the race: another process already transitioned the
        # job out of PROCESSING. ``atomic_transition`` returns None.
        # Synchronous: the handler wraps it in asyncio.to_thread, so
        # the callable itself doesn't need to be a coroutine.
        def fake_atomic_transition(*args, **kwargs):
            return None
        handler._job_repo.atomic_transition = fake_atomic_transition

        await handler._requeue_for_contention(job, "race: already transitioned")

        # The critical assertion: the lock is GONE even though
        # atomic_transition was a no-op.
        active = job_queue_service._lock_manager._lock_repo.get_active_locks(
            job.project_id, job.queue_id,
        )
        active_job_ids = {l.job_id for l in active}
        assert job.job_id not in active_job_ids, (
            "M13: lock leaked when atomic_transition returned None"
        )


class TestRequeueForContentionReleasesLockWhenTransitionRaises:
    """M13 regression: if ``atomic_transition`` raises, the lock must
    STILL be released. Pre-fix the exception escaped before reaching
    the release line.
    """

    @pytest.mark.asyncio
    async def test_lock_released_when_transition_raises(
        self, make_handler, make_processing_message_job, job_queue_service
    ):
        handler = make_handler()
        job = make_processing_message_job()

        await job_queue_service._lock_manager.acquire_queue_lock(
            project_id=job.project_id, queue_id=job.queue_id,
            job_id=job.job_id, instance_id=job.instance_id,
            concurrency_limit=3,
        )

        # Simulate the transition failing (DB blip, schema mismatch,
        # anything). Synchronous: the handler wraps it in
        # asyncio.to_thread, so the callable itself doesn't need to
        # be a coroutine.
        def fake_atomic_transition(*args, **kwargs):
            raise RuntimeError("simulated DB error")
        handler._job_repo.atomic_transition = fake_atomic_transition

        # Must not raise.
        await handler._requeue_for_contention(job, "race: transition error")

        active = job_queue_service._lock_manager._lock_repo.get_active_locks(
            job.project_id, job.queue_id,
        )
        active_job_ids = {l.job_id for l in active}
        assert job.job_id not in active_job_ids, (
            "M13: lock leaked when atomic_transition raised"
        )


class TestRequeueForContentionHandlesLockReleaseError:
    """If ``release_queue_lock`` itself errors, the dispatch bus
    notify still runs and no exception escapes. Pre-fix the
    exception propagated and aborted the handler.
    """

    @pytest.mark.asyncio
    async def test_dispatch_bus_still_called_when_release_errors(
        self, make_handler, make_processing_message_job, job_queue_service
    ):
        handler = make_handler()
        job = make_processing_message_job()

        await job_queue_service._lock_manager.acquire_queue_lock(
            project_id=job.project_id, queue_id=job.queue_id,
            job_id=job.job_id, instance_id=job.instance_id,
            concurrency_limit=3,
        )

        # Force release to raise.
        async def fake_release(*args, **kwargs):
            raise RuntimeError("simulated release error")
        job_queue_service._lock_manager.release_queue_lock = fake_release

        bus = MagicMock()
        bus.notify_new_job = MagicMock()
        job_queue_service._dispatch_bus = bus

        # Must not raise.
        await handler._requeue_for_contention(job, "release error path")

        # Dispatch bus still called.
        bus.notify_new_job.assert_called_once_with(job.project_id)


class TestRequeueForContentionSkipsLockReleaseWhenProjectOrQueueMissing:
    """When job.project_id or job.queue_id is missing, the lock
    cannot be released (we don't know which (project, queue) pair
    it belongs to). Pre-fix and post-fix this should be a no-op
    for the lock-release step. The transition + dispatch bus notify
    must still run.
    """

    @pytest.mark.asyncio
    async def test_no_lock_release_when_queue_id_missing(
        self, make_handler, make_processing_message_job, job_queue_service
    ):
        handler = make_handler()
        job = make_processing_message_job()

        # Strip queue_id.
        job.queue_id = None

        # Spy on release_queue_lock to confirm it is NOT called.
        spy = MagicMock(wraps=job_queue_service._lock_manager.release_queue_lock)
        job_queue_service._lock_manager.release_queue_lock = spy

        # Must not raise.
        await handler._requeue_for_contention(job, "no queue_id")

        spy.assert_not_called()
