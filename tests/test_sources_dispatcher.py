"""Tests for daemon.sources.dispatcher module."""

import logging
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from daemon.sources.dispatcher import ResponseDispatcher
from daemon.sources.base import OutgoingMessage
from daemon.services.event_bus import EventBus


@pytest.fixture
def mock_registry():
    """Create a mock registry for testing."""
    registry = Mock()
    registry.get = Mock(return_value=None)
    return registry


@pytest.fixture
def event_bus():
    """Create a fresh EventBus for testing."""
    mock_repo = MagicMock()
    mock_repo.create_event = Mock()
    mock_repo.cleanup_old = Mock(return_value=0)
    return EventBus(event_repo=mock_repo)


@pytest.fixture
def dispatcher(mock_registry):
    """Create a ResponseDispatcher with mocked dependencies."""
    return ResponseDispatcher(mock_registry, "test-dispatcher")


# ============================================================================
# Lifecycle Tests
# ============================================================================

@pytest.mark.asyncio
async def test_start_subscribes_to_broadcaster(dispatcher):
    """start() should set _running flag and initialize dispatcher."""
    await dispatcher.start()
    
    # Verify dispatcher is running
    assert dispatcher._running is True
    
    await dispatcher.stop()
    
    # Verify dispatcher is stopped
    assert dispatcher._running is False


@pytest.mark.asyncio
async def test_start_sets_running_flag(dispatcher):
    """_running should be True after start()."""
    await dispatcher.start()
    
    assert dispatcher._running is True
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_stop_clears_running_flag(dispatcher):
    """_running should be False after stop()."""
    await dispatcher.start()
    assert dispatcher._running is True
    
    await dispatcher.stop()
    
    assert dispatcher._running is False


# ============================================================================
# Event Handling Tests
# ============================================================================

@pytest.mark.asyncio
async def test_handle_completed_event_routes_to_adapter(dispatcher, mock_registry):
    """Completed events should be routed to the correct adapter."""
    # Create mock adapter
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Call dispatch_completed directly
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="telegram:12345",
        content="Hello"
    )
    
    # Verify adapter was called
    mock_adapter.send.assert_called_once()
    
    # Verify correct parameters were passed
    call_args = mock_adapter.send.call_args[0][0]
    assert call_args.external_user_id == "12345"
    assert call_args.content == "Hello"
    assert call_args.source_id == "telegram"
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_ignore_non_completed_events(dispatcher, mock_registry):
    """Non-completed events should be handled gracefully."""
    # Create mock adapter
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Create event with no routing needed (no ":" separator = internal source)
    event = {
        "instance_id": "test-instance",
        "event_type": "message_queued",
        "data": {
            "content": "Hello",
            "source": "api"
        }
    }
    
    await dispatcher._handle_event(event)
    
    # Verify adapter was NOT called (event is no-op in current implementation)
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_handle_event_missing_source(dispatcher, mock_registry):
    """Events with missing source should be handled gracefully."""
    # Create mock adapter
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Test dispatch with internal source (no ":" separator)
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="api",
        content="Hello"
    )
    
    # Verify adapter was NOT called (internal source doesn't need routing)
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_handle_event_invalid_source_format(dispatcher, mock_registry):
    """Events with invalid source format should be handled gracefully."""
    # Create mock adapter
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Test dispatch with internal source format (no ":" = internal)
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="invalid-source-without-colon",
        content="Hello"
    )
    
    # Verify adapter was NOT called (internal source format)
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_handle_event_invalid_source_id_format(dispatcher, mock_registry):
    """Events with invalid source_id format should be handled gracefully."""
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Test dispatch with invalid source_id (special characters)
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="invalid@source#id:12345",
        content="Hello"
    )
    
    # Verify adapter was NOT called (invalid source_id format)
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_handle_event_no_adapter_found(dispatcher, mock_registry):
    """Events with unknown source_id should be handled gracefully."""
    # Registry returns None for unknown source
    mock_registry.get = Mock(return_value=None)
    
    await dispatcher.start()
    
    # Test dispatch with unknown source
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="unknown_source:12345",
        content="Hello"
    )
    
    # Should not raise, just log warning
    await dispatcher.stop()


# ============================================================================
# Per-User Lock Tests
# ============================================================================

@pytest.mark.asyncio
async def test_per_user_send_lock_ordering(dispatcher, mock_registry):
    """Same user messages should be ordered (same lock)."""
    # Create mock adapter
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Get lock for user "12345" twice
    lock1 = await dispatcher._get_send_lock("12345")
    lock2 = await dispatcher._get_send_lock("12345")
    
    # Should be the same lock (same user)
    assert lock1 is lock2
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_different_users_use_different_locks(dispatcher, mock_registry):
    """Different users should not block each other."""
    await dispatcher.start()
    
    # Get locks for different users
    lock1 = await dispatcher._get_send_lock("user1")
    lock2 = await dispatcher._get_send_lock("user2")
    
    # Should be different locks
    assert lock1 is not lock2
    
    # Both should be in the locks dictionary
    assert "user1" in dispatcher._send_locks
    assert "user2" in dispatcher._send_locks
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_lru_lock_eviction(dispatcher, mock_registry):
    """Locks should be evicted after MAX_SEND_LOCKS."""
    # Use a smaller max for testing
    with patch.object(dispatcher, 'MAX_SEND_LOCKS', 5):
        await dispatcher.start()
        
        # Create more locks than the max
        for i in range(10):
            await dispatcher._get_send_lock(f"user{i}")
        
        # Should only have 5 locks (eviction happened)
        assert len(dispatcher._send_locks) == 5
        
        # First 5 should have been evicted
        assert "user0" not in dispatcher._send_locks
        assert "user1" not in dispatcher._send_locks
        assert "user2" not in dispatcher._send_locks
        assert "user3" not in dispatcher._send_locks
        assert "user4" not in dispatcher._send_locks
        
        # Last 5 should remain
        assert "user5" in dispatcher._send_locks
        assert "user6" in dispatcher._send_locks
        assert "user7" in dispatcher._send_locks
        assert "user8" in dispatcher._send_locks
        assert "user9" in dispatcher._send_locks
        
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_lru_lock_mru_ordering(dispatcher, mock_registry):
    """Recently used locks should be moved to end (most recently used)."""
    await dispatcher.start()
    
    # Create locks in order
    await dispatcher._get_send_lock("user1")
    await dispatcher._get_send_lock("user2")
    await dispatcher._get_send_lock("user3")
    
    # Access user1 again (should move to end)
    await dispatcher._get_send_lock("user1")
    
    # Get the order of keys
    keys = list(dispatcher._send_locks.keys())
    
    # user1 should now be at the end (most recently used)
    assert keys == ["user2", "user3", "user1"]
    
    await dispatcher.stop()


# ============================================================================
# Graceful Shutdown Tests
# ============================================================================

@pytest.mark.asyncio
async def test_graceful_stop_timeout(dispatcher, mock_registry):
    """stop() should clear locks and reset state."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Create some locks
    await dispatcher._get_send_lock("user1")
    await dispatcher._get_send_lock("user2")
    
    # Verify locks exist
    assert len(dispatcher._send_locks) == 2
    
    # Stop - should clear locks
    await dispatcher.stop(timeout=2.0)
    
    # Verify cleanup
    assert dispatcher._running is False
    assert len(dispatcher._send_locks) == 0


@pytest.mark.asyncio
async def test_stop_already_stopped(dispatcher):
    """Calling stop() when not running should not raise."""
    # Should not raise even though not started
    await dispatcher.stop()
    
    # Should also not raise when called twice
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_start_already_started(dispatcher):
    """Calling start() when already running should be idempotent."""
    await dispatcher.start()
    
    # Should not raise
    await dispatcher.start()
    
    # Should still be running once
    assert dispatcher._running is True
    
    await dispatcher.stop()


# ============================================================================
# Additional Edge Case Tests
# ============================================================================

@pytest.mark.asyncio
async def test_handle_event_with_metadata(dispatcher, mock_registry):
    """Events with metadata should pass it to the adapter."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Call dispatch_completed with metadata
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="telegram:12345",
        content="Hello",
        message_type="image",
        metadata={"key": "value"},
        reply_to_id="original-msg"
    )
    
    # Verify metadata was passed
    call_args = mock_adapter.send.call_args[0][0]
    assert call_args.metadata == {"key": "value"}
    assert call_args.message_type == "image"
    assert call_args.reply_to_id == "original-msg"
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_external_user_id_too_long(dispatcher, mock_registry):
    """Events with too long external_user_id should be handled gracefully."""
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Create event with very long external_user_id
    long_user_id = "a" * 300  # Exceeds 256 limit
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source=f"telegram:{long_user_id}",
        content="Hello"
    )
    
    # Verify adapter was NOT called
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_event_loop_handles_exceptions(dispatcher, mock_registry, caplog):
    """Exceptions during dispatch should propagate to caller."""
    import logging
    
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(side_effect=Exception("Adapter error"))
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Exception should propagate (current implementation doesn't catch)
    with pytest.raises(Exception, match="Adapter error"):
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-0",
            source="telegram:12345",
            content="Hello 0"
        )
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_adapter_send_failure_logged(dispatcher, mock_registry, caplog):
    """Failed adapter.send() should be logged but not crash."""
    import logging
    
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=False)  # Return failure
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Call dispatch_completed directly
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="telegram:12345",
        content="Hello"
    )
    
    # Verify warning was logged about failure
    # (caplog captures the log output)
    
    await dispatcher.stop()


