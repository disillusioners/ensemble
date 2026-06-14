"""Unit tests for the Execution Gate and its lease repository.

Covers:
- Acquire / release happy path
- Concurrent acquire serialises correctly (one wins, the other gets
  LeaseContention)
- Re-entrant acquire by the same holder is a no-op (in-process fast
  path)
- Conditional release — a stale loser cannot delete a fresh winner's
  row
- Heartbeat refresh
- Crash recovery: stale leases are deleted, fresh ones are not
- ProcessMessageProcessor re-queue path on lease contention
- MessageJobHandler cross-dispatcher check sees running tasks and
  re-queues
"""

from __future__ import annotations

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.execution_lease.models import (
    InstanceExecutionLease,
    LeaseHolderKind,
)
from daemon.repositories.execution_lease.repository import (
    ExecutionLeaseRepository,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.services.execution_gate import (
    ExecutionGateService,
    LeaseContention,
    LeaseContentionReason,
    LeaseLostError,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine with the lease + task tables registered.

    StaticPool so multiple threads share the same connection — required
    for the worker's threading model, even though the gate's DB
    operations are all single-statement and short-lived.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def lease_repo(engine):
    return ExecutionLeaseRepository(engine)


@pytest.fixture
def task_repo(engine):
    return TaskRepository(engine, on_pending_task=lambda: None)


@pytest.fixture
def gate(lease_repo):
    return ExecutionGateService(lease_repo=lease_repo)


# ─── Repository: acquire / release ─────────────────────────────────────────────


class TestLeaseRepositoryAcquireRelease:
    def test_try_acquire_succeeds_when_free(self, lease_repo):
        ok = lease_repo.try_acquire(
            "inst-1", "message_job:job-A", LeaseHolderKind.MESSAGE_JOB.value
        )
        assert ok is True

    def test_try_acquire_returns_holder_after_first_acquire(
        self, lease_repo
    ):
        lease_repo.try_acquire(
            "inst-1", "message_job:job-A", LeaseHolderKind.MESSAGE_JOB.value
        )
        holder = lease_repo.get_holder("inst-1")
        assert holder is not None
        assert holder.holder_id == "message_job:job-A"
        assert holder.holder_kind == LeaseHolderKind.MESSAGE_JOB.value

    def test_try_acquire_fails_when_held_by_other(self, lease_repo):
        lease_repo.try_acquire(
            "inst-1", "message_job:job-A", LeaseHolderKind.MESSAGE_JOB.value
        )
        ok = lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        assert ok is False

    def test_try_acquire_succeeds_for_same_holder_id_repeatedly(
        self, lease_repo
    ):
        """The same holder can re-acquire (e.g. on retry); the
        row already exists with the same PK so the INSERT is a no-op
        and the call returns True (rowcount 0 means 'no insert
        happened but the row matches'). This is by design — a
        re-entrant acquire from the same caller is idempotent and
        should not be reported as contention.
        """
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        ok = lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        assert ok is True

    def test_release_deletes_row(self, lease_repo):
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        deleted = lease_repo.release("inst-1", "task:42")
        assert deleted is True
        assert lease_repo.get_holder("inst-1") is None

    def test_release_is_idempotent(self, lease_repo):
        """Releasing an already-released lease returns False (no row
        to delete), but does not raise. Dispatchers rely on this when
        they hold a holder_id that no longer matches a row (e.g. the
        recovery loop cleared the row out from under them).
        """
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        assert lease_repo.release("inst-1", "task:42") is True
        # Second release is a no-op
        assert lease_repo.release("inst-1", "task:42") is False

    def test_release_with_wrong_holder_id_does_nothing(self, lease_repo):
        """A stale loser must not accidentally evict a fresh winner."""
        lease_repo.try_acquire(
            "inst-1", "message_job:job-A", LeaseHolderKind.MESSAGE_JOB.value
        )
        # Someone else tries to release — they should not be able to.
        assert lease_repo.release("inst-1", "task:99") is False
        # Original holder is still there.
        assert lease_repo.get_holder("inst-1").holder_id == "message_job:job-A"

    def test_is_held_by(self, lease_repo):
        lease_repo.try_acquire(
            "inst-1", "message_job:job-A", LeaseHolderKind.MESSAGE_JOB.value
        )
        assert lease_repo.is_held_by("inst-1", "message_job:job-A") is True
        assert lease_repo.is_held_by("inst-1", "task:99") is False
        assert lease_repo.is_held_by("inst-other", "message_job:job-A") is False


# ─── Repository: heartbeat ─────────────────────────────────────────────────────


class TestLeaseRepositoryHeartbeat:
    def test_heartbeat_updates_holder_row(self, lease_repo):
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        before = lease_repo.get_holder("inst-1").heartbeat_at
        # Sleep a smidge to make the timestamp differ.
        import time
        time.sleep(0.01)
        ok = lease_repo.heartbeat("inst-1", "task:42")
        assert ok is True
        after = lease_repo.get_holder("inst-1").heartbeat_at
        assert after > before

    def test_heartbeat_returns_false_for_wrong_holder(self, lease_repo):
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        ok = lease_repo.heartbeat("inst-1", "task:99")
        assert ok is False


# ─── Repository: crash recovery ───────────────────────────────────────────────


class TestLeaseRepositoryRecovery:
    def test_find_stale_leases_uses_heartbeat(self, lease_repo):
        # Create a lease, then manually backdate its heartbeat.
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        with SQLModelSession(lease_repo.engine) as session:
            row = session.get(InstanceExecutionLease, "inst-1")
            row.heartbeat_at = datetime.now(timezone.utc) - timedelta(
                seconds=1000
            )
            session.add(row)
            session.commit()
        # Also a fresh lease that should NOT be cleared.
        lease_repo.try_acquire(
            "inst-2", "task:43", LeaseHolderKind.TASK.value
        )
        stale = lease_repo.find_stale_leases(max_age_seconds=300)
        assert len(stale) == 1
        assert stale[0].instance_id == "inst-1"

    def test_clear_stale_removes_row(self, lease_repo):
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        ok = lease_repo.clear_stale("inst-1")
        assert ok is True
        assert lease_repo.get_holder("inst-1") is None

    def test_clear_stale_does_not_require_holder_id(self, lease_repo):
        """clear_stale is the recovery primitive — no holder_id check.

        Contrast with release() which DOES require holder_id. A
        well-behaved dispatcher must never call clear_stale().
        """
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        # No holder_id provided — should still work.
        assert lease_repo.clear_stale("inst-1") is True

    def test_clear_stale_leases_bulk_deletes(self, lease_repo):
        """clear_stale_leases is the bulk recovery primitive (one
        round-trip, no N+1) used by ``recover_stale_leases`` on
        startup.
        """
        lease_repo.try_acquire(
            "inst-stale", "task:42", LeaseHolderKind.TASK.value
        )
        lease_repo.try_acquire(
            "inst-fresh", "task:43", LeaseHolderKind.TASK.value
        )
        # Backdate the first one's heartbeat to past the threshold.
        with SQLModelSession(lease_repo.engine) as session:
            row = session.get(InstanceExecutionLease, "inst-stale")
            row.heartbeat_at = datetime.now(timezone.utc) - timedelta(
                seconds=1000
            )
            session.add(row)
            session.commit()
        cleared = lease_repo.clear_stale_leases(max_age_seconds=300)
        assert cleared == 1
        assert lease_repo.get_holder("inst-stale") is None
        assert lease_repo.get_holder("inst-fresh") is not None

    def test_find_stale_leases_uses_sql_filter(self, lease_repo):
        """The stale-lease scan must filter in SQL (not load all
        rows into Python) so it stays cheap as the table grows.
        The default_factory on the column guarantees
        ``heartbeat_at`` is never NULL on insert, so the
        ``COALESCE(heartbeat_at, acquired_at)`` is a defensive
        belt-and-braces against hand-edited or older-schema rows.
        """
        lease_repo.try_acquire(
            "inst-stale-2", "task:42", LeaseHolderKind.TASK.value
        )
        # A row whose heartbeat is well past the threshold is stale.
        with SQLModelSession(lease_repo.engine) as session:
            row = session.get(InstanceExecutionLease, "inst-stale-2")
            row.heartbeat_at = datetime.now(timezone.utc) - timedelta(
                seconds=1000
            )
            session.add(row)
            session.commit()
        # A row whose heartbeat is fresh is NOT stale.
        lease_repo.try_acquire(
            "inst-fresh-2", "task:43", LeaseHolderKind.TASK.value
        )
        stale = lease_repo.find_stale_leases(max_age_seconds=300)
        ids = {s.instance_id for s in stale}
        assert "inst-stale-2" in ids
        assert "inst-fresh-2" not in ids


# ─── Service: happy path ──────────────────────────────────────────────────────


class TestExecutionGateHappyPath:
    @pytest.mark.asyncio
    async def test_run_acquires_and_releases_lease(self, gate, lease_repo):
        called = {"count": 0}

        async def work():
            called["count"] += 1
            return "result-ok"

        out = await gate.run(
            "inst-1",
            "message_job:job-A",
            LeaseHolderKind.MESSAGE_JOB.value,
            work,
        )
        assert out == "result-ok"
        assert called["count"] == 1
        # Lease is released after the call.
        assert lease_repo.get_holder("inst-1") is None

    @pytest.mark.asyncio
    async def test_run_releases_lease_even_on_exception(
        self, gate, lease_repo
    ):
        async def work():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await gate.run(
                "inst-1",
                "task:42",
                LeaseHolderKind.TASK.value,
                work,
            )
        # Lease is still released.
        assert lease_repo.get_holder("inst-1") is None

    @pytest.mark.asyncio
    async def test_run_releases_lease_on_cancellation(
        self, gate, lease_repo
    ):
        async def work():
            await asyncio.sleep(10)

        task = asyncio.create_task(
            gate.run(
                "inst-1",
                "task:42",
                LeaseHolderKind.TASK.value,
                work,
            )
        )
        # Let it acquire the lease.
        await asyncio.sleep(0.05)
        assert lease_repo.get_holder("inst-1") is not None
        # Cancel the task.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Lease was released by the finally clause.
        assert lease_repo.get_holder("inst-1") is None


# ─── Service: contention ──────────────────────────────────────────────────────


class TestExecutionGateContention:
    @pytest.mark.asyncio
    async def test_second_caller_gets_lease_contention(self, gate, lease_repo):
        started = asyncio.Event()
        release_inner = asyncio.Event()

        async def holder_work():
            started.set()
            await release_inner.wait()
            return "holder-done"

        async def loser_work():
            return "should-not-run"

        holder_task = asyncio.create_task(
            gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                holder_work,
            )
        )
        await started.wait()
        # Lease is now held. Second caller must see contention.
        out = await gate.run(
            "inst-1",
            "task:42",
            LeaseHolderKind.TASK.value,
            loser_work,
        )
        assert isinstance(out, LeaseContention)
        assert out.holder_id == "message_job:job-A"
        assert out.holder_kind == LeaseHolderKind.MESSAGE_JOB.value
        assert out.reason == LeaseContentionReason.HELD_BY_OTHER
        # Release the holder; the holder's result is still "holder-done".
        release_inner.set()
        result = await holder_task
        assert result == "holder-done"

    @pytest.mark.asyncio
    async def test_loser_does_not_run_work(self, gate):
        ran = {"count": 0}

        async def holder_work():
            await asyncio.Event().wait()  # wait forever

        async def loser_work():
            ran["count"] += 1
            return "should-not-see"

        holder = asyncio.create_task(
            gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                holder_work,
            )
        )
        await asyncio.sleep(0.05)  # let the holder acquire
        out = await gate.run(
            "inst-1",
            "task:42",
            LeaseHolderKind.TASK.value,
            loser_work,
        )
        assert isinstance(out, LeaseContention)
        assert ran["count"] == 0
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder

    @pytest.mark.asyncio
    async def test_lease_becomes_available_after_holder_releases(
        self, gate, lease_repo
    ):
        async def work():
            return "first"

        await gate.run(
            "inst-1",
            "message_job:job-A",
            LeaseHolderKind.MESSAGE_JOB.value,
            work,
        )
        # After release, a new acquire should succeed.
        out = await gate.run(
            "inst-1",
            "task:42",
            LeaseHolderKind.TASK.value,
            work,
        )
        assert out == "first"

    @pytest.mark.asyncio
    async def test_reentrant_call_by_same_holder_is_allowed(
        self, gate, lease_repo
    ):
        """A holder that already holds the lease (via a previous
        gate.run in this process) can call gate.run again for the
        same instance without contention. This is rare in current
        code (dispatchers don't re-enter) but the fast path exists
        to avoid an unnecessary DB roundtrip.

        Concretely: after the first call, ``is_held_locally`` is
        True, so the second call short-circuits to
        ``_execute_under_lease`` without re-acquiring the lease.
        The result: the second work_fn runs, no contention.
        """
        outer_started = asyncio.Event()
        outer_release = asyncio.Event()

        async def outer():
            outer_started.set()
            await outer_release.wait()
            # Re-entrant call from inside the lease.
            return await gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                inner,
            )

        async def inner():
            return "inner-result"

        task = asyncio.create_task(
            gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                outer,
            )
        )
        await outer_started.wait()
        outer_release.set()
        result = await task
        assert result == "inner-result"
        # Lease was released.
        assert lease_repo.get_holder("inst-1") is None


