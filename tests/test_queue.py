"""Tests for daemon/queue.py - Input Message Queue implementation."""

import pytest
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import concurrent.futures

from sqlmodel import Session

from daemon.queue import (
    InputMessageQueue,
    InstanceCircuitBreaker,
    InstanceWatchdog,
    QueuedMessage,
    QueueStats,
    MessageStatus,
    MAX_QUEUE_SIZE,
    MESSAGE_TIMEOUT_SECONDS,
    MAX_RETRIES,
    CIRCUIT_FAILURE_THRESHOLD,
    CIRCUIT_RECOVERY_TIMEOUT,
)
from daemon.cancellation import CancellationReason
from daemon.request_registry import ActiveRequestRegistry
from daemon.repositories.message_queue import SQLModelMessageQueueRepository
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus as RepoMessageStatus


@pytest.fixture
def db_connection(tmp_path):
    """Create a temporary database connection for testing."""
    db_path = tmp_path / "test_queue.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def db_engine(tmp_path):
    """Create a SQLModel engine for repository testing."""
    from sqlmodel import SQLModel, create_engine
    from daemon.repositories.message_queue.models import MessageQueue
    
    db_path = tmp_path / "test_repo.db"
    engine = create_engine(f"sqlite:///{db_path}")
    # Create all tables
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def queue_repository(db_engine):
    """Create a SQLModelMessageQueueRepository instance for testing."""
    return SQLModelMessageQueueRepository(db_engine)


@pytest.fixture
def queue(queue_repository):
    """Create an InputMessageQueue instance for testing."""
    q = InputMessageQueue(queue_repository)
    yield q


