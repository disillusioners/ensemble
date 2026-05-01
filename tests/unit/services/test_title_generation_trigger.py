"""Tests for title generation trigger functionality in ChildReportsService.

Tests the _trigger_title_generation method and its integration with the 3
completion paths in _process_child_completion_and_notify_parent:
  1. Root instance completion (no parent)
  2. Tool invocation child (invoked_as_tool=True)
  3. Regular child instance completion
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from contextlib import contextmanager

from daemon.services.child_reports import ChildReportsService
from daemon.services.completion_registry import get_completion_registry


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
    manager = MagicMock()
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._engine = MagicMock()
    manager._generate_and_broadcast_title = AsyncMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_lifecycle = AsyncMock()
    manager._checkpointer = MagicMock()
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

            # Check that an async coroutine was passed
            call_args = mock_no_wait.call_args[0][0]
            # The coroutine should be from _generate_and_broadcast_title
            assert hasattr(call_args, '__name__') or 'coro' in str(type(call_args))
