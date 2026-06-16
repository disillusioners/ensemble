"""Tests for the ExecutionGate wrapping in ``_resume_processing_background``.

Race #5 fix: the resume path now acquires a per-instance execution
lease via ``ExecutionGateService.run()`` before driving
``graph.astream``. Without this, concurrent /resume calls (or a
WorkerPool / JobQueue dispatch racing a resume) would corrupt the
langgraph checkpoint.

These tests cover:

1. **Concurrent resume + MESSAGE job contention** — when a MESSAGE job
   is mid-flight, a fresh ``_resume_processing_background`` call sees
   ``LeaseContention`` and retries / falls back rather than racing on
   the checkpoint.
2. **Concurrent resume + WorkerPool task** — same, but the holder is
   a ``task:`` lease (the WorkerPool path). The resume must defer
   rather than start a parallel ``graph.astream`` call.
3. **Retry limit prevents infinite loop (Fix C6)** — after
   ``MAX_RESUME_RETRIES`` (3) consecutive contentions, the resume
   falls back to ``enqueue_message`` instead of retrying forever. The
   ``enqueue_message`` call uses ``source="resume_exhausted"`` so
   operators can tell the difference from a normal resume.
4. **LeaseLostError handling** — when the in-flight lease row is
   cleared mid-resume (e.g. ``recover_stale_leases`` on another
   node), the resume transitions the instance to ``ERROR`` and marks
   the JobQueue job ``FAILED``.

The tests use a fake ``_execution_gate`` so the lease acquire /
release path is bypassed and we can deterministically return
``LeaseContention`` / ``MessageResult`` / raise ``LeaseLostError``.
Real-world ``ExecutionGateService`` behaviour is covered by
``tests/unit/services/test_execution_gate.py``.

To keep the suite fast, ``asyncio.sleep`` is patched to a no-op so
the 0.5/1.0/2.0s backoff delays don't actually block.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.manager import InstanceManager
from daemon.config import Config
from daemon.repositories.execution_lease.models import LeaseHolderKind
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.execution_gate import (
    LeaseContention,
    LeaseContentionReason,
    LeaseLostError,
)
from daemon.services.job_queue_service import DemandState


# ─── Test doubles ─────────────────────────────────────────────────────────────


class MockMessageResult:
    """Mock return value of ``_process_message_with_tracking``."""

    def __init__(self, content: str = "Resume completed") -> None:
        self.content = content


class MockAsyncMessageResult:
    """Mock return value of ``enqueue_message`` (WorkerPool path)."""

    def __init__(self, message_id: str | None = None) -> None:
        self.message_id = message_id or str(uuid.uuid4())
        self.instance_id = None
        self.status = "queued"


def _make_contention(holder_kind: str, holder_id: str) -> LeaseContention:
    """Build a ``LeaseContention`` instance for use in tests."""
    return LeaseContention(
        reason=LeaseContentionReason.HELD_BY_OTHER,
        holder_id=holder_id,
        holder_kind=holder_kind,
    )


def _make_fake_gate(
    side_effects: list | None = None,
    raise_after: tuple[type[BaseException], str] | None = None,
) -> MagicMock:
    """Build a fake ``_execution_gate`` whose ``run`` method returns the
    values in ``side_effects`` in order. If ``raise_after`` is set, the
    ``run`` call raises the given exception *instead* of returning
    (used for the ``LeaseLostError`` test).
    """
    gate = MagicMock()
    if raise_after is not None:
        exc_cls, msg = raise_after
        gate.run = AsyncMock(side_effect=exc_cls(msg))
    else:
        gate.run = AsyncMock(side_effect=side_effects or [MockMessageResult()])
    return gate


class MockInstanceMeta:
    """Mock instance metadata returned by ``_instance_repository.get``.

    The post-processing branch of ``_resume_processing_background``
    checks ``instance.waiting_for > 0`` and ``instance.status`` to
    decide whether to skip the job completion. ``MagicMock`` defaults
    raise ``TypeError`` on integer comparison, so we use a real
    object with the two fields the resume path reads.
    """

    def __init__(
        self,
        instance_id: str = "test-instance",
        status: str = InstanceStatus.RUNNING.value,
        waiting_for: int = 0,
    ) -> None:
        self.instance_id = instance_id
        self.status = status
        self.waiting_for = waiting_for


def _make_manager(gate: MagicMock) -> InstanceManager:
    """Build a minimally-mocked ``InstanceManager`` for exercising
    ``_resume_processing_background`` directly.

    Only the attributes / methods the resume path actually touches
    are wired up. Anything else on the manager is the default
    ``MagicMock`` created by ``__new__`` + manual attribute setting.
    """
    job_queue_service = MagicMock()
    job_queue_service.complete_job = AsyncMock()

    instance_repository = MagicMock()
    instance_repository.update_instance = MagicMock()
    # Default: instance is RUNNING with no pending children. The
    # resume path's ``waiting_for > 0`` check would raise TypeError
    # on a bare MagicMock; the real-looking object avoids that.
    instance_repository.get = MagicMock(return_value=MockInstanceMeta())

    manager = InstanceManager.__new__(InstanceManager)
    manager._job_queue_service = job_queue_service
    manager._instance_repository = instance_repository
    manager._execution_gate = gate
    manager._process_message_with_tracking = AsyncMock(
        return_value=MockMessageResult()
    )
    manager._process_child_completion_and_notify_parent = AsyncMock()
    manager.enqueue_message = AsyncMock(return_value=MockAsyncMessageResult())
    manager._graph_tasks = {}
    return manager


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestResumeGateWrapping:
    """Verify the resume path goes through ``_execution_gate.run``."""

    @pytest.mark.asyncio
    async def test_happy_path_acquires_gate_and_completes_job(self):
        """When the gate is free, resume acquires it and runs to completion.

        Verifies:
        - ``_execution_gate.run`` is called once with holder_id
          ``resume:<message_id>`` and ``LeaseHolderKind.MESSAGE_JOB``.
        - The job ends in ``DemandState.COMPLETED``.
        - The instance is NOT marked ERROR.
        """
        gate = _make_fake_gate(side_effects=[MockMessageResult()])
        manager = _make_manager(gate)

        instance_id = "inst-happy"
        message_id = str(uuid.uuid4())
        old_job_id = "job-happy"

        await manager._resume_processing_background(
            instance_id=instance_id,
            message="resume",
            message_id=message_id,
            old_job_id=old_job_id,
            silent=False,
            images=None,
        )

        gate.run.assert_awaited_once()
        kwargs = gate.run.await_args.kwargs
        assert kwargs["instance_id"] == instance_id
        assert kwargs["holder_id"] == f"resume:{message_id}"
        assert kwargs["holder_kind"] == LeaseHolderKind.MESSAGE_JOB.value
        assert callable(kwargs["work_fn"])

        # Job completed, instance NOT errored.
        manager._job_queue_service.complete_job.assert_awaited_once()
        # ``complete_job(job_id, demand_state, ...)`` is called with
        # positional args; check via .args[1] for the DemandState.
        assert (
            manager._job_queue_service.complete_job.await_args.args[1]
            == DemandState.COMPLETED
        )
        manager._instance_repository.update_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_resume_vs_message_job_contends(self):
        """Concurrent resume + MESSAGE job: one wins, the other contends.

        Simulates a MESSAGE job holding the lease when a resume
        attempt arrives. The first ``gate.run`` call returns
        ``LeaseContention`` (the MESSAGE job is mid-flight); a retry
        succeeds (the MESSAGE job has finished and released the lease).
        Verify the resume retries and eventually completes.
        """
        contention = _make_contention(
            holder_kind=LeaseHolderKind.MESSAGE_JOB.value,
            holder_id="message_job:job-other-123",
        )
        gate = _make_fake_gate(side_effects=[contention, MockMessageResult()])
        manager = _make_manager(gate)

        # Patch sleep so the 0.5s backoff doesn't block the test.
        with patch("daemon.manager.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await manager._resume_processing_background(
                instance_id="inst-contend-msg",
                message="resume",
                message_id=str(uuid.uuid4()),
                old_job_id="job-contend-msg",
                silent=False,
                images=None,
            )

        # First attempt contended, second attempt succeeded → 2 calls total.
        assert gate.run.await_count == 2
        # Backoff was applied between the attempts.
        mock_sleep.assert_awaited_once()
        # First backoff is 0.5s (the entry at index 0).
        assert mock_sleep.await_args.args[0] == manager.RESUME_BACKOFF_DELAYS[0]

        # Job completed (the second attempt succeeded).
        manager._job_queue_service.complete_job.assert_awaited_once()
        assert (
            manager._job_queue_service.complete_job.await_args.args[1]
            == DemandState.COMPLETED
        )

    @pytest.mark.asyncio
    async def test_concurrent_resume_vs_workerpool_task_contends(self):
        """Concurrent resume + WorkerPool task: same as above but the
        holder is a ``task:`` lease (the WorkerPool path).

        The ``holder_kind`` in the ``LeaseContention`` should be
        ``task`` — the resume must respect that this is a sibling
        dispatcher (WorkerPool) and back off.
        """
        contention = _make_contention(
            holder_kind=LeaseHolderKind.TASK.value,
            holder_id="task:42",
        )
        gate = _make_fake_gate(side_effects=[contention, MockMessageResult()])
        manager = _make_manager(gate)

        with patch("daemon.manager.asyncio.sleep", new=AsyncMock()):
            await manager._resume_processing_background(
                instance_id="inst-contend-task",
                message="resume",
                message_id=str(uuid.uuid4()),
                old_job_id="job-contend-task",
                silent=False,
                images=None,
            )

        assert gate.run.await_count == 2

        # The retry must re-enter with an incremented attempt counter
        # so the backoff schedule advances.
        second_call_kwargs = gate.run.await_args_list[1].kwargs
        assert second_call_kwargs["instance_id"] == "inst-contend-task"

        manager._job_queue_service.complete_job.assert_awaited_once()
        assert (
            manager._job_queue_service.complete_job.await_args.args[1]
            == DemandState.COMPLETED
        )

    @pytest.mark.asyncio
    async def test_retry_limit_falls_back_to_enqueue(self):
        """After ``MAX_RESUME_RETRIES`` (3) contentions, resume falls
        back to ``enqueue_message(source="resume_exhausted")`` instead
        of retrying forever.

        Verifies:
        - ``gate.run`` is called exactly ``MAX_RESUME_RETRIES`` + 1 = 4
          times (3 retries, then the 4th attempt is the final fall-back
          decision — but the final attempt's ``gate.run`` call
          *returns* the LeaseContention, after which we fall back).
        - ``enqueue_message`` is called exactly once with
          ``source="resume_exhausted"``.
        - The original job is NOT completed as COMPLETED (we
          abandoned the in-process path).
        - The instance is NOT marked ERROR (we recovered via enqueue).
        """
        contention = _make_contention(
            holder_kind=LeaseHolderKind.TASK.value,
            holder_id="task:stuck",
        )
        # 4 LeaseContention returns: attempts 0, 1, 2, 3 — the 4th
        # triggers the fall-back (since _retry_attempt becomes 3 and
        # 3 is NOT < MAX_RESUME_RETRIES=3, so we fall back).
        gate = _make_fake_gate(side_effects=[contention] * 4)
        manager = _make_manager(gate)

        with patch("daemon.manager.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await manager._resume_processing_background(
                instance_id="inst-exhaust",
                message="resume",
                message_id=str(uuid.uuid4()),
                old_job_id="job-exhaust",
                silent=False,
                images=None,
            )

        # Exactly 4 gate attempts: 3 retries + the final one that
        # triggers fall-back.
        assert gate.run.await_count == manager.MAX_RESUME_RETRIES + 1

        # Sleep was called once per retry (3 total), NOT 4 — the final
        # attempt doesn't sleep because it falls back immediately.
        assert mock_sleep.await_count == manager.MAX_RESUME_RETRIES

        # Fallback: enqueue_message called once with source=resume_exhausted.
        manager.enqueue_message.assert_awaited_once()
        em_kwargs = manager.enqueue_message.await_args.kwargs
        assert em_kwargs["instance_id"] == "inst-exhaust"
        assert em_kwargs["message"] == "resume"
        assert em_kwargs["source"] == "resume_exhausted"

        # We abandoned the in-process path: job NOT completed as
        # COMPLETED, instance NOT marked ERROR.
        manager._job_queue_service.complete_job.assert_not_awaited()
        manager._instance_repository.update_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_eventually_succeeds_under_retry_limit(self):
        """The retry should succeed when contention clears within the
        retry budget. Specifically: 2 contentions, then success →
        resume completes the job normally.
        """
        contention = _make_contention(
            holder_kind=LeaseHolderKind.MESSAGE_JOB.value,
            holder_id="message_job:transient",
        )
        gate = _make_fake_gate(side_effects=[contention, contention, MockMessageResult()])
        manager = _make_manager(gate)

        with patch("daemon.manager.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await manager._resume_processing_background(
                instance_id="inst-recover",
                message="resume",
                message_id=str(uuid.uuid4()),
                old_job_id="job-recover",
                silent=False,
                images=None,
            )

        # 2 retries + 1 success.
        assert gate.run.await_count == 3
        assert mock_sleep.await_count == 2

        # Backoff delays match the schedule: 0.5s then 1.0s.
        sleep_delays = [call.args[0] for call in mock_sleep.await_args_list]
        assert sleep_delays == [
            manager.RESUME_BACKOFF_DELAYS[0],
            manager.RESUME_BACKOFF_DELAYS[1],
        ]

        manager._job_queue_service.complete_job.assert_awaited_once()
        manager.enqueue_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lease_lost_marks_instance_error_and_job_failed(self):
        """When ``_execution_gate.run`` raises ``LeaseLostError``, the
        resume transitions the instance to ``ERROR`` and the job to
        ``FAILED``. No fall-back to ``enqueue_message`` (the gate
        already detected a deeper problem — the row was cleared by
        another process).
        """
        gate = _make_fake_gate(
            raise_after=(LeaseLostError, "lease row cleared by another process")
        )
        manager = _make_manager(gate)

        await manager._resume_processing_background(
            instance_id="inst-lease-lost",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-lease-lost",
            silent=False,
            images=None,
        )

        # Job marked FAILED.
        manager._job_queue_service.complete_job.assert_awaited_once()
        cj_args = manager._job_queue_service.complete_job.await_args.args
        cj_kwargs = manager._job_queue_service.complete_job.await_args.kwargs
        assert cj_args[1] == DemandState.FAILED
        assert "lease" in cj_kwargs["error"].lower()

        # Instance marked ERROR.
        manager._instance_repository.update_instance.assert_called_once()
        ui_kwargs = manager._instance_repository.update_instance.call_args.kwargs
        assert ui_kwargs["status"] == InstanceStatus.ERROR.value

        # No fall-back to enqueue_message on lease loss.
        manager.enqueue_message.assert_not_awaited()

        # _process_message_with_tracking must NOT have been called
        # outside the gate (the gate cancelled it).
        manager._process_message_with_tracking.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lease_lost_swallows_secondary_errors(self):
        """If both ``complete_job`` and ``update_instance`` fail on
        lease loss, the resume should not raise — log warnings instead.
        """
        gate = _make_fake_gate(
            raise_after=(LeaseLostError, "row cleared")
        )
        manager = _make_manager(gate)

        # Make the secondary calls raise — the resume must swallow.
        manager._job_queue_service.complete_job = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        manager._instance_repository.update_instance = MagicMock(
            side_effect=RuntimeError("db down")
        )

        # Must not raise.
        await manager._resume_processing_background(
            instance_id="inst-lease-lost-2",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-lease-lost-2",
            silent=False,
            images=None,
        )

    @pytest.mark.asyncio
    async def test_gate_uses_message_job_kind_not_resume(self):
        """Per the planning doc, resume reuses ``LeaseHolderKind.MESSAGE_JOB``
        — no new enum value is added. The test pins this so a future
        refactor that adds a ``RESUME`` kind is caught here.
        """
        gate = _make_fake_gate(side_effects=[MockMessageResult()])
        manager = _make_manager(gate)

        await manager._resume_processing_background(
            instance_id="inst-kind",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-kind",
            silent=False,
            images=None,
        )

        kwargs = gate.run.await_args.kwargs
        assert kwargs["holder_kind"] == LeaseHolderKind.MESSAGE_JOB.value
        # The holder_id format is ``resume:<message_id>``.
        assert kwargs["holder_id"].startswith("resume:")

    @pytest.mark.asyncio
    async def test_other_exception_inside_gate_propagates_to_error_handler(self):
        """If the gate raises an exception that is NOT ``LeaseLostError``,
        it must propagate to the existing error handler (job FAILED,
        instance ERROR). The race-fix should not break the existing
        error path.
        """
        gate = _make_fake_gate(
            raise_after=(RuntimeError, "boom")
        )
        manager = _make_manager(gate)

        # Should not raise — the existing except Exception block
        # catches and marks the job FAILED.
        await manager._resume_processing_background(
            instance_id="inst-other-err",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-other-err",
            silent=False,
            images=None,
        )

        manager._job_queue_service.complete_job.assert_awaited_once()
        cj_args = manager._job_queue_service.complete_job.await_args.args
        assert cj_args[1] == DemandState.FAILED

        manager._instance_repository.update_instance.assert_called_once()
        ui_kwargs = manager._instance_repository.update_instance.call_args.kwargs
        assert ui_kwargs["status"] == InstanceStatus.ERROR.value


class TestResumeGateIntegration:
    """Integration-style tests using a real ``ExecutionGateService``
    backed by an in-memory SQLite lease table, to verify the resume
    path actually contends when another holder owns the lease.
    """

    @pytest.fixture
    def real_gate(self):
        """Build a real ``ExecutionGateService`` over an in-memory DB."""
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlmodel import SQLModel

        from daemon.repositories.execution_lease.repository import (
            ExecutionLeaseRepository,
        )
        from daemon.services.execution_gate import ExecutionGateService

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        lease_repo = ExecutionLeaseRepository(engine)
        yield ExecutionGateService(lease_repo=lease_repo)
        engine.dispose()

    @pytest.mark.asyncio
    async def test_real_gate_blocks_resume_while_message_job_holds_lease(
        self, real_gate
    ):
        """Use a real ``ExecutionGateService`` to verify the resume
        path actually sees ``LeaseContention`` when another dispatcher
        holds the lease.

        Step 1: Acquire the lease as a MESSAGE job (simulating a
                concurrent /api message dispatch).
        Step 2: Call ``_resume_processing_background`` — it should see
                ``LeaseContention`` (the MESSAGE job is the holder).
        Step 3: Release the MESSAGE job's lease.
        Step 4: The resume's retry should now succeed.

        We patch ``asyncio.sleep`` so the backoff is instant.
        """
        manager = _make_manager(real_gate)
        instance_id = "inst-real-contend"
        message_id = str(uuid.uuid4())
        old_job_id = "job-real-contend"

        # 1. Another dispatcher holds the lease.
        acquired = await asyncio.to_thread(
            real_gate._lease_repo.try_acquire,
            instance_id,
            f"message_job:other-job-1",
            LeaseHolderKind.MESSAGE_JOB.value,
        )
        assert acquired is True

        # 2+3. Schedule the resume; after the resume's first
        # LeaseContention (which sleeps), release the holding lease so
        # the retry succeeds.
        async def release_after_delay():
            await asyncio.sleep(0.01)
            await asyncio.to_thread(
                real_gate._lease_repo.release,
                instance_id,
                "message_job:other-job-1",
            )

        release_task = asyncio.create_task(release_after_delay())
        try:
            with patch("daemon.manager.asyncio.sleep", new=AsyncMock()):
                await manager._resume_processing_background(
                    instance_id=instance_id,
                    message="resume",
                    message_id=message_id,
                    old_job_id=old_job_id,
                    silent=False,
                    images=None,
                )
        finally:
            await release_task

        # 4. The resume completed (job COMPLETED), the lease was
        # released cleanly.
        manager._job_queue_service.complete_job.assert_awaited_once()
        cj_args = manager._job_queue_service.complete_job.await_args.args
        assert cj_args[1] == DemandState.COMPLETED

        # Lease is free after the resume finishes.
        holder = await asyncio.to_thread(
            real_gate._lease_repo.get_holder, instance_id
        )
        assert holder is None

    @pytest.mark.asyncio
    async def test_real_gate_workerpool_task_blocks_resume(self, real_gate):
        """Same as above but the holder is a TASK lease (WorkerPool path)."""
        manager = _make_manager(real_gate)
        instance_id = "inst-real-task"
        message_id = str(uuid.uuid4())
        old_job_id = "job-real-task"

        # 1. WorkerPool task holds the lease.
        acquired = await asyncio.to_thread(
            real_gate._lease_repo.try_acquire,
            instance_id,
            f"task:99",
            LeaseHolderKind.TASK.value,
        )
        assert acquired is True

        async def release_after_delay():
            await asyncio.sleep(0.01)
            await asyncio.to_thread(
                real_gate._lease_repo.release, instance_id, "task:99"
            )

        release_task = asyncio.create_task(release_after_delay())
        try:
            with patch("daemon.manager.asyncio.sleep", new=AsyncMock()):
                await manager._resume_processing_background(
                    instance_id=instance_id,
                    message="resume",
                    message_id=message_id,
                    old_job_id=old_job_id,
                    silent=False,
                    images=None,
                )
        finally:
            await release_task

        manager._job_queue_service.complete_job.assert_awaited_once()
        cj_args = manager._job_queue_service.complete_job.await_args.args
        assert cj_args[1] == DemandState.COMPLETED


class TestResumeGraphTaskTracking:
    """Verify the resume still registers in ``_graph_tasks`` so
    ``pause_instance_cascade`` can find / cancel it.
    """

    @pytest.mark.asyncio
    async def test_resume_processing_job_still_tracks_graph_task(self):
        """The caller (``resume_processing_job``) stores the asyncio
        task in ``_graph_tasks[instance_id]``. The gate wrapping is
        inside the background task, so the tracking should be
        unaffected — this test pins that behaviour.
        """
        gate = _make_fake_gate(
            side_effects=[LeaseContention(
                reason=LeaseContentionReason.HELD_BY_OTHER,
                holder_id="task:1",
                holder_kind=LeaseHolderKind.TASK.value,
            ), MockMessageResult()]
        )
        manager = _make_manager(gate)

        # Simulate the outer call: store a task in _graph_tasks.
        async def _slow_background():
            await manager._resume_processing_background(
                instance_id="inst-track",
                message="resume",
                message_id=str(uuid.uuid4()),
                old_job_id="job-track",
                silent=False,
                images=None,
            )

        bg_task = asyncio.create_task(_slow_background())
        manager._graph_tasks["inst-track"] = bg_task
        try:
            with patch("daemon.manager.asyncio.sleep", new=AsyncMock()):
                await bg_task
        finally:
            manager._graph_tasks.pop("inst-track", None)

        # The outer task is still tracked while running, and removed
        # by the caller. We just verify the task ran to completion.
        assert bg_task.done()
        manager._job_queue_service.complete_job.assert_awaited_once()