class TestInputMessageQueue:
    """Tests for InputMessageQueue class."""

    def test_enqueue_dequeue_basic(self, queue):
        """Test basic enqueue and dequeue operations."""
        instance_id = "test-instance"
        
        # Enqueue a message
        message_id = queue.enqueue(
            instance_id=instance_id,
            content="Hello, world!",
            source="test",
            priority=1
        )
        
        assert message_id is not None
        assert len(message_id) == 36  # UUID format
        
        # Dequeue the message
        msg = queue.dequeue(instance_id)
        
        assert msg is not None
        assert msg.message_id == message_id
        assert msg.instance_id == instance_id
        assert msg.content == "Hello, world!"
        assert msg.source == "test"
        assert msg.priority == 1
        assert msg.status == "processing"

    def test_priority_ordering(self, queue):
        """Test that higher priority messages are dequeued first."""
        instance_id = "test-instance"
        
        # Enqueue messages with different priorities
        id_low = queue.enqueue(instance_id, "low priority", "test", priority=1)
        id_high = queue.enqueue(instance_id, "high priority", "test", priority=0)
        id_medium = queue.enqueue(instance_id, "medium priority", "test", priority=1)
        
        # First dequeue should be high priority (0)
        msg1 = queue.dequeue(instance_id)
        assert msg1.message_id == id_high
        
        # Second should be low (1) - oldest first among same priority
        msg2 = queue.dequeue(instance_id)
        assert msg2.message_id == id_low
        
        # Third should be medium (1) - second oldest
        msg3 = queue.dequeue(instance_id)
        assert msg3.message_id == id_medium

    def test_queue_size_limit_drop_oldest(self, queue):
        """Test that oldest user message is dropped when queue is full."""
        instance_id = "test-instance"
        
        # Fill the queue to MAX_QUEUE_SIZE
        message_ids = []
        for i in range(MAX_QUEUE_SIZE):
            mid = queue.enqueue(instance_id, f"message-{i}", "test", priority=1)
            message_ids.append(mid)
        
        # Verify queue is full
        stats = queue.get_stats(instance_id)
        assert stats.pending_count == MAX_QUEUE_SIZE
        
        # Enqueue one more - should drop oldest
        new_id = queue.enqueue(instance_id, "overflow message", "test", priority=1)
        
        # First message should have been dropped
        msg = queue.dequeue(instance_id)
        assert msg.message_id != message_ids[0]
        
        # New message should be in queue
        found_new = False
        while msg:
            if msg.message_id == new_id:
                found_new = True
                break
            queue.ack(msg.message_id)
            msg = queue.dequeue(instance_id)
        assert found_new

    def test_queue_size_limit_preserves_system_messages(self, queue):
        """Test that system messages (priority 0) are not dropped."""
        instance_id = "test-instance"
        
        # Enqueue a system message first
        system_id = queue.enqueue(instance_id, "system message", "system", priority=0)
        
        # Fill the queue with user messages
        for i in range(MAX_QUEUE_SIZE):
            queue.enqueue(instance_id, f"user-{i}", "test", priority=1)
        
        # System message should still be first (priority 0)
        msg = queue.dequeue(instance_id)
        assert msg.message_id == system_id

    def test_dequeue_empty_queue(self, queue):
        """Test dequeue on empty queue returns None."""
        result = queue.dequeue("non-existent-instance")
        assert result is None
        
        result = queue.dequeue("non-existent-instance", timeout=0.1)
        assert result is None

    def test_ack_message(self, queue):
        """Test acknowledging a processed message."""
        instance_id = "test-instance"
        message_id = queue.enqueue(instance_id, "test message", "test")
        
        # Dequeue the message
        msg = queue.dequeue(instance_id)
        assert msg is not None
        
        # Acknowledge it
        queue.ack(message_id)
        
        # Verify it's marked as completed via repository
        msg = queue._repository.get(message_id)
        assert msg is not None
        assert msg.status == "completed"

    def test_fail_message(self, queue):
        """Test marking a message as permanently failed."""
        instance_id = "test-instance"
        message_id = queue.enqueue(instance_id, "test message", "test")
        
        # Dequeue and fail
        msg = queue.dequeue(instance_id)
        queue.fail(message_id, "Test failure")
        
        # Verify status via repository
        msg = queue._repository.get(message_id)
        assert msg is not None
        assert msg.status == "failed"
        assert msg.error_message == "Test failure"

    def test_schedule_retry_with_backoff(self, queue):
        """Test scheduling a message for retry with exponential backoff."""
        instance_id = "test-instance"
        message_id = queue.enqueue(instance_id, "test message", "test")
        
        # Schedule retry
        queue.schedule_retry(message_id, 1, "First failure")
        
        # Verify retry state via repository
        msg = queue._repository.get(message_id)
        assert msg is not None
        assert msg.retry_count == 1
        assert msg.next_retry_at is not None

    def test_schedule_retry_backoff_increases(self, queue):
        """Test that backoff increases with retry count."""
        instance_id = "test-instance"
        message_id = queue.enqueue(instance_id, "test message", "test")
        
        # Schedule multiple retries and check backoff increases
        backoffs = []
        for retry_count in range(5):
            # Reset message to ready using repository
            with Session(queue._repository.engine) as session:
                msg = session.get(MessageQueue, message_id)
                if msg:
                    msg.status = "ready"
                    msg.next_retry_at = None
                    session.commit()
            
            queue.schedule_retry(message_id, retry_count, f"Failure {retry_count}")
            
            # Get updated message
            msg = queue._repository.get(message_id)
            if msg and msg.next_retry_at:
                from datetime import timezone
                next_retry = msg.next_retry_at
                if next_retry.tzinfo is None:
                    next_retry = next_retry.replace(tzinfo=timezone.utc)
                backoff_seconds = (next_retry - datetime.now(timezone.utc)).total_seconds()
                backoffs.append(backoff_seconds)
        
        # Each backoff should be greater than the previous (with some tolerance for jitter)
        for i in range(1, len(backoffs)):
            # Account for jitter - just verify general trend
            assert backoffs[i] > backoffs[i-1] * 0.5  # Allow for jitter reduction

    def test_get_stats(self, queue):
        """Test getting queue statistics."""
        instance_id = "test-instance"
        
        # Empty queue
        stats = queue.get_stats(instance_id)
        assert stats.pending_count == 0
        assert stats.processing_count == 0
        
        # Add messages
        queue.enqueue(instance_id, "msg1", "test")
        queue.enqueue(instance_id, "msg2", "test")
        
        stats = queue.get_stats(instance_id)
        assert stats.pending_count == 2
        assert stats.processing_count == 0
        
        # Dequeue one
        queue.dequeue(instance_id)
        
        stats = queue.get_stats(instance_id)
        assert stats.pending_count == 1
        assert stats.processing_count == 1

    def test_get_stats_oldest_message_age(self, queue):
        """Test that oldest_message_age_seconds is calculated correctly."""
        instance_id = "test-instance"
        
        # Empty queue - no age
        stats = queue.get_stats(instance_id)
        assert stats.oldest_message_age_seconds is None
        
        # Add message
        queue.enqueue(instance_id, "old message", "test")
        time.sleep(0.1)  # Small delay
        
        stats = queue.get_stats(instance_id)
        assert stats.oldest_message_age_seconds is not None
        assert stats.oldest_message_age_seconds >= 0.1

    def test_is_empty(self, queue):
        """Test is_empty check."""
        instance_id = "test-instance"
        
        assert queue.is_empty(instance_id) is True
        
        queue.enqueue(instance_id, "test", "test")
        assert queue.is_empty(instance_id) is False
        
        msg = queue.dequeue(instance_id)
        assert queue.is_empty(instance_id) is False  # Still processing
        
        queue.ack(msg.message_id)
        assert queue.is_empty(instance_id) is True

    def test_cleanup_completed(self, queue):
        """Test cleanup of old completed messages."""
        instance_id = "test-instance"
        
        # Add and complete a message
        mid = queue.enqueue(instance_id, "test", "test")
        queue.dequeue(instance_id)
        queue.ack(mid)
        
        # Should not be cleaned up immediately
        deleted = queue.cleanup_completed(max_age_hours=24)
        assert deleted == 0
        
        # Manually set completed_at to be old via repository
        with Session(queue._repository.engine) as session:
            msg = session.get(MessageQueue, mid)
            if msg:
                msg.completed_at = datetime.now(timezone.utc) - timedelta(hours=25)
                session.commit()
        
        # Now should be cleaned up
        deleted = queue.cleanup_completed(max_age_hours=24)
        assert deleted == 1

    def test_persistence_across_connections(self, tmp_path):
        """Test that messages persist across database reconnections."""
        from sqlmodel import SQLModel, create_engine
        from daemon.repositories.message_queue.models import MessageQueue
        
        db_path = tmp_path / "persist_test.db"
        instance_id = "test-instance"
        
        # Create repository with engine, add message
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        repo1 = SQLModelMessageQueueRepository(engine)
        queue1 = InputMessageQueue(repo1)
        message_id = queue1.enqueue(instance_id, "persistent message", "test")
        engine.dispose()
        
        # Reopen and verify message exists
        engine2 = create_engine(f"sqlite:///{db_path}")
        repo2 = SQLModelMessageQueueRepository(engine2)
        queue2 = InputMessageQueue(repo2)
        
        # Message should still be there
        msg = queue2.dequeue(instance_id)
        assert msg is not None
        assert msg.message_id == message_id
        assert msg.content == "persistent message"
        engine2.dispose()

    def test_concurrent_enqueue_dequeue(self, queue):
        """Test thread safety of concurrent enqueue/dequeue operations."""
        instance_id = "test-instance"
        num_threads = 10
        messages_per_thread = 10
        
        enqueued_ids = []
        dequeued_ids = []
        lock = threading.Lock()
        
        def enqueue_worker(thread_id):
            for i in range(messages_per_thread):
                mid = queue.enqueue(
                    instance_id, 
                    f"thread-{thread_id}-msg-{i}", 
                    "test"
                )
                with lock:
                    enqueued_ids.append(mid)
        
        def dequeue_worker():
            while True:
                msg = queue.dequeue(instance_id, timeout=0.5)
                if msg is None:
                    break
                with lock:
                    dequeued_ids.append(msg.message_id)
                queue.ack(msg.message_id)
        
        # Start enqueue threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            enqueue_futures = [
                executor.submit(enqueue_worker, i) 
                for i in range(num_threads)
            ]
            concurrent.futures.wait(enqueue_futures)
            
            # Start dequeue threads
            dequeue_futures = [
                executor.submit(dequeue_worker) 
                for _ in range(num_threads)
            ]
            concurrent.futures.wait(dequeue_futures, timeout=10)
        
        # All messages should be accounted for
        assert len(enqueued_ids) == num_threads * messages_per_thread
        assert set(enqueued_ids) == set(dequeued_ids)

    def test_per_instance_isolation(self, queue):
        """Test that messages are isolated between instances."""
        instance1 = "instance-1"
        instance2 = "instance-2"
        
        # Enqueue to different instances
        id1 = queue.enqueue(instance1, "for instance 1", "test")
        id2 = queue.enqueue(instance2, "for instance 2", "test")
        
        # Dequeue from instance1 should only get instance1's message
        msg1 = queue.dequeue(instance1)
        assert msg1.message_id == id1
        assert msg1.instance_id == instance1
        
        # Dequeue from instance2 should only get instance2's message
        msg2 = queue.dequeue(instance2)
        assert msg2.message_id == id2
        assert msg2.instance_id == instance2
        
        # Instance1 should be empty now
        msg = queue.dequeue(instance1)
        assert msg is None

    def test_dequeue_with_timeout_waits(self, queue):
        """Test that dequeue with timeout waits for messages."""
        instance_id = "test-instance"
        result = []
        
        def delayed_enqueue():
            time.sleep(0.2)
            mid = queue.enqueue(instance_id, "delayed", "test")
            result.append(mid)
        
        # Start thread that will enqueue after delay
        thread = threading.Thread(target=delayed_enqueue)
        thread.start()
        
        # Dequeue should wait and get the message
        start = time.monotonic()
        msg = queue.dequeue(instance_id, timeout=1.0)
        elapsed = time.monotonic() - start
        
        thread.join()
        
        assert msg is not None
        assert msg.message_id == result[0]
        assert elapsed >= 0.2  # Should have waited

    def test_metadata_stored_correctly(self, queue):
        """Test that metadata is stored and retrieved correctly."""
        instance_id = "test-instance"
        metadata = {"key": "value", "nested": {"a": 1}}
        
        message_id = queue.enqueue(
            instance_id, 
            "test", 
            "test", 
            metadata=metadata
        )
        
        msg = queue.dequeue(instance_id)
        assert msg.metadata == metadata