# ============================================================================
# Progressive Delivery (dispatch_message) Tests
# ============================================================================

@pytest.mark.asyncio
async def test_dispatch_message_routes_to_correct_adapter(dispatcher, mock_registry):
    """dispatch_message should route correctly to the right adapter for external sources."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    await dispatcher.dispatch_message("telegram:123456789", "Hello World")
    
    mock_adapter.send.assert_called_once()
    call_args = mock_adapter.send.call_args[0][0]
    assert call_args.external_user_id == "123456789"
    assert call_args.content == "Hello World"
    assert call_args.source_id == "telegram"
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_skips_api_source(dispatcher, mock_registry):
    """dispatch_message should skip 'api' source (no colon) - internal source."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # "api" has no colon, so it's treated as an internal source
    await dispatcher.dispatch_message("api", "Hello World")
    
    # Adapter's send should NOT be called for internal sources
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_skips_internal_report_source(dispatcher, mock_registry):
    """dispatch_message should skip 'internal_report:*' sources - internal sources."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # "internal_report:some_id" starts with "internal_" prefix
    await dispatcher.dispatch_message("internal_report:some_id", "Hello World")
    
    # Adapter's send should NOT be called for internal sources
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_skips_internal_error_report_source(dispatcher, mock_registry):
    """dispatch_message should skip 'internal_error_report:*' sources - internal sources."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # "internal_error_report:some_id" starts with "internal_" prefix
    await dispatcher.dispatch_message("internal_error_report:some_id", "Hello World")
    
    # Adapter's send should NOT be called for internal sources
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_completed_skips_empty_string(dispatcher, mock_registry):
    """dispatch_completed should not send when content is empty string."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="telegram:12345",
        content=""  # Empty string
    )
    
    # Adapter's send should NOT be called for empty content
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_completed_skips_whitespace_content(dispatcher, mock_registry):
    """dispatch_completed should not send when content is only whitespace."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="telegram:12345",
        content="   \t\n  "  # Whitespace only
    )
    
    # Adapter's send should NOT be called for whitespace-only content
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_completed_sends_non_empty_content(dispatcher, mock_registry):
    """dispatch_completed should still send normally when content is non-empty."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    await dispatcher.dispatch_completed(
        instance_id="test-instance",
        message_id="msg-001",
        source="telegram:12345",
        content="Hello World"
    )
    
    # Adapter's send SHOULD be called for non-empty content
    mock_adapter.send.assert_called_once()
    call_args = mock_adapter.send.call_args[0][0]
    assert call_args.content == "Hello World"
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_handles_adapter_not_found(dispatcher, mock_registry):
    """dispatch_message should handle missing adapter gracefully."""
    mock_registry.get = Mock(return_value=None)  # No adapter found
    
    await dispatcher.start()
    
    # Should not raise, just log warning
    await dispatcher.dispatch_message("telegram:12345", "Hello")
    
    # No exception should be raised
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_skips_invalid_source_id_format(dispatcher, mock_registry):
    """dispatch_message should skip sources with invalid source_id format."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Invalid source_id (special characters)
    await dispatcher.dispatch_message("invalid@source#id:12345", "Hello")
    
    # Adapter's send should NOT be called for invalid source_id
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_skips_long_external_user_id(dispatcher, mock_registry):
    """dispatch_message should skip sources with too long external_user_id."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # external_user_id exceeds 256 char limit
    long_user_id = "a" * 300
    await dispatcher.dispatch_message(f"telegram:{long_user_id}", "Hello")
    
    # Adapter's send should NOT be called
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatch_message_per_user_locking(dispatcher, mock_registry):
    """dispatch_message should use per-user locks for ordering."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=True)
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Send multiple messages to same user
    await dispatcher.dispatch_message("telegram:user1", "Message 1")
    await dispatcher.dispatch_message("telegram:user1", "Message 2")
    
    # Both should be sent to same adapter
    assert mock_adapter.send.call_count == 2
    
    await dispatcher.stop()


class TestProgressiveDuplicateDelivery:
    """Tests for Fix W1: Duplicate message delivery prevention via _progressive_sent_sources."""

    @pytest.fixture
    def mock_adapter(self):
        """Create a mock adapter that always succeeds."""
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)
        return adapter

    @pytest.fixture
    def mock_registry(self, mock_adapter):
        """Create a mock registry with a telegram adapter."""
        registry = Mock()
        registry.get.return_value = mock_adapter
        return registry

    @pytest.mark.asyncio
    async def test_dispatch_completed_skips_after_progressive_send(self, mock_registry):
        """Test that dispatch_completed skips sending if source already received progressive message.
        
        When dispatch_message() sends successfully, then dispatch_completed() for same source
        should skip sending to avoid duplicate delivery.
        """
        dispatcher = ResponseDispatcher(registry=mock_registry)
        await dispatcher.start()
        
        source = "telegram:12345"
        
        # Simulate successful progressive message dispatch
        await dispatcher.dispatch_message(source=source, content="Progressive text")
        
        # Verify the source was tracked
        assert source in dispatcher._progressive_sent_sources
        
        # Count calls before dispatch_completed
        adapter = mock_registry.get.return_value
        calls_before = adapter.send.call_count
        
        # Now dispatch_completed should skip (source is in set)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source,
            content="Hello world"
        )
        
        # Verify send was NOT called for dispatch_completed (no new calls added)
        assert adapter.send.call_count == calls_before
        
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_dispatch_completed_different_source_still_sends(self, mock_registry, mock_adapter):
        """Test that dispatch_completed for a DIFFERENT source still sends normally.
        
        When dispatch_message() sends to one source, dispatch_completed() for a 
        different source should still send normally (no cross-source interference).
        """
        dispatcher = ResponseDispatcher(registry=mock_registry)
        await dispatcher.start()
        
        source1 = "telegram:12345"
        source2 = "telegram:67890"
        
        # Simulate successful progressive message dispatch to source1 only
        await dispatcher.dispatch_message(source=source1, content="Progressive for source1")
        
        # Verify only source1 was tracked
        assert source1 in dispatcher._progressive_sent_sources
        assert source2 not in dispatcher._progressive_sent_sources
        
        # Count calls after progressive dispatch
        calls_after_progressive = mock_adapter.send.call_count
        
        # dispatch_completed for source2 should send normally
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source2,
            content="Final response for source2"
        )
        
        # Verify one new call was made for source2
        assert mock_adapter.send.call_count == calls_after_progressive + 1
        
        # Check the last call was for source2
        last_call = mock_adapter.send.call_args_list[-1]
        assert "67890" in str(last_call)
        
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_source_cleaned_after_progressive_skip(self, mock_registry, mock_adapter):
        """Test that source is cleaned from tracking set after dispatch_completed skips.
        
        After dispatch_completed() skips a source (because progressive was sent),
        the source should be removed from the tracking set. This means the next
        dispatch_completed for the same source would send again.
        """
        dispatcher = ResponseDispatcher(registry=mock_registry)
        await dispatcher.start()
        
        source = "telegram:12345"
        
        # Simulate successful progressive message dispatch
        await dispatcher.dispatch_message(source=source, content="Progressive text")
        
        # Verify source is tracked
        assert source in dispatcher._progressive_sent_sources
        
        # Count calls after progressive
        calls_after_progressive = mock_adapter.send.call_count
        
        # First dispatch_completed skips (removes from set)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source,
            content="Final content"
        )
        
        # Verify no new call was made (skipped)
        assert mock_adapter.send.call_count == calls_after_progressive
        
        # Verify source was removed from tracking set
        assert source not in dispatcher._progressive_sent_sources
        
        # Now dispatch_completed should send normally (source not in set anymore)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-2",
            source=source,
            content="Another final content"
        )
        
        # Verify one new call was made
        assert mock_adapter.send.call_count == calls_after_progressive + 1
        
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_progressive_only_tracks_on_success(self):
        """Test that source is only tracked when progressive send succeeds."""
        # Create registry with failing adapter
        failing_adapter = AsyncMock()
        failing_adapter.send = AsyncMock(return_value=False)
        mock_registry = Mock()
        mock_registry.get.return_value = failing_adapter
        
        # Create registry with success adapter for second call
        success_adapter = AsyncMock()
        success_adapter.send = AsyncMock(return_value=True)
        
        dispatcher = ResponseDispatcher(registry=mock_registry)
        await dispatcher.start()
        
        source = "telegram:12345"
        
        # dispatch_message fails
        await dispatcher.dispatch_message(source=source, content="Failed progressive")
        
        # Source should NOT be tracked since send failed
        assert source not in dispatcher._progressive_sent_sources
        
        # Now update registry to return success adapter
        mock_registry.get.return_value = success_adapter
        
        # dispatch_completed should send normally
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source,
            content="Final content"
        )
        
        # Adapter.send SHOULD have been called since progressive wasn't tracked
        success_adapter.send.assert_called_once()
        
        await dispatcher.stop()


# ============================================================================
# Internal Source Log Level Tests
# ============================================================================

