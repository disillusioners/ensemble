"""Message queue types and models."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum

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
    instance_id: str
    content: str
    source: str
    priority: int = 1
    retry_count: int = 0
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing_started_at: datetime | None = None
    last_activity_at: datetime | None = None
    status: str = "ready"
    error_message: str | None = None


@dataclass
class QueueStats:
    """Queue statistics."""
    pending_count: int
    processing_count: int
    oldest_message_age_seconds: float | None