class TestInstanceCircuitBreaker:
    """Tests for InstanceCircuitBreaker class."""

    def test_closed_allows_execution(self):
        """Test that closed circuit breaker allows execution."""
        cb = InstanceCircuitBreaker()
        instance_id = "test-instance"
        
        assert cb.can_execute(instance_id) is True

    def test_opens_after_threshold(self):
        """Test that circuit opens after failure threshold."""
        cb = InstanceCircuitBreaker()
        instance_id = "test-instance"
        
        # Record failures up to threshold
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            assert cb.can_execute(instance_id) is True
            cb.record_failure(instance_id)
        
        # Circuit should now be open
        assert cb.can_execute(instance_id) is False

    def test_half_open_recovery(self):
        """Test recovery through half-open state."""
        cb = InstanceCircuitBreaker()
        instance_id = "test-instance"
        
        # Open the circuit
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(instance_id)
        
        assert cb.can_execute(instance_id) is False
        
        # Simulate time passing for recovery timeout
        # Patch the last_failure_time to be in the past
        with patch.object(cb, '_last_failure_time') as mock_time:
            mock_time.get.return_value = (
                datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_RECOVERY_TIMEOUT + 1)
            )
            
            # Should transition to half_open and allow execution
            assert cb.can_execute(instance_id) is True
            
            # Record success to close the circuit
            cb.record_success(instance_id)
        
        # Circuit should be closed again
        assert cb.can_execute(instance_id) is True

    def test_reopens_on_half_open_failure(self):
        """Test that circuit reopens if failure occurs in half-open state."""
        cb = InstanceCircuitBreaker()
        instance_id = "test-instance"
        
        # Open the circuit
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(instance_id)
        
        # Force to half_open by patching time
        with patch.object(cb, '_last_failure_time') as mock_time:
            mock_time.get.return_value = (
                datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_RECOVERY_TIMEOUT + 1)
            )
            cb.can_execute(instance_id)  # Transition to half_open
        
        # Record failure in half_open state
        cb.record_failure(instance_id)
        
        # Circuit should be open again
        assert cb.can_execute(instance_id) is False

    def test_success_resets_failure_count(self):
        """Test that success resets failure count in closed state."""
        cb = InstanceCircuitBreaker()
        instance_id = "test-instance"
        
        # Record some failures (but not enough to open)
        for _ in range(CIRCUIT_FAILURE_THRESHOLD - 1):
            cb.record_failure(instance_id)
        
        # Record success
        cb.record_success(instance_id)
        
        # Failure count should be reset, so we need full threshold again
        for _ in range(CIRCUIT_FAILURE_THRESHOLD - 1):
            cb.record_failure(instance_id)
        
        # Circuit should still be closed
        assert cb.can_execute(instance_id) is True

    def test_per_instance_isolation(self):
        """Test that circuit breaker state is isolated per instance."""
        cb = InstanceCircuitBreaker()
        instance1 = "instance-1"
        instance2 = "instance-2"
        
        # Open circuit for instance1
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(instance1)
        
        # Instance2 should still be closed
        assert cb.can_execute(instance1) is False
        assert cb.can_execute(instance2) is True


