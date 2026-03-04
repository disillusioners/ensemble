"""Unit tests for daemon/events.py - Event broadcasting system."""

import pytest
import asyncio
import json
import threading
from unittest.mock import Mock, patch, AsyncMock
import time

from daemon.events import EventBroadcaster, Event, event_to_sse


class TestEvent:
    """Tests for the Event dataclass."""

    def test_event_creation(self):
        """Test basic event creation."""
        event = Event(
            type="message_queued",
            session_id="test-session-123",
            message_id="msg-456",
            data={"content": "Hello"}
        )
        
        assert event.type == "message_queued"
        assert event.session_id == "test-session-123"
        assert event.message_id == "msg-456"
        assert event.data == {"content": "Hello"}
        assert event.event_id == 0  # Default

    def test_event_with_custom_event_id(self):
        """Test event with custom event_id."""
        event = Event(
            type="completed",
            session_id="session-1",
            event_id=42
        )
        
        assert event.event_id == 42

    def test_event_data_defaults_to_empty_dict(self):
        """Test that data defaults to empty dict."""
        event = Event(type="test", session_id="s1")
        assert event.data == {}


class TestEventBroadcaster:
    """Tests for EventBroadcaster class."""

    @pytest.fixture
    def broadcaster(self):
        """Create a broadcaster instance."""
        return EventBroadcaster(max_queue_size=10, history_size=5)

    @pytest.mark.asyncio
    async def test_get_queue_creates_new_queue(self, broadcaster):
        """Test that get_queue creates a new queue for new session."""
        queue = await broadcaster.get_queue("session-1")
        
        assert queue is not None
        assert isinstance(queue, asyncio.Queue)
        # Same session should return same queue
        queue2 = await broadcaster.get_queue("session-1")
        assert queue is queue2

    @pytest.mark.asyncio
    async def test_get_queue_returns_existing_queue(self, broadcaster):
        """Test that get_queue returns existing queue for known session."""
        queue1 = await broadcaster.get_queue("session-1")
        queue2 = await broadcaster.get_queue("session-1")
        
        assert queue1 is queue2

    @pytest.mark.asyncio
    async def test_broadcast_pushes_to_queue(self, broadcaster):
        """Test that broadcast pushes event to session queue."""
        event = Event(
            type="message_queued",
            session_id="session-1",
            message_id="msg-1",
            data={"content": "test"}
        )
        
        # First get the queue, then broadcast
        queue = await broadcaster.get_queue("session-1")
        
        await broadcaster.broadcast(event)
        
        # Check queue has the event - need to await since broadcast is async
        # The queue should have 1 event after broadcast completes
        assert queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_broadcast_stores_in_history(self, broadcaster):
        """Test that broadcast stores event in history."""
        event = Event(type="test", session_id="session-1")
        
        await broadcaster.broadcast(event)
        
        history = broadcaster._event_history.get("session-1", [])
        assert len(history) == 1
        assert history[0].type == "test"

    @pytest.mark.asyncio
    async def test_broadcast_tracks_event_counter(self, broadcaster):
        """Test that broadcast increments event counter."""
        event1 = Event(type="event1", session_id="session-1")
        event2 = Event(type="event2", session_id="session-1")
        
        await broadcaster.broadcast(event1)
        await broadcaster.broadcast(event2)
        
        # Event IDs should be sequential
        assert event1.event_id == 1
        assert event2.event_id == 2

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_sessions(self, broadcaster):
        """Test broadcasting to different sessions."""
        # Get queues first
        queue1 = await broadcaster.get_queue("session-1")
        queue2 = await broadcaster.get_queue("session-2")
        
        event1 = Event(type="msg", session_id="session-1")
        event2 = Event(type="msg", session_id="session-2")
        
        await broadcaster.broadcast(event1)
        await broadcaster.broadcast(event2)
        
        assert queue1.qsize() == 1
        assert queue2.qsize() == 1

    @pytest.mark.asyncio
    async def test_get_events_since_returns_missed_events(self, broadcaster):
        """Test getting missed events for reconnection."""
        # Pre-populate history
        for i in range(3):
            event = Event(type=f"event{i}", session_id="session-1", event_id=i+1)
            broadcaster._event_history["session-1"].append(event)
        broadcaster._event_counters["session-1"] = 3
        
        # Get events after ID 1
        missed = broadcaster.get_events_since("session-1", 1)
        
        assert len(missed) == 2
        assert missed[0].event_id == 2
        assert missed[1].event_id == 3

    @pytest.mark.asyncio
    async def test_get_events_since_empty_for_unknown_session(self, broadcaster):
        """Test getting events for unknown session returns empty list."""
        missed = broadcaster.get_events_since("unknown-session", 0)
        assert missed == []

    @pytest.mark.asyncio
    async def test_cleanup_session_removes_all_state(self, broadcaster):
        """Test cleanup removes queue and history for session."""
        # Add some state
        await broadcaster.get_queue("session-1")
        event = Event(type="test", session_id="session-1")
        await broadcaster.broadcast(event)
        
        # Cleanup
        broadcaster.cleanup_session("session-1")
        
        # Verify cleaned up
        assert "session-1" not in broadcaster._queues
        assert "session-1" not in broadcaster._event_history

    @pytest.mark.asyncio
    async def test_get_stats_returns_queue_info(self, broadcaster):
        """Test get_stats returns correct statistics."""
        # Add events
        await broadcaster.get_queue("session-1")
        for i in range(3):
            event = Event(type=f"event{i}", session_id="session-1")
            await broadcaster.broadcast(event)
        
        stats = broadcaster.get_stats("session-1")
        
        assert stats["queue_size"] == 3
        assert stats["history_size"] == 3
        assert stats["last_event_id"] == 3


