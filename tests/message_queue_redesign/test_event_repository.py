"""Tests for EventRepository."""

import pytest
from datetime import datetime, timezone

from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.event.repository import EventRepository


class TestEventCreation:
    """Tests for event creation."""

    def test_create_event(self, engine):
        """Test creating a basic event."""
        repo = EventRepository(engine)
        
        event = repo.create_event(
            instance_id="test-instance-123",
            kind=EventKind.MESSAGE_RECEIVED.value,
            data={"message": "Hello"},
        )
        
        assert event.id is not None
        assert event.instance_id == "test-instance-123"
        assert event.kind == EventKind.MESSAGE_RECEIVED.value
        assert event.data is not None
        assert event.created_at is not None

    def test_create_event_without_data(self, engine):
        """Test creating an event without data."""
        repo = EventRepository(engine)
        
        event = repo.create_event(
            instance_id="test-instance-123",
            kind=EventKind.PROCESSING_STARTED.value,
        )
        
        assert event.id is not None
        assert event.data is None

    def test_create_event_all_kinds(self, engine):
        """Test creating events of all kinds."""
        repo = EventRepository(engine)
        
        for kind in EventKind:
            event = repo.create_event(
                instance_id="test-instance",
                kind=kind.value,
            )
            assert event.kind == kind.value

    def test_create_event_with_message_id(self, engine):
        """Test creating an event with message_id."""
        repo = EventRepository(engine)
        
        event = repo.create_event(
            instance_id="test-instance-123",
            kind=EventKind.MESSAGE_RECEIVED.value,
            data={"message": "Hello"},
            message_id="msg-abc-123",
        )
        
        assert event.id is not None
        assert event.instance_id == "test-instance-123"
        assert event.message_id == "msg-abc-123"
        assert event.kind == EventKind.MESSAGE_RECEIVED.value

    def test_create_event_without_message_id(self, engine):
        """Test creating an event without message_id (should be None)."""
        repo = EventRepository(engine)
        
        event = repo.create_event(
            instance_id="test-instance",
            kind=EventKind.PROCESSING_STARTED.value,
        )
        
        assert event.message_id is None


class TestMessageIdCorrelation:
    """Tests for event-to-message correlation via message_id."""

    def test_event_message_correlation(self, engine):
        """Test correlating events with a message via message_id."""
        repo = EventRepository(engine)
        message_id = "msg-correlation-test"
        
        # Create multiple events for the same message
        event1 = repo.create_event(
            instance_id="test-instance",
            kind=EventKind.MESSAGE_RECEIVED.value,
            message_id=message_id,
        )
        event2 = repo.create_event(
            instance_id="test-instance",
            kind=EventKind.PROCESSING_STARTED.value,
            message_id=message_id,
        )
        
        # Retrieve all events
        events = repo.get_by_instance("test-instance")
        
        # Find events with the same message_id
        correlated_events = [e for e in events if e.message_id == message_id]
        assert len(correlated_events) == 2
        assert correlated_events[0].kind == EventKind.MESSAGE_RECEIVED.value
        assert correlated_events[1].kind == EventKind.PROCESSING_STARTED.value

    def test_events_from_different_messages(self, engine):
        """Test that events from different messages are distinguishable."""
        repo = EventRepository(engine)
        
        # Create events for different messages
        repo.create_event(
            instance_id="test-instance",
            kind=EventKind.MESSAGE_RECEIVED.value,
            message_id="msg-1",
        )
        repo.create_event(
            instance_id="test-instance",
            kind=EventKind.MESSAGE_RECEIVED.value,
            message_id="msg-2",
        )
        repo.create_event(
            instance_id="test-instance",
            kind=EventKind.PROCESSING_STARTED.value,
            message_id="msg-1",
        )
        
        events = repo.get_by_instance("test-instance")
        assert len(events) == 3
        
        # Count events per message
        msg1_events = [e for e in events if e.message_id == "msg-1"]
        msg2_events = [e for e in events if e.message_id == "msg-2"]
        
        assert len(msg1_events) == 2
        assert len(msg2_events) == 1


class TestEventRetrieval:
    """Tests for event retrieval."""

    def test_get_event(self, engine):
        """Test getting an event by ID."""
        repo = EventRepository(engine)
        
        created = repo.create_event(
            instance_id="test-instance",
            kind=EventKind.MESSAGE_RECEIVED.value,
        )
        
        retrieved = repo.get(created.id)
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.kind == created.kind

    def test_get_event_not_found(self, engine):
        """Test getting non-existent event."""
        repo = EventRepository(engine)
        
        result = repo.get(99999)
        assert result is None

    def test_get_by_instance(self, engine):
        """Test getting all events for an instance."""
        repo = EventRepository(engine)
        
        # Create events for two instances
        for _ in range(3):
            repo.create_event(instance_id="instance-1", kind=EventKind.MESSAGE_RECEIVED.value)
        for _ in range(2):
            repo.create_event(instance_id="instance-2", kind=EventKind.MESSAGE_RECEIVED.value)
        
        # Get events for instance-1
        events = repo.get_by_instance("instance-1")
        assert len(events) == 3
        
        # Get events for instance-2
        events = repo.get_by_instance("instance-2")
        assert len(events) == 2


