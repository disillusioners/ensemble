"""Unit tests for the asyncio.Lock-based Execution Gate.

Covers the gate's core contract after the C12 collapse from
DB-backed lease to per-instance ``asyncio.Lock``:

- **Acquire / release happy path** — ``gate.run`` runs the work_fn
  and returns its result; the lock is released on exit.
- **Exception / cancellation release** — the lock is released even
  when the work_fn raises or the awaited task is cancelled.
- **Re-entrant call from the same holder deadlocks** — the new gate
  is NOT re-entrant (asyncio.Lock is not re-entrant). A holder
  re-entering ``gate.run`` from inside its own work_fn would
  deadlock. This is pinned as a regression guard so a future
  "fast path" addition does not silently reintroduce the bug.
- **Concurrent callers serialize** — two ``gate.run`` calls for the
  same instance never overlap their work_fns; the second blocks
  until the first releases.
- **Different instances run in parallel** — the gate does NOT
  false-serialize unrelated instances.
- **CancellationToken cooperation** — cancelling the awaiting task
  interrupts the in-flight work_fn (the gate does not own
  cancellation; the caller's CancellationToken does).

DB-backed lease tests (acquire/release on the SQLModel table,
heartbeat, recovery, in-process fast path) have been removed
because the ``instance_execution_leases`` table is no longer
written at runtime. The migration file
``20260614_000002_create_instance_execution_leases.sql`` is
retained as part of released history but the table is now unused.
"""

from __future__ import annotations

import asyncio

import pytest

from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.execution_gate import ExecutionGateService


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def gate():
    """A fresh ExecutionGateService with no constructor args.

    The new gate accepts (and ignores) the old ``lease_repo=`` kwarg
    for backward compat with ``InstanceManager``'s old call site —
    this fixture exercises the new no-arg path.
    """
    return ExecutionGateService()


@pytest.fixture
def gate_backward_compat():
    """An ExecutionGateService constructed with old-style kwargs.

    Verifies the constructor still accepts (and ignores) the legacy
    ``lease_repo=``, ``stale_lease_seconds=``,
    ``heartbeat_interval_seconds=``,
    ``heartbeat_max_consecutive_errors=`` arguments that the old
    ``InstanceManager`` passed. This pins the backward-compat
    contract.
    """
    return ExecutionGateService(
        lease_repo=None,
        stale_lease_seconds=300,
        heartbeat_interval_seconds=30.0,
        heartbeat_max_consecutive_errors=5,
    )


# ─── Constructor / backward compat ────────────────────────────────────────────


class TestExecutionGateConstructor:
    def test_no_args_constructs_cleanly(self):
        """The new gate has no required constructor args."""
        gate = ExecutionGateService()
        assert gate is not None

    def test_accepts_legacy_kwargs_silently(self, gate_backward_compat):
        """Old call sites pass ``lease_repo=`` etc. — the constructor
        must accept (and ignore) them without raising.
        """
        assert gate_backward_compat is not None


# ─── Happy path ───────────────────────────────────────────────────────────────


