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

Test 2 — Post-commit re-arm (orphan-race fix, 2026-06-20):
  Proves the GENERATION-COUNTER post-commit re-arm in ``_finalize_job``
  prevents orphaning when ``register_message_send(childB)`` arrives
  DURING the ``_finalize_job_db_sync`` execution window.

  Scenario (the actual orphan bug, NOT a false positive):
    1. Register child A → resolve child A → callback fires → _finalize_job
    2. While _finalize_job holds the per-parent lock (across to_thread),
       a register_message_send for child B is scheduled on the event loop.
    3. The register IMMEDIATELY bumps the generation counter (bump is
       outside the per-parent lock), then BLOCKS on the lock.
    4. _finalize_job_db_sync reads cm_pending = 0 (child B blocked on lock
       — its _pending entry is not yet visible). Gate passes. Job commits
       to COMPLETED.
    5. _finalize_job releases the lock. The blocked register acquires the
       lock and adds child B to _pending (pending = 1, gen = 2).
    6. _finalize_job reads post_gen > pre_gen → DETECTS the late register
       and re-arms the job COMPLETED → PROCESSING.
    7. child B can now resolve and find a PROCESSING job (no orphan).

  WITHOUT the post-commit re-arm (the orphan bug):
    - Step 6 does not happen. The job stays COMPLETED.
    - When child B resolves later, handle_correlation_complete looks for
      a PROCESSING job for the parent, finds NONE (it's COMPLETED), and
      silently skips — child B is ORPHANED.

  This test FAILS without the post-commit re-arm (verified by monkey-patching
  the re-arm branch out — see the ``test_no_rearm_orphan_bug`` helper).

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

pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed; tests CM-threading integration")

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

import pytest

pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed; tests CM lock/finalize threading")

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobItem, JobStatus
# CM-era imports removed in Phase 5 (CorrelationManager → DependencyBus).
# Tests in this module are skipped via ``pytestmark`` above.
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
            agent_id="developer",
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
    agent_id: str = "developer",
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
# Test 2: Post-commit re-arm (orphan-race fix)
# =============================================================================


@pytest.mark.asyncio
async def test_post_commit_rearm_prevents_orphan(engine: Engine):
    """Prove the generation-counter post-commit re-arm prevents orphaning.

    This is the CORRECT test for the orphan-race fix (2026-06-20). It replaces
    the previous false-positive test which pre-seeded child A (pending=1)
    before finalization, triggering the defer via child A — the concurrent
    child B was irrelevant and the test passed even with the fix disabled.

    The scenario this test exercises (the ACTUAL orphan bug):

      1. Register child A → resolve child A → callback → ``_finalize_job``
         starts. ``pre_gen`` captured (generation counter BEFORE lock).
      2. ``_finalize_job`` acquires ``cm._get_lock(instance_id)`` and calls
         ``asyncio.to_thread(_finalize_job_db_sync, ...)``.
      3. DURING the sync helper (worker thread, lock held on event loop),
         we schedule ``register_message_send(childB)`` on the event loop
         via ``run_coroutine_threadsafe``. The register:
           (a) Bumps the generation counter IMMEDIATELY (the bump is
               OUTSIDE the per-parent lock — visible to readers that hold
               the lock).
           (b) BLOCKS on ``async with cm._get_lock(instance_id)`` (the
               same lock _finalize_job holds).
      4. The sync helper reads ``cm_pending = 0`` (child B is blocked on
         the lock; its ``_pending`` entry is not yet visible) and
         ``cm.is_complete() = True`` → gate passes → job commits to
         COMPLETED.
      5. ``_finalize_job`` releases the lock. The blocked register
         acquires the lock and adds child B to ``_pending`` (pending=1).
      6. ``_finalize_job`` reads ``post_gen > pre_gen`` → DETECTS the late
         register → re-arms job COMPLETED → PROCESSING.
      7. Resolve child B → callback fires → ``_get_processing_job_for_instance``
         finds the re-armed PROCESSING job → ``_finalize_job`` commits to
         COMPLETED. No orphan.

    WITHOUT the post-commit re-arm:
      - Step 6 is skipped. Job stays COMPLETED.
      - Step 7: child B resolves, ``_get_processing_job_for_instance``
        returns None (job is COMPLETED, not PROCESSING), callback no-ops.
        Child B is ORPHANED.

    This test is verified NOT to be a false positive: see
    ``test_post_commit_rearm_can_be_disabled`` below, which monkey-patches
    the re-arm branch out and asserts the orphan manifests.
    """
    import time as _time

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
        completion_callback=None,  # Wired below after observer is built
        event_bus=None,
    )
    set_correlation_manager(cm)

    # ── Build observer ──────────────────────────────────────────────────────
    observer, _mocks = make_observer(engine, real_job_repo=True)

    # ``_get_processing_job_for_instance`` calls ``get_job_by_instance``;
    # return the seeded PROCESSING job. The mock keeps the in-memory
    # ``job_item`` reference alive; we keep its ``status`` attribute in sync
    # with the DB after each transition so the lookup returns the right
    # ``from_status`` for ``atomic_transition``.
    async def _get_job_by_instance(iid: str):
        return job_item

    _mocks["job_queue_service"].get_job_by_instance = _get_job_by_instance

    # ── Wire CM completion_callback → observer.handle_correlation_complete ─
    # Production flow: when ``resolve_response`` brings pending to 0, the CM
    # fires the completion_callback AFTER releasing the per-parent lock (W1
    # fix). The callback then calls ``handle_correlation_complete`` →
    # ``_finalize_job``, which re-acquires the lock for its own critical
    # section. This is the symmetric path used by the orphan-race fix test.
    async def _on_correlation_complete(parent_id: str, terminal_status: str) -> None:
        await observer.handle_correlation_complete(parent_id, terminal_status)

    cm._completion_callback = _on_correlation_complete

    # ── Register child A (only child A — NO pre-seeded pending) ────────────
    child_a, msg_a = f"child-A-{uuid.uuid4().hex[:4]}", str(uuid.uuid4())
    child_b, msg_b = f"child-B-{uuid.uuid4().hex[:4]}", str(uuid.uuid4())

    await cm.register_message_send(instance_id, child_a, msg_a)
    assert cm.get_pending_count(instance_id) == 1
    assert cm.get_generation(instance_id) == 1

    # ── Capture the event loop (must happen on the main thread) ────────────
    main_loop = asyncio.get_running_loop()

    # ── Patch _finalize_job_db_sync to inject the concurrent register ──────
    # We wrap the real sync helper so that DURING its execution (while the
    # per-parent lock is held on the event loop) we schedule the late
    # register on the event loop. The register bumps generation immediately
    # (before blocking on the lock), then blocks until the lock is released.
    register_done = asyncio.Event()
    original_finalize_sync = observer._finalize_job_db_sync

    def patched_finalize_sync(job_id, instance_id, terminal_status,
                              result_summary, error_message):
        """Inject a concurrent register during the sync helper window.

        Runs on the worker thread spawned by ``asyncio.to_thread``. The
        per-parent lock is held on the EVENT LOOP by ``_finalize_job``;
        we are on a separate thread and do NOT need the lock for this
        instrumentation.
        """
        # Schedule the late register on the captured event loop.
        async def _do_register():
            try:
                await cm.register_message_send(instance_id, child_b, msg_b)
            finally:
                register_done.set()

        asyncio.run_coroutine_threadsafe(_do_register(), main_loop)

        # Give the event loop time to run the register's generation bump.
        # The bump is the FIRST thing the coroutine does (before awaiting
        # the lock), so one event-loop tick is enough. The 50ms sleep is
        # generous headroom for slow CI; it does NOT affect correctness
        # because the register is now blocked on the lock regardless.
        _time.sleep(0.05)

        # Now run the REAL sync helper. The register is blocked on the
        # lock, so its _pending entry is NOT visible — the gate sees
        # cm_pending = 0 and commits the job to COMPLETED.
        return original_finalize_sync(
            job_id, instance_id, terminal_status,
            result_summary, error_message,
        )

    observer._finalize_job_db_sync = patched_finalize_sync

    # ── Resolve child A → fires handle_correlation_complete ────────────────
    # Production flow: ``notify_corr_resolve`` → ``cm.resolve_response`` →
    # remove child A (pending=0) → is_complete=True → completion_callback
    # fires → handle_correlation_complete → _finalize_job.
    # Inside _finalize_job:
    #   pre_gen=1 → lock acquired → to_thread(patched_finalize_sync)
    #   → register scheduled (gen bumped to 2, blocked on lock)
    #   → sync helper commits job COMPLETED → lock released
    #   → register acquires lock, adds child B (pending=1)
    #   → post_gen=2 > pre_gen=1 → RE-ARM job to PROCESSING
    await cm.resolve_response(instance_id, child_a, msg_a, status="responded")

    # Keep the mock's cached job_item.status in sync with the DB so the
    # second handle_correlation_complete lookup finds a PROCESSING job.
    job_item.status = JobStatus.PROCESSING.value

    # ── Wait for the concurrent register to finish ─────────────────────────
    # It was blocked on the lock during finalize; it should complete shortly
    # after _finalize_job released the lock (the post-gen re-arm reads the
    # generation AFTER the lock release, so the register must have either
    # completed or be about to — either way post_gen reflects the bump).
    await asyncio.wait_for(register_done.wait(), timeout=5.0)

    # Yield to the event loop so any post-re-arm outbox work that the
    # _finalize_job coroutine may schedule (notify_watchers, SSE,
    # _trigger_next_job) gets a chance to run BEFORE we assert it didn't.
    # Without this yield the assertions could race ahead of the outbox
    # and pass trivially even if the outbox was about to fire.
    for _ in range(5):
        await asyncio.sleep(0)

    # ── ASSERT 1a: post-commit outbox was SUPPRESSED during re-arm ─────────
    # The re-arm transitioned the job COMPLETED → PROCESSING. Terminal-side
    # outbox side effects (notify_watchers, SSE status_change, lifecycle
    # event, _trigger_next_job) are only valid for jobs that ACTUALLY
    # committed to a terminal state. The re-arm returned BEFORE the outbox,
    # so none of these should have been called. If the fix is missing, all
    # four would fire spuriously on a PROCESSING job — this assertion
    # catches that fall-through.
    assert not _mocks["job_queue_service"].notify_watchers.called, (
        "notify_watchers should NOT be called during re-arm — "
        "the job is back to PROCESSING, not terminal. "
        "If this fails, the _finalize_job post-commit re-arm block "
        "is falling through into the outbox (Fix 1 regression)."
    )
    assert not _mocks["live_hub"].stream_status_change.called, (
        "SSE stream_status_change should NOT be called during re-arm — "
        "the job is back to PROCESSING, not terminal."
    )
    assert not _mocks["events_service"]._publish_instance_lifecycle_event.called, (
        "lifecycle event should NOT be published during re-arm — "
        "the job is back to PROCESSING, not terminal."
    )
    assert not _mocks["job_queue_service"]._get_next_job.called, (
        "_get_next_job (via _trigger_next_job) should NOT be called "
        "during re-arm — the job is back to PROCESSING, not terminal."
    )

    # ── Restore the original sync helper ───────────────────────────────────
    # The patched sync helper injects a register during the to_thread window.
    # For the second finalize (child B's resolve) we want the REAL sync
    # helper — no more concurrent registers. The patched helper is the
    # instrument for the orphan-race scenario; child B's resolve is the
    # normal lifecycle completion path.
    observer._finalize_job_db_sync = original_finalize_sync

    # ── ASSERT 1: generation counter detected the late register ────────────
    # The job was committed to COMPLETED by the sync helper, then the
    # post-commit re-arm detected post_gen > pre_gen and transitioned it
    # back to PROCESSING. If the re-arm is missing/broken, the job stays
    # COMPLETED and child B is orphaned.
    re_read = re_read_job(engine, job_id)
    assert re_read is not None, f"Job row {job_id} disappeared from DB"
    assert re_read.status == JobStatus.PROCESSING.value, (
        f"Job should be re-armed to PROCESSING (late register detected via "
        f"generation counter), got {re_read.status}. The post-commit re-arm "
        f"is not working — child B is orphaned. "
        f"(pre_gen=1, post_gen={cm.get_generation(instance_id)})"
    )

    # ── ASSERT 2: child B is tracked in CM ─────────────────────────────────
    pending = cm.get_pending_count(instance_id)
    assert pending >= 1, (
        f"Expected at least 1 pending child (late register of child B), "
        f"got {pending}. The concurrent register did not complete."
    )

    # ── ASSERT 3: resolve child B finds the re-armed PROCESSING job ────────
    # This is the COMPLETE lifecycle proof: not just that the job was
    # re-armed, but that the late child can resolve and complete normally.
    # Without the re-arm, handle_correlation_complete would no-op (no
    # PROCESSING job found) and the job would stay PROCESSING forever
    # (orphaned — child B's resolve is silently dropped).
    #
    # We call ``resolve_response`` (the production hook) so the CM removes
    # child_b from _pending (pending → 0) and fires the completion_callback
    # → handle_correlation_complete → _finalize_job → COMPLETED.
    await cm.resolve_response(instance_id, child_b, msg_b, status="responded")

    # ── ASSERT 4: job is now COMPLETED (not orphaned) ──────────────────────
    re_read = re_read_job(engine, job_id)
    assert re_read is not None, f"Job row {job_id} disappeared from DB"
    assert re_read.status == JobStatus.COMPLETED.value, (
        f"Job should be COMPLETED after child B resolves, got {re_read.status}. "
        f"The late child's callback did not complete the re-armed job."
    )

    # Clean up.
    set_correlation_manager(None)


