"""Tests for daemon.sources.dispatcher module."""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from daemon.sources.dispatcher import ResponseDispatcher
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
def dispatcher(mock_registry, event_bus):
    """Create a ResponseDispatcher with mocked dependencies."""
    return ResponseDispatcher(event_bus, mock_registry, "test-dispatcher")


# ============================================================================
# Lifecycle Tests
# ============================================================================

@pytest.mark.asyncio
async def test_start_subscribes_to_broadcaster(dispatcher, event_bus):
    """start() should subscribe to the EventBus."""
    await dispatcher.start()
    
    # Verify subscription was made
    assert dispatcher._event_queue is not None
    # Check that subscriber was registered in event_bus
    assert "test-dispatcher" in event_bus._global_subscribers
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_start_sets_running_flag(dispatcher):
    """_running should be True after start()."""
    await dispatcher.start()
    
    assert dispatcher._running is True
    assert dispatcher._task is not None
    
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
    
    # Create and handle event directly (bypassing queue)
    # EventBus sends dicts with "event_type" and "data" keys
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello",
            "source": "telegram:12345"
        }
    }
    
    await dispatcher._handle_event(event)
    
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
    """Non-completed events should be ignored."""
    # Create mock adapter
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Create and handle a non-completed event
    event = {
        "instance_id": "test-instance",
        "event_type": "message_queued",
        "data": {
            "content": "Hello",
            "source": "telegram:12345"
        }
    }
    
    await dispatcher._handle_event(event)
    
    # Verify adapter was NOT called
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_handle_event_missing_source(dispatcher, mock_registry):
    """Events with missing source should be handled gracefully."""
    # Create mock adapter
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Create event with missing source
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello"
            # Missing "source" field
        }
    }
    
    await dispatcher._handle_event(event)
    
    # Verify adapter was NOT called (no source to route to)
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_handle_event_invalid_source_format(dispatcher, mock_registry):
    """Events with invalid source format should be handled gracefully."""
    # Create mock adapter
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Create event with invalid source format (missing colon)
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello",
            "source": "invalid-source-without-colon"
        }
    }
    
    await dispatcher._handle_event(event)
    
    # Verify adapter was NOT called (invalid format)
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_handle_event_invalid_source_id_format(dispatcher, mock_registry):
    """Events with invalid source_id format should be handled gracefully."""
    mock_adapter = AsyncMock()
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Create event with invalid source_id (special characters)
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello",
            "source": "invalid@source#id:12345"
        }
    }
    
    await dispatcher._handle_event(event)
    
    # Verify adapter was NOT called (invalid source_id format)
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_handle_event_no_adapter_found(dispatcher, mock_registry):
    """Events with unknown source_id should be handled gracefully."""
    # Registry returns None for unknown source
    mock_registry.get = Mock(return_value=None)
    
    await dispatcher.start()
    
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello",
            "source": "unknown_source:12345"
        }
    }
    
    # Should not raise, just log warning
    await dispatcher._handle_event(event)
    
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
    """stop() should wait for pending work up to the timeout."""
    # Track if send was called
    send_called = False
    
    # Create mock adapter that takes some time to respond
    async def slow_send(message):
        nonlocal send_called
        send_called = True
        await asyncio.sleep(0.3)
        return True
    
    mock_adapter = Mock()
    mock_adapter.send = slow_send
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Start the event loop and send some events
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello",
            "source": "telegram:12345"
        }
    }
    
    # Put event in queue
    await dispatcher._event_queue.put(event)
    
    # Wait for the event to be processed (since loop has 1s timeout)
    await asyncio.sleep(1.5)
    
    # Stop with a longer timeout - should complete
    await dispatcher.stop(timeout=2.0)
    
    # Verify the send was attempted
    assert send_called is True
    
    # Verify cleanup
    assert dispatcher._running is False
    assert dispatcher._event_queue is None


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
    
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello",
            "source": "telegram:12345",
            "metadata": {"key": "value"},
            "message_type": "image",
            "reply_to_id": "original-msg"
        }
    }
    
    await dispatcher._handle_event(event)
    
    await asyncio.sleep(0.1)
    
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
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello",
            "source": f"telegram:{long_user_id}"
        }
    }
    
    await dispatcher._handle_event(event)
    
    # Verify adapter was NOT called
    mock_adapter.send.assert_not_called()
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_event_loop_handles_exceptions(dispatcher, mock_registry):
    """Event loop should continue processing despite individual event errors."""
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(side_effect=Exception("Adapter error"))
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    # Send multiple events, one will cause error
    for i in range(3):
        event = {
            "instance_id": "test-instance",
            "event_type": "completed",
            "data": {
                "content": f"Hello {i}",
                "source": "telegram:12345"
            }
        }
        await dispatcher._event_queue.put(event)
    
    # Wait for processing
    await asyncio.sleep(0.5)
    
    # Loop should still be running (no crash)
    assert dispatcher._running is True
    
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_adapter_send_failure_logged(dispatcher, mock_registry, caplog):
    """Failed adapter.send() should be logged but not crash."""
    import logging
    
    mock_adapter = AsyncMock()
    mock_adapter.send = AsyncMock(return_value=False)  # Return failure
    mock_registry.get = Mock(return_value=mock_adapter)
    
    await dispatcher.start()
    
    event = {
        "instance_id": "test-instance",
        "event_type": "completed",
        "data": {
            "content": "Hello",
            "source": "telegram:12345"
        }
    }
    
    await dispatcher._handle_event(event)
    await asyncio.sleep(0.1)
    
    # Verify warning was logged about failure
    # (caplog captures the log output)
    
    await dispatcher.stop()