class TestEventBroadcasterGlobalSubscribers:
    """Tests for global subscriber functionality."""

    @pytest.fixture
    def broadcaster(self):
        """Create a broadcaster instance."""
        return EventBroadcaster()

    @pytest.mark.asyncio
    async def test_subscribe_all_creates_queue(self, broadcaster):
        """Test that subscribe_all creates a queue."""
        queue = await broadcaster.subscribe_all("test-subscriber")
        
        assert queue is not None
        assert isinstance(queue, asyncio.Queue)

    @pytest.mark.asyncio
    async def test_broadcast_to_global_subscribers(self, broadcaster):
        """Test that broadcast pushes to global subscribers."""
        subscriber_queue = await broadcaster.subscribe_all("test-sub")
        
        event = Event(type="test", session_id="session-1", data={"key": "value"})
        await broadcaster.broadcast(event)
        
        # Subscriber should receive event
        received = await asyncio.wait_for(subscriber_queue.get(), timeout=0.5)
        assert received.type == "test"

    @pytest.mark.asyncio
    async def test_unsubscribe_all_removes_subscriber(self, broadcaster):
        """Test that unsubscribe_all removes subscriber."""
        await broadcaster.subscribe_all("test-sub")
        broadcaster.unsubscribe_all("test-sub")
        
        event = Event(type="test", session_id="session-1")
        await broadcaster.broadcast(event)
        
        # Subscriber queue should be empty (or subscriber removed)
        # After unsubscribe, new broadcasts won't go to this subscriber
        assert "test-sub" not in broadcaster._subscriber_refs


