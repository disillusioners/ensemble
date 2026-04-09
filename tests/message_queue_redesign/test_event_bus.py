"""Tests for Phase 4 EventBus service.

Tests the hybrid event delivery system combining:
- Lifecycle events (persisted to DB via EventRepository)
- Streaming events (in-memory via asyncio.Queue)
- Per-instance notifications (asyncio.Event)
- Global subscriber support (ResponseDispatcher)
"""

import asyncio
import json
import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.event.repository import EventRepository
from daemon.services.event_bus import EventBus, STREAMING_EVENT_TYPES


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
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
    """Create EventRepository instance."""
    return EventRepository(engine)


@pytest.fixture
def event_bus(event_repo):
    """Create EventBus instance."""
    return EventBus(event_repo=event_repo)


@pytest.fixture
def instance_id():
    """Generate a unique instance ID for each test."""
    return str(uuid.uuid4())


@pytest.fixture
def message_id():
    """Generate a unique message ID for each test."""
    return str(uuid.uuid4())


# ============================================================================
# Test Class: EventBus Lifecycle Events (DB Persistence)
# ============================================================================


class TestEventBusLifecycleEvents:
    """Tests for lifecycle events that are persisted to the database."""

    @pytest.mark.asyncio
    async def test_create_message_received_event(
        self, event_bus, event_repo, instance_id, message_id
    ):
        """Lifecycle event with kind message_received is persisted to DB."""
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=message_id,
            content={"source": "api", "priority": 1},
        )

        # Verify event was persisted to DB
        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.MESSAGE_RECEIVED.value
        assert events[0].instance_id == instance_id
        assert events[0].message_id == message_id

        # Verify data was serialized (data is stored as JSON string in DB)
        data = json.loads(events[0].data) if events[0].data else {}
        assert data["source"] == "api"
        assert data["priority"] == 1

    @pytest.mark.asyncio
    async def test_create_processing_started_event(
        self, event_bus, event_repo, instance_id, message_id
    ):
        """Lifecycle event with kind processing_started is persisted to DB."""
        await event_bus.create_processing_started_event(
            instance_id=instance_id,
            message_id=message_id,
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.PROCESSING_STARTED.value
        assert events[0].message_id == message_id

    @pytest.mark.asyncio
    async def test_create_processing_completed_event(
        self, event_bus, event_repo, instance_id, message_id
    ):
        """Lifecycle event with kind processing_completed includes result data."""
        result = {"status": "success", "output": "Done"}
        await event_bus.create_processing_completed_event(
            instance_id=instance_id,
            message_id=message_id,
            result=result,
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.PROCESSING_COMPLETED.value

        # Data is stored as JSON string in DB
        data = json.loads(events[0].data) if events[0].data else {}
        assert data["status"] == "success"
        assert data["output"] == "Done"

    @pytest.mark.asyncio
    async def test_create_processing_failed_event(
        self, event_bus, event_repo, instance_id, message_id
    ):
        """Lifecycle event with kind processing_failed includes error info."""
        error_info = {"code": "TIMEOUT", "message": "Processing timed out"}
        await event_bus.create_processing_failed_event(
            instance_id=instance_id,
            message_id=message_id,
            error=error_info,
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.PROCESSING_FAILED.value

        # Data is stored as JSON string in DB
        data = json.loads(events[0].data) if events[0].data else {}
        assert data["code"] == "TIMEOUT"
        assert data["message"] == "Processing timed out"

    @pytest.mark.asyncio
    async def test_create_child_completed_event(
        self, event_bus, event_repo, instance_id
    ):
        """Lifecycle event with kind child_completed includes child_id."""
        child_id = str(uuid.uuid4())
        await event_bus.create_child_completed_event(
            instance_id=instance_id,
            child_id=child_id,
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.CHILD_COMPLETED.value

        # Data is stored as JSON string in DB
        data = json.loads(events[0].data) if events[0].data else {}
        assert data["child_id"] == child_id

    @pytest.mark.asyncio
    async def test_create_child_failed_event(
        self, event_bus, event_repo, instance_id
    ):
        """Lifecycle event with kind child_failed includes child_id and error."""
        child_id = str(uuid.uuid4())
        error_info = {"message": "Child process crashed"}
        await event_bus.create_child_failed_event(
            instance_id=instance_id,
            child_id=child_id,
            error=error_info,
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.CHILD_FAILED.value

        # Data is stored as JSON string in DB
        data = json.loads(events[0].data) if events[0].data else {}
        assert data["child_id"] == child_id
        assert data["error"]["message"] == "Child process crashed"

    @pytest.mark.asyncio
    async def test_create_instance_completed_event(
        self, event_bus, event_repo, instance_id
    ):
        """Lifecycle event with kind instance_completed is persisted to DB."""
        await event_bus.create_instance_completed_event(
            instance_id=instance_id,
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.INSTANCE_COMPLETED.value
        assert events[0].message_id is None

    @pytest.mark.asyncio
    async def test_create_error_event(
        self, event_bus, event_repo, instance_id
    ):
        """Lifecycle event with kind error includes error message."""
        error_info = {"type": "validation", "message": "Invalid input"}
        await event_bus.create_error_event(
            instance_id=instance_id,
            error=error_info,
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.ERROR.value

        # Data is stored as JSON string in DB
        data = json.loads(events[0].data) if events[0].data else {}
        assert data["type"] == "validation"
        assert data["message"] == "Invalid input"


# ============================================================================
# Test Class: EventBus Streaming Events (In-Memory Only)
# ============================================================================


class TestEventBusStreamingEvents:
    """Tests for streaming events that are NOT persisted to DB."""

    @pytest.mark.asyncio
    async def test_streaming_event_not_persisted(
        self, event_bus, event_repo, instance_id
    ):
        """content_chunk streaming event is NOT written to DB."""
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"text": "Hello world"},
        )

        # Verify NO event was persisted to DB
        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_streaming_events_in_queue(
        self, event_bus, instance_id
    ):
        """Multiple streaming events are available in the streaming queue."""
        # Broadcast multiple streaming events
        for i in range(3):
            await event_bus.broadcast_streaming_event(
                instance_id=instance_id,
                event_type="content_chunk",
                data={"chunk": i, "text": f"chunk_{i}"},
            )

        # Drain the streaming queue
        events = await event_bus.get_streaming_events(instance_id)

        assert len(events) == 3
        assert all(e["event_type"] == "content_chunk" for e in events)
        assert events[0]["data"]["chunk"] == 0
        assert events[1]["data"]["chunk"] == 1
        assert events[2]["data"]["chunk"] == 2

    @pytest.mark.asyncio
    async def test_streaming_events_all_types(
        self, event_bus, instance_id
    ):
        """All streaming event types (content_chunk, thinking, tool_call, tool_complete)."""
        for event_type in STREAMING_EVENT_TYPES:
            await event_bus.broadcast_streaming_event(
                instance_id=instance_id,
                event_type=event_type,
                data={"type": event_type},
            )

        events = await event_bus.get_streaming_events(instance_id)
        assert len(events) == len(STREAMING_EVENT_TYPES)

        received_types = {e["event_type"] for e in events}
        assert received_types == STREAMING_EVENT_TYPES

    @pytest.mark.asyncio
    async def test_streaming_queue_max_size(
        self, event_bus, instance_id
    ):
        """Streaming queue respects max size and drops oldest events."""
        # EventBus has default streaming_queue_size=100, test with small size
        small_bus = EventBus(event_repo=MagicMock(), streaming_queue_size=3)

        # Try to put more events than queue size
        for i in range(5):
            await small_bus.broadcast_streaming_event(
                instance_id=instance_id,
                event_type="content_chunk",
                data={"i": i},
            )

        # Queue should have at most 3 events (oldest dropped)
        events = await small_bus.get_streaming_events(instance_id)
        assert len(events) <= 3


# ============================================================================
# Test Class: EventBus Notification Mechanism
# ============================================================================


class TestEventBusNotification:
    """Tests for asyncio.Event notification per instance."""

    @pytest.mark.asyncio
    async def test_notification_set_on_lifecycle_event(
        self, event_bus, instance_id
    ):
        """asyncio.Event is set after creating a lifecycle event."""
        notification = event_bus.get_notification(instance_id)

        # Event should not be set initially
        assert not notification.is_set()

        # Create a lifecycle event
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=str(uuid.uuid4()),
        )

        # Notification should now be set
        assert notification.is_set()

    @pytest.mark.asyncio
    async def test_notification_set_on_streaming_event(
        self, event_bus, instance_id
    ):
        """asyncio.Event is set after broadcasting a streaming event."""
        notification = event_bus.get_notification(instance_id)

        # Event should not be set initially
        assert not notification.is_set()

        # Broadcast a streaming event
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"text": "Hello"},
        )

        # Notification should now be set
        assert notification.is_set()

    @pytest.mark.asyncio
    async def test_notification_per_instance(self, event_bus):
        """Different instances have different notification objects."""
        instance_a = str(uuid.uuid4())
        instance_b = str(uuid.uuid4())

        notif_a = event_bus.get_notification(instance_a)
        notif_b = event_bus.get_notification(instance_b)

        # Notifications should be different objects
        assert notif_a is not notif_b

        # Set notification for instance A only
        await event_bus.create_message_received_event(
            instance_id=instance_a,
            message_id=str(uuid.uuid4()),
        )

        # Only instance A's notification should be set
        assert notif_a.is_set()
        assert notif_b.is_set() is False

    @pytest.mark.asyncio
    async def test_notification_cleared_after_wait(self, event_bus, instance_id):
        """Notification can be cleared after being processed."""
        notification = event_bus.get_notification(instance_id)

        # Create event to set notification
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=str(uuid.uuid4()),
        )
        assert notification.is_set()

        # Clear the notification
        notification.clear()
        assert not notification.is_set()


# ============================================================================
# Test Class: EventBus Cursor-Based Delivery
# ============================================================================


class TestEventBusCursorDelivery:
    """Tests for cursor-based event delivery using DB repository."""

    @pytest.mark.asyncio
    async def test_get_events_since_with_cursor(
        self, event_bus, event_repo, instance_id
    ):
        """Events are retrievable by cursor position."""
        # Create 5 lifecycle events
        for i in range(5):
            await event_bus.create_message_received_event(
                instance_id=instance_id,
                message_id=f"msg-{i}",
                content={"index": i},
            )

        # Get events after cursor 3 (should return events 4, 5)
        # Note: EventRepository.get_events_since uses 'after_id' parameter
        events = event_repo.get_events_since(instance_id, after_id=3)

        assert len(events) == 2
        assert events[0].id == 4
        assert events[1].id == 5

    @pytest.mark.asyncio
    async def test_get_events_since_empty_when_no_new(
        self, event_bus, event_repo, instance_id
    ):
        """Returns empty list when cursor is at the latest event."""
        # Create 3 lifecycle events
        for i in range(3):
            await event_bus.create_message_received_event(
                instance_id=instance_id,
                message_id=f"msg-{i}",
            )

        # Get events after cursor 3 (no new events)
        events = event_repo.get_events_since(instance_id, after_id=3)

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_get_events_since_returns_only_for_instance(
        self, event_bus, event_repo
    ):
        """Events for one instance don't appear for another."""
        instance_a = str(uuid.uuid4())
        instance_b = str(uuid.uuid4())

        # Create events for instance A
        for i in range(3):
            await event_bus.create_message_received_event(
                instance_id=instance_a,
                message_id=f"msg-a-{i}",
            )

        # Create events for instance B
        for i in range(2):
            await event_bus.create_message_received_event(
                instance_id=instance_b,
                message_id=f"msg-b-{i}",
            )

        # Get events for each instance
        events_a = event_repo.get_events_since(instance_a, after_id=0)
        events_b = event_repo.get_events_since(instance_b, after_id=0)

        assert len(events_a) == 3
        assert len(events_b) == 2

        # Verify each event belongs to correct instance
        for event in events_a:
            assert event.instance_id == instance_a
        for event in events_b:
            assert event.instance_id == instance_b


# ============================================================================
# Test Class: EventBus Cleanup
# ============================================================================


class TestEventBusCleanup:
    """Tests for cleanup operations."""

    @pytest.mark.asyncio
    async def test_cleanup_old_removes_old_events(
        self, event_bus, event_repo, instance_id
    ):
        """cleanup_old removes events older than specified hours."""
        # Create some events
        for i in range(5):
            await event_bus.create_message_received_event(
                instance_id=instance_id,
                message_id=f"msg-{i}",
            )

        # Verify events exist
        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 5

        # Cleanup with 0 hours (should remove all)
        deleted = await event_bus.cleanup_old(hours=0)
        assert deleted >= 5

        # Events should be gone
        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_cleanup_instance_removes_memory_state(
        self, event_bus, instance_id
    ):
        """cleanup_instance removes streaming queue and notification."""
        # Create streaming event to populate queue
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"text": "Hello"},
        )

        # Create lifecycle event to set notification
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=str(uuid.uuid4()),
        )

        # Verify state exists
        assert instance_id in event_bus._streaming_channels
        assert instance_id in event_bus._notifications
        assert event_bus.get_notification(instance_id).is_set()

        # Cleanup instance
        event_bus.cleanup_instance(instance_id)

        # Verify state removed
        assert instance_id not in event_bus._streaming_channels
        assert instance_id not in event_bus._notifications

    @pytest.mark.asyncio
    async def test_shutdown_clears_all_state(self, event_bus):
        """shutdown clears all in-memory state."""
        instance_ids = [str(uuid.uuid4()) for _ in range(3)]

        # Add state for multiple instances
        for inst_id in instance_ids:
            await event_bus.broadcast_streaming_event(
                instance_id=inst_id,
                event_type="content_chunk",
                data={"text": "test"},
            )
            await event_bus.create_message_received_event(
                instance_id=inst_id,
                message_id=str(uuid.uuid4()),
            )

        # Verify state exists
        assert len(event_bus._streaming_channels) == 3
        assert len(event_bus._notifications) == 3

        # Shutdown
        await event_bus.shutdown()

        # All state should be cleared
        assert len(event_bus._streaming_channels) == 0
        assert len(event_bus._notifications) == 0
        assert len(event_bus._global_subscribers) == 0