class TestExecutionGateHappyPath:
    @pytest.mark.asyncio
    async def test_run_executes_work_fn_and_returns_result(self, gate):
        """``gate.run`` runs the work_fn and returns its result."""
        called = {"count": 0}

        async def work():
            called["count"] += 1
            return "result-ok"

        out = await gate.run("inst-1", "holder-A", "task", work)
        assert out == "result-ok"
        assert called["count"] == 1

    @pytest.mark.asyncio
    async def test_run_releases_lock_on_exception(self, gate):
        """If the work_fn raises, the lock must be released so the
        next call on the same instance can proceed.
        """

        async def work():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await gate.run("inst-1", "holder-A", "task", work)

        # Lock is released → is_held returns False.
        assert await gate.is_held("inst-1") is False

    @pytest.mark.asyncio
    async def test_run_releases_lock_on_cancellation(self, gate):
        """If the awaiting task is cancelled, the lock must still
        be released so the next call can proceed.
        """

        async def work():
            await asyncio.sleep(10)

        task = asyncio.create_task(
            gate.run("inst-1", "holder-A", "task", work)
        )
        # Let the work_fn start and acquire the lock.
        await asyncio.sleep(0.05)
        assert await gate.is_held("inst-1") is True

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Lock is released by the ``async with`` unwind.
        assert await gate.is_held("inst-1") is False

    @pytest.mark.asyncio
    async def test_sequential_acquire_release_acquire_cycle(self, gate):
        """After holder-A releases, holder-B can acquire the same
        instance. The lock must release cleanly between calls.
        """

        async def work_a():
            return "A-done"

        async def work_b():
            return "B-done"

        result_a = await gate.run("inst-1", "holder-A", "task", work_a)
        assert await gate.is_held("inst-1") is False
        result_b = await gate.run("inst-1", "holder-B", "task", work_b)
        assert await gate.is_held("inst-1") is False
        assert result_a == "A-done"
        assert result_b == "B-done"


# ─── Concurrency / serialization ──────────────────────────────────────────────


class TestExecutionGateSerialization:
    @pytest.mark.asyncio
    async def test_concurrent_callers_serialize_same_instance(self, gate):
        """Two concurrent ``gate.run`` calls for the SAME instance
        must not execute their work_fns concurrently. The second
        caller's work_fn must wait for the first to release.
        """
        instance_id = "inst-serialize-1"
        active = 0
        max_active = 0
        counter_lock = asyncio.Lock()
        events: list[tuple[str, str]] = []

        async def worker_a():
            nonlocal active, max_active
            events.append(("start", "A"))
            async with counter_lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            async with counter_lock:
                active -= 1
            events.append(("end", "A"))
            return "A-done"

        async def worker_b():
            nonlocal active, max_active
            events.append(("start", "B"))
            async with counter_lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            async with counter_lock:
                active -= 1
            events.append(("end", "B"))
            return "B-done"

        results = await asyncio.gather(
            gate.run(instance_id, "holder-A", "task", worker_a),
            gate.run(instance_id, "holder-B", "task", worker_b),
        )

        # Headline contract: at most one work_fn in flight at a time.
        assert max_active == 1, (
            f"Gate failed to serialize: max concurrent work_fns = "
            f"{max_active}. Events: {events}. Results: {results}"
        )
        # Both work_fns ran (the second blocked, not contended).
        assert results == ["A-done", "B-done"]
        # No work_fn events interleave.
        for i, (phase, worker) in enumerate(events):
            if phase == "start":
                # The next event must be the SAME worker's "end"
                # before any other worker can "start".
                assert events[i + 1] == ("end", worker), (
                    f"work_fn overlap detected: {events}"
                )
        # Lock is released.
        assert await gate.is_held(instance_id) is False

    @pytest.mark.asyncio
    async def test_different_instances_run_in_parallel(self, gate):
        """Two concurrent ``gate.run`` calls for DIFFERENT instances
        must run their work_fns in parallel — the gate must NOT
        false-serialize unrelated instances.
        """
        active = 0
        max_active = 0
        counter_lock = asyncio.Lock()

        async def make_worker(instance_tag: str):
            async def work():
                nonlocal active, max_active
                async with counter_lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.08)
                async with counter_lock:
                    active -= 1
                return f"{instance_tag}-done"

            return work

        work_a = await make_worker("A")
        work_b = await make_worker("B")

        results = await asyncio.gather(
            gate.run("instance-A", "holder-A", "task", work_a),
            gate.run("instance-B", "holder-B", "task", work_b),
        )

        # Distinct instances serialize independently — both work_fns
        # overlap → max_active == 2.
        assert max_active == 2, (
            f"Gate falsely serialized unrelated instances: "
            f"max concurrent = {max_active}, expected 2. Results: {results}"
        )
        assert results == ["A-done", "B-done"]
        assert await gate.is_held("instance-A") is False
        assert await gate.is_held("instance-B") is False