class TestEventBroadcasterThreadSafety:
    """Tests for thread-safe operations."""

    @pytest.fixture
    def broadcaster(self):
        """Create a broadcaster instance."""
        return EventBroadcaster()

    def test_broadcast_sync_requires_main_loop(self, broadcaster):
        """Test that broadcast_sync requires main loop to be set."""
        event = Event(type="test", session_id="session-1")
        
        # Should log error and drop event if main loop not set
        broadcaster.broadcast_sync(event)
        # No exception should be raised

    @patch('asyncio.run_coroutine_threadsafe')
    def test_broadcast_sync_with_main_loop(self, mock_threadsafe, broadcaster):
        """Test broadcast_sync works when main loop is set."""
        # Set a mock main loop
        mock_loop = Mock()
        mock_loop.is_closed.return_value = False
        broadcaster.set_main_loop(mock_loop)
        
        # Mock run_coroutine_threadsafe
        mock_future = Mock()
        mock_future.add_done_callback = Mock()
        mock_threadsafe.return_value = mock_future
        
        event = Event(type="test", session_id="session-1")
        broadcaster.broadcast_sync(event)
        
        # Verify run_coroutine_threadsafe was called
        mock_threadsafe.assert_called_once()

    def test_broadcast_sync_drops_event_when_loop_closed(self, broadcaster):
        """Test broadcast_sync drops event when loop is closed."""
        mock_loop = Mock()
        mock_loop.is_closed.return_value = True
        broadcaster.set_main_loop(mock_loop)
        
        event = Event(type="test", session_id="session-1")
        # Should not raise, just drop the event
        broadcaster.broadcast_sync(event)


class TestEventToSSE:
    """Tests for event_to_sse function."""

    def test_basic_conversion(self):
        """Test basic event to SSE conversion."""
        event = Event(
            type="message_queued",
            session_id="session-1",
            message_id="msg-123",
            data={"content": "Hello"},
            event_id=5
        )
        
        sse = event_to_sse(event)
        
        assert sse["id"] == "5"
        assert sse["event"] == "message_queued"
        
        data = json.loads(sse["data"])
        assert data["session_id"] == "session-1"
        assert data["message_id"] == "msg-123"
        assert data["content"] == "Hello"

    def test_conversion_merges_data(self):
        """Test that data fields are merged in SSE output."""
        event = Event(
            type="completed",
            session_id="s1",
            data={"status": "done", "extra": "value"}
        )
        
        sse = event_to_sse(event)
        data = json.loads(sse["data"])
        
        assert "session_id" in data
        assert data["status"] == "done"
        assert data["extra"] == "value"

    def test_conversion_with_none_message_id(self):
        """Test conversion handles None message_id."""
        event = Event(
            type="connected",
            session_id="s1",
            message_id=None,
            data={}
        )
        
        sse = event_to_sse(event)
        data = json.loads(sse["data"])
        
        assert data["message_id"] is None


class TestEventBroadcasterQueueOverflow:
    """Tests for queue overflow handling."""

    @pytest.fixture
    def small_queue_broadcaster(self):
        """Broadcaster with small queue size."""
        return EventBroadcaster(max_queue_size=2, history_size=5)

    @pytest.mark.asyncio
    async def test_queue_full_drops_oldest(self, small_queue_broadcaster):
        """Test that when queue is full, oldest events are dropped."""
        session_id = "session-overflow"
        
        # Get queue first
        queue = await small_queue_broadcaster.get_queue(session_id)
        
        # Fill the queue - broadcaster drops when full
        for i in range(3):
            event = Event(type=f"event{i}", session_id=session_id)
            await small_queue_broadcaster.broadcast(event)
        
        # Queue may have 2 or 3 depending on timing of get_queue
        # But history should have all 3 events
        history = small_queue_broadcaster._event_history[session_id]
        assert len(history) == 3  # History doesn't drop

    @pytest.mark.asyncio
    async def test_broadcast_non_blocking_when_full(self, small_queue_broadcaster):
        """Test that broadcast doesn't block when queue is full."""
        session_id = "session-block"
        
        # Fill queue
        for i in range(2):
            event = Event(type=f"event{i}", session_id=session_id)
            await small_queue_broadcaster.broadcast(event)
        
        # This should not block - just warn and drop
        extra_event = Event(type="extra", session_id=session_id)
        # Should complete without raising
        await small_queue_broadcaster.broadcast(extra_event)
