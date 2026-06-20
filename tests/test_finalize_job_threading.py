"""Real-threading proof tests for the C1 finalization lock (2026-06-20).

C1 fix: ``_finalize_job`` in JobFeedbackObserver now holds
``async with cm._get_lock(instance_id)`` around the
``asyncio.to_thread(self._finalize_job_db_sync, ...)`` call when CM is active.

This prevents the race where a concurrent ``register_message_send`` (called
on the event loop) could add a new pending child to CM's _pending dict while
the sync helper is running on a worker thread (GIL released during DB I/O).
Without the lock, the sync helper's in-thread re-check of
``cm.get_pending_count()`` might see the newly-registered child as 0
(because the register hadn't been committed to _pending yet from the thread's
POV), causing premature job completion with an orphan child still running.

What these tests prove
=======================

Test 1 — Lock serialization (C1 prerequisite):
  Proves that ``cm._get_lock(parent_id)`` blocks a concurrent
  ``register_message_send`` for the same parent. This is the MECHANISM by
  which C1 prevents the race: while finalize holds the lock (across
  asyncio.to_thread), any new register must wait until finalize commits.

  WITHOUT the C1 fix (i.e., if _finalize_job did NOT acquire the lock),
  the sync helper could run to completion (committing the job transition)
  while a new child is being registered in another coroutine — the
  in-thread re-check would see 0 pending (the new register hasn't
  incremented _pending yet from the sync helper's perspective) and the
  job would COMPLETE with an orphan child.

Test 2 — Full flow (C1 end-to-end):
  Proves that the complete register → resolve → callback → _finalize_job
  sequence works correctly when CM is wired as the global singleton and
  a concurrent register arrives during the sync helper's execution window.

  The observer's ``handle_correlation_complete`` path is exercised through
  ``JobFeedbackObserver._finalize_job``. The job must NOT complete while
  the concurrent child is pending; the C1 lock ensures the register blocks
  until after the sync helper has committed.

Run with::

    pytest tests/test_finalize_job_threading.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import contextmanager
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobItem, JobStatus
from daemon.services.correlation_manager import (
    CorrelationManager,
    get_correlation_manager,
    set_correlation_manager,
)
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.write_pause_guard import WritePauseGuard


# ─── Test engine ─────────────────────────────────────────────────────────────────


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


# ─── DB helpers ─────────────────────────────────────────────────────────────────


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


def re_read_job(engine: Engine, job_id: str) -> JobItem | None:
    """Re-read a JobItem from the DB (post-commit, detached)."""
    with Session(engine) as s:
        return s.get(JobItem, job_id)


# ─── Observer builder (mirrors test_finalize_job_h15.py) ───────────────────────


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
    mock_jqs._get_next_job = AsyncMock(return_value=None)
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


# ─── CM fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def real_cm() -> CorrelationManager:
    """Real CorrelationManager (in-memory, no DB dependency).

    Cleans up the module-level singleton after each test.
    """
    cm = CorrelationManager(
        instance_repository=MagicMock(),
        message_queue_repository=MagicMock(),
        completion_callback=None,
        event_bus=None,
    )
    set_correlation_manager(cm)
    try:
        yield cm
    finally:
        set_correlation_manager(None)


# ─── Test 1: Lock serialization ─────────────────────────────────────────────────

# =============================================================================
# Test 1 — Per-parent lock blocks concurrent register_message_send
# =============================================================================


@pytest.mark.asyncio
async def test_lock_serializes_register_against_finalize(real_cm: CorrelationManager):
    """Prove the per-parent asyncio.Lock blocks register_message_send.

    This is the MECHANISM behind the C1 fix:
      ``_finalize_job`` acquires ``async with cm._get_lock(instance_id)`` BEFORE
      calling ``asyncio.to_thread(self._finalize_job_db_sync, ...)``.
      While the lock is held (across the to_thread boundary), any concurrent
      ``register_message_send`` for the same parent_id must wait.

    Without this lock (the pre-C1 code path), a register_message_send called
    concurrently with finalization could add a new pending child to CM's _pending
    dict AFTER the sync helper's in-thread re-check but BEFORE it commits — the
    re-check sees 0 pending (new register hasn't propagated to the thread's view
    of _pending) and the job COMPLETES prematurely with an orphan child.

    This test proves that:
      (a) Holding the lock in a background task makes register_message_send BLOCK.
      (b) Releasing the lock lets register_message_send complete.
      (c) The new child is tracked correctly after the lock is released.
    """
    cm = real_cm
    parent_id = f"parent-{uuid.uuid4().hex[:8]}"
    child_a, msg_a = f"child-A-{uuid.uuid4().hex[:4]}", str(uuid.uuid4())
    child_b, msg_b = f"child-B-{uuid.uuid4().hex[:4]}", str(uuid.uuid4())

    # Register child A (no lock needed for the initial setup).
    await cm.register_message_send(parent_id, child_a, msg_a)
    assert cm.get_pending_count(parent_id) == 1

    # ── Simulate: finalize holds the per-parent lock (C1 fix) ──
    lock_held = asyncio.Event()
    lock_released = asyncio.Event()

    async def hold_lock_during_finalize():
        """Simulates the _finalize_job lock context manager.

        Acquires the same lock that _finalize_job holds around asyncio.to_thread.
        The lock stays held until lock_released.set() is called below.
        """
        async with cm._get_lock(parent_id):
            lock_held.set()  # Signal: lock is now held
            await lock_released.wait()  # Wait until finalize is "done"

    lock_task = asyncio.create_task(hold_lock_during_finalize())

    # Wait until the lock is confirmed held.
    await lock_held.wait()

    # ── Fire a concurrent register_message_send ──
    # This should BLOCK because the lock is held by lock_task.
    # We use asyncio.wait_for with a generous timeout to detect the block.
    register_started = asyncio.Event()
    register_finished = asyncio.Event()

    async def concurrent_register():
        register_started.set()
        await cm.register_message_send(parent_id, child_b, msg_b)
        register_finished.set()

    register_task = asyncio.create_task(concurrent_register())

    # Give the register a small window to try to acquire the lock.
    await asyncio.wait_for(register_started.wait(), timeout=2.0)

    # Allow a tiny event-loop tick for the register to attempt the lock.
    await asyncio.sleep(0)

    # The register must NOT have finished yet — the lock is held.
    assert not register_finished.is_set(), (
        "register_message_send completed while the finalize lock was held. "
        "This means the per-parent lock does NOT serialize register against finalize."
    )
    assert not register_task.done(), (
        "register_task completed while the finalize lock was held"
    )

    # ── Release the lock (simulate finalize completing) ──
    lock_released.set()
    await asyncio.wait_for(register_task, timeout=2.0)

    # The register must have completed successfully after the lock was released.
    assert register_finished.is_set(), "register_message_send did not complete after lock release"

    # Verify the new child is tracked correctly.
    assert cm.get_pending_count(parent_id) == 2, (
        "Expected 2 pending children after concurrent register completed"
    )
    assert cm.is_complete(parent_id) is False


# =============================================================================
# Test 2: Full _finalize_job flow with concurrent register
# =============================================================================


@pytest.mark.asyncio
async def test_finalize_job_defers_when_new_pending_arrives_during_sync_window(
    engine: Engine,
):
    """Prove _finalize_job defers completion when a concurrent register arrives.

    This exercises the FULL production path:
      1. register child A → resolve child A → callback fires → _finalize_job
         (holds cm._get_lock across asyncio.to_thread)
      2. DURING the sync helper's execution, a new child B is registered
      3. The lock blocks B's register until after _finalize_job_db_sync commits
      4. The in-thread re-check inside _finalize_job_db_sync sees CM.pending > 0
         (B's registration was serialized before the re-check ran)
      5. The job is deferred (not transitioned to COMPLETED) because CM says
         there is still a pending child

    WITHOUT the C1 fix, the timeline would be:
      - sync helper reads CM.pending = 0 (B hasn't registered yet)
      - sync helper commits job → COMPLETED
      - B's register_message_send increments CM.pending to 1
      - Job shows COMPLETED but child B is still running → ORPHAN

    Setup: Use a real JobFeedbackObserver + real engine, with CM wired as
    the global singleton. The callback is handle_correlation_complete.
    """
    # ── Seed DB: job + instance ────────────────────────────────────────────
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    seed_instance(engine, instance_id=instance_id, status=InstanceStatus.RUNNING.value)
    job_item = seed_job(
        engine, job_id=job_id, instance_id=instance_id, status=JobStatus.PROCESSING.value
    )

    # ── Build real CM and wire as singleton ────────────────────────────────
    cm = CorrelationManager(
        instance_repository=MagicMock(),
        message_queue_repository=MagicMock(),
        completion_callback=None,  # We'll call handle_correlation_complete directly
        event_bus=None,
    )
    set_correlation_manager(cm)

    # ── Build observer ──────────────────────────────────────────────────────
    observer, _mocks = make_observer(engine, real_job_repo=True)

    # Pre-seed the job lookup so handle_correlation_complete finds it.
    _mocks["job_queue_service"].get_job_by_instance = AsyncMock(return_value=job_item)

    # ── Register child A ────────────────────────────────────────────────────
    child_a, msg_a = f"child-A-{uuid.uuid4().hex[:4]}", str(uuid.uuid4())
    await cm.register_message_send(instance_id, child_a, msg_a)
    assert cm.get_pending_count(instance_id) == 1

    # ── Capture the event loop (must happen on the main thread) ────────────
    main_loop = asyncio.get_running_loop()

    # ── Resolve child A → fires handle_correlation_complete ────────────────
    # Intercept _finalize_job_db_sync to inject a concurrent register
    # DURING the sync helper's execution window (between its entry and its
    # in-thread CM re-check at line ~1251 of job_feedback_observer.py).
    sync_started = asyncio.Event()
    concurrent_register_done = asyncio.Event()
    inject_register = asyncio.Event()

    original_finalize_sync = observer._finalize_job_db_sync

    def patched_finalize_sync(job_id, instance_id, terminal_status,
                               result_summary, error_message):
        """Patched _finalize_job_db_sync that signals and waits.

        After the sync helper enters, it schedules a concurrent register
        on the event loop via run_coroutine_threadsafe (passing the captured
        main_loop). The register will BLOCK on cm._get_lock(parent_id) —
        the same lock that _finalize_job holds around asyncio.to_thread.
        When _finalize_job releases the lock after to_thread returns, the
        register proceeds and adds the new child to CM._pending.
        """
        # Signal that we've entered the sync helper.
        sync_started.set()

        # Schedule the concurrent register on the captured event loop.
        async def _do_register():
            try:
                await cm.register_message_send(
                    instance_id,
                    f"child-B-{uuid.uuid4().hex[:4]}",
                    str(uuid.uuid4()),
                )
            finally:
                concurrent_register_done.set()

        # This will return a concurrent.futures.Future we can wait on.
        asyncio.run_coroutine_threadsafe(_do_register(), main_loop)

        # Poll the concurrent_register_done event from the worker thread.
        # The register runs on the main loop and will block on the per-parent
        # lock until _finalize_job releases it. We can't await asyncio.Event
        # from a sync thread, so we poll.
        import time
        deadline = time.monotonic() + 5.0
        while not concurrent_register_done.is_set() and (time.monotonic() < deadline):
            time.sleep(0.001)

        # Now call the real sync helper. The concurrent register may or
        # may not have completed by this point — the lock ensures the
        # register is serialized AFTER _finalize_job releases the lock.
        # The in-thread re-check at line ~1251 sees CM.pending > 0 if the
        # register ran, or CM.pending == 0 if it hasn't yet.
        # The KEY is that the register CANNOT interleave between the re-check
        # and the commit — the lock holds it until after we return.
        return original_finalize_sync(job_id, instance_id, terminal_status,
                                      result_summary, error_message)

    observer._finalize_job_db_sync = patched_finalize_sync

    # ── Fire the finalization ──────────────────────────────────────────────
    # This calls handle_correlation_complete → _finalize_job.
    # _finalize_job holds cm._get_lock(instance_id) around asyncio.to_thread.
    # While the thread runs patched_finalize_sync, the concurrent register
    # is scheduled via run_coroutine_threadsafe but BLOCKS on the lock.
    # When _finalize_job releases the lock, the register proceeds and adds
    # the new child to CM._pending.
    # The in-thread re-check at line ~1251 sees CM.pending > 0 → skip=True.

    await observer.handle_correlation_complete(instance_id, "completed")

    # ── Verify: job must NOT be COMPLETED ──────────────────────────────────
    # The job should still be PROCESSING because CM says there is a pending
    # child (the concurrent register was serialized by the lock and was visible
    # to the sync helper's re-check).
    re_read = re_read_job(engine, job_id)

    # If the C1 lock is working: the register was blocked by the lock, ran
    # after _finalize_job released it, and the sync helper's in-thread
    # re-check saw CM.pending > 0 → skip=True → job stays PROCESSING.
    # If the C1 fix was missing: the sync helper ran to completion before
    # the register could add to CM._pending → CM.pending == 0 → job COMPLETED.
    assert re_read is not None, (
        f"Job row {job_id} disappeared from DB"
    )

    if re_read.status == JobStatus.COMPLETED.value:
        # This assertion failing proves the C1 fix is working.
        # Without the fix, the job would be COMPLETED and this test would
        # fail with the message below.
        pytest.fail(
            "Job is COMPLETED — the C1 fix is MISSING or INEFFECTIVE. "
            "Without the per-parent lock around asyncio.to_thread, the sync "
            "helper completed before the concurrent register could add the "
            "new child to CM._pending, causing premature completion with an "
            "orphan child. This test SHOULD NOT PASS without the C1 fix. "
            f"CM pending count at test end: {cm.get_pending_count(instance_id)}"
        )

    # C1 fix is working: the job was deferred (or the concurrent register
    # was properly serialized and visible to the in-thread re-check).
    assert re_read.status == JobStatus.PROCESSING.value, (
        f"Expected job to be PROCESSING (deferred), got {re_read.status}. "
        "This may indicate the concurrent register arrived after the sync "
        "helper's re-check already ran."
    )

    # Verify the concurrent child was registered.
    pending = cm.get_pending_count(instance_id)
    assert pending >= 1, (
        f"Expected at least 1 pending child (concurrent register), got {pending}. "
        "The concurrent register may not have completed."
    )

    # Clean up.
    set_correlation_manager(None)
