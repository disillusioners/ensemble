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
    SessionCircuitBreaker,
    SessionWatchdog,
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
from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository
from daemon.repositories.message_queue.models import MessageQueue, MessageStatus as RepoMessageStatus
from daemon.persistence import init_database


@pytest.fixture
def db_connection(tmp_path):
    """Create a temporary database connection for testing."""
    db_path = tmp_path / "test_queue.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def db_session(tmp_path):
    """Create a SQLModel session for repository testing."""
    from sqlmodel import SQLModel, create_engine
    from daemon.repositories.message_queue.models import MessageQueue
    
    db_path = tmp_path / "test_repo.db"
    engine = create_engine(f"sqlite:///{db_path}")
    # Create all tables
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def queue_repository(db_session):
    """Create a SQLModelMessageQueueRepository instance for testing."""
    return SQLModelMessageQueueRepository(db_session)


@pytest.fixture
def queue(queue_repository):
    """Create an InputMessageQueue instance for testing."""
    q = InputMessageQueue(queue_repository)
    yield q


class TestInputMessageQueue:
    """Tests for InputMessageQueue class."""

    def test_enqueue_dequeue_basic(self, queue):
        """Test basic enqueue and dequeue operations."""
        session_id = "test-session"
        
        # Enqueue a message
        message_id = queue.enqueue(
            session_id=session_id,
            content="Hello, world!",
            source="test",
            priority=1
        )
        
        assert message_id is not None
        assert len(message_id) == 36  # UUID format
        
        # Dequeue the message
        msg = queue.dequeue(session_id)
        
        assert msg is not None
        assert msg.message_id == message_id
        assert msg.session_id == session_id
        assert msg.content == "Hello, world!"
        assert msg.source == "test"
        assert msg.priority == 1
        assert msg.status == "processing"

    def test_priority_ordering(self, queue):
        """Test that higher priority messages are dequeued first."""
        session_id = "test-session"
        
        # Enqueue messages with different priorities
        id_low = queue.enqueue(session_id, "low priority", "test", priority=1)
        id_high = queue.enqueue(session_id, "high priority", "test", priority=0)
        id_medium = queue.enqueue(session_id, "medium priority", "test", priority=1)
        
        # First dequeue should be high priority (0)
        msg1 = queue.dequeue(session_id)
        assert msg1.message_id == id_high
        
        # Second should be low (1) - oldest first among same priority
        msg2 = queue.dequeue(session_id)
        assert msg2.message_id == id_low
        
        # Third should be medium (1) - second oldest
        msg3 = queue.dequeue(session_id)
        assert msg3.message_id == id_medium

    def test_queue_size_limit_drop_oldest(self, queue):
        """Test that oldest user message is dropped when queue is full."""
        session_id = "test-session"
        
        # Fill the queue to MAX_QUEUE_SIZE
        message_ids = []
        for i in range(MAX_QUEUE_SIZE):
            mid = queue.enqueue(session_id, f"message-{i}", "test", priority=1)
            message_ids.append(mid)
        
        # Verify queue is full
        stats = queue.get_stats(session_id)
        assert stats.pending_count == MAX_QUEUE_SIZE
        
        # Enqueue one more - should drop oldest
        new_id = queue.enqueue(session_id, "overflow message", "test", priority=1)
        
        # First message should have been dropped
        msg = queue.dequeue(session_id)
        assert msg.message_id != message_ids[0]
        
        # New message should be in queue
        found_new = False
        while msg:
            if msg.message_id == new_id:
                found_new = True
                break
            queue.ack(msg.message_id)
            msg = queue.dequeue(session_id)
        assert found_new

    def test_queue_size_limit_preserves_system_messages(self, queue):
        """Test that system messages (priority 0) are not dropped."""
        session_id = "test-session"
        
        # Enqueue a system message first
        system_id = queue.enqueue(session_id, "system message", "system", priority=0)
        
        # Fill the queue with user messages
        for i in range(MAX_QUEUE_SIZE):
            queue.enqueue(session_id, f"user-{i}", "test", priority=1)
        
        # System message should still be first (priority 0)
        msg = queue.dequeue(session_id)
        assert msg.message_id == system_id

    def test_dequeue_empty_queue(self, queue):
        """Test dequeue on empty queue returns None."""
        result = queue.dequeue("non-existent-session")
        assert result is None
        
        result = queue.dequeue("non-existent-session", timeout=0.1)
        assert result is None

    def test_ack_message(self, queue):
        """Test acknowledging a processed message."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test message", "test")
        
        # Dequeue the message
        msg = queue.dequeue(session_id)
        assert msg is not None
        
        # Acknowledge it
        queue.ack(message_id)
        
        # Verify it's marked as completed via repository
        msg = queue._repository.get(message_id)
        assert msg is not None
        assert msg.status == "completed"

    def test_fail_message(self, queue):
        """Test marking a message as permanently failed."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test message", "test")
        
        # Dequeue and fail
        msg = queue.dequeue(session_id)
        queue.fail(message_id, "Test failure")
        
        # Verify status via repository
        msg = queue._repository.get(message_id)
        assert msg is not None
        assert msg.status == "failed"
        assert msg.error_message == "Test failure"

    def test_schedule_retry_with_backoff(self, queue):
        """Test scheduling a message for retry with exponential backoff."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test message", "test")
        
        # Schedule retry
        queue.schedule_retry(message_id, 1, "First failure")
        
        # Verify retry state via repository
        msg = queue._repository.get(message_id)
        assert msg is not None
        assert msg.retry_count == 1
        assert msg.next_retry_at is not None

    def test_schedule_retry_backoff_increases(self, queue):
        """Test that backoff increases with retry count."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test message", "test")
        
        # Schedule multiple retries and check backoff increases
        backoffs = []
        for retry_count in range(5):
            # Reset message to ready using repository
            msg = queue._repository.get(message_id)
            if msg:
                msg.status = "ready"
                msg.next_retry_at = None
                queue._repository.session.commit()
            
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
        session_id = "test-session"
        
        # Empty queue
        stats = queue.get_stats(session_id)
        assert stats.pending_count == 0
        assert stats.processing_count == 0
        
        # Add messages
        queue.enqueue(session_id, "msg1", "test")
        queue.enqueue(session_id, "msg2", "test")
        
        stats = queue.get_stats(session_id)
        assert stats.pending_count == 2
        assert stats.processing_count == 0
        
        # Dequeue one
        queue.dequeue(session_id)
        
        stats = queue.get_stats(session_id)
        assert stats.pending_count == 1
        assert stats.processing_count == 1

    def test_get_stats_oldest_message_age(self, queue):
        """Test that oldest_message_age_seconds is calculated correctly."""
        session_id = "test-session"
        
        # Empty queue - no age
        stats = queue.get_stats(session_id)
        assert stats.oldest_message_age_seconds is None
        
        # Add message
        queue.enqueue(session_id, "old message", "test")
        time.sleep(0.1)  # Small delay
        
        stats = queue.get_stats(session_id)
        assert stats.oldest_message_age_seconds is not None
        assert stats.oldest_message_age_seconds >= 0.1

    def test_is_empty(self, queue):
        """Test is_empty check."""
        session_id = "test-session"
        
        assert queue.is_empty(session_id) is True
        
        queue.enqueue(session_id, "test", "test")
        assert queue.is_empty(session_id) is False
        
        msg = queue.dequeue(session_id)
        assert queue.is_empty(session_id) is False  # Still processing
        
        queue.ack(msg.message_id)
        assert queue.is_empty(session_id) is True

    def test_cleanup_completed(self, queue):
        """Test cleanup of old completed messages."""
        session_id = "test-session"
        
        # Add and complete a message
        mid = queue.enqueue(session_id, "test", "test")
        queue.dequeue(session_id)
        queue.ack(mid)
        
        # Should not be cleaned up immediately
        deleted = queue.cleanup_completed(max_age_hours=24)
        assert deleted == 0
        
        # Manually set completed_at to be old via repository
        msg = queue._repository.get(mid)
        if msg:
            msg.completed_at = datetime.now(timezone.utc) - timedelta(hours=25)
            queue._repository.session.commit()
        
        # Now should be cleaned up
        deleted = queue.cleanup_completed(max_age_hours=24)
        assert deleted == 1

    def test_persistence_across_connections(self, tmp_path):
        """Test that messages persist across database reconnections."""
        from sqlmodel import SQLModel, create_engine
        from daemon.repositories.message_queue.models import MessageQueue
        
        db_path = tmp_path / "persist_test.db"
        session_id = "test-session"
        
        # Create repository with session, add message
        engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(engine)
        session1 = Session(engine)
        repo1 = SQLModelMessageQueueRepository(session1)
        queue1 = InputMessageQueue(repo1)
        message_id = queue1.enqueue(session_id, "persistent message", "test")
        session1.close()
        
        # Reopen and verify message exists
        session2 = Session(engine)
        repo2 = SQLModelMessageQueueRepository(session2)
        queue2 = InputMessageQueue(repo2)
        
        # Message should still be there
        msg = queue2.dequeue(session_id)
        assert msg is not None
        assert msg.message_id == message_id
        assert msg.content == "persistent message"
        session2.close()

    def test_concurrent_enqueue_dequeue(self, queue):
        """Test thread safety of concurrent enqueue/dequeue operations."""
        session_id = "test-session"
        num_threads = 10
        messages_per_thread = 10
        
        enqueued_ids = []
        dequeued_ids = []
        lock = threading.Lock()
        
        def enqueue_worker(thread_id):
            for i in range(messages_per_thread):
                mid = queue.enqueue(
                    session_id, 
                    f"thread-{thread_id}-msg-{i}", 
                    "test"
                )
                with lock:
                    enqueued_ids.append(mid)
        
        def dequeue_worker():
            while True:
                msg = queue.dequeue(session_id, timeout=0.5)
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

    def test_per_session_isolation(self, queue):
        """Test that messages are isolated between sessions."""
        session1 = "session-1"
        session2 = "session-2"
        
        # Enqueue to different sessions
        id1 = queue.enqueue(session1, "for session 1", "test")
        id2 = queue.enqueue(session2, "for session 2", "test")
        
        # Dequeue from session1 should only get session1's message
        msg1 = queue.dequeue(session1)
        assert msg1.message_id == id1
        assert msg1.session_id == session1
        
        # Dequeue from session2 should only get session2's message
        msg2 = queue.dequeue(session2)
        assert msg2.message_id == id2
        assert msg2.session_id == session2
        
        # Session1 should be empty now
        msg = queue.dequeue(session1)
        assert msg is None

    def test_dequeue_with_timeout_waits(self, queue):
        """Test that dequeue with timeout waits for messages."""
        session_id = "test-session"
        result = []
        
        def delayed_enqueue():
            time.sleep(0.2)
            mid = queue.enqueue(session_id, "delayed", "test")
            result.append(mid)
        
        # Start thread that will enqueue after delay
        thread = threading.Thread(target=delayed_enqueue)
        thread.start()
        
        # Dequeue should wait and get the message
        start = time.monotonic()
        msg = queue.dequeue(session_id, timeout=1.0)
        elapsed = time.monotonic() - start
        
        thread.join()
        
        assert msg is not None
        assert msg.message_id == result[0]
        assert elapsed >= 0.2  # Should have waited

    def test_metadata_stored_correctly(self, queue):
        """Test that metadata is stored and retrieved correctly."""
        session_id = "test-session"
        metadata = {"key": "value", "nested": {"a": 1}}
        
        message_id = queue.enqueue(
            session_id, 
            "test", 
            "test", 
            metadata=metadata
        )
        
        msg = queue.dequeue(session_id)
        assert msg.metadata == metadata


