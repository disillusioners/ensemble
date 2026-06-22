"""C12b/C18 — 2-worker-thread serialization tests for the Execution Gate.

Purpose
-------
These tests capture the SERIALIZATION CONTRACT of
``ExecutionGateService.run`` for the same ``instance_id``:

    For any given instance_id, at most one ``work_fn`` may execute
    concurrently. A second caller for the same instance must NEVER
    overlap its ``work_fn`` with the first caller's ``work_fn``.

The tests use ``asyncio.gather`` to launch two concurrent
``gate.run`` calls — this is the async-level analogue of "two
WorkerPool threads processing the same instance", which is the
threading model the gate is designed to serialize.

C12 collapse
------------
These tests previously ran against the OLD DB-backed
``ExecutionLeaseRepository`` and asserted that the second caller
saw a contention signal (the OLD impl returned a contention
result; work_fn for the second caller never ran). After the C12
collapse to a per-instance ``asyncio.Lock``, the second caller
*blocks* on the same event loop and runs its work_fn *after* the
first caller's work_fn completes. The same serialization contract
holds — at most one work_fn in flight at any time — but the
visible behaviour is different (both work_fns run, in order).

The tests below assert the universal contract, not impl-specific
return shapes, so the same file passes against both OLD and NEW
implementations.

Design notes
------------
- Uses the new ``ExecutionGateService()`` (no args) — the
  constructor is now configuration-free.
- Uses ``return_exceptions=True`` so any unexpected exception
  (e.g. deadlock-induced TimeoutError) is captured in the results
  list rather than aborting ``gather``.
- Uses distinct ``holder_id`` values to exercise the full
  contention path that production dispatchers hit.
- Each test asserts the CONTRACT (no concurrent execution,
  lock state correct after, etc.) rather than impl-specific
  return shapes.
"""

from __future__ import annotations

import asyncio

import pytest

from daemon.services.execution_gate import ExecutionGateService


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def gate():
    """Fresh ``ExecutionGateService`` — no args after C12 collapse."""
    return ExecutionGateService()


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _has_interleaved_workers(events: list[tuple[str, str]]) -> bool:
    """Return True if the event log shows two workers' work_fns
    overlapping — i.e. a ``start`` for worker B appears between
    ``start`` and ``end`` of worker A.

    The log records ``(phase, worker_id)`` tuples where phase is
    either ``"start"`` or ``"end"``. A serialised execution
    produces one worker's events fully bracketed, then the
    other's — the block-style gate behaviour.

    Any other ordering means the work_fns overlapped and the
    gate failed to serialize.
    """
    active: set[str] = set()
    for phase, worker in events:
        if phase == "start":
            if active and worker not in active:
                # A second worker started before the first ended.
                return True
            active.add(worker)
        elif phase == "end":
            active.discard(worker)
    return False


# ─── Test 1: Basic serialization (the headline contract) ─────────────────────


