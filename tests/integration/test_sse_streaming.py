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


class TestSSEDeduplication:
    """Test SSE event deduplication in stream_events."""

    @pytest.mark.asyncio
    async def test_stream_events_skips_duplicate_event_ids(self, engine, event_repo, event_bus):
        """Events with same ID should only be sent once."""
        instance_id = "test-instance-dedup"

        # Create the same event twice (simulating race condition)
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id="msg-dup-1",
            content={"text": "Hello"}
        )

        # Get the event and broadcast the same streaming event twice
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            message_id="msg-dup-1",
            delta={"type": "content_chunk", "content": "test", "index": 0}
        )

        # Get streaming events (should have one)
        streaming1 = await event_bus.get_streaming_events(instance_id)
        assert len(streaming1) == 1

        # Get again (simulating SSE polling same event)
        streaming2 = await event_bus.get_streaming_events(instance_id)
        assert len(streaming2) == 0  # Should be empty now (consumed)

    @pytest.mark.asyncio
    async def test_event_repository_returns_distinct_events(self, engine, event_repo, event_bus):
        """EventRepository should return distinct events by ID."""
        instance_id = "test-instance-distinct"

        # Create multiple lifecycle events
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id="msg-1",
            content={"text": "First"}
        )
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id="msg-2",
            content={"text": "Second"}
        )

        # Get all events
        events = event_repo.get_events_since(instance_id, after_id=None)
        assert len(events) == 2

        # Get with cursor after first event
        events_after_first = event_repo.get_events_since(instance_id, after_id=events[0].id)
        assert len(events_after_first) == 1
        assert events_after_first[0].message_id == "msg-2"

        # All event IDs should be unique
        event_ids = [e.id for e in events]
        assert len(event_ids) == len(set(event_ids)), "Event IDs should be unique"