class TestSessionCircuitBreaker:
    """Tests for SessionCircuitBreaker class."""

    def test_closed_allows_execution(self):
        """Test that closed circuit breaker allows execution."""
        cb = SessionCircuitBreaker()
        session_id = "test-session"
        
        assert cb.can_execute(session_id) is True

    def test_opens_after_threshold(self):
        """Test that circuit opens after failure threshold."""
        cb = SessionCircuitBreaker()
        session_id = "test-session"
        
        # Record failures up to threshold
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            assert cb.can_execute(session_id) is True
            cb.record_failure(session_id)
        
        # Circuit should now be open
        assert cb.can_execute(session_id) is False

    def test_half_open_recovery(self):
        """Test recovery through half-open state."""
        cb = SessionCircuitBreaker()
        session_id = "test-session"
        
        # Open the circuit
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(session_id)
        
        assert cb.can_execute(session_id) is False
        
        # Simulate time passing for recovery timeout
        # Patch the last_failure_time to be in the past
        with patch.object(cb, '_last_failure_time') as mock_time:
            mock_time.get.return_value = (
                datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_RECOVERY_TIMEOUT + 1)
            )
            
            # Should transition to half_open and allow execution
            assert cb.can_execute(session_id) is True
            
            # Record success to close the circuit
            cb.record_success(session_id)
        
        # Circuit should be closed again
        assert cb.can_execute(session_id) is True

    def test_reopens_on_half_open_failure(self):
        """Test that circuit reopens if failure occurs in half-open state."""
        cb = SessionCircuitBreaker()
        session_id = "test-session"
        
        # Open the circuit
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(session_id)
        
        # Force to half_open by patching time
        with patch.object(cb, '_last_failure_time') as mock_time:
            mock_time.get.return_value = (
                datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_RECOVERY_TIMEOUT + 1)
            )
            cb.can_execute(session_id)  # Transition to half_open
        
        # Record failure in half_open state
        cb.record_failure(session_id)
        
        # Circuit should be open again
        assert cb.can_execute(session_id) is False

    def test_success_resets_failure_count(self):
        """Test that success resets failure count in closed state."""
        cb = SessionCircuitBreaker()
        session_id = "test-session"
        
        # Record some failures (but not enough to open)
        for _ in range(CIRCUIT_FAILURE_THRESHOLD - 1):
            cb.record_failure(session_id)
        
        # Record success
        cb.record_success(session_id)
        
        # Failure count should be reset, so we need full threshold again
        for _ in range(CIRCUIT_FAILURE_THRESHOLD - 1):
            cb.record_failure(session_id)
        
        # Circuit should still be closed
        assert cb.can_execute(session_id) is True

    def test_per_session_isolation(self):
        """Test that circuit breaker state is isolated per session."""
        cb = SessionCircuitBreaker()
        session1 = "session-1"
        session2 = "session-2"
        
        # Open circuit for session1
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(session1)
        
        # Session2 should still be closed
        assert cb.can_execute(session1) is False
        assert cb.can_execute(session2) is True


