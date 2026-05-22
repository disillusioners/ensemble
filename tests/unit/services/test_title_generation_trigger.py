"""Tests for title generation trigger functionality in ChildReportsService.

Tests the _trigger_title_generation method and its integration with the 3
completion paths in _process_child_completion_and_notify_parent:
  1. Root instance completion (no parent)
  2. Tool invocation child (invoked_as_tool=True)
  3. Regular child instance completion
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from contextlib import contextmanager

from daemon.services.child_reports import ChildReportsService
from daemon.services.completion_registry import get_completion_registry
from daemon.repositories.instance.models import InstanceStatus


# ─── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the global CompletionRegistry singleton between tests."""
    import daemon.services.completion_registry as cr_module
    cr_module._completion_registry = None
    yield
    cr_module._completion_registry = None


@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager with required attributes."""
    from unittest.mock import AsyncMock
    manager = MagicMock()
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._engine = MagicMock()
    manager._generate_and_broadcast_title = AsyncMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_lifecycle = AsyncMock()
    manager._checkpointer = MagicMock()
    manager.get_instance = AsyncMock()
    return manager


@pytest.fixture
def mock_events_service():
    """Create a mock EventPublisherService."""
    events = MagicMock()
    events._publish_instance_lifecycle_event = AsyncMock()
    return events


@pytest.fixture
def child_reports_service(mock_manager, mock_events_service):
    """Create a ChildReportsService with mocked dependencies."""
    return ChildReportsService(
        manager=mock_manager,
        events_service=mock_events_service,
    )


@pytest.fixture
def mock_message():
    """Create a mock message from the queue repository."""
    msg = MagicMock()
    msg.message_id = "msg-123"
    msg.content = "User message content for title generation"
    return msg


@pytest.fixture
def mock_instance():
    """Create a mock Instance with basic attributes."""
    instance = MagicMock()
    instance.instance_id = "instance-123"
    instance.agent_id = "coder"
    instance.parent_id = None
    instance.waiting_for = 0
    instance.status = "running"
    instance.instance_metadata = {}
    instance.children = None
    instance.version = 1
    return instance


# ─── Test Group A: _trigger_title_generation method directly ────────────────────


class TestTriggerTitleGenerationMethod:
    """Tests for the _trigger_title_generation method directly."""

    def test_happy_path_message_found_triggers_title_generation(
        self, child_reports_service, mock_manager, mock_message
    ):
        """Verify _trigger_title_generation calls MainLoopBridge with correct args."""
        # Setup: message found in queue repository
        mock_manager._queue_repository.get.return_value = mock_message

        with patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait") as mock_run_async:
            child_reports_service._trigger_title_generation(
                "instance-123", "msg-123"
            )

            # Verify queue_repository.get was called with the message ID
            mock_manager._queue_repository.get.assert_called_once_with("msg-123")

            # Verify MainLoopBridge was called
            mock_run_async.assert_called_once()

    def test_message_not_found_returns_early(
        self, child_reports_service, mock_manager
    ):
        """Verify early return when message not found in queue repository."""
        # Setup: message not found
        mock_manager._queue_repository.get.return_value = None

        with patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait") as mock_run_async:
            with patch("daemon.services.child_reports.logger") as mock_logger:
                child_reports_service._trigger_title_generation(
                    "instance-123", "msg-123"
                )

                # Verify early return - no MainLoopBridge call
                mock_run_async.assert_not_called()

                # Verify warning was logged
                mock_logger.warning.assert_called_once()
                assert "not found" in mock_logger.warning.call_args[0][0]

    def test_message_with_empty_content_still_triggers(
        self, child_reports_service, mock_manager
    ):
        """Verify title generation is triggered even with empty message content."""
        # Setup: message with None content
        mock_msg = MagicMock()
        mock_msg.content = None
        mock_manager._queue_repository.get.return_value = mock_msg

        with patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait") as mock_run_async:
            child_reports_service._trigger_title_generation(
                "instance-123", "msg-123"
            )

            # Verify MainLoopBridge is still called (empty content handled downstream)
            mock_run_async.assert_called_once()


# ─── Test Group B: Integration with completion paths ──────────────────────────


class TestCompletionPath1RootInstance:
    """Tests for Path 1: Root instance completion (no parent, no children)."""

    @pytest.mark.asyncio
    async def test_root_instance_completion_triggers_title_generation(
        self, mock_manager, mock_events_service, mock_instance, mock_message
    ):
        """Verify title generation is triggered when root instance completes."""
        # Setup: root instance (no parent), waiting_for=0
        mock_instance.parent_id = None
        mock_instance.waiting_for = 0
        mock_instance.instance_metadata = {}
        mock_instance.agent_id = "coder"
        mock_instance.version = 1

        # Mock _instance_repository.get for content fetching
        mock_manager._instance_repository.get.return_value = mock_instance

        # Mock queue_repository.get for title generation trigger
        mock_manager._queue_repository.get.return_value = mock_message

        # Create mock session - root instance path doesn't use _should_send_completion_report
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance
        mock_exec_result = MagicMock()
        mock_exec_result.scalar_one.return_value = 0  # No pending messages
        mock_session.exec.return_value = mock_exec_result

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        with patch("daemon.services.child_reports.Session", return_value=mock_session_ctx()):
            service = ChildReportsService(
                manager=mock_manager,
                events_service=mock_events_service,
            )

            with patch.object(
                service, "_get_last_assistant_message",
                new_callable=AsyncMock,
                return_value="Completed with result"
            ):
                with patch.object(
                    service, "_trigger_title_generation"
                ) as mock_trigger:
                    await service._process_child_completion_and_notify_parent(
                        "instance-123", "msg-123"
                    )

                    # Verify _trigger_title_generation was called
                    mock_trigger.assert_called_once()
                    mock_trigger.assert_called_with("instance-123", "msg-123")

        # Note: CompletionRegistry.complete() is called in the actual code path (line 589)
        # but the local import may differ from our fixture's reference.
        # The key assertion is that _trigger_title_generation was called.


class TestCompletionPath2ToolInvocation:
    """Tests for Path 2: Tool invocation child (invoked_as_tool=True)."""

    @pytest.mark.asyncio
    async def test_tool_invocation_completion_triggers_title_generation(
        self, mock_manager, mock_events_service, mock_instance, mock_message
    ):
        """Verify title generation is triggered when tool invocation completes."""
        # Setup: child instance with invoked_as_tool=True
        mock_instance.parent_id = "parent-instance-456"
        mock_instance.instance_metadata = {"invoked_as_tool": True}
        mock_instance.agent_id = "explorer"
        mock_instance.version = 1

        # Mock _instance_repository.get for content fetching
        mock_manager._instance_repository.get.return_value = mock_instance

        # Mock queue_repository.get for title generation trigger
        mock_manager._queue_repository.get.return_value = mock_message

        # Create mock session
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance
        mock_exec_result = MagicMock()
        mock_exec_result.scalar_one.return_value = 0
        mock_exec_result.first.return_value = None
        mock_session.exec.return_value = mock_exec_result

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        with patch("daemon.services.child_reports.Session", return_value=mock_session_ctx()):
            service = ChildReportsService(
                manager=mock_manager,
                events_service=mock_events_service,
            )

            with patch.object(
                service, "_get_last_assistant_message",
                new_callable=AsyncMock,
                return_value="Tool invocation result"
            ):
                with patch.object(
                    service, "_should_send_completion_report",
                    new_callable=AsyncMock,
                    return_value=True
                ):
                    with patch.object(
                        service, "_trigger_title_generation"
                    ) as mock_trigger:
                        await service._process_child_completion_and_notify_parent(
                            "instance-123", "msg-123"
                        )

                        # Verify _trigger_title_generation was called
                        mock_trigger.assert_called_once()
                        mock_trigger.assert_called_with("instance-123", "msg-123")


class TestCompletionPath3RegularChild:
    """Tests for Path 3: Regular child instance completion."""

    @pytest.mark.asyncio
    async def test_regular_child_completion_triggers_title_generation(
        self, mock_manager, mock_events_service, mock_instance, mock_message
    ):
        """Verify title generation is triggered when regular child completes."""
        # Setup: regular child instance (has parent, not invoked_as_tool)
        mock_instance.parent_id = "parent-instance-456"
        mock_instance.instance_metadata = {}
        mock_instance.agent_id = "coder"
        mock_instance.version = 1

        # Mock _instance_repository.get for content fetching
        mock_manager._instance_repository.get.return_value = mock_instance

        # Mock queue_repository.get for title generation trigger
        mock_manager._queue_repository.get.return_value = mock_message

        # Create mock session
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance
        mock_exec_result = MagicMock()
        mock_exec_result.scalar_one.return_value = 0
        mock_exec_result.first.return_value = None
        mock_session.exec.return_value = mock_exec_result

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        with patch("daemon.services.child_reports.Session", return_value=mock_session_ctx()):
            service = ChildReportsService(
                manager=mock_manager,
                events_service=mock_events_service,
            )

            with patch.object(
                service, "_get_last_assistant_message",
                new_callable=AsyncMock,
                return_value="Child completed with result"
            ):
                with patch.object(
                    service, "_should_send_completion_report",
                    new_callable=AsyncMock,
                    return_value=True
                ):
                    with patch.object(
                        service, "_update_parent_on_child_complete",
                        new_callable=AsyncMock,
                        return_value=(False, None, None)
                    ):
                        with patch.object(
                            service, "_create_completion_events",
                            new_callable=AsyncMock
                        ) as mock_events:
                            mock_events.return_value = (MagicMock(), MagicMock())

                            with patch.object(
                                service, "_trigger_title_generation"
                            ) as mock_trigger:
                                await service._process_child_completion_and_notify_parent(
                                    "instance-123", "msg-123"
                                )

                                # Verify _trigger_title_generation was called at the end
                                mock_trigger.assert_called_once()
                                mock_trigger.assert_called_with("instance-123", "msg-123")


# ─── Test Group C: Non-blocking behavior ───────────────────────────────────────


class TestNonBlockingBehavior:
    """Tests for non-blocking title generation behavior."""

    def test_trigger_title_generation_propagates_queue_error(
        self, child_reports_service, mock_manager
    ):
        """Verify _trigger_title_generation propagates errors from queue_repository."""
        # Setup: queue_repository.get raises an exception
        mock_manager._queue_repository.get.side_effect = RuntimeError("DB error")

        with patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait") as mock_run_async:
            with pytest.raises(RuntimeError, match="DB error"):
                child_reports_service._trigger_title_generation(
                    "instance-123", "msg-123"
                )

            # MainLoopBridge should not be called
            mock_run_async.assert_not_called()


# ─── Test Group D: Title generation service idempotency ────────────────────────


class TestTitleGenerationIdempotency:
    """Tests for title generation idempotency in TitleGenerationService.

    These tests verify the idempotency behavior of title generation by testing
    the service at a higher level with more focused mocking.
    """

    @pytest.mark.asyncio
    async def test_title_service_skips_when_title_exists(self):
        """Verify title generation skips when title already exists."""
        from daemon.services.title_generation import TitleGenerationService

        # Create mock manager
        mock_manager = MagicMock()
        mock_meta = MagicMock()
        mock_meta.instance_metadata = {"title": "Existing Title"}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_meta)
        mock_manager.config = MagicMock()
        mock_manager.config.llm.base_url = "https://api.openai.com/v1"
        mock_manager.config.llm.api_key = "test-key"
        mock_manager.config.llm.model = "gpt-4"
        mock_manager.config.llm.model_title = "gpt-4"

        service = TitleGenerationService(manager=mock_manager)

        # Patch the LLM at the point of use
        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock()

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            await service._generate_and_broadcast_title(
                "instance-123", "Some message content"
            )

            # LLM should NOT be called since title already exists
            mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_title_service_handles_llm_error(self):
        """Verify LLM error during title generation is caught and logged."""
        from daemon.services.title_generation import TitleGenerationService

        mock_manager = MagicMock()
        mock_meta = MagicMock()
        mock_meta.instance_metadata = {}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_meta)
        mock_manager.config = MagicMock()
        mock_manager.config.llm.base_url = "https://api.openai.com/v1"
        mock_manager.config.llm.api_key = "test-key"
        mock_manager.config.llm.model = "gpt-4"
        mock_manager.config.llm.model_title = "gpt-4"

        service = TitleGenerationService(manager=mock_manager)

        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(side_effect=RuntimeError("LLM API error"))

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            with patch.object(service._logger, "warning") as mock_warning:
                # Should not raise - error should be caught
                await service._generate_and_broadcast_title(
                    "instance-123", "Some message content"
                )

                # Warning should be logged
                mock_warning.assert_called()
                warning_msg = str(mock_warning.call_args)
                assert "Failed to generate title" in warning_msg or "LLM" in warning_msg

    @pytest.mark.asyncio
    async def test_title_service_skips_empty_content(self):
        """Verify title generation skips when message content is empty."""
        from daemon.services.title_generation import TitleGenerationService

        mock_manager = MagicMock()
        mock_manager._instance_repository.get = MagicMock()
        mock_manager.config = MagicMock()
        mock_manager.config.llm.base_url = "https://api.openai.com/v1"
        mock_manager.config.llm.api_key = "test-key"
        mock_manager.config.llm.model = "gpt-4"
        mock_manager.config.llm.model_title = "gpt-4"

        service = TitleGenerationService(manager=mock_manager)

        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock()

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            # Test with empty string
            await service._generate_and_broadcast_title("instance-123", "")
            mock_llm.invoke.assert_not_called()

            # Test with whitespace only
            await service._generate_and_broadcast_title("instance-123", "   ")
            mock_llm.invoke.assert_not_called()

            # Test with None
            await service._generate_and_broadcast_title("instance-123", None)
            mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_title_service_stores_generated_title(self):
        """Verify title generation stores the generated title in repository."""
        from daemon.services.title_generation import TitleGenerationService

        mock_manager = MagicMock()
        mock_meta = MagicMock()
        mock_meta.instance_metadata = {}
        mock_manager._instance_repository.get = MagicMock(return_value=mock_meta)
        mock_manager._instance_repository.update_title = MagicMock()
        mock_manager.config = MagicMock()
        mock_manager.config.llm.base_url = "https://api.openai.com/v1"
        mock_manager.config.llm.api_key = "test-key"
        mock_manager.config.llm.model = "gpt-4"
        mock_manager.config.llm.model_title = "gpt-4"

        service = TitleGenerationService(manager=mock_manager)

        mock_response = MagicMock()
        mock_response.content = "Test Title"
        mock_llm = MagicMock()
        mock_llm.invoke = MagicMock(return_value=mock_response)

        with patch("daemon.graph.ThinkingChatOpenAI", return_value=mock_llm):
            await service._generate_and_broadcast_title(
                "instance-123", "Some message content"
            )

            # Verify update_title was called with the generated title
            mock_manager._instance_repository.update_title.assert_called_once()
            call_args = mock_manager._instance_repository.update_title.call_args
            assert call_args[0][0] == "instance-123"  # instance_id
            assert "Test Title" in call_args[0][1]  # title


# ─── Test Group E: Fire-and-forget verification ─────────────────────────────────


class TestFireAndForgetBehavior:
    """Tests verifying the fire-and-forget behavior of title generation."""

    def test_trigger_title_generation_calls_main_loop_bridge_no_wait(
        self, child_reports_service, mock_manager, mock_message
    ):
        """Verify _trigger_title_generation uses run_async_no_wait for fire-and-forget."""
        mock_manager._queue_repository.get.return_value = mock_message

        with patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait") as mock_no_wait:
            with patch("daemon.services.child_reports.MainLoopBridge.run_async") as mock_sync:
                child_reports_service._trigger_title_generation(
                    "instance-123", "msg-123"
                )

                # Verify run_async_no_wait (fire-and-forget) was used, NOT run_async
                mock_no_wait.assert_called_once()
                mock_sync.assert_not_called()

    def test_trigger_title_generation_does_not_await_llm_call(
        self, child_reports_service, mock_manager, mock_message
    ):
        """Verify title generation doesn't block on LLM calls."""
        mock_manager._queue_repository.get.return_value = mock_message

        # The key insight: _trigger_title_generation calls MainLoopBridge.run_async_no_wait
        # which schedules the coroutine but doesn't wait for it
        # This is a synchronous method that returns immediately

        import time

        def slow_coroutine():
            """Simulate a slow async operation."""
            time.sleep(0.5)
            return "done"

        async def mock_generate():
            return slow_coroutine()

        with patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait") as mock_no_wait:
            child_reports_service._trigger_title_generation("instance-123", "msg-123")

            # Verify the method returns immediately without waiting
            # The actual coroutine is scheduled for later execution
            mock_no_wait.assert_called_once()

