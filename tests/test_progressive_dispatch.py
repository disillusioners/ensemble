"""Tests for the progressive message delivery feature.

This module tests the end-to-end progressive delivery behavior across
daemon/sources/dispatcher.py and daemon/manager.py, focusing on:
- dispatcher.dispatch_message() routing, skipping, and tracking
- dispatcher.dispatch_completed() dedup via _progressive_sent_sources
- manager._process_message_with_tracking() streaming loop content extraction
- Error handling and empty content guards
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from daemon.config import Config, LLMConfig, LimitsConfig, PersistenceConfig, DaemonConfig, AgentsConfig


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def mock_config():
    """Create a mock config for manager tests."""
    return Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7
        ),
        limits=LimitsConfig(
            max_instances=5,
            max_children_per_instance=3,
            instance_timeout_minutes=60,
            message_rate_limit=60
        ),
        persistence=PersistenceConfig(
            db_path=":memory:",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300
        ),
        daemon=DaemonConfig(host="0.0.0.0", port=8079),
        agents=AgentsConfig(directory="./agents")
    )


@pytest.fixture
def mock_checkpointer():
    """Create a mock checkpointer."""
    return Mock()


@pytest.fixture
def mock_prompt_cache():
    """Create a mock prompt cache."""
    return Mock()


@pytest.fixture
def mock_instance_repository():
    """Create a mock instance repository."""
    mock_repo = MagicMock()
    mock_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_repo.get.return_value = None
    mock_repo.list.return_value = ([], 0)
    return mock_repo


@pytest.fixture
def mock_adapter():
    """Mock adapter that always succeeds."""
    adapter = AsyncMock()
    adapter.send = AsyncMock(return_value=True)
    return adapter


@pytest.fixture
def failing_adapter():
    """Mock adapter that always fails."""
    adapter = AsyncMock()
    adapter.send = AsyncMock(return_value=False)
    return adapter


@pytest.fixture
def error_adapter():
    """Mock adapter that raises an exception."""
    adapter = AsyncMock()
    adapter.send = AsyncMock(side_effect=Exception("Adapter error"))
    return adapter


@pytest.fixture
def mock_registry(mock_adapter):
    """Mock registry with a telegram adapter."""
    registry = Mock()
    registry.get.return_value = mock_adapter
    return registry


@pytest.fixture
def dispatcher(mock_registry):
    """Create a ResponseDispatcher with mocked dependencies."""
    from daemon.sources.dispatcher import ResponseDispatcher
    disp = ResponseDispatcher(mock_registry, "test-dispatcher")
    return disp


# ==============================================================================
# Dispatcher: dispatch_message() routing and skipping
# ==============================================================================

@pytest.mark.asyncio
async def test_dispatch_message_routes_correctly(dispatcher, mock_adapter, mock_registry):
    """Test #1: dispatch_message() calls the right adapter's send_message() method."""
    await dispatcher.start()

    await dispatcher.dispatch_message("telegram:123456789", "Hello World")

    mock_adapter.send.assert_called_once()
    call_args = mock_adapter.send.call_args[0][0]
    assert call_args.external_user_id == "123456789"
    assert call_args.content == "Hello World"
    assert call_args.source_id == "telegram"
    assert call_args.message_type == "text"

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_skips_api_source(dispatcher, mock_adapter, mock_registry):
    """Test #2: dispatch_message() skips "api" source (no colon = internal)."""
    await dispatcher.start()

    # "api" has no colon, so treated as internal source
    await dispatcher.dispatch_message("api", "Hello World")

    # Adapter should NOT be called for internal sources
    mock_adapter.send.assert_not_called()

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_skips_internal_report_source(dispatcher, mock_adapter, mock_registry):
    """Test #3: dispatch_message() skips "internal_report:id" source (internal_ prefix)."""
    await dispatcher.start()

    await dispatcher.dispatch_message("internal_report:child123", "Hello World")

    # Adapter should NOT be called for internal sources
    mock_adapter.send.assert_not_called()

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_skips_internal_error_report_source(dispatcher, mock_adapter, mock_registry):
    """Test #4: dispatch_message() skips "internal_error_report:id" source (internal_ prefix)."""
    await dispatcher.start()

    await dispatcher.dispatch_message("internal_error_report:child123", "Hello World")

    # Adapter should NOT be called for internal sources
    mock_adapter.send.assert_not_called()

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_tracks_source_in_progressive_sent_sources(dispatcher, mock_adapter, mock_registry):
    """Test #5: After successful dispatch, source is in _progressive_sent_sources."""
    await dispatcher.start()

    source = "telegram:12345"
    assert source not in dispatcher._progressive_sent_sources

    await dispatcher.dispatch_message(source, "Progressive text")

    # Source should be tracked after successful send
    assert source in dispatcher._progressive_sent_sources

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_does_not_track_on_failure(dispatcher, mock_adapter, mock_registry):
    """Test #5b (extended): Source is NOT tracked when adapter returns False."""
    await dispatcher.start()

    # Make the adapter fail
    mock_adapter.send = AsyncMock(return_value=False)

    source = "telegram:12345"
    await dispatcher.dispatch_message(source, "Failed text")

    # Source should NOT be tracked on failure
    assert source not in dispatcher._progressive_sent_sources

    await dispatcher.stop()