# ─── Re-entrance ──────────────────────────────────────────────────────────────


class TestExecutionGateReentrance:
    @pytest.mark.asyncio
    async def test_reentrant_call_from_same_holder_deadlocks(self, gate):
        """The asyncio.Lock gate is NOT re-entrant. A holder that
        calls ``gate.run`` from inside its own work_fn (same
        instance) deadlocks. This is a known limitation — production
        dispatchers do not re-enter, so the deadlock cannot be
        triggered in practice. This test pins the limitation so a
        future "fast path" addition does not silently introduce a
        regression where the gate silently re-enters.

        We assert the deadlock by racing the call against a timeout.
        """

        async def outer():
            # Re-entrant call: same instance, same holder.
            return await gate.run(
                "inst-1", "holder-A", "task", _never_called
            )

        async def _never_called():
            raise AssertionError("work_fn must not run on re-entrant call")

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                gate.run("inst-1", "holder-A", "task", outer),
                timeout=0.5,
            )
        # Lock is held by the timed-out outer call.
        # We don't assert on is_held here because the timeout left
        # the task running — the lock may or may not be released
        # depending on the interpreter's cleanup. The point is the
        # gate deadlocked, not the cleanup state.


# ─── Diagnostic helpers ───────────────────────────────────────────────────────


class TestExecutionGateDiagnosticHelpers:
    @pytest.mark.asyncio
    async def test_is_held_false_initially(self, gate):
        assert await gate.is_held("inst-1") is False

    @pytest.mark.asyncio
    async def test_is_held_true_during_run(self, gate):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def work():
            entered.set()
            await release.wait()

        task = asyncio.create_task(
            gate.run("inst-1", "holder-A", "task", work)
        )
        await entered.wait()
        assert await gate.is_held("inst-1") is True
        release.set()
        await task
        assert await gate.is_held("inst-1") is False

    @pytest.mark.asyncio
    async def test_is_held_distinct_per_instance(self, gate):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def work():
            entered.set()
            await release.wait()

        task = asyncio.create_task(
            gate.run("inst-1", "holder-A", "task", work)
        )
        await entered.wait()
        assert await gate.is_held("inst-1") is True
        assert await gate.is_held("inst-2") is False
        release.set()
        await task

    @pytest.mark.asyncio
    async def test_is_held_by_ignores_holder_identity(self, gate):
        """The asyncio.Lock gate does not track holder identity —
        ``is_held_by`` accepts a ``holder_id`` and returns True iff
        the lock is held, regardless of which caller holds it.
        Backward-compat contract.
        """
        entered = asyncio.Event()
        release = asyncio.Event()

        async def work():
            entered.set()
            await release.wait()

        task = asyncio.create_task(
            gate.run("inst-1", "holder-A", "task", work)
        )
        await entered.wait()
        assert await gate.is_held_by("inst-1", "holder-A") is True
        assert await gate.is_held_by("inst-1", "holder-B") is True
        release.set()
        await task

    @pytest.mark.asyncio
    async def test_cancel_instance_execution_is_noop(self, gate):
        """``cancel_instance_execution`` is preserved for backward
        compat but is a no-op under the asyncio.Lock gate. The
        caller's ``CancellationToken`` is the cancellation
        mechanism.
        """
        assert gate.cancel_instance_execution("inst-1") is None
        assert gate.cancel_instance_execution("anything") is None

    @pytest.mark.asyncio
    async def test_recover_stale_leases_is_noop(self, gate):
        """``recover_stale_leases`` is preserved for backward compat
        but is a no-op under the asyncio.Lock gate — there is no
        lease row to recover.
        """
        assert await gate.recover_stale_leases() == 0
        assert await gate.recover_stale_leases(max_age_seconds=300) == 0

    @pytest.mark.asyncio
    async def test_heartbeat_is_noop(self, gate):
        """``heartbeat`` is preserved for backward compat but is a
        no-op under the asyncio.Lock gate.
        """
        assert await gate.heartbeat("inst-1", "holder-A") is True

    def test_lease_repo_property_returns_none(self, gate):
        """The ``_lease_repo`` property is preserved for backward
        compat and returns None under the asyncio.Lock gate.
        """
        assert gate._lease_repo is None