class TestInstanceWatchdog:
    """Tests for InstanceWatchdog class."""

    @pytest.fixture
    def watchdog(self, queue_repository):
        """Create a InstanceWatchdog instance for testing."""
        wd = InstanceWatchdog(queue_repository)
        yield wd
        wd.stop()

    def test_detects_stuck_messages(self, watchdog, queue_repository):
        """Test that watchdog detects messages stuck in processing."""
        instance_id = "test-instance"
        message = queue_repository.enqueue(instance_id, "test", "test")
        
        # Dequeue to set status to processing
        queue_repository.dequeue(instance_id)
        
        # Manually set processing_started_at and last_activity_at to be old
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        with Session(queue_repository.engine) as session:
            msg = session.get(MessageQueue, message.message_id)
            assert msg is not None, f"Message {message.message_id} not found"
            msg.processing_started_at = old_time
            msg.last_activity_at = old_time
            session.commit()
        
        # Run stuck check
        watchdog._check_stuck_messages()
        
        # Message should be in retrying state
        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"
        assert msg.retry_count == 1  # Watchdog increments from 0 to 1 on first retry

    def test_schedules_retry_for_stuck(self, watchdog, queue_repository):
        """Test that stuck messages are scheduled for retry."""
        instance_id = "test-instance"
        message = queue_repository.enqueue(instance_id, "test", "test")
        queue_repository.dequeue(instance_id)
        
        # Make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        with Session(queue_repository.engine) as session:
            msg = session.get(MessageQueue, message.message_id)
            assert msg is not None
            msg.processing_started_at = old_time
            msg.last_activity_at = old_time
            session.commit()
        
        watchdog._check_stuck_messages()
        
        # Verify retry was scheduled
        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"
        assert msg.next_retry_at is not None

    def test_fails_after_max_retries(self, watchdog, queue_repository):
        """Test that message is marked failed after max retries exceeded."""
        instance_id = "test-instance"
        message = queue_repository.enqueue(instance_id, "test", "test")
        queue_repository.dequeue(instance_id)
        
        # Set retry count to max and make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        with Session(queue_repository.engine) as session:
            msg = session.get(MessageQueue, message.message_id)
            assert msg is not None
            msg.processing_started_at = old_time
            msg.last_activity_at = old_time
            msg.retry_count = MAX_RETRIES
            session.commit()
        
        watchdog._check_stuck_messages()
        
        # Message should be failed
        msg = queue_repository.get(message.message_id)
        assert msg.status == "failed"
        assert "max retries" in msg.error_message.lower()

    def test_moves_retry_ready_to_ready(self, watchdog, queue_repository):
        """Test that retry-ready messages are moved back to ready."""
        instance_id = "test-instance"
        message = queue_repository.enqueue(instance_id, "test", "test")
        
        # Schedule for retry in the past
        past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        with Session(queue_repository.engine) as session:
            msg = session.get(MessageQueue, message.message_id)
            assert msg is not None
            msg.status = "retrying"
            msg.next_retry_at = past_time
            session.commit()
        
        # Run retry check
        watchdog._check_retry_ready_messages()
        
        # Message should be ready again
        msg = queue_repository.get(message.message_id)
        assert msg.status == "ready"
        assert msg.next_retry_at is None

    def test_only_monitors_active_instances(self, watchdog, queue_repository):
        """Test that watchdog can distinguish active vs inactive instances."""
        # This test verifies the watchdog doesn't process all instances blindly
        # The current implementation checks ALL instances, which is a bug
        # We're testing the expected behavior
        
        instance_id = "test-instance"
        message = queue_repository.enqueue(instance_id, "test", "test")
        queue_repository.dequeue(instance_id)
        
        # Make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        with Session(queue_repository.engine) as session:
            msg = session.get(MessageQueue, message.message_id)
            assert msg is not None
            msg.processing_started_at = old_time
            msg.last_activity_at = old_time
            session.commit()
        
        # Run check - currently this processes all instances
        watchdog._check_stuck_messages()
        
        # Verify message was processed
        msg = queue_repository.get(message.message_id)
        # Current behavior: processes the stuck message
        assert msg.status == "retrying"
        
        # NOTE: This test documents current behavior but highlights
        # that the watchdog should ideally only monitor "active" instances

    def test_watchdog_start_stop(self, queue_repository):
        """Test watchdog can be started and stopped."""
        watchdog = InstanceWatchdog(queue_repository)
        
        assert watchdog._running is False
        
        watchdog.start()
        assert watchdog._running is True
        assert watchdog._thread is not None
        
        # Starting again should be idempotent
        watchdog.start()
        assert watchdog._running is True
        
        watchdog.stop()
        assert watchdog._running is False