# ==============================================================================
# Dispatcher: dispatch_completed() dedup via _progressive_sent_sources
# ==============================================================================

@pytest.mark.asyncio
async def test_dispatch_completed_skips_when_progressive_already_sent(dispatcher, mock_adapter, mock_registry):
    """Test #6: dispatch_completed() skips when source was sent progressively (dedup)."""
    await dispatcher.start()

    source = "telegram:12345"

    # Simulate progressive send
    await dispatcher.dispatch_message(source, "Progressive text")

    # Verify progressive was tracked
    assert source in dispatcher._progressive_sent_sources

    # Count adapter calls before dispatch_completed
    calls_before = mock_adapter.send.call_count

    # dispatch_completed should skip (source already tracked)
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-1",
        source=source,
        content="Final response"
    )

    # No new calls should have been made (skipped)
    assert mock_adapter.send.call_count == calls_before

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_completed_still_sends_when_not_progressive(dispatcher, mock_adapter, mock_registry):
    """Test #7: dispatch_completed() sends normally when source was NOT sent progressively."""
    await dispatcher.start()

    source = "telegram:67890"

    # dispatch_completed for this source should send normally (not in progressive set)
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-1",
        source=source,
        content="Final response for source"
    )

    mock_adapter.send.assert_called_once()
    call_args = mock_adapter.send.call_args[0][0]
    assert call_args.external_user_id == "67890"
    assert call_args.content == "Final response for source"

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_completed_empty_content_guard(dispatcher, mock_adapter, mock_registry):
    """Test #8: dispatch_completed() skips when content is empty/whitespace."""
    await dispatcher.start()

    source = "telegram:12345"

    # Empty string should be skipped
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-1",
        source=source,
        content=""
    )
    mock_adapter.send.assert_not_called()

    # Whitespace only should be skipped
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-2",
        source=source,
        content="   \t\n  "
    )
    mock_adapter.send.assert_not_called()

    # Non-empty content should still send
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-3",
        source=source,
        content="Actual content"
    )
    assert mock_adapter.send.call_count == 1

    await dispatcher.stop()


# ==============================================================================
# Dispatcher: _progressive_sent_sources cleanup
# ==============================================================================

@pytest.mark.asyncio
async def test_progressive_sent_sources_cleanup_after_dispatch_completed(dispatcher, mock_adapter, mock_registry):
    """Test #9: Source is removed from _progressive_sent_sources after dispatch_completed skips it.

    When dispatch_completed() skips a source (because progressive was already sent),
    it discards the source from the tracking set. This means the NEXT dispatch_completed
    call for the same source will send normally (since the source is no longer tracked).
    """
    await dispatcher.start()

    source = "telegram:12345"

    # Simulate progressive send (tracks the source)
    await dispatcher.dispatch_message(source, "Progressive text")
    assert source in dispatcher._progressive_sent_sources

    # First dispatch_completed skips (tracked) and discards source
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-1",
        source=source,
        content="Final content"
    )

    # Source should be removed from tracking set (discarded on skip)
    assert source not in dispatcher._progressive_sent_sources

    # Now dispatch_completed should send normally (source not in set anymore)
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-2",
        source=source,
        content="Another final content"
    )

    # Two total sends: progressive message + second dispatch_completed
    # First dispatch_completed was skipped (discarded and skipped)
    assert mock_adapter.send.call_count == 2

    await dispatcher.stop()


# ==============================================================================
# Dispatcher: Error handling in progressive dispatch
# ==============================================================================

@pytest.mark.asyncio
async def test_dispatch_message_handles_adapter_exception(dispatcher, mock_registry):
    """Test #10: Error in progressive dispatch is caught and logged, doesn't break execution."""
    error_adapter = AsyncMock()
    error_adapter.send = AsyncMock(side_effect=Exception("Adapter error"))
    mock_registry.get.return_value = error_adapter

    await dispatcher.start()

    # Should not raise - error is caught and logged internally
    await dispatcher.dispatch_message("telegram:12345", "Hello")

    # Source should NOT be tracked (since send failed with exception)
    assert "telegram:12345" not in dispatcher._progressive_sent_sources

    await dispatcher.stop()


# ==============================================================================
# Manager: Streaming loop content extraction - string content
# ==============================================================================

@pytest.mark.asyncio
async def test_manager_streaming_extracts_string_content(
    mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository
):
    """Test #11: Streaming loop extracts text from string content."""
    from daemon.manager import InstanceManager

    # Create a mock graph that yields an agent node message with string content
    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Streaming response text"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-stream-1"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repository
        manager.source_dispatcher = mock_dispatcher
        # Mock spawn_instance to avoid calling real lifecycle service
        manager.spawn_instance = Mock(return_value="test-instance")
        # Add mock graph to instances so get_instance can find it
        manager.instances["test-instance"] = (mock_graph, "agents/coder")

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Hello",
            message_id="test-msg-001",
            message_source="telegram:12345"
        )

        # dispatch_message should have been called with the string content
        mock_dispatcher.dispatch_message.assert_called()
        call_args = mock_dispatcher.dispatch_message.call_args
        assert call_args.kwargs['content'] == "Streaming response text"


# ==============================================================================
# Manager: Streaming loop content extraction - list content
# ==============================================================================