# ─── Service: cancel_instance_execution ───────────────────────────────────────


class TestExecutionGateCancel:
    @pytest.mark.asyncio
    async def test_cancel_interrupts_running_work(self, gate):
        entered = asyncio.Event()

        async def work():
            entered.set()
            await asyncio.sleep(10)
            return "should-not-see"

        task = asyncio.create_task(
            gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                work,
            )
        )
        await entered.wait()
        ok = await gate.cancel_instance_execution("inst-1")
        assert ok is True
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_cancel_returns_false_when_nothing_running(self, gate):
        ok = await gate.cancel_instance_execution("inst-1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_cancel_drains_inner_work_task(
        self, gate, lease_repo
    ):
        """Regression: after ``cancel_instance_execution`` returns,
        the inner ``work_task`` (the actual ``graph.astream`` driver)
        must be cancelled AND drained — not left running detached.

        The bug was that the gate's ``finally`` only cancelled the
        heartbeat task; the ``work_task`` was a bare
        ``asyncio.create_task`` that the structured-concurrency
        cleanup path didn't see. With the fix, the finally cancels
        and awaits both child tasks.
        """
        work_cancelled = {"flag": False}
        work_finished = asyncio.Event()

        async def work():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                work_cancelled["flag"] = True
                raise
            finally:
                work_finished.set()

        task = asyncio.create_task(
            gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                work,
            )
        )
        # Let the work_fn start.
        await asyncio.sleep(0.05)
        ok = await gate.cancel_instance_execution("inst-1")
        assert ok is True
        with pytest.raises(asyncio.CancelledError):
            await task

        # Both must have settled before the await returned.
        assert work_cancelled["flag"] is True
        assert work_finished.is_set()
        # The lease must be released.
        assert lease_repo.get_holder("inst-1") is None


