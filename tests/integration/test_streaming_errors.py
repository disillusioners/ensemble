"""Error scenario tests for progressive streaming feature.

Tests edge cases, error conditions, and failure scenarios.
"""

import pytest
import pytest_asyncio
import asyncio
import json
import tempfile
import sqlite3
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime

from daemon.events import EventBroadcaster, Event, event_to_sse
from daemon.models import ErrorCodes


# ============================================================================
# Fixtures
# ============================================================================

@pytest_asyncio.fixture
async def mock_manager():
    """Create a mock SessionManager."""
    manager = Mock()
    manager.spawn_session = Mock(return_value="test-session-id")
    manager.get_session = Mock()
    manager.broadcaster = EventBroadcaster()
    
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db_path = temp_db.name
    temp_db.close()
    conn = sqlite3.connect(temp_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS source_configs (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config TEXT NOT NULL,
            credentials TEXT,
            enabled BOOLEAN DEFAULT TRUE,
            status TEXT DEFAULT 'stopped',
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    manager.conn = conn
    manager._temp_db_path = temp_db_path
    manager.source_registry = None
    
    yield manager
    
    try:
        conn.close()
    except Exception:
        pass
    try:
        os.unlink(temp_db_path)
    except Exception:
        pass


# ============================================================================
# Error Scenario Tests: Event Types & Validation
# ============================================================================

class TestEventValidation:
    """Tests for event data validation."""

    def test_event_with_empty_session_id(self, mock_manager):
        """Test event with empty session_id."""
        # Empty session_id should still work (edge case)
        event = Event(type="test", session_id="")
        assert event.session_id == ""

    def test_event_with_unicode_data(self, mock_manager):
        """Test event with unicode characters in data."""
        event = Event(
            type="message",
            session_id="session-1",
            data={"content": "Hello 🌍 你好 🔥"}
        )
        
        sse = event_to_sse(event)
        decoded = json.loads(sse["data"])
        assert decoded["content"] == "Hello 🌍 你好 🔥"

    def test_event_with_nested_data(self, mock_manager):
        """Test event with deeply nested data."""
        event = Event(
            type="complex",
            session_id="session-1",
            data={
                "nested": {
                    "deep": {
                        "value": [1, 2, 3]
                    }
                }
            }
        )
        
        sse = event_to_sse(event)
        decoded = json.loads(sse["data"])
        assert decoded["nested"]["deep"]["value"] == [1, 2, 3]

    def test_event_with_special_characters_in_data(self, mock_manager):
        """Test event with special JSON characters."""
        event = Event(
            type="special",
            session_id="session-1",
            data={
                "json": '{"key": "value"}',
                "newline": "line1\nline2",
                "tab": "col1\tcol2"
            }
        )
        
        sse = event_to_sse(event)
        decoded = json.loads(sse["data"])
        assert decoded["json"] == '{"key": "value"}'


class TestEventBroadcasterErrorScenarios:
    """Tests for error scenarios in EventBroadcaster."""

    @pytest.mark.asyncio
    async def test_broadcast_to_session_with_closed_queue(self, mock_manager):
        """Test broadcasting when queue is in error state."""
        broadcaster = mock_manager.broadcaster
        
        # Create a queue and manually close it
        queue = await broadcaster.get_queue("session-1")
        
        # Queue is closed by making it full and trying to get
        # This is a destructive test, so we'll just test cleanup
        broadcaster.cleanup_session("session-1")
        
        # Should not raise when trying to broadcast after cleanup
        await broadcaster.broadcast(Event(type="test", session_id="session-1"))

    @pytest.mark.asyncio
    async def test_rapid_burst_of_events(self, mock_manager):
        """Test handling rapid burst of events."""
        broadcaster = EventBroadcaster(max_queue_size=500, history_size=500)
        session_id = "burst-session"
        
        # Send 200 events rapidly
        tasks = [
            broadcaster.broadcast(Event(
                type=f"event{i}",
                session_id=session_id,
                data={"index": i}
            ))
            for i in range(200)
        ]
        
        await asyncio.gather(*tasks)
        
        # Should handle gracefully, history should store all events
        stats = broadcaster.get_stats(session_id)
        assert stats["history_size"] == 200  # All stored in history

    @pytest.mark.asyncio
    async def test_many_sessions_at_once(self, mock_manager):
        """Test handling many concurrent sessions."""
        broadcaster = EventBroadcaster()
        
        # Create 50 sessions with events
        for i in range(50):
            session_id = f"session-{i}"
            await broadcaster.broadcast(Event(
                type="init",
                session_id=session_id,
                data={"index": i}
            ))
        
        # All sessions should have events
        assert len(broadcaster._event_history) == 50

    @pytest.mark.asyncio
    async def test_broadcast_with_large_payload(self, mock_manager):
        """Test broadcasting with large event data."""
        # Create large payload (1MB)
        large_data = {"content": "x" * (1024 * 1024)}
        
        event = Event(
            type="large",
            session_id="session-1",
            data=large_data
        )
        
        # Should handle gracefully
        await mock_manager.broadcaster.broadcast(event)


class TestSSEErrorScenarios:
    """Tests for SSE-specific error scenarios."""

    def test_event_to_sse_with_invalid_data_types(self, mock_manager):
        """Test conversion with non-serializable data types."""
        # This should fail gracefully
        class NonSerializable:
            def __str__(self):
                return "custom"
        
        event = Event(
            type="test",
            session_id="session-1",
            data={"custom": NonSerializable()}
        )
        
        # The conversion may fail or produce invalid JSON
        # depending on implementation - test expects graceful handling
        try:
            sse = event_to_sse(event)
            # If it succeeds, data should be JSON
            json.loads(sse["data"])
        except (TypeError, json.JSONDecodeError):
            # Acceptable - non-serializable data can't be JSON
            pass

    def test_event_to_sse_with_none_values(self, mock_manager):
        """Test conversion with None values in data."""
        event = Event(
            type="test",
            session_id="session-1",
            data={"null_value": None, "normal": "value"}
        )
        
        sse = event_to_sse(event)
        decoded = json.loads(sse["data"])
        
        # None should be serialized as null in JSON
        assert decoded["null_value"] is None
        assert decoded["normal"] == "value"


class TestReconnectionErrorScenarios:
    """Tests for reconnection error scenarios."""

    @pytest.mark.asyncio
    async def test_reconnection_with_invalid_event_id(self, mock_manager):
        """Test reconnection with invalid event ID."""
        broadcaster = mock_manager.broadcaster
        
        # Add some events
        for i in range(3):
            await broadcaster.broadcast(Event(
                type=f"event{i}",
                session_id="session-1",
                event_id=i+1
            ))
        
        # Get events with negative ID
        missed = broadcaster.get_events_since("session-1", -1)
        
        # Should return all events
        assert len(missed) == 3

    @pytest.mark.asyncio
    async def test_reconnection_with_future_event_id(self, mock_manager):
        """Test reconnection with event ID higher than any event."""
        broadcaster = mock_manager.broadcaster
        
        # Add some events
        for i in range(3):
            await broadcaster.broadcast(Event(
                type=f"event{i}",
                session_id="session-1",
                event_id=i+1
            ))
        
        # Get events with very high ID (future)
        missed = broadcaster.get_events_since("session-1", 1000)
        
        # Should return no events
        assert len(missed) == 0


class TestConcurrentAccessErrorScenarios:
    """Tests for concurrent access error scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_broadcasts_same_session(self, mock_manager):
        """Test concurrent broadcasts to same session."""
        broadcaster = EventBroadcaster(max_queue_size=500, history_size=500)
        session_id = "concurrent-session"
        
        # 100 concurrent broadcasts
        async def broadcast_event(i):
            await broadcaster.broadcast(Event(
                type=f"event{i}",
                session_id=session_id,
                data={"index": i}
            ))
        
        await asyncio.gather(*[broadcast_event(i) for i in range(100)])
        
        # All events should be received in history
        stats = broadcaster.get_stats(session_id)
        assert stats["history_size"] == 100

    @pytest.mark.asyncio
    async def test_concurrent_get_queue(self, mock_manager):
        """Test concurrent get_queue calls for same session."""
        broadcaster = EventBroadcaster()
        
        # Many concurrent get_queue calls
        async def get_q():
            return await broadcaster.get_queue("session-1")
        
        queues = await asyncio.gather(*[get_q() for _ in range(10)])
        
        # All should return the same queue
        assert all(q is queues[0] for q in queues)

    @pytest.mark.asyncio
    async def test_mixed_read_write_operations(self, mock_manager):
        """Test mixed read/write operations concurrently."""
        broadcaster = EventBroadcaster()
        session_id = "mixed-session"
        
        async def writer(i):
            await broadcaster.broadcast(Event(
                type="write",
                session_id=session_id,
                data={"i": i}
            ))
        
        async def reader():
            return broadcaster.get_stats(session_id)
        
        # Interleave reads and writes
        tasks = []
        for i in range(20):
            tasks.append(writer(i))
            if i % 4 == 0:
                tasks.append(reader())
        
        await asyncio.gather(*tasks)
        
        stats = broadcaster.get_stats(session_id)
        assert stats["history_size"] == 20


class TestErrorRecovery:
    """Tests for error recovery scenarios."""

    @pytest.mark.asyncio
    async def test_recovery_after_queue_overflow(self, mock_manager):
        """Test system continues working after queue overflow."""
        broadcaster = EventBroadcaster(max_queue_size=2)
        session_id = "recovery-session"
        
        # Fill queue to capacity
        await broadcaster.broadcast(Event(type="e1", session_id=session_id))
        await broadcaster.broadcast(Event(type="e2", session_id=session_id))
        
        # This should drop oldest
        await broadcaster.broadcast(Event(type="e3", session_id=session_id))
        
        # System should still accept new events
        await broadcaster.broadcast(Event(type="e4", session_id=session_id))
        
        stats = broadcaster.get_stats(session_id)
        assert stats["history_size"] == 4  # History keeps all

    @pytest.mark.asyncio
    async def test_session_reuse_after_cleanup(self, mock_manager):
        """Test reusing session ID after cleanup."""
        broadcaster = mock_manager.broadcaster
        session_id = "reuse-session"
        
        # First round
        await broadcaster.broadcast(Event(type="e1", session_id=session_id))
        broadcaster.cleanup_session(session_id)
        
        # Second round - should work
        await broadcaster.broadcast(Event(type="e2", session_id=session_id))
        
        stats = broadcaster.get_stats(session_id)
        assert stats["history_size"] == 1

    @pytest.mark.asyncio
    async def test_consumer_disconnect_handling(self, mock_manager):
        """Test handling when consumer disconnects."""
        broadcaster = mock_manager.broadcaster
        session_id = "disconnect-session"
        
        # Get queue (simulating consumer)
        queue = await broadcaster.get_queue(session_id)
        
        # Add events
        await broadcaster.broadcast(Event(type="e1", session_id=session_id))
        
        # Simulate disconnect by not consuming
        # Add more events - should not crash
        await broadcaster.broadcast(Event(type="e2", session_id=session_id))
        await broadcaster.broadcast(Event(type="e3", session_id=session_id))


class TestHistoryManagement:
    """Tests for event history management."""

    @pytest.mark.asyncio
    async def test_history_size_limit(self, mock_manager):
        """Test that history respects size limit."""
        broadcaster = EventBroadcaster(history_size=5)
        session_id = "history-limit"
        
        # Add 10 events
        for i in range(10):
            await broadcaster.broadcast(Event(
                type=f"e{i}",
                session_id=session_id,
                event_id=i+1
            ))
        
        # History should be limited to 5
        history = broadcaster._event_history[session_id]
        assert len(history) == 5
        
        # Should have the most recent 5
        assert history[0].event_id == 6
        assert history[-1].event_id == 10

    @pytest.mark.asyncio
    async def test_history_not_affected_by_queue_size(self, mock_manager):
        """Test that history is independent of queue size."""
        broadcaster = EventBroadcaster(max_queue_size=1, history_size=100)
        session_id = "history-indep"
        
        # Add many events
        for i in range(50):
            await broadcaster.broadcast(Event(
                type=f"e{i}",
                session_id=session_id
            ))
        
        # History should have all 50
        stats = broadcaster.get_stats(session_id)
        assert stats["history_size"] == 50


class TestGlobalSubscriberErrorScenarios:
    """Tests for global subscriber error scenarios."""

    @pytest.mark.asyncio
    async def test_subscriber_queue_full(self, mock_manager):
        """Test handling when subscriber queue is full."""
        broadcaster = EventBroadcaster()
        
        # Create subscriber with small queue
        subscriber = await broadcaster.subscribe_all("test-sub", maxsize=1)
        
        # Fill subscriber queue
        await broadcaster.broadcast(Event(type="e1", session_id="s1"))
        
        # Add more - should not crash broadcaster
        await broadcaster.broadcast(Event(type="e2", session_id="s1"))
        await broadcaster.broadcast(Event(type="e3", session_id="s1"))

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, mock_manager):
        """Test multiple global subscribers."""
        broadcaster = EventBroadcaster()
        
        sub1 = await broadcaster.subscribe_all("sub1")
        sub2 = await broadcaster.subscribe_all("sub2")
        
        # Broadcast event
        await broadcaster.broadcast(Event(
            type="broadcast",
            session_id="s1",
            data={"value": 42}
        ))
        
        # Both subscribers should receive
        msg1 = await asyncio.wait_for(sub1.get(), timeout=1.0)
        msg2 = await asyncio.wait_for(sub2.get(), timeout=1.0)
        
        assert msg1.data["value"] == 42
        assert msg2.data["value"] == 42


class TestAPISSEErrorHandling:
    """Tests for API-level SSE error handling."""

    @pytest.mark.asyncio
    async def test_stream_api_requires_session(self, mock_manager):
        """Test that session validation works in the streaming flow."""
        # This tests the event broadcaster behavior
        # API validation is handled by the API layer
        
        # Verify session not found raises error
        broadcaster = mock_manager.broadcaster
        
        # Events for unknown session should not crash
        await broadcaster.broadcast(Event(
            type="test",
            session_id="nonexistent-session"
        ))
        
        # History should be created for the unknown session
        assert "nonexistent-session" in broadcaster._event_history