@pytest.mark.asyncio
async def test_manager_streaming_extracts_list_content(
    mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository
):
    """Test #12: Streaming loop extracts text from list content (multi-modal)."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = [{"type": "text", "text": "List content response"}]
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-list-1"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]), \
         patch('daemon.manager.parse_think_tags', return_value=("List content response", None)):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_dispatcher
            # Mock spawn_instance to avoid calling real lifecycle service
            manager.spawn_instance = Mock(return_value="test-instance")
            # Add mock graph to instances so get_instance can find it
            manager.instances["test-instance"] = (mock_graph, "agents/coder")

            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

            result = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )

            mock_dispatcher.dispatch_message.assert_called()
            call_args = mock_dispatcher.dispatch_message.call_args
            assert call_args.kwargs['content'] == "List content response"


@pytest.mark.asyncio
async def test_manager_streaming_mixed_list_content_only_text_dispatched(
    mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository
):
    """Test #13: Streaming loop handles list with mixed text and non-text blocks."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = [
        {"type": "text", "text": "Part one "},
        {"type": "image", "url": "http://example.com/image.png"},
        {"type": "text", "text": "Part two"}
    ]
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-mixed-1"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]), \
         patch('daemon.manager.parse_think_tags', return_value=("Part one  Part two", None)):

            manager = InstanceManager(mock_config)
            manager._instance_repository = mock_instance_repository
            manager.source_dispatcher = mock_dispatcher
            # Mock spawn_instance to avoid calling real lifecycle service
            manager.spawn_instance = Mock(return_value="test-instance")
            # Add mock graph to instances so get_instance can find it
            manager.instances["test-instance"] = (mock_graph, "agents/coder")

            instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

            result = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Hello",
                message_id="test-msg-001",
                message_source="telegram:12345"
            )

            # Should have been called with joined text (non-text blocks filtered)
            mock_dispatcher.dispatch_message.assert_called()
            call_args = mock_dispatcher.dispatch_message.call_args
            content = call_args.kwargs['content']
            assert "Part one" in content
            assert "Part two" in content
            # Image URL should not appear
            assert "http://example.com" not in content


# ==============================================================================
# Manager: Streaming deduplication by message ID
# ==============================================================================