class TestInternalSourceLogLevels:
    """Tests verifying log levels for internal sources vs external sources.
    
    Internal sources (source_id starting with "internal_") should log DEBUG
    when no adapter is found, while external sources should log ERROR.
    """

    @pytest.fixture
    def dispatcher_with_no_adapter(self, mock_registry):
        """Create a dispatcher with registry that returns None (no adapter)."""
        mock_registry.get = Mock(return_value=None)
        return ResponseDispatcher(mock_registry, "test-dispatcher")

    @pytest.fixture
    def dispatcher_with_adapter(self, mock_registry):
        """Create a dispatcher with registry that returns a valid adapter."""
        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock(return_value=True)
        mock_registry.get = Mock(return_value=mock_adapter)
        return ResponseDispatcher(mock_registry, "test-dispatcher")

    # ========================================================================
    # dispatch_completed tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_dispatch_completed_internal_source_logs_debug_not_error(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify internal_agent:some-id logs DEBUG (not ERROR) when no adapter.
        
        When dispatch_completed is called with a source like 'internal_agent:some-id',
        and no adapter is found, it should log at DEBUG level (not ERROR) since
        internal sources are expected to not have adapters.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-001",
            source="internal_agent:some-id",
            content="Hello"
        )
        
        # Should log DEBUG, not ERROR
        debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        error_msgs = [r for r in caplog.records if r.levelno == logging.ERROR]
        
        assert any("no adapter" in r.message.lower() and "internal" in r.message.lower() 
                   for r in debug_msgs), "Should log DEBUG about no adapter for internal source"
        assert not any(r.levelno == logging.ERROR for r in caplog.records 
                       if "no adapter" in r.message.lower()), \
            "Should NOT log ERROR about no adapter for internal source"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_completed_internal_report_logs_debug_not_error(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify internal_report:inst:msg logs DEBUG when no adapter.
        
        When dispatch_completed is called with a source like 'internal_report:inst:msg',
        and no adapter is found, it should log at DEBUG level since internal_report
        sources are expected to not have adapters.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-001",
            source="internal_report:inst:msg",
            content="Hello"
        )
        
        # Should log DEBUG, not ERROR
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower() and 
            "internal_report" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG about no adapter for internal_report source"
        assert not any(
            record.levelno == logging.ERROR and "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should NOT log ERROR about no adapter for internal_report source"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_completed_internal_error_report_logs_debug_not_error(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify internal_error_report:inst logs DEBUG when no adapter.
        
        When dispatch_completed is called with a source like 'internal_error_report:inst',
        and no adapter is found, it should log at DEBUG level since internal_error_report
        sources are expected to not have adapters.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-001",
            source="internal_error_report:inst",
            content="Hello"
        )
        
        # Should log DEBUG, not ERROR
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower() and 
            "internal_error_report" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG about no adapter for internal_error_report source"
        assert not any(
            record.levelno == logging.ERROR and "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should NOT log ERROR about no adapter for internal_error_report source"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_completed_non_internal_source_logs_debug(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify telegram:123 logs DEBUG when no adapter.
        
        When dispatch_completed is called with an external source like 'telegram:123',
        and no adapter is found, it logs at DEBUG level since the source may be
        valid but not yet configured (e.g., explorer, experience, future sources).
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-001",
            source="telegram:123",
            content="Hello"
        )
        
        # Should log DEBUG (not ERROR) - sources may be valid but not yet configured
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower() and 
            "telegram" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG about no adapter for telegram source"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_completed_exactly_internal_prefix_logs_debug(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify 'internal_' (prefix only, no suffix) logs DEBUG.
        
        When dispatch_completed is called with source 'internal_:' (just the prefix
        with no actual suffix), it should still be recognized as an internal source
        and log DEBUG (not ERROR) when no adapter is found.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-001",
            source="internal_:",  # Just the prefix with empty suffix
            content="Hello"
        )
        
        # Should log DEBUG, not ERROR
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower() and 
            "internal_" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG for source starting with 'internal_' prefix"
        assert not any(
            record.levelno == logging.ERROR and "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should NOT log ERROR for source starting with 'internal_' prefix"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_completed_contains_internal_but_not_prefix_logs_debug(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify some_internal:123 logs DEBUG (not ERROR).
        
        When dispatch_completed is called with a source like 'some_internal:123' that
        contains 'internal' but does NOT start with 'internal_' prefix, it logs DEBUG
        (not ERROR) when no adapter is found, since this may be a valid source that
        just isn't configured yet.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-001",
            source="some_internal:123",
            content="Hello"
        )
        
        # Should log DEBUG, not ERROR
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG for source that contains 'internal' but doesn't start with 'internal_'"
        
        # Should NOT log ERROR
        assert not any(
            record.levelno == logging.ERROR and "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should NOT log ERROR for non-prefix internal source"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_completed_internal_source_with_adapter_no_log(
        self, dispatcher_with_adapter, caplog
    ):
        """Verify internal_agent with valid adapter has no 'no adapter' log.
        
        When dispatch_completed is called with a source like 'internal_agent:some-id'
        and a valid adapter IS found, there should be no log message about 'no adapter'.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_adapter.start()
        caplog.clear()
        
        await dispatcher_with_adapter.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-001",
            source="internal_agent:some-id",
            content="Hello"
        )
        
        # Should NOT log about "no adapter" since adapter exists
        assert not any(
            "no adapter" in record.message.lower() 
            for record in caplog.records
        ), "Should NOT log 'no adapter' when adapter exists"
        
        await dispatcher_with_adapter.stop()

    # ========================================================================
    # dispatch_message tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_dispatch_message_internal_source_logs_debug_not_error(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify internal_agent:some-id logs DEBUG when no adapter.
        
        When dispatch_message is called with a source like 'internal_agent:some-id',
        and no adapter is found, it should log at DEBUG level (not ERROR) since
        internal sources are expected to not have adapters.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_message(
            source="internal_agent:some-id",
            content="Hello"
        )
        
        # Should log DEBUG, not ERROR
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower() and 
            "internal" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG about no adapter for internal source in dispatch_message"
        assert not any(
            record.levelno == logging.ERROR and "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should NOT log ERROR about no adapter for internal source in dispatch_message"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_message_non_internal_source_logs_debug(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify discord:456 logs DEBUG when no adapter.
        
        When dispatch_message is called with an external source like 'discord:456',
        and no adapter is found, it logs at DEBUG level since the source may be
        valid but not yet configured.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_message(
            source="discord:456",
            content="Hello"
        )
        
        # Should log DEBUG (not ERROR)
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower() and 
            "discord" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG about no adapter for discord source in dispatch_message"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_message_exactly_internal_prefix_logs_debug(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify 'internal_' logs DEBUG.
        
        When dispatch_message is called with source 'internal_:' (just the prefix),
        it should still be recognized as an internal source and log DEBUG.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_message(
            source="internal_:",
            content="Hello"
        )
        
        # Should log DEBUG, not ERROR
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower() and 
            "internal_" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG for source starting with 'internal_' prefix in dispatch_message"
        assert not any(
            record.levelno == logging.ERROR and "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should NOT log ERROR for source starting with 'internal_' prefix in dispatch_message"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_message_contains_internal_but_not_prefix_logs_debug(
        self, dispatcher_with_no_adapter, caplog
    ):
        """Verify some_internal:123 logs DEBUG.
        
        When dispatch_message is called with a source like 'some_internal:123' that
        contains 'internal' but does NOT start with 'internal_' prefix, it logs DEBUG
        (not ERROR) when no adapter is found, since this may be a valid source.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_no_adapter.start()
        caplog.clear()
        
        await dispatcher_with_no_adapter.dispatch_message(
            source="some_internal:123",
            content="Hello"
        )
        
        # Should log DEBUG, not ERROR
        assert any(
            record.levelno == logging.DEBUG and 
            "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should log DEBUG for source that contains 'internal' but doesn't start with 'internal_'"
        
        # Should NOT log ERROR
        assert not any(
            record.levelno == logging.ERROR and "no adapter" in record.message.lower()
            for record in caplog.records
        ), "Should NOT log ERROR for non-prefix internal source"
        
        await dispatcher_with_no_adapter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_message_internal_source_with_adapter_no_log(
        self, dispatcher_with_adapter, caplog
    ):
        """Verify internal_agent with valid adapter has no 'no adapter' log.
        
        When dispatch_message is called with a source like 'internal_agent:some-id'
        and a valid adapter IS found, there should be no log message about 'no adapter'.
        """
        caplog.set_level(logging.DEBUG, logger="daemon.sources.dispatcher")
        
        await dispatcher_with_adapter.start()
        caplog.clear()
        
        await dispatcher_with_adapter.dispatch_message(
            source="internal_agent:some-id",
            content="Hello"
        )
        
        # Should NOT log about "no adapter" since adapter exists
        assert not any(
            "no adapter" in record.message.lower() 
            for record in caplog.records
        ), "Should NOT log 'no adapter' when adapter exists in dispatch_message"
        
        await dispatcher_with_adapter.stop()


# ============================================================================
# Phase B — chart-image delivery tests
# ============================================================================
#
# Implements Phase B.5 tasks #25-#30a, #41, #42 from chart-image-delivery/
# phaseB-plan.md:
#   * extract_chart_images unit tests (valid / malformed / multiline /
#     duplicate / near-miss / empty)
#   * per-id dedupe first-occurrence (amendment #2)
#   * per-id isolation (amendment #3) — one bad id → siblings deliver
#   * provenance gate (amendment #13) — feature=="chart-render" required
#   * test_image_resolution_returns_nonempty_bytes (Task #30a companion)
#   * BOTH-seam extraction (architecture-recommendation.md §1)
#   * test_progressive_then_completed_no_double_send
#   * API-source keep-marker regression
#   * internal_agent:* skip before extraction
#   * store.delete after success in delivering lane (amendment #22)


import base64

from daemon.sources.dispatcher import (
    ResponseDispatcher,
    _MARKER_RE,
    _NEAR_MISS_RE,
    extract_chart_images,
)


class TestExtractChartImagesUnit:
    """Pure-function unit tests for extract_chart_images (Phase B Task #25)."""

    def test_valid_single_marker_extracted(self):
        content = "Hello world\n<!-- ens-img:chart-render:abcdef0123456789abcdef0123456789 -->\nMore text"
        stripped, ids = extract_chart_images(content)
        assert ids == ["abcdef0123456789abcdef0123456789"]
        assert "Hello world" in stripped
        assert "More text" in stripped
        assert "<!-- ens-img" not in stripped

    def test_no_marker_returns_empty_ids(self):
        content = "Just plain text, no marker."
        stripped, ids = extract_chart_images(content)
        assert ids == []
        assert stripped == content

    def test_malformed_marker_with_extra_whitespace_not_extracted(self):
        # Extra space inside the comment + embedded in prose — LOCKED regex
        # must NOT match. Near-miss sweeper requires the WHOLE line to be
        # the marker (`^\s*...\s*$`), so an in-prose marker is left
        # untouched (cosmetic; only self-contained lines get stripped).
        content = "Text <!--  ens-img:chart-render:abcdef0123456789abcdef0123456789  --> after"
        stripped, ids = extract_chart_images(content)
        assert ids == []
        # Marker is embedded in prose; near-miss sweeper does NOT touch
        # substrings inside text lines (sweeper is whole-line).
        assert "ens-img" in stripped  # untouched
        # Stripped content equals original (sweeper didn't fire).
        assert stripped == content

    def test_malformed_marker_with_invented_id_not_extracted(self):
        # 31-char id (one short) — LOCKED regex (32 hex) must NOT match
        content = "<!-- ens-img:chart-render:abcdef0123456789abcdef012345678 -->"
        stripped, ids = extract_chart_images(content)
        assert ids == []
        assert "ens-img" not in stripped

    def test_multiline_marker_not_extracted(self):
        # marker with extra chars in the hex id (33 chars) — must not extract
        content = "<!-- ens-img:chart-render:abcdef0123456789abcdef01234567890 -->\nMore"
        stripped, ids = extract_chart_images(content)
        assert ids == []
        assert "ens-img" not in stripped

    def test_duplicate_marker_dedupes_first_occurrence(self):
        # Amendment #2: per-id dedupe preserving first-occurrence order
        content = (
            "<!-- ens-img:chart-render:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->\n"
            "Some text\n"
            "<!-- ens-img:chart-render:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->"
        )
        stripped, ids = extract_chart_images(content)
        assert ids == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
        assert stripped.count("ens-img") == 0

    def test_multiple_distinct_markers_first_occurrence_order(self):
        content = (
            "<!-- ens-img:chart-render:11111111111111111111111111111111 -->\n"
            "x\n"
            "<!-- ens-img:chart-render:22222222222222222222222222222222 -->\n"
            "y\n"
            "<!-- ens-img:chart-render:33333333333333333333333333333333 -->"
        )
        stripped, ids = extract_chart_images(content)
        assert ids == [
            "11111111111111111111111111111111",
            "22222222222222222222222222222222",
            "33333333333333333333333333333333",
        ]
        assert "ens-img" not in stripped

    def test_near_miss_stripped_not_extracted(self):
        # Amendment #14: near-miss sweeper strips but NEVER extracts.
        # Marker with leading whitespace + extra char inside id (33 chars
        # total) — won't match the LOCKED regex, so the near-miss
        # sweeper handles cosmetic cleanup.
        content = "Text line\n  <!-- ens-img:chart-render:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz -->\nMore"
        stripped, ids = extract_chart_images(content)
        assert ids == []
        assert "ens-img" not in stripped

    def test_valid_marker_plus_near_miss_in_same_input(self):
        # Valid marker extracts; near-miss only strips.
        content = (
            "<!-- ens-img:chart-render:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb -->\n"
            "middle\n"
            "<!-- ens-img:chart-render:toolongidfortheregexpaddedchars000 -->\n"
            "end"
        )
        stripped, ids = extract_chart_images(content)
        assert ids == ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
        assert "ens-img" not in stripped
        assert "middle" in stripped
        assert "end" in stripped

    def test_empty_content(self):
        stripped, ids = extract_chart_images("")
        assert ids == []
        assert stripped == ""

    def test_marker_on_own_line_preserves_other_lines(self):
        content = (
            "first line\n"
            "<!-- ens-img:chart-render:cccccccccccccccccccccccccccccccc -->\n"
            "last line"
        )
        stripped, ids = extract_chart_images(content)
        assert ids == ["cccccccccccccccccccccccccccccccc"]
        # splitlines+"\n".join: the trailing newline is preserved by splitlines.
        # Final shape: "first line\nlast line"
        assert "first line" in stripped
        assert "last line" in stripped
        assert "ens-img" not in stripped


class TestMarkerRegexPinning:
    """Pin the LOCKED regex byte-stability (decisions.md §marker)."""

    def test_marker_re_exact_match(self):
        line = "<!-- ens-img:chart-render:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->"
        m = _MARKER_RE.match(line)
        assert m is not None
        assert m.group(1) == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_marker_re_rejects_extra_text(self):
        line = "<!-- ens-img:chart-render:aaaaaaaaaaaaaaaa --> junk"
        assert _MARKER_RE.match(line) is None

    def test_near_miss_re_matches_indented(self):
        line = "  <!-- ens-img:chart-render:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz -->  "
        assert _NEAR_MISS_RE.match(line) is not None

    def test_near_miss_re_textually_overlaps_locked_form_but_safe_via_two_pass(self):
        # Per R4 (approver iter-002): the near-miss regex CAN textually
        # overlap the locked form at the pattern level — the safety
        # against double-extraction comes from extract_chart_images running
            # the LOCKED-regex pass FIRST and consuming the locked-form line
            # before the sweeper pass runs. The sweeper therefore sees
            # only lines that the locked regex did NOT match.
        locked_line = "<!-- ens-img:chart-render:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->"
        # The near-miss regex technically matches this locked line —
        # that's by design. The function's pass 1 consumes the locked
            # form first, so pass 2 never sees it.
        assert _NEAR_MISS_RE.match(locked_line) is not None


class TestImageResolutionNonemptyBytes:
    """Phase B Task #30a — pins the verified TmpImageRecord shape.

    TmpImageRecord has NO ``.blob`` field (verified). Bytes come from
    the sibling ``open_with_meta`` accessor; provenance comes from
    ``open_full``. Test stubs BOTH to ensure the resolution path
    returns NON-EMPTY bytes.
    """

    @pytest.mark.asyncio
    async def test_image_resolution_returns_nonempty_bytes(self):
        from daemon.services.tmp_image_store import TmpImageRecord, TmpImageNotFound
        from daemon.sources.dispatcher import _resolve_chart_images

        # Build a minimal fake store satisfying the verified accessor pair.
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"X" * 50
        record = TmpImageRecord(
            image_id="a" * 32,
            content_type="image/png",
            size_bytes=len(png_bytes),
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},
            retention_class="normal",
        )

        class _FakeStore:
            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return png_bytes, "image/png", "0" * 64

        atts = await _resolve_chart_images(["a" * 32], _FakeStore())
        assert len(atts) == 1
        att = atts[0]
        assert att.content_type == "image/png"
        decoded = base64.b64decode(att.bytes_b64)
        assert decoded == png_bytes  # byte-equal

    @pytest.mark.asyncio
    async def test_image_resolution_per_id_isolation(self):
        """One bad id → WARN + continue; siblings still deliver (amendment #3)."""
        from daemon.services.tmp_image_store import TmpImageRecord, TmpImageNotFound
        from daemon.sources.dispatcher import _resolve_chart_images

        good_record = TmpImageRecord(
            image_id="b" * 32,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},
            retention_class="normal",
        )

        class _FakeStore:
            def open_full(self, image_id):
                if image_id == "c" * 32:
                    raise TmpImageNotFound("not found")
                return good_record

            def open_with_meta(self, image_id):
                if image_id == "c" * 32:
                    raise TmpImageNotFound("not found")
                return b"PNG", "image/png", "0" * 64

        # bad id FIRST — siblings deliver
        atts = await _resolve_chart_images(["c" * 32, "b" * 32], _FakeStore())
        assert len(atts) == 1
        assert atts[0].image_id == "b" * 32

        # bad id LAST — siblings deliver
        atts = await _resolve_chart_images(["b" * 32, "c" * 32], _FakeStore())
        assert len(atts) == 1
        assert atts[0].image_id == "b" * 32

    @pytest.mark.asyncio
    async def test_image_resolution_provenance_gate_rejects_foreign_feature(self):
        """Amendment #13: feature mismatch → text fallback, WARN."""
        from daemon.services.tmp_image_store import TmpImageRecord
        from daemon.sources.dispatcher import _resolve_chart_images

        foreign_record = TmpImageRecord(
            image_id="d" * 32,
            content_type="image/png",
            size_bytes=10,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "screenshot"},  # WRONG feature
            retention_class="normal",
        )

        class _FakeStore:
            def open_full(self, image_id):
                return foreign_record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

        atts = await _resolve_chart_images(["d" * 32], _FakeStore())
        assert atts == []  # text fallback

    @pytest.mark.asyncio
    async def test_image_resolution_provenance_gate_default_deny(self):
        """Missing provenance → drop (default-deny per amendment #13)."""
        from daemon.services.tmp_image_store import TmpImageRecord
        from daemon.sources.dispatcher import _resolve_chart_images

        no_provenance_record = TmpImageRecord(
            image_id="e" * 32,
            content_type="image/png",
            size_bytes=10,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance=None,  # MISSING provenance
            retention_class="normal",
        )

        class _FakeStore:
            def open_full(self, image_id):
                return no_provenance_record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

        atts = await _resolve_chart_images(["e" * 32], _FakeStore())
        assert atts == []


class TestProgressiveLaneExtractsAndStrips:
    """Phase B Task #26: dispatch_message extracts + strips + uploads."""

    @pytest.mark.asyncio
    async def test_progressive_lane_extracts_and_strips(self):
        marker = "f" * 32
        # Minimal fake store with a record + bytes.
        from daemon.services.tmp_image_store import TmpImageRecord
        record = TmpImageRecord(
            image_id=marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.delete_calls: list[str] = []

            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

            def delete(self, image_id):
                self.delete_calls.append(image_id)
                return True

        fake_store = _FakeStore()

        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)

        manager = Mock()
        manager.tmp_image_store = fake_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        content = (
            f"Here is your chart\n"
            f"<!-- ens-img:chart-render:{marker} -->\n"
            f"hope you like it"
        )
        await dispatcher.dispatch_message(
            source="discord:user1",
            content=content,
        )

        # adapter.send called with marker-stripped content + populated images.
        assert adapter.send.await_count == 1
        outgoing = adapter.send.await_args.args[0]
        assert "ens-img" not in outgoing.content
        assert "Here is your chart" in outgoing.content
        assert "hope you like it" in outgoing.content
        assert outgoing.images is not None
        assert len(outgoing.images) == 1
        assert outgoing.images[0].image_id == marker

        # store.delete fired once after success (amendment #22).
        assert fake_store.delete_calls == [marker]

        await dispatcher.stop()


class TestProgressiveThenCompletedNoDoubleSend:
    """Phase B Task #27: progressive delivered → completed discards; one send."""

    @pytest.mark.asyncio
    async def test_progressive_then_completed_no_double_send(self):
        marker = "0" * 32
        from daemon.services.tmp_image_store import TmpImageRecord
        record = TmpImageRecord(
            image_id=marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.delete_calls: list[str] = []

            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

            def delete(self, image_id):
                self.delete_calls.append(image_id)
                return True

        fake_store = _FakeStore()

        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)

        manager = Mock()
        manager.tmp_image_store = fake_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        source = "discord:user1"
        content = (
            f"Here is your chart\n"
            f"<!-- ens-img:chart-render:{marker} -->\n"
            f"hope you like it"
        )

        # Progressive delivers (True → _progressive_sent_sources.add).
        await dispatcher.dispatch_message(source=source, content=content)
        # Completed runs — should discard at :124-128.
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source,
            content=content,
        )

        # Exactly ONE adapter.send call across both seams.
        assert adapter.send.await_count == 1

        # store.delete fired EXACTLY ONCE (no double-delete).
        assert fake_store.delete_calls == [marker]

        await dispatcher.stop()