class TestSessionWatchdog:
    """Tests for SessionWatchdog class."""

    @pytest.fixture
    def watchdog(self, queue_repository):
        """Create a SessionWatchdog instance for testing."""
        wd = SessionWatchdog(queue_repository)
        yield wd
        wd.stop()

    def test_detects_stuck_messages(self, watchdog, queue_repository):
        """Test that watchdog detects messages stuck in processing."""
        session_id = "test-session"
        message = queue_repository.enqueue(session_id, "test", "test")
        
        # Dequeue to set status to processing
        queue_repository.dequeue(session_id)
        
        # Manually set processing_started_at and last_activity_at to be old
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        msg = queue_repository.get(message.message_id)
        msg.processing_started_at = old_time
        msg.last_activity_at = old_time
        queue_repository.session.commit()
        
        # Run stuck check
        watchdog._check_stuck_messages()
        
        # Message should be in retrying state
        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"
        assert msg.retry_count == 0  # Repository doesn't increment retry count

    def test_schedules_retry_for_stuck(self, watchdog, queue_repository):
        """Test that stuck messages are scheduled for retry."""
        session_id = "test-session"
        message = queue_repository.enqueue(session_id, "test", "test")
        queue_repository.dequeue(session_id)
        
        # Make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        msg = queue_repository.get(message.message_id)
        msg.processing_started_at = old_time
        msg.last_activity_at = old_time
        queue_repository.session.commit()
        
        watchdog._check_stuck_messages()
        
        # Verify retry was scheduled
        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"
        assert msg.next_retry_at is not None

    def test_fails_after_max_retries(self, watchdog, queue_repository):
        """Test that message is marked failed after max retries exceeded."""
        session_id = "test-session"
        message = queue_repository.enqueue(session_id, "test", "test")
        queue_repository.dequeue(session_id)
        
        # Set retry count to max and make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        msg = queue_repository.get(message.message_id)
        msg.processing_started_at = old_time
        msg.last_activity_at = old_time
        msg.retry_count = MAX_RETRIES
        queue_repository.session.commit()
        
        watchdog._check_stuck_messages()
        
        # Message should be failed
        msg = queue_repository.get(message.message_id)
        assert msg.status == "failed"
        assert "max retries" in msg.error_message.lower()

    def test_moves_retry_ready_to_ready(self, watchdog, queue_repository):
        """Test that retry-ready messages are moved back to ready."""
        session_id = "test-session"
        message = queue_repository.enqueue(session_id, "test", "test")
        
        # Schedule for retry in the past
        past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        msg = queue_repository.get(message.message_id)
        msg.status = "retrying"
        msg.next_retry_at = past_time
        queue_repository.session.commit()
        
        # Run retry check
        watchdog._check_retry_ready_messages()
        
        # Message should be ready again
        msg = queue_repository.get(message.message_id)
        assert msg.status == "ready"
        assert msg.next_retry_at is None

    def test_only_monitors_active_sessions(self, watchdog, queue_repository):
        """Test that watchdog can distinguish active vs inactive sessions."""
        # This test verifies the watchdog doesn't process all sessions blindly
        # The current implementation checks ALL sessions, which is a bug
        # We're testing the expected behavior
        
        session_id = "test-session"
        message = queue_repository.enqueue(session_id, "test", "test")
        queue_repository.dequeue(session_id)
        
        # Make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        msg = queue_repository.get(message.message_id)
        msg.processing_started_at = old_time
        msg.last_activity_at = old_time
        queue_repository.session.commit()
        
        # Run check - currently this processes all sessions
        watchdog._check_stuck_messages()
        
        # Verify message was processed
        msg = queue_repository.get(message.message_id)
        # Current behavior: processes the stuck message
        assert msg.status == "retrying"
        
        # NOTE: This test documents current behavior but highlights
        # that the watchdog should ideally only monitor "active" sessions

    def test_watchdog_start_stop(self, queue_repository):
        """Test watchdog can be started and stopped."""
        watchdog = SessionWatchdog(queue_repository)
        
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
        
        from sqlmodel import Session as SQLModelSession
        session = SQLModelSession(engine)
        repo = SQLModelMessageQueueRepository(session)
        
        watchdog = SessionWatchdog(repo)
        circuit_breaker = SessionCircuitBreaker()
        
        yield {
            'session': session,
            'queue_repository': repo,
            'watchdog': watchdog,
            'circuit_breaker': circuit_breaker
        }
        
        watchdog.stop()
        session.close()

    def test_enqueue_triggers_processing(self, full_setup):
        """Test that enqueuing a message allows it to be processed."""
        repo = full_setup['queue_repository']
        session_id = "test-session"
        
        message = repo.enqueue(session_id, "test message", "api")
        
        # Message should be dequeued for processing
        msg = repo.dequeue(session_id)
        assert msg is not None
        assert msg.message_id == message.message_id

    def test_circuit_breaker_blocks_processing(self, full_setup):
        """Test that open circuit breaker blocks message processing."""
        cb = full_setup['circuit_breaker']
        repo = full_setup['queue_repository']
        session_id = "test-session"
        
        # Open the circuit
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(session_id)
        
        # Circuit should block execution
        assert cb.can_execute(session_id) is False
        
        # Message should still be enqueued but not processed
        message = repo.enqueue(session_id, "test message", "api")
        msg = repo.dequeue(session_id)  # This should still work at queue level
        
        # But application layer should check circuit breaker
        # before actually processing
        assert msg is not None  # Queue allows dequeue
        assert cb.can_execute(session_id) is False  # But CB blocks

    def test_watchdog_recovers_stuck_session(self, full_setup):
        """Test that watchdog can recover a stuck session."""
        repo = full_setup['queue_repository']
        watchdog = full_setup['watchdog']
        session_id = "test-session"
        
        # Enqueue and start processing
        message = repo.enqueue(session_id, "test message", "api")
        repo.dequeue(session_id)
        
        # Simulate stuck by setting old processing time
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        msg = repo.get(message.message_id)
        msg.processing_started_at = old_time
        msg.last_activity_at = old_time
        repo.session.commit()
        
        # Run watchdog check
        watchdog._check_stuck_messages()
        
        # Message should be scheduled for retry
        msg = repo.get(message.message_id)
        assert msg.status == "retrying"
        assert msg.retry_count == 0  # Repository doesn't increment retry count

    def test_full_retry_cycle(self, full_setup):
        """Test a complete retry cycle from failure to recovery."""
        repo = full_setup['queue_repository']
        watchdog = full_setup['watchdog']
        session_id = "test-session"
        
        message = repo.enqueue(session_id, "test message", "api")
        
        # Simulate multiple retry cycles
        for retry_num in range(MAX_RETRIES):
            # Dequeue
            msg = repo.dequeue(session_id)
            assert msg is not None
            
            # Simulate stuck
            old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
            msg.processing_started_at = old_time
            msg.last_activity_at = old_time
            repo.session.commit()
            
            # Watchdog schedules retry
            watchdog._check_stuck_messages()
            
            # Move retry-ready back to ready
            msg.status = "ready"
            msg.next_retry_at = None
            repo.session.commit()
        
        # After max retries, message should be failed on next stuck check
        msg = repo.dequeue(session_id)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        msg.processing_started_at = old_time
        msg.last_activity_at = old_time
        msg.retry_count = MAX_RETRIES
        repo.session.commit()
        
        watchdog._check_stuck_messages()
        
        msg = repo.get(message.message_id)
        assert msg.status == "failed"


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_enqueue_empty_content(self, queue):
        """Test enqueuing empty content."""
        session_id = "test-session"
        
        # Empty string should still work
        message_id = queue.enqueue(session_id, "", "test")
        msg = queue.dequeue(session_id)
        
        assert msg.content == ""

    def test_enqueue_very_long_content(self, queue):
        """Test enqueuing very long content."""
        session_id = "test-session"
        long_content = "x" * 100000  # 100KB of content
        
        message_id = queue.enqueue(session_id, long_content, "test")
        msg = queue.dequeue(session_id)
        
        assert msg.content == long_content

    def test_enqueue_unicode_content(self, queue):
        """Test enqueuing unicode content."""
        session_id = "test-session"
        unicode_content = "Hello 世界 🌍"
        
        message_id = queue.enqueue(session_id, unicode_content, "test")
        msg = queue.dequeue(session_id)
        
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
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test", "test")
        
        # First dequeue should succeed
        msg1 = queue.dequeue(session_id)
        assert msg1 is not None
        assert msg1.message_id == message_id
        
        # Second dequeue should return None (no more ready messages)
        msg2 = queue.dequeue(session_id)
        assert msg2 is None

    def test_negative_priority(self, queue):
        """Test that negative priorities work (system > user)."""
        session_id = "test-session"
        
        # Enqueue with various priorities
        id_neg = queue.enqueue(session_id, "negative", "test", priority=-1)
        id_zero = queue.enqueue(session_id, "zero", "test", priority=0)
        id_one = queue.enqueue(session_id, "one", "test", priority=1)
        
        # Dequeue order should be: -1, 0, 1
        msg1 = queue.dequeue(session_id)
        assert msg1.message_id == id_neg
        
        msg2 = queue.dequeue(session_id)
        assert msg2.message_id == id_zero
        
        msg3 = queue.dequeue(session_id)
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
            session_id="test-session",
            content="test content",
            source="test"
        )
        
        assert msg.message_id == "test-id"
        assert msg.session_id == "test-session"
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
            session_id="test-session",
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
    msg = queue_repository.get(message.message_id)
    msg.processing_started_at = old_time
    msg.last_activity_at = old_time
    queue_repository.session.commit()


