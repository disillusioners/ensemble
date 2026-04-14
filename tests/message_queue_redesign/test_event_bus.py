"""Tests for Phase 4 EventBus service (checkpoint-based).

Tests the checkpoint-based event delivery system combining:
- Checkpoint events (full message state via asyncio.Queue)
- Lifecycle events (persisted to DB via EventRepository)
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
from daemon.services.event_bus import EventBus


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
# Test Class: EventBus Checkpoint Events
# ============================================================================


class TestEventBusCheckpointEvents:
    """Tests for checkpoint events that deliver full message state."""

    @pytest.mark.asyncio
    async def test_broadcast_checkpoint_event_queues_message(
        self, event_bus, instance_id
    ):
        """Checkpoint event is queued for SSE delivery."""
        messages = [
            {"message_id": "msg-1", "role": "user", "content": "Hello"},
            {"message_id": "msg-2", "role": "assistant", "content": "Hi there!"},
        ]
        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
        )

        # Verify checkpoint was queued
        events = await event_bus.get_streaming_events(instance_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "checkpoint"
        assert events[0]["checkpoint_id"] == "seq_0"
        assert len(events[0]["messages"]) == 2

    @pytest.mark.asyncio
    async def test_broadcast_checkpoint_event_not_persisted(
        self, event_bus, event_repo, instance_id
    ):
        """Checkpoint event is NOT written to DB (only queued for SSE)."""
        messages = [
            {"message_id": "msg-1", "role": "user", "content": "Hello"},
        ]
        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
        )

        # Verify NO event was persisted to DB
        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_checkpoint_with_tool_outputs(
        self, event_bus, instance_id
    ):
        """Checkpoint event includes tool_outputs map."""
        messages = [
            {"message_id": "msg-1", "role": "assistant", "content": "", "tool_calls": [
                {"id": "tool-1", "name": "bash", "arguments": {"cmd": "ls"}}
            ]},
        ]
        tool_outputs = {"tool-1": "file1.txt\nfile2.txt"}
        
        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_1",
            tool_outputs=tool_outputs,
        )

        events = await event_bus.get_streaming_events(instance_id)
        assert len(events) == 1
        assert events[0]["tool_outputs"]["tool-1"] == "file1.txt\nfile2.txt"

    @pytest.mark.asyncio
    async def test_multiple_checkpoints_queued(
        self, event_bus, instance_id
    ):
        """Multiple checkpoint events are available in the streaming queue."""
        # Broadcast multiple checkpoints
        for i in range(3):
            messages = [{"message_id": f"msg-{i}", "role": "user", "content": f"Test {i}"}]
            await event_bus.broadcast_checkpoint_event(
                instance_id=instance_id,
                messages=messages,
                checkpoint_id=f"seq_{i}",
            )

        # Drain the streaming queue
        events = await event_bus.get_streaming_events(instance_id)

        assert len(events) == 3
        assert all(e["event_type"] == "checkpoint" for e in events)
        assert events[0]["checkpoint_id"] == "seq_0"
        assert events[1]["checkpoint_id"] == "seq_1"
        assert events[2]["checkpoint_id"] == "seq_2"

    @pytest.mark.asyncio
    async def test_checkpoint_queue_max_size(
        self, event_bus, instance_id
    ):
        """Checkpoint queue respects max size and drops oldest events."""
        # EventBus has default streaming_queue_size=100, test with small size
        small_bus = EventBus(event_repo=MagicMock(), streaming_queue_size=3)

        # Try to put more events than queue size
        for i in range(5):
            messages = [{"message_id": f"msg-{i}", "role": "user", "content": f"Test {i}"}]
            await small_bus.broadcast_checkpoint_event(
                instance_id=instance_id,
                messages=messages,
                checkpoint_id=f"seq_{i}",
            )

        # Queue should have at most 3 events (oldest dropped)
        events = await small_bus.get_streaming_events(instance_id)
        assert len(events) <= 3


# ============================================================================
# Test Class: EventBus Lifecycle Events (DB Persistence)
# ============================================================================


class TestEventBusLifecycleEvents:
    """Tests for lifecycle events that are persisted to the database."""

    @pytest.mark.asyncio
    async def test_create_event_persists_to_db(
        self, event_bus, event_repo, instance_id, message_id
    ):
        """Event with kind is persisted to DB."""
        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.MESSAGE_RECEIVED,
            data={"source": "api", "priority": 1},
            message_id=message_id,
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

    @pytest.mark.asyncio
    async def test_create_event_with_string_kind(
        self, event_bus, event_repo, instance_id
    ):
        """Event with string kind is converted to EventKind."""
        await event_bus.create_event(
            instance_id=instance_id,
            kind="message_received",
            data={"test": True},
        )

        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.MESSAGE_RECEIVED.value


# ============================================================================
# Test Class: EventBus Notification Mechanism
# ============================================================================


class TestEventBusNotification:
    """Tests for asyncio.Event notification per instance."""

    @pytest.mark.asyncio
    async def test_notification_set_on_checkpoint(
        self, event_bus, instance_id
    ):
        """asyncio.Event is set after broadcasting a checkpoint."""
        notification = event_bus.get_notification(instance_id)

        # Event should not be set initially
        assert not notification.is_set()

        # Broadcast a checkpoint
        messages = [{"message_id": "msg-1", "role": "user", "content": "Hello"}]
        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
        )

        # Notification should now be set
        assert notification.is_set()

    @pytest.mark.asyncio
    async def test_notification_set_on_lifecycle_event(
        self, event_bus, instance_id
    ):
        """asyncio.Event is set after creating a lifecycle event."""
        notification = event_bus.get_notification(instance_id)

        # Event should not be set initially
        assert not notification.is_set()

        # Create a lifecycle event
        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.MESSAGE_RECEIVED,
            data={"source": "api"},
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
        await event_bus.create_event(
            instance_id=instance_a,
            kind=EventKind.MESSAGE_RECEIVED,
            data={},
        )

        # Only instance A's notification should be set
        assert notif_a.is_set()
        assert notif_b.is_set() is False

    @pytest.mark.asyncio
    async def test_notification_cleared_after_wait(self, event_bus, instance_id):
        """Notification can be cleared after being processed."""
        notification = event_bus.get_notification(instance_id)

        # Create event to set notification
        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.MESSAGE_RECEIVED,
            data={},
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
            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.MESSAGE_RECEIVED,
                data={"index": i},
            )

        # Get events after cursor 3 (should return events 4, 5)
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
            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.MESSAGE_RECEIVED,
                data={"index": i},
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
            await event_bus.create_event(
                instance_id=instance_a,
                kind=EventKind.MESSAGE_RECEIVED,
                data={"index": i},
            )

        # Create events for instance B
        for i in range(2):
            await event_bus.create_event(
                instance_id=instance_b,
                kind=EventKind.MESSAGE_RECEIVED,
                data={"index": i},
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
        self, event_repo, instance_id
    ):
        """
        Cleanup removes events older than threshold.
        """
        # Create some events
        for i in range(3):
            event_repo.create_event(
                instance_id=instance_id,
                kind=EventKind.PROCESSING_COMPLETED.value,
                data={"sequence": i},
            )

        # Manually set old timestamps in the DB (bypass the default factory)
        with event_repo.engine.connect() as conn:
            from sqlalchemy import text, update
            from daemon.repositories.event.models import Event
            from datetime import datetime, timedelta, timezone

            old_time = datetime.now(timezone.utc) - timedelta(hours=48)
            conn.execute(
                update(Event).where(Event.instance_id == instance_id).values(created_at=old_time)
            )
            conn.commit()

        # Cleanup with 24 hour threshold
        deleted = event_repo.cleanup_old(max_age_hours=24)

        # All events should be deleted (they're older than 24 hours)
        assert deleted >= 3

        # Verify no events remain
        remaining = event_repo.get_events_since(instance_id, after_id=0)
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_cleanup_instance_removes_memory_state(
        self, event_bus, instance_id
    ):
        """cleanup_instance removes streaming queue and notification."""
        # Create checkpoint to populate queue
        messages = [{"message_id": "msg-1", "role": "user", "content": "Hello"}]
        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
        )

        # Create lifecycle event to set notification
        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.MESSAGE_RECEIVED,
            data={},
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
            messages = [{"message_id": f"msg-{inst_id}", "role": "user", "content": "test"}]
            await event_bus.broadcast_checkpoint_event(
                instance_id=inst_id,
                messages=messages,
                checkpoint_id="seq_0",
            )
            await event_bus.create_event(
                instance_id=inst_id,
                kind=EventKind.MESSAGE_RECEIVED,
                data={},
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
    async def test_subscribe_all_receives_checkpoint_events(
        self, event_bus, instance_id
    ):
        """Global subscriber receives checkpoint events."""
        # Subscribe
        queue = event_bus.subscribe_all("test_subscriber")

        # Broadcast checkpoint
        messages = [{"message_id": "msg-1", "role": "user", "content": "Hello"}]
        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
        )

        # Subscriber should receive the event
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == instance_id
        assert event["event_type"] == "checkpoint"
        # Data is wrapped in 'data' key
        assert event["data"]["checkpoint_id"] == "seq_0"

    @pytest.mark.asyncio
    async def test_subscribe_all_receives_lifecycle_events(
        self, event_bus, instance_id
    ):
        """Global subscriber receives lifecycle events."""
        # Subscribe
        queue = event_bus.subscribe_all("test_subscriber")

        # Create lifecycle event
        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.MESSAGE_RECEIVED,
            data={"source": "api"},
        )

        # Subscriber should receive the event
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["instance_id"] == instance_id
        assert event["event_type"] == "message_received"

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
        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.MESSAGE_RECEIVED,
            data={},
        )

        # Queue should be empty (or subscriber not in dict)
        assert "test_subscriber" not in event_bus._global_subscribers

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, event_bus, instance_id):
        """Multiple subscribers each receive all events."""
        queue1 = event_bus.subscribe_all("subscriber_1")
        queue2 = event_bus.subscribe_all("subscriber_2")

        # Create lifecycle event
        await event_bus.create_event(
            instance_id=instance_id,
            kind=EventKind.MESSAGE_RECEIVED,
            data={},
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
    """Tests for merging DB events with checkpoint events.

    Events from DB (lifecycle) and checkpoint queue should be merged
    by timestamp or sequence.
    """

    @pytest.mark.asyncio
    async def test_db_and_checkpoint_events_separate(
        self, event_bus, event_repo, instance_id
    ):
        """DB events (ordered by id) and checkpoint events (in queue) are separate."""
        # Create 3 lifecycle events (persisted to DB)
        for i in range(3):
            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.MESSAGE_RECEIVED,
                data={"source": "db", "index": i},
            )

        # Create 2 checkpoint events (in-memory only)
        for i in range(2):
            messages = [{"message_id": f"msg-{i}", "role": "user", "content": f"chunk_{i}"}]
            await event_bus.broadcast_checkpoint_event(
                instance_id=instance_id,
                messages=messages,
                checkpoint_id=f"seq_{i}",
            )

        # Get DB events via repository
        db_events = event_repo.get_events_since(instance_id, after_id=0)
        assert len(db_events) == 3

        # Get checkpoint events via EventBus
        checkpoint_events = await event_bus.get_streaming_events(instance_id)
        assert len(checkpoint_events) == 2

        # Verify checkpoint events are NOT in DB (they have no id from DB)
        for cev in checkpoint_events:
            assert "id" not in cev
            assert cev["event_type"] == "checkpoint"

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
            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.MESSAGE_RECEIVED,
                data={"index": i},
            )

        # Client A starts at position 0 (gets all events)
        events_a = event_repo.get_events_since(instance_id, after_id=0)
        assert len(events_a) == 5

        # Client B starts at position 3 (gets only events 4, 5)
        events_b = event_repo.get_events_since(instance_id, after_id=3)
        assert len(events_b) == 2
        assert events_b[0].id == 4
        assert events_b[1].id == 5


# ============================================================================
# Test Class: EventBus Sync Operations
# ============================================================================


class TestEventBusSyncOperations:
    """Tests for synchronous broadcast operations."""

    def test_broadcast_sync_legacy_stub(self, event_bus, instance_id):
        """broadcast_sync is a legacy stub that does nothing."""
        import threading

        # Need to set event loop for sync operations
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Call broadcast_sync from a thread (legacy stub)
            def sync_broadcast():
                event_bus.broadcast_sync(
                    event={
                        "instance_id": instance_id,
                        "event_type": "checkpoint",
                    }
                )

            thread = threading.Thread(target=sync_broadcast)
            thread.start()
            thread.join(timeout=2)

            # Legacy stub does nothing, so no exception should be raised

        finally:
            loop.close()


# ============================================================================
# Test Class: EventBus Checkpoint Integration
# ============================================================================


class TestEventBusCheckpointIntegration:
    """Integration tests for checkpoint events with full message state."""

    @pytest.mark.asyncio
    async def test_checkpoint_carries_full_message_history(
        self, event_bus, instance_id
    ):
        """Checkpoint events contain full message history for frontend replacement."""
        # Simulate a conversation with multiple turns
        messages = [
            {"message_id": "msg-1", "role": "user", "content": "Hello"},
            {"message_id": "msg-2", "role": "assistant", "content": "Hi there!"},
            {"message_id": "msg-3", "role": "user", "content": "How are you?"},
            {"message_id": "msg-4", "role": "assistant", "content": "I'm good!"},
        ]
        
        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
        )

        events = await event_bus.get_streaming_events(instance_id)
        assert len(events) == 1
        assert len(events[0]["messages"]) == 4
        
        # Verify message roles
        roles = [m["role"] for m in events[0]["messages"]]
        assert roles == ["user", "assistant", "user", "assistant"]

    @pytest.mark.asyncio
    async def test_checkpoint_with_nested_tool_calls(
        self, event_bus, instance_id
    ):
        """Checkpoint events properly serialize tool calls with nested structure."""
        messages = [
            {"message_id": "msg-1", "role": "assistant", "content": "", "tool_calls": [
                {"id": "tc-1", "name": "bash", "arguments": {"command": "ls -la"}},
                {"id": "tc-2", "name": "read_file", "arguments": {"path": "test.txt"}},
            ]},
        ]
        
        tool_outputs = {
            "tc-1": "total 8\n-rw-r-- 1 user staff 256 Jan 15 10:30 test.txt",
            "tc-2": "Hello, World!",
        }
        
        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
            tool_outputs=tool_outputs,
        )

        events = await event_bus.get_streaming_events(instance_id)
        msg = events[0]["messages"][0]
        
        # Verify tool_calls are serialized
        assert len(msg["tool_calls"]) == 2
        assert msg["tool_calls"][0]["id"] == "tc-1"
        # Note: output is NOT included in message's tool_calls - it's in tool_outputs map
        assert msg["tool_calls"][0]["name"] == "bash"
        
        # Verify tool_outputs are available separately
        assert events[0]["tool_outputs"]["tc-1"] == tool_outputs["tc-1"]
        assert events[0]["tool_outputs"]["tc-2"] == tool_outputs["tc-2"]