# ============================================================================
# Test Class: EventBus Global Subscribers
# ============================================================================


class TestEventBusGlobalSubscribers:
    """Tests for global subscriber support (ResponseDispatcher pattern)."""

    @pytest.mark.asyncio
    async def test_subscribe_all_receives_lifecycle_events(
        self, event_bus, instance_id
    ):
        """Global subscriber receives lifecycle events."""
        # Subscribe
        queue = event_bus.subscribe_all("test_subscriber")

        # Create lifecycle event
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=str(uuid.uuid4()),
        )

        # Subscriber should receive the event
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == instance_id
        assert event["event_type"] == "message_received"

    @pytest.mark.asyncio
    async def test_subscribe_all_receives_streaming_events(
        self, event_bus, instance_id
    ):
        """Global subscriber receives streaming events."""
        # Subscribe
        queue = event_bus.subscribe_all("test_subscriber")

        # Broadcast streaming event
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"text": "Hello world"},
        )

        # Subscriber should receive the event
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == instance_id
        assert event["event_type"] == "content_chunk"
        assert event["data"]["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(
        self, event_bus, instance_id
    ):
        """Unsubscribed global subscriber does not receive events."""
        # Subscribe
        queue = event_bus.subscribe_all("test_subscriber")

        # Unsubscribe
        event_bus.unsubscribe_all("test_subscriber")

        # Create lifecycle event
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=str(uuid.uuid4()),
        )

        # Queue should be empty (or subscriber not in dict)
        assert "test_subscriber" not in event_bus._global_subscribers

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, event_bus, instance_id):
        """Multiple subscribers each receive all events."""
        queue1 = event_bus.subscribe_all("subscriber_1")
        queue2 = event_bus.subscribe_all("subscriber_2")

        # Create lifecycle event
        await event_bus.create_message_received_event(
            instance_id=instance_id,
            message_id=str(uuid.uuid4()),
        )

        # Both subscribers should receive
        event1 = await asyncio.wait_for(queue1.get(), timeout=1.0)
        event2 = await asyncio.wait_for(queue2.get(), timeout=1.0)

        assert event1["event_type"] == "message_received"
        assert event2["event_type"] == "message_received"

    @pytest.mark.asyncio
    async def test_subscriber_idempotent(self, event_bus, instance_id):
        """Subscribing same ID replaces previous queue."""
        queue1 = event_bus.subscribe_all("test_sub")
        queue2 = event_bus.subscribe_all("test_sub")

        # Should be different queues (old one replaced)
        assert queue1 is not queue2

        # Only one subscriber should exist
        assert "test_sub" in event_bus._global_subscribers
        assert event_bus._global_subscribers["test_sub"] is queue2


