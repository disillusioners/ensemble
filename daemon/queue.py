"""Message queue implementation with SQLite backend."""

import json
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import IntEnum
from typing import Any, Optional

import sqlite3

logger = logging.getLogger("daemon.queue")


# Configuration constants
MAX_QUEUE_SIZE = 100
MESSAGE_TIMEOUT_SECONDS = 600  # 10 minutes
MAX_RETRIES = 5
CHECK_INTERVAL_SECONDS = 30

# Backoff configuration
INITIAL_BACKOFF_SECONDS = 2
BACKOFF_MULTIPLIER = 2.5
MAX_BACKOFF_SECONDS = 300
BACKOFF_JITTER = 0.1  # 10%

# Circuit breaker configuration
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_RECOVERY_TIMEOUT = 300  # 5 minutes


class MessageStatus(IntEnum):
    """Message status enumeration."""
    READY = 0
    PROCESSING = 1
    RETRYING = 2
    COMPLETED = 3
    FAILED = 4


@dataclass
class QueuedMessage:
    """Represents a queued message."""
    message_id: str
    session_id: str
    content: str
    source: str
    priority: int = 1
    retry_count: int = 0
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing_started_at: Optional[datetime] = None
    status: str = "ready"
    error_message: Optional[str] = None


@dataclass
class QueueStats:
    """Queue statistics."""
    pending_count: int
    processing_count: int
    oldest_message_age_seconds: Optional[float]