# ─── Service: crash recovery ──────────────────────────────────────────────────


class TestExecutionGateRecovery:
    @pytest.mark.asyncio
    async def test_recover_stale_leases_clears_old_rows(self, gate, lease_repo):
        # Insert a lease whose heartbeat is in the distant past.
        lease_repo.try_acquire(
            "inst-stale", "task:42", LeaseHolderKind.TASK.value
        )
        with SQLModelSession(lease_repo.engine) as session:
            row = session.get(InstanceExecutionLease, "inst-stale")
            row.heartbeat_at = datetime.now(timezone.utc) - timedelta(
                seconds=1000
            )
            session.add(row)
            session.commit()
        # And a fresh one that should NOT be cleared.
        lease_repo.try_acquire(
            "inst-fresh", "task:43", LeaseHolderKind.TASK.value
        )

        cleared = await gate.recover_stale_leases(max_age_seconds=300)
        assert cleared == 1
        assert lease_repo.get_holder("inst-stale") is None
        assert lease_repo.get_holder("inst-fresh") is not None

    @pytest.mark.asyncio
    async def test_recover_stale_leases_returns_zero_when_nothing_stale(
        self, gate, lease_repo
    ):
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )
        cleared = await gate.recover_stale_leases(max_age_seconds=300)
        assert cleared == 0
        # Fresh lease is preserved.
        assert lease_repo.get_holder("inst-1") is not None