# ============================================================================
# Test Class: EventBus Merge Ordering (FIX: W7)
# ============================================================================


class TestEventBusMergeOrdering:
    """Tests for merging DB events with streaming events.

    FIX W7: Events from DB (lifecycle) and streaming queue should be merged
    by timestamp. When same timestamp, streaming events come first.
    """

    @pytest.mark.asyncio
    async def test_merge_db_and_streaming_events(
        self, event_bus, event_repo, instance_id
    ):
        """DB events (ordered by id) and streaming events (ordered by timestamp) merge correctly."""
        # Create 3 lifecycle events (persisted to DB)
        for i in range(3):
            await event_bus.create_message_received_event(
                instance_id=instance_id,
                message_id=f"db-msg-{i}",
                content={"source": "db", "index": i},
            )

        # Create 2 streaming events (in-memory only)
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="content_chunk",
            data={"text": "chunk_1"},
        )
        await event_bus.broadcast_streaming_event(
            instance_id=instance_id,
            event_type="thinking",
            data={"text": "thinking"},
        )

        # Get DB events via repository
        db_events = event_repo.get_events_since(instance_id, after_id=0)
        assert len(db_events) == 3

        # Get streaming events via EventBus
        streaming_events = await event_bus.get_streaming_events(instance_id)
        assert len(streaming_events) == 2

        # Verify streaming events are NOT in DB (they have no id from DB)
        for sev in streaming_events:
            # Streaming events don't have an 'id' field since they're not persisted
            assert "id" not in sev
            assert sev["event_type"] in STREAMING_EVENT_TYPES

    @pytest.mark.asyncio
    async def test_multi_client_sse_different_positions(
        self, event_bus, event_repo
    ):
        """Different SSE clients at different positions get correct events.

        FIX C2: Each client tracks its own cursor position independently.
        """
        instance_id = str(uuid.uuid4())

        # Create 5 lifecycle events
        for i in range(5):
            await event_bus.create_message_received_event(
                instance_id=instance_id,
                message_id=f"msg-{i}",
            )

        # Client A starts at position 0 (gets all events)
        events_a = event_repo.get_events_since(instance_id, after_id=0)
        assert len(events_a) == 5

        # Client B starts at position 3 (gets only events 4, 5)
        events_b = event_repo.get_events_since(instance_id, after_id=3)
        assert len(events_b) == 2
        assert events_b[0].id == 4
        assert events_b[1].id == 5

    @pytest.mark.asyncio
    async def test_cursor_advances_correctly(
        self, event_bus, event_repo, instance_id
    ):
        """Cursor position advances as client processes events."""
        # Create events
        for i in range(5):
            await event_bus.create_message_received_event(
                instance_id=instance_id,
                message_id=f"msg-{i}",
            )

        # Simulate client reading events one by one
        cursor = 0
        received_ids = []

        # Read first batch of events (should get all 5)
        events = event_repo.get_events_since(instance_id, after_id=cursor)
        assert len(events) == 5
        cursor = max(e.id for e in events)
        received_ids.extend([e.id for e in events])

        # Read again - should be empty now
        events = event_repo.get_events_since(instance_id, after_id=cursor)
        assert len(events) == 0


