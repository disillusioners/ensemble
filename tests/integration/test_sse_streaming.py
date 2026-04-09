"""Integration tests for SSE streaming with EventBus."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.services.event_bus import EventBus
from daemon.repositories.event.models import EventKind
from daemon.repositories.event.repository import EventRepository


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def engine():
    """In-memory SQLite for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def event_repo(engine):
    """EventRepository fixture."""
    return EventRepository(engine=engine)


@pytest.fixture
def event_bus(event_repo):
    """EventBus fixture."""
    return EventBus(event_repo=event_repo)


# ============================================================================
# Integration Tests: EventBus SSE Streaming
# ============================================================================

class TestEventBusLifecyclePersistence:
    """Test lifecycle events are persisted to DB via EventBus."""

    @pytest.mark.asyncio
    async def test_processing_completed_event_persisted_to_db(self, engine, event_repo, event_bus):
        """Processing completed lifecycle event should be persisted to DB."""
        instance_id = "test-instance-123"

        # Create lifecycle event
        await event_bus.create_processing_completed_event(
            instance_id=instance_id,
            message_id="msg-123",
            result={"output": "test result"}
        )

        # Verify persisted to DB
        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 1
        assert events[0].kind == EventKind.PROCESSING_COMPLETED.value
        assert events[0].message_id == "msg-123"
        # Data should be stored as JSON string
        assert events[0].data is not None
        assert "output" in events[0].data

    @pytest.mark.asyncio
    async def test_message_received_event_persisted_to_db(self, engine, event_repo, event_bus):
        """Message received lifecycle event should be persisted to DB."""
        instance_id = "test-instance-456"

        # Create lifecycle event
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id="msg-789",
            content={"text": "Hello world"}
        )

        # Verify persisted to DB
        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 1
        assert events[0].kind == EventKind.MESSAGE_RECEIVED.value
        assert events[0].message_id == "msg-789"

    @pytest.mark.asyncio
    async def test_processing_started_event_persisted_to_db(self, engine, event_repo, event_bus):
        """Processing started lifecycle event should be persisted to DB."""
        instance_id = "test-instance-789"

        await event_bus.create_processing_started_event(
            instance_id=instance_id,
            message_id="msg-start"
        )

        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 1
        assert events[0].kind == EventKind.PROCESSING_STARTED.value

    @pytest.mark.asyncio
    async def test_processing_failed_event_persisted_to_db(self, engine, event_repo, event_bus):
        """Processing failed lifecycle event should be persisted to DB."""
        instance_id = "test-instance-fail"

        await event_bus.create_processing_failed_event(
            instance_id=instance_id,
            message_id="msg-fail",
            error={"message": "Something went wrong"}
        )

        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 1
        assert events[0].kind == EventKind.PROCESSING_FAILED.value

    @pytest.mark.asyncio
    async def test_multiple_lifecycle_events_sequentially(self, engine, event_repo, event_bus):
        """Multiple lifecycle events should all be persisted in order."""
        instance_id = "test-instance-multi"

        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id="msg-1",
            content={"text": "First message"}
        )

        await event_bus.create_processing_started_event(
            instance_id=instance_id,
            message_id="msg-1",
        )

        await event_bus.create_processing_completed_event(
            instance_id=instance_id,
            message_id="msg-1",
            result={"content": "Response"}
        )

        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 3
        assert events[0].kind == EventKind.MESSAGE_RECEIVED.value
        assert events[1].kind == EventKind.PROCESSING_STARTED.value
        assert events[2].kind == EventKind.PROCESSING_COMPLETED.value


