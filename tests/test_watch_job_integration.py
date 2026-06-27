"""Integration tests for the watch_job -> CM pending_jobs -> completion callback chain.

Tests the B1-B4 implementation:
  - B1: ParentCorrelation.pending_jobs set + is_complete() checks both pending and pending_jobs
  - B2: CM.register_job_send / CM.resolve_job + module helpers notify_corr_register_job / notify_corr_resolve_job
  - B3: watch_job calls notify_corr_register_job
  - B4: _finalize_job post-commit outbox calls notify_corr_resolve_job

All tests run at the CorrelationManager level (unit tests, SQLite/mocks, NOT PostgreSQL).
The CM is the authoritative source of truth for completion — if these tests pass, the
watch_job -> completion chain is correct regardless of the observer wiring.

Test organization:
  Category 1 (TestPendingJobsTracking): basic pending_jobs register/resolve
  Category 2 (TestMixedMessageAndJob): combined message + job correlations
  Category 3 (TestGenerationCounter): generation counter bumps for re-arm detection
  Category 4 (TestWatchJobOrphanProtection): Variant B orphan-race regression
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

import pytest

pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed; tests watch_job-CM integration")

# CM-era imports removed in Phase 5 (CorrelationManager → DependencyBus).
# Tests in this module are skipped via ``pytestmark`` above.


# =============================================================================
# Local helpers (mirroring tests/test_correlation_manager.py patterns)
# =============================================================================


def make_instance_repo() -> MagicMock:
    """Build a mock SQLModelInstanceRepository (CM shadow validation reads it)."""
    repo = MagicMock(name="InstanceRepo")
    repo.list = MagicMock(return_value=([], 0))
    repo.get_all_with_waiting_for = MagicMock(return_value=[])
    repo.get_children = MagicMock(return_value=[])
    repo.get = MagicMock(return_value=None)
    return repo


def make_msg_repo() -> MagicMock:
    """Build a mock SQLModelMessageQueueRepository."""
    repo = MagicMock(name="MsgRepo")
    repo.list = MagicMock(return_value=[])
    repo.get_pending_for_instances = MagicMock(return_value=[])
    return repo


def make_callback() -> tuple[list, Any]:
    """Build a completion_callback that records every invocation."""
    recorder: list[tuple[str, str]] = []

    async def _cb(parent_id: str, terminal_status: str) -> None:
        recorder.append((parent_id, terminal_status))

    _cb.calls = recorder  # type: ignore[attr-defined]
    return recorder, _cb


def make_cm(*, callback: Any = None) -> CorrelationManager:
    """Instantiate a CM with sensible mocked defaults."""
    return CorrelationManager(
        instance_repository=make_instance_repo(),
        message_queue_repository=make_msg_repo(),
        completion_callback=callback,
    )


# =============================================================================
# Category 1 — Basic pending_jobs tracking
# =============================================================================


class TestPendingJobsTracking:
    """Tests for the basic register_job_send / resolve_job lifecycle."""

    @pytest.mark.asyncio
    async def test_register_job_marks_incomplete(self):
        """Registering a single watched job keeps the parent incomplete with count=1."""
        cm = make_cm()
        parent = "parent-1"
        job1 = "job-aaa"

        await cm.register_job_send(parent, job1)

        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 1

    @pytest.mark.asyncio
    async def test_register_then_resolve_job_completes(self):
        """Register one job then resolve it -> callback fires (completed), parent cleaned up."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        job1 = "job-aaa"

        await cm.register_job_send(parent, job1)
        result = await cm.resolve_job(parent, job1)  # default status=STATUS_RESPONDED

        assert result is True, "last correlation should report True"
        assert recorder == [(parent, "completed")], "callback should fire exactly once with completed"
        assert cm.is_complete(parent) is True, "after completion parent is untracked -> is_complete returns True"
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_multiple_jobs_resolve_one_still_pending(self):
        """Two registered jobs, resolving only one keeps parent incomplete, no callback yet."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        job1, job2 = "job-1", "job-2"

        await cm.register_job_send(parent, job1)
        await cm.register_job_send(parent, job2)

        result = await cm.resolve_job(parent, job1)

        assert result is False, "not the last correlation"
        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 1
        assert recorder == [], "callback must NOT fire while a job is still pending"


# =============================================================================
# Category 2 — Mixed message and job correlations
# =============================================================================


class TestMixedMessageAndJob:
    """Tests combining message correlations with watched-job correlations."""

    @pytest.mark.asyncio
    async def test_message_and_job_both_pending_neither_alone_completes(self):
        """Message + job correlation together: resolving only one leaves parent incomplete."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"

        # Register one message and one job
        await cm.register_message_send(parent, "childA", "msgA")
        await cm.register_job_send(parent, "job1")

        assert cm.get_pending_count(parent) == 2, "one message + one job = two pending"

        # Resolve only the message — job1 still pending, NOT complete
        await cm.resolve_response(parent, "childA", "msgA")

        assert cm.is_complete(parent) is False
        assert cm.get_pending_count(parent) == 1
        assert recorder == [], "callback must not fire while watched job is still pending"

        # Now resolve the watched job — parent finally completes
        await cm.resolve_job(parent, "job1")

        assert recorder == [(parent, "completed")]
        assert cm.get_pending_count(parent) == 0

    @pytest.mark.asyncio
    async def test_job_completes_first_then_message(self):
        """Order-swap: resolving job first leaves parent incomplete until message also resolves."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"

        await cm.register_message_send(parent, "childA", "msgA")
        await cm.register_job_send(parent, "job1")

        # Resolve job FIRST — message still pending
        await cm.resolve_job(parent, "job1")

        assert cm.is_complete(parent) is False, "message still pending -> parent incomplete"
        assert recorder == [], "callback must not fire while message correlation is pending"

        # Now resolve the message
        await cm.resolve_response(parent, "childA", "msgA")

        assert recorder == [(parent, "completed")]

    @pytest.mark.asyncio
    async def test_error_status_on_job_yields_error_terminal(self):
        """Resolving a watched job with STATUS_ERROR sets had_error -> terminal_status='error'."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"
        job1 = "job-aaa"

        await cm.register_job_send(parent, job1)
        result = await cm.resolve_job(parent, job1, status=STATUS_ERROR)

        assert result is True
        assert recorder == [(parent, "error")], "had_error flips terminal_status from completed to error"


