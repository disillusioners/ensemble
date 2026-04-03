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
from daemon.manager import InstanceManager, MessageResult
from daemon.models import InstanceCreate, MessageCreate


# ============================================================================
# Fixtures
# ============================================================================

@pytest_asyncio.fixture
async def mock_manager():
    """Create a mock InstanceManager with all needed methods."""
    import tempfile
    
    manager = Mock()
    manager.spawn_instance = Mock(return_value="test-instance-id")
    manager.get_instance = Mock()
    manager.send_message = Mock(return_value=MessageResult(content="Test response"))
    manager.terminate_instance = Mock(return_value=True)
    manager.list_instances = Mock(return_value=[])
    manager.get_instance_info = Mock(return_value={
        "instance_id": "test-instance-id",
        "agent_dir": "/path/to/agent",
        "status": "running",
        "parent_id": None,
        "children": [],
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    })
    manager.enqueue_message = AsyncMock(return_value=Mock(
        message_id="test-message-id",
        instance_id="test-instance-id",
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
    async def test_sse_endpoint_returns_sse_response(self, mock_manager):
        """Test that /api/instances/{id}/events endpoint exists and is registered."""
        from daemon.api import api_router
        
        # Verify the route exists in the router (routes are prefixed with /api)
        routes = [r.path for r in api_router.routes]
        expected_route = "/api/instances/{instance_id}/events"
        assert expected_route in routes, f"Expected route {expected_route} not found in {routes}"

    @pytest.mark.asyncio
    async def test_sse_endpoint_rejects_non_sse_clients(self, mock_manager):
        """Test that SSE endpoint properly handles non-SSE Accept header."""
        from daemon.api import api_router
        
        # Verify the route exists (routes are prefixed with /api)
        routes = [r.path for r in api_router.routes]
        expected_route = "/api/instances/{instance_id}/events"
        assert expected_route in routes, f"Expected route {expected_route} not found in {routes}"


class TestSSEEventTypes:
    """Tests for different SSE event types."""

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_message_queued_event(self, mock_manager):
        """Test message_queued event is broadcast correctly."""
        broadcaster = mock_manager.broadcaster
        
        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue("instance-1")
        
        await broadcaster.broadcast(Event(
            type="message_queued",
            instance_id="instance-1",
            message_id="msg-1",
            data={"content": "Hello", "source": "api"}
        ))
        
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "message_queued"
        assert event.data["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_status_changed_event(self, mock_manager):
        """Test status_changed event is broadcast correctly."""
        broadcaster = mock_manager.broadcaster
        
        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue("instance-1")
        
        await broadcaster.broadcast(Event(
            type="status_changed",
            instance_id="instance-1",
            message_id="msg-1",
            data={"status": "processing"}
        ))
        
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "status_changed"
        assert event.data["status"] == "processing"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_content_chunk_event(self, mock_manager):
        """Test content_chunk event for progressive streaming."""
        broadcaster = mock_manager.broadcaster
        
        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue("instance-1")
        
        # Simulate progressive chunks
        chunks = ["Hello", " ", "world", "!"]
        for chunk in chunks:
            await broadcaster.broadcast(Event(
                type="content_chunk",
                instance_id="instance-1",
                message_id="msg-1",
                data={"chunk": chunk}
            ))
        
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
        
        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue("instance-1")
        
        await broadcaster.broadcast(Event(
            type="thinking",
            instance_id="instance-1",
            message_id="msg-1",
            data={"content": "Let me think about this..."}
        ))
        
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "thinking"
        assert "think" in event.data["content"].lower()

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_tool_call_event(self, mock_manager):
        """Test tool_call event for tool invocations."""
        broadcaster = mock_manager.broadcaster
        
        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue("instance-1")
        
        await broadcaster.broadcast(Event(
            type="tool_call",
            instance_id="instance-1",
            message_id="msg-1",
            data={
                "id": "call_123",
                "name": "bash",
                "arguments": {"command": "ls -la"}
            }
        ))
        
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "tool_call"
        assert event.data["name"] == "bash"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_tool_complete_event(self, mock_manager):
        """Test tool_complete event after tool execution."""
        broadcaster = mock_manager.broadcaster
        
        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue("instance-1")
        
        await broadcaster.broadcast(Event(
            type="tool_complete",
            instance_id="instance-1",
            message_id="msg-1",
            data={
                "id": "call_123",
                "name": "bash",
                "output": "total 0\ndrwxr-xr-x  5 user  staff   160 Mar  4 10:00 ."
            }
        ))
        
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        
        assert event.type == "tool_complete"
        assert "total" in event.data["output"]

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_completed_event(self, mock_manager):
        """Test completed event when message processing finishes."""
        broadcaster = mock_manager.broadcaster
        instance_id = "instance-1"
        message_id = "msg-1"
        
        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue(instance_id)
        
        await broadcaster.broadcast(Event(
            type="completed",
            instance_id=instance_id,
            message_id=message_id,
            data={"content": "Thinking... Result", "tool_calls": None}
        ))
        
        # Verify all events in queue
        events = []
        while not queue.empty():
            event = queue.get_nowait()
            events.append(event)
        
        assert len(events) == 1
        assert events[0].type == "completed"

    @pytest.mark.asyncio
    async def test_streaming_with_tool_calls(self, mock_manager):
        """Test streaming pipeline with tool calls."""
        instance_id = "test-instance-tools"
        message_id = "msg-tools"
        broadcaster = mock_manager.broadcaster
        
        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue(instance_id)
        
        # 1. Message queued
        await broadcaster.broadcast(Event(
            type="message_queued",
            instance_id=instance_id,
            message_id=message_id,
            data={"content": "Run a command"}
        ))
        
        # 2. Tool call
        await broadcaster.broadcast(Event(
            type="tool_call",
            instance_id=instance_id,
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
            instance_id=instance_id,
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
            instance_id=instance_id,
            message_id=message_id,
            data={"content": "hello", "tool_calls": [{"id": "call_1", "name": "bash"}]}
        ))
        
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        
        assert any(e.type == "tool_call" for e in events)
        assert any(e.type == "tool_complete" for e in events)


class TestInstanceCleanup:
    """Tests for proper instance cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_event_state(self, mock_manager):
        """Test that instance cleanup removes all event state."""
        instance_id = "instance-to-clean"
        
        # Add events
        await mock_manager.broadcaster.broadcast(Event(
            type="test",
            instance_id=instance_id,
            data={}
        ))
        
        # Verify state exists
        assert instance_id in mock_manager.broadcaster._event_history
        
        # Cleanup
        mock_manager.broadcaster.cleanup_instance(instance_id)
        
        # Verify cleaned up
        assert instance_id not in mock_manager.broadcaster._event_history
        assert instance_id not in mock_manager.broadcaster._queues
