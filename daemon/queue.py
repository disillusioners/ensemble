"""Message queue implementation with SQLite backend."""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Optional, Callable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .request_registry import ActiveRequestRegistry
    from .cancellation import CancellationReason
    from .repositories.message_queue.repository import SQLModelMessageQueueRepository
    from .repositories.message_queue.models import MessageQueue

logger = logging.getLogger("daemon.queue")


# Configuration constants
MAX_QUEUE_SIZE = 100
MESSAGE_TIMEOUT_SECONDS = 3600  # 1 hour
MAX_RETRIES = 5
CHECK_INTERVAL_SECONDS = 30

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
    last_activity_at: Optional[datetime] = None  # NEW
    status: str = "ready"
    error_message: Optional[str] = None


@dataclass
class QueueStats:
    """Queue statistics."""
    pending_count: int
    processing_count: int
    oldest_message_age_seconds: Optional[float]


class InputMessageQueue:
    """Message queue with per-session locking.
    
    Delegates database operations to SQLModelMessageQueueRepository.
    """

    def __init__(self, repository: "SQLModelMessageQueueRepository"):
        """Initialize the queue with the given repository."""
        self._repository = repository
        self._locks: dict[str, threading.Lock] = {}
        self._conditions: dict[str, threading.Condition] = {}
        self._lock_guard = threading.Lock()

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
        with self._get_lock(session_id):
            # Check queue size using repository
            stats = self._repository.get_stats(session_id)
            current_count = stats["pending_count"] + stats["processing_count"]

            if current_count >= MAX_QUEUE_SIZE:
                # Drop oldest ready message (priority >= 1) - FIFO by enqueued_at
                # Get all ready messages and sort by enqueued_at ascending to find oldest
                ready_messages = self._repository.list(
                    session_id=session_id,
                    status="ready",
                    limit=100
                )
                # Sort by enqueued_at ascending to find oldest
                ready_messages.sort(key=lambda m: m.enqueued_at)
                # Find oldest message with priority >= 1
                oldest_msg = None
                for msg in ready_messages:
                    if msg.priority >= 1:
                        oldest_msg = msg
                        break
                if oldest_msg:
                    self._repository.delete(oldest_msg.message_id)
                    logger.warning(f"Queue full for session {session_id}, dropped oldest message")

            # Use repository to enqueue
            msg = self._repository.enqueue(
                session_id=session_id,
                content=content,
                source=source,
                priority=priority,
                message_metadata=metadata,
            )
            message_id = msg.message_id

        # Notify any waiting dequeue calls
        condition = self._get_condition(session_id)
        with condition:
            condition.notify()

        logger.info(f"📥 Enqueued message {message_id[:8]}... to session {session_id[:8]}...")
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

            # Use repository to dequeue
            msg = self._repository.dequeue_by_session(session_id)

            if msg is None:
                logger.debug(f"No ready message for session {session_id[:8]}...")
                return None

            now = datetime.now(timezone.utc)
            queued_msg = QueuedMessage(
                message_id=msg.message_id,
                session_id=msg.session_id,
                content=msg.content,
                source=msg.source,
                priority=msg.priority,
                retry_count=msg.retry_count,
                metadata=msg.message_metadata or {},
                created_at=msg.enqueued_at,
                processing_started_at=msg.processing_started_at,
                last_activity_at=msg.last_activity_at,
                status=msg.status,
                error_message=msg.error_message,
            )
            logger.info(f"📤 Dequeued message {queued_msg.message_id[:8]}... from session {session_id[:8]}...")
            return queued_msg

    def _peek_ready_message(self, session_id: str) -> Optional[str]:
        """Check if there's a ready message without claiming it."""
        # Use repository to list ready messages
        ready_messages = self._repository.list(
            session_id=session_id,
            status="ready",
            limit=1
        )
        return ready_messages[0].message_id if ready_messages else None

    def ack(self, message_id: str) -> None:
        """Mark a message as successfully processed."""
        self._repository.complete(message_id)
        logger.debug(f"Message {message_id} acknowledged")

    def update_activity(self, message_id: str) -> None:
        """Update last_activity_at timestamp for a processing message.
        
        This is called during message processing to indicate the session
        is still active (not stuck), even if processing takes a long time.
        
        Args:
            message_id: The message ID to update.
        """
        self._repository.update_activity(message_id)
        logger.debug(f"Updated activity for message {message_id}")

    def fail(self, message_id: str, error: str) -> None:
        """Mark a message as permanently failed."""
        self._repository.fail(message_id, error)
        logger.warning(f"Message {message_id} marked as failed: {error}")

    def schedule_retry(self, message_id: str, retry_count: int, error: str) -> None:
        """Schedule a message for retry with exponential backoff.
        
        Note: Uses the repository's retry logic which handles backoff calculation.
        The retry_count parameter is informational since repository auto-increments.
        """
        # Use repository's retry method - it handles backoff and status changes
        self._repository.retry(message_id, error)
        logger.debug(f"Message {message_id} scheduled for retry")

    def get_stats(self, session_id: str) -> QueueStats:
        """Get queue statistics for a session."""
        stats = self._repository.get_stats(session_id)
        return QueueStats(
            pending_count=stats["pending_count"],
            processing_count=stats["processing_count"],
            oldest_message_age_seconds=stats["oldest_message_age_seconds"]
        )

    def is_empty(self, session_id: str) -> bool:
        """Check if the queue is empty for a session.
        
        Returns True if there are no ready, processing, or retry-ready messages.
        """
        return self._repository.is_empty(session_id)

    def cleanup_completed(self, max_age_hours: int = 24) -> int:
        """Remove old completed messages."""
        deleted = self._repository.cleanup_old(max_age_hours)
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

    def __init__(
        self, 
        queue_repository: "SQLModelMessageQueueRepository",
        request_registry: Optional["ActiveRequestRegistry"] = None,
        on_message_failed: Optional[Callable[[str, str, str], None]] = None
    ):
        """Initialize the watchdog.
        
        Args:
            queue_repository: The message queue repository for database operations.
            request_registry: Optional registry for cancelling active requests.
            on_message_failed: Optional callback when a message is permanently failed.
                Called with (session_id, message_id, error_message).
        """
        self._queue_repository: "SQLModelMessageQueueRepository" = queue_repository
        self._request_registry = request_registry
        self._on_message_failed = on_message_failed
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
        from .cancellation import CancellationReason
        
        # Find stuck messages using repository
        stuck_messages = self._queue_repository.find_stuck_messages()
        
        for message in stuck_messages:
            message_id = message.message_id
            retry_count = message.retry_count
            max_retries = message.max_retries
            
            # Attempt to cancel the running request
            cancelled = False
            if self._request_registry:
                cancelled = self._request_registry.cancel(
                    message_id, 
                    CancellationReason.WATCHDOG_RETRY
                )
                if cancelled:
                    logger.info(f"Cancelled stuck request {message_id[:8]}...")
            
            if retry_count >= max_retries:
                # Too many retries - mark as failed
                error_msg = "Message timed out after max retries"
                self._queue_repository.fail_stuck_message(
                    message_id, 
                    error_msg
                )
                logger.warning(f"Message {message_id} failed due to timeout (max retries exceeded)")
                # Notify callback if registered
                if self._on_message_failed:
                    try:
                        self._on_message_failed(message.session_id, message_id, error_msg)
                    except Exception as e:
                        logger.error(f"Error in on_message_failed callback: {e}")
            else:
                # Schedule retry
                self._queue_repository.schedule_retry_for_stuck(
                    message_id,
                    retry_count + 1,
                    f"Message stuck in processing for > {MESSAGE_TIMEOUT_SECONDS}s"
                )
                logger.info(f"Message {message_id[:8]}... scheduled for retry (cancelled={cancelled})")

    def _check_retry_ready_messages(self) -> None:
        """Move retry-ready messages back to ready status."""
        # Find retry-ready messages using repository
        retry_ready_messages = self._queue_repository.find_retry_ready_messages()
        
        if retry_ready_messages:
            message_ids = [msg.message_id for msg in retry_ready_messages]
            count = self._queue_repository.move_retry_ready_to_ready(message_ids)
            logger.debug(f"Moved {count} messages from retrying to ready")