class TestEventBusStreamingNotPersisted:
    """Test streaming events are NOT persisted to DB."""

    @pytest.mark.asyncio
    async def test_content_chunk_not_persisted(self, engine, event_repo, event_bus):
        """Content chunk streaming event should NOT be persisted to DB."""
        instance_id = "test-instance-stream"

        # Broadcast streaming event
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"chunk": "Hello "}
        )

        # Verify NOT persisted to DB
        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 0, "Streaming events should NOT be persisted"

        # But should be in streaming queue
        streaming = await event_bus.get_streaming_events(instance_id)
        assert len(streaming) == 1
        assert streaming[0]["event_type"] == "content_chunk"

    @pytest.mark.asyncio
    async def test_thinking_event_not_persisted(self, engine, event_repo, event_bus):
        """Thinking streaming event should NOT be persisted to DB."""
        instance_id = "test-instance-thinking"

        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="thinking",
            data={"content": "Let me think..."}
        )

        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 0, "Streaming events should NOT be persisted"

    @pytest.mark.asyncio
    async def test_tool_call_event_not_persisted(self, engine, event_repo, event_bus):
        """Tool call streaming event should NOT be persisted to DB."""
        instance_id = "test-instance-tool"

        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="tool_call",
            data={"id": "call_1", "name": "bash", "arguments": {"command": "ls"}}
        )

        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 0, "Streaming events should NOT be persisted"

    @pytest.mark.asyncio
    async def test_tool_complete_event_not_persisted(self, engine, event_repo, event_bus):
        """Tool complete streaming event should NOT be persisted to DB."""
        instance_id = "test-instance-tool-complete"

        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="tool_complete",
            data={"id": "call_1", "name": "bash", "output": "files here"}
        )

        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 0, "Streaming events should NOT be persisted"

    @pytest.mark.asyncio
    async def test_mixed_lifecycle_and_streaming(self, engine, event_repo, event_bus):
        """Mix of lifecycle and streaming events - only lifecycle persisted."""
        instance_id = "test-instance-mixed"

        # Lifecycle event
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id="msg-1",
            content={"text": "Hello"}
        )

        # Streaming events
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"chunk": "Hi "}
        )
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"chunk": "there!"}
        )

        # Only lifecycle events should be in DB
        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 1
        assert events[0].kind == EventKind.MESSAGE_RECEIVED.value

        # Streaming events in queue
        streaming = await event_bus.get_streaming_events(instance_id)
        assert len(streaming) == 2


class TestEventBusNotification:
    """Test EventBus notification mechanism."""

    @pytest.mark.asyncio
    async def test_notification_set_on_lifecycle_event(self, event_bus):
        """Lifecycle events should trigger notification."""
        instance_id = "test-instance-notify"

        # Get notification
        notification = event_bus.get_notification(instance_id)
        assert not notification.is_set()

        # Create lifecycle event
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id="msg-456",
            content={"text": "test"}
        )

        # Notification should be set
        assert notification.is_set()

    @pytest.mark.asyncio
    async def test_notification_set_on_streaming_event(self, event_bus):
        """Streaming events should also trigger notification."""
        instance_id = "test-instance-stream-notify"

        notification = event_bus.get_notification(instance_id)
        assert not notification.is_set()

        # Broadcast streaming event
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"chunk": "test"}
        )

        # Notification should be set
        assert notification.is_set()


class TestEventBusCleanup:
    """Test EventBus cleanup functionality."""

    @pytest.mark.asyncio
    async def test_cleanup_instance_removes_in_memory_state(self, event_bus):
        """Cleanup should remove in-memory state for an instance."""
        instance_id = "instance-to-clean"

        # Add some state
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"chunk": "test"}
        )
        event_bus.get_notification(instance_id)

        # Verify state exists
        assert instance_id in event_bus._streaming_channels
        assert instance_id in event_bus._notifications

        # Cleanup
        event_bus.cleanup_instance(instance_id)

        # Verify cleaned up
        assert instance_id not in event_bus._streaming_channels
        assert instance_id not in event_bus._notifications


# ============================================================================
# Legacy Tests: EventBroadcaster (deprecated)
# ============================================================================

