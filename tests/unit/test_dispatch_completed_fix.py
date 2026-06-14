"""Unit tests for dispatch_completed fix in MessageJobHandler.

These tests verify that:
1. Agent responses processed through JobQueue path reach external sources (Telegram, Discord)
2. Internal reports (completion/error reports) are resolved to the original external source
3. Dispatch errors don't fail the job (best-effort delivery)
4. Edge cases are handled gracefully
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from daemon.services.message_job_handler import MessageJobHandler
from daemon.services.job_processor import JobProcessor
from daemon.services.job_queue_service import DemandState
from daemon.manager import MessageResult


# ── Test Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_source_dispatcher():
    """Create a mock source dispatcher with dispatch_completed as AsyncMock."""
    dispatcher = MagicMock()
    dispatcher.dispatch_completed = AsyncMock()
    return dispatcher


@pytest.fixture
def mock_manager(monkeypatch):
    """Create a mock InstanceManager with _process_message_with_tracking."""
    manager = MagicMock()
    manager._process_message_with_tracking = AsyncMock(
        return_value=MessageResult(content="Processed message response", tool_calls=None)
    )
    manager._instance_repository = MagicMock()
    manager._queue_repository = MagicMock()
    # Execution Gate stub: transparent, runs the work.
    from daemon.services.execution_gate import ExecutionGateService

    async def _passthrough(*args, **kwargs):
        work_fn = kwargs.get("work_fn")
        return await work_fn()

    gate = MagicMock(spec=ExecutionGateService)
    gate.run = AsyncMock(side_effect=_passthrough)
    manager.execution_gate = gate
    # Cross-dispatcher pre-flight is now a SQL query on
    # ``TaskRepository.find_running_by_instance``. The mock manager's
    # ``_task_repo`` is a MagicMock by default; override it with a
    # tiny stub whose ``find_running_by_instance`` returns None so
    # the handler takes the happy path (no running task to defer to).
    task_repo_stub = MagicMock()
    task_repo_stub.find_running_by_instance = MagicMock(return_value=None)
    manager._task_repo = task_repo_stub
    return manager


@pytest.fixture
def mock_job_service():
    """Create a mock JobQueueService."""
    service = MagicMock()
    service.complete_job = AsyncMock()
    service._lock_manager = MagicMock()
    service._lock_manager.release_queue_lock = AsyncMock()
    return service


@pytest.fixture
def mock_job_repo():
    """Create a mock JobRepository."""
    repo = MagicMock()
    repo.find_processing_message_jobs_by_instance = MagicMock(return_value=[])
    return repo


def create_message_job(
    instance_id: str = "test-instance-id",
    message_id: str = "msg-123",
    source: str = "telegram:832330949",
    job_id: str = "test-job-id",
) -> MagicMock:
    """Helper to create a mock MESSAGE job."""
    job = MagicMock()
    job.job_id = job_id
    job.instance_id = instance_id
    job.message = "Test message"
    job.job_metadata = {
        "message_id": message_id,
        "source": source,
    }
    job.project_id = "test-project"
    job.queue_id = "test-queue"
    return job


def create_mock_instance(instance_id: str, original_source: str = "telegram:832330949") -> MagicMock:
    """Helper to create a mock instance with instance_metadata."""
    instance = MagicMock()
    instance.instance_id = instance_id
    instance.instance_metadata = {"original_source": original_source}
    instance.status = "running"
    instance.waiting_for = 0
    return instance


# ── Test Class 1: TestDispatchAfterProcessing ────────────────────────────────────


class TestDispatchAfterProcessing:
    """Tests for dispatch_completed being called after successful message processing."""

    @pytest.mark.asyncio
    async def test_dispatch_completed_called_after_processing(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Verify dispatch_completed is called after _process_message_with_tracking succeeds."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        await handler.handle(job)

        # Verify dispatch_completed was called
        mock_source_dispatcher.dispatch_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_completed_receives_correct_args(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Verify dispatch_completed is called with the correct arguments."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(
            instance_id="instance-abc",
            message_id="msg-456",
            source="telegram:12345",
        )

        await handler.handle(job)

        # Verify correct args were passed
        mock_source_dispatcher.dispatch_completed.assert_called_once_with(
            instance_id="instance-abc",
            message_id="msg-456",
            source="telegram:12345",
            content="Processed message response",
            message_type="final",
        )

    @pytest.mark.asyncio
    async def test_dispatch_completed_not_called_without_source_dispatcher(
        self, mock_manager, mock_job_service, mock_job_repo
    ):
        """Verify dispatch_completed is NOT called when source_dispatcher is None."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=None,  # No dispatcher
        )
        job = create_message_job()

        await handler.handle(job)

        # Verify the manager was called (processing happened)
        mock_manager._process_message_with_tracking.assert_called_once()
        # No error should be raised even without dispatcher

    @pytest.mark.asyncio
    async def test_job_still_completes_after_dispatch(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Verify job is completed even when dispatch is called."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        await handler.handle(job)

        # Verify job was completed
        mock_job_service.complete_job.assert_called_once_with(
            job.job_id,
            demand_state=DemandState.COMPLETED,
            result_summary="Processed message response",
        )


# ── Test Class 2: TestInternalReportResolution ──────────────────────────────────


class TestInternalReportResolution:
    """Tests for resolving internal reports to original external source."""

    @pytest.mark.asyncio
    async def test_internal_report_resolves_to_original_source(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When source starts with 'internal_report:', resolve to original_source from metadata."""
        # Setup: instance has original_source in metadata
        mock_instance = create_mock_instance("test-instance-id", original_source="telegram:832330949")
        mock_manager._instance_repository.get.return_value = mock_instance

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(source="internal_report:completion-123")

        await handler.handle(job)

        # Verify dispatch uses original_source, not internal_report source
        mock_source_dispatcher.dispatch_completed.assert_called_once_with(
            instance_id="test-instance-id",
            message_id="msg-123",
            source="telegram:832330949",  # Original source, NOT "internal_report:..."
            content="Processed message response",
            message_type="final",
        )

    @pytest.mark.asyncio
    async def test_internal_error_report_resolves_to_original_source(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When source starts with 'internal_error_report:', resolve to original_source."""
        mock_instance = create_mock_instance("test-instance-id", original_source="discord:channel-789")
        mock_manager._instance_repository.get.return_value = mock_instance

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(source="internal_error_report:error-456")

        await handler.handle(job)

        # Verify dispatch uses original_source from metadata
        mock_source_dispatcher.dispatch_completed.assert_called_once_with(
            instance_id="test-instance-id",
            message_id="msg-123",
            source="discord:channel-789",  # Original source
            content="Processed message response",
            message_type="final",
        )

    @pytest.mark.asyncio
    async def test_internal_report_skipped_when_no_original_source(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When internal report has no original_source in metadata, dispatch is skipped."""
        mock_instance = MagicMock()
        mock_instance.instance_metadata = {}  # Empty metadata - no original_source
        mock_instance.status = "running"
        mock_instance.waiting_for = 0
        mock_manager._instance_repository.get.return_value = mock_instance

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(source="internal_report:completion-123")

        await handler.handle(job)

        # Verify dispatch was NOT called (no original_source to dispatch to)
        mock_source_dispatcher.dispatch_completed.assert_not_called()
        # But job should still complete
        mock_job_service.complete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_internal_report_skipped_when_instance_not_found(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When instance is not found, dispatch is skipped."""
        mock_manager._instance_repository.get.return_value = None

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(source="internal_report:completion-123")

        await handler.handle(job)

        # Verify dispatch was NOT called
        mock_source_dispatcher.dispatch_completed.assert_not_called()
        # Job should still complete
        mock_job_service.complete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_internal_report_skipped_when_instance_metadata_is_none(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When instance_metadata is None, dispatch is skipped."""
        mock_instance = MagicMock()
        mock_instance.instance_metadata = None  # None, not empty dict
        mock_instance.status = "running"
        mock_instance.waiting_for = 0
        mock_manager._instance_repository.get.return_value = mock_instance

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(source="internal_report:completion-123")

        await handler.handle(job)

        # Verify dispatch was NOT called
        mock_source_dispatcher.dispatch_completed.assert_not_called()

    @pytest.mark.asyncio
    async def test_internal_agent_job_event_resolves_to_original_source(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """internal_agent:job_event:* notifications resolve to original_source.

        Regression test: JobFeedbackObserver sends watcher notifications with
        source `internal_agent:job_event:<job_id>:<status>`. The handler must
        resolve these to the instance's original_source so external sources
        (Slack, Telegram) receive the final report.
        """
        mock_instance = create_mock_instance("test-instance-id", original_source="slack-bot:TWS:U1")
        mock_manager._instance_repository.get.return_value = mock_instance

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(
            source="internal_agent:job_event:abc-123:completed",
        )

        await handler.handle(job)

        # Verify dispatch uses original_source, not the internal_agent job_event source
        mock_source_dispatcher.dispatch_completed.assert_called_once_with(
            instance_id="test-instance-id",
            message_id="msg-123",
            source="slack-bot:TWS:U1",
            content="Processed message response",
            message_type="final",
        )

    @pytest.mark.asyncio
    async def test_internal_agent_job_event_skipped_when_no_original_source(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """internal_agent:job_event:* with no original_source is skipped (no infinite loop)."""
        mock_instance = MagicMock()
        mock_instance.instance_metadata = {}  # No original_source
        mock_instance.status = "running"
        mock_instance.waiting_for = 0
        mock_manager._instance_repository.get.return_value = mock_instance

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(
            source="internal_agent:job_event:abc-123:completed",
        )

        await handler.handle(job)

        # Verify dispatch was NOT called (no original_source to dispatch to)
        mock_source_dispatcher.dispatch_completed.assert_not_called()
        # But job should still complete
        mock_job_service.complete_job.assert_called_once()


# ── Test Class 3: TestRegularSourceDispatch ──────────────────────────────────────


class TestRegularSourceDispatch:
    """Tests for dispatching to regular (non-internal) sources."""

    @pytest.mark.asyncio
    async def test_telegram_source_dispatched_directly(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Regular telegram source goes directly to dispatch."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(source="telegram:832330949")

        await handler.handle(job)

        mock_source_dispatcher.dispatch_completed.assert_called_once_with(
            instance_id="test-instance-id",
            message_id="msg-123",
            source="telegram:832330949",
            content="Processed message response",
            message_type="final",
        )

    @pytest.mark.asyncio
    async def test_discord_source_dispatched_directly(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Regular discord source goes directly to dispatch."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(source="discord:channel-123")

        await handler.handle(job)

        mock_source_dispatcher.dispatch_completed.assert_called_once_with(
            instance_id="test-instance-id",
            message_id="msg-123",
            source="discord:channel-123",
            content="Processed message response",
            message_type="final",
        )

    @pytest.mark.asyncio
    async def test_api_source_dispatched_directly(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """API source goes directly to dispatch."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(source="api")

        await handler.handle(job)

        mock_source_dispatcher.dispatch_completed.assert_called_once_with(
            instance_id="test-instance-id",
            message_id="msg-123",
            source="api",
            content="Processed message response",
            message_type="final",
        )


# ── Test Class 4: TestDispatchErrorHandling ─────────────────────────────────────


class TestDispatchErrorHandling:
    """Tests for handling errors during dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_error_does_not_fail_job(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When dispatch_completed raises an exception, job should still complete."""
        mock_source_dispatcher.dispatch_completed.side_effect = Exception("Dispatch failed!")

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        # Should NOT raise - dispatch is best-effort
        await handler.handle(job)

        # Job should still be completed
        mock_job_service.complete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_error_is_logged(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher, caplog
    ):
        """When dispatch_completed raises, error should be logged."""
        import logging
        mock_source_dispatcher.dispatch_completed.side_effect = Exception("Dispatch failed!")

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        with caplog.at_level(logging.ERROR):
            await handler.handle(job)

        # Error should be logged
        assert any("Error dispatching to external source" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_dispatch_error_propagates_not_to_job_completion(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Verify that even after dispatch error, the job completion still happens."""
        call_order = []

        async def track_complete(*args, **kwargs):
            call_order.append("complete_job")

        async def track_dispatch(*args, **kwargs):
            call_order.append("dispatch")
            raise Exception("Dispatch failed!")

        mock_source_dispatcher.dispatch_completed.side_effect = track_dispatch
        mock_job_service.complete_job.side_effect = track_complete

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        await handler.handle(job)

        # complete_job should have been called
        assert "complete_job" in call_order


# ── Test Class 5: TestDispatchEdgeCases ────────────────────────────────────────


class TestDispatchEdgeCases:
    """Tests for edge cases in dispatch logic."""

    @pytest.mark.asyncio
    async def test_result_none_skips_dispatch(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When result is None, dispatch should be skipped."""
        mock_manager._process_message_with_tracking = AsyncMock(return_value=None)

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        await handler.handle(job)

        # Dispatch should NOT be called
        mock_source_dispatcher.dispatch_completed.assert_not_called()

    @pytest.mark.asyncio
    async def test_result_content_none_still_dispatches(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When result.content is None, dispatch should still be called with empty string."""
        mock_manager._process_message_with_tracking = AsyncMock(
            return_value=MessageResult(content=None, tool_calls=None)
        )

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        await handler.handle(job)

        # Dispatch should be called with content=""
        mock_source_dispatcher.dispatch_completed.assert_called_once()
        call_kwargs = mock_source_dispatcher.dispatch_completed.call_args.kwargs
        assert call_kwargs["content"] == ""

    @pytest.mark.asyncio
    async def test_result_content_empty_string_still_dispatches(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When result.content is empty string, dispatch should be called with empty string."""
        mock_manager._process_message_with_tracking = AsyncMock(
            return_value=MessageResult(content="", tool_calls=None)
        )

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        await handler.handle(job)

        # Dispatch should be called
        mock_source_dispatcher.dispatch_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_handler_does_not_crash_without_source_dispatcher(
        self, mock_manager, mock_job_service, mock_job_repo
    ):
        """When source_dispatcher is None, handler should not crash."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=None,
        )
        job = create_message_job()

        # Should not raise
        await handler.handle(job)

        # Job should complete
        mock_job_service.complete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_handler_does_not_crash_with_source_but_no_dispatcher(
        self, mock_manager, mock_job_service, mock_job_repo
    ):
        """When source exists but source_dispatcher is None, handler should not crash."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=None,
        )
        job = create_message_job(source="telegram:12345")

        # Should not raise
        await handler.handle(job)

        # Job should complete
        mock_job_service.complete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_works_with_none_message_id(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When message_id is None, dispatch should still work."""
        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(message_id=None)

        await handler.handle(job)

        # Dispatch should be called with message_id=None
        mock_source_dispatcher.dispatch_completed.assert_called_once()
        call_kwargs = mock_source_dispatcher.dispatch_completed.call_args.kwargs
        assert call_kwargs["message_id"] is None

    @pytest.mark.asyncio
    async def test_no_metadata_uses_default_source(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When job_metadata is None, defaults to source='api'."""
        job = create_message_job()
        job.job_metadata = None  # No metadata

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )

        await handler.handle(job)

        # Should still dispatch to default source
        mock_source_dispatcher.dispatch_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_source_skips_dispatch(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """When source is empty string, dispatch should be skipped."""
        job = create_message_job(source="")

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )

        await handler.handle(job)

        # Dispatch should NOT be called (empty source is falsy)
        mock_source_dispatcher.dispatch_completed.assert_not_called()


# ── Test Class 6: TestJobProcessorWiring ────────────────────────────────────────


class TestJobProcessorWiring:
    """Tests for JobProcessor correctly wiring source_dispatcher to MessageJobHandler."""

    @pytest.mark.asyncio
    async def test_setup_message_job_handler_passes_source_dispatcher(self):
        """Verify setup_message_job_handler passes instance_manager.source_dispatcher."""
        # Create mocks
        mock_instance_manager = MagicMock()
        mock_source_dispatcher = MagicMock()
        mock_instance_manager.source_dispatcher = mock_source_dispatcher

        mock_queue_service = MagicMock()
        mock_queue_service._repository = MagicMock()

        mock_project_repo = MagicMock()
        mock_queue_repo = MagicMock()

        # Create JobProcessor
        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
        )

        # Setup message job handler
        processor.setup_message_job_handler()

        # Verify handler was created with source_dispatcher
        assert processor._message_job_handler is not None
        assert processor._message_job_handler._source_dispatcher is mock_source_dispatcher

    @pytest.mark.asyncio
    async def test_setup_message_job_handler_is_idempotent(self):
        """Verify setup_message_job_handler can be called multiple times safely."""
        mock_instance_manager = MagicMock()
        mock_source_dispatcher = MagicMock()
        mock_instance_manager.source_dispatcher = mock_source_dispatcher

        mock_queue_service = MagicMock()
        mock_queue_service._repository = MagicMock()

        mock_project_repo = MagicMock()
        mock_queue_repo = MagicMock()

        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
        )

        # Call setup multiple times
        processor.setup_message_job_handler()
        first_handler = processor._message_job_handler

        processor.setup_message_job_handler()
        second_handler = processor._message_job_handler

        # Should be the same handler
        assert first_handler is second_handler

    @pytest.mark.asyncio
    async def test_setup_message_job_handler_sets_on_queue_service(self):
        """Verify setup_message_job_handler also sets handler on queue_service."""
        mock_instance_manager = MagicMock()
        mock_source_dispatcher = MagicMock()
        mock_instance_manager.source_dispatcher = mock_source_dispatcher

        mock_queue_service = MagicMock()
        mock_queue_service._repository = MagicMock()

        mock_project_repo = MagicMock()
        mock_queue_repo = MagicMock()

        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
        )

        processor.setup_message_job_handler()

        # Verify queue_service also has the handler
        assert mock_queue_service._message_job_handler is processor._message_job_handler

    @pytest.mark.asyncio
    async def test_source_dispatcher_can_be_none(self):
        """Verify source_dispatcher can be None without breaking setup."""
        mock_instance_manager = MagicMock()
        mock_instance_manager.source_dispatcher = None  # No dispatcher

        mock_queue_service = MagicMock()
        mock_queue_service._repository = MagicMock()

        mock_project_repo = MagicMock()
        mock_queue_repo = MagicMock()

        processor = JobProcessor(
            queue_service=mock_queue_service,
            instance_manager=mock_instance_manager,
            project_repo=mock_project_repo,
            queue_repo=mock_queue_repo,
        )

        # Should not raise
        processor.setup_message_job_handler()

        # Handler should still be created with None dispatcher
        assert processor._message_job_handler is not None
        assert processor._message_job_handler._source_dispatcher is None


# ── Additional Integration Tests ────────────────────────────────────────────────


class TestDispatchIntegration:
    """Integration-style tests for the full dispatch flow."""

    @pytest.mark.asyncio
    async def test_full_flow_with_telegram_source(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Test complete flow: message → process → dispatch to Telegram."""
        # Setup
        mock_instance = create_mock_instance("test-instance-id", "telegram:12345")
        mock_manager._instance_repository.get.return_value = mock_instance

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job(
            instance_id="test-instance-id",
            message_id="msg-abc",
            source="telegram:12345",
        )

        # Execute
        await handler.handle(job)

        # Verify complete flow
        mock_manager._process_message_with_tracking.assert_called_once()
        mock_source_dispatcher.dispatch_completed.assert_called_once()
        mock_job_service.complete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_flow_internal_report_routing(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Test complete flow: internal report → resolve original source → dispatch."""
        # Setup: instance was originally from Telegram
        mock_instance = create_mock_instance("child-instance", "telegram:55555")
        mock_manager._instance_repository.get.return_value = mock_instance

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        # Job came from internal completion report
        job = create_message_job(
            instance_id="child-instance",
            message_id="internal-msg-123",
            source="internal_report:child-completed",
        )

        # Execute
        await handler.handle(job)

        # Verify: resolved to original Telegram source
        mock_source_dispatcher.dispatch_completed.assert_called_once_with(
            instance_id="child-instance",
            message_id="internal-msg-123",
            source="telegram:55555",  # Resolved from metadata
            content="Processed message response",
            message_type="final",
        )
        mock_job_service.complete_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_with_tool_calls_result(
        self, mock_manager, mock_job_service, mock_job_repo, mock_source_dispatcher
    ):
        """Test dispatch works when result has tool_calls (not just content)."""
        mock_manager._process_message_with_tracking = AsyncMock(
            return_value=MessageResult(
                content="I'll help you with that.",
                tool_calls=[{"id": "call_123", "type": "function"}],
            )
        )

        handler = MessageJobHandler(
            manager=mock_manager,
            job_queue_service=mock_job_service,
            job_repository=mock_job_repo,
            source_dispatcher=mock_source_dispatcher,
        )
        job = create_message_job()

        await handler.handle(job)

        # Dispatch should be called with content
        mock_source_dispatcher.dispatch_completed.assert_called_once()
        call_kwargs = mock_source_dispatcher.dispatch_completed.call_args.kwargs
        assert call_kwargs["content"] == "I'll help you with that."