class InputMessageQueue:
    """SQLite-backed message queue with per-session locking."""

    def __init__(self, conn: sqlite3.Connection):
        """Initialize the queue with the given database connection."""
        self._conn = conn
        self._locks: dict[str, threading.Lock] = {}
        self._conditions: dict[str, threading.Condition] = {}
        self._lock_guard = threading.Lock()
        self._initialize_tables()

    def _initialize_tables(self) -> None:
        """Create database tables if they don't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS message_queue (
                message_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT DEFAULT 'ready',
                priority INTEGER DEFAULT 1,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 5,
                error_message TEXT,
                metadata JSON,
                enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processing_started_at TIMESTAMP,
                completed_at TIMESTAMP,
                next_retry_at TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_message_queue_session_status 
            ON message_queue(session_id, status, priority, enqueued_at)
        """)
        self._conn.commit()
        logger.info("Message queue tables initialized")

    def _get_lock(self, session_id: str) -> threading.Lock:
        """Get or create a lock for a session."""
        with self._lock_guard:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
                self._conditions[session_id] = threading.Condition(self._locks[session_id])
            return self._locks[session_id]

    def _get_condition(self, session_id: str) -> threading.Condition:
        """Get or create a condition for a session."""
        with self._lock_guard:
            if session_id not in self._conditions:
                lock = self._locks.get(session_id) or threading.Lock()
                self._locks[session_id] = lock
                self._conditions[session_id] = threading.Condition(lock)
            return self._conditions[session_id]

    def enqueue(
        self,
        session_id: str,
        content: str,
        source: str,
        priority: int = 1,
        metadata: Optional[dict] = None
    ) -> str:
        """Add a message to the queue. Returns the message_id."""
        message_id = str(uuid.uuid4())
        metadata_json = json.dumps(metadata) if metadata else None

        with self._get_lock(session_id):
            # Check queue size
            cursor = self._conn.execute(
                "SELECT COUNT(*) FROM message_queue WHERE session_id = ? AND status IN ('ready', 'processing', 'retrying')",
                (session_id,)
            )
            current_count = cursor.fetchone()[0]

            if current_count >= MAX_QUEUE_SIZE:
                # Drop oldest USER message (priority >= 1)
                # Drop the oldest user message (FIFO), not based on priority
                self._conn.execute("""
                    DELETE FROM message_queue 
                    WHERE message_id = (
                        SELECT message_id FROM message_queue 
                        WHERE session_id = ? AND priority >= 1 AND status = 'ready'
                        ORDER BY enqueued_at ASC 
                        LIMIT 1
                    )
                """, (session_id,))
                logger.warning(f"Queue full for session {session_id}, dropped oldest message")

            self._conn.execute("""
                INSERT INTO message_queue (
                    message_id, session_id, content, source, priority, metadata
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (message_id, session_id, content, source, priority, metadata_json))
            self._conn.commit()

        # Notify any waiting dequeue calls
        condition = self._get_condition(session_id)
        with condition:
            condition.notify()

        logger.debug(f"Enqueued message {message_id} to session {session_id}")
        return message_id

    def dequeue(self, session_id: str, timeout: float = 0) -> Optional[QueuedMessage]:
        """Atomically claim the next message for processing."""
        condition = self._get_condition(session_id)
        
        with condition:
            # Wait for a message to be available
            if timeout > 0:
                start_time = time.monotonic()
                while self._peek_ready_message(session_id) is None:
                    elapsed = time.monotonic() - start_time
                    remaining = timeout - elapsed
                    if remaining <= 0:
                        return None
                    condition.wait(timeout=remaining)
            else:
                # Quick check without waiting
                if self._peek_ready_message(session_id) is None:
                    return None

            # Atomically claim the message using UPDATE...RETURNING
            # Priority: lower number = higher priority (0=system > 1=user)
            now = datetime.now(timezone.utc)
            cursor = self._conn.execute("""
                UPDATE message_queue
                SET status = 'processing', 
                    processing_started_at = ?
                WHERE message_id = (
                    SELECT message_id FROM message_queue
                    WHERE session_id = ?
                    AND status = 'ready'
                    AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    ORDER BY priority ASC, enqueued_at ASC
                    LIMIT 1
                )
                RETURNING message_id, session_id, content, source, priority, 
                         retry_count, metadata, enqueued_at, processing_started_at,
                         status, error_message
            """, (now, session_id, now))
            
            row = cursor.fetchone()
            self._conn.commit()

            if row is None:
                return None

            return QueuedMessage(
                message_id=row[0],
                session_id=row[1],
                content=row[2],
                source=row[3],
                priority=row[4],
                retry_count=row[5],
                metadata=json.loads(row[6]) if row[6] else {},
                created_at=datetime.fromisoformat(row[7].replace("Z", "+00:00")) if row[7] else now,
                processing_started_at=datetime.fromisoformat(row[8].replace("Z", "+00:00")) if row[8] else now,
                status=row[9],
                error_message=row[10]
            )

    def _peek_ready_message(self, session_id: str) -> Optional[str]:
        """Check if there's a ready message without claiming it."""
        # Priority: lower number = higher priority (0=system > 1=user)
        now = datetime.now(timezone.utc)
        cursor = self._conn.execute("""
            SELECT message_id FROM message_queue
            WHERE session_id = ?
            AND status = 'ready'
            AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY priority ASC, enqueued_at ASC
            LIMIT 1
        """, (session_id, now))
        row = cursor.fetchone()
        return row[0] if row else None

    def ack(self, message_id: str) -> None:
        """Mark a message as successfully processed."""
        now = datetime.now(timezone.utc)
        self._conn.execute("""
            UPDATE message_queue
            SET status = 'completed', completed_at = ?
            WHERE message_id = ?
        """, (now, message_id))
        self._conn.commit()
        logger.debug(f"Message {message_id} acknowledged")

    def fail(self, message_id: str, error: str) -> None:
        """Mark a message as permanently failed."""
        self._conn.execute("""
            UPDATE message_queue
            SET status = 'failed', error_message = ?
            WHERE message_id = ?
        """, (error, message_id))
        self._conn.commit()
        logger.warning(f"Message {message_id} marked as failed: {error}")

    def schedule_retry(self, message_id: str, retry_count: int, error: str) -> None:
        """Schedule a message for retry with exponential backoff."""
        # Calculate backoff: initial 2s, multiplier 2.5, max 300s, 10% jitter
        backoff = min(
            INITIAL_BACKOFF_SECONDS * (BACKOFF_MULTIPLIER ** retry_count),
            MAX_BACKOFF_SECONDS
        )
        # Add jitter
        jitter = backoff * BACKOFF_JITTER * (2 * random.random() - 1)
        backoff = max(1, backoff + jitter)

        next_retry = datetime.now(timezone.utc) + timedelta(seconds=backoff)

        self._conn.execute("""
            UPDATE message_queue
            SET status = 'retrying',
                retry_count = ?,
                error_message = ?,
                next_retry_at = ?
            WHERE message_id = ?
        """, (retry_count, error, next_retry, message_id))
        self._conn.commit()
        logger.debug(f"Message {message_id} scheduled for retry {retry_count} at {next_retry}")

    def get_stats(self, session_id: str) -> QueueStats:
        """Get queue statistics for a session."""
        now = datetime.now(timezone.utc)

        # Pending count (ready + retrying with next_retry_at <= now)
        cursor = self._conn.execute("""
            SELECT COUNT(*) FROM message_queue
            WHERE session_id = ?
            AND status IN ('ready', 'retrying')
            AND (status = 'ready' OR next_retry_at <= ?)
        """, (session_id, now))
        pending_count = cursor.fetchone()[0]

        # Processing count
        cursor = self._conn.execute("""
            SELECT COUNT(*) FROM message_queue
            WHERE session_id = ? AND status = 'processing'
        """, (session_id,))
        processing_count = cursor.fetchone()[0]

        # Oldest message age
        cursor = self._conn.execute("""
            SELECT MIN(enqueued_at) FROM message_queue
            WHERE session_id = ? AND status IN ('ready', 'processing', 'retrying')
        """, (session_id,))
        oldest = cursor.fetchone()[0]
        oldest_message_age_seconds = None
        if oldest:
            # Handle both timezone-aware and naive timestamps from SQLite
            if oldest.endswith("Z"):
                oldest_utc = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            else:
                oldest_utc = datetime.fromisoformat(oldest)
                if oldest_utc.tzinfo is None:
                    oldest_utc = oldest_utc.replace(tzinfo=timezone.utc)
            oldest_message_age_seconds = (now - oldest_utc).total_seconds()

        return QueueStats(
            pending_count=pending_count,
            processing_count=processing_count,
            oldest_message_age_seconds=oldest_message_age_seconds
        )

    def is_empty(self, session_id: str) -> bool:
        """Check if the queue is empty for a session.
        
        Returns True if there are no ready, processing, or retry-ready messages.
        """
        now = datetime.now(timezone.utc)
        cursor = self._conn.execute("""
            SELECT COUNT(*) FROM message_queue
            WHERE session_id = ?
            AND (
                status = 'ready'
                OR status = 'processing'
                OR (status = 'retrying' AND next_retry_at <= ?)
            )
        """, (session_id, now))
        return cursor.fetchone()[0] == 0

    def cleanup_completed(self, max_age_hours: int = 24) -> int:
        """Remove old completed messages."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        cursor = self._conn.execute("""
            DELETE FROM message_queue
            WHERE status = 'completed' AND completed_at < ?
        """, (cutoff,))
        self._conn.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} completed messages older than {max_age_hours}h")
        return deleted


class SessionCircuitBreaker:
    """Circuit breaker to prevent cascading failures."""

    def __init__(self):
        """Initialize the circuit breaker."""
        self._states: dict[str, str] = {}  # session_id -> state
        self._failure_counts: dict[str, int] = {}
        self._last_failure_time: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def can_execute(self, session_id: str) -> bool:
        """Check if execution is allowed for the session."""
        with self._lock:
            state = self._states.get(session_id, "closed")
            
            if state == "closed":
                return True
            
            if state == "open":
                # Check if recovery timeout has passed
                last_failure = self._last_failure_time.get(session_id)
                if last_failure:
                    if (datetime.now(timezone.utc) - last_failure).total_seconds() >= CIRCUIT_RECOVERY_TIMEOUT:
                        # Transition to half_open
                        self._states[session_id] = "half_open"
                        self._failure_counts[session_id] = 0
                        logger.info(f"Circuit breaker for session {session_id} transitioned to half_open")
                        return True
                return False
            
            # half_open - allow one request
            if state == "half_open":
                return True
            
            return False

    def record_success(self, session_id: str) -> None:
        """Record a successful execution."""
        with self._lock:
            # Use same default as can_execute - "closed" for new sessions
            state = self._states.get(session_id, "closed")
            
            if state == "half_open":
                # Successful request in half_open - close the circuit
                self._states[session_id] = "closed"
                self._failure_counts[session_id] = 0
                logger.info(f"Circuit breaker for session {session_id} closed")
            elif state == "closed":
                # Reset failure count on success
                self._failure_counts[session_id] = 0

    def record_failure(self, session_id: str) -> None:
        """Record a failed execution."""
        with self._lock:
            self._failure_counts[session_id] = self._failure_counts.get(session_id, 0) + 1
            self._last_failure_time[session_id] = datetime.now(timezone.utc)
            
            current_state = self._states.get(session_id, "closed")
            
            if current_state == "closed":
                if self._failure_counts[session_id] >= CIRCUIT_FAILURE_THRESHOLD:
                    self._states[session_id] = "open"
                    logger.warning(f"Circuit breaker for session {session_id} opened after {self._failure_counts[session_id]} failures")
            
            elif current_state == "half_open":
                # Any failure in half_open opens the circuit again
                self._states[session_id] = "open"
                logger.warning(f"Circuit breaker for session {session_id} reopened after half_open failure")


class SessionWatchdog:
    """Background thread to monitor and recover stuck messages."""

    def __init__(self, queue: InputMessageQueue, conn: sqlite3.Connection):
        """Initialize the watchdog."""
        self._queue = queue
        self._conn = conn
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the watchdog daemon thread."""
        if self._running:
            logger.warning("SessionWatchdog already running")
            return
        
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("SessionWatchdog started")

    def stop(self) -> None:
        """Stop the watchdog thread."""
        if not self._running:
            return
        
        self._running = False
        self._stop_event.set()
        
        if self._thread:
            self._thread.join(timeout=5)
        
        logger.info("SessionWatchdog stopped")

    def _run_loop(self) -> None:
        """Main loop that checks for stuck and retry-ready messages."""
        while not self._stop_event.is_set():
            try:
                self._check_stuck_messages()
                self._check_retry_ready_messages()
            except Exception as e:
                logger.error(f"Error in SessionWatchdog loop: {e}")
            
            # Wait for next check interval or stop signal
            self._stop_event.wait(timeout=CHECK_INTERVAL_SECONDS)

    def _check_stuck_messages(self) -> None:
        """Find and handle stuck processing messages."""
        timeout_threshold = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS)
        
        cursor = self._conn.execute("""
            SELECT message_id, session_id, retry_count, max_retries
            FROM message_queue
            WHERE status = 'processing'
            AND processing_started_at < ?
        """, (timeout_threshold,))
        
        stuck_messages = cursor.fetchall()
        
        for row in stuck_messages:
            message_id, session_id, retry_count, max_retries = row
            
            if retry_count >= max_retries:
                # Too many retries - mark as failed
                self._queue.fail(message_id, "Message timed out after max retries")
                logger.warning(f"Message {message_id} failed due to timeout (max retries exceeded)")
            else:
                # Schedule retry
                self._queue.schedule_retry(
                    message_id,
                    retry_count + 1,
                    f"Message stuck in processing for > {MESSAGE_TIMEOUT_SECONDS}s"
                )
                logger.info(f"Message {message_id} scheduled for retry due to stuck processing")

    def _check_retry_ready_messages(self) -> None:
        """Move retry-ready messages back to ready status."""
        now = datetime.now(timezone.utc)
        
        # Move retrying messages with next_retry_at <= now back to ready
        cursor = self._conn.execute("""
            UPDATE message_queue
            SET status = 'ready',
                next_retry_at = NULL
            WHERE status = 'retrying'
            AND next_retry_at <= ?
            RETURNING message_id
        """, (now,))
        
        ready_messages = cursor.fetchall()
        self._conn.commit()
        
        if ready_messages:
            logger.debug(f"Moved {len(ready_messages)} messages from retrying to ready")
