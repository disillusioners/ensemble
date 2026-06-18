"""Unit tests for terminate_instance in InstanceLifecycleService.

Covers the terminate-pause-latency PR changes:
- §4.1 Fix A:  bounded-await graph task unwind + parallel cascade
- §4.2 Fix B:  cascade trigger=DELETE tag + summary [TRACE] log
- §4.3 Fix C:  notify_all() wakeup on dispatch bus
"""

import asyncio
import logging
import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from daemon.repositories.instance.models import InstanceStatus


# =============================================================================
# Mock helpers
# =============================================================================


def make_meta(instance_id: str, status: str = "running", children: list = None) -> MagicMock:
    """Build a minimal mock meta that the code accesses."""
    meta = MagicMock()
    meta.instance_id = instance_id
    meta.status = status
    meta.agent_id = "test-agent"
    meta.parent_id = None
    meta.children = children or []
    return meta


def make_manager(
    *,
    meta_for: dict[str, MagicMock] | None = None,
    graph_tasks: dict[str, asyncio.Task] | None = None,
    with_dispatch_bus: bool = True,
) -> MagicMock:
    """Construct a mock manager wired to the lifecycle service."""
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._graph_tasks = graph_tasks or {}
    manager._request_registry = MagicMock()
    manager._live_hub = MagicMock()
    manager._live_hub.cleanup_instance = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._watcher_repo = MagicMock()
    manager._watcher_repo.remove_all_watches_for_instance = MagicMock(return_value=0)
    manager._mcp_service = None
    manager.instances = {}
    manager._queue_repository = MagicMock()
    manager._queue_repository.delete_by_instance = MagicMock(return_value=0)

    # Wire _instance_repository.get() to return the right meta per ID
    if meta_for:
        manager._instance_repository.get.side_effect = lambda iid: meta_for.get(iid)

    # Optional dispatch bus for notify_all tests
    if with_dispatch_bus:
        manager._job_queue_mgmt_service = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus = MagicMock()
        manager._job_queue_mgmt_service._dispatch_bus.notify_all = MagicMock()

    return manager


def make_job_queue_service(
    with_jobs: bool = False,
    message_jobs: list = None,
    all_jobs: list = None,
) -> MagicMock:
    """Build a mock JobQueueService for terminate_instance."""
    svc = MagicMock()
    svc._repository = MagicMock()
    svc._repository.find_jobs_by_instance = MagicMock(return_value=message_jobs or [])
    svc.cancel_job = AsyncMock(return_value=True)
    svc.cancel_message_job = AsyncMock(return_value=True)
    svc.complete_job = AsyncMock(return_value=None)
    svc.complete_job_sync = MagicMock(return_value=None)
    svc.release_lock_by_instance = AsyncMock(return_value=[])
    svc.trigger_next_job_sync = MagicMock()

    # For step 7 (get_job_by_instance_sync)
    svc.get_job_by_instance_sync = MagicMock(return_value=None)

    return svc


def make_cancellation_service() -> MagicMock:
    return MagicMock()


def make_lifecycle_service(
    manager: MagicMock,
    job_queue_service: MagicMock = None,
) -> "InstanceLifecycleService":
    """Instantiate InstanceLifecycleService with the given manager."""
    from daemon.services.instance_lifecycle import InstanceLifecycleService
    return InstanceLifecycleService(
        manager=manager,
        cancellation_service=make_cancellation_service(),
        job_queue_service=job_queue_service,
    )