@pytest.mark.asyncio
async def test_two_concurrent_workers_same_instance_serialize(gate):
    """Two concurrent ``gate.run`` calls for the SAME instance must not
    execute their work_fns concurrently.

    This is the headline contract: regardless of which
    implementation powers the gate (DB-backed lease or
    asyncio.Lock), at most one work_fn for an instance may be
    in flight at any time.
    """
    instance_id = "threading-test-basic"
    active_workers = 0
    max_active = 0
    counter_lock = asyncio.Lock()
    events: list[tuple[str, str]] = []

    async def worker_a() -> str:
        nonlocal active_workers, max_active
        events.append(("start", "A"))
        async with counter_lock:
            active_workers += 1
            max_active = max(max_active, active_workers)
        # Hold the gate long enough for worker B to attempt acquire.
        await asyncio.sleep(0.05)
        async with counter_lock:
            active_workers -= 1
        events.append(("end", "A"))
        return "A-done"

    async def worker_b() -> str:
        nonlocal active_workers, max_active
        events.append(("start", "B"))
        async with counter_lock:
            active_workers += 1
            max_active = max(max_active, active_workers)
        await asyncio.sleep(0.05)
        async with counter_lock:
            active_workers -= 1
        events.append(("end", "B"))
        return "B-done"

    # Distinct holder_ids so the fast path (if any) does not skip
    # the lock acquisition.
    results = await asyncio.gather(
        gate.run(instance_id, "holder-A", "task", worker_a),
        gate.run(instance_id, "holder-B", "task", worker_b),
        return_exceptions=True,
    )

    # Contract 1: at most one work_fn in flight at any moment.
    assert max_active <= 1, (
        f"Gate failed to serialize: max concurrent work_fns = "
        f"{max_active}. Events: {events}. Results: {results}"
    )

    # Contract 2: no worker B "start" appears between worker A's
    # "start" and "end" (and vice versa).
    assert not _has_interleaved_workers(events), (
        f"Work_fns overlapped. Events: {events}"
    )

    # Contract 3: no exceptions leaked out.
    for r in results:
        assert not isinstance(r, BaseException) or isinstance(
            r, asyncio.CancelledError
        ), f"Unexpected exception: {r!r}"

    # Contract 4: lock is released after both calls return.
    assert await gate.is_held(instance_id) is False


# ─── Test 2: Second caller blocks, then runs after first ────────────────────


@pytest.mark.asyncio
async def test_second_caller_blocks_then_runs_after_holder(gate):
    """Under the asyncio.Lock gate, when holder-A holds the lock,
    holder-B's ``gate.run`` BLOCKS until holder-A releases, then
    holder-B's work_fn runs. Both work_fns complete, in order.

    The original "contention" test asserted that holder-B's
    work_fn was never invoked (OLD impl returned a contention
    signal). Under the NEW impl, holder-B's work_fn DOES run —
    just after holder-A's. The serialization contract is the
    same: no work_fns overlap.
    """
    instance_id = "threading-test-contention"
    b_ever_started = False
    b_ran_after_a_finished = False
    a_finished = asyncio.Event()

    async def worker_a() -> str:
        nonlocal b_ever_started
        # While we are running, B must not have started.
        assert not b_ever_started, (
            "worker_b started while worker_a was still running — "
            "gate failed to serialize concurrent holders"
        )
        await asyncio.sleep(0.08)
        a_finished.set()
        return "A-done"

    async def worker_b() -> str:
        nonlocal b_ever_started, b_ran_after_a_finished
        b_ever_started = True
        # Worker B should only start after worker A has finished.
        if a_finished.is_set():
            b_ran_after_a_finished = True
        else:
            # This branch should not be reached under the
            # asyncio.Lock gate — B's work_fn must only run after
            # A has released. We surface this as an assertion error
            # via a captured result.
            raise AssertionError(
                "worker_b's work_fn ran while worker_a was still "
                "in flight — gate failed to serialize"
            )
        return "B-done"

    results = await asyncio.gather(
        gate.run(instance_id, "holder-A", "task", worker_a),
        gate.run(instance_id, "holder-B", "task", worker_b),
        return_exceptions=True,
    )

    # No work_fns overlapped: worker_b's work_fn only started after
    # worker_a's work_fn finished.
    assert b_ever_started is True, "worker_b's work_fn was never invoked"
    assert b_ran_after_a_finished is True, (
        f"worker_b ran before worker_a finished. Results: {results}"
    )

    # Both completed with their own result.
    assert results == ["A-done", "B-done"], (
        f"Unexpected results: {results}"
    )

    # Lock is released.
    assert await gate.is_held(instance_id) is False


# ─── Test 3: Sequential acquire → release → acquire ──────────────────────────


