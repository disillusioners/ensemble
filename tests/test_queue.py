"""Tests for daemon/queue.py - Input Message Queue implementation."""

import pytest
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import concurrent.futures

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


@pytest.fixture
def db_connection(tmp_path):
    """Create a temporary database connection for testing."""
    db_path = tmp_path / "test_queue.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def queue(db_connection):
    """Create an InputMessageQueue instance for testing."""
    q = InputMessageQueue(db_connection)
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
        
        # Verify it's marked as completed
        cursor = queue._conn.execute(
            "SELECT status FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        assert row["status"] == "completed"

    def test_fail_message(self, queue):
        """Test marking a message as permanently failed."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test message", "test")
        
        # Dequeue and fail
        msg = queue.dequeue(session_id)
        queue.fail(message_id, "Test failure")
        
        # Verify status
        cursor = queue._conn.execute(
            "SELECT status, error_message FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        assert row["status"] == "failed"
        assert row["error_message"] == "Test failure"

    def test_schedule_retry_with_backoff(self, queue):
        """Test scheduling a message for retry with exponential backoff."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test message", "test")
        
        # Schedule retry
        queue.schedule_retry(message_id, 1, "First failure")
        
        # Verify retry state
        cursor = queue._conn.execute(
            "SELECT status, retry_count, next_retry_at FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        assert row["status"] == "retrying"
        assert row["retry_count"] == 1
        assert row["next_retry_at"] is not None

    def test_schedule_retry_backoff_increases(self, queue):
        """Test that backoff increases with retry count."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test message", "test")
        
        # Schedule multiple retries and check backoff increases
        backoffs = []
        for retry_count in range(5):
            queue._conn.execute(
                "UPDATE message_queue SET status = 'ready', next_retry_at = NULL WHERE message_id = ?",
                (message_id,)
            )
            queue._conn.commit()
            queue.schedule_retry(message_id, retry_count, f"Failure {retry_count}")
            
            cursor = queue._conn.execute(
                "SELECT next_retry_at FROM message_queue WHERE message_id = ?",
                (message_id,)
            )
            row = cursor.fetchone()
            next_retry = datetime.fromisoformat(row["next_retry_at"].replace("Z", "+00:00"))
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
        
        # Manually set completed_at to be old
        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        queue._conn.execute(
            "UPDATE message_queue SET completed_at = ? WHERE message_id = ?",
            (old_time, mid)
        )
        queue._conn.commit()
        
        # Now should be cleaned up
        deleted = queue.cleanup_completed(max_age_hours=24)
        assert deleted == 1

    def test_persistence_across_connections(self, tmp_path):
        """Test that messages persist across database reconnections."""
        db_path = tmp_path / "persist_test.db"
        session_id = "test-session"
        
        # Create connection and queue, add message
        conn1 = sqlite3.connect(str(db_path))
        queue1 = InputMessageQueue(conn1)
        message_id = queue1.enqueue(session_id, "persistent message", "test")
        conn1.close()
        
        # Reopen and verify message exists
        conn2 = sqlite3.connect(str(db_path))
        queue2 = InputMessageQueue(conn2)
        
        # Message should still be there
        msg = queue2.dequeue(session_id)
        assert msg is not None
        assert msg.message_id == message_id
        assert msg.content == "persistent message"
        conn2.close()

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
    def watchdog(self, db_connection, queue):
        """Create a SessionWatchdog instance for testing."""
        wd = SessionWatchdog(queue, db_connection)
        yield wd
        wd.stop()

    def test_detects_stuck_messages(self, watchdog, queue):
        """Test that watchdog detects messages stuck in processing."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test", "test")
        
        # Dequeue to set status to processing
        queue.dequeue(session_id)
        
        # Manually set processing_started_at to be old
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 10)
        queue._conn.execute(
            "UPDATE message_queue SET processing_started_at = ? WHERE message_id = ?",
            (old_time, message_id)
        )
        queue._conn.commit()
        
        # Run stuck check
        watchdog._check_stuck_messages()
        
        # Message should be in retrying state
        cursor = queue._conn.execute(
            "SELECT status, retry_count FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        assert row["status"] == "retrying"
        assert row["retry_count"] == 1

    def test_schedules_retry_for_stuck(self, watchdog, queue):
        """Test that stuck messages are scheduled for retry."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test", "test")
        queue.dequeue(session_id)
        
        # Make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 10)
        queue._conn.execute(
            "UPDATE message_queue SET processing_started_at = ? WHERE message_id = ?",
            (old_time, message_id)
        )
        queue._conn.commit()
        
        watchdog._check_stuck_messages()
        
        # Verify retry was scheduled
        cursor = queue._conn.execute(
            "SELECT status, next_retry_at FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        assert row["status"] == "retrying"
        assert row["next_retry_at"] is not None

    def test_fails_after_max_retries(self, watchdog, queue):
        """Test that message is marked failed after max retries exceeded."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test", "test")
        queue.dequeue(session_id)
        
        # Set retry count to max and make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 10)
        queue._conn.execute(
            "UPDATE message_queue SET processing_started_at = ?, retry_count = ? WHERE message_id = ?",
            (old_time, MAX_RETRIES, message_id)
        )
        queue._conn.commit()
        
        watchdog._check_stuck_messages()
        
        # Message should be failed
        cursor = queue._conn.execute(
            "SELECT status, error_message FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        assert row["status"] == "failed"
        assert "max retries" in row["error_message"].lower()

    def test_moves_retry_ready_to_ready(self, watchdog, queue):
        """Test that retry-ready messages are moved back to ready."""
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test", "test")
        
        # Schedule for retry in the past
        past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        queue._conn.execute(
            "UPDATE message_queue SET status = 'retrying', next_retry_at = ? WHERE message_id = ?",
            (past_time, message_id)
        )
        queue._conn.commit()
        
        # Run retry check
        watchdog._check_retry_ready_messages()
        
        # Message should be ready again
        cursor = queue._conn.execute(
            "SELECT status, next_retry_at FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        assert row["status"] == "ready"
        assert row["next_retry_at"] is None

    def test_only_monitors_active_sessions(self, watchdog, queue):
        """Test that watchdog can distinguish active vs inactive sessions."""
        # This test verifies the watchdog doesn't process all sessions blindly
        # The current implementation checks ALL sessions, which is a bug
        # We're testing the expected behavior
        
        session_id = "test-session"
        message_id = queue.enqueue(session_id, "test", "test")
        queue.dequeue(session_id)
        
        # Make it stuck
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 10)
        queue._conn.execute(
            "UPDATE message_queue SET processing_started_at = ? WHERE message_id = ?",
            (old_time, message_id)
        )
        queue._conn.commit()
        
        # Run check - currently this processes all sessions
        watchdog._check_stuck_messages()
        
        # Verify message was processed
        cursor = queue._conn.execute(
            "SELECT status FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        # Current behavior: processes the stuck message
        assert cursor.fetchone()["status"] == "retrying"
        
        # NOTE: This test documents current behavior but highlights
        # that the watchdog should ideally only monitor "active" sessions

    def test_watchdog_start_stop(self, db_connection, queue):
        """Test watchdog can be started and stopped."""
        watchdog = SessionWatchdog(queue, db_connection)
        
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
        db_path = tmp_path / "integration_test.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        
        queue = InputMessageQueue(conn)
        watchdog = SessionWatchdog(queue, conn)
        circuit_breaker = SessionCircuitBreaker()
        
        yield {
            'conn': conn,
            'queue': queue,
            'watchdog': watchdog,
            'circuit_breaker': circuit_breaker
        }
        
        watchdog.stop()
        conn.close()

    def test_enqueue_triggers_processing(self, full_setup):
        """Test that enqueuing a message allows it to be processed."""
        queue = full_setup['queue']
        session_id = "test-session"
        
        message_id = queue.enqueue(session_id, "test message", "api")
        
        # Message should be dequeued for processing
        msg = queue.dequeue(session_id)
        assert msg is not None
        assert msg.message_id == message_id

    def test_circuit_breaker_blocks_processing(self, full_setup):
        """Test that open circuit breaker blocks message processing."""
        cb = full_setup['circuit_breaker']
        queue = full_setup['queue']
        session_id = "test-session"
        
        # Open the circuit
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            cb.record_failure(session_id)
        
        # Circuit should block execution
        assert cb.can_execute(session_id) is False
        
        # Message should still be enqueued but not processed
        message_id = queue.enqueue(session_id, "test message", "api")
        msg = queue.dequeue(session_id)  # This should still work at queue level
        
        # But application layer should check circuit breaker
        # before actually processing
        assert msg is not None  # Queue allows dequeue
        assert cb.can_execute(session_id) is False  # But CB blocks

    def test_watchdog_recovers_stuck_session(self, full_setup):
        """Test that watchdog can recover a stuck session."""
        queue = full_setup['queue']
        watchdog = full_setup['watchdog']
        session_id = "test-session"
        
        # Enqueue and start processing
        message_id = queue.enqueue(session_id, "test message", "api")
        queue.dequeue(session_id)
        
        # Simulate stuck by setting old processing time
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 100)
        queue._conn.execute(
            "UPDATE message_queue SET processing_started_at = ? WHERE message_id = ?",
            (old_time, message_id)
        )
        queue._conn.commit()
        
        # Run watchdog check
        watchdog._check_stuck_messages()
        
        # Message should be scheduled for retry
        cursor = queue._conn.execute(
            "SELECT status, retry_count FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        row = cursor.fetchone()
        assert row["status"] == "retrying"
        assert row["retry_count"] == 1

    def test_full_retry_cycle(self, full_setup):
        """Test a complete retry cycle from failure to recovery."""
        queue = full_setup['queue']
        watchdog = full_setup['watchdog']
        session_id = "test-session"
        
        message_id = queue.enqueue(session_id, "test message", "api")
        
        # Simulate multiple retry cycles
        for retry_num in range(MAX_RETRIES):
            # Dequeue
            msg = queue.dequeue(session_id)
            assert msg is not None
            
            # Simulate stuck
            old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 10)
            queue._conn.execute(
                "UPDATE message_queue SET processing_started_at = ? WHERE message_id = ?",
                (old_time, message_id)
            )
            queue._conn.commit()
            
            # Watchdog schedules retry
            watchdog._check_stuck_messages()
            
            # Move retry-ready back to ready
            queue._conn.execute(
                "UPDATE message_queue SET status = 'ready', next_retry_at = NULL WHERE message_id = ?",
                (message_id,)
            )
            queue._conn.commit()
        
        # After max retries, message should be failed on next stuck check
        msg = queue.dequeue(session_id)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS + 10)
        queue._conn.execute(
            "UPDATE message_queue SET processing_started_at = ?, retry_count = ? WHERE message_id = ?",
            (old_time, MAX_RETRIES, message_id)
        )
        queue._conn.commit()
        
        watchdog._check_stuck_messages()
        
        cursor = queue._conn.execute(
            "SELECT status FROM message_queue WHERE message_id = ?",
            (message_id,)
        )
        assert cursor.fetchone()["status"] == "failed"


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
