"""SQLModel-based MessageQueue Repository implementation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import delete as sql_delete, func, and_, or_
from sqlalchemy.engine import Engine
from sqlmodel import Session, select, col

from .models import MessageQueue, MessageStatus

# Configuration constants
MESSAGE_TIMEOUT_SECONDS = 3600  # 1 hour


class SQLModelMessageQueueRepository:
    """SQLModel-based MessageQueue repository for queue operations."""
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def enqueue(
        self,
        session_id: str,
        content: str,
        source: str,
        priority: int = 1,
        max_retries: int = 5,
        message_metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> MessageQueue:
        """Add a message to the queue."""
        message_id = message_id or str(uuid.uuid4())
        
        message = MessageQueue(
            message_id=message_id,
            session_id=session_id,
            content=content,
            source=source,
            status=MessageStatus.READY.value,
            priority=priority,
            max_retries=max_retries,
            message_metadata=message_metadata or {},
            enqueued_at=datetime.now(timezone.utc),
        )

        with Session(self.engine) as session:
            session.add(message)
            session.commit()
            session.refresh(message)

        return message

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, message_id: str) -> MessageQueue | None:
        """Get a message by ID."""
        with Session(self.engine) as session:
            return session.get(MessageQueue, message_id)

    def get_by_session(self, session_id: str) -> list[MessageQueue]:
        """Get all messages for a session."""
        with Session(self.engine) as session:
            stmt = select(MessageQueue).where(
                MessageQueue.session_id == session_id
            ).order_by(col(MessageQueue.enqueued_at).desc())
            return list(session.exec(stmt))

    def get_by_id(self, message_id: str) -> MessageQueue | None:
        """Get a message by ID (alias for get)."""
        return self.get(message_id)

    # --------------------------------------------------------
    # DEQUEUE (get next ready message)
    # --------------------------------------------------------

    def dequeue(self, session_id: str | None = None) -> MessageQueue | None:
        """Get the next ready message for processing.
        
        Args:
            session_id: Optional session ID to filter by. If provided, only
                       messages for this session will be considered.
        
        Returns the highest priority ready message that is due for processing.
        """
        now = datetime.now(timezone.utc)
        
        # Find ready messages that are either:
        # 1. Not scheduled for retry (next_retry_at is null)
        # 2. Scheduled for retry in the past
        stmt = (
            select(MessageQueue)
            .where(MessageQueue.status == MessageStatus.READY.value)
            .where(
                (MessageQueue.next_retry_at.is_(None))
                | (MessageQueue.next_retry_at <= now)
            )
        )
        
        # Filter by session_id if provided
        if session_id is not None:
            stmt = stmt.where(MessageQueue.session_id == session_id)
        
        # Lock the row to prevent race conditions (TOCTOU)
        # SQLite will serialize access to the same row
        stmt = stmt.with_for_update()
        
        stmt = stmt.order_by(
            col(MessageQueue.priority).asc(),
            col(MessageQueue.enqueued_at).asc()
        ).limit(1)
        
        with Session(self.engine) as session:
            message = session.exec(stmt).first()
            
            # Atomically claim the message by setting status to PROCESSING
            if message is not None:
                message.status = MessageStatus.PROCESSING.value
                message.processing_started_at = now
                message.last_activity_at = now
                session.commit()
                session.refresh(message)
            
            return message
    
    def find_stuck_messages(self) -> list[MessageQueue]:
        """Find all stuck processing messages.
        
        A message is stuck if:
        - status is 'processing'
        - last_activity_at is NULL AND processing_started_at < timeout_threshold
        - OR last_activity_at < timeout_threshold
        
        Args:
            timeout_seconds: Timeout threshold in seconds.
            
        Returns:
            List of stuck messages.
        """
        timeout_threshold = datetime.now(timezone.utc) - timedelta(seconds=MESSAGE_TIMEOUT_SECONDS)
        
        with Session(self.engine) as session:
            stmt = select(MessageQueue).where(
                MessageQueue.status == MessageStatus.PROCESSING.value
            ).where(
                or_(
                    MessageQueue.last_activity_at.is_(None),
                    MessageQueue.processing_started_at < timeout_threshold
                ),
                MessageQueue.last_activity_at < timeout_threshold
            )
            return list(session.exec(stmt))
    
    def find_retry_ready_messages(self) -> list[MessageQueue]:
        """Find all retry-ready messages that can be moved to ready.
        
        Returns:
            List of messages with next_retry_at <= now.
        """
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            stmt = select(MessageQueue).where(
                MessageQueue.status == MessageStatus.RETRYING.value
            ).where(MessageQueue.next_retry_at <= now)
            return list(session.exec(stmt))
    
    def move_retry_ready_to_ready(self, message_ids: list[str]) -> int:
        """Move retry-ready messages back to ready status.
        
        Args:
            message_ids: List of message IDs to update.
            
        Returns:
            Number of messages moved.
        """
        count = 0
        with Session(self.engine) as session:
            for msg_id in message_ids:
                message = session.get(MessageQueue, msg_id)
                if message:
                    message.status = MessageStatus.READY.value
                    message.next_retry_at = None
                    count += 1
            session.commit()
        return count
    
    def fail_stuck_message(self, message_id: str, error_message: str) -> MessageQueue | None:
        """Mark a stuck message as permanently failed.
        
        Args:
            message_id: The message ID to fail.
            error_message: The error message describing the failure.
            
        Returns:
            The updated message or None if not found.
        """
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None
            
            message.status = MessageStatus.FAILED.value
            message.error_message = error_message
            message.completed_at = datetime.now(timezone.utc)
            
            session.commit()
            session.refresh(message)
            
            return message
    
    def schedule_retry_for_stuck(self, message_id: str, retry_count: int, error_message: str) -> MessageQueue | None:
        """Schedule a stuck message for retry.
        
        Args:
            message_id: The message ID to retry.
            retry_count: The new retry count.
            error_message: The error message from the stuck condition.
            
        Returns:
            The updated message or None if not found.
        """
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None
            
            # Update retry count (retry_count > 0 is the canonical check for retry status)
            message.retry_count = retry_count
            
            # Exponential backoff: 1min, 2min, 4min, 8min, etc.
            delay = min(60 * (2 ** (retry_count - 1)), 3600)  # Max 1 hour
            message.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            message.status = MessageStatus.RETRYING.value
            message.processing_started_at = None
            message.error_message = error_message
            
            session.commit()
            session.refresh(message)
            
            return message

    def dequeue_by_session(self, session_id: str) -> MessageQueue | None:
        """Get the next ready message for a specific session.
        
        This is a convenience wrapper around dequeue() for session-specific dequeue.
        
        Args:
            session_id: The session ID to dequeue from.
            
        Returns:
            The next ready message for the session, or None if no messages available.
        """
        return self.dequeue(session_id=session_id)

    # --------------------------------------------------------
    # UPDATE STATUS
    # --------------------------------------------------------

    def complete(self, message_id: str) -> MessageQueue | None:
        """Mark message as completed."""
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None

            message.status = MessageStatus.COMPLETED.value
            message.completed_at = datetime.now(timezone.utc)
            
            session.commit()
            session.refresh(message)
            
            return message

    def fail(self, message_id: str, error_message: str) -> MessageQueue | None:
        """Mark message as failed with error message."""
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None

            message.status = MessageStatus.FAILED.value
            message.error_message = error_message
            message.completed_at = datetime.now(timezone.utc)
            
            session.commit()
            session.refresh(message)

            return message

    def retry(self, message_id: str, error_message: str | None = None) -> MessageQueue | None:
        """Increment retry count and set next_retry_at.
        
        Args:
            message_id: The message ID to retry.
            error_message: Optional error message from previous attempt.
        
        Returns:
            The updated message, or None if not found or max retries exceeded.
        """
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None

            # Check if we can retry
            if message.retry_count >= message.max_retries:
                message.status = MessageStatus.FAILED.value
                message.error_message = f"Max retries ({message.max_retries}) exceeded"
                message.completed_at = datetime.now(timezone.utc)
            else:
                # Increment retry count and set next retry time
                message.retry_count += 1
                # Set error message if provided
                if error_message:
                    message.error_message = error_message
                # Exponential backoff: 1min, 2min, 4min, 8min, etc.
                delay = min(60 * (2 ** (message.retry_count - 1)), 3600)  # Max 1 hour
                message.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                message.status = MessageStatus.READY.value
                message.processing_started_at = None
            
            session.commit()
            session.refresh(message)

            return message

    def update_activity(self, message_id: str) -> MessageQueue | None:
        """Update last_activity_at timestamp for a processing message.
        
        This is called during message processing to indicate the session
        is still active (not stuck), even if processing takes a long time.
        
        Args:
            message_id: The message ID to update.
            
        Returns:
            The updated message or None if not found.
        """
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None
            
            message.last_activity_at = datetime.now(timezone.utc)
            
            session.commit()
            session.refresh(message)
            
            return message
    
    def get_status(self, message_id: str) -> str | None:
        """Get the current status of a message.
        
        Args:
            message_id: The message ID to check.
            
        Returns:
            The status string or None if not found.
        """
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return None
            return message.status
    
    def is_empty(self, session_id: str) -> bool:
        """Check if the queue is empty for a session.
        
        Returns True if there are no ready, processing, or retry-ready messages.
        """
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            stmt = select(func.count()).select_from(MessageQueue).where(
                MessageQueue.session_id == session_id
            ).where(
                or_(
                    MessageQueue.status == MessageStatus.READY.value,
                    MessageQueue.status == MessageStatus.PROCESSING.value,
                    and_(
                        MessageQueue.status == MessageStatus.RETRYING.value,
                        MessageQueue.next_retry_at <= now
                    )
                )
            )
            count = session.exec(stmt).one()
            return count == 0

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list(
        self,
        status: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MessageQueue]:
        """List messages with optional filters."""
        with Session(self.engine) as session:
            stmt = select(MessageQueue)
            
            if status:
                stmt = stmt.where(MessageQueue.status == status)
            if session_id:
                stmt = stmt.where(MessageQueue.session_id == session_id)

            stmt = stmt.order_by(
                col(MessageQueue.priority).asc(),
                col(MessageQueue.enqueued_at).asc()
            ).offset(offset).limit(limit)
            
            return list(session.exec(stmt))

    def list_ready(self, limit: int = 100) -> list[MessageQueue]:
        """List all ready messages."""
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            stmt = (
                select(MessageQueue)
                .where(MessageQueue.status == MessageStatus.READY.value)
                .where(
                    (MessageQueue.next_retry_at.is_(None))
                    | (MessageQueue.next_retry_at <= now)
                )
                .order_by(
                    col(MessageQueue.priority).asc(),
                    col(MessageQueue.enqueued_at).asc()
                )
                .limit(limit)
            )
            return list(session.exec(stmt))

    def list_pending(self, session_id: str | None = None, limit: int = 100) -> list[MessageQueue]:
        """List pending (ready or processing) messages."""
        with Session(self.engine) as session:
            stmt = select(MessageQueue).where(
                (MessageQueue.status == MessageStatus.READY.value)
                | (MessageQueue.status == MessageStatus.PROCESSING.value)
            )
            
            if session_id:
                stmt = stmt.where(MessageQueue.session_id == session_id)

            stmt = stmt.order_by(
                col(MessageQueue.priority).asc(),
                col(MessageQueue.enqueued_at).asc()
            ).limit(limit)
            
            return list(session.exec(stmt))

    # --------------------------------------------------------
    # CLEANUP
    # --------------------------------------------------------

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Remove old completed/failed messages from the queue.
        
        Args:
            max_age_hours: Maximum age of completed/failed messages to keep.
            
        Returns:
            Number of messages deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        
        with Session(self.engine) as session:
            stmt = sql_delete(MessageQueue).where(
                MessageQueue.status.in_([
                    MessageStatus.COMPLETED.value,
                    MessageStatus.FAILED.value
                ])
            ).where(MessageQueue.completed_at < cutoff)
            
            result = session.exec(stmt)
            session.commit()
            
            return result.rowcount

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, message_id: str) -> dict[str, Any]:
        """Delete a message from the queue."""
        with Session(self.engine) as session:
            message = session.get(MessageQueue, message_id)
            if message is None:
                return {"deleted": False, "message_id": message_id, "error": "Not found"}

            session.delete(message)
            session.commit()

            return {
                "deleted": True,
                "message_id": message_id,
            }

    def delete_by_session(self, session_id: str) -> int:
        """Delete all messages for a session."""
        with Session(self.engine) as session:
            stmt = sql_delete(MessageQueue).where(MessageQueue.session_id == session_id)
            result = session.exec(stmt)
            session.commit()
            
            return result.rowcount

    def clear_all(self) -> int:
        """Delete all messages from the queue.
        
        Useful for development to start with a clean queue on startup.
        
        Returns:
            Number of messages deleted.
        """
        with Session(self.engine) as session:
            stmt = sql_delete(MessageQueue)
            result = session.exec(stmt)
            session.commit()
            
            return result.rowcount

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    def count_by_status(self) -> dict[str, int]:
        """Get count of messages by status."""
        counts = {}
        with Session(self.engine) as session:
            for status in MessageStatus:
                stmt = select(func.count()).select_from(MessageQueue).where(
                    MessageQueue.status == status.value
                )
                counts[status.value] = session.exec(stmt).one()
        return counts

    def count_pending(self) -> int:
        """Get count of pending (ready + processing) messages."""
        with Session(self.engine) as session:
            stmt = select(func.count()).select_from(MessageQueue).where(
                (MessageQueue.status == MessageStatus.READY.value)
                | (MessageQueue.status == MessageStatus.PROCESSING.value)
            )
            return session.exec(stmt).one()
    
    def get_stats(self, session_id: str) -> dict[str, Any]:
        """Get queue statistics for a session.
        
        Returns a dict with:
        - pending_count: Number of ready + retry-ready messages
        - processing_count: Number of processing messages
        - oldest_message_age_seconds: Age of oldest message in seconds (or None)
        """
        now = datetime.now(timezone.utc)
        
        with Session(self.engine) as session:
            # Pending count (ready + retrying with next_retry_at <= now)
            pending_stmt = select(func.count()).select_from(MessageQueue).where(
                MessageQueue.session_id == session_id
            ).where(
                or_(
                    MessageQueue.status == MessageStatus.READY.value,
                    and_(
                        MessageQueue.status == MessageStatus.RETRYING.value,
                        MessageQueue.next_retry_at <= now
                    )
                )
            )
            pending_count = session.exec(pending_stmt).one()
            
            # Processing count
            processing_stmt = select(func.count()).select_from(MessageQueue).where(
                MessageQueue.session_id == session_id
            ).where(
                MessageQueue.status == MessageStatus.PROCESSING.value
            )
            processing_count = session.exec(processing_stmt).one()
            
            # Oldest message age
            oldest_stmt = select(func.min(MessageQueue.enqueued_at)).where(
                MessageQueue.session_id == session_id
            ).where(
                MessageQueue.status.in_([
                    MessageStatus.READY.value,
                    MessageStatus.PROCESSING.value,
                    MessageStatus.RETRYING.value
                ])
            )
            oldest = session.exec(oldest_stmt).one()
            oldest_message_age_seconds = None
            if oldest:
                # Make oldest timezone-aware if it's naive (stored without tz in DB)
                if oldest.tzinfo is None:
                    oldest = oldest.replace(tzinfo=timezone.utc)
                oldest_message_age_seconds = (now - oldest).total_seconds()
            
            return {
                "pending_count": pending_count,
                "processing_count": processing_count,
                "oldest_message_age_seconds": oldest_message_age_seconds
            }