# ─── TaskRepository requeue (gate contention path) ───────────────────────────


class TestTaskProcessorRequeueOnContention:
    """The gate's contention path triggers
    ``TaskRepository.requeue_task_with_backoff`` on the WorkerPool
    side. These tests exercise the requeue logic in isolation — the
    gate itself does not implement contention (it blocks on a
    per-instance lock instead), but the requeue path is the same one
    the WorkerPool would take if a future contention-aware variant
    were added.
    """

    @pytest.fixture
    def task_repo(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        yield TaskRepository(engine, on_pending_task=lambda: None)
        engine.dispose()

    @pytest.mark.asyncio
    async def test_requeue_task_moves_running_to_pending(self, task_repo):
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-1",
            message_id="msg-1",
        )
        task_repo.claim_pending_task(worker_id="worker-1")
        requeued = task_repo.requeue_task_with_backoff(
            task.id, min_delay_seconds=0.0, max_delay_seconds=0.0
        )
        assert requeued is not None
        assert requeued.status == TaskStatus.PENDING.value
        assert requeued.worker_id is None
        assert requeued.started_at is None
        assert requeued.last_heartbeat_at is None

    @pytest.mark.asyncio
    async def test_requeue_task_is_noop_for_completed_tasks(self, task_repo):
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-1",
            message_id="msg-1",
        )
        task_repo.claim_pending_task(worker_id="worker-1")
        task_repo.complete_task(task.id, {"ok": True})
        assert task_repo.requeue_task_with_backoff(task.id) is None
        row = task_repo.get(task.id)
        assert row.status == TaskStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_requeue_task_with_backoff_sets_next_retry_at(
        self, task_repo
    ):
        """requeue_task_with_backoff must set next_retry_at so the
        worker does NOT re-claim the same task on the next poll.
        """
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-1",
            message_id="msg-1",
        )
        task_repo.claim_pending_task(worker_id="worker-1")
        requeued = task_repo.requeue_task_with_backoff(
            task.id, min_delay_seconds=0.5, max_delay_seconds=2.0
        )
        assert requeued is not None
        assert requeued.status == TaskStatus.PENDING.value
        assert requeued.next_retry_at is not None
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(requeued.next_retry_at)
        now = datetime.now(timezone.utc).timestamp()
        assert ts.timestamp() > now
        re_claimed = task_repo.claim_pending_task(worker_id="worker-2")
        assert re_claimed is None

    @pytest.mark.asyncio
    async def test_requeue_task_with_backoff_is_noop_for_completed(
        self, task_repo
    ):
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-1",
            message_id="msg-1",
        )
        task_repo.claim_pending_task(worker_id="worker-1")
        task_repo.complete_task(task.id, {"ok": True})
        assert task_repo.requeue_task_with_backoff(task.id) is None

    def test_find_running_by_instance_returns_running_task(self, task_repo):
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-1",
            message_id="msg-1",
        )
        task_repo.claim_pending_task(worker_id="worker-1")
        running = task_repo.find_running_by_instance("inst-1")
        assert running is not None
        assert running.id == task.id
        assert running.status == TaskStatus.RUNNING.value

    def test_find_running_by_instance_returns_none_when_idle(self, task_repo):
        assert task_repo.find_running_by_instance("inst-1") is None

    def test_find_running_by_instance_ignores_completed(self, task_repo):
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-1",
            message_id="msg-1",
        )
        task_repo.claim_pending_task(worker_id="worker-1")
        task_repo.complete_task(task.id, {"ok": True})
        assert task_repo.find_running_by_instance("inst-1") is None