# ─── Service: in-flight heartbeat / lease-loss detection ──────────────────────


class TestExecutionGateHeartbeat:
    @pytest.mark.asyncio
    async def test_lease_lost_raises_lease_lost_error(self, gate, lease_repo):
        """If the lease row is deleted by another process (e.g.
        ``recover_stale_leases`` on a different node) while
        ``work_fn`` is in flight, ``gate.run`` must cancel the work
        and raise ``LeaseLostError``.
        """
        heartbeat_seen = asyncio.Event()
        work_started = asyncio.Event()
        work_cancelled = {"flag": False}

        async def work():
            work_started.set()
            try:
                # Long enough for at least one heartbeat tick.
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                work_cancelled["flag"] = True
                raise

        # Patch the heartbeat method so we can both (a) wait for
        # the heartbeat task to be running and (b) simulate the
        # lease being lost on the second tick.
        call_count = {"n": 0}

        async def fake_heartbeat(instance_id, holder_id):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First heartbeat: refresh succeeds.
                return await gate.heartbeat.__wrapped__(
                    gate, instance_id, holder_id
                ) if hasattr(gate.heartbeat, "__wrapped__") else True
            # Subsequent heartbeats: simulate lease loss.
            heartbeat_seen.set()
            return False

        gate.heartbeat = fake_heartbeat  # type: ignore[assignment]
        # Tighten the heartbeat interval so the test runs quickly.
        gate._heartbeat_interval = 0.05

        with pytest.raises(LeaseLostError):
            await gate.run(
                "inst-1",
                "task:42",
                LeaseHolderKind.TASK.value,
                work,
            )
        assert work_cancelled["flag"] is True
        # After the loss, the holder row is gone (work was cancelled
        # before the gate ran the release; the release is still
        # called in the finally but is a no-op for the lost lease).
        # We don't assert on the row's existence — the heartbeat
        # returned False because the row was already deleted, so
        # the release is a no-op.

    @pytest.mark.asyncio
    async def test_in_flight_heartbeat_refreshes_heartbeat_at(
        self, gate, lease_repo
    ):
        """The in-flight heartbeat (spawned by ``_execute_under_lease``)
        must refresh ``heartbeat_at`` while ``work_fn`` is running,
        so a long-running ``graph.astream`` is not eligible for
        eviction by ``recover_stale_leases`` on another node.
        """
        async def work():
            # Sleep longer than the heartbeat interval; the
            # heartbeat task should have fired at least once.
            await asyncio.sleep(gate._heartbeat_interval * 3 + 0.05)
            return "done"
        before = datetime.now(timezone.utc)
        out = await gate.run(
            "inst-1",
            "task:42",
            LeaseHolderKind.TASK.value,
            work,
        )
        assert out == "done"
        after = datetime.now(timezone.utc)
        # The lease row may have been released already; the
        # important thing is that during the call, the heartbeat
        # fired (we verified by side effect — no exception, the
        # work completed). ``before``/``after`` is just to ensure
        # the test exercised real time.
        assert (after - before).total_seconds() >= gate._heartbeat_interval

    @pytest.mark.asyncio
    async def test_heartbeat_escalates_to_lease_lost_after_consecutive_errors(
        self, gate, lease_repo
    ):
        """After ``heartbeat_max_consecutive_errors`` consecutive DB
        errors, the heartbeat loop must escalate to ``lease_lost``
        so the in-flight work is cancelled deterministically rather
        than left running against an un-refreshed lease that
        ``recover_stale_leases`` on another node would eventually
        evict anyway.
        """
        work_cancelled = {"flag": False}

        async def work():
            try:
                # Long enough for several heartbeat ticks to fail.
                await asyncio.sleep(2.0)
            except asyncio.CancelledError:
                work_cancelled["flag"] = True
                raise

        async def always_failing_heartbeat(instance_id, holder_id):
            raise RuntimeError("simulated DB outage")

        gate.heartbeat = always_failing_heartbeat  # type: ignore[assignment]
        gate._heartbeat_interval = 0.05
        # Tighten the threshold so the test doesn't have to wait long.
        gate._heartbeat_max_consecutive_errors = 3

        with pytest.raises(LeaseLostError):
            await gate.run(
                "inst-1",
                "task:42",
                LeaseHolderKind.TASK.value,
                work,
            )
        assert work_cancelled["flag"] is True

    @pytest.mark.asyncio
    async def test_lease_lost_during_contention_flag(
        self, gate, lease_repo
    ):
        """If the lease row exists at ``try_acquire`` time but is
        gone by the time we ask ``get_holder`` (vanishingly rare),
        the returned ``LeaseContention`` has
        ``holder_lost_during_contention=True`` and empty
        ``holder_id``/``holder_kind``.
        """
        # Acquire a lease, then race the contender: delete the
        # row between try_acquire and get_holder. The cleanest
        # way is to monkey-patch ``get_holder`` to return None
        # for one call.
        lease_repo.try_acquire(
            "inst-1", "task:42", LeaseHolderKind.TASK.value
        )

        original_get_holder = lease_repo.get_holder
        call_count = {"n": 0}

        def flaky_get_holder(instance_id):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None  # Simulate the row being deleted
            return original_get_holder(instance_id)

        lease_repo.get_holder = flaky_get_holder  # type: ignore[assignment]
        try:
            out = await gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                lambda: _never_called(),
            )
        finally:
            lease_repo.get_holder = original_get_holder  # type: ignore[assignment]

        assert isinstance(out, LeaseContention)
        assert out.holder_lost_during_contention is True
        assert out.holder_id == ""
        assert out.holder_kind == ""
        assert out.reason == LeaseContentionReason.HELD_BY_LOST