@pytest.mark.asyncio
async def test_manager_streaming_deduplication_by_message_id(
    mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository
):
    """Test #14: Same message ID is not dispatched twice."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    # Two messages with the SAME ID
    msg1 = Mock()
    msg1.content = "First update"
    msg1.type = 'ai'
    msg1.tool_calls = []
    msg1.id = "msg-same-id"

    msg2 = Mock()
    msg2.content = "Second update"
    msg2.type = 'ai'
    msg2.tool_calls = []
    msg2.id = "msg-same-id"  # Same ID

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [msg1]}})
        yield ("updates", {"agent": {"messages": [msg2]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [msg1, msg2]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repository
        manager.source_dispatcher = mock_dispatcher
        # Mock spawn_instance to avoid calling real lifecycle service
        manager.spawn_instance = Mock(return_value="test-instance")
        # Add mock graph to instances so get_instance can find it
        manager.instances["test-instance"] = (mock_graph, "agents/coder")

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Hello",
            message_id="test-msg-001",
            message_source="telegram:12345"
        )

        # Should only dispatch ONCE (first message, second is deduped)
        mock_dispatcher.dispatch_message.assert_called_once()
        call_args = mock_dispatcher.dispatch_message.call_args
        assert call_args.kwargs['content'] == "First update"


@pytest.mark.asyncio
async def test_manager_streaming_multiple_messages_same_execution(
    mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository
):
    """Test #15: Multiple messages with different IDs are each dispatched separately."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    msg1 = Mock()
    msg1.content = "First message"
    msg1.type = 'ai'
    msg1.tool_calls = []
    msg1.id = "msg-unique-1"

    msg2 = Mock()
    msg2.content = "Second message"
    msg2.type = 'ai'
    msg2.tool_calls = []
    msg2.id = "msg-unique-2"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [msg1]}})
        yield ("updates", {"agent": {"messages": [msg2]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [msg1, msg2]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repository
        manager.source_dispatcher = mock_dispatcher
        # Mock spawn_instance to avoid calling real lifecycle service
        manager.spawn_instance = Mock(return_value="test-instance")
        # Add mock graph to instances so get_instance can find it
        manager.instances["test-instance"] = (mock_graph, "agents/coder")

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Hello",
            message_id="test-msg-001",
            message_source="telegram:12345"
        )

        # Should dispatch TWICE (once for each unique message)
        assert mock_dispatcher.dispatch_message.call_count == 2
        contents = [call.kwargs['content'] for call in mock_dispatcher.dispatch_message.call_args_list]
        assert "First message" in contents
        assert "Second message" in contents


# ==============================================================================
# Manager: Empty content from streaming
# ==============================================================================

@pytest.mark.asyncio
async def test_manager_streaming_empty_content_not_dispatched(
    mock_config, mock_checkpointer, mock_prompt_cache, mock_instance_repository
):
    """Test #16: Empty content from streaming should not be dispatched."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    # Message with empty content
    empty_msg = Mock()
    empty_msg.content = ""
    empty_msg.type = 'ai'
    empty_msg.tool_calls = []
    empty_msg.id = "msg-empty"

    # Message with whitespace-only content
    whitespace_msg = Mock()
    whitespace_msg.content = "   \t\n  "
    whitespace_msg.type = 'ai'
    whitespace_msg.tool_calls = []
    whitespace_msg.id = "msg-whitespace"

    # Valid message
    valid_msg = Mock()
    valid_msg.content = "Valid content"
    valid_msg.type = 'ai'
    valid_msg.tool_calls = []
    valid_msg.id = "msg-valid"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [empty_msg]}})
        yield ("updates", {"agent": {"messages": [whitespace_msg]}})
        yield ("updates", {"agent": {"messages": [valid_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [valid_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repository
        manager.source_dispatcher = mock_dispatcher
        # Mock spawn_instance to avoid calling real lifecycle service
        manager.spawn_instance = Mock(return_value="test-instance")
        # Add mock graph to instances so get_instance can find it
        manager.instances["test-instance"] = (mock_graph, "agents/coder")

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Hello",
            message_id="test-msg-001",
            message_source="telegram:12345"
        )

        # Only the valid message should have been dispatched
        mock_dispatcher.dispatch_message.assert_called_once()
        call_args = mock_dispatcher.dispatch_message.call_args
        assert call_args.kwargs['content'] == "Valid content"


# ==============================================================================
# Manager: Original source preservation for child completion reports
# ==============================================================================

@pytest.mark.asyncio
async def test_manager_stores_original_source_in_metadata(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #17: External source is stored in instance metadata as original_source."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Response"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-1"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Create mock instance repository that tracks set_metadata calls
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.return_value = None  # Initial get returns None
    mock_instance_repo.set_metadata = MagicMock()

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Hello",
            message_id="test-msg-001",
            message_source="telegram:123456789"
        )

        # Verify set_metadata was called with original_source
        mock_instance_repo.set_metadata.assert_called_with(
            instance_id, "original_source", "telegram:123456789"
        )


@pytest.mark.asyncio
async def test_manager_uses_original_source_for_internal_report(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #18: Internal report source uses original external source for dispatch."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Response after child completion"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-2"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Create mock instance repository that returns original_source in metadata
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {
        "original_source": "telegram:123456789"
    }
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.return_value = mock_instance_meta
    mock_instance_repo.set_metadata = MagicMock()

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        # Process with internal_report source
        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Child completed",
            message_id="test-msg-002",
            message_source="internal_report:child123:msg456"
        )

        # Verify dispatch was called with ORIGINAL source, not internal_report source
        mock_dispatcher.dispatch_message.assert_called()
        call_args = mock_dispatcher.dispatch_message.call_args
        assert call_args.kwargs['source'] == "telegram:123456789"
        assert call_args.kwargs['content'] == "Response after child completion"


@pytest.mark.asyncio
async def test_manager_skips_dispatch_when_no_original_source(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #19: Dispatch is skipped when internal report has no original_source."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Response that should not be dispatched"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-3"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Create mock instance repository with NO original_source
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {}  # Empty metadata
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.return_value = mock_instance_meta
    mock_instance_repo.set_metadata = MagicMock()

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        # Process with internal_report source but no original_source stored
        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Child completed",
            message_id="test-msg-003",
            message_source="internal_report:child123:msg456"
        )

        # Verify dispatch was NOT called (no original source to use)
        mock_dispatcher.dispatch_message.assert_not_called()


@pytest.mark.asyncio
async def test_manager_uses_original_source_for_internal_error_report(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #20: Internal error report also uses original external source for dispatch."""
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Response after error"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-4"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Create mock instance repository that returns original_source in metadata
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {
        "original_source": "discord:user_abc"
    }
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.return_value = mock_instance_meta
    mock_instance_repo.set_metadata = MagicMock()

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        # Process with internal_error_report source
        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Child error",
            message_id="test-msg-004",
            message_source="internal_error_report:child123"
        )

        # Verify dispatch was called with ORIGINAL source
        mock_dispatcher.dispatch_message.assert_called()
        call_args = mock_dispatcher.dispatch_message.call_args
        assert call_args.kwargs['source'] == "discord:user_abc"


# ==============================================================================
# CRITICAL FIXES: C1, C2, W1, W2 - Source Propagation Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_internal_agent_source_does_not_trigger_source_replacement(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #21 (C1): internal_agent:* does NOT trigger source replacement.
    
    internal_agent:* is agent-to-agent communication, NOT a completion report.
    The source should remain as internal_agent:* and not be replaced with original_source.
    """
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Response to internal agent message"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-internal-1"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Create mock instance repository with original_source but should NOT be used
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {
        "original_source": "telegram:original123"
    }
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.return_value = mock_instance_meta
    mock_instance_repo.set_metadata = MagicMock()

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        # Process with internal_agent source (should NOT be treated as completion report)
        result = await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Forward this to the coder agent",
            message_id="test-msg-agent",
            message_source="internal_agent:coder123"
        )

        # Verify dispatch was called with the internal_agent source, NOT original_source
        mock_dispatcher.dispatch_message.assert_called()
        call_args = mock_dispatcher.dispatch_message.call_args
        # CRITICAL: source should be internal_agent, not telegram:original123
        assert call_args.kwargs['source'] == "internal_agent:coder123"
        assert call_args.kwargs['content'] == "Response to internal agent message"


@pytest.mark.asyncio
async def test_source_inheritance_parent_to_child(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #22 (C2): Child inherits original_source from parent during spawn.
    
    When parent spawns a child, the child should inherit the parent's original_source.
    This ensures grandchildren also get the original telegram source.
    """
    from daemon.manager import InstanceManager

    mock_graph = Mock()
    mock_graph.invoke = Mock(return_value={"messages": []})
    mock_graph.ainvoke = Mock(return_value={"messages": []})

    # Use valid UUID formats for instance IDs
    parent_uuid = "11111111-1111-1111-1111-111111111111"
    child_uuid = "22222222-2222-2222-2222-222222222222"
    
    # Mock instance repository to track parent and child
    parent_instance_meta = MagicMock()
    parent_instance_meta.instance_metadata = {
        "original_source": "telegram:parent_chat_456"
    }
    parent_instance_meta.children = []
    
    child_instance_meta = MagicMock()
    child_instance_meta.instance_metadata = {}
    child_instance_meta.children = []
    
    # Return parent for parent queries, child for child queries
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id=child_uuid)
    mock_instance_repo.get.side_effect = lambda i: (
        parent_instance_meta if i == parent_uuid else child_instance_meta
    )
    mock_instance_repo.set_metadata = MagicMock()
    mock_instance_repo.count_children.return_value = 0
    mock_instance_repo.get_tree_root_id.return_value = parent_uuid

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        # Use valid UUIDs for instances
        manager.instances[parent_uuid] = (mock_graph, "agents/leader")

        # Spawn child with valid parent UUID
        child_id = manager.spawn_instance(
            agent_id="coder",
            instance_id=child_uuid,
            parent_id=parent_uuid
        )

        # Verify set_metadata was called to inherit original_source
        mock_instance_repo.set_metadata.assert_called_with(
            child_uuid, "original_source", "telegram:parent_chat_456"
        )