# =============================================================================
# Category 3 — Generation counter integration
# =============================================================================


class TestGenerationCounter:
    """Tests for the generation counter that powers the re-arm gate."""

    @pytest.mark.asyncio
    async def test_register_job_send_bumps_generation(self):
        """Each register_job_send call monotonically bumps the per-parent generation counter."""
        cm = make_cm()
        parent = "parent-1"
        job1, job2 = "job-1", "job-2"

        assert cm.get_generation(parent) == 0, "untracked parent starts at 0"

        await cm.register_job_send(parent, job1)
        assert cm.get_generation(parent) == 1

        await cm.register_job_send(parent, job2)
        assert cm.get_generation(parent) == 2

    @pytest.mark.asyncio
    async def test_resolve_job_also_bumps_generation(self):
        """resolve_job also bumps the generation counter (symmetric with register_job_send).

        # In production, _finalize_job reads get_generation() before/after its DB commit.
        # A change means a register was in-flight -> re-arm (COMPLETED->PROCESSING).
        # This test proves the counter changes on resolve, which is the symmetric
        # bump that makes the re-arm gate fire correctly when a concurrent
        # register_job_send lands during finalization.
        """
        cm = make_cm()
        parent = "parent-1"
        job1 = "job-1"

        await cm.register_job_send(parent, job1)
        gen_before = cm.get_generation(parent)
        assert gen_before == 1

        await cm.resolve_job(parent, job1)
        gen_after = cm.get_generation(parent)

        assert gen_after > gen_before, (
            "resolve_job must bump the generation counter so _finalize_job's "
            "before/after comparison can detect an in-flight register"
        )


# =============================================================================
# Category 4 — watch_job orphan protection (Variant B regression)
# =============================================================================


class TestWatchJobOrphanProtection:
    """Tests proving the CM state that prevents premature completion of a parent
    whose message children have all responded but whose watched job is still running.
    """

    @pytest.mark.asyncio
    async def test_watched_job_keeps_parent_alive_after_messages_resolve(self):
        """Full watch_job flow at CM level: message resolves but watched job still pending -> no callback."""
        recorder, cb = make_callback()
        cm = make_cm(callback=cb)
        parent = "parent-1"

        # Simulate: parent calls watch_job (register_job_send) AND sends a message.
        await cm.register_message_send(parent, "childA", "msgA")
        await cm.register_job_send(parent, "watched-job-1")

        # The message child responds, but the watched job is still running.
        await cm.resolve_response(parent, "childA", "msgA")

        # Parent must NOT be complete yet — watched job still pending.
        assert cm.is_complete(parent) is False
        assert recorder == [], "callback must not fire while watched job is still pending"

        # The watched job finally completes.
        await cm.resolve_job(parent, "watched-job-1")

        # NOW complete.
        assert recorder == [(parent, "completed")]