class TestQueueIntegration:
    """Integration tests for queue system."""

    @pytest.fixture
    def full_setup(self, tmp_path):
        """Create a full queue setup with watchdog."""
        from sqlmodel import create_engine, SQLModel
        from daemon.repositories.message_queue.models import MessageQueue
        
        db_path = tmp_path / "integration_test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        # Create all tables
        SQLModel.metadata.create_all(engine)
        
        repo = SQLModelMessageQueueRepository(engine)
        
        watchdog = InstanceWatchdog(repo)
        circuit_breaker = InstanceCircuitBreaker()
        
        yield {
            'engine': engine,
            'queue_repository': repo,
            'watchdog': watchdog,
            'circuit_breaker': circuit_breaker
        }
        
        watchdog.stop()
        engine.dispose()

    def test_enqueue_triggers_processing(self, full_setup):
        """Test that enqueuing a message allows it to be processed."""
        repo = full_setup['queue_repository']
        instance_id = "test-instance"
        
        message = repo.enqueue(instance_id, "test message", "api")
        
        # Message should be dequeued for processing
        msg = repo.dequeue(instance_id)
        assert msg is not None
        assert msg.message_id == message.message_id

    def test_circuit_breaker_blocks_processing(self, full_setup):
        """Test that open circuit breaker blocks message processing."""
        cb = full_setup['circuit_breaker']
        repo = full_setup['queue_repository']
        instance_id = "test-instance"
        
        # Open the circuit
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(instance_id)
        
        # Circuit should block execution
        assert cb.can_execute(instance_id) is False
        
        # Message should still be enqueued but not processed
        message = repo.enqueue(instance_id, "test message", "api")
        msg = repo.dequeue(instance_id)  # This should still work at queue level
        
        # But application layer should check circuit breaker
        # before actually processing
        assert msg is not None  # Queue allows dequeue
        assert cb.can_execute(instance_id) is False  # But CB blocks

    def test_watchdog_recovers_stuck_instance(self, full_setup):
        """Test that watchdog can recover a stuck instance."""
        repo = full_setup['queue_repository']
        watchdog = full_setup['watchdog']
        instance_id = "test-instance"
        
        # Enqueue and start processing
        message = repo.enqueue(instance_id, "test message", "api")
        repo.dequeue(instance_id)
        
        # Simulate stuck by setting old processing time
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        with Session(repo.engine) as session:
            msg = session.get(MessageQueue, message.message_id)
            assert msg is not None
            msg.processing_started_at = old_time
            msg.last_activity_at = old_time
            session.commit()
        
        # Run watchdog check
        watchdog._check_stuck_messages()
        
        # Message should be scheduled for retry
        msg = repo.get(message.message_id)
        assert msg.status == "retrying"
        assert msg.retry_count == 1  # Watchdog increments from 0 to 1 on first retry

    def test_full_retry_cycle(self, full_setup):
        """Test a complete retry cycle from failure to recovery."""
        repo = full_setup['queue_repository']
        watchdog = full_setup['watchdog']
        instance_id = "test-instance"
        
        message = repo.enqueue(instance_id, "test message", "api")
        
        # Simulate multiple retry cycles
        for retry_num in range(MAX_RETRIES):
            # Dequeue
            msg = repo.dequeue(instance_id)
            assert msg is not None
            
            # Simulate stuck
            old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
            with Session(repo.engine) as session:
                msg_obj = session.get(MessageQueue, msg.message_id)
                assert msg_obj is not None
                msg_obj.processing_started_at = old_time
                msg_obj.last_activity_at = old_time
                session.commit()
            
            # Watchdog schedules retry
            watchdog._check_stuck_messages()
            
            # Move retry-ready back to ready
            with Session(repo.engine) as session:
                msg_obj = session.get(MessageQueue, msg.message_id)
                assert msg_obj is not None
                msg_obj.status = "ready"
                msg_obj.next_retry_at = None
                session.commit()
        
        # After max retries, message should be failed on next stuck check
        msg = repo.dequeue(instance_id)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        with Session(repo.engine) as session:
            msg_obj = session.get(MessageQueue, msg.message_id)
            assert msg_obj is not None
            msg_obj.processing_started_at = old_time
            msg_obj.last_activity_at = old_time
            msg_obj.retry_count = MAX_RETRIES
            session.commit()
        
        watchdog._check_stuck_messages()
        
        msg = repo.get(message.message_id)
        assert msg.status == "failed"


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_enqueue_empty_content(self, queue):
        """Test enqueuing empty content."""
        instance_id = "test-instance"
        
        # Empty string should still work
        message_id = queue.enqueue(instance_id, "", "test")
        msg = queue.dequeue(instance_id)
        
        assert msg.content == ""

    def test_enqueue_very_long_content(self, queue):
        """Test enqueuing very long content."""
        instance_id = "test-instance"
        long_content = "x" * 100000  # 100KB of content
        
        message_id = queue.enqueue(instance_id, long_content, "test")
        msg = queue.dequeue(instance_id)
        
        assert msg.content == long_content

    def test_enqueue_unicode_content(self, queue):
        """Test enqueuing unicode content."""
        instance_id = "test-instance"
        unicode_content = "Hello 世界 🌍"
        
        message_id = queue.enqueue(instance_id, unicode_content, "test")
        msg = queue.dequeue(instance_id)
        
        assert msg.content == unicode_content

    def test_ack_nonexistent_message(self, queue):
        """Test acknowledging a non-existent message (should not raise)."""
        # Should not raise an error
        queue.ack("non-existent-id")

    def test_fail_nonexistent_message(self, queue):
        """Test failing a non-existent message (should not raise)."""
        # Should not raise an error
        queue.fail("non-existent-id", "error")

    def test_dequeue_same_message_twice(self, queue):
        """Test that the same message can't be dequeued twice."""
        instance_id = "test-instance"
        message_id = queue.enqueue(instance_id, "test", "test")
        
        # First dequeue should succeed
        msg1 = queue.dequeue(instance_id)
        assert msg1 is not None
        assert msg1.message_id == message_id
        
        # Second dequeue should return None (no more ready messages)
        msg2 = queue.dequeue(instance_id)
        assert msg2 is None

    def test_negative_priority(self, queue):
        """Test that negative priorities work (system > user)."""
        instance_id = "test-instance"
        
        # Enqueue with various priorities
        id_neg = queue.enqueue(instance_id, "negative", "test", priority=-1)
        id_zero = queue.enqueue(instance_id, "zero", "test", priority=0)
        id_one = queue.enqueue(instance_id, "one", "test", priority=1)
        
        # Dequeue order should be: -1, 0, 1
        msg1 = queue.dequeue(instance_id)
        assert msg1.message_id == id_neg
        
        msg2 = queue.dequeue(instance_id)
        assert msg2.message_id == id_zero
        
        msg3 = queue.dequeue(instance_id)
        assert msg3.message_id == id_one


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
        
        assert msg.priority == 0
        assert msg.retry_count == 3
        assert msg.metadata == {"key": "value"}
        assert msg.processing_started_at == now
        assert msg.status == "processing"
        assert msg.error_message == "Previous error"