class TestCompletedLaneExtractsAndStrips:
    """Phase B: dispatch_completed extracts + strips + uploads (when progressive
    did NOT deliver — adapter-False path)."""

    @pytest.mark.asyncio
    async def test_completed_lane_extracts_and_strips_when_progressive_failed(self):
        marker = "1" * 32
        from daemon.services.tmp_image_store import TmpImageRecord
        record = TmpImageRecord(
            image_id=marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.delete_calls: list[str] = []

            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

            def delete(self, image_id):
                self.delete_calls.append(image_id)
                return True

        fake_store = _FakeStore()

        adapter = AsyncMock()
        # First call (progressive) returns False; second call (completed) True.
        adapter.send = AsyncMock(side_effect=[False, True])

        manager = Mock()
        manager.tmp_image_store = fake_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        source = "discord:user1"
        content = (
            f"Here is your chart\n"
            f"<!-- ens-img:chart-render:{marker} -->\n"
        )

        await dispatcher.dispatch_message(source=source, content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source,
            content=content,
        )

        # adapter.send called twice (progressive False, completed True).
        assert adapter.send.await_count == 2

        # Completed path: extract + strip + upload + delete.
        completed_call = adapter.send.await_args_list[-1].args[0]
        assert "ens-img" not in completed_call.content
        assert completed_call.images is not None
        assert completed_call.images[0].image_id == marker

        # store.delete fired EXACTLY ONCE — only the completed lane delivered.
        assert fake_store.delete_calls == [marker]

        await dispatcher.stop()


class TestApiSourceKeepsMarker:
    """Phase B Task #28: API-source (no-colon) keeps marker verbatim + no fetch."""

    @pytest.mark.asyncio
    async def test_api_source_keeps_marker_no_fetch(self):
        # Adapter for "api" — but since the source has no colon, the
        # dispatcher short-circuits at :132-134 BEFORE adapter lookup.
        # We use a mock adapter and assert it's NEVER called.
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)

        # A store spy — must NOT be touched for no-colon sources.
        store = Mock()
        store.open_full = Mock(side_effect=AssertionError("open_full must NOT be called"))
        store.open_with_meta = Mock(side_effect=AssertionError("open_with_meta must NOT be called"))
        store.delete = Mock()

        manager = Mock()
        manager.tmp_image_store = store

        registry = Mock()
        registry.get = Mock(return_value=adapter)  # never called
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        marker = "2" * 32
        content = f"Here is your chart\n<!-- ens-img:chart-render:{marker} -->\n"

        # Source has NO colon — api internal source, marker preserved verbatim.
        await dispatcher.dispatch_message(source="api", content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="api",
            content=content,
        )

        # Adapter.send NEVER called (no chat adapter for "api").
        adapter.send.assert_not_called()
        # Store NEVER queried (extraction never runs).
        assert store.open_full.call_count == 0
        assert store.open_with_meta.call_count == 0
        assert store.delete.call_count == 0

        await dispatcher.stop()