# =============================================================================
# Test 1 — Re-entrancy guard: already-terminated → returns immediately, no side-effects
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_returns_early_if_already_terminated(caplog: pytest.LogCaptureFixture):
    """
    When the instance is already TERMINATED, terminate_instance must:
    - Return True immediately (duration < 50 ms)
    - NOT call cancel_by_instance
    - NOT attempt graph-task cancellation
    - NOT emit a cascade log
    - NOT call notify_all
    - Log the 'already terminated' line
    """
    caplog.set_level(logging.INFO)

    instance_id = "already-gone-123"
    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id, status=InstanceStatus.TERMINATED.value)},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    t0 = time.monotonic()
    result = await svc.terminate_instance(instance_id)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    assert result is True, "Should return True for already-terminated instance"
    assert elapsed_ms < 100, f"Should return immediately, took {elapsed_ms} ms"

    # No request cancellation
    manager._request_registry.cancel_by_instance.assert_not_called()

    # No graph-task interaction
    assert manager._graph_tasks.get(instance_id) is None

    # No cascade log (no children anyway, but this also proves no cascade happened)
    cascade_logs = [r for r in caplog.records if "Cascading" in r.message]
    assert len(cascade_logs) == 0

    # The "already terminated" info line is present
    already_logged = any(
        "already terminated" in r.message and instance_id[:8] in r.message
        for r in caplog.records
    )
    assert already_logged, "Expected 'already terminated' info log not found"

    # No notify_all (early return before step 9)
    manager._job_queue_mgmt_service._dispatch_bus.notify_all.assert_not_called()


# =============================================================================
# Test 2 — Fast graph task (0.5 s): bounded await completes within 5 s
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_bounded_await_graph_task_unwinds_within_timeout(
    caplog: pytest.LogCaptureFixture,
):
    """
    When the graph task needs ~0.5 s to unwind after cancel, terminate_instance must:
    - Complete in ~1 s total (well under the 5 s cap)
    - Await the task to completion
    - Emit the 'Cancelled graph task' log with unwind_ms >= 400
    - Emit the [TRACE] summary log with graph_unwind_ms matching
    - Call notify_all exactly once
    """
    caplog.set_level(logging.DEBUG)

    instance_id = "fast-unwind-123"
    # Simulate a real LLM call: a long sleep that, on cancel, runs cleanup work
    # for ~0.5 s before honoring the cancellation. The cleanup work is what the
    # bounded-await is supposed to wait for.
    async def fast_work_with_cleanup():
        try:
            await asyncio.sleep(60)  # Long sleep that will be cancelled
        except asyncio.CancelledError:
            await asyncio.sleep(0.5)  # Cleanup work
            raise  # Re-raise to honor cancellation

    loop = asyncio.get_running_loop()
    graph_task = loop.create_task(fast_work_with_cleanup())
    await asyncio.sleep(0.05)  # Let the task actually start its sleep

    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id)},
        graph_tasks={instance_id: graph_task},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    t0 = time.monotonic()
    result = await svc.terminate_instance(instance_id)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    assert result is True
    assert elapsed_ms < 2000, f"Should complete quickly; took {elapsed_ms} ms"
    assert graph_task.done(), "Graph task should be done"

    # 'Cancelled graph task' log emitted
    cancel_logs = [r for r in caplog.records if "Cancelled graph task" in r.message]
    assert len(cancel_logs) >= 1, "Expected 'Cancelled graph task' log"

    # unwind_ms field present and >= 400
    unwind_msgs = [r.message for r in caplog.records if "unwind_ms=" in r.message]
    assert len(unwind_msgs) >= 1, "Expected log with unwind_ms field"
    # Extract the numeric value
    import re
    m = re.search(r"unwind_ms=(\d+)", unwind_msgs[0])
    assert m is not None, f"Could not find unwind_ms= in: {unwind_msgs[0]}"
    unwind_val = int(m.group(1))
    assert unwind_val >= 400, f"Expected unwind_ms >= 400, got {unwind_val}"

    # [TRACE] summary log
    trace_logs = [
        r for r in caplog.records
        if "[TRACE] terminate_instance" in r.message and "complete" in r.message
    ]
    assert len(trace_logs) >= 1, "Expected [TRACE] summary log"
    trace_msg = trace_logs[0].message
    assert "graph_unwind_ms=" in trace_msg

    # notify_all called once
    manager._job_queue_mgmt_service._dispatch_bus.notify_all.assert_called_once()