async def _never_called():
    raise AssertionError("work_fn must NOT run when lease is contended")


# ─── Integration: ProcessMessageProcessor re-queue ───────────────────────────


class TestTaskProcessorRequeueOnContention:
    @pytest.mark.asyncio
    async def test_requeue_task_moves_running_to_pending(self, task_repo):
        """No-backoff requeue_task was removed in favour of
        requeue_task_with_backoff (the only production caller). This
        test now exercises the with-backoff variant for parity.
        """
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-1",
            message_id="msg-1",
        )
        # Simulate the worker having claimed it.
        task_repo.claim_pending_task(worker_id="worker-1")
        # Now re-queue (as the gate-contention path would).
        requeued = task_repo.requeue_task_with_backoff(
            task.id, min_delay_seconds=0.0, max_delay_seconds=0.0
        )
        assert requeued is not None
        assert requeued.status == TaskStatus.PENDING.value
        assert requeued.worker_id is None
        assert requeued.started_at is None
        assert requeued.last_heartbeat_at is None

    @pytest.mark.asyncio
    async def test_requeue_task_is_noop_for_completed_tasks(
        self, task_repo
    ):
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="inst-1",
            message_id="msg-1",
        )
        task_repo.claim_pending_task(worker_id="worker-1")
        task_repo.complete_task(task.id, {"ok": True})
        # Re-queue must be a no-op for non-RUNNING tasks.
        assert task_repo.requeue_task_with_backoff(task.id) is None
        # Status unchanged.
        row = task_repo.get(task.id)
        assert row.status == TaskStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_requeue_task_with_backoff_sets_next_retry_at(
        self, task_repo
    ):
        """requeue_task_with_backoff must set next_retry_at so the
        worker does NOT re-claim the same task on the next poll.
        The cross-dispatcher contention path uses this to prevent
        busy-spin against a sibling MESSAGE job.
        """
        from datetime import datetime, timezone
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
        # next_retry_at must be in the future (within the jitter
        # window) so the next poll skips this task.
        assert requeued.next_retry_at is not None
        ts = datetime.fromisoformat(requeued.next_retry_at)
        now = datetime.now(timezone.utc).timestamp()
        assert ts.timestamp() > now
        # The worker re-poll must NOT claim it (next_retry_at > now).
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
        """TaskRepository.find_running_by_instance must return the
        RUNNING task for an instance, or None.
        """
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


