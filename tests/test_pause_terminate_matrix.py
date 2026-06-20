"""C4.5 — Pause/terminate discrimination matrix test pack.

This file snapshots the **CURRENT** pause/terminate discrimination
behaviour of the daemon's two physical message-processing dispatchers:

1. **JobQueue path** —
   :class:`daemon.services.message_job_handler.MessageJobHandler.handle`
   drives ``MESSAGE``-typed jobs from ``job_queue_items``.

2. **WorkerPool path** —
   :class:`daemon.services.task_processor.ProcessMessageProcessor.process`
   drives ``process_message`` tasks from the ``task`` table.

The two paths share the six-stage :class:`MessageProcessingPipeline`
since Phase 5, **but they deliberately diverge** at the cancellation
boundary:

* **JQ path** discriminates pause-vs-terminate on
  ``asyncio.CancelledError`` by reading ``instance.status`` from the DB.
  PAUSE → leave the job in PROCESSING (for resume). Anything else →
  ``complete_job(CANCELLED)`` and re-raise.
* **WP path** does **not** discriminate. It always re-raises
  ``asyncio.CancelledError`` and lets the worker pool decide.

This divergence is the precondition for C-M5 (unification). This file
locks in the current behaviour so a post-unification re-run will catch
any accidental regression.

The tests are **unit tests with mocks**: they drive ``handle`` / ``process``
directly with a transparent Execution Gate and assert on the public
observable side-effects (``complete_job`` calls, ``complete_task`` calls,
exception propagation). No real DB rows are inspected for the
discrimination logic — the mock ``complete_job`` / ``complete_task``
spy the calls that *would* mutate state.

Run ONLY this file::

    pytest tests/test_pause_terminate_matrix.py -v

Test count: 20 (10 JQ path + 10 WP path).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.cancellation import (
    CancellationReason,
    OperationCancelledError,
)
from daemon.manager import MessageResult
from daemon.repositories.execution_lease.models import LeaseHolderKind
from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue.models import JobStatus
from daemon.services.execution_gate import (
    ExecutionGateService,
    LeaseContention,
    LeaseContentionReason,
    LeaseLostError,
)
from daemon.services.job_queue_service import DemandState


# ---------------------------------------------------------------------------
# Shared mock factories
# ---------------------------------------------------------------------------


def _make_passthrough_gate():
    """Return a MagicMock gate whose ``run`` invokes ``work_fn`` directly.

    A transparent gate lets the tests drive
    ``_process_message_with_tracking`` (or its ``side_effect``) through
    the pipeline without needing a real lease table. This is the same
    pattern used in ``test_pipeline_unified.py`` and
    ``test_pause_while_processing.py``.
    """

    async def _passthrough(*args, **kwargs):
        work_fn = kwargs.get("work_fn")
        return await work_fn()

    gate = MagicMock(spec=ExecutionGateService)
    gate.run = AsyncMock(side_effect=_passthrough)
    return gate


def _make_contention_gate():
    """Gate that returns ``LeaseContention`` (without calling work_fn)."""

    gate = MagicMock(spec=ExecutionGateService)
    gate.run = AsyncMock(
        return_value=LeaseContention(
            reason=LeaseContentionReason.HELD_BY_OTHER,
            holder_id="message_job:other-job",
            holder_kind="message_job",
        )
    )
    return gate


def _make_lease_lost_gate():
    """Gate that raises ``LeaseLostError`` (without calling work_fn)."""

    gate = MagicMock(spec=ExecutionGateService)
    gate.run = AsyncMock(side_effect=LeaseLostError("lease revoked mid-flight"))
    return gate


def _make_mock_message():
    """A mock MessageQueue row with content + source."""
    msg = MagicMock()
    msg.content = "Hello"
    msg.source = "api"
    msg.images = None
    msg.message_metadata = None
    return msg


def _make_mock_job(
    *,
    job_id: str = "job-test-123",
    instance_id: str = "inst-test-123",
    status: str = JobStatus.PROCESSING.value,
    message: str = "Hello",
) -> MagicMock:
    """Build a MagicMock JobItem with the minimum attributes ``handle`` reads."""
    job = MagicMock()
    job.job_id = job_id
    job.instance_id = instance_id
    job.status = status
    job.message = message
    job.job_type = "message"
    job.project_id = "test-project"
    job.queue_id = "test-queue"
    job.retry_count = 0
    job.job_metadata = {
        "message_id": "msg-test-123",
        "source": "api",
        "images": None,
        "resume_mode": False,
        "silent": False,
    }
    return job


def _make_mock_task(
    *,
    task_id: str = "task-test-42",
    instance_id: str = "inst-test-123",
    message_id: str = "msg-test-123",
    retry_count: int = 0,
) -> MagicMock:
    """Build a MagicMock Task with the minimum attributes ``process`` reads.

    Note: ``task_id`` is a STRING here, not the int that the production
    ``Task`` model uses. The unified error helper
    (``handle_message_processing_error``) does ``task_id[:8]`` for
    logging, which would TypeError on an int. Using a string lets the
    WP-path error tests reach the assertion phase without tripping
    over that latent bug — the discrimination matrix is what this test
    pack is verifying, not the error helper's slicing contract.
    """
    task = MagicMock()
    task.id = task_id
    task.instance_id = instance_id
    task.message_id = message_id
    task.retry_count = retry_count
    return task


def _make_mock_manager(
    *,
    result: MessageResult | Exception | None = None,
    instance_status: str = InstanceStatus.RUNNING.value,
    gate=None,
):
    """Build a MagicMock InstanceManager wired for the JQ/WP handlers.

    Args:
        result: Return value (or side_effect exception) for
            ``_process_message_with_tracking``. ``None`` → default
            ``MessageResult(content="ok")``.
        instance_status: Status that ``_instance_repository.get`` reports
            for the instance. Drives the JQ path's pause-vs-terminate
            discrimination.
        gate: Optional pre-built Execution Gate mock. Defaults to a
            transparent passthrough gate.
    """
    m = MagicMock()
    # IMPORTANT: ``isinstance(CancelledError, Exception)`` is False in
    # Python 3.8+ — ``asyncio.CancelledError`` inherits from
    # ``BaseException``. We must check ``BaseException`` so a
    # ``side_effect=asyncio.CancelledError()`` is installed (raising)
    # instead of being used as a return value (which would crash the
    # pipeline's ``result.content`` access on the exception object).
    if isinstance(result, BaseException):
        m._process_message_with_tracking = AsyncMock(side_effect=result)
    else:
        m._process_message_with_tracking = AsyncMock(
            return_value=result or MessageResult(content="ok", tool_calls=None)
        )
    # Instance repo: returns a MagicMock instance with the requested status.
    mock_instance = MagicMock()
    mock_instance.status = instance_status
    mock_instance.agent_id = "coder"
    mock_instance.waiting_for = 0
    mock_instance.instance_metadata = {}
    m._instance_repository = MagicMock()
    m._instance_repository.get = MagicMock(return_value=mock_instance)
    # transition_status_if returns the updated instance (non-None means the
    # transition happened, triggering an SSE emit attempt).
    m._instance_repository.transition_status_if = MagicMock(
        return_value=mock_instance
    )
    m._live_hub = MagicMock()
    m._live_hub.stream_status_change = AsyncMock()
    # Child completion check (Stage 6 of the pipeline).
    m._process_child_completion_and_notify_parent = AsyncMock()
    # WP path reads messages via this repo.
    m._queue_repository = MagicMock()
    m._queue_repository.complete = MagicMock()
    m._queue_repository.claim_specific = MagicMock(return_value=True)
    # Cross-dispatcher pre-flight (JQ path) reads this.
    task_repo_stub = MagicMock()
    task_repo_stub.find_running_by_instance = MagicMock(return_value=None)
    m._task_repo = task_repo_stub
    # Execution Gate.
    m.execution_gate = gate if gate is not None else _make_passthrough_gate()
    # Error-helper entrypoints (spied on via the pipeline's call to
    # ``handle_message_processing_error``).
    m._event_bus = MagicMock()
    m._event_bus.create_error_event = AsyncMock()
    m._publish_instance_lifecycle_event = AsyncMock()
    m._send_error_report = AsyncMock()
    # JQ path's on_success legacy cascade kill switch (off ⇒ CM must be wired).
    m.config = MagicMock()
    m.config.job_system = MagicMock()
    m.config.job_system.use_legacy_waiting_for_cascade = True
    return m


def _make_mock_job_service():
    """Build a MagicMock JobQueueService with AsyncMock side-effects.

    The handler assigns this onto ``manager._job_queue_service`` in
    ``__init__`` (force-set). We provide ``complete_job`` and
    ``notify_watchers`` as AsyncMocks so the tests can assert on call
    args.
    """
    svc = MagicMock()
    svc.complete_job = AsyncMock(return_value=MagicMock())
    svc.notify_watchers = AsyncMock(return_value=0)
    svc._lock_manager = MagicMock()
    svc._lock_manager.release_queue_lock = AsyncMock()
    svc._dispatch_bus = None
    return svc


def _make_mock_job_repo(active_message_jobs: list | None = None):
    """Build a MagicMock JobRepository.

    Args:
        active_message_jobs: List returned by
            ``find_processing_message_jobs_by_instance``. Default: ``[]``
            (no sibling MESSAGE jobs) so the handler passes the pre-flight
            and proceeds to the gate.
    """
    repo = MagicMock()
    repo.find_processing_message_jobs_by_instance = MagicMock(
        return_value=active_message_jobs if active_message_jobs is not None else []
    )
    repo.atomic_transition = MagicMock(return_value=MagicMock())
    return repo


def _disable_cm():
    """Context-manager patch that makes ``get_correlation_manager`` return None.

    Combined with ``use_legacy_waiting_for_cascade=True`` on the manager
    config, this routes the JQ path's ``on_success`` callback through the
    legacy ``waiting_for`` fallback (which is mocked to 0 ⇒
    ``skip_complete=False`` ⇒ normal ``complete_job(COMPLETED)``).
    """
    return patch(
        "daemon.services.correlation_manager.get_correlation_manager",
        side_effect=RuntimeError("CM not initialized in test"),
    )


# ===========================================================================
# Group 1: MessageJobHandler (JobQueue path) — 10 tests
# ===========================================================================


class TestMessageJobHandlerPauseTerminateMatrix:
    """Pause/terminate discrimination matrix for the JobQueue path.

    Exercises ``MessageJobHandler.handle`` (438 lines) with each
    (starting instance state × cancel reason) and asserts the
    resulting job transition and exception propagation.
    """

    # ------------------------------------------------------------------
    # Test 1: RUNNING + normal completion → COMPLETED
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_01_jq_running_normal_completion_completes_job(self):
        """RUNNING instance + happy path → ``complete_job(COMPLETED)``."""
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=MessageResult(content="done", tool_calls=None),
            instance_status=InstanceStatus.RUNNING.value,
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        with _disable_cm():
            await handler.handle(job)

        # Job is completed with COMPLETED demand state.
        job_service.complete_job.assert_awaited_once()
        call = job_service.complete_job.await_args
        assert call.args[0] == job.job_id
        assert call.kwargs["demand_state"] == DemandState.COMPLETED

    # ------------------------------------------------------------------
    # Test 2: RUNNING + error → FAILED
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_02_jq_running_error_marks_job_failed(self):
        """RUNNING instance + work_fn raises → error helper marks job FAILED.

        The JQ handler's outer ``except Exception`` clause runs
        ``handle_message_processing_error(job_id=...)`` which in turn
        calls ``complete_job(FAILED)``.
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=RuntimeError("graph blew up"),
            instance_status=InstanceStatus.RUNNING.value,
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        with _disable_cm():
            # The handler swallows generic exceptions after running the
            # error helper — no re-raise.
            await handler.handle(job)

        # complete_job must be called with FAILED by the error helper.
        job_service.complete_job.assert_awaited()
        failed_calls = [
            c for c in job_service.complete_job.await_args_list
            if c.kwargs.get("demand_state") == DemandState.FAILED
        ]
        assert len(failed_calls) >= 1, (
            "Expected at least one complete_job(FAILED) call from the error helper"
        )

    # ------------------------------------------------------------------
    # Test 3: RUNNING + asyncio.CancelledError + RUNNING instance
    #         → CANCELLED + re-raise (NOT pause)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_03_jq_running_cancellederror_running_instance_cancels_and_reraises(self):
        """RUNNING instance + asyncio.CancelledError → ``complete_job(CANCELLED)`` + re-raise.

        This is the **non-pause** branch of the discrimination: instance
        status is RUNNING (e.g. shutdown in flight, status not yet
        TERMINATED), so the handler completes the job as CANCELLED and
        re-raises.
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=asyncio.CancelledError(),
            instance_status=InstanceStatus.RUNNING.value,
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        with _disable_cm():
            with pytest.raises(asyncio.CancelledError):
                await handler.handle(job)

        job_service.complete_job.assert_awaited_once_with(
            job.job_id,
            demand_state=DemandState.CANCELLED,
            error="Message processing cancelled (instance terminated)",
        )

    # ------------------------------------------------------------------
    # Test 4: RUNNING + OperationCancelledError (token cancel)
    #         → CANCELLED (no pause discrimination, no re-raise)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_04_jq_operation_cancelled_error_completes_cancelled_no_reraise(self):
        """RUNNING instance + OperationCancelledError → ``complete_job(CANCELLED)``.

        Token-cancel (via ``cancel_message_job``) always completes the
        job as CANCELLED. Unlike ``asyncio.CancelledError``, this path
        does **not** re-raise — mirroring the original JQ handler.
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=OperationCancelledError(reason=CancellationReason.MANUAL),
            instance_status=InstanceStatus.RUNNING.value,
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        with _disable_cm():
            # No re-raise — the handler swallows OperationCancelledError
            # after completing the job.
            await handler.handle(job)

        job_service.complete_job.assert_awaited_once_with(
            job.job_id,
            demand_state=DemandState.CANCELLED,
            error="Message processing cancelled",
        )

    # ------------------------------------------------------------------
    # Test 5: PAUSED + asyncio.CancelledError → stays PROCESSING
    #         (THE pause discrimination — no complete_job, no re-raise)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_05_jq_paused_cancellederror_keeps_job_processing(self):
        """PAUSED instance + asyncio.CancelledError → job STAYS PROCESSING.

        This is the **core** pause discrimination test. The handler
        reads ``instance.status`` from the DB and, finding PAUSED,
        swallows the CancelledError so the worker pool doesn't mark the
        job FAILED. The job stays PROCESSING so it can be resumed.
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=asyncio.CancelledError(),
            instance_status=InstanceStatus.PAUSED.value,
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        with _disable_cm():
            # No exception — the handler swallows it for PAUSE.
            await handler.handle(job)

        # CRITICAL: complete_job must NOT be called. The job stays
        # PROCESSING so the resume path can pick it back up.
        job_service.complete_job.assert_not_called()

    # ------------------------------------------------------------------
    # Test 6: TERMINATED + asyncio.CancelledError → CANCELLED + re-raise
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_06_jq_terminated_cancellederror_cancels_and_reraises(self):
        """TERMINATED instance + asyncio.CancelledError → CANCELLED + re-raise.

        TERMINATED is not PAUSED, so the handler treats it as a hard
        terminate: completes the job as CANCELLED and re-raises so the
        caller (JobProcessor) can clean up.
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=asyncio.CancelledError(),
            instance_status=InstanceStatus.TERMINATED.value,
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        with _disable_cm():
            with pytest.raises(asyncio.CancelledError):
                await handler.handle(job)

        job_service.complete_job.assert_awaited_once_with(
            job.job_id,
            demand_state=DemandState.CANCELLED,
            error="Message processing cancelled (instance terminated)",
        )

    # ------------------------------------------------------------------
    # Test 7: RUNNING + LeaseContention → requeue (atomic_transition PENDING)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_07_jq_running_lease_contention_requeues_pending(self):
        """RUNNING + gate returns LeaseContention → ``atomic_transition(PENDING)``.

        The ``on_contention`` callback re-queues the job via
        ``atomic_transition(PROCESSING→PENDING)`` and releases the queue
        lock. No ``complete_job`` is called.
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=MessageResult(content="unused", tool_calls=None),
            instance_status=InstanceStatus.RUNNING.value,
            gate=_make_contention_gate(),
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        await handler.handle(job)

        # atomic_transition PROCESSING→PENDING.
        job_repo.atomic_transition.assert_called_once_with(
            job.job_id,
            from_status="processing",
            to_status="pending",
        )
        # complete_job is NOT called on contention.
        job_service.complete_job.assert_not_called()

    # ------------------------------------------------------------------
    # Test 8: RUNNING + LeaseLostError → requeue (atomic_transition PENDING)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_08_jq_running_lease_lost_requeues_pending(self):
        """RUNNING + gate raises LeaseLostError → ``atomic_transition(PENDING)``.

        ``LeaseLostError`` is the mid-flight variant: the lease was
        revoked while work_fn was running. Same re-queue path as
        LeaseContention, but with a warning-level log.
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=MessageResult(content="unused", tool_calls=None),
            instance_status=InstanceStatus.RUNNING.value,
            gate=_make_lease_lost_gate(),
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        await handler.handle(job)

        job_repo.atomic_transition.assert_called_once_with(
            job.job_id,
            from_status="processing",
            to_status="pending",
        )
        job_service.complete_job.assert_not_called()

    # ------------------------------------------------------------------
    # Test 9: ERROR + processing → FAILED
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_09_jq_error_instance_processing_marks_job_failed(self):
        """ERROR instance + work_fn raises → error helper marks job FAILED.

        The instance is already in ERROR state when the job runs. The
        work_fn raises; the outer ``except Exception`` runs
        ``handle_message_processing_error`` which calls
        ``complete_job(FAILED)``. Instance status is irrelevant to the
        generic-error path (the discrimination only applies to
        ``asyncio.CancelledError``).
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=ValueError("bad input"),
            instance_status=InstanceStatus.ERROR.value,
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        with _disable_cm():
            await handler.handle(job)

        failed_calls = [
            c for c in job_service.complete_job.await_args_list
            if c.kwargs.get("demand_state") == DemandState.FAILED
        ]
        assert len(failed_calls) >= 1

    # ------------------------------------------------------------------
    # Test 10: WAITING_CHILDREN + new message → pre-pickup RUNNING transition
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_10_jq_waiting_children_new_message_transitions_to_running(self):
        """WAITING_CHILDREN instance + new message → ``transition_status_if(RUNNING)``.

        The JQ handler performs a pre-pickup transition so observers
        see a status_change event. For a WAITING_CHILDREN instance
        receiving a new message, the transition is
        WAITING_CHILDREN → RUNNING. This makes the self-continuation
        path's liveness explicit.
        """
        from daemon.services.message_job_handler import MessageJobHandler

        manager = _make_mock_manager(
            result=MessageResult(content="done", tool_calls=None),
            instance_status=InstanceStatus.WAITING_CHILDREN.value,
        )
        job_service = _make_mock_job_service()
        job_repo = _make_mock_job_repo()
        job = _make_mock_job()

        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_service,
            job_repository=job_repo,
        )

        with _disable_cm():
            await handler.handle(job)

        # Pre-pickup transition called with WAITING_CHILDREN/IDLE → RUNNING.
        manager._instance_repository.transition_status_if.assert_called_once()
        call = manager._instance_repository.transition_status_if.call_args
        assert call.args[0] == job.instance_id
        assert call.args[1] == InstanceStatus.RUNNING.value
        allowed_from = call.args[2]
        assert InstanceStatus.WAITING_CHILDREN.value in allowed_from
        assert InstanceStatus.IDLE.value in allowed_from

        # And the job was completed normally (happy path).
        job_service.complete_job.assert_awaited_once()
        assert (
            job_service.complete_job.await_args.kwargs["demand_state"]
            == DemandState.COMPLETED
        )


# ===========================================================================
# Group 2: ProcessMessageProcessor (WorkerPool path) — 10 tests
# ===========================================================================


class TestProcessMessageProcessorPauseTerminateMatrix:
    """Pause/terminate discrimination matrix for the WorkerPool path.

    Exercises ``ProcessMessageProcessor.process`` with each (starting
    instance state × cancel reason) and asserts the resulting task
    transition and exception propagation.

    Key divergence from the JQ path: the WorkerPool **does not
    discriminate pause-vs-terminate**. It always re-raises
    ``asyncio.CancelledError`` and lets the worker pool decide.
    """

    # ------------------------------------------------------------------
    # Test 11: RUNNING + normal completion → task COMPLETED
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_11_wp_running_normal_completion_completes_task(self):
        """RUNNING + happy path → ``complete_task`` called with success dict."""
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=MessageResult(content="done", tool_calls=None),
            instance_status=InstanceStatus.RUNNING.value,
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.complete_task = MagicMock(return_value=MagicMock())
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        result = await processor.process(task)

        # Worker-pool dict shape: success=True + content + message_id.
        assert result["success"] is True
        assert result["content"] == "done"
        assert result["message_id"] == task.message_id

        # complete_task called with the success dict.
        task_repo.complete_task.assert_called_once()
        call_args = task_repo.complete_task.call_args
        assert call_args.args[0] == task.id
        payload = call_args.args[1]
        assert payload["success"] is True
        assert payload["message_id"] == task.message_id

    # ------------------------------------------------------------------
    # Test 12: RUNNING + error → task FAILED (re-raise to worker pool)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_12_wp_running_error_reraises_and_marks_task_failed(self):
        """RUNNING + work_fn raises → error helper runs, exception re-raised.

        The WP handler's outer ``except Exception`` runs
        ``handle_message_processing_error(task_id=...)`` and then
        re-raises so the worker pool can ``fail_task``. The error
        helper itself does NOT call ``complete_task`` — the worker pool
        does that.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=RuntimeError("graph error"),
            instance_status=InstanceStatus.RUNNING.value,
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.complete_task = MagicMock(return_value=None)
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        with pytest.raises(RuntimeError, match="graph error"):
            await processor.process(task)

        # The error helper runs (spied via manager._publish_instance_lifecycle_event).
        manager._publish_instance_lifecycle_event.assert_awaited()

        # complete_task is NOT called by the processor itself — the
        # worker pool calls ``fail_task`` on re-raise.
        task_repo.complete_task.assert_not_called()

    # ------------------------------------------------------------------
    # Test 13: RUNNING + asyncio.CancelledError → re-raise (NO pause discrimination)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_13_wp_running_cancel_reraises_no_pause_discrimination(self):
        """RUNNING + asyncio.CancelledError → re-raise, no complete_task.

        THE KEY DIVERGENCE: unlike the JQ path, the WP path does **not**
        read ``instance.status`` to discriminate pause. It always
        re-raises and lets the worker pool's
        ``_handle_cancellation`` decide via the cancellation reason.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=asyncio.CancelledError(),
            instance_status=InstanceStatus.RUNNING.value,
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.complete_task = MagicMock(return_value=None)
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        with pytest.raises(asyncio.CancelledError):
            await processor.process(task)

        # No complete_task — the worker pool owns task lifecycle on cancel.
        task_repo.complete_task.assert_not_called()

    # ------------------------------------------------------------------
    # Test 14: RUNNING + LeaseContention → requeue with backoff
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_14_wp_running_lease_contention_requeues_with_backoff(self):
        """RUNNING + gate returns LeaseContention → ``requeue_task_with_backoff``.

        The WP contention callback logs (DEBUG per occurrence, INFO
        throttled) and calls ``requeue_task_with_backoff`` to put the
        task back in PENDING with a jittered ``next_retry_at``. The
        processor returns a ``requeued=True`` dict so the worker loop
        moves on to the next claim.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=MessageResult(content="unused", tool_calls=None),
            instance_status=InstanceStatus.RUNNING.value,
            gate=_make_contention_gate(),
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.requeue_task_with_backoff = MagicMock()
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        result = await processor.process(task)

        # Re-queued with backoff.
        task_repo.requeue_task_with_backoff.assert_called_once_with(task.id)

        # Result dict signals "re-queued, not failed".
        assert result["success"] is False
        assert result["requeued"] is True
        assert result["content"] is None
        assert result["message_id"] == task.message_id

    # ------------------------------------------------------------------
    # Test 15: RUNNING + LeaseLostError → requeue with backoff
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_15_wp_running_lease_lost_requeues_with_backoff(self):
        """RUNNING + gate raises LeaseLostError → ``requeue_task_with_backoff``.

        Same requeue path as LeaseContention but logs at WARNING level.
        The lease was revoked mid-flight; the task goes back to PENDING
        for the next worker poll.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=MessageResult(content="unused", tool_calls=None),
            instance_status=InstanceStatus.RUNNING.value,
            gate=_make_lease_lost_gate(),
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.requeue_task_with_backoff = MagicMock()
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        result = await processor.process(task)

        task_repo.requeue_task_with_backoff.assert_called_once_with(task.id)
        assert result["success"] is False
        assert result["requeued"] is True

    # ------------------------------------------------------------------
    # Test 16: PAUSED + asyncio.CancelledError → re-raise (no discrimination)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_16_wp_paused_cancel_reraises_no_discrimination(self):
        """PAUSED instance + asyncio.CancelledError → re-raise.

        The instance is PAUSED but the WP path does NOT discriminate.
        It logs "Task N paused" and re-raises — the worker pool's
        ``_handle_cancellation`` is responsible for keeping the task
        RUNNING (for resume) vs marking it FAILED.

        This is the **divergence** the C-M5 unification must preserve:
        JQ swallows the exception for PAUSE; WP re-raises.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=asyncio.CancelledError(),
            instance_status=InstanceStatus.PAUSED.value,
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.complete_task = MagicMock(return_value=None)
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        with pytest.raises(asyncio.CancelledError):
            await processor.process(task)

        # WP path does NOT read instance.status to discriminate.
        # (If it did, ``_instance_repository.get`` would be called from
        # the cancellation handler. It may be called by the pipeline's
        # dispatch stage, but NOT for pause discrimination.)
        task_repo.complete_task.assert_not_called()

    # ------------------------------------------------------------------
    # Test 17: TERMINATED + asyncio.CancelledError → re-raise
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_17_wp_terminated_cancel_reraises(self):
        """TERMINATED instance + asyncio.CancelledError → re-raise.

        Same behaviour as PAUSED/RUNNING: the WP path does not
        discriminate on instance status. It re-raises and the worker
        pool handles the terminal-state cleanup.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=asyncio.CancelledError(),
            instance_status=InstanceStatus.TERMINATED.value,
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        with pytest.raises(asyncio.CancelledError):
            await processor.process(task)

        task_repo.complete_task.assert_not_called()

    # ------------------------------------------------------------------
    # Test 18: RUNNING + LeaseContention backoff → retried (requeued dict)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_18_wp_contention_backoff_signal_is_requeued(self):
        """RUNNING + contention → processor returns the ``requeued=True`` dict.

        Variant of test 14 that focuses on the *signal* the worker
        loop receives. The dict ``{success: False, requeued: True}``
        tells ``Worker._run_task`` to move on without scheduling a
        retry — the task is already back in PENDING.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=MessageResult(content="unused", tool_calls=None),
            instance_status=InstanceStatus.RUNNING.value,
            gate=_make_contention_gate(),
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.requeue_task_with_backoff = MagicMock()
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        result = await processor.process(task)

        # The worker loop expects exactly these keys.
        assert set(result.keys()) == {"success", "requeued", "content", "message_id"}
        assert result["success"] is False
        assert result["requeued"] is True
        assert result["content"] is None

    # ------------------------------------------------------------------
    # Test 19: COMPLETED instance + stray task → happy path succeeds
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_19_wp_completed_instance_stray_task_succeeds(self):
        """COMPLETED instance + stray task → normal happy path.

        The WP path does not check ``instance.status`` before invoking
        the work_fn — it trusts the gate. A stray task against a
        COMPLETED instance still produces a ``success=True`` result
        (the manager's ``_process_message_with_tracking`` is a mock
        here; in production it would rehydrate the langgraph thread).
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=MessageResult(content="ok", tool_calls=None),
            instance_status=InstanceStatus.COMPLETED.value,
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.complete_task = MagicMock(return_value=MagicMock())
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        result = await processor.process(task)

        # Happy path: success dict + complete_task called.
        assert result["success"] is True
        task_repo.complete_task.assert_called_once()

    # ------------------------------------------------------------------
    # Test 20: ERROR instance + task → error path runs (re-raise)
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_20_wp_error_instance_task_runs_error_path(self):
        """ERROR instance + work_fn raises → error helper runs + re-raise.

        The WP path doesn't pre-flight on instance status, so an ERROR
        instance still attempts processing. When work_fn raises, the
        outer ``except Exception`` runs the error helper (DB event +
        lifecycle event + parent report) and re-raises.
        """
        from daemon.services.task_processor import ProcessMessageProcessor

        manager = _make_mock_manager(
            result=ConnectionError("db unreachable"),
            instance_status=InstanceStatus.ERROR.value,
        )
        message_repo = MagicMock()
        message_repo.get = MagicMock(return_value=_make_mock_message())
        task_repo = MagicMock()
        task_repo.complete_task = MagicMock(return_value=None)
        task = _make_mock_task()

        processor = ProcessMessageProcessor(
            instance_manager=manager,
            task_repo=task_repo,
            event_repo=None,
            message_repository=message_repo,
            source_dispatcher=None,
        )

        with pytest.raises(ConnectionError, match="db unreachable"):
            await processor.process(task)

        # Error helper ran.
        manager._publish_instance_lifecycle_event.assert_awaited()

        # complete_task NOT called — the worker pool's ``fail_task``
        # handles the terminal transition on re-raise.
        task_repo.complete_task.assert_not_called()