@pytest.mark.asyncio
async def test_write_once_guard_original_source(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #23 (W1): original_source is write-once, not overwritten by subsequent messages.
    
    First external message sets original_source=telegram:123, second external
    message with telegram:456 should NOT overwrite it.
    """
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Response"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-1"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # First call returns empty metadata (no original_source set yet)
    # Subsequent calls return the original_source that was set
    original_source_set = {"value": None}
    
    def get_side_effect(instance_id):
        meta = MagicMock()
        if original_source_set["value"] is None:
            meta.instance_metadata = {}
        else:
            meta.instance_metadata = {"original_source": original_source_set["value"]}
        return meta
    
    def set_metadata_side_effect(instance_id, key, value):
        if key == "original_source":
            original_source_set["value"] = value
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.side_effect = get_side_effect
    mock_instance_repo.set_metadata.side_effect = set_metadata_side_effect

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        # First message from telegram:123 - should SET original_source
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="First message",
            message_id="msg-001",
            message_source="telegram:123"
        )

        # Verify original_source was set to telegram:123
        assert original_source_set["value"] == "telegram:123"
        
        # Count how many times set_metadata was called
        first_call_count = mock_instance_repo.set_metadata.call_count
        
        # Reset the side effect to return the set value on next get
        call_count_at_second_msg = first_call_count

        # Second message from telegram:456 - should NOT overwrite original_source
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Second message",
            message_id="msg-002",
            message_source="telegram:456"
        )

        # Verify set_metadata was NOT called again (write-once)
        # It should have the same call count as before
        assert mock_instance_repo.set_metadata.call_count == call_count_at_second_msg


@pytest.mark.asyncio
async def test_integration_external_source_child_report_dispatch(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #24 (Integration): External msg stores source → child report triggers dispatch with original source.
    
    Full flow:
    1. External message (telegram:external_123) stores source in instance metadata
    2. Child instance is spawned (inherits source via C2)
    3. Child completion report triggers dispatch
    4. Dispatch uses the original telegram source, not internal_report
    """
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    # Response for initial external message
    ai_msg_initial = Mock()
    ai_msg_initial.content = "Processing your request"
    ai_msg_initial.type = 'ai'
    ai_msg_initial.tool_calls = []
    ai_msg_initial.id = "msg-initial"

    # Response after child completion
    ai_msg_completion = Mock()
    ai_msg_completion.content = "Child task completed successfully"
    ai_msg_completion.type = 'ai'
    ai_msg_completion.tool_calls = []
    ai_msg_completion.id = "msg-completion"

    call_count = [0]
    
    async def mock_astream(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield ("updates", {"agent": {"messages": [ai_msg_initial]}})
        else:
            yield ("updates", {"agent": {"messages": [ai_msg_completion]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg_initial]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Track metadata state
    metadata_state = {"original_source": None}
    
    def get_side_effect(instance_id):
        meta = MagicMock()
        if metadata_state["original_source"] is None:
            meta.instance_metadata = {}
        else:
            meta.instance_metadata = {"original_source": metadata_state["original_source"]}
        return meta
    
    def set_metadata_side_effect(instance_id, key, value):
        if key == "original_source":
            metadata_state["original_source"] = value
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.side_effect = get_side_effect
    mock_instance_repo.set_metadata.side_effect = set_metadata_side_effect

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        # Step 1: External message stores source
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Start the task",
            message_id="msg-ext-001",
            message_source="telegram:external_chat_789"
        )

        # Verify original_source was stored
        assert metadata_state["original_source"] == "telegram:external_chat_789"

        # Step 2 & 3: Process child completion report - should use original source
        mock_dispatcher.dispatch_message.reset_mock()
        mock_instance_repo.get.return_value.instance_metadata = {
            "original_source": "telegram:external_chat_789"
        }
        
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Child completed",
            message_id="msg-child-complete",
            message_source="internal_report:child123:msg456"
        )

        # Verify dispatch used the ORIGINAL source, not internal_report
        mock_dispatcher.dispatch_message.assert_called()
        call_args = mock_dispatcher.dispatch_message.call_args
        assert call_args.kwargs['source'] == "telegram:external_chat_789"
        assert "internal_report" not in call_args.kwargs['source']


# ==============================================================================
# Dispatcher: Source Narrowing Tests (C1 - internal_agent: is NOT internal_report)
# ==============================================================================

@pytest.mark.asyncio
async def test_dispatch_message_internal_agent_dispatches_normally(dispatcher, mock_adapter, mock_registry):
    """Test #25 (C1 - Dispatcher): internal_agent:* does NOT skip, dispatches normally.
    
    The fix narrowed the internal source check from ALL internal_* to ONLY
    internal_report:* and internal_error_report:*. This means internal_agent:*
    should be dispatched to the adapter, NOT skipped.
    """
    await dispatcher.start()

    # internal_agent should dispatch normally (NOT be skipped like internal_report)
    await dispatcher.dispatch_message("internal_agent:coder123", "Agent message content")

    # Adapter SHOULD be called - internal_agent is NOT internal_report or internal_error_report
    mock_adapter.send.assert_called_once()
    call_args = mock_adapter.send.call_args[0][0]
    assert call_args.external_user_id == "coder123"
    assert call_args.content == "Agent message content"
    assert call_args.source_id == "internal_agent"

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_internal_report_skips_and_retrieves_original_source(dispatcher, mock_adapter, mock_registry):
    """Test #26: internal_report:* skips dispatcher AND triggers source recovery in manager.
    
    When manager sees internal_report:*, it:
    1. Does NOT use internal_report as dispatch_source
    2. Retrieves original_source from metadata
    3. Uses original_source for dispatch
    
    This test verifies the dispatcher skips internal_report (matching behavior).
    """
    await dispatcher.start()

    # internal_report should be skipped (not dispatched to adapter)
    await dispatcher.dispatch_message("internal_report:child123:msg456", "Completion report")

    # Adapter should NOT be called - internal_report is internal
    mock_adapter.send.assert_not_called()

    await dispatcher.stop()


@pytest.mark.asyncio
async def test_internal_error_report_skips_and_retrieves_original_source(dispatcher, mock_adapter, mock_registry):
    """Test #27: internal_error_report:* skips dispatcher AND triggers source recovery.
    
    Same as internal_report test but for error reports.
    """
    await dispatcher.start()

    # internal_error_report should be skipped (not dispatched to adapter)
    await dispatcher.dispatch_message("internal_error_report:child123", "Error report")

    # Adapter should NOT be called - internal_error_report is internal
    mock_adapter.send.assert_not_called()

    await dispatcher.stop()


# ==============================================================================
# Manager: Warning Log Test (W2 - Warning instead of Debug for missing source)
# ==============================================================================

@pytest.mark.asyncio
async def test_manager_warns_when_original_source_not_found(
    mock_config, mock_checkpointer, mock_prompt_cache, caplog
):
    """Test #28 (W2): Warning logged when original_source not found in metadata.
    
    When an internal_report comes in but no original_source exists in metadata,
    a WARNING should be logged (not debug) to help debugging.
    """
    import logging
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Response that won't be dispatched"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-warning"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Create mock instance repository with NO original_source
    mock_instance_meta = MagicMock()
    mock_instance_meta.instance_metadata = {}  # Empty - no original_source
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.return_value = mock_instance_meta
    mock_instance_repo.set_metadata = MagicMock()

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher

        instance_id = manager.spawn_instance(agent_id="coder", instance_id="test-instance")

        # Process with internal_report but no original_source stored
        with caplog.at_level(logging.WARNING):
            result = await manager._process_message_with_tracking(
                instance_id=instance_id,
                message="Child completed but no source",
                message_id="test-msg-warning",
                message_source="internal_report:child123:msg456"
            )

        # Verify WARNING was logged about missing original_source
        assert any(
            "No original_source found" in record.message and record.levelno == logging.WARNING
            for record in caplog.records
        ), "Expected WARNING log about missing original_source"

        # Verify dispatch was NOT called (no source to use)
        mock_dispatcher.dispatch_message.assert_not_called()


# ==============================================================================
# Full Chain Integration Test: External → Store → Child Inherits → Child Report → Dispatch
# ==============================================================================

@pytest.mark.asyncio
async def test_full_chain_external_msg_to_telegram_after_child_completion(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #29 (Full Integration): End-to-end test of the fixed source propagation.
    
    Full scenario:
    1. External message from telegram:123 arrives
    2. original_source stored as telegram:123 in metadata
    3. Parent spawns child agent (child inherits telegram:123 via C2)
    4. Child sends internal_report: back to parent
    5. Parent retrieves original_source=telegram:123 from metadata
    6. Parent's subsequent AI messages dispatch to telegram:123
    
    This is the exact scenario that was broken before the fix.
    """
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    # Response for initial external message
    ai_msg_initial = Mock()
    ai_msg_initial.content = "Processing your request"
    ai_msg_initial.type = 'ai'
    ai_msg_initial.tool_calls = []
    ai_msg_initial.id = "msg-initial"

    # Response after child completion - THIS is what we need to verify dispatches
    ai_msg_after_child = Mock()
    ai_msg_after_child.content = "Child finished, here's the result to user"
    ai_msg_after_child.type = 'ai'
    ai_msg_after_child.tool_calls = []
    ai_msg_after_child.id = "msg-after-child"

    call_count = [0]
    
    async def mock_astream(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield ("updates", {"agent": {"messages": [ai_msg_initial]}})
        else:
            yield ("updates", {"agent": {"messages": [ai_msg_after_child]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg_initial]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Track metadata state for original_source
    metadata_state = {"original_source": None, "children": []}

    parent_uuid = "11111111-1111-1111-1111-111111111111"
    child_uuid = "22222222-2222-2222-2222-222222222222"

    def get_side_effect(instance_id):
        meta = MagicMock()
        if instance_id.startswith("22222222"):
            # Child instance - empty metadata initially
            meta.instance_metadata = {}
        else:
            # Parent instance - return stored state
            if metadata_state["original_source"] is None:
                meta.instance_metadata = {}
            else:
                meta.instance_metadata = {"original_source": metadata_state["original_source"]}
        meta.children = metadata_state["children"]
        return meta
    
    def set_metadata_side_effect(instance_id, key, value):
        if key == "original_source" and metadata_state["original_source"] is None:
            metadata_state["original_source"] = value

    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.side_effect = get_side_effect
    mock_instance_repo.set_metadata.side_effect = set_metadata_side_effect
    mock_instance_repo.count_children.return_value = 0
    mock_instance_repo.get_tree_root_id.return_value = parent_uuid

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher
        manager.instances[parent_uuid] = (mock_graph, "agents/leader")

        # Step 1: External message stores source
        await manager._process_message_with_tracking(
            instance_id=parent_uuid,
            message="Start the task",
            message_id="msg-ext-001",
            message_source="telegram:123"
        )

        # Verify original_source was stored
        assert metadata_state["original_source"] == "telegram:123"

        # Step 2: Spawn child (simulating what happens in real flow)
        child_id = manager.spawn_instance(
            agent_id="coder",
            instance_id=child_uuid,
            parent_id=parent_uuid
        )

        # Verify child inherited original_source (C2 fix)
        # The spawn should have called set_metadata on child with parent's original_source
        # Check that set_metadata was called for the child with original_source
        child_inherited_calls = [
            call for call in mock_instance_repo.set_metadata.call_args_list
            if call[0][0] == child_uuid and call[0][1] == "original_source"
        ]
        assert len(child_inherited_calls) > 0, "Child should inherit original_source from parent"
        assert child_inherited_calls[0][0][2] == "telegram:123"

        # Step 3: Child sends internal_report back to parent
        # Update mock to return original_source for parent
        mock_instance_repo.get.side_effect = lambda i: (
            MagicMock(
                instance_metadata={"original_source": "telegram:123"},
                children=[]
            ) if i == parent_uuid else MagicMock(instance_metadata={}, children=[])
        )
        
        mock_dispatcher.dispatch_message.reset_mock()
        
        await manager._process_message_with_tracking(
            instance_id=parent_uuid,
            message="Child completed",
            message_id="msg-child-complete",
            message_source=f"internal_report:{child_uuid}:msg456"
        )

        # Step 4: Verify dispatch used telegram:123 (NOT internal_report)
        mock_dispatcher.dispatch_message.assert_called()
        call_args = mock_dispatcher.dispatch_message.call_args
        assert call_args.kwargs['source'] == "telegram:123", \
            f"Expected source=telegram:123, got source={call_args.kwargs['source']}"
        assert "internal_report" not in call_args.kwargs['source'], \
            "Source should be telegram:123, not internal_report"


# ==============================================================================
# Source Inheritance: Grandchild inherits from grandparent (chain)
# ==============================================================================

@pytest.mark.asyncio
async def test_source_inheritance_grandchild_from_grandparent(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #30 (C2 Extended): Grandchild inherits original_source through chain.
    
    grandparent (telegram:xyz) → parent (inherits telegram:xyz) → child (inherits telegram:xyz)
    
    This ensures the source propagates correctly through multiple levels of spawning.
    """
    from daemon.manager import InstanceManager

    mock_graph = Mock()
    mock_graph.invoke = Mock(return_value={"messages": []})
    mock_graph.ainvoke = Mock(return_value={"messages": []})

    grandparent_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    parent_uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    grandchild_uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    
    metadata_state = {
        grandparent_uuid: {"original_source": "telegram:grandparent_chat"},
        parent_uuid: {},
        grandchild_uuid: {},
    }
    
    def get_side_effect(instance_id):
        meta = MagicMock()
        meta.instance_metadata = metadata_state.get(instance_id, {}).copy()
        meta.children = []
        return meta
    
    inheritance_calls = []
    
    def set_metadata_side_effect(instance_id, key, value):
        if key == "original_source":
            # Record the inheritance
            inheritance_calls.append({
                "child": instance_id,
                "source": value
            })
            # Update metadata state
            if instance_id in metadata_state:
                metadata_state[instance_id]["original_source"] = value
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock()
    mock_instance_repo.get.side_effect = get_side_effect
    mock_instance_repo.set_metadata.side_effect = set_metadata_side_effect
    mock_instance_repo.count_children.return_value = 0
    mock_instance_repo.get_tree_root_id.return_value = grandparent_uuid

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.instances[grandparent_uuid] = (mock_graph, "agents/leader")

        # Simulate grandparent received external message and stored original_source
        metadata_state[grandparent_uuid]["original_source"] = "telegram:grandparent_chat"

        # Parent spawns from grandparent
        manager.spawn_instance(
            agent_id="coder",
            instance_id=parent_uuid,
            parent_id=grandparent_uuid
        )

        # Grandchild spawns from parent
        manager.spawn_instance(
            agent_id="coder",
            instance_id=grandchild_uuid,
            parent_id=parent_uuid
        )

        # Verify inheritance chain
        # Parent should inherit from grandparent
        parent_inheritance = [c for c in inheritance_calls if c["child"] == parent_uuid]
        assert len(parent_inheritance) > 0, "Parent should inherit from grandparent"
        assert parent_inheritance[0]["source"] == "telegram:grandparent_chat"

        # Grandchild should inherit from parent (which has grandparent's source)
        grandchild_inheritance = [c for c in inheritance_calls if c["child"] == grandchild_uuid]
        assert len(grandchild_inheritance) > 0, "Grandchild should inherit from parent"
        assert grandchild_inheritance[0]["source"] == "telegram:grandparent_chat", \
            "Grandchild should inherit telegram:grandparent_chat (via parent)"


# ==============================================================================
# Write-Once Guard: Multiple external sources should not overwrite
# ==============================================================================

@pytest.mark.asyncio
async def test_write_once_guard_persists_through_multiple_external_messages(
    mock_config, mock_checkpointer, mock_prompt_cache
):
    """Test #31 (W1 Extended): Write-once guard persists across multiple messages.
    
    First message from source A sets original_source=A.
    Subsequent messages from sources B, C, D should NOT overwrite original_source=A.
    This ensures the first source is "sticky" throughout the instance lifetime.
    """
    from daemon.manager import InstanceManager

    mock_graph = Mock()

    ai_msg = Mock()
    ai_msg.content = "Response"
    ai_msg.type = 'ai'
    ai_msg.tool_calls = []
    ai_msg.id = "msg-1"

    async def mock_astream(*args, **kwargs):
        yield ("updates", {"agent": {"messages": [ai_msg]}})
        yield ("updates", {"agent": {"messages": []}})

    mock_graph.astream = mock_astream
    mock_graph.invoke = Mock(return_value={"messages": [ai_msg]})

    mock_dispatcher = AsyncMock()
    mock_dispatcher.dispatch_message = AsyncMock(return_value=None)

    # Track original_source with write-once semantics
    original_source = {"value": None}
    
    def get_side_effect(instance_id):
        meta = MagicMock()
        meta.instance_metadata = {"original_source": original_source["value"]} if original_source["value"] else {}
        meta.children = []
        return meta
    
    set_calls = []
    
    def set_metadata_side_effect(instance_id, key, value):
        if key == "original_source":
            set_calls.append(value)
            if original_source["value"] is None:
                original_source["value"] = value
    
    mock_instance_repo = MagicMock()
    mock_instance_repo.create.return_value = MagicMock(instance_id="test-instance")
    mock_instance_repo.get.side_effect = get_side_effect
    mock_instance_repo.set_metadata.side_effect = set_metadata_side_effect

    instance_id = "test-instance-123"

    with patch('daemon.manager.PromptCache', return_value=mock_prompt_cache), \
         patch('daemon.manager.build_instance_graph', return_value=mock_graph), \
         patch('daemon.manager.load_and_cache_prompt', return_value=("sys", 10)), \
         patch('daemon.manager.create_instance_tools', return_value=[]):

        manager = InstanceManager(mock_config)
        manager._instance_repository = mock_instance_repo
        manager.source_dispatcher = mock_dispatcher
        manager.instances[instance_id] = (mock_graph, "agents/leader")

        # First message from telegram:first_source
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="First message",
            message_id="msg-001",
            message_source="telegram:first_source"
        )
        
        assert original_source["value"] == "telegram:first_source"
        initial_set_count = len(set_calls)

        # Second message from discord:second_source - should NOT overwrite
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Second message",
            message_id="msg-002",
            message_source="discord:second_source"
        )
        
        # Should still be first source
        assert original_source["value"] == "telegram:first_source"
        assert len(set_calls) == initial_set_count, "Should NOT have called set_metadata again"

        # Third message from slack:third_source - should NOT overwrite
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="Third message",
            message_id="msg-003",
            message_source="slack:third_source"
        )
        
        # Should STILL be first source
        assert original_source["value"] == "telegram:first_source"
        assert len(set_calls) == initial_set_count, "Should NOT have called set_metadata again"