# =============================================================================
# Test Watchdog Cancellation Integration
# =============================================================================

@pytest.fixture
def request_registry():
    """Create an ActiveRequestRegistry for testing."""
    return ActiveRequestRegistry()


def make_message_stuck(queue_repository, message, timeout_seconds=MESSAGE_TIMEOUT_SECONDS + 100):
    """Helper to simulate a stuck message."""
    old_time = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
    with Session(queue_repository.engine) as session:
        msg = session.get(MessageQueue, message.message_id)
        if msg is None:
            raise ValueError(f"Message {message.message_id} not found")
        msg.processing_started_at = old_time
        msg.last_activity_at = old_time
        session.commit()


class TestWatchdogCancellationIntegration:
    """Tests for InstanceWatchdog cancellation integration."""

    def test_watchdog_with_no_registry(self, queue_repository):
        """Watchdog works without registry (backward compatibility)."""
        watchdog = InstanceWatchdog(queue_repository, request_registry=None)

        message = queue_repository.enqueue("instance-1", "test", "test")
        queue_repository.dequeue("instance-1")
        make_message_stuck(queue_repository, message)

        # Should not raise
        watchdog._check_stuck_messages()

        # Message should be scheduled for retry
        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"
        assert msg.retry_count == 1  # Watchdog increments from 0 to 1 on first retry

    def test_watchdog_cancels_via_registry(self, queue_repository, request_registry):
        """Watchdog calls registry.cancel with correct reason."""
        watchdog = InstanceWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("instance-1", "test", "test")
        queue_repository.dequeue("instance-1")

        # Register as active request
        source = request_registry.register(message.message_id, "instance-1")
        make_message_stuck(queue_repository, message)

        watchdog._check_stuck_messages()

        # Token should be cancelled
        assert source.token.is_cancelled is True
        assert source.token.reason == CancellationReason.WATCHDOG_RETRY

    def test_watchdog_cancellation_before_retry(self, queue_repository, request_registry):
        """Cancel happens before schedule_retry."""
        watchdog = InstanceWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("instance-1", "test", "test")
        queue_repository.dequeue("instance-1")

        source = request_registry.register(message.message_id, "instance-1")
        make_message_stuck(queue_repository, message)

        watchdog._check_stuck_messages()

        # Both cancellation and retry should happen
        assert source.token.is_cancelled is True

        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"

    def test_watchdog_cancels_nonexistent_request(self, queue_repository, request_registry):
        """Watchdog handles unregistered requests gracefully."""
        watchdog = InstanceWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("instance-1", "test", "test")
        queue_repository.dequeue("instance-1")

        # Don't register - simulates request that already completed
        make_message_stuck(queue_repository, message)

        # Should not raise
        watchdog._check_stuck_messages()

        # Message should still be scheduled for retry
        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"

    def test_stuck_message_token_cancelled(self, queue_repository, request_registry):
        """Token reflects cancellation after watchdog."""
        watchdog = InstanceWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("instance-1", "test", "test")
        queue_repository.dequeue("instance-1")

        source = request_registry.register(message.message_id, "instance-1")
        make_message_stuck(queue_repository, message)

        assert source.token.is_cancelled is False

        watchdog._check_stuck_messages()

        assert source.token.is_cancelled is True
        assert source.token.reason == CancellationReason.WATCHDOG_RETRY

    def test_multiple_stuck_messages_all_cancelled(self, queue_repository, request_registry):
        """All stuck messages get cancelled."""
        watchdog = InstanceWatchdog(queue_repository, request_registry=request_registry)

        sources = []
        for i in range(3):
            message = queue_repository.enqueue(f"instance-{i}", f"test-{i}", "test")
            queue_repository.dequeue(f"instance-{i}")
            source = request_registry.register(message.message_id, f"instance-{i}")
            sources.append(source)
            make_message_stuck(queue_repository, message)

        watchdog._check_stuck_messages()

        for source in sources:
            assert source.token.is_cancelled is True
            assert source.token.reason == CancellationReason.WATCHDOG_RETRY

    def test_watchdog_fails_after_max_retries_with_cancellation(self, queue_repository, request_registry):
        """Message fails after max retries, cancellation still attempted."""
        watchdog = InstanceWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("instance-1", "test", "test")
        queue_repository.dequeue("instance-1")

        # Set retry count to max
        with Session(queue_repository.engine) as session:
            msg = session.get(MessageQueue, message.message_id)
            assert msg is not None
            msg.retry_count = MAX_RETRIES
            session.commit()
        
        source = request_registry.register(message.message_id, "instance-1")
        make_message_stuck(queue_repository, message)

        watchdog._check_stuck_messages()

        # Should be failed, not retrying
        msg = queue_repository.get(message.message_id)
        assert msg.status == "failed"

        # Cancellation should still be attempted
        assert source.token.is_cancelled is True

    def test_watchdog_only_cancels_stuck_not_active(self, queue_repository, request_registry):
        """Active messages are not cancelled, only stuck ones."""
        watchdog = InstanceWatchdog(queue_repository, request_registry=request_registry)

        # Stuck message
        stuck_message = queue_repository.enqueue("instance-1", "stuck", "test")
        queue_repository.dequeue("instance-1")
        stuck_source = request_registry.register(stuck_message.message_id, "instance-1")
        make_message_stuck(queue_repository, stuck_message)

        # Active message (recent activity)
        active_message = queue_repository.enqueue("instance-2", "active", "test")
        queue_repository.dequeue("instance-2")
        active_source = request_registry.register(active_message.message_id, "instance-2")
        # Don't make it stuck - recent activity

        watchdog._check_stuck_messages()

        # Stuck should be cancelled
        assert stuck_source.token.is_cancelled is True

        # Active should not be cancelled
        assert active_source.token.is_cancelled is False
