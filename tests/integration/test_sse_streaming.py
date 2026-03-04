"""Integration tests for progressive streaming (SSE) functionality."""

import pytest
import pytest_asyncio
import asyncio
import json
import tempfile
import sqlite3
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime
import httpx
import sse_starlette

from daemon import api as api_module
from daemon.events import EventBroadcaster, Event
from daemon.manager import SessionManager, MessageResult
from daemon.models import SessionCreate, MessageCreate


# ============================================================================
# Fixtures
# ============================================================================

@pytest_asyncio.fixture
async def mock_manager():
    """Create a mock SessionManager with all needed methods."""
    import tempfile
    
    manager = Mock()
    manager.spawn_session = Mock(return_value="test-session-id")
    manager.get_session = Mock()
    manager.send_message = Mock(return_value=MessageResult(content="Test response"))
    manager.terminate_session = Mock(return_value=True)
    manager.list_sessions = Mock(return_value=[])
    manager.get_session_info = Mock(return_value={
        "session_id": "test-session-id",
        "agent_dir": "/path/to/agent",
        "status": "running",
        "parent_id": None,
        "children": [],
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    })
    manager.enqueue_message = AsyncMock(return_value=Mock(
        message_id="test-message-id",
        session_id="test-session-id",
        status="queued"
    ))
    manager.get_messages = AsyncMock(return_value=[])
    manager.get_queue_stats = Mock(return_value=Mock(
        pending_count=0,
        processing_count=0,
        oldest_message_age_seconds=0
    ))
    
    # Mock broadcaster
    manager.broadcaster = EventBroadcaster()
    
    # Temp database
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
    
    # Cleanup
    try:
        conn.close()
    except Exception:
        pass
    try:
        os.unlink(temp_db_path)
    except Exception:
        pass


@pytest.fixture
def app_with_mock_manager(mock_manager):
    """Create FastAPI app with mocked manager."""
    with patch.object(api_module, 'manager', mock_manager), \
         patch.object(api_module, 'start_time', 1000.0):
        yield mock_manager


@pytest_asyncio.fixture
async def client(app_with_mock_manager):
    """Create async test client."""
    from daemon.api import app
    
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), 
        base_url="http://test"
    ) as ac:
        yield ac


# ============================================================================
# Integration Tests: SSE Streaming
# ============================================================================

class TestSSEStreamConnection:
    """Tests for SSE stream connection establishment."""

    @pytest.mark.asyncio
    async def test_sse_endpoint_returns_sse_response(self, client, mock_manager):
        """Test that /sessions/{id}/events returns SSE stream."""
        # Mock the event generator to avoid real async operations
        mock_manager.get_session = Mock()  # Raises KeyError if not found
        
        response = await client.get(
            "/sessions/test-session-id/events",
            headers={"Accept": "text/event-stream"}
        )
        
        # Should return a streaming response
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_sse_endpoint_rejects_non_sse_clients(self, client, mock_manager):
        """Test that SSE endpoint properly handles non-SSE Accept header."""
        mock_manager.get_session = Mock()
        
        response = await client.get(
            "/sessions/test-session-id/events",
            headers={"Accept": "application/json"}
        )
        
        # SSE endpoint should still work (sse_starlette handles this)
        assert response.status_code in [200, 406]