class TestLegacyEventBroadcaster:
    """Tests for legacy EventBroadcaster (deprecated).

    These tests cover the old in-memory EventBroadcaster API which is no longer
    used by the SSE endpoints. Kept for reference and migration validation.
    """

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_message_queued_event(self):
        """Test legacy message_queued event is broadcast correctly."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "instance-1"

        # Create queue BEFORE broadcasting so events are captured
        queue = await broadcaster.get_queue(instance_id)

        await broadcaster.broadcast(Event(
            type="message_queued",
            instance_id=instance_id,
            message_id="msg-1",
            data={"content": "Hello", "source": "api"}
        ))

        event = await queue.get()

        assert event.type == "message_queued"
        assert event.data["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_status_changed_event(self):
        """Test legacy status_changed event is broadcast correctly."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "instance-1"

        queue = await broadcaster.get_queue(instance_id)

        await broadcaster.broadcast(Event(
            type="status_changed",
            instance_id=instance_id,
            message_id="msg-1",
            data={"status": "processing"}
        ))

        event = await queue.get()

        assert event.type == "status_changed"
        assert event.data["status"] == "processing"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_content_chunk_event(self):
        """Test legacy content_chunk event for progressive streaming."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "instance-1"

        queue = await broadcaster.get_queue(instance_id)

        # Simulate progressive chunks
        chunks = ["Hello", " ", "world", "!"]
        for chunk in chunks:
            await broadcaster.broadcast(Event(
                type="content_chunk",
                instance_id=instance_id,
                message_id="msg-1",
                data={"chunk": chunk}
            ))

        # Collect all chunks
        received_chunks = []
        for _ in range(4):
            event = await queue.get()
            received_chunks.append(event.data["chunk"])

        assert "".join(received_chunks) == "Hello world!"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_thinking_event(self):
        """Test legacy thinking event for extended thinking models."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "instance-1"

        queue = await broadcaster.get_queue(instance_id)

        await broadcaster.broadcast(Event(
            type="thinking",
            instance_id=instance_id,
            message_id="msg-1",
            data={"content": "Let me think about this..."}
        ))

        event = await queue.get()

        assert event.type == "thinking"
        assert "think" in event.data["content"].lower()

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_tool_call_event(self):
        """Test legacy tool_call event for tool invocations."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "instance-1"

        queue = await broadcaster.get_queue(instance_id)

        await broadcaster.broadcast(Event(
            type="tool_call",
            instance_id=instance_id,
            message_id="msg-1",
            data={
                "id": "call_123",
                "name": "bash",
                "arguments": {"command": "ls -la"}
            }
        ))

        event = await queue.get()

        assert event.type == "tool_call"
        assert event.data["name"] == "bash"

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_tool_complete_event(self):
        """Test legacy tool_complete event after tool execution."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "instance-1"

        queue = await broadcaster.get_queue(instance_id)

        await broadcaster.broadcast(Event(
            type="tool_complete",
            instance_id=instance_id,
            message_id="msg-1",
            data={
                "id": "call_123",
                "name": "bash",
                "output": "total 0\ndrwxr-xr-x  5 user  staff   160 Mar  4 10:00 ."
            }
        ))

        event = await queue.get()

        assert event.type == "tool_complete"
        assert "total" in event.data["output"]

    @pytest.mark.asyncio
    async def test_event_broadcaster_sends_completed_event(self):
        """Test legacy completed event when message processing finishes."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "instance-1"
        message_id = "msg-1"

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
    async def test_streaming_with_tool_calls(self):
        """Test legacy streaming pipeline with tool calls."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "test-instance-tools"
        message_id = "msg-tools"

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

    @pytest.mark.asyncio
    async def test_cleanup_removes_event_state(self):
        """Test that legacy cleanup removes all event state."""
        from daemon.events import EventBroadcaster, Event

        broadcaster = EventBroadcaster()
        instance_id = "instance-to-clean"

        # Add events
        await broadcaster.broadcast(Event(
            type="test",
            instance_id=instance_id,
            data={}
        ))

        # Verify state exists
        assert instance_id in broadcaster._event_history

        # Cleanup
        broadcaster.cleanup_instance(instance_id)

        # Verify cleaned up
        assert instance_id not in broadcaster._event_history
        assert instance_id not in broadcaster._queues