# =============================================================================
# Category 5 — Phase B reviewer fixes (B-F1, B-W3)
# =============================================================================


class TestReviewerFixes:
    """Tests proving the Phase B reviewer fixes are wired correctly.

    These tests are DETERMINISTIC: each one builds the minimum fixture set
    required to exercise one specific behaviour (wiring, ordering, or
    rebuild) without depending on a live DB, EventBus, or HTTP stack.

    * B-F1: ``init_correlation_manager`` must read the SAME attribute name
      (``_watcher_repo``) that ``daemon/api.py:249`` writes. A typo
      (``_watcher_repository`` vs ``_watcher_repo``) leaves the CM's
      ``_watcher_repo`` as None, silently disabling Step 5 of
      ``rebuild_from_db`` (watched-job crash recovery).
    * B-W3: ``_finalize_job`` must call ``get_watchers_for_job`` BEFORE
      ``notify_watchers`` (which deletes watcher rows) and
      ``notify_corr_resolve_job`` AFTER ``notify_watchers``. Reordering
      either step would either: (a) drain CM's ``pending_jobs`` set with
      an empty list (get_watchers after notify_watchers), or (b) race with
      the watcher notification that the parent is also expecting.
    * B-W3: ``rebuild_from_db`` Step 5 must reconstruct ``pending_jobs``
      from the watcher repository. Without this, watched-job parents get
      empty ``pending_jobs`` after restart → Variant B orphan survives
      a crash.
    """

    @pytest.mark.asyncio
    async def test_init_correlation_manager_wires_watcher_repo(self):
        """B-F1: ``init_correlation_manager`` must set ``cm._watcher_repo``
        to the manager's ``_watcher_repo`` attribute. A typo
        (``_watcher_repository``) leaves it ``None``, disabling crash
        recovery for watched jobs.

        Regression guard: the previous code did
        ``getattr(manager, "_watcher_repository", None)`` which always
        returned None because ``daemon/api.py:249`` writes
        ``_watcher_repo`` (no "y"). This test would have failed with
        ``assert cm._watcher_repo is not None``.

        Phase 5: skipped via module-level pytestmark.
        """
        # CM-era imports removed in Phase 5; test body below is dead code.

        # Build a fake manager with a watcher_repo wired up (the
        # way ``daemon/api.py:249`` sets it on the real InstanceManager).
        watcher_repo_mock = MagicMock(name="JobWatcherRepository")
        manager = MagicMock(name="InstanceManager")
        manager._watcher_repo = watcher_repo_mock
        manager._instance_repository = make_instance_repo()
        manager._queue_repository = make_msg_repo()
        manager._event_bus = MagicMock(name="EventBus")

        # Mock app with a state attribute that accepts attribute writes.
        app = MagicMock(name="FastAPI app")
        app.state = MagicMock(name="app.state")

        # Make correlation_manager.start() a no-op so we don't have to
        # wire an EventBus subscription for this test.
        original_start = CorrelationManager.start

        async def _noop_start(self) -> None:
            return None

        CorrelationManager.start = _noop_start  # type: ignore[assignment]
        registered_cm: Any = None
        try:
            await init_correlation_manager(app, manager, completion_callback=None)
            # Pull the registered CM BEFORE resetting the singleton below.
            registered_cm = cm_module.get_correlation_manager()
        finally:
            CorrelationManager.start = original_start  # type: ignore[assignment]
            # Reset the module-level singleton so subsequent tests aren't
            # affected by this one.
            cm_module.set_correlation_manager(None)

        assert registered_cm is not None, (
            "init_correlation_manager must register the new CM via "
            "set_correlation_manager — None means the init failed silently"
        )
        assert registered_cm._watcher_repo is not None, (
            "B-F1 regression: cm._watcher_repo is None — the typo "
            "(_watcher_repository vs _watcher_repo) is back. The "
            "watcher_repo wiring is broken and crash recovery for "
            "watched jobs is silently disabled."
        )
        # Sanity: the CM should hold the SAME mock object the manager
        # exposes (passed by reference, not copied).
        assert registered_cm._watcher_repo is watcher_repo_mock

    @pytest.mark.asyncio
    async def test_init_correlation_manager_handles_missing_watcher_repo(self):
        """B-F1 defensive: when the manager exposes NO ``_watcher_repo``
        (e.g. legacy InstanceManager that hasn't been wired yet), the CM
        must still initialize with ``_watcher_repo=None`` — graceful
        degradation per the B1 contract. We do NOT want init to raise
        when the repo is missing; the daemon must keep running with
        message-only tracking.

        Phase 5: skipped via module-level pytestmark.
        """
        # CM-era imports removed in Phase 5; test body below is dead code.

        # Manager without _watcher_repo (simulates legacy / not-yet-wired).
        manager = MagicMock(name="InstanceManager")
        # Ensure getattr returns None — no _watcher_repo attribute.
        del manager._watcher_repo
        manager._instance_repository = make_instance_repo()
        manager._queue_repository = make_msg_repo()
        manager._event_bus = MagicMock(name="EventBus")

        app = MagicMock(name="FastAPI app")
        app.state = MagicMock(name="app.state")

        original_start = CorrelationManager.start

        async def _noop_start(self) -> None:
            return None

        CorrelationManager.start = _noop_start  # type: ignore[assignment]
        registered_cm: Any = None
        try:
            await init_correlation_manager(app, manager, completion_callback=None)
            registered_cm = cm_module.get_correlation_manager()
            # Graceful degradation: watcher_repo is None but CM is alive.
            assert registered_cm._watcher_repo is None
            # And register_job_send / resolve_job still work — they're
            # in-memory only and don't need the repo.
            await registered_cm.register_job_send("parent-x", "job-y")
            assert registered_cm.get_pending_count("parent-x") == 1
        finally:
            CorrelationManager.start = original_start  # type: ignore[assignment]
            cm_module.set_correlation_manager(None)

        assert registered_cm is not None

    @pytest.mark.asyncio
    async def test_b4_watcher_prefetch_before_notify_after_resolve(self):
        """B-W3: in ``_finalize_job``'s post-commit outbox, the call order
        MUST be:

          1. ``watcher_repo.get_watchers_for_job(job_id)``   (pre-fetch)
          2. ``notify_watchers(job_id, terminal_status)``    (terminal notify)
          3. ``notify_corr_resolve_job(parent_id, ...)``    (drain CM)

        Why this order matters:
          * If ``get_watchers_for_job`` runs AFTER ``notify_watchers``,
            it returns ``[]`` (notify_watchers deletes watcher rows in
            terminal states) → CM ``pending_jobs`` is never drained for
            watch-based parents → completion callback never fires →
            parent hangs in PROCESSING forever.
          * If ``notify_corr_resolve_job`` runs BEFORE ``notify_watchers``,
            the parent receives the completion notification BEFORE its
            watchers do, breaking the expected ordering contract.

        This test exercises the real ``_finalize_job`` flow with a
        completely-mocked dependency set and asserts the call order
        via a side-effect-tracking list.
        """
        from unittest.mock import AsyncMock, patch

        from daemon.repositories.instance.models import InstanceStatus
        from daemon.services.job_feedback_observer import (
            JobFeedbackObserver,
            _FinalizeJobResult,
        )

        # ─── Build the JobFeedbackObserver with fully-mocked deps ───
        # Every external dependency is a MagicMock; only the methods we
        # care about (post-commit outbox) are given explicit behaviour.
        observer = JobFeedbackObserver(
            event_bus=MagicMock(name="EventBus"),
            job_queue_service=MagicMock(name="JobQueueService"),
            job_repo=MagicMock(name="JobRepository"),
            lock_repo=MagicMock(name="LockRepository"),
            project_repo=MagicMock(name="ProjectRepository"),
            instance_manager=MagicMock(name="InstanceManager"),
        )

        # ``_finalize_job_db_sync`` runs in a worker thread (via
        # ``asyncio.to_thread``). For the test we short-circuit it to
        # return a successful result synchronously by patching it as a
        # plain MagicMock — the to_thread wrapper will just await the
        # return value. The post-commit outbox doesn't care that the
        # sync body didn't actually run.
        success_result = _FinalizeJobResult(
            skip=False,
            terminal_status=InstanceStatus.COMPLETED.value,
            job_id="job-integration",
            instance_id="inst-integration",
            parent_id="parent-integration",
            agent_id="leader",
            result_summary="done",
            instance_was_terminal=False,
        )
        observer._finalize_job_db_sync = MagicMock(return_value=success_result)

        # ─── Wire the post-commit outbox methods with ordered tracking ───
        # We use a single ordered list to record the call sequence as
        # the post-commit outbox progresses through get_watchers →
        # notify_watchers → notify_corr_resolve_job.
        call_order: list[str] = []

        # Fake watcher object with the .instance_id attribute the outbox
        # reads to drive notify_corr_resolve_job.
        fake_watcher = MagicMock(name="JobWatcher")
        fake_watcher.instance_id = "parent-integration"

        # Set up watcher_repo.get_watchers_for_job: must return the
        # watcher list (NOT None — the outbox iterates it). The outbox
        # reads this BEFORE calling notify_watchers.
        observer._job_queue_service._watcher_repo = MagicMock(name="watcher_repo")
        observer._job_queue_service._watcher_repo.get_watchers_for_job = MagicMock(
            return_value=[fake_watcher],
            side_effect=lambda *_a, **_kw: (
                call_order.append("get_watchers_for_job"),
                [fake_watcher],  # Must return the list — the outbox iterates it.
            )[-1],
        )

        # notify_watchers: this is the method that deletes watcher rows
        # in terminal states. The outbox calls it AFTER get_watchers_for_job.
        observer._job_queue_service.notify_watchers = AsyncMock(
            side_effect=lambda *_a, **_kw: call_order.append("notify_watchers"),
        )

        # Patch the module-level notify_corr_resolve_job that
        # _finalize_job imports. It must be called AFTER notify_watchers.
        async def _track_resolve(*_a, **_kw):
            call_order.append("notify_corr_resolve_job")

        # Stub out the dispatcher and next-job trigger so we don't need
        # the full instance-side cascade (SSE / CompletionRegistry /
        # lifecycle event) for this ordering test.
        observer._dispatch_instance_post_commit_side_effects = AsyncMock(
            return_value=None
        )
        observer._trigger_next_job = AsyncMock(return_value=None)

        # Build a minimal ``job`` object (the outbox reads job.job_id).
        job = MagicMock(name="JobItem")
        job.job_id = "job-integration"

        with patch(
            "daemon.services.job_feedback_observer.notify_corr_resolve_job",
            side_effect=_track_resolve,
        ):
            await observer._finalize_job(
                job=job,
                instance_id="inst-integration",
                terminal_status=InstanceStatus.COMPLETED.value,
                error=None,
            )

        # ─── Assert the ordering ───
        # Find the indices of the three ordering markers.
        idx_prefetch = call_order.index("get_watchers_for_job")
        idx_notify = call_order.index("notify_watchers")
        idx_resolve = call_order.index("notify_corr_resolve_job")

        assert idx_prefetch < idx_notify, (
            f"B-W3 ORDER BUG: get_watchers_for_job (idx={idx_prefetch}) "
            f"must be called BEFORE notify_watchers (idx={idx_notify}). "
            f"If reversed, notify_watchers deletes watcher rows and the "
            f"prefetch returns [] -> CM pending_jobs is never drained. "
            f"Full call order: {call_order}"
        )
        assert idx_notify < idx_resolve, (
            f"B-W3 ORDER BUG: notify_watchers (idx={idx_notify}) must be "
            f"called BEFORE notify_corr_resolve_job (idx={idx_resolve}). "
            f"Full call order: {call_order}"
        )


# Note: ``test_rebuild_reconstructs_pending_jobs_from_watcher_repo`` was
# removed in Phase 5 — it tested ``CorrelationManager.rebuild_from_db``
# which no longer exists (CorrelationManager was replaced by
# DependencyBus + ``JobQueueService.reconcile_terminal_watches``).
# Restart-reconciliation coverage now lives in
# ``tests/unit/services/test_work_resolver.py::TestReconcileTerminalWatchesResolver``.
# The mocks for the removed ``watcher_repo.get_watched_processing_job_ids``
# method are gone with it — that method was deleted in P2 Batch 1.

