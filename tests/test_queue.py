"""Tests for daemon/queue.py - Queue types (QueueStats, QueuedMessage, MessageStatus)."""

import pytest
from datetime import datetime, timezone

from daemon.queue import (
    QueuedMessage,
    QueueStats,
    MessageStatus,
)


class TestQueueStats:
    """Tests for QueueStats dataclass."""

    def test_queue_stats_defaults(self):
        """Test QueueStats with default values."""
        stats = QueueStats(
            pending_count=0,
            processing_count=0,
            oldest_message_age_seconds=None
        )
        
        assert stats.pending_count == 0
        assert stats.processing_count == 0
        assert stats.oldest_message_age_seconds is None

    def test_queue_stats_with_values(self):
        """Test QueueStats with actual values."""
        stats = QueueStats(
            pending_count=10,
            processing_count=2,
            oldest_message_age_seconds=3600.5
        )
        
        assert stats.pending_count == 10
        assert stats.processing_count == 2
        assert stats.oldest_message_age_seconds == 3600.5


class TestQueuedMessage:
    """Tests for QueuedMessage dataclass."""

    def test_queued_message_defaults(self):
        """Test QueuedMessage with default values."""
        msg = QueuedMessage(
            message_id="test-id",
            instance_id="test-instance",
            content="test content",
            source="test"
        )
        
        assert msg.message_id == "test-id"
        assert msg.instance_id == "test-instance"
        assert msg.content == "test content"
        assert msg.source == "test"
        assert msg.priority == 1
        assert msg.retry_count == 0
        assert msg.metadata == {}
        assert msg.status == "ready"
        assert msg.error_message is None

    def test_queued_message_with_all_fields(self):
        """Test QueuedMessage with all fields specified."""
        now = datetime.now(timezone.utc)
        msg = QueuedMessage(
            message_id="test-id",
            instance_id="test-instance",
            content="test content",
            source="test",
            priority=0,
            retry_count=3,
            metadata={"key": "value"},
            created_at=now,
            processing_started_at=now,
            status="processing",
            error_message="Previous error"
        )

        assert msg.message_id == "test-id"
        assert msg.instance_id == "test-instance"
        assert msg.content == "test content"
        assert msg.source == "test"
        assert msg.priority == 0
        assert msg.retry_count == 3
        assert msg.metadata == {"key": "value"}
        assert msg.created_at == now
        assert msg.processing_started_at == now
        assert msg.status == "processing"
        # admission_state was removed from QueuedMessage; status alone is
        # the surviving column for message lifecycle state.
        assert msg.error_message == "Previous error"