# =============================================================================
# Test 3 — Slow graph task (30 s): bounded await times out at ~5 s
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_bounded_await_graph_task_times_out_at_5s(
    caplog: pytest.LogCaptureFixture,
):
    """
    When the graph task sleeps for 30 s (never returns), terminate_instance must:
    - Return in ~5.5-6 s (after the 5 s timeout fires)
    - Catch asyncio.TimeoutError and emit a warning log
    - graph_unwind_ms in [4500, 6000]
    - NOT re-raise — the function continues and returns True
    - The task is left running in the background (not awaited to done)
    """
    caplog.set_level(logging.DEBUG)

    instance_id = "slow-unwind-123"

    # Simulate a stuck LLM call: even on cancel, the cleanup work takes
    # a long time. The bounded-await should give up at 5 s.
    async def slow_work_with_long_cleanup():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(60)  # Stuck cleanup
            raise

    loop = asyncio.get_running_loop()
    graph_task = loop.create_task(slow_work_with_long_cleanup())
    await asyncio.sleep(0.05)  # Let the task actually start its sleep

    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id)},
        graph_tasks={instance_id: graph_task},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    t0 = time.monotonic()
    result = await svc.terminate_instance(instance_id)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    assert result is True, "Should return True even after timeout"
    assert 4500 <= elapsed_ms <= 7000, (
        f"Expected ~5-6 s elapsed; got {elapsed_ms} ms"
    )

    # Warning log about timeout
    timeout_warnings = [
        r for r in caplog.records
        if "did not unwind within 5s" in r.message or "TimeoutError" in r.message
    ]
    assert len(timeout_warnings) >= 1, "Expected timeout warning log"

    # [TRACE] summary log present
    trace_logs = [
        r for r in caplog.records
        if "[TRACE] terminate_instance" in r.message and "complete" in r.message
    ]
    assert len(trace_logs) >= 1, "Expected [TRACE] summary log"
    trace_msg = trace_logs[0].message
    import re
    m = re.search(r"graph_unwind_ms=(\d+)", trace_msg)
    assert m is not None, f"Could not find graph_unwind_ms= in: {trace_msg}"
    unwind_val = int(m.group(1))
    assert 4500 <= unwind_val <= 6500, (
        f"Expected graph_unwind_ms in [4500, 6500], got {unwind_val}"
    )

    # notify_all called once
    manager._job_queue_mgmt_service._dispatch_bus.notify_all.assert_called_once()

    # Clean up: cancel the background task
    graph_task.cancel()
    try:
        await graph_task
    except asyncio.CancelledError:
        pass


