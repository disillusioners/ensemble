"""Pipeline unit tests for :class:`MessageProcessingPipeline`.

The shared
:class:`daemon.services.message_processing_pipeline.MessageProcessingPipeline`
handles the 6 processing stages for the single WorkerPool dispatcher:

* **WorkerPool path** —
  :class:`daemon.services.task_processor.ProcessMessageProcessor`
  driven by worker threads polling the ``task`` table.

The legacy JobQueue path (``MessageJobHandler``) was removed in Phase
D — all message work now flows through the unified
``JobFeedbackObserver`` lifecycle path. The
``TestPathParity`` parity suite was deleted with the MJH legacy path.

This file verifies **pipeline behaviour** directly:

* **TestPipelineUnit** — unit tests for
  :meth:`MessageProcessingPipeline.execute` covering the
  happy / error / cancel branches.

These are unit tests with mocks — no real DB or real CM is required
(unless explicitly noted). The goal is to verify the **contract**:
the shared pipeline handles all the stages consistently.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.cancellation import (
    CancellationReason,
    OperationCancelledError,
)
from daemon.manager import MessageResult
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
    verify the three branches of ``execute``:

    1. Happy path — all 6 stages run in order, ``on_success`` fires.
    2. Error path — ``handle_message_processing_error`` runs, ``on_error`` fires.
    3. Cancel path — ``on_cancel`` is invoked, or the error re-raises.
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
            holder_kind="task",
            callbacks=callbacks,
        )

        # Stage 2: gate.run invoked (work_fn executed inside passthrough)
        manager.execution_gate.run.assert_awaited_once()
        gate_kwargs = manager.execution_gate.run.call_args.kwargs
        assert gate_kwargs["instance_id"] == "inst-1"
        assert gate_kwargs["holder_id"] == "task:t1"
        assert gate_kwargs["holder_kind"] == "task"

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
                holder_kind="task",
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
                holder_kind="task",
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
                    holder_kind="task",
                    callbacks=callbacks,
                )

        # Pipeline's error helper was NOT called for stage-2 errors
        mock_helper.assert_not_called()
        # on_error callback was NOT called for stage-2 errors
        callbacks.on_error.assert_not_awaited()

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
            holder_kind="message_job",
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
                holder_kind="task",
                callbacks=callbacks,
            )