# ─── Cross-dispatcher race scenario ───────────────────────────────────────────


class TestCrossDispatcherRaceScenario:
    """Reproduces the original "giter-report-lost" race at the
    service level.

    The original bug: a MESSAGE job (JobQueue side) and a Task
    (WorkerPool side) both tried to drive ``graph.astream`` for the
    same instance concurrently. The asyncio.Lock gate prevents
    this: the second caller's work_fn waits for the first to
    release. Both work_fns run, but never concurrently.
    """

    @pytest.mark.asyncio
    async def test_message_job_and_task_serialize_on_same_instance(
        self, gate
    ):
        """A MESSAGE job runs first, then a Task for the same
        instance runs after the MESSAGE job releases. Their
        work_fns never overlap.
        """
        instance_id = "inst-cross"
        execution_order: list[str] = []
        message_job_release = asyncio.Event()
        task_started = asyncio.Event()
        active = 0
        max_active = 0
        counter_lock = asyncio.Lock()

        async def message_job_work():
            nonlocal active, max_active
            execution_order.append("mj-start")
            async with counter_lock:
                active += 1
                max_active = max(max_active, active)
            # Wait until the Task has started its blocked gate.run
            # call (so we know the lock has been passed to us).
            await task_started.wait()
            await message_job_release.wait()
            async with counter_lock:
                active -= 1
            execution_order.append("mj-end")
            return "message_job_done"

        async def task_work():
            nonlocal active, max_active
            execution_order.append("task-start")
            async with counter_lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            async with counter_lock:
                active -= 1
            execution_order.append("task-end")
            return "task_done"

        # Launch the MESSAGE job first; it acquires the lock.
        mj_task = asyncio.create_task(
            gate.run(instance_id, "message_job:A", "message_job", message_job_work)
        )
        # Yield so the MESSAGE job acquires the lock.
        await asyncio.sleep(0.01)

        # Schedule the Task to run on the same instance. This call
        # blocks (NEW asyncio.Lock semantics) until the MESSAGE
        # job releases. Run it as a background task so we can
        # coordinate the release.
        task_task = asyncio.create_task(
            gate.run(instance_id, "task:42", "task", task_work)
        )

        # Wait until the task has been scheduled (i.e. its
        # gate.run is blocked waiting for the lock).
        await asyncio.sleep(0.02)
        # Signal the MESSAGE job that the task is blocked, then
        # release the MESSAGE job so it finishes.
        task_started.set()
        message_job_release.set()

        # Now both should complete in order.
        mj_outcome = await mj_task
        task_outcome = await task_task

        # No work_fn overlapped.
        assert max_active == 1
        # Both ran, in order.
        assert mj_outcome == "message_job_done"
        assert task_outcome == "task_done"
        # Execution order: MESSAGE job bracketed, then Task bracketed.
        assert execution_order == [
            "mj-start", "mj-end", "task-start", "task-end",
        ]


    @pytest.mark.asyncio
    async def test_concurrent_holders_serialize_via_lock(self, gate):
        """Two ``gate.run`` calls launched in parallel for the same
        instance must produce non-overlapping work_fns. The lock
        guarantees this regardless of which caller was scheduled
        first.
        """
        instance_id = "inst-parallel-holders"
        active = 0
        max_active = 0
        counter_lock = asyncio.Lock()

        async def holder1():
            nonlocal active, max_active
            async with counter_lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            async with counter_lock:
                active -= 1
            return "h1"

        async def holder2():
            nonlocal active, max_active
            async with counter_lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            async with counter_lock:
                active -= 1
            return "h2"

        r1, r2 = await asyncio.gather(
            gate.run(instance_id, "message_job:A", "message_job", holder1),
            gate.run(instance_id, "task:B", "task", holder2),
        )

        # The lock serializes: at most one work_fn at a time.
        assert max_active == 1
        # Both completed with their own result.
        assert {r1, r2} == {"h1", "h2"}
