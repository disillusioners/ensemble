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
from daemon.cancellation import CancellationTokenSource
from daemon.repositories.instance.models import InstanceStatus
from daemon.services.execution_gate import (
    LeaseContention,
    LeaseContentionReason,
    LeaseHolderKind,
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

    # Real CancellationTokenSource so we can verify the token
    # threaded through ``_process_message_with_tracking`` is the
    # same instance the registry returned. ``register`` returns the
    # source; ``unregister`` is a no-op mock.
    request_registry = MagicMock()
    request_registry.register = MagicMock(
        return_value=CancellationTokenSource()
    )
    request_registry.unregister = MagicMock()

    manager = InstanceManager.__new__(InstanceManager)
    manager._job_queue_service = job_queue_service
    manager._instance_repository = instance_repository
    manager._execution_gate = gate
    manager._request_registry = request_registry
    manager._process_message_with_tracking = AsyncMock(
        return_value=MockMessageResult()
    )
    manager._process_child_completion_and_notify_parent = AsyncMock()
    manager.enqueue_message = AsyncMock(return_value=MockAsyncMessageResult())
    manager._graph_tasks = {}
    # A9: wire a minimal ``config`` so the legacy ``waiting_for`` gate
    # in the resume path can read ``use_legacy_waiting_for_cascade``.
    # The test exercises the resume path's gate-wrapping logic, not
    # the completion cascade — so we set the kill switch ON to
    # authorize the legacy ``SELECT waiting_for`` fallback that the
    # resume path uses when CM is None. Without this, the A9 hard
    # error fires (CM=None + flag=OFF is an invalid state per
    # ADR-011).
    manager.config = MagicMock()
    manager.config.job_system = MagicMock()
    manager.config.job_system.use_legacy_waiting_for_cascade = True
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
        - W2: ``complete_job`` is called on the ``old_job_id`` with
          ``DemandState.CANCELLED`` so the original PROCESSING job does
          not sit orphaned. The error message identifies the source
          (``resume_exhausted fallback``).
        - ``enqueue_message`` is called exactly once with
          ``source="resume_exhausted"``.
        - The instance is NOT marked ERROR (we recovered via enqueue).
        - W1: ``enqueue_message`` carries ``resume_mode: True`` in the
          metadata so the LLM treats the message as a checkpoint
          resume, not a fresh prompt.
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

        # W2: old_job_id is marked CANCELLED before the fallback enqueue.
        # ``complete_job`` is awaited once with the right args.
        manager._job_queue_service.complete_job.assert_awaited_once()
        cj_args = manager._job_queue_service.complete_job.await_args.args
        cj_kwargs = manager._job_queue_service.complete_job.await_args.kwargs
        assert cj_args[0] == "job-exhaust"
        assert cj_args[1] == DemandState.CANCELLED
        assert "resume_exhausted" in cj_kwargs["error"].lower()

        # Fallback: enqueue_message called once with source=resume_exhausted.
        manager.enqueue_message.assert_awaited_once()
        em_kwargs = manager.enqueue_message.await_args.kwargs
        assert em_kwargs["instance_id"] == "inst-exhaust"
        assert em_kwargs["message"] == "resume"
        assert em_kwargs["source"] == "resume_exhausted"
        # W1: resume_mode metadata must be present so the LLM path
        # treats this as a checkpoint resume (not a fresh prompt).
        assert em_kwargs["metadata"] == {"resume_mode": True, "silent": False}

        # Instance NOT marked ERROR (we recovered via enqueue).
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


class TestResumeCleanupAndCancellation:
    """Verify the resume path's per-instance cleanup (W3) and
    cancellation-token threading (W4) work end-to-end.

    W3: ``_graph_tasks[instance_id]`` is popped in the outermost
    ``finally`` block so the entry does not leak across fallback,
    lease-lost, exception, or normal completion paths.

    W4: A ``CancellationToken`` passed to
    ``_resume_processing_background`` is propagated to
    ``_process_message_with_tracking`` so ``pause_instance_cascade``
    can cooperatively interrupt LLM streaming via the token rather
    than abruptly via ``task.cancel()``. The message_id is
    unregistered from ``_request_registry`` in the finally block.
    """

    @pytest.mark.asyncio
    async def test_graph_tasks_entry_popped_after_fallback(self):
        """W3: After the fallback path (lease contention exhausted),
        ``_graph_tasks[instance_id]`` is popped in the outermost
        finally. Without this, the next resume call would
        short-circuit to ``"already_resuming"`` because the previous
        task entry would still be present (and not done).
        """
        contention = _make_contention(
            holder_kind=LeaseHolderKind.TASK.value,
            holder_id="task:stuck",
        )
        # 4 contentions: 3 retries + the final one that triggers fallback.
        gate = _make_fake_gate(side_effects=[contention] * 4)
        manager = _make_manager(gate)
        manager._graph_tasks["inst-cleanup-fb"] = "sentinel"

        with patch("daemon.manager.asyncio.sleep", new=AsyncMock()):
            await manager._resume_processing_background(
                instance_id="inst-cleanup-fb",
                message="resume",
                message_id=str(uuid.uuid4()),
                old_job_id="job-cleanup-fb",
                silent=False,
                images=None,
            )

        assert "inst-cleanup-fb" not in manager._graph_tasks

    @pytest.mark.asyncio
    async def test_graph_tasks_entry_popped_on_happy_path(self):
        """W3: cleanup also runs on the happy path (lease free, resume
        completes successfully). The pre-existing tests verify the
        processing logic but did not pin this — the outer call
        previously relied on ``pause_instance_cascade`` to pop the
        entry, which left a window where stale entries blocked the
        next resume.
        """
        gate = _make_fake_gate(side_effects=[MockMessageResult()])
        manager = _make_manager(gate)
        manager._graph_tasks["inst-cleanup-happy"] = "sentinel"

        await manager._resume_processing_background(
            instance_id="inst-cleanup-happy",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-cleanup-happy",
            silent=False,
            images=None,
        )

        assert "inst-cleanup-happy" not in manager._graph_tasks

    @pytest.mark.asyncio
    async def test_graph_tasks_entry_popped_on_lease_lost(self):
        """W3: cleanup also runs after ``LeaseLostError`` — a fatal
        path that previously left the ``_graph_tasks`` entry behind.
        """
        gate = _make_fake_gate(
            raise_after=(LeaseLostError, "row cleared by another process")
        )
        manager = _make_manager(gate)
        manager._graph_tasks["inst-cleanup-ll"] = "sentinel"

        await manager._resume_processing_background(
            instance_id="inst-cleanup-ll",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-cleanup-ll",
            silent=False,
            images=None,
        )

        assert "inst-cleanup-ll" not in manager._graph_tasks

    @pytest.mark.asyncio
    async def test_graph_tasks_entry_popped_after_retry_chain(self):
        """W3: when contention triggers retry recursion, the
        intermediate (recursive) calls' finally blocks are no-ops
        (``_retry_attempt != 0``); only the outermost call's finally
        actually pops the entry. After the full retry chain resolves
        (whether success or fallback), the entry is gone exactly
        once.
        """
        contention = _make_contention(
            holder_kind=LeaseHolderKind.TASK.value,
            holder_id="task:retry",
        )
        # 1 contention → 1 retry → success on the second attempt.
        gate = _make_fake_gate(side_effects=[contention, MockMessageResult()])
        manager = _make_manager(gate)
        manager._graph_tasks["inst-cleanup-retry"] = "sentinel"

        with patch("daemon.manager.asyncio.sleep", new=AsyncMock()):
            await manager._resume_processing_background(
                instance_id="inst-cleanup-retry",
                message="resume",
                message_id=str(uuid.uuid4()),
                old_job_id="job-cleanup-retry",
                silent=False,
                images=None,
            )

        # Popped exactly once by the outermost finally; the recursive
        # call's finally is a no-op so the entry survives the recursion
        # and is only removed by the outermost cleanup.
        assert "inst-cleanup-retry" not in manager._graph_tasks

    @pytest.mark.asyncio
    async def test_cancellation_token_passed_to_process_message_with_tracking(self):
        """W4: a ``CancellationToken`` passed to
        ``_resume_processing_background`` is propagated by identity to
        ``_process_message_with_tracking`` so the LLM streaming
        callback can raise ``OperationCancelledError`` cooperatively
        on pause.

        Unlike the gate-wrapping tests above (which use a fake
        ``gate.run`` that returns a ``MockMessageResult`` without
        invoking ``work_fn``), this test uses a gate that actually
        calls ``work_fn`` so the closure body — where the
        ``cancellation_token`` is passed to
        ``_process_message_with_tracking`` — runs for real.
        """
        gate = MagicMock()

        async def gate_run(instance_id, holder_id, holder_kind, work_fn):
            # Invoke the closure; the gate then propagates the return value.
            return await work_fn()

        gate.run = gate_run
        manager = _make_manager(gate)

        cts = CancellationTokenSource()
        token = cts.token

        await manager._resume_processing_background(
            instance_id="inst-ct",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-ct",
            silent=False,
            images=None,
            cancellation_token=token,
        )

        manager._process_message_with_tracking.assert_awaited_once()
        kwargs = manager._process_message_with_tracking.await_args.kwargs
        # Identity check (``is``): the exact same token object must
        # be threaded through so cancelling it propagates to the
        # streaming callback.
        assert kwargs["cancellation_token"] is token

    @pytest.mark.asyncio
    async def test_cancellation_token_default_is_none(self):
        """W4: when ``_resume_processing_background`` is called
        without a token (legacy callers that have not been migrated),
        ``_process_message_with_tracking`` receives ``None``. The
        default-parameter contract is preserved.

        Same gate shape as ``test_cancellation_token_passed_to_...``
        — invoke ``work_fn`` so the closure body runs and the
        default ``cancellation_token=None`` is observable.
        """
        gate = MagicMock()

        async def gate_run(instance_id, holder_id, holder_kind, work_fn):
            return await work_fn()

        gate.run = gate_run
        manager = _make_manager(gate)

        await manager._resume_processing_background(
            instance_id="inst-no-ct",
            message="resume",
            message_id=str(uuid.uuid4()),
            old_job_id="job-no-ct",
            silent=False,
            images=None,
        )

        manager._process_message_with_tracking.assert_awaited_once()
        kwargs = manager._process_message_with_tracking.await_args.kwargs
        assert kwargs["cancellation_token"] is None

    @pytest.mark.asyncio
    async def test_request_registry_unregister_called_in_finally(self):
        """W4: the outermost finally block calls
        ``_request_registry.unregister(message_id)`` so the CTS that
        ``resume_processing_job`` registered is released. This test
        verifies the contract directly: any caller passing a
        ``message_id`` should see ``unregister`` invoked exactly once
        in the finally block on every exit path (success, exception,
        fallback, lease-lost).
        """
        gate = _make_fake_gate(side_effects=[MockMessageResult()])
        manager = _make_manager(gate)

        message_id = str(uuid.uuid4())
        await manager._resume_processing_background(
            instance_id="inst-unreg",
            message="resume",
            message_id=message_id,
            old_job_id="job-unreg",
            silent=False,
            images=None,
        )

        manager._request_registry.unregister.assert_called_once_with(message_id)

    @pytest.mark.asyncio
    async def test_request_registry_unregister_called_on_fallback(self):
        """W4: ``unregister`` is called on the fallback path too
        (lease contention exhausted). Without this, the CTS would
        leak until the registry's garbage collection picks it up.
        """
        contention = _make_contention(
            holder_kind=LeaseHolderKind.TASK.value,
            holder_id="task:fb",
        )
        gate = _make_fake_gate(side_effects=[contention] * 4)
        manager = _make_manager(gate)

        message_id = str(uuid.uuid4())
        with patch("daemon.manager.asyncio.sleep", new=AsyncMock()):
            await manager._resume_processing_background(
                instance_id="inst-unreg-fb",
                message="resume",
                message_id=message_id,
                old_job_id="job-unreg-fb",
                silent=False,
                images=None,
            )

        manager._request_registry.unregister.assert_called_once_with(message_id)