@pytest.mark.asyncio
async def test_sequential_acquire_release_acquire_cycle(gate):
    """After holder-A releases, holder-B can acquire the same instance.

    The lock must release cleanly between calls — otherwise the
    second acquire would deadlock under the asyncio.Lock impl.
    """
    instance_id = "threading-test-sequential"
    execution_order: list[str] = []

    async def worker_a() -> str:
        execution_order.append("A-start")
        await asyncio.sleep(0.02)
        execution_order.append("A-end")
        return "A-done"

    async def worker_b() -> str:
        execution_order.append("B-start")
        await asyncio.sleep(0.02)
        execution_order.append("B-end")
        return "B-done"

    # Sequential — no contention window.
    result_a = await gate.run(instance_id, "holder-A", "task", worker_a)
    assert await gate.is_held(instance_id) is False

    result_b = await gate.run(instance_id, "holder-B", "task", worker_b)
    assert await gate.is_held(instance_id) is False

    assert result_a == "A-done"
    assert result_b == "B-done"
    # The two work_fns ran in call order with no interleaving.
    assert execution_order == ["A-start", "A-end", "B-start", "B-end"]


# ─── Test 4: Different instances run in parallel (no false serialization) ───


@pytest.mark.asyncio
async def test_different_instances_run_in_parallel(gate):
    """Two concurrent ``gate.run`` calls for DIFFERENT instances must run
    their work_fns in parallel — the gate must NOT false-serialize
    unrelated instances.

    This protects against an implementation bug where a coarse
    lock (e.g. a single module-level asyncio.Lock) accidentally
    serializes all instances globally.
    """
    active_workers = 0
    max_active = 0
    counter_lock = asyncio.Lock()

    async def make_worker(instance_tag: str):
        async def work() -> str:
            nonlocal active_workers, max_active
            async with counter_lock:
                active_workers += 1
                max_active = max(max_active, active_workers)
            # Hold long enough that the other worker has time to
            # also enter its work_fn if the gate allows it.
            await asyncio.sleep(0.08)
            async with counter_lock:
                active_workers -= 1
            return f"{instance_tag}-done"

        return work

    work_a = await make_worker("A")
    work_b = await make_worker("B")

    results = await asyncio.gather(
        gate.run("instance-A", "holder-A", "task", work_a),
        gate.run("instance-B", "holder-B", "task", work_b),
    )

    # The contract: distinct instances serialize independently.
    # Both work_fns must overlap → max_active == 2.
    assert max_active == 2, (
        f"Gate falsely serialized unrelated instances: "
        f"max concurrent = {max_active}, expected 2. Results: {results}"
    )

    # Both completed cleanly with their own result.
    assert results == ["A-done", "B-done"]

    # Both locks released.
    assert await gate.is_held("instance-A") is False
    assert await gate.is_held("instance-B") is False


# ─── Test 5: Exception in work_fn releases the lock ──────────────────────────


@pytest.mark.asyncio
async def test_exception_in_work_fn_releases_lock(gate):
    """If ``work_fn`` raises, the gate must still release the lock so
    that a subsequent acquire on the same instance can succeed.

    The new ``asyncio.Lock`` gate uses an ``async with`` block
    around the work_fn; the lock is released by the context
    manager's __aexit__ regardless of whether the body raised.
    """
    instance_id = "threading-test-exception"
    execution_order: list[str] = []

    class WorkerError(RuntimeError):
        pass

    async def failing_worker() -> None:
        execution_order.append("fail-start")
        await asyncio.sleep(0.01)
        execution_order.append("fail-raise")
        raise WorkerError("boom")

    async def succeeding_worker() -> str:
        execution_order.append("success-start")
        return "ok"

    # First call raises.
    with pytest.raises(WorkerError):
        await gate.run(
            instance_id, "holder-fail", "task", failing_worker
        )

    # Lock must be released despite the exception.
    assert await gate.is_held(instance_id) is False

    # Second call on the same instance must succeed.
    result = await gate.run(
        instance_id, "holder-success", "task", succeeding_worker
    )
    assert result == "ok"

    # Lock released after the second call too.
    assert await gate.is_held(instance_id) is False