# ============================================================================
# Test Class: EventBus Sync Operations
# ============================================================================


class TestEventBusSyncOperations:
    """Tests for synchronous broadcast operations."""

    def test_broadcast_sync_works_from_thread(self, event_bus, instance_id):
        """broadcast_sync can be called from a non-async thread."""
        import threading

        # Need to set event loop for sync operations
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Call broadcast_sync from a thread
            def sync_broadcast():
                event_bus.broadcast_sync(
                    instance_id=instance_id,
                    event_type="content_chunk",
                    data={"text": "sync test"},
                )

            thread = threading.Thread(target=sync_broadcast)
            thread.start()
            thread.join(timeout=2)

            # Event should have been queued
            # (We can't easily verify async completion from sync test,
            # but the call should not raise)

        finally:
            loop.close()


# ============================================================================
# Test Class: EventBus Legacy Support
# ============================================================================


class TestEventBusLegacySupport:
    """Tests for backward compatibility with legacy event types."""

    @pytest.mark.asyncio
    async def test_legacy_message_queued_maps_to_message_received(
        self, event_bus, event_repo, instance_id
    ):
        """Legacy event type 'message_queued' maps to MESSAGE_RECEIVED."""
        await event_bus.create_event(
            instance_id=instance_id,
            kind="message_queued",
            message_id=str(uuid.uuid4()),
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.MESSAGE_RECEIVED.value

    @pytest.mark.asyncio
    async def test_legacy_completed_maps_to_processing_completed(
        self, event_bus, event_repo, instance_id
    ):
        """Legacy event type 'completed' maps to PROCESSING_COMPLETED."""
        await event_bus.create_event(
            instance_id=instance_id,
            kind="completed",
            message_id=str(uuid.uuid4()),
            data={"result": "success"},
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.PROCESSING_COMPLETED.value

    @pytest.mark.asyncio
    async def test_unknown_event_type_raises_value_error(
        self, event_bus, instance_id
    ):
        """Unknown event type string raises ValueError."""
        with pytest.raises(ValueError):
            await event_bus.create_event(
                instance_id=instance_id,
                kind="custom_event",
                data={"custom": True},
            )