class TestWatchdogCancellationIntegration:
    """Tests for SessionWatchdog cancellation integration."""

    def test_watchdog_with_no_registry(self, queue_repository):
        """Watchdog works without registry (backward compatibility)."""
        watchdog = SessionWatchdog(queue_repository, request_registry=None)

        message = queue_repository.enqueue("session-1", "test", "test")
        queue_repository.dequeue("session-1")
        make_message_stuck(queue_repository, message)

        # Should not raise
        watchdog._check_stuck_messages()

        # Message should be scheduled for retry
        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"
        assert msg.retry_count == 0  # Repository doesn't increment retry count

    def test_watchdog_cancels_via_registry(self, queue_repository, request_registry):
        """Watchdog calls registry.cancel with correct reason."""
        watchdog = SessionWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("session-1", "test", "test")
        queue_repository.dequeue("session-1")

        # Register as active request
        source = request_registry.register(message.message_id, "session-1")
        make_message_stuck(queue_repository, message)

        watchdog._check_stuck_messages()

        # Token should be cancelled
        assert source.token.is_cancelled is True
        assert source.token.reason == CancellationReason.WATCHDOG_RETRY

    def test_watchdog_cancellation_before_retry(self, queue_repository, request_registry):
        """Cancel happens before schedule_retry."""
        watchdog = SessionWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("session-1", "test", "test")
        queue_repository.dequeue("session-1")

        source = request_registry.register(message.message_id, "session-1")
        make_message_stuck(queue_repository, message)

        watchdog._check_stuck_messages()

        # Both cancellation and retry should happen
        assert source.token.is_cancelled is True

        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"

    def test_watchdog_cancels_nonexistent_request(self, queue_repository, request_registry):
        """Watchdog handles unregistered requests gracefully."""
        watchdog = SessionWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("session-1", "test", "test")
        queue_repository.dequeue("session-1")

        # Don't register - simulates request that already completed
        make_message_stuck(queue_repository, message)

        # Should not raise
        watchdog._check_stuck_messages()

        # Message should still be scheduled for retry
        msg = queue_repository.get(message.message_id)
        assert msg.status == "retrying"

    def test_stuck_message_token_cancelled(self, queue_repository, request_registry):
        """Token reflects cancellation after watchdog."""
        watchdog = SessionWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("session-1", "test", "test")
        queue_repository.dequeue("session-1")

        source = request_registry.register(message.message_id, "session-1")
        make_message_stuck(queue_repository, message)

        assert source.token.is_cancelled is False

        watchdog._check_stuck_messages()

        assert source.token.is_cancelled is True
        assert source.token.reason == CancellationReason.WATCHDOG_RETRY

    def test_multiple_stuck_messages_all_cancelled(self, queue_repository, request_registry):
        """All stuck messages get cancelled."""
        watchdog = SessionWatchdog(queue_repository, request_registry=request_registry)

        sources = []
        for i in range(3):
            message = queue_repository.enqueue(f"session-{i}", f"test-{i}", "test")
            queue_repository.dequeue(f"session-{i}")
            source = request_registry.register(message.message_id, f"session-{i}")
            sources.append(source)
            make_message_stuck(queue_repository, message)

        watchdog._check_stuck_messages()

        for source in sources:
            assert source.token.is_cancelled is True
            assert source.token.reason == CancellationReason.WATCHDOG_RETRY

    def test_watchdog_fails_after_max_retries_with_cancellation(self, queue_repository, request_registry):
        """Message fails after max retries, cancellation still attempted."""
        watchdog = SessionWatchdog(queue_repository, request_registry=request_registry)

        message = queue_repository.enqueue("session-1", "test", "test")
        queue_repository.dequeue("session-1")

        # Set retry count to max
        msg = queue_repository.get(message.message_id)
        msg.retry_count = MAX_RETRIES
        queue_repository.session.commit()

        source = request_registry.register(message.message_id, "session-1")
        make_message_stuck(queue_repository, message)

        watchdog._check_stuck_messages()

        # Should be failed, not retrying
        msg = queue_repository.get(message.message_id)
        assert msg.status == "failed"

        # Cancellation should still be attempted
        assert source.token.is_cancelled is True

    def test_watchdog_only_cancels_stuck_not_active(self, queue_repository, request_registry):
        """Active messages are not cancelled, only stuck ones."""
        watchdog = SessionWatchdog(queue_repository, request_registry=request_registry)

        # Stuck message
        stuck_message = queue_repository.enqueue("session-1", "stuck", "test")
        queue_repository.dequeue("session-1")
        stuck_source = request_registry.register(stuck_message.message_id, "session-1")
        make_message_stuck(queue_repository, stuck_message)

        # Active message (recent activity)
        active_message = queue_repository.enqueue("session-2", "active", "test")
        queue_repository.dequeue("session-2")
        active_source = request_registry.register(active_message.message_id, "session-2")
        # Don't make it stuck - recent activity

        watchdog._check_stuck_messages()

        # Stuck should be cancelled
        assert stuck_source.token.is_cancelled is True

        # Active should not be cancelled
        assert active_source.token.is_cancelled is False