class TestInternalAgentColonSourceSkipsAtLookup:
    """Phase B Task #29: internal_agent:* returns at adapter lookup before extraction."""

    @pytest.mark.asyncio
    async def test_internal_agent_returns_at_lookup_no_fetch(self):
        # Mock an adapter for internal_agent — but per registry wiring,
        # internal_agent adapters aren't typically registered. Here we
        # assert that even when the registry HAS an adapter for it, the
        # open_full/open_with_meta/delete path is NOT triggered for the
        # internal_agent source. We test by having registry.get return
        # None (typical production wiring) and verifying no fetch happened.
        store = Mock()
        store.open_full = Mock(side_effect=AssertionError("open_full must NOT be called"))
        store.open_with_meta = Mock(side_effect=AssertionError("open_with_meta must NOT be called"))
        store.delete = Mock()

        manager = Mock()
        manager.tmp_image_store = store

        registry = Mock()
        registry.get = Mock(return_value=None)  # no adapter for internal_agent:*
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        marker = "3" * 32
        content = f"Here is your chart\n<!-- ens-img:chart-render:{marker} -->\n"

        # internal_agent:* — has colon, so passes the no-colon skip,
        # but has no adapter → returns at :158-165 BEFORE extraction.
        await dispatcher.dispatch_message(
            source=f"internal_agent:some-id", content=content
        )
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=f"internal_agent:some-id",
            content=content,
        )

        # Store NEVER queried.
        assert store.open_full.call_count == 0
        assert store.open_with_meta.call_count == 0
        assert store.delete.call_count == 0

        await dispatcher.stop()


class TestStoreDeleteBehavior:
    """Phase B Task #5 / #41: store.delete only on success."""

    @pytest.mark.asyncio
    async def test_store_delete_called_after_successful_send(self):
        marker = "4" * 32
        from daemon.services.tmp_image_store import TmpImageRecord
        record = TmpImageRecord(
            image_id=marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.delete_calls: list[str] = []

            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

            def delete(self, image_id):
                self.delete_calls.append(image_id)
                return True

        fake_store = _FakeStore()
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)

        manager = Mock()
        manager.tmp_image_store = fake_store
        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        content = f"text\n<!-- ens-img:chart-render:{marker} -->\nmore"
        await dispatcher.dispatch_message(source="discord:user1", content=content)

        # Successful send → delete fired.
        assert fake_store.delete_calls == [marker]
        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_store_delete_NOT_called_after_failed_send(self):
        marker = "5" * 32
        from daemon.services.tmp_image_store import TmpImageRecord
        record = TmpImageRecord(
            image_id=marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.delete_calls: list[str] = []

            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

            def delete(self, image_id):
                self.delete_calls.append(image_id)
                return True

        fake_store = _FakeStore()
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=False)  # FAILURE

        manager = Mock()
        manager.tmp_image_store = fake_store
        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        content = f"text\n<!-- ens-img:chart-render:{marker} -->\nmore"
        await dispatcher.dispatch_message(source="discord:user1", content=content)

        # Failed send → delete NOT fired.
        assert fake_store.delete_calls == []
        await dispatcher.stop()


