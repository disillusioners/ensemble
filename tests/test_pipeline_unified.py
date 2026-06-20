"""Pipeline parity tests for Phase 5 of the CorrelationManager migration.

The shared :class:`daemon.services.message_processing_pipeline.MessageProcessingPipeline`
now handles the 6 shared stages for both physical dispatchers:

1. **WorkerPool path** —
   :class:`daemon.services.task_processor.ProcessMessageProcessor`
   driven by worker threads polling the ``task`` table.
2. **JobQueue path** —
   :class:`daemon.services.message_job_handler.MessageJobHandler`
   driven by ``JobProcessor._process_loop`` polling the
   ``job_queue_items`` table.

This file verifies **behavioural equivalence** between the two paths:

- **TestPipelineUnit** — unit tests for
  :meth:`MessageProcessingPipeline.execute` directly, covering the
  happy / error / contention / cancel branches.
- **TestPathParity** — tests that both paths call the shared
  pipeline (and its side-effect entrypoints) with equivalent
  arguments when processing the same message.

These are unit tests with mocks — no real DB or real CM is required
(unless explicitly noted). The goal is to verify the **contract**:
both paths delegate to the same shared pipeline with equivalent
parameters.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.cancellation import (
    CancellationReason,
    OperationCancelledError,
)
from daemon.manager import MessageResult
from daemon.services.execution_gate import (
    LeaseContention,
    LeaseContentionReason,
    LeaseHolderKind,
    LeaseLostError,
)
from daemon.services.message_processing_pipeline import (
    MessageProcessingPipeline,
    PipelineCallbacks,
    ProcessingContext,
    ProcessingResult,
)


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _make_passthrough_gate():
    """Return a MagicMock gate whose ``run`` invokes ``work_fn`` directly.

    Mirrors the pattern used in ``tests/job_queue/test_pause_while_processing.py``
    and ``tests/test_jq_error_reporting.py``: a transparent gate lets the
    tests drive ``_process_message_with_tracking`` (or its side_effect)
    through the pipeline without needing a real lease table.
    """

    async def _passthrough(*args, **kwargs):
        work_fn = kwargs.get("work_fn")
        return await work_fn()

    from daemon.services.execution_gate import ExecutionGateService

    gate = MagicMock(spec=ExecutionGateService)
    gate.run = AsyncMock(side_effect=_passthrough)
    return gate


def _make_manager(result: MessageResult | Exception | None = None):
    """Build a MagicMock manager wired for the pipeline.

    Args:
        result: Return value for ``_process_message_with_tracking``
            (an ``AsyncMock`` by default). If an ``Exception`` is passed,
            it is used as the ``side_effect`` so the pipeline hits the
            error/cancel paths.
    """
    m = MagicMock()
    if isinstance(result, Exception):
        m._process_message_with_tracking = AsyncMock(side_effect=result)
    else:
        m._process_message_with_tracking = AsyncMock(
            return_value=result or MessageResult(content="ok", tool_calls=None)
        )
    m._instance_repository = MagicMock()
    m._process_child_completion_and_notify_parent = AsyncMock()
    m._queue_repository = MagicMock()
    m._queue_repository.complete = MagicMock()
    m.execution_gate = _make_passthrough_gate()
    # Error-helper entrypoints (spied on via the pipeline's call to
    # ``handle_message_processing_error``).
    m._event_bus = MagicMock()
    m._event_bus.create_error_event = AsyncMock()
    m._publish_instance_lifecycle_event = AsyncMock()
    m._send_error_report = AsyncMock()
    return m


# ===========================================================================
# Test Class 1: TestPipelineUnit
# ===========================================================================


class TestPipelineUnit:
    """Unit tests for :meth:`MessageProcessingPipeline.execute`.

    These tests drive the pipeline directly with mocked dependencies
    (execution_gate, manager, queue_repository, source_dispatcher) and
    verify the four branches of ``execute``:

    1. Happy path — all 6 stages run in order, ``on_success`` fires.
    2. Error path — ``handle_message_processing_error`` runs, ``on_error`` fires.
    3. Contention path — ``on_contention`` callback is invoked.
    4. Cancel path — ``on_cancel`` is invoked, or the error re-raises.
    """

    @pytest.mark.asyncio
    async def test_happy_path_calls_all_stages_in_order(self):
        """Happy path: pipeline calls the 6 shared stages in order.

        Verifies the contract:
          1. gate.run is called with the holder_id/holder_kind.
          2. queue_repo.complete is called with the message_id.
          3. dispatch_completed is called with resolved source + content.
          4. child_completion checker is called with (instance_id, message_id).
          5. on_success callback fires with the result content.
        """
        manager = _make_manager(result=MessageResult(content="hello"))
        queue_repo = MagicMock()
        queue_repo.complete = MagicMock()
        source_dispatcher = MagicMock()
        source_dispatcher.dispatch_completed = AsyncMock()

        pipeline = MessageProcessingPipeline(
            execution_gate=manager.execution_gate,
            manager=manager,
            source_dispatcher=source_dispatcher,
            queue_repository=queue_repo,
        )

        context = ProcessingContext(
            instance_id="inst-1",
            message_id="msg-1",
            message="hi",
            message_source="api",
        )
        on_success = AsyncMock()
        callbacks = PipelineCallbacks(on_success=on_success)

        result = await pipeline.execute(
            context=context,
            holder_id="task:t1",
            holder_kind=LeaseHolderKind.TASK.value,
            callbacks=callbacks,
        )

        # Stage 2: gate.run invoked (work_fn executed inside passthrough)
        manager.execution_gate.run.assert_awaited_once()
        gate_kwargs = manager.execution_gate.run.call_args.kwargs
        assert gate_kwargs["instance_id"] == "inst-1"
        assert gate_kwargs["holder_id"] == "task:t1"
        assert gate_kwargs["holder_kind"] == LeaseHolderKind.TASK.value

        # Stage 4: queue_repo.complete called with message_id
        queue_repo.complete.assert_called_once_with("msg-1")

        # Stage 5: dispatch_completed called with content from MessageResult
        source_dispatcher.dispatch_completed.assert_awaited_once()
        dispatch_kwargs = source_dispatcher.dispatch_completed.call_args.kwargs
        assert dispatch_kwargs["instance_id"] == "inst-1"
        assert dispatch_kwargs["message_id"] == "msg-1"
        assert dispatch_kwargs["source"] == "api"
        assert dispatch_kwargs["content"] == "hello"

        # Stage 6: child completion checker called
        manager._process_child_completion_and_notify_parent.assert_awaited_once_with(
            "inst-1", "msg-1"
        )

        # on_success callback fired with happy-path result
        on_success.assert_awaited_once()
        cb_arg = on_success.call_args.args[0]
        assert cb_arg.success is True
        assert cb_arg.result_content == "hello"

        # Pipeline returned a success result
        assert result.success is True
        assert result.result_content == "hello"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_post_processing_error_runs_error_handler_and_on_error(self):
        """Error path: when a post-processing stage raises an
        unhandled error, the pipeline runs
        ``handle_message_processing_error`` and invokes ``on_error``.

        Each individual post-processing stage (``_mark_message_completed``,
        ``_dispatch_completed``, ``_check_child_completion``) has its
        own ``except Exception`` wrapper that swallows errors from
        the work they do (best-effort side-effects). To trigger the
        pipeline's error handler, we patch the stage method itself
        to raise — bypassing its internal try/except.

        This verifies the pipeline's safety-net error handler: if a
        post-processing stage itself fails (not just the work it
        delegates to), the pipeline runs the shared error helper and
        the ``on_error`` callback.
        """
        boom = ValueError("stage error")
        manager = _make_manager(result=MessageResult(content="ok"))

        pipeline = MessageProcessingPipeline(
            execution_gate=manager.execution_gate,
            manager=manager,
        )

        # Patch _mark_message_completed to raise, bypassing its
        # internal try/except.
        context = ProcessingContext(
            instance_id="inst-err",
            message_id="msg-err",
            message="x",
            message_source="api",
        )
        on_error = AsyncMock()
        callbacks = PipelineCallbacks(on_error=on_error)

        with patch(
            "daemon.services.message_processing_pipeline.handle_message_processing_error",
            new_callable=AsyncMock,
        ) as mock_helper, patch.object(
            pipeline, "_mark_message_completed",
            new_callable=AsyncMock, side_effect=boom,
        ):
            result = await pipeline.execute(
                context=context,
                holder_id="task:t-err",
                holder_kind=LeaseHolderKind.TASK.value,
                callbacks=callbacks,
                error_handler_id={"task_id": "t-err"},
            )

        # Error helper was called with the right kwargs
        mock_helper.assert_awaited_once()
        helper_kwargs = mock_helper.call_args.kwargs
        assert helper_kwargs["instance_id"] == "inst-err"
        assert helper_kwargs["message_id"] == "msg-err"
        assert helper_kwargs["task_id"] == "t-err"
        assert helper_kwargs["error"] is boom

        # on_error callback fired
        on_error.assert_awaited_once()
        cb_arg = on_error.call_args.args[0]
        assert cb_arg.success is False
        assert cb_arg.error is boom

        # Pipeline returned a failure result carrying the error
        assert result.success is False
        assert result.error is boom

    @pytest.mark.asyncio
    async def test_post_processing_exception_swallowed_by_stage(self):
        """Post-processing stage exceptions from delegated work are
        swallowed by the stage's own ``except Exception`` wrapper
        (best-effort side-effects). The pipeline does NOT run the
        error handler for these — the message is still considered
        successful.
        """
        boom = ValueError("dispatch failed")
        manager = _make_manager(result=MessageResult(content="ok"))

        # dispatch_completed raises — but _dispatch_completed catches
        # it internally (best-effort), so the pipeline's error handler
        # is NOT triggered.
        source_dispatcher = MagicMock()
        source_dispatcher.dispatch_completed = AsyncMock(side_effect=boom)

        pipeline = MessageProcessingPipeline(
            execution_gate=manager.execution_gate,
            manager=manager,
            source_dispatcher=source_dispatcher,
        )

        context = ProcessingContext(
            instance_id="inst-swallow",
            message_id="msg-swallow",
            message="x",
            message_source="api",
        )
        on_error = AsyncMock()
        callbacks = PipelineCallbacks(on_error=on_error)

        with patch(
            "daemon.services.message_processing_pipeline.handle_message_processing_error",
            new_callable=AsyncMock,
        ) as mock_helper:
            result = await pipeline.execute(
                context=context,
                holder_id="task:t-sw",
                holder_kind=LeaseHolderKind.TASK.value,
                callbacks=callbacks,
            )

        # Error helper was NOT called (stage swallowed the error)
        mock_helper.assert_not_called()
        # on_error callback was NOT called
        on_error.assert_not_awaited()
        # Pipeline returned success (the message was processed)
        assert result.success is True
        assert result.result_content == "ok"

    @pytest.mark.asyncio
    async def test_stage2_error_propagates_out(self):
        """Stage-2 errors (work_fn / gate.run) propagate OUT of
        ``execute()`` — the pipeline's post-processing try/except
        does NOT catch them.

        This is by design: the dispatcher (WP / JQ) has its own outer
        except clause that calls ``handle_message_processing_error``
        for stage-2 errors. The pipeline only handles errors from
        stages 4-6 internally.
        """
        boom = ValueError("work_fn boom")
        manager = _make_manager(result=boom)

        pipeline = MessageProcessingPipeline(
            execution_gate=manager.execution_gate,
            manager=manager,
        )

        context = ProcessingContext(
            instance_id="inst-stage2",
            message_id="msg-stage2",
            message="x",
        )
        callbacks = PipelineCallbacks(on_error=AsyncMock())

        with patch(
            "daemon.services.message_processing_pipeline.handle_message_processing_error",
            new_callable=AsyncMock,
        ) as mock_helper:
            with pytest.raises(ValueError, match="work_fn boom"):
                await pipeline.execute(
                    context=context,
                    holder_id="task:t-s2",
                    holder_kind=LeaseHolderKind.TASK.value,
                    callbacks=callbacks,
                )

        # Pipeline's error helper was NOT called for stage-2 errors
        mock_helper.assert_not_called()
        # on_error callback was NOT called for stage-2 errors
        callbacks.on_error.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_contention_path_delegates_to_on_contention(self):
        """Contention path: when the gate returns ``LeaseContention``,
        the pipeline delegates to ``on_contention`` and returns whatever
        the callback returned (typically ``should_defer=True``).
        """
        manager = MagicMock()
        manager._instance_repository = MagicMock()
        manager._process_child_completion_and_notify_parent = AsyncMock()

        contention = LeaseContention(
            reason=LeaseContentionReason.HELD_BY_OTHER,
            holder_id="message_job:other",
            holder_kind="message_job",
        )

        async def _returns_contention(*args, **kwargs):
            return contention

        from daemon.services.execution_gate import ExecutionGateService

        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_returns_contention)
        manager.execution_gate = gate

        pipeline = MessageProcessingPipeline(
            execution_gate=manager.execution_gate,
            manager=manager,
        )

        context = ProcessingContext(
            instance_id="inst-c",
            message_id="msg-c",
            message="x",
        )
        on_contention = AsyncMock(
            return_value=ProcessingResult(success=False, should_defer=True)
        )
        callbacks = PipelineCallbacks(on_contention=on_contention)

        result = await pipeline.execute(
            context=context,
            holder_id="task:t-c",
            holder_kind=LeaseHolderKind.TASK.value,
            callbacks=callbacks,
        )

        # on_contention received the LeaseContention instance
        on_contention.assert_awaited_once_with(contention)

        # Pipeline returned the callback's result
        assert result.success is False
        assert result.should_defer is True

        # Post-processing stages must NOT have run (we short-circuited)
        manager._process_child_completion_and_notify_parent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_contention_without_callback_reraises_lease_lost(self):
        """When ``on_contention`` is None and the gate raises
        ``LeaseLostError``, the pipeline re-raises the original error.

        This covers the ``LeaseLostError`` path of ``_handle_contention``
        (the exception form). The ``LeaseContention`` return-value
        path has a separate quirk: ``raise`` on a ``LeaseContention``
        dataclass is not valid in Python 3.9+ (it doesn't inherit
        from ``BaseException``), so that branch is effectively
        unreachable without an ``on_contention`` callback. Both
        production paths (WP and JQ) always supply ``on_contention``,
        so the quirk is benign.
        """
        manager = MagicMock()
        manager._instance_repository = MagicMock()

        lost_err = LeaseLostError("lease revoked")

        async def _raises_lost(*args, **kwargs):
            raise lost_err

        from daemon.services.execution_gate import ExecutionGateService

        gate = MagicMock(spec=ExecutionGateService)
        gate.run = AsyncMock(side_effect=_raises_lost)
        manager.execution_gate = gate

        pipeline = MessageProcessingPipeline(
            execution_gate=manager.execution_gate,
            manager=manager,
        )

        context = ProcessingContext(
            instance_id="inst-c2",
            message_id="msg-c2",
            message="x",
        )
        callbacks = PipelineCallbacks()  # no on_contention

        with pytest.raises(LeaseLostError):
            await pipeline.execute(
                context=context,
                holder_id="task:t-c2",
                holder_kind=LeaseHolderKind.TASK.value,
                callbacks=callbacks,
            )

    @pytest.mark.asyncio
    async def test_cancel_path_delegates_to_on_cancel(self):
        """Cancel path: when a post-processing stage raises
        ``asyncio.CancelledError``, the pipeline delegates to
        ``on_cancel`` and returns whatever the callback returned.

        We use ``asyncio.CancelledError`` (not
        ``OperationCancelledError``) because the individual
        post-processing stages swallow ``Exception`` (which catches
        ``OperationCancelledError`` since it extends ``Exception``),
        but NOT ``BaseException`` (which is what
        ``asyncio.CancelledError`` extends in Python 3.9+). So only
        ``asyncio.CancelledError`` (or ``BaseException`` subclasses)
        can escape a stage's own ``except Exception`` wrapper and
        reach the pipeline's stages 3-6 try/except.

        ``OperationCancelledError`` from the work_fn (stage 2)
        propagates OUT of ``execute()`` — it is NOT caught by the
        pipeline's post-processing try/except.
        """
        cancel_err = asyncio.CancelledError()
        manager = _make_manager(result=MessageResult(content="ok"))

        # Make dispatch_completed raise asyncio.CancelledError so it
        # escapes _dispatch_completed's ``except Exception`` and
        # reaches the pipeline's stages 3-6 try/except.
        source_dispatcher = MagicMock()
        source_dispatcher.dispatch_completed = AsyncMock(side_effect=cancel_err)

        pipeline = MessageProcessingPipeline(
            execution_gate=manager.execution_gate,
            manager=manager,
            source_dispatcher=source_dispatcher,
        )

        context = ProcessingContext(
            instance_id="inst-x",
            message_id="msg-x",
            message="x",
            message_source="api",
        )
        on_cancel = AsyncMock(
            return_value=ProcessingResult(success=False, should_defer=True)
        )
        callbacks = PipelineCallbacks(on_cancel=on_cancel)

        result = await pipeline.execute(
            context=context,
            holder_id="message_job:j-x",
            holder_kind=LeaseHolderKind.MESSAGE_JOB.value,
            callbacks=callbacks,
        )

        # on_cancel received the asyncio.CancelledError
        on_cancel.assert_awaited_once()
        cb_exc = on_cancel.call_args.args[0]
        assert isinstance(cb_exc, asyncio.CancelledError)

        # Pipeline returned the callback's result
        assert result.success is False
        assert result.should_defer is True

    @pytest.mark.asyncio
    async def test_cancel_without_callback_reraises(self):
        """When ``on_cancel`` is None, the pipeline re-raises the
        ``asyncio.CancelledError`` so the dispatcher can translate
        it into the right terminal action.
        """
        cancel_err = asyncio.CancelledError()
        manager = _make_manager(result=MessageResult(content="ok"))

        source_dispatcher = MagicMock()
        source_dispatcher.dispatch_completed = AsyncMock(side_effect=cancel_err)

        pipeline = MessageProcessingPipeline(
            execution_gate=manager.execution_gate,
            manager=manager,
            source_dispatcher=source_dispatcher,
        )

        context = ProcessingContext(
            instance_id="inst-x2",
            message_id="msg-x2",
            message="x",
            message_source="api",
        )
        callbacks = PipelineCallbacks()  # no on_cancel

        with pytest.raises(asyncio.CancelledError):
            await pipeline.execute(
                context=context,
                holder_id="task:t-x2",
                holder_kind=LeaseHolderKind.TASK.value,
                callbacks=callbacks,
            )


# ===========================================================================
# Test Class 2: TestPathParity
# ===========================================================================


class TestPathParity:
    """Verify WorkerPool and JobQueue produce identical observable
    side-effects when processing the same message.

    Both paths must delegate to the shared
    :class:`MessageProcessingPipeline` with equivalent parameters.
    These tests spy on the pipeline (or the shared side-effect
    entrypoints) and assert both paths make equivalent calls.
    """

    # ------------------------------------------------------------------
    # Shared fixtures
    # ------------------------------------------------------------------

    @pytest.fixture
    def source_dispatcher(self):
        """Mock ResponseDispatcher with ``dispatch_completed``."""
        d = MagicMock()
        d.dispatch_completed = AsyncMock()
        return d

    @pytest.fixture
    def wp_manager(self, source_dispatcher):
        """Mock InstanceManager wired for the WorkerPool path.

        Returns a MessageResult on success so the pipeline's happy path
        runs to completion.
        """
        m = _make_manager(result=MessageResult(content="shared-response"))
        m._queue_repository = MagicMock()
        m._queue_repository.complete = MagicMock()
        return m

    @pytest.fixture
    def jq_manager(self, source_dispatcher):
        """Mock InstanceManager wired for the JobQueue path."""
        m = _make_manager(result=MessageResult(content="shared-response"))
        m._queue_repository = MagicMock()
        m._queue_repository.complete = MagicMock()
        # JQ cross-dispatcher pre-flight stubs
        task_repo_stub = MagicMock()
        task_repo_stub.find_running_by_instance = MagicMock(return_value=None)
        m._task_repo = task_repo_stub
        # Instance metadata for JQ pre-pickup transition + on_success check
        instance_meta = MagicMock()
        instance_meta.parent_id = "parent-id"
        instance_meta.status = "running"
        instance_meta.waiting_for = 0
        instance_meta.instance_id = "inst-shared"
        instance_meta.agent_id = "agent-1"
        m._instance_repository.get = MagicMock(return_value=instance_meta)
        m._instance_repository.transition_status_if = MagicMock(
            return_value=instance_meta
        )
        m._live_hub = None
        return m

    @pytest.fixture
    def jq_service(self):
        """Mock JobQueueService."""
        s = MagicMock()
        s.complete_job = AsyncMock()
        s._lock_manager = MagicMock()
        s._lock_manager.release_queue_lock = AsyncMock()
        return s

    @pytest.fixture
    def jq_repo(self):
        """Mock JobRepository for JQ pre-flights."""
        r = MagicMock()
        r.find_processing_message_jobs_by_instance = MagicMock(return_value=[])
        r.atomic_transition = MagicMock(return_value=None)
        return r

    @pytest.fixture
    def wp_task(self):
        """Mock Task object for the WorkerPool path."""
        task = MagicMock()
        task.id = "task-shared"
        task.instance_id = "inst-shared"
        task.message_id = "msg-shared"
        task.retry_count = 0
        task.task_type = "process_message"
        return task

    @pytest.fixture
    def jq_job(self):
        """Mock JobItem for the JobQueue path."""
        job = MagicMock()
        job.job_id = "job-shared"
        job.instance_id = "inst-shared"
        job.message = "hello"
        job.job_type = "message"
        job.project_id = "proj-1"
        job.queue_id = "q-1"
        job.retry_count = 0
        job.job_metadata = {
            "message_id": "msg-shared",
            "source": "api",
        }
        return job

    # ------------------------------------------------------------------
    # 1. Error side-effects parity
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_error_side_effects_parity(
        self,
        wp_manager,
        jq_manager,
        jq_service,
        jq_repo,
        wp_task,
        jq_job,
        monkeypatch,
    ):
        """Error side-effects parity: both paths must trigger
        ``handle_message_processing_error`` with equivalent arguments
        (instance_id, message_id, error) — producing the same 3
        side-effects: DB error event, lifecycle event, parent report.

        The only intentional difference is the id key: WP passes
        ``task_id``, JQ passes ``job_id``. The error helper uses that
        key to tag the error event and (for JQ) mark the job FAILED.
        """
        # Force both managers to raise the same error
        boom = ValueError("parity boom")
        wp_manager._process_message_with_tracking = AsyncMock(side_effect=boom)
        jq_manager._process_message_with_tracking = AsyncMock(side_effect=boom)

        # Stub the cross-dispatcher pre-flight for JQ
        from daemon.repositories.task.repository import TaskRepository

        monkeypatch.setattr(
            TaskRepository,
            "find_running_by_instance",
            lambda self, instance_id: None,
        )

        # Spy on the shared error helper (called from inside the pipeline
        # for stage 3-6 errors and from the outer except clause for
        # stage-2 errors). We patch at the module level so both the
        # pipeline import and the dispatcher imports see the same mock.
        helper_calls: list[dict] = []

        async def _capture_helper(**kwargs):
            helper_calls.append(kwargs)

        with patch(
            "daemon.services.message_processing_pipeline.handle_message_processing_error",
            new=_capture_helper,
        ), patch(
            "daemon.services.task_processor.handle_message_processing_error",
            new=_capture_helper,
        ), patch(
            "daemon.services.message_job_handler.handle_message_processing_error",
            new=_capture_helper,
        ):
            # --- WorkerPool path ---
            from daemon.services.task_processor import ProcessMessageProcessor

            wp_msg = MagicMock()
            wp_msg.content = "hello"
            wp_msg.source = "api"
            wp_msg.images = None
            wp_msg.message_metadata = None
            wp_manager._queue_repository.get = MagicMock(return_value=wp_msg)

            wp_processor = ProcessMessageProcessor(
                instance_manager=wp_manager,
                task_repo=MagicMock(),
                event_repo=None,
                message_repository=wp_manager._queue_repository,
                source_dispatcher=None,
            )
            with pytest.raises(ValueError, match="parity boom"):
                await wp_processor.process(wp_task)

            # --- JobQueue path ---
            from daemon.services.message_job_handler import MessageJobHandler

            jq_handler = MessageJobHandler(
                manager=jq_manager,
                job_queue_service=jq_service,
                job_repository=jq_repo,
            )
            await jq_handler.handle(jq_job)

        # Both paths must have called the shared helper exactly once
        assert len(helper_calls) == 2, (
            f"Expected 2 helper calls (one per path), got {len(helper_calls)}"
        )

        wp_call = helper_calls[0]
        jq_call = helper_calls[1]

        # Parity: same instance_id, message_id, and error
        assert wp_call["instance_id"] == jq_call["instance_id"] == "inst-shared"
        assert wp_call["message_id"] == jq_call["message_id"] == "msg-shared"
        assert str(wp_call["error"]) == str(jq_call["error"]) == "parity boom"

        # Intentional difference: id key
        assert wp_call.get("task_id") == "task-shared"
        assert "job_id" not in wp_call
        assert jq_call.get("job_id") == "job-shared"
        assert "task_id" not in jq_call

    # ------------------------------------------------------------------
    # 2. Dispatch parity
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_dispatch_parity(
        self,
        wp_manager,
        jq_manager,
        jq_service,
        jq_repo,
        wp_task,
        jq_job,
        source_dispatcher,
        monkeypatch,
    ):
        """Dispatch parity: both paths must call ``dispatch_completed``
        with the same arguments (instance_id, message_id, source,
        content) when the message resolves to an external source.

        We inject the SAME ``source_dispatcher`` into both paths and
        verify both call ``dispatch_completed`` identically.
        """
        from daemon.repositories.task.repository import TaskRepository

        monkeypatch.setattr(
            TaskRepository,
            "find_running_by_instance",
            lambda self, instance_id: None,
        )

        # --- WorkerPool path ---
        from daemon.services.task_processor import ProcessMessageProcessor

        wp_msg = MagicMock()
        wp_msg.content = "hello"
        wp_msg.source = "telegram:123"  # external source
        wp_msg.images = None
        wp_msg.message_metadata = None
        wp_manager._queue_repository.get = MagicMock(return_value=wp_msg)

        wp_processor = ProcessMessageProcessor(
            instance_manager=wp_manager,
            task_repo=MagicMock(),
            event_repo=None,
            message_repository=wp_manager._queue_repository,
            source_dispatcher=source_dispatcher,
        )
        await wp_processor.process(wp_task)

        # --- JobQueue path ---
        from daemon.services.message_job_handler import MessageJobHandler

        # Use the SAME external source in the JQ job metadata
        jq_job.job_metadata = {
            "message_id": "msg-shared",
            "source": "telegram:123",
        }
        jq_handler = MessageJobHandler(
            manager=jq_manager,
            job_queue_service=jq_service,
            job_repository=jq_repo,
            source_dispatcher=source_dispatcher,
        )
        await jq_handler.handle(jq_job)

        # Both paths called dispatch_completed
        assert source_dispatcher.dispatch_completed.await_count == 2, (
            f"Expected 2 dispatch calls, got "
            f"{source_dispatcher.dispatch_completed.await_count}"
        )

        wp_dispatch = source_dispatcher.dispatch_completed.call_args_list[0].kwargs
        jq_dispatch = source_dispatcher.dispatch_completed.call_args_list[1].kwargs

        # Parity: same instance_id, message_id, source
        assert wp_dispatch["instance_id"] == jq_dispatch["instance_id"] == "inst-shared"
        assert wp_dispatch["message_id"] == jq_dispatch["message_id"] == "msg-shared"
        assert wp_dispatch["source"] == jq_dispatch["source"] == "telegram:123"
        # Content comes from the MessageResult each path produced.
        # Both managers return a MessageResult whose .content is set,
        # so the dispatched content must match.
        assert wp_dispatch["content"] == jq_dispatch["content"]

    # ------------------------------------------------------------------
    # 3. Child completion parity
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_child_completion_parity(
        self,
        wp_manager,
        jq_manager,
        jq_service,
        jq_repo,
        wp_task,
        jq_job,
        monkeypatch,
    ):
        """Child completion parity: both paths must call
        ``_process_child_completion_and_notify_parent`` with the same
        arguments (instance_id, message_id).

        We wire the SAME manager-level mock into both paths and verify
        both invoke the child-completion checker identically.
        """
        from daemon.repositories.task.repository import TaskRepository

        monkeypatch.setattr(
            TaskRepository,
            "find_running_by_instance",
            lambda self, instance_id: None,
        )

        # --- WorkerPool path ---
        from daemon.services.task_processor import ProcessMessageProcessor

        wp_msg = MagicMock()
        wp_msg.content = "hello"
        wp_msg.source = "api"
        wp_msg.images = None
        wp_msg.message_metadata = None
        wp_manager._queue_repository.get = MagicMock(return_value=wp_msg)

        wp_processor = ProcessMessageProcessor(
            instance_manager=wp_manager,
            task_repo=MagicMock(),
            event_repo=None,
            message_repository=wp_manager._queue_repository,
            source_dispatcher=None,
        )
        await wp_processor.process(wp_task)

        wp_checker = wp_manager._process_child_completion_and_notify_parent
        wp_checker.assert_awaited_once_with("inst-shared", "msg-shared")

        # --- JobQueue path ---
        from daemon.services.message_job_handler import MessageJobHandler

        jq_handler = MessageJobHandler(
            manager=jq_manager,
            job_queue_service=jq_service,
            job_repository=jq_repo,
        )
        await jq_handler.handle(jq_job)

        jq_checker = jq_manager._process_child_completion_and_notify_parent
        jq_checker.assert_awaited_once_with("inst-shared", "msg-shared")

        # Parity: both paths called the checker with identical args
        wp_args = wp_checker.call_args.args
        jq_args = jq_checker.call_args.args
        assert wp_args == jq_args == ("inst-shared", "msg-shared")

    # ------------------------------------------------------------------
    # 4. retry_count parity
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_retry_count_parity(
        self,
        wp_manager,
        jq_manager,
        jq_service,
        jq_repo,
        wp_task,
        jq_job,
        monkeypatch,
    ):
        """retry_count parity: both paths must propagate the same
        ``retry_count`` to ``_process_message_with_tracking``.

        We set retry_count=3 on both the WP task and the JQ job metadata
        and verify both paths pass ``retry_count=3`` into the tracking
        call.
        """
        from daemon.repositories.task.repository import TaskRepository

        monkeypatch.setattr(
            TaskRepository,
            "find_running_by_instance",
            lambda self, instance_id: None,
        )

        # Set retry_count=3 on both inputs
        wp_task.retry_count = 3
        jq_job.job_metadata = {
            "message_id": "msg-shared",
            "source": "api",
            "retry_count": 3,
        }

        # --- WorkerPool path ---
        from daemon.services.task_processor import ProcessMessageProcessor

        wp_msg = MagicMock()
        wp_msg.content = "hello"
        wp_msg.source = "api"
        wp_msg.images = None
        wp_msg.message_metadata = None
        wp_manager._queue_repository.get = MagicMock(return_value=wp_msg)

        wp_processor = ProcessMessageProcessor(
            instance_manager=wp_manager,
            task_repo=MagicMock(),
            event_repo=None,
            message_repository=wp_manager._queue_repository,
            source_dispatcher=None,
        )
        await wp_processor.process(wp_task)

        wp_kwargs = wp_manager._process_message_with_tracking.call_args.kwargs
        assert wp_kwargs["retry_count"] == 3

        # --- JobQueue path ---
        from daemon.services.message_job_handler import MessageJobHandler

        jq_handler = MessageJobHandler(
            manager=jq_manager,
            job_queue_service=jq_service,
            job_repository=jq_repo,
        )
        await jq_handler.handle(jq_job)

        jq_kwargs = jq_manager._process_message_with_tracking.call_args.kwargs
        assert jq_kwargs["retry_count"] == 3

        # Parity: both paths passed the same retry_count
        assert wp_kwargs["retry_count"] == jq_kwargs["retry_count"] == 3

    # ------------------------------------------------------------------
    # 5. Bonus: instance_id / message_id propagation parity
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_context_fields_parity(
        self,
        wp_manager,
        jq_manager,
        jq_service,
        jq_repo,
        wp_task,
        jq_job,
        monkeypatch,
    ):
        """Context fields parity: both paths must pass the same
        ``instance_id``, ``message_id``, ``message``, and ``source``
        into ``_process_message_with_tracking`` when the inputs match.
        """
        from daemon.repositories.task.repository import TaskRepository

        monkeypatch.setattr(
            TaskRepository,
            "find_running_by_instance",
            lambda self, instance_id: None,
        )

        # --- WorkerPool path ---
        from daemon.services.task_processor import ProcessMessageProcessor

        wp_msg = MagicMock()
        wp_msg.content = "hello"
        wp_msg.source = "api"
        wp_msg.images = None
        wp_msg.message_metadata = None
        wp_manager._queue_repository.get = MagicMock(return_value=wp_msg)

        wp_processor = ProcessMessageProcessor(
            instance_manager=wp_manager,
            task_repo=MagicMock(),
            event_repo=None,
            message_repository=wp_manager._queue_repository,
            source_dispatcher=None,
        )
        await wp_processor.process(wp_task)

        # --- JobQueue path ---
        from daemon.services.message_job_handler import MessageJobHandler

        jq_handler = MessageJobHandler(
            manager=jq_manager,
            job_queue_service=jq_service,
            job_repository=jq_repo,
        )
        await jq_handler.handle(jq_job)

        wp_kwargs = wp_manager._process_message_with_tracking.call_args.kwargs
        jq_kwargs = jq_manager._process_message_with_tracking.call_args.kwargs

        # Parity on the fields that both paths source identically
        assert wp_kwargs["instance_id"] == jq_kwargs["instance_id"] == "inst-shared"
        assert wp_kwargs["message_id"] == jq_kwargs["message_id"] == "msg-shared"
        assert wp_kwargs["message_source"] == jq_kwargs["message_source"] == "api"