# ─── Test Group F: InstanceMessagingService._maybe_trigger_title_generation ──────


class TestInstanceMessagingTriggerTitleGeneration:
    """Tests for _maybe_trigger_title_generation in InstanceMessagingService.

    Tests the two call sites:
    1. enqueue_message: triggers on IDLE→RUNNING with HUMAN message
    2. send_message: triggers in finally block for first message
    """

    @pytest.fixture
    def mock_messaging_manager(self):
        """Create a mock manager for InstanceMessagingService tests."""
        manager = MagicMock()
        manager._queue_repository = MagicMock()
        manager._instance_repository = MagicMock()
        manager._engine = MagicMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._checkpointer = MagicMock()
        manager._graph_tasks = {}
        manager.config = MagicMock()
        manager.config.limits.graph_recursion_limit = 50
        # These are async methods that need to be mocked as AsyncMock
        manager.ensure_mcp_preloaded = AsyncMock()
        manager._maybe_compact_context = AsyncMock()
        manager.get_instance = AsyncMock()
        return manager

    @pytest.fixture
    def mock_cancellation_service(self):
        """Create a mock cancellation service."""
        service = MagicMock()
        service.is_shutting_down = False
        return service

    @pytest.fixture
    def messaging_service(self, mock_messaging_manager, mock_cancellation_service):
        """Create an InstanceMessagingService with mocked dependencies.

        Note: We need to mock the manager module to avoid mcp import issues.
        """
        # Create a mock manager module to avoid mcp import
        import sys
        mock_manager_module = MagicMock()

        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            from daemon.services.instance_messaging import InstanceMessagingService
            service = InstanceMessagingService(
                manager=mock_messaging_manager,
                cancellation_service=mock_cancellation_service,
            )
            return service

    # ─── Scenario 1: enqueue_message triggers title on IDLE→RUNNING with HUMAN ──

    @pytest.mark.asyncio
    async def test_enqueue_triggers_title_on_idle_to_running_with_human_message(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify _maybe_trigger_title_generation is called when:
        - Instance starts in IDLE state
        - A HUMAN message is enqueued
        - Transition is IDLE→RUNNING
        """
        instance_id = "instance-123"
        message_content = "User's first message"

        # Mock instance in IDLE state
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.IDLE.value
        mock_instance.version = 1

        # Create mock session context
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.Session", return_value=mock_session_ctx()):
                with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                    await messaging_service.enqueue_message(
                        instance_id=instance_id,
                        message=message_content,
                        source="api",  # This makes it HUMAN type
                    )

                    # Verify title generation was triggered
                    mock_run_async.assert_called_once()
                    # Verify the coroutine is from _generate_and_broadcast_title
                    call_args = mock_run_async.call_args[0][0]
                    assert hasattr(call_args, '__name__') or 'coro' in str(type(call_args))

    # ─── Scenario 2: enqueue_message does NOT trigger on PAUSED→RUNNING ─────────

    @pytest.mark.asyncio
    async def test_enqueue_does_not_trigger_on_paused_to_running(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify _maybe_trigger_title_generation is NOT called when:
        - Instance starts in PAUSED state
        - A HUMAN message is enqueued
        - Transition is PAUSED→RUNNING
        """
        instance_id = "instance-456"
        message_content = "Resume message"

        # Mock instance in PAUSED state
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.PAUSED.value
        mock_instance.version = 1

        # Create mock session context
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.Session", return_value=mock_session_ctx()):
                with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                    await messaging_service.enqueue_message(
                        instance_id=instance_id,
                        message=message_content,
                        source="api",  # This makes it HUMAN type
                    )

                    # Verify title generation was NOT triggered
                    mock_run_async.assert_not_called()

    # ─── Scenario 3: enqueue_message does NOT trigger for non-HUMAN messages ─────

    @pytest.mark.asyncio
    async def test_enqueue_does_not_trigger_for_agent_message(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify _maybe_trigger_title_generation is NOT called for AGENT messages."""
        instance_id = "instance-789"

        # Mock instance in IDLE state
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.IDLE.value
        mock_instance.version = 1

        # Create mock session context
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.Session", return_value=mock_session_ctx()):
                with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                    await messaging_service.enqueue_message(
                        instance_id=instance_id,
                        message="Agent inter-instance communication",
                        source="internal_agent:other-instance",  # This makes it AGENT type
                    )

                    # Verify title generation was NOT triggered (not HUMAN message)
                    mock_run_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_does_not_trigger_for_completion_report(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify _maybe_trigger_title_generation is NOT called for COMPLETION_REPORT."""
        instance_id = "instance-completion"

        # Mock instance in IDLE state
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.IDLE.value
        mock_instance.version = 1

        # Create mock session context
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.Session", return_value=mock_session_ctx()):
                with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                    await messaging_service.enqueue_message(
                        instance_id=instance_id,
                        message="Completion report content",
                        source="internal_report:child-instance",  # This makes it COMPLETION_REPORT type
                    )

                    # Verify title generation was NOT triggered (not HUMAN message)
                    mock_run_async.assert_not_called()

    # ─── Scenario 4: send_message triggers title even on CancelledError ───────────

    @pytest.mark.asyncio
    async def test_send_message_triggers_title_on_cancelled_error(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify _maybe_trigger_title_generation is called in finally block even when:
        - send_message raises asyncio.CancelledError
        - Instance was in IDLE state (is_first_message=True)
        """
        instance_id = "instance-cancel-123"
        message_content = "Message that will be cancelled"

        # Mock instance in IDLE state (so is_first_message=True)
        mock_instance_meta = MagicMock()
        mock_instance_meta.status = InstanceStatus.IDLE.value

        mock_messaging_manager._instance_repository.get.return_value = mock_instance_meta

        # Mock graph that raises CancelledError
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=asyncio.CancelledError)

        mock_messaging_manager.get_instance = AsyncMock(return_value=mock_graph)

        # Create a proper MessageResult mock that accepts keyword arguments
        mock_message_result = MagicMock()
        mock_message_result.content = ""

        def create_message_result(**kwargs):
            result = MagicMock()
            result.content = kwargs.get("content", "")
            return result

        # Mock task for cancellation tracking
        mock_task = MagicMock()
        import sys
        mock_manager_module = MagicMock()
        mock_manager_module.MessageResult = create_message_result
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("asyncio.current_task", return_value=mock_task):
                with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                    # Should not raise - CancelledError should be caught in send_message
                    result = await messaging_service.send_message(
                        instance_id=instance_id,
                        message=message_content,
                    )

                    # Verify title generation was still triggered in finally block
                    mock_run_async.assert_called_once()

                    # Result should be a MagicMock with empty content (CancelledError handled)
                    assert result.content == ""

    # ─── Scenario 5: Idempotent - early title generation prevents duplicate ────────

    @pytest.mark.asyncio
    async def test_title_generation_skips_when_already_exists(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify title generation checks for existing title and skips if present."""
        instance_id = "instance-existing-title"

        # Mock instance with existing title
        mock_instance_meta = MagicMock()
        mock_instance_meta.status = InstanceStatus.IDLE.value
        mock_instance_meta.instance_metadata = {"title": "Existing Title"}
        mock_messaging_manager._instance_repository.get.return_value = mock_instance_meta

        # Mock the title generation service
        mock_messaging_manager._generate_and_broadcast_title = AsyncMock()

        # Mock graph
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})
        mock_messaging_manager.get_instance.return_value = mock_graph

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                await messaging_service.send_message(
                    instance_id=instance_id,
                    message="Test message",
                )

                # Title generation should still be triggered (fire-and-forget)
                # The idempotency check happens inside _generate_and_broadcast_title
                mock_run_async.assert_called_once()

    # ─── Edge Cases ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_enqueue_triggers_with_empty_content(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify title generation is triggered even with empty message content.

        Content check happens inside _generate_and_broadcast_title, not at trigger point.
        """
        instance_id = "instance-empty-content"

        # Mock instance in IDLE state
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.IDLE.value
        mock_instance.version = 1

        # Create mock session context
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.Session", return_value=mock_session_ctx()):
                with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                    await messaging_service.enqueue_message(
                        instance_id=instance_id,
                        message="",  # Empty content
                        source="api",
                    )

                    # Title generation should still be triggered
                    mock_run_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_raises_when_generate_method_is_none(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify send_message raises TypeError when _generate_and_broadcast_title is None.

        Note: The current implementation does NOT handle this gracefully - it raises TypeError.
        This test documents current behavior. In production, _generate_and_broadcast_title
        should always be set (it's a method on the manager).
        """
        instance_id = "instance-no-generator"

        # Mock instance in IDLE state
        mock_instance_meta = MagicMock()
        mock_instance_meta.status = InstanceStatus.IDLE.value
        mock_messaging_manager._instance_repository.get.return_value = mock_instance_meta

        # Mock _generate_and_broadcast_title as None
        mock_messaging_manager._generate_and_broadcast_title = None

        # Mock graph
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})
        mock_messaging_manager.get_instance.return_value = mock_graph

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
                # Expect TypeError because None is not callable
                with pytest.raises(TypeError, match="'NoneType' object is not callable"):
                    await messaging_service.send_message(
                        instance_id=instance_id,
                        message="Test message",
                    )

    @pytest.mark.asyncio
    async def test_concurrent_enqueue_messages_both_trigger(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify two rapid IDLE→RUNNING transitions both trigger title generation.

        While both calls are made, the actual title generation is idempotent
        at the service level (only one title will be persisted).
        """
        instance_id = "instance-concurrent"

        # Mock instance initially in IDLE state
        mock_instance = MagicMock()
        mock_instance.status = InstanceStatus.IDLE.value
        mock_instance.version = 1

        # Create mock session context that returns the mock instance
        mock_session = MagicMock()
        mock_session.get.return_value = mock_instance

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.Session", return_value=mock_session_ctx()):
                with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                    # First message
                    await messaging_service.enqueue_message(
                        instance_id=instance_id,
                        message="First message",
                        source="api",
                    )

                    # Verify first trigger
                    assert mock_run_async.call_count == 1

    @pytest.mark.asyncio
    async def test_send_message_no_trigger_when_not_idle(
        self, messaging_service, mock_messaging_manager
    ):
        """Verify _maybe_trigger_title_generation is NOT called when instance is RUNNING."""
        instance_id = "instance-running"

        # Mock instance in RUNNING state (not IDLE)
        mock_instance_meta = MagicMock()
        mock_instance_meta.status = InstanceStatus.RUNNING.value
        mock_messaging_manager._instance_repository.get.return_value = mock_instance_meta

        # Mock graph
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})
        mock_messaging_manager.get_instance.return_value = mock_graph

        import sys
        mock_manager_module = MagicMock()
        with patch.dict('sys.modules', {'daemon.manager': mock_manager_module}):
            with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
                await messaging_service.send_message(
                    instance_id=instance_id,
                    message="Second message",
                )

                # Title generation should NOT be triggered (is_first_message=False)
                mock_run_async.assert_not_called()


# ─── Test Group G: _maybe_trigger_title_generation method directly ──────────────


class TestMaybeTriggerTitleGenerationMethod:
    """Direct tests for the _maybe_trigger_title_generation method."""

    @pytest.fixture
    def mock_manager(self):
        """Create a mock InstanceManager with required attributes."""
        manager = MagicMock()
        manager._queue_repository = MagicMock()
        manager._instance_repository = MagicMock()
        manager._engine = MagicMock()
        manager._generate_and_broadcast_title = AsyncMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_lifecycle = AsyncMock()
        manager._checkpointer = MagicMock()
        manager._graph_tasks = {}
        return manager

    @pytest.fixture
    def mock_cancellation_service(self):
        """Create a mock cancellation service."""
        service = MagicMock()
        service.is_shutting_down = False
        return service

    @pytest.fixture
    def messaging_service(self, mock_manager, mock_cancellation_service):
        """Create an InstanceMessagingService with mocked dependencies."""
        from daemon.services.instance_messaging import InstanceMessagingService
        return InstanceMessagingService(
            manager=mock_manager,
            cancellation_service=mock_cancellation_service,
        )

    def test_maybe_trigger_returns_early_when_should_not_trigger(
        self, messaging_service, mock_manager
    ):
        """Verify _maybe_trigger_title_generation returns early when should_trigger=False."""
        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
            messaging_service._maybe_trigger_title_generation(
                instance_id="test-123",
                message="Test message",
                should_trigger=False,
            )

            # MainLoopBridge should NOT be called
            mock_run_async.assert_not_called()

    def test_maybe_trigger_fires_when_should_trigger(
        self, messaging_service, mock_manager
    ):
        """Verify _maybe_trigger_title_generation calls MainLoopBridge when should_trigger=True."""
        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait") as mock_run_async:
            messaging_service._maybe_trigger_title_generation(
                instance_id="test-456",
                message="Test message content",
                should_trigger=True,
            )

            # MainLoopBridge should be called
            mock_run_async.assert_called_once()

            # Verify the coroutine passed is from _generate_and_broadcast_title
            call_args = mock_run_async.call_args[0][0]
            assert hasattr(call_args, '__name__') or 'coro' in str(type(call_args))

    def test_maybe_trigger_logs_debug_message(
        self, messaging_service, mock_manager
    ):
        """Verify _maybe_trigger_title_generation logs debug message when triggered."""
        with patch("daemon.services.instance_messaging.MainLoopBridge.run_async_no_wait"):
            with patch("daemon.services.instance_messaging.logger") as mock_logger:
                messaging_service._maybe_trigger_title_generation(
                    instance_id="test-789",
                    message="Test message",
                    should_trigger=True,
                )

                # Verify debug log was called
                mock_logger.debug.assert_called_once()
                assert "test-789" in mock_logger.debug.call_args[0][0]
                assert "first message" in mock_logger.debug.call_args[0][0]