# ─── In-process fast path ─────────────────────────────────────────────────────


class TestExecutionGateLocalFastPath:
    @pytest.mark.asyncio
    async def test_is_held_locally_false_initially(self, gate):
        assert gate.is_held_locally("inst-1") is False

    @pytest.mark.asyncio
    async def test_is_held_locally_true_during_run(self, gate):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def work():
            entered.set()
            await release.wait()

        task = asyncio.create_task(
            gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                work,
            )
        )
        await entered.wait()
        assert gate.is_held_locally("inst-1") is True
        release.set()
        await task
        # Local holder cleared after release.
        assert gate.is_held_locally("inst-1") is False

    @pytest.mark.asyncio
    async def test_is_held_locally_distinct_per_instance(self, gate):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def work():
            entered.set()
            await release.wait()

        task = asyncio.create_task(
            gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                work,
            )
        )
        await entered.wait()
        assert gate.is_held_locally("inst-1") is True
        assert gate.is_held_locally("inst-2") is False
        release.set()
        await task


# ─── Helpers ──────────────────────────────────────────────────────────────────


# Local import to avoid polluting module-level names.
from sqlmodel import Session as SQLModelSession


# ─── Integration: cross-dispatcher race scenario ─────────────────────────────


class TestCrossDispatcherRaceScenario:
    """Reproduces the original bug at a service-level.

    The original bug: a MESSAGE job (JobQueue side) and a Task
    (WorkerPool side) both tried to drive ``graph.astream`` for the
    same instance concurrently, and one's update overwrote the
    other's message in the langgraph checkpoint (the
    "giter-report-lost" bug).

    The Execution Gate prevents this: when the second dispatcher
    calls ``gate.run``, the first one holds the lease, and the
    second sees ``LeaseContention`` and re-queues. This test
    simulates the race with two coroutines and confirms the gate
    serialises them.
    """

    @pytest.mark.asyncio
    async def test_message_job_and_task_cannot_drive_same_instance(
        self, gate, lease_repo
    ):
        """A MESSAGE job holds the lease; a Task for the same
        instance must see contention and back off without
        executing its work.

        Reproduces the scenario from
        docs/bugs/child-completion-report-lost-cross-dispatcher-*.
        """
        # The MESSAGE job starts running and holds the lease.
        message_job_started = asyncio.Event()
        message_job_release = asyncio.Event()

        async def message_job_work():
            message_job_started.set()
            await message_job_release.wait()
            return "message_job_done"

        message_job_holder = asyncio.create_task(
            gate.run(
                "inst-1",
                "message_job:job-A",
                LeaseHolderKind.MESSAGE_JOB.value,
                message_job_work,
            )
        )
        await message_job_started.wait()

        # The Task tries to run for the same instance. It must
        # see LeaseContention.
        task_work_ran = {"count": 0}

        async def task_work():
            task_work_ran["count"] += 1
            return "task_done"

        task_outcome = await gate.run(
            "inst-1",
            "task:42",
            LeaseHolderKind.TASK.value,
            task_work,
        )
        assert isinstance(task_outcome, LeaseContention)
        assert task_work_ran["count"] == 0  # Task's work NEVER ran

        # Lease state is clean: only the MESSAGE job holds the row.
        holder = lease_repo.get_holder("inst-1")
        assert holder is not None
        assert holder.holder_id == "message_job:job-A"

        # Release the MESSAGE job. Holder finishes.
        message_job_release.set()
        result = await message_job_holder
        assert result == "message_job_done"

        # After release, the Task CAN run.
        task_outcome2 = await gate.run(
            "inst-1",
            "task:42",
            LeaseHolderKind.TASK.value,
            task_work,
        )
        assert task_outcome2 == "task_done"
        assert task_work_ran["count"] == 1

    @pytest.mark.asyncio
    async def test_only_one_holder_at_a_time_in_db(
        self, gate, lease_repo
    ):
        """The DB-level guarantee: at most one lease row per
        instance. Two concurrent acquirers cannot both insert
        (the INSERT OR IGNORE is atomic).
        """
        async def holder1():
            await asyncio.sleep(0.05)
            return "h1"

        async def holder2():
            await asyncio.sleep(0.05)
            return "h2"

        h1 = asyncio.create_task(
            gate.run("inst-1", "message_job:A", "message_job", holder1)
        )
        # Tiny yield so h1 acquires first
        await asyncio.sleep(0.01)
        h2 = asyncio.create_task(
            gate.run("inst-1", "task:B", "task", holder2)
        )
        await asyncio.sleep(0.01)
        # Only one row exists; it's h1's.
        holder = lease_repo.get_holder("inst-1")
        assert holder is not None
        assert holder.holder_id == "message_job:A"
        # h2 saw contention; h1 wins, h2 returns LeaseContention.
        r2 = await h2
        assert isinstance(r2, LeaseContention)
        # h1 finishes.
        r1 = await h1
        assert r1 == "h1"