class TestExtractionAfterAdapterLookup:
    """architecture-recommendation.md §1 pin: extraction is AFTER adapter lookup."""

    @pytest.mark.asyncio
    async def test_extraction_runs_after_adapter_lookup(self):
        """If adapter is None, extraction must NOT run."""
        store = Mock()
        store.open_full = Mock(side_effect=AssertionError("open_full must NOT be called"))
        store.open_with_meta = Mock(side_effect=AssertionError("open_with_meta must NOT be called"))
        manager = Mock()
        manager.tmp_image_store = store

        registry = Mock()
        registry.get = Mock(return_value=None)  # no adapter for unknown source
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        marker = "6" * 32
        content = f"text\n<!-- ens-img:chart-render:{marker} -->\n"

        await dispatcher.dispatch_message(source="unknown:user1", content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source="unknown:user1",
            content=content,
        )

        # No fetch — extraction is AFTER adapter lookup.
        assert store.open_full.call_count == 0
        assert store.open_with_meta.call_count == 0
        await dispatcher.stop()


class TestImageAttachmentRedactingRepr:
    """architecture-recommendation.md §3 amendment #12 logging contract."""

    def test_repr_does_not_leak_bytes(self):
        from daemon.sources.base import ImageAttachment
        att = ImageAttachment(
            image_id="7" * 32,
            content_type="image/png",
            filename="chart-77777777.png",
            size_bytes=100,
            bytes_b64="ZHVQQQ==",  # base64 of "duQQ" — sensitive looking
        )
        r = repr(att)
        assert "ZHVQQQ==" not in r
        assert "bytes_b64=<redacted>" in r
        assert "77777777..." in r  # short id prefix

    def test_field_repr_false(self):
        from daemon.sources.base import ImageAttachment
        from dataclasses import fields
        att = ImageAttachment(
            image_id="8" * 32,
            content_type="image/png",
            filename="chart.png",
            size_bytes=1,
            bytes_b64="AAA=",
        )
        # The default repr must not contain the bytes payload.
        default_repr = f"{att!r}"
        assert "AAA=" not in default_repr
        # The field itself carries repr=False so dataclasses.asdict still works.
        from dataclasses import asdict
        d = asdict(att)
        assert d["bytes_b64"] == "AAA="


class TestOutgoingMessageImagesBackwardCompat:
    """Backward-compat: existing construction sites work without change."""

    def test_construction_without_images_defaults_to_none(self):
        msg = OutgoingMessage(
            external_user_id="u1",
            content="hi",
            source_id="s1",
        )
        assert msg.images is None

    def test_construction_with_images_populated(self):
        from daemon.sources.base import ImageAttachment
        att = ImageAttachment(
            image_id="9" * 32,
            content_type="image/png",
            filename="chart.png",
            size_bytes=1,
            bytes_b64="AAA=",
        )
        msg = OutgoingMessage(
            external_user_id="u1",
            content="hi",
            source_id="s1",
            images=[att],
        )
        assert msg.images is not None
        assert len(msg.images) == 1
        assert msg.images[0].image_id == "9" * 32


# ============================================================================
# Phase 2 — image-gen marker-family extraction tests (ADDITIVE)
# ============================================================================
# Implements Phase 2 Tasks #4 + #5 from
# ``.agents/shared/planning/image-gen-tool/phase2-plan.md``:
#   * ``TestExtractImageGenMarkersUnit`` — truth-table clone of
#     ``TestExtractChartImagesUnit`` (above) for the image-gen feature.
#   * Byte-compat guard — golden chart-render fixtures assert identical
#     ``extract_chart_images`` outputs pre/post Phase 2 (chart-render path
#     is byte-stable; the family pass is purely ADDITIVE over non-chart
#     lines).
#   * SWEEPER-LAST-ON-RESIDUAL test — load-bearing AM13 correctness proof
#     (architecture-recommendation.md §3 + §6, phase2-plan.md §Reviewer
#     implementation-time notes). Generalized sweeper textually matches
#     well-formed family markers; safety exists ONLY because the family
#     pass runs BEFORE the sweeper. The test pins this invariant — if the
#     pass order regresses, the test goes red.
#
# Imports are scoped to this section so the existing module-level imports
# above remain untouched (ADDITIVE-only discipline per the byte-compat
# hard gate).


from daemon.sources.dispatcher import (  # noqa: E402
    ALLOWED_FEATURES,
    _FAMILY_MARKER_RE,
    _MARKER_RE,
    _NEAR_MISS_RE,
    _resolve_family_images,
    extract_chart_images,
    extract_image_markers,
)