class TestSSEEventTypes:
    """Tests for different SSE event types."""

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_message_queued_event(self, mock_manager):
        """Test message_queued event is broadcast correctly."""
        broadcaster = mock_manager.broadcaster
        
        await broadcaster.broadcast(Event(
            type="message_queued",
            session_id="session-1",
            message_id="msg-1",
            data={"content": "Hello", "source": "api"}
        ))
        
        queue = await broadcaster.get_queue("session-1")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "message_queued"
        assert event.data["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_status_changed_event(self, mock_manager):
        """Test status_changed event is broadcast correctly."""
        broadcaster = mock_manager.broadcaster
        
        await broadcaster.broadcast(Event(
            type="status_changed",
            session_id="session-1",
            message_id="msg-1",
            data={"status": "processing"}
        ))
        
        queue = await broadcaster.get_queue("session-1")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "status_changed"
        assert event.data["status"] == "processing"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_content_chunk_event(self, mock_manager):
        """Test content_chunk event for progressive streaming."""
        broadcaster = mock_manager.broadcaster
        
        # Simulate progressive chunks
        chunks = ["Hello", " ", "world", "!"]
        for chunk in chunks:
            await broadcaster.broadcast(Event(
                type="content_chunk",
                session_id="session-1",
                message_id="msg-1",
                data={"chunk": chunk}
            ))
        
        queue = await broadcaster.get_queue("session-1")
        
        # Collect all chunks
        received_chunks = []
        for _ in range(4):
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            received_chunks.append(event.data["chunk"])
        
        assert "".join(received_chunks) == "Hello world!"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_thinking_event(self, mock_manager):
        """Test thinking event for extended thinking models."""
        broadcaster = mock_manager.broadcaster
        
        await broadcaster.broadcast(Event(
            type="thinking",
            session_id="session-1",
            message_id="msg-1",
            data={"content": "Let me think about this..."}
        ))
        
        queue = await broadcaster.get_queue("session-1")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "thinking"
        assert "think" in event.data["content"].lower()

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_tool_call_event(self, mock_manager):
        """Test tool_call event for tool invocations."""
        broadcaster = mock_manager.broadcaster
        
        await broadcaster.broadcast(Event(
            type="tool_call",
            session_id="session-1",
            message_id="msg-1",
            data={
                "id": "call_123",
                "name": "bash",
                "arguments": {"command": "ls -la"}
            }
        ))
        
        queue = await broadcaster.get_queue("session-1")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "tool_call"
        assert event.data["name"] == "bash"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_tool_complete_event(self, mock_manager):
        """Test tool_complete event after tool execution."""
        broadcaster = mock_manager.broadcaster
        
        await broadcaster.broadcast(Event(
            type="tool_complete",
            session_id="session-1",
            message_id="msg-1",
            data={
                "id": "call_123",
                "name": "bash",
                "output": "total 0\ndrwxr-xr-x  5 user  staff   160 Mar  4 10:00 ."
            }
        ))
        
        queue = await broadcaster.get_queue("session-1")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "tool_complete"
        assert "total" in event.data["output"]

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_completed_event(self, mock_manager):
        """Test completed event when message processing finishes."""
        broadcaster = mock_manager.broadcaster
        
        await broadcaster.broadcast(Event(
            type="completed",
            session_id="session-1",
            message_id="msg-1",
            data={
                "content": "Final response",
                "thinking": "My thoughts",
                "tool_calls": []
            }
        ))
        
        queue = await broadcaster.get_queue("session-1")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "completed"
        assert event.data["content"] == "Final response"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_error_event(self, mock_manager):
        """Test error event when something goes wrong."""
        broadcaster = mock_manager.broadcaster
        
        await broadcaster.broadcast(Event(
            type="error",
            session_id="session-1",
            message_id="msg-1",
            data={"error": "API rate limit exceeded", "status": "failed"}
        ))
        
        queue = await broadcaster.get_queue("session-1")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "error"
        assert "rate limit" in event.data["error"].lower()


class TestSSEEventToSSEConversion:
    """Tests for event to SSE format conversion."""

    def test_event_to_sse_format(self, mock_manager):
        """Test that events are converted to proper SSE format."""
        from daemon.events import event_to_sse
        
        event = Event(
            type="message_queued",
            session_id="session-1",
            message_id="msg-123",
            data={"content": "Hello"},
            event_id=5
        )
        
        sse = event_to_sse(event)
        
        # SSE format should have id, event, data
        assert "id" in sse
        assert "event" in sse
        assert "data" in sse
        assert sse["id"] == "5"
        assert sse["event"] == "message_queued"
        
        # Data should be JSON
        data = json.loads(sse["data"])
        assert data["session_id"] == "session-1"
        assert data["message_id"] == "msg-123"

    def test_connected_event_format(self, mock_manager):
        """Test connected event for initial connection."""
        from daemon.events import event_to_sse
        
        event = Event(
            type="connected",
            session_id="session-1",
            data={}
        )
        
        sse = event_to_sse(event)
        
        assert sse["event"] == "connected"


class TestSSEReconnection:
    """Tests for SSE reconnection support."""

    @pytest.mark.asyncio
    async def test_get_events_since_for_reconnection(self, mock_manager):
        """Test that missed events can be retrieved for reconnection."""
        broadcaster = mock_manager.broadcaster
        
        # Simulate some events that happened
        for i in range(5):
            await broadcaster.broadcast(Event(
                type=f"event{i}",
                session_id="session-1",
                event_id=i+1
            ))
        
        # Client reconnects with last event ID 3
        missed_events = broadcaster.get_events_since("session-1", 3)
        
        # Should get events 4 and 5
        assert len(missed_events) == 2
        assert missed_events[0].event_id == 4
        assert missed_events[1].event_id == 5

    @pytest.mark.asyncio
    async def test_reconnection_with_no_missed_events(self, mock_manager):
        """Test reconnection when no events were missed."""
        broadcaster = mock_manager.broadcaster
        
        # No events
        missed_events = broadcaster.get_events_since("session-1", 0)
        
        assert len(missed_events) == 0


class TestSSEErrorHandling:
    """Tests for error handling in SSE streaming."""

    @pytest.mark.asyncio
    async def test_queue_full_does_not_crash_broadcaster(self, mock_manager):
        """Test that full queue doesn't crash the broadcaster."""
        broadcaster = EventBroadcaster(max_queue_size=1)
        
        # Fill the queue
        await broadcaster.broadcast(Event(
            type="event1",
            session_id="session-1"
        ))
        
        # Try to add more - should not crash
        await broadcaster.broadcast(Event(
            type="event2", 
            session_id="session-1"
        ))

    @pytest.mark.asyncio
    async def test_broadcast_to_nonexistent_session(self, mock_manager):
        """Test broadcasting to session with no queue."""
        broadcaster = mock_manager.broadcaster
        
        # Should not raise - just silently drops
        await broadcaster.broadcast(Event(
            type="test",
            session_id="nonexistent-session"
        ))


# ============================================================================
# End-to-End Streaming Tests
# ============================================================================

class TestEndToEndStreaming:
    """End-to-end tests for the streaming pipeline."""

    @pytest.mark.asyncio
    async def test_full_streaming_pipeline(self, mock_manager):
        """Test complete streaming pipeline from message to completion."""
        session_id = "test-session-e2e"
        message_id = "msg-e2e"
        broadcaster = mock_manager.broadcaster
        
        # 1. Message queued
        await broadcaster.broadcast(Event(
            type="message_queued",
            session_id=session_id,
            message_id=message_id,
            data={"content": "Hello", "source": "api"}
        ))
        
        # 2. Status changed to processing
        await broadcaster.broadcast(Event(
            type="status_changed",
            session_id=session_id,
            message_id=message_id,
            data={"status": "processing"}
        ))
        
        # 3. Stream some content chunks
        for chunk in ["Thinking", "...", " Result"]:
            await broadcaster.broadcast(Event(
                type="content_chunk",
                session_id=session_id,
                message_id=message_id,
                data={"chunk": chunk}
            ))
        
        # 4. Completion
        await broadcaster.broadcast(Event(
            type="completed",
            session_id=session_id,
            message_id=message_id,
            data={"content": "Thinking... Result", "tool_calls": None}
        ))
        
        # Verify all events in queue
        queue = await broadcaster.get_queue(session_id)
        events = []
        while not queue.empty():
            event = queue.get_nowait()
            events.append(event)
        
        assert len(events) == 6  # queued + status + 3 chunks + completed
        assert events[0].type == "message_queued"
        assert events[1].type == "status_changed"
        assert events[-1].type == "completed"

    @pytest.mark.asyncio
    async def test_streaming_with_tool_calls(self, mock_manager):
        """Test streaming pipeline with tool calls."""
        session_id = "test-session-tools"
        message_id = "msg-tools"
        broadcaster = mock_manager.broadcaster
        
        # 1. Message queued
        await broadcaster.broadcast(Event(
            type="message_queued",
            session_id=session_id,
            message_id=message_id,
            data={"content": "Run a command"}
        ))
        
        # 2. Tool call
        await broadcaster.broadcast(Event(
            type="tool_call",
            session_id=session_id,
            message_id=message_id,
            data={
                "id": "call_1",
                "name": "bash",
                "arguments": {"command": "echo hello"}
            }
        ))
        
        # 3. Tool complete
        await broadcaster.broadcast(Event(
            type="tool_complete",
            session_id=session_id,
            message_id=message_id,
            data={
                "id": "call_1",
                "name": "bash",
                "output": "hello"
            }
        ))
        
        # 4. Final response
        await broadcaster.broadcast(Event(
            type="completed",
            session_id=session_id,
            message_id=message_id,
            data={"content": "hello", "tool_calls": [{"id": "call_1", "name": "bash"}]}
        ))
        
        queue = await broadcaster.get_queue(session_id)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        
        assert any(e.type == "tool_call" for e in events)
        assert any(e.type == "tool_complete" for e in events)


class TestSessionCleanup:
    """Tests for proper session cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_event_state(self, mock_manager):
        """Test that session cleanup removes all event state."""
        session_id = "session-to-clean"
        
        # Add events
        await mock_manager.broadcaster.broadcast(Event(
            type="test",
            session_id=session_id,
            data={}
        ))
        
        # Verify state exists
        assert session_id in mock_manager.broadcaster._event_history
        
        # Cleanup
        mock_manager.broadcaster.cleanup_session(session_id)
        
        # Verify cleaned up
        assert session_id not in mock_manager.broadcaster._event_history
        assert session_id not in mock_manager.broadcaster._queues