@pytest.mark.asyncio
async def test_post_commit_rearm_can_be_disabled(engine: Engine):
    """False-positive guard: prove Test 2 FAILS when the re-arm is disabled.

    This test exercises the SAME scenario as
    ``test_post_commit_rearm_prevents_orphan`` but monkey-patches the
    post-commit re-arm branch in ``_finalize_job`` to be a no-op. The job
    must then stay COMPLETED (not re-armed), proving the parent test is NOT
    a false positive — it genuinely depends on the re-arm logic.

    If this test ever PASSES (job gets re-armed despite the patch), the
    parent test is a false positive and must be rewritten.
    """
    import time as _time

    # ── Seed DB ────────────────────────────────────────────────────────────
    instance_id = f"inst-{uuid.uuid4().hex[:8]}"
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    seed_instance(engine, instance_id=instance_id, status=InstanceStatus.RUNNING.value)
    job_item = seed_job(
        engine, job_id=job_id, instance_id=instance_id, status=JobStatus.PROCESSING.value
    )

    cm = CorrelationManager(
        instance_repository=MagicMock(),
        message_queue_repository=MagicMock(),
        completion_callback=None,  # Wired below after observer is built
        event_bus=None,
    )
    set_correlation_manager(cm)

    observer, _mocks = make_observer(engine, real_job_repo=True)

    async def _get_job_by_instance(iid: str):
        return job_item

    _mocks["job_queue_service"].get_job_by_instance = _get_job_by_instance

    # Wire CM completion_callback → observer.handle_correlation_complete
    async def _on_correlation_complete(parent_id: str, terminal_status: str) -> None:
        await observer.handle_correlation_complete(parent_id, terminal_status)

    cm._completion_callback = _on_correlation_complete

    child_a, msg_a = f"child-A-{uuid.uuid4().hex[:4]}", str(uuid.uuid4())
    child_b, msg_b = f"child-B-{uuid.uuid4().hex[:4]}", str(uuid.uuid4())

    await cm.register_message_send(instance_id, child_a, msg_a)

    main_loop = asyncio.get_running_loop()
    register_done = asyncio.Event()
    original_finalize_sync = observer._finalize_job_db_sync

    def patched_finalize_sync(job_id, instance_id, terminal_status,
                              result_summary, error_message):
        async def _do_register():
            try:
                await cm.register_message_send(instance_id, child_b, msg_b)
            finally:
                register_done.set()

        asyncio.run_coroutine_threadsafe(_do_register(), main_loop)
        _time.sleep(0.05)
        return original_finalize_sync(
            job_id, instance_id, terminal_status,
            result_summary, error_message,
        )

    observer._finalize_job_db_sync = patched_finalize_sync

    # ── DISABLE the post-commit re-arm ─────────────────────────────────────
    # Monkey-patch ``cm.get_generation`` to always return 0 so the
    # ``post_gen > pre_gen`` condition is never true. This simulates the
    # absence of the orphan-race fix.
    cm.get_generation = lambda parent_id: 0  # type: ignore[assignment]

    # Resolve child A via the production hook (not handle_correlation_complete
    # directly) so the CM properly removes child_a from _pending (pending→0)
    # and fires the completion_callback → handle_correlation_complete →
    # _finalize_job. The sync helper's CM re-check then sees pending=0 and
    # commits the job to COMPLETED. With the re-arm disabled, the job
    # STAYS COMPLETED — the orphan manifests.
    await cm.resolve_response(instance_id, child_a, msg_a, status="responded")

    await asyncio.wait_for(register_done.wait(), timeout=5.0)

    # ── ASSERT: job stays COMPLETED (re-arm disabled → orphan manifests) ───
    re_read = re_read_job(engine, job_id)
    assert re_read is not None, f"Job row {job_id} disappeared from DB"
    assert re_read.status == JobStatus.COMPLETED.value, (
        f"Job should STAY COMPLETED when the post-commit re-arm is disabled "
        f"(simulating the bug), got {re_read.status}. This means the parent "
        f"test (test_post_commit_rearm_prevents_orphan) is a FALSE POSITIVE — "
        f"it does not actually depend on the re-arm logic and must be rewritten."
    )

    # Child B is orphaned: it's in _pending but the job is COMPLETED.
    assert cm.get_pending_count(instance_id) >= 1, (
        "Child B should be pending (orphaned against a COMPLETED job)"
    )

    set_correlation_manager(None)