# =============================================================================
# Test 4 — Parallel cascade: 3 children × 2 s each → ~2 s total, not ~6 s
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_parallel_cascade_with_3_children_each_2s():
    """
    Parent with 3 children where each child's terminate sleeps 2 s.
    Because cascade is parallel, total time should be ~2 s (max of children),
    NOT ~6 s (serial sum).
    """
    instance_id = "parent-123"
    child_ids = ["child-a-123", "child-b-456", "child-c-789"]

    manager = make_manager(
        meta_for={
            instance_id: make_meta(instance_id, children=child_ids),
            **{cid: make_meta(cid, children=[]) for cid in child_ids},
        },
        graph_tasks={},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    # We need the parent's REAL terminate_instance to run (so the cascade
    # code executes), but child calls must go to our mock. Routing wrapper
    # by ID.
    real_terminate = svc.terminate_instance

    async def slow_child(child_id: str) -> bool:
        await asyncio.sleep(2.0)
        return True

    async def routing_terminate(call_id: str) -> bool:
        if call_id == instance_id:
            # Parent: invoke real method
            return await real_terminate(call_id)
        # Child: simulate a slow terminate
        return await slow_child(call_id)

    svc.terminate_instance = routing_terminate  # type: ignore[method-assign]

    t0 = time.monotonic()
    result = await svc.terminate_instance(instance_id)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    assert result is True
    # Should complete in ~2 s (parallel), not ~6 s (serial)
    assert elapsed_ms < 4000, (
        f"Expected parallel ~2 s; took {elapsed_ms} ms — cascade may be serial!"
    )


# =============================================================================
# Test 5 — Cascade: failed child → warning log, parent still returns True
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_cascade_logs_failed_children_as_warnings(
    caplog: pytest.LogCaptureFixture,
):
    """
    When a child terminate raises RuntimeError:
    - The parent must catch it (return_exceptions=True) and NOT propagate
    - A warning log must be emitted with the exception type and message
    - The parent still returns True
    """
    caplog.set_level(logging.DEBUG)

    instance_id = "parent-fail-123"
    child_ids = ["child-ok-123", "child-fail-456"]

    manager = make_manager(
        meta_for={
            instance_id: make_meta(instance_id, children=child_ids),
            **{cid: make_meta(cid, children=[]) for cid in child_ids},
        },
        graph_tasks={},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    real_terminate = svc.terminate_instance

    async def unreliable_terminate(child_id: str) -> bool:
        if child_id == "child-fail-456":
            raise RuntimeError("db locked")
        await asyncio.sleep(0.05)
        return True

    async def routing_terminate(call_id: str) -> bool:
        if call_id == instance_id:
            return await real_terminate(call_id)
        return await unreliable_terminate(call_id)

    svc.terminate_instance = routing_terminate  # type: ignore[method-assign]

    result = await svc.terminate_instance(instance_id)

    assert result is True, "Parent should return True even if a child fails"

    # Warning log for the failed child (the production code truncates cid[:8],
    # so search for the prefix and the exception type/message instead)
    fail_warnings = [
        r for r in caplog.records
        if "child-fa" in r.message and "RuntimeError" in r.message and "db locked" in r.message
    ]
    assert len(fail_warnings) >= 1, (
        f"Expected warning about failed child; logs: {[r.message for r in caplog.records]}"
    )


# =============================================================================
# Test 6 — notify_all: called exactly once at the correct attribute path
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_calls_notify_all_on_dispatch_bus():
    """
    terminate_instance must call notify_all() on
    manager._job_queue_mgmt_service._dispatch_bus
    exactly once.
    """
    instance_id = "notify-test-123"

    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id)},
        graph_tasks={},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    await svc.terminate_instance(instance_id)

    manager._job_queue_mgmt_service._dispatch_bus.notify_all.assert_called_once()


# =============================================================================
# Test 7 — notify_all defensive getattr: missing _dispatch_bus is a no-op
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_notify_all_is_noop_when_dispatch_bus_missing(
    caplog: pytest.LogCaptureFixture,
):
    """
    When _job_queue_mgmt_service or _dispatch_bus is absent (None/missing),
    terminate_instance must complete without error and the rest of the
    function must still run.
    """
    caplog.set_level(logging.INFO)

    instance_id = "no-bus-123"

    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id)},
        graph_tasks={},
        # with_dispatch_bus=False → _job_queue_mgmt_service is NOT set
        with_dispatch_bus=False,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    # Must not raise
    t0 = time.monotonic()
    result = await svc.terminate_instance(instance_id)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    assert result is True, "Should return True even without dispatch bus"
    assert elapsed_ms < 2000, f"Took {elapsed_ms} ms — function may have hung"

    # Summary log still emitted
    trace_logs = [
        r for r in caplog.records
        if "[TRACE] terminate_instance" in r.message and "complete" in r.message
    ]
    assert len(trace_logs) >= 1, "Summary log should still be emitted"