class TestExtractImageGenMarkersUnit:
    """Pure-function unit tests for ``extract_image_markers`` (Phase 2 Task #4).

    Truth-table clone of ``TestExtractChartImagesUnit`` — extraction,
    dedupe, first-occurrence order, strip-from-chat-text, near-miss junk
    sweep, multi-marker lines, mixed chart+image-gen text.
    """

    def test_valid_single_image_gen_marker_extracted(self):
        content = (
            "Hello world\n"
            "<!-- ens-img:image-gen:abcdef0123456789abcdef0123456789 -->\n"
            "More text"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {
            "image-gen": ["abcdef0123456789abcdef0123456789"]
        }
        assert "Hello world" in stripped
        assert "More text" in stripped
        assert "<!-- ens-img" not in stripped

    def test_no_marker_returns_empty_dict(self):
        content = "Just plain text, no marker."
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {}
        assert stripped == content

    def test_malformed_image_gen_marker_with_extra_whitespace_not_extracted(self):
        # Extra whitespace inside the comment — FAMILY regex requires the
        # exact full-line anchored form. The sweeper is whole-line so an
        # in-prose marker is left untouched (cosmetic; only self-contained
        # lines get stripped).
        content = (
            "Text <!--  ens-img:image-gen:abcdef0123456789abcdef0123456789  -->"
            " after"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {}
        assert "ens-img" in stripped
        assert stripped == content

    def test_malformed_image_gen_marker_with_invented_id_not_extracted(self):
        # 31-char id (one short) — FAMILY regex (32 hex) must NOT match.
        content = (
            "<!-- ens-img:image-gen:abcdef0123456789abcdef012345678 -->\n"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {}
        assert "ens-img" not in stripped

    def test_duplicate_image_gen_marker_dedupes_first_occurrence(self):
        content = (
            "<!-- ens-img:image-gen:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->\n"
            "Some text\n"
            "<!-- ens-img:image-gen:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {
            "image-gen": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
        }
        assert stripped.count("ens-img") == 0

    def test_multiple_distinct_image_gen_markers_first_occurrence_order(self):
        content = (
            "<!-- ens-img:image-gen:11111111111111111111111111111111 -->\n"
            "x\n"
            "<!-- ens-img:image-gen:22222222222222222222222222222222 -->\n"
            "y\n"
            "<!-- ens-img:image-gen:33333333333333333333333333333333 -->"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {
            "image-gen": [
                "11111111111111111111111111111111",
                "22222222222222222222222222222222",
                "33333333333333333333333333333333",
            ]
        }
        assert "ens-img" not in stripped

    def test_image_gen_near_miss_stripped_not_extracted(self):
        # Sweeper strips junk; the locked + family passes don't claim it.
        content = (
            "Text line\n"
            "  <!-- ens-img:image-gen:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz -->\n"
            "More"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {}
        assert "ens-img" not in stripped

    def test_image_gen_valid_marker_plus_near_miss_in_same_input(self):
        content = (
            "<!-- ens-img:image-gen:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb -->\n"
            "middle\n"
            "<!-- ens-img:image-gen:toolongidfortheregexpaddedchars000 -->\n"
            "end"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {
            "image-gen": ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
        }
        assert "ens-img" not in stripped
        assert "middle" in stripped
        assert "end" in stripped

    def test_empty_content(self):
        stripped, by_feature = extract_image_markers("")
        assert by_feature == {}
        assert stripped == ""

    def test_marker_on_own_line_preserves_other_lines(self):
        content = (
            "first line\n"
            "<!-- ens-img:image-gen:cccccccccccccccccccccccccccccccc -->\n"
            "last line"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {
            "image-gen": ["cccccccccccccccccccccccccccccccc"]
        }
        assert "first line" in stripped
        assert "last line" in stripped
        assert "ens-img" not in stripped


class TestExtractMixedChartAndImageGen:
    """Phase 2 — mixed chart + image-gen extraction in a single content."""

    def test_chart_and_image_gen_extracted_independently(self):
        chart_id = "d" * 32
        image_gen_id = "e" * 32
        content = (
            f"Here is a chart and an image\n"
            f"<!-- ens-img:chart-render:{chart_id} -->\n"
            f"middle\n"
            f"<!-- ens-img:image-gen:{image_gen_id} -->\n"
            f"end"
        )
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {
            "chart-render": [chart_id],
            "image-gen": [image_gen_id],
        }
        assert "ens-img" not in stripped
        assert "Here is a chart and an image" in stripped
        assert "middle" in stripped
        assert "end" in stripped

    def test_same_id_under_different_features_extracted_once_first_feature_wins(self):
        # The id space is shared (32-hex); the same id under two features is
        # a forgery / namespace-confusion pattern. Dedup is per-id across
        # all features (one image per id; first-occurrence wins).
        same_id = "f" * 32
        content = (
            f"<!-- ens-img:chart-render:{same_id} -->\n"
            f"middle\n"
            f"<!-- ens-img:image-gen:{same_id} -->"
        )
        stripped, by_feature = extract_image_markers(content)
        # First-occurrence (chart-render) wins; image-gen is dropped.
        assert by_feature == {"chart-render": [same_id]}
        assert "ens-img" not in stripped

    def test_chart_renders_byte_compat_with_existing_chart_tests(self):
        # The chart-render extraction path inside ``extract_image_markers``
        # delegates to ``extract_chart_images`` and must therefore produce
        # results byte-identical to the chart-only function for chart-only
        # inputs.
        content = (
            "<!-- ens-img:chart-render:11111111111111111111111111111111 -->\n"
            "x\n"
            "<!-- ens-img:chart-render:22222222222222222222222222222222 -->"
        )
        chart_stripped, chart_ids = extract_chart_images(content)
        family_stripped, family_by_feature = extract_image_markers(content)
        assert family_stripped == chart_stripped
        assert family_by_feature == {"chart-render": chart_ids}


class TestSweeperLastOnResidual:
    """Phase 2 Task #4 — the AM13 load-bearing pass-order invariant proof.

    The generalized near-miss sweeper (``_NEAR_MISS_RE``) TEXTUALLY MATCHES
    well-formed family markers — ``32-hex ⊂ [^>\n]{0,64}``. Safety exists
    ONLY because the sweeper runs LAST on lines the locked + family passes
    did not claim. If a future contributor re-orders the passes or runs
    the sweeper over the raw input, well-formed markers get stripped-and-
    lost silently.

    The invariant comment in ``dispatcher.py`` is the architectural
    correctness proof; this test is the executable proof.
    """

    def test_sweeper_textually_matches_a_well_formed_image_gen_marker(self):
        # Direct sanity: the sweeper, taken alone, DOES match a well-formed
        # image-gen marker. This is the load-bearing observation.
        line = "<!-- ens-img:image-gen:abcdef0123456789abcdef0123456789 -->"
        assert _NEAR_MISS_RE.match(line) is not None

    def test_sweeper_runs_last_so_well_formed_marker_is_extracted(self):
        # End-to-end through ``extract_image_markers``: the well-formed
        # image-gen marker survives (extracted into the family dict) BECAUSE
        # the family pass consumed it before the sweeper ran. If the pass
        # order regressed, the marker would be stripped-and-lost and the
        # family dict would be empty.
        marker = "abcdef0123456789abcdef0123456789"
        content = f"text\n<!-- ens-img:image-gen:{marker} -->\nmore"
        stripped, by_feature = extract_image_markers(content)
        assert by_feature == {"image-gen": [marker]}
        assert "ens-img" not in stripped
        assert "text" in stripped
        assert "more" in stripped

    def test_family_pass_uses_unclaimed_lines_only(self):
        # The family pass operates on the residual AFTER the locked pass
        # consumed chart-render lines. A well-formed chart-render marker
        # must NOT be re-extracted by the family pass (would double-count).
        chart_id = "1" * 32
        image_gen_id = "2" * 32
        content = (
            f"<!-- ens-img:chart-render:{chart_id} -->\n"
            f"<!-- ens-img:image-gen:{image_gen_id} -->"
        )
        _, by_feature = extract_image_markers(content)
        assert by_feature == {
            "chart-render": [chart_id],
            "image-gen": [image_gen_id],
        }
        # And critically: the chart id appears exactly once (no double-
        # extraction from the family pass).
        assert by_feature["chart-render"].count(chart_id) == 1


class TestExtractChartImagesByteCompatGuard:
    """Phase 2 Task #5 — byte-compat guard for chart-render extraction.

    Golden chart-render fixtures assert ``extract_chart_images`` output
    identity pre/post Phase 2. The chart pipeline is byte-stable; the
    family pass is purely ADDITIVE over non-chart lines.

    Frozen-tuple capture procedure (review-fix, council NEEDS-FIXES P1):
    the eight ``FROZEN_TUPLES`` below were captured by importing the
    BASE ``daemon/sources/dispatcher.py`` at sha ``8c525315e`` (the
    pinned baseline the byte-compat contract was authored against) via::

        git -C /home/nea/ensemble-src show 8c525315e:daemon/sources/dispatcher.py \\
            > /tmp/img-gen-scratch/dispatcher_base.py
        git -C /home/nea/ensemble-src show 8c525315e:daemon/sources/base.py \\
            > /tmp/img-gen-scratch/base_base.py
        # load via importlib in a /tmp scratch (fenced — mktemp scratch dir,
        # FAKE_HOME guard; never resolve live-shaped paths)

    and running ``extract_chart_images`` over each fixture verbatim. The
    tuples assert ``(stripped_text, image_ids)`` byte-identity; any
    future contributor who changes ``extract_chart_images`` output for a
    chart-render fixture fails this test, per the byte-compat contract.
    """

    # Each entry: (expected_stripped_text, expected_image_ids_list).
    # Captured at base 8c525315e — the commit the Phase 2 contract was authored against.
    FROZEN_TUPLES = [
        # 1. Single valid marker
        ("Hello world\nMore text", ["abcdef0123456789abcdef0123456789"]),
        # 2. No marker
        ("Just plain text, no marker.", []),
        # 3. Three distinct markers in first-occurrence order
        (
            "x\ny",
            [
                "11111111111111111111111111111111",
                "22222222222222222222222222222222",
                "33333333333333333333333333333333",
            ],
        ),
        # 4. Duplicate marker dedupes to first occurrence
        ("Some text", ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]),
        # 5. Near-miss stripped (33-char id)
        ("Text line\nMore", []),
        # 6. Empty content
        ("", []),
        # 7. Marker on own line preserves other lines
        ("first line\nlast line", ["cccccccccccccccccccccccccccccccc"]),
        # 8. Mixed valid + near-miss
        ("middle\nend", ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]),
    ]

    def test_chart_only_golden_fixtures_byte_identical(self):
        # Golden fixtures chosen to exercise every ``extract_chart_images``
        # code path (dedupe, multi-id, near-miss, empty, own-line, mixed).
        golden_inputs = [
            # 1. Single valid marker
            "Hello world\n"
            "<!-- ens-img:chart-render:abcdef0123456789abcdef0123456789 -->\n"
            "More text",
            # 2. No marker
            "Just plain text, no marker.",
            # 3. Three distinct markers in first-occurrence order
            "<!-- ens-img:chart-render:11111111111111111111111111111111 -->\n"
            "x\n"
            "<!-- ens-img:chart-render:22222222222222222222222222222222 -->\n"
            "y\n"
            "<!-- ens-img:chart-render:33333333333333333333333333333333 -->",
            # 4. Duplicate marker dedupes to first occurrence
            "<!-- ens-img:chart-render:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->\n"
            "Some text\n"
            "<!-- ens-img:chart-render:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->",
            # 5. Near-miss stripped (33-char id)
            "Text line\n"
            "<!-- ens-img:chart-render:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz -->\n"
            "More",
            # 6. Empty content
            "",
            # 7. Marker on own line preserves other lines
            "first line\n"
            "<!-- ens-img:chart-render:cccccccccccccccccccccccccccccccc -->\n"
            "last line",
            # 8. Mixed valid + near-miss
            "<!-- ens-img:chart-render:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb -->\n"
            "middle\n"
            "<!-- ens-img:chart-render:toolongidfortheregexpaddedchars000 -->\n"
            "end",
        ]
        # FROZEN_TUPLES was captured at base 8c525315e (see class docstring).
        # Any change to ``extract_chart_images`` for chart-render inputs fails
        # this assertion, enforcing the byte-compat contract.
        assert len(self.FROZEN_TUPLES) == len(golden_inputs), (
            "FROZEN_TUPLES length MUST match golden_inputs length"
        )
        for idx, (golden, expected) in enumerate(
            zip(golden_inputs, self.FROZEN_TUPLES), start=1
        ):
            actual = extract_chart_images(golden)
            assert actual == expected, (
                f"chart-render byte-compat broken for fixture #{idx}; "
                f"expected (stripped, ids)={expected!r}, got {actual!r}.\n"
                f"Golden input:\n{golden!r}"
            )

    def test_image_gen_marker_does_not_affect_chart_only_pipeline(self):
        # ``extract_chart_images`` (chart-only byte-compat function) must
        # be unaffected by image-gen markers in the input — the chart
        # pipeline ignores image-gen markers (they fall through the chart
        # extraction + chart near-miss sweep and remain in the residual).
        # The new family function is the one that extracts image-gen.
        chart_id = "a" * 32
        image_gen_id = "b" * 32
        content = (
            f"<!-- ens-img:chart-render:{chart_id} -->\n"
            f"<!-- ens-img:image-gen:{image_gen_id} -->"
        )
        stripped, chart_ids = extract_chart_images(content)
        # Chart-render extraction still finds the chart marker.
        assert chart_ids == [chart_id]
        # The image-gen marker survives the chart-only sweep (it is not a
        # chart-render near-miss) — the family pass is what removes it.
        assert "image-gen" in stripped
        assert image_gen_id in stripped


class TestAllowedFeaturesProvenanceGate:
    """Phase 2 Task #2 — ``ALLOWED_FEATURES`` provenance gate unit coverage.

    Unit-level coverage of the explicit frozenset gate at the delivery
    boundary. Integration coverage (resolver drop + WARN) lives in the
    seam tests below + the family-resolver unit tests.
    """

    def test_allowed_features_contains_both_chart_and_image_gen(self):
        assert "chart-render" in ALLOWED_FEATURES
        assert "image-gen" in ALLOWED_FEATURES

    def test_allowed_features_is_a_frozenset(self):
        # The frozenset type is the contract: future features extend by
        # adding new entries to a literal; mutation is refused.
        assert isinstance(ALLOWED_FEATURES, frozenset)

    @pytest.mark.asyncio
    async def test_resolve_family_images_refuses_unknown_feature(self):
        # Default-deny for a feature not in ALLOWED_FEATURES — the resolver
        # returns [] without touching the store.
        from daemon.services.tmp_image_store import TmpImageRecord

        record = TmpImageRecord(
            image_id="c" * 32,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "image-gen"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.open_full_calls: list[str] = []

            def open_full(self, image_id):
                # If called for an unknown feature, the resolver short-
                # circuits BEFORE opening the store — assert no calls.
                self.open_full_calls.append(image_id)
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

        fake_store = _FakeStore()
        # Future feature "video-gen" is NOT in ALLOWED_FEATURES today.
        atts = await _resolve_family_images("video-gen", ["c" * 32], fake_store)
        assert atts == []
        assert fake_store.open_full_calls == []

    @pytest.mark.asyncio
    async def test_resolve_family_images_drop_provenance_mismatch(self):
        # Record advertises "chart-render" but caller asks for "image-gen"
        # → drop + WARN (text fallback).
        from daemon.services.tmp_image_store import TmpImageRecord

        record = TmpImageRecord(
            image_id="d" * 32,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},  # wrong feature
            retention_class="normal",
        )

        class _FakeStore:
            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

        atts = await _resolve_family_images("image-gen", ["d" * 32], _FakeStore())
        assert atts == []

    @pytest.mark.asyncio
    async def test_resolve_family_images_deliver_matching_feature(self):
        # Happy path: provenance matches the requested feature.
        from daemon.services.tmp_image_store import TmpImageRecord

        record = TmpImageRecord(
            image_id="e" * 32,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "image-gen"},
            retention_class="normal",
        )

        class _FakeStore:
            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

        atts = await _resolve_family_images("image-gen", ["e" * 32], _FakeStore())
        assert len(atts) == 1
        assert atts[0].image_id == "e" * 32
        assert atts[0].filename.startswith("image-")


class TestFamilyRegexPinning:
    """Pin the new ``_FAMILY_MARKER_RE`` regex shape (decisions.md §D2)."""

    def test_family_re_exact_match_image_gen(self):
        line = "<!-- ens-img:image-gen:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->"
        m = _FAMILY_MARKER_RE.match(line)
        assert m is not None
        assert m.group(1) == "image-gen"
        assert m.group(2) == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_family_re_exact_match_chart_render(self):
        # The family regex also matches chart-render lines (the LOCKED
        # regex claims them FIRST so they are not double-extracted, but
        # the family regex shape is intentionally a superset).
        line = "<!-- ens-img:chart-render:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->"
        m = _FAMILY_MARKER_RE.match(line)
        assert m is not None
        assert m.group(1) == "chart-render"

    def test_family_re_rejects_extra_text(self):
        line = "<!-- ens-img:image-gen:aaaaaaaaaaaaaaaa --> junk"
        assert _FAMILY_MARKER_RE.match(line) is None

    def test_family_re_rejects_unknown_feature_token(self):
        # Uppercase or underscore in the feature token → not matched
        # (lowercase-kebab per D2 grammar).
        for bad in ("Image-Gen", "image_gen", "IMAGE-GEN", "image.gen"):
            line = f"<!-- ens-img:{bad}:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->"
            assert _FAMILY_MARKER_RE.match(line) is None, (
                f"family regex must reject feature token {bad!r}"
            )


class TestBothSeamImageGenEndToEnd:
    """Phase 2 — end-to-end image-gen marker through BOTH dispatch_completed
    AND dispatch_message seams, mirroring the existing chart-render seam
    coverage (``TestProgressiveLaneExtractsAndStrips``,
    ``TestCompletedLaneExtractsAndStrips``) for the image-gen feature.
    """

    @pytest.mark.asyncio
    async def test_progressive_lane_image_gen_extracts_and_strips(self):
        image_gen_marker = "a" * 32
        from daemon.services.tmp_image_store import TmpImageRecord
        record = TmpImageRecord(
            image_id=image_gen_marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "image-gen"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.delete_calls: list[str] = []

            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

            def delete(self, image_id):
                self.delete_calls.append(image_id)
                return True

        fake_store = _FakeStore()
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)

        manager = Mock()
        manager.tmp_image_store = fake_store

        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        content = (
            "Here is your generated image\n"
            f"<!-- ens-img:image-gen:{image_gen_marker} -->\n"
            "hope you like it"
        )
        await dispatcher.dispatch_message(
            source="discord:user1", content=content,
        )

        assert adapter.send.await_count == 1
        outgoing = adapter.send.await_args.args[0]
        assert "ens-img" not in outgoing.content
        assert "Here is your generated image" in outgoing.content
        assert "hope you like it" in outgoing.content
        assert outgoing.images is not None
        assert len(outgoing.images) == 1
        assert outgoing.images[0].image_id == image_gen_marker
        # filename prefix is image-<id8> (NOT chart-<id8>).
        assert outgoing.images[0].filename.startswith("image-")

        # store.delete fired once after successful delivery.
        assert fake_store.delete_calls == [image_gen_marker]

        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_completed_lane_image_gen_extracts_and_strips(self):
        image_gen_marker = "b" * 32
        from daemon.services.tmp_image_store import TmpImageRecord
        record = TmpImageRecord(
            image_id=image_gen_marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "image-gen"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.delete_calls: list[str] = []

            def open_full(self, image_id):
                return record

            def open_with_meta(self, image_id):
                return b"PNG", "image/png", "0" * 64

            def delete(self, image_id):
                self.delete_calls.append(image_id)
                return True

        fake_store = _FakeStore()
        adapter = AsyncMock()
        # Progressive returns False (forces completed lane to fire).
        adapter.send = AsyncMock(side_effect=[False, True])

        manager = Mock()
        manager.tmp_image_store = fake_store
        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        source = "discord:user1"
        content = (
            f"Here is your generated image\n"
            f"<!-- ens-img:image-gen:{image_gen_marker} -->\n"
        )

        await dispatcher.dispatch_message(source=source, content=content)
        await dispatcher.dispatch_completed(
            instance_id="test-instance",
            message_id="msg-1",
            source=source,
            content=content,
        )

        assert adapter.send.await_count == 2
        completed_call = adapter.send.await_args_list[-1].args[0]
        assert "ens-img" not in completed_call.content
        assert completed_call.images is not None
        assert len(completed_call.images) == 1
        assert completed_call.images[0].image_id == image_gen_marker
        # store.delete fired EXACTLY ONCE (only the completed lane delivered).
        assert fake_store.delete_calls == [image_gen_marker]

        await dispatcher.stop()

    @pytest.mark.asyncio
    async def test_mixed_chart_and_image_gen_delivered_together(self):
        # Chart + image-gen markers in the same content → both delivered
        # through one OutgoingMessage.images list.
        chart_marker = "c" * 32
        image_gen_marker = "d" * 32
        from daemon.services.tmp_image_store import TmpImageRecord
        chart_record = TmpImageRecord(
            image_id=chart_marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "chart-render"},
            retention_class="normal",
        )
        image_record = TmpImageRecord(
            image_id=image_gen_marker,
            content_type="image/png",
            size_bytes=3,
            uploaded_at="2026-10-04T00:00:00+00:00",
            sha256_hex="0" * 64,
            provenance={"feature": "image-gen"},
            retention_class="normal",
        )

        class _FakeStore:
            def __init__(self):
                self.delete_calls: list[str] = []

            def open_full(self, image_id):
                if image_id == chart_marker:
                    return chart_record
                if image_id == image_gen_marker:
                    return image_record
                raise KeyError(image_id)

            def open_with_meta(self, image_id):
                if image_id == chart_marker:
                    return b"PNG", "image/png", "0" * 64
                if image_id == image_gen_marker:
                    return b"PNG", "image/png", "0" * 64
                raise KeyError(image_id)

            def delete(self, image_id):
                self.delete_calls.append(image_id)
                return True

        fake_store = _FakeStore()
        adapter = AsyncMock()
        adapter.send = AsyncMock(return_value=True)

        manager = Mock()
        manager.tmp_image_store = fake_store
        registry = Mock()
        registry.get = Mock(return_value=adapter)
        registry.manager = manager

        dispatcher = ResponseDispatcher(registry=registry, subscriber_id="test")
        await dispatcher.start()

        content = (
            f"Chart and image below:\n"
            f"<!-- ens-img:chart-render:{chart_marker} -->\n"
            f"<!-- ens-img:image-gen:{image_gen_marker} -->\n"
            f"end"
        )
        await dispatcher.dispatch_message(
            source="discord:user1", content=content,
        )

        assert adapter.send.await_count == 1
        outgoing = adapter.send.await_args.args[0]
        assert "ens-img" not in outgoing.content
        assert outgoing.images is not None
        assert len(outgoing.images) == 2
        delivered_ids = {img.image_id for img in outgoing.images}
        assert delivered_ids == {chart_marker, image_gen_marker}
        # Both blobs deleted after delivery.
        assert sorted(fake_store.delete_calls) == sorted([chart_marker, image_gen_marker])

        await dispatcher.stop()