class TestCursorBasedDelivery:
    """Tests for cursor-based event delivery (SSE)."""

    def test_get_events_since_no_cursor(self, engine):
        """Test getting events without cursor (initial connection)."""
        repo = EventRepository(engine)
        
        # Create events
        for i in range(5):
            repo.create_event(instance_id="test-instance", kind=EventKind.MESSAGE_RECEIVED.value)
        
        # Get events without cursor - should return most recent (up to limit)
        events = repo.get_events_since("test-instance", after_id=None, limit=3)
        
        # Should return up to limit events
        assert len(events) <= 3
        # Events should be in chronological order
        for i in range(len(events) - 1):
            assert events[i].id <= events[i + 1].id

    def test_get_events_since_with_cursor(self, engine):
        """Test getting events with cursor position."""
        repo = EventRepository(engine)
        
        # Create 5 events
        events = []
        for i in range(5):
            event = repo.create_event(instance_id="test-instance", kind=EventKind.MESSAGE_RECEIVED.value)
            events.append(event)
        
        # Get events after cursor (after event 2)
        after_id = events[1].id
        retrieved = repo.get_events_since("test-instance", after_id=after_id)
        
        # Should return events 3, 4, 5
        assert len(retrieved) == 3
        # Should not include events before cursor
        assert all(e.id > after_id for e in retrieved)

    def test_get_events_since_empty_result(self, engine):
        """Test cursor beyond all events."""
        repo = EventRepository(engine)
        
        # Create events
        event = repo.create_event(instance_id="test-instance", kind=EventKind.MESSAGE_RECEIVED.value)
        
        # Get events after the last one
        result = repo.get_events_since("test-instance", after_id=event.id)
        
        assert len(result) == 0

    def test_get_events_since_different_instances(self, engine):
        """Test cursor-based delivery for different instances."""
        repo = EventRepository(engine)
        
        # Create interleaved events for different instances
        repo.create_event(instance_id="instance-1", kind=EventKind.MESSAGE_RECEIVED.value)
        repo.create_event(instance_id="instance-2", kind=EventKind.MESSAGE_RECEIVED.value)
        repo.create_event(instance_id="instance-1", kind=EventKind.PROCESSING_STARTED.value)
        
        # Get events for instance-1 after first event
        events = repo.get_by_instance("instance-1")
        first_event_id = events[0].id
        
        # Should only get instance-1 events after cursor
        result = repo.get_events_since("instance-1", after_id=first_event_id)
        
        # Should only include the PROCESSING_STARTED event
        assert len(result) == 1
        assert result[0].kind == EventKind.PROCESSING_STARTED.value


class TestEventStats:
    """Tests for event statistics."""

    def test_get_latest_event_id(self, engine):
        """Test getting the latest event ID for an instance."""
        repo = EventRepository(engine)
        
        # Initially no events
        assert repo.get_latest_event_id("test-instance") is None
        
        # Create events
        events = []
        for _ in range(3):
            event = repo.create_event(instance_id="test-instance", kind=EventKind.MESSAGE_RECEIVED.value)
            events.append(event)
        
        latest = repo.get_latest_event_id("test-instance")
        assert latest == events[-1].id

    def test_count_by_instance(self, engine):
        """Test counting events for an instance."""
        repo = EventRepository(engine)
        
        # Create events for two instances
        for _ in range(3):
            repo.create_event(instance_id="instance-1", kind=EventKind.MESSAGE_RECEIVED.value)
        for _ in range(5):
            repo.create_event(instance_id="instance-2", kind=EventKind.MESSAGE_RECEIVED.value)
        
        assert repo.count_by_instance("instance-1") == 3
        assert repo.count_by_instance("instance-2") == 5


class TestEventCleanup:
    """Tests for event cleanup."""

    def test_cleanup_old(self, engine):
        """Test cleaning up old events."""
        repo = EventRepository(engine)
        
        # Create some events
        for _ in range(5):
            repo.create_event(instance_id="test-instance", kind=EventKind.MESSAGE_RECEIVED.value)
        
        # Clean up events older than 24 hours (none should be deleted)
        deleted = repo.cleanup_old(max_age_hours=24)
        assert deleted == 0
        
        # Verify all events still exist
        assert repo.count_by_instance("test-instance") == 5

    def test_delete_by_instance(self, engine):
        """Test deleting all events for an instance."""
        repo = EventRepository(engine)
        
        # Create events for two instances
        for _ in range(3):
            repo.create_event(instance_id="instance-1", kind=EventKind.MESSAGE_RECEIVED.value)
        for _ in range(2):
            repo.create_event(instance_id="instance-2", kind=EventKind.MESSAGE_RECEIVED.value)
        
        # Delete all for instance-1
        count = repo.delete_by_instance("instance-1")
        assert count == 3
        
        # Verify instance-1 events are gone
        assert repo.count_by_instance("instance-1") == 0
        
        # Verify instance-2 events remain
        assert repo.count_by_instance("instance-2") == 2