# =============================================================================
# Test 8 — Cascade log contains trigger=DELETE
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_cascade_log_contains_trigger_delete(
    caplog: pytest.LogCaptureFixture,
):
    """
    The cascade log line for a successful child terminate must include
    'trigger=DELETE'.
    """
    caplog.set_level(logging.DEBUG)

    instance_id = "trigger-test-123"
    child_ids = ["child-trigger-123"]

    manager = make_manager(
        meta_for={
            instance_id: make_meta(instance_id, children=child_ids),
            **{cid: make_meta(cid, children=[]) for cid in child_ids},
        },
        graph_tasks={},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    # Parent's real code must run so the cascade log is emitted; child
    # calls go to a quick success.
    real_terminate = svc.terminate_instance

    async def quick_child(child_id: str) -> bool:
        return True

    async def routing_terminate(call_id: str) -> bool:
        if call_id == instance_id:
            return await real_terminate(call_id)
        return await quick_child(call_id)

    svc.terminate_instance = routing_terminate  # type: ignore[method-assign]

    await svc.terminate_instance(instance_id)

    cascade_logs = [
        r for r in caplog.records
        if "Cascading" in r.message and "trigger=DELETE" in r.message
    ]
    assert len(cascade_logs) >= 1, (
        f"Expected cascade log with trigger=DELETE; logs: "
        f"{[r.message for r in caplog.records if 'Cascading' in r.message]}"
    )


# =============================================================================
# Test 9 — Summary [TRACE] log contains all four fields
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_summary_log_has_all_fields(
    caplog: pytest.LogCaptureFixture,
):
    """
    The [TRACE] terminate_instance summary log must contain all four fields:
    graph_unwind_ms=, jobs_cancelled=, children=, duration_ms=
    """
    caplog.set_level(logging.DEBUG)

    instance_id = "summary-test-123"

    manager = make_manager(
        meta_for={
            instance_id: make_meta(instance_id, children=["ch-1", "ch-2"]),
            "ch-1": make_meta("ch-1", children=[]),
            "ch-2": make_meta("ch-2", children=[]),
        },
        graph_tasks={},  # No graph task → graph_unwind_ms=0
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    # Parent must run the real code so the [TRACE] summary log is emitted.
    real_terminate = svc.terminate_instance

    async def routing_terminate(call_id: str) -> bool:
        if call_id == instance_id:
            return await real_terminate(call_id)
        # Children: succeed quickly
        return True

    svc.terminate_instance = routing_terminate  # type: ignore[method-assign]

    await svc.terminate_instance(instance_id)

    trace_logs = [
        r for r in caplog.records
        if "[TRACE] terminate_instance" in r.message and "complete" in r.message
    ]
    assert len(trace_logs) >= 1, f"Expected [TRACE] summary log; records: {[r.message for r in caplog.records]}"

    trace_msg = trace_logs[0].message

    required_fields = ["graph_unwind_ms=", "jobs_cancelled=", "children=", "duration_ms="]
    for field in required_fields:
        assert field in trace_msg, f"Summary log missing '{field}'; got: {trace_msg}"

    # Verify children=2 (we have 2 children in meta)
    import re
    m = re.search(r"children=(\d+)", trace_msg)
    assert m is not None and int(m.group(1)) == 2, (
        f"Expected children=2; got: {trace_msg}"
    )


# =============================================================================
# Test 10 — terminate_instance resets waiting_for=0 (Fix 3 part B)
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_resets_waiting_for_to_zero_on_instance_repo():
    """terminate_instance must reset ``waiting_for=0`` on the instance repo.

    Without this, a terminate→revive cycle leaves a non-zero ``waiting_for``
    counter in the DB even though the instance is brand-new. The revived
    instance would inherit a stale counter and ``is_complete()`` checks would
    be wrong until manual cleanup.
    """
    instance_id = "wf-reset-123"

    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id)},
        graph_tasks={},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    await svc.terminate_instance(instance_id)

    # update was called with waiting_for=0 for this instance.
    update_calls = manager._instance_repository.update.call_args_list
    wf_calls = [
        c for c in update_calls if c.kwargs.get("waiting_for") == 0
    ]
    assert len(wf_calls) == 1, (
        f"Expected exactly one update(waiting_for=0) call, got "
        f"{update_calls}"
    )
    assert wf_calls[0].args[0] == instance_id


# =============================================================================
# Test 11 — terminate_instance clears CorrelationManager state (Fix 3 part B)
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_clears_correlation_manager_state_for_instance():
    """terminate_instance must call ``cm.clear_for_instance(instance_id)``.

    Otherwise a terminated-and-revived instance would inherit its previous
    ``_pending[parent_id]`` entry, and ``is_complete()`` would never return
    True until daemon restart (S3 leak from the CM docs).
    """
    from daemon.services.correlation_manager import (
        CorrelationManager,
        set_correlation_manager,
    )

    instance_id = "cm-clear-123"

    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id)},
        graph_tasks={},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    # Wire a real CM and populate its state for this parent.
    cm = CorrelationManager(
        instance_repository=make_instance_repo_mock(),
        message_queue_repository=make_msg_repo_mock(),
    )
    await cm.start()
    set_correlation_manager(cm)
    try:
        # Register a correlation so _pending and _locks are populated.
        await cm.register_message_send(instance_id, "child-001", "msg-001")
        assert cm.get_pending_count(instance_id) == 1
        assert instance_id in cm._pending
        assert instance_id in cm._locks

        # Terminate — must clear CM state for this parent.
        await svc.terminate_instance(instance_id)

        # CM state for this parent is gone.
        assert instance_id not in cm._pending, (
            f"_pending should be cleared; found: {list(cm._pending)}"
        )
        assert instance_id not in cm._locks, (
            f"_locks should be cleared; found: {list(cm._locks)}"
        )
        assert cm.get_pending_count(instance_id) == 0
    finally:
        await cm.stop()
        set_correlation_manager(None)


# =============================================================================
# Test 12 — terminate_instance with no CM registered still completes
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_succeeds_when_correlation_manager_is_none():
    """When CM is None (not wired), terminate_instance must NOT crash.

    The CM cleanup is wrapped in a None-check (``if cm is not None``). The
    daemon must remain safe when CM is absent (graceful degradation path).
    """
    from daemon.services.correlation_manager import set_correlation_manager

    instance_id = "no-cm-123"

    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id)},
        graph_tasks={},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    # Ensure no CM is wired.
    set_correlation_manager(None)
    try:
        # Must not raise.
        result = await svc.terminate_instance(instance_id)
        assert result is True
    finally:
        set_correlation_manager(None)


# =============================================================================
# Test 13 — terminate_instance with CM that raises does not fail termination
# =============================================================================


@pytest.mark.asyncio
async def test_terminate_handles_correlation_manager_failure_gracefully(
    caplog: pytest.LogCaptureFixture,
):
    """When ``cm.clear_for_instance`` raises, terminate_instance must still
    complete (defensive try/except in step 7.8). The CM failure is logged at
    WARNING but does NOT propagate — legacy ``waiting_for`` cascade is the
    graceful-degradation fallback.
    """
    from unittest.mock import AsyncMock, MagicMock

    from daemon.services.correlation_manager import (
        CorrelationManager,
        set_correlation_manager,
    )

    instance_id = "cm-raises-123"

    manager = make_manager(
        meta_for={instance_id: make_meta(instance_id)},
        graph_tasks={},
        with_dispatch_bus=True,
    )
    svc = make_lifecycle_service(manager, make_job_queue_service())

    # Wire a CM mock whose clear_for_instance raises.
    cm = MagicMock(spec=CorrelationManager)
    cm.clear_for_instance = AsyncMock(
        side_effect=RuntimeError("simulated CM failure")
    )
    set_correlation_manager(cm)
    caplog.set_level(logging.WARNING)
    try:
        # Must NOT propagate the CM error.
        result = await svc.terminate_instance(instance_id)

        assert result is True
        # The failing CM was called (we know the code path was reached).
        cm.clear_for_instance.assert_awaited_once_with(instance_id)

        # Failure was logged.
        fail_logs = [
            r for r in caplog.records
            if "Failed to clear CM state" in r.message
            and instance_id[:8] in r.message
        ]
        assert len(fail_logs) >= 1, (
            f"Expected failure log; got: "
            f"{[r.message for r in caplog.records]}"
        )
    finally:
        set_correlation_manager(None)


def make_instance_repo_mock():
    """Lightweight mock for InstanceRepo used by CM tests."""
    from unittest.mock import MagicMock

    repo = MagicMock(name="InstanceRepo")
    repo.get = MagicMock(return_value=None)
    repo.get_all_with_waiting_for = MagicMock(return_value=[])
    return repo


def make_msg_repo_mock():
    """Lightweight mock for MessageQueueRepo used by CM tests."""
    from unittest.mock import MagicMock

    repo = MagicMock(name="MsgRepo")
    repo.get_pending_for_instances = MagicMock(return_value=[])
    return repo
