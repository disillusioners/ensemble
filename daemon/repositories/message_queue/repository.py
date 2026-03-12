"""SQLModel-based MessageQueue Repository implementation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import delete as sql_delete, func, and_, or_
from sqlmodel import Session, select, col

from .models import MessageQueue, MessageStatus


class SQLModelMessageQueueRepository:
    """SQLModel-based MessageQueue repository for queue operations."""
    
    def __init__(self, session: Session):
        """Initialize repository with a database session."""
        self.session = session

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
            enqueued_at=datetime.utcnow(),
        )

        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)

        return message

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, message_id: str) -> MessageQueue | None:
        """Get a message by ID."""
        return self.session.get(MessageQueue, message_id)

    def get_by_session(self, session_id: str) -> list[MessageQueue]:
        """Get all messages for a session."""
        stmt = select(MessageQueue).where(
            MessageQueue.session_id == session_id
        ).order_by(col(MessageQueue.enqueued_at).desc())
        return list(self.session.exec(stmt))

    def get_by_id(self, message_id: str) -> MessageQueue | None:
        """Get a message by ID (alias for get)."""
        return self.get(message_id)

    # --------------------------------------------------------
    # DEQUEUE (get next ready message)
    # --------------------------------------------------------

    def dequeue(self) -> MessageQueue | None:
        """Get the next ready message for processing.
        
        Returns the highest priority ready message that is due for processing.
        """
        now = datetime.utcnow()
        
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
            .order_by(
                col(MessageQueue.priority).desc(),
                col(MessageQueue.enqueued_at).asc()
            )
            .limit(1)
        )
        
        return self.session.exec(stmt).first()

    # --------------------------------------------------------
    # UPDATE STATUS
    # --------------------------------------------------------

    def mark_processing(self, message_id: str) -> MessageQueue | None:
        """Update status to 'processing'."""
        message = self.session.get(MessageQueue, message_id)
        if message is None:
            return None

        message.status = MessageStatus.PROCESSING.value
        message.processing_started_at = datetime.utcnow()
        
        self.session.commit()
        self.session.refresh(message)

        return message

    def complete(self, message_id: str) -> MessageQueue | None:
        """Mark message as completed."""
        message = self.session.get(MessageQueue, message_id)
        if message is None:
            return None

        message.status = MessageStatus.COMPLETED.value
        message.completed_at = datetime.utcnow()
        
        self.session.commit()
        self.session.refresh(message)

        return message

    def fail(self, message_id: str, error_message: str) -> MessageQueue | None:
        """Mark message as failed with error message."""
        message = self.session.get(MessageQueue, message_id)
        if message is None:
            return None

        message.status = MessageStatus.FAILED.value
        message.error_message = error_message
        message.completed_at = datetime.utcnow()
        
        self.session.commit()
        self.session.refresh(message)

        return message

    def retry(self, message_id: str, error_message: str | None = None) -> MessageQueue | None:
        """Increment retry count and set next_retry_at.
        
        Args:
            message_id: The message ID to retry.
            error_message: Optional error message from previous attempt.
        
        Returns:
            The updated message, or None if not found or max retries exceeded.
        """
        message = self.session.get(MessageQueue, message_id)
        if message is None:
            return None

        # Check if we can retry
        if message.retry_count >= message.max_retries:
            message.status = MessageStatus.FAILED.value
            message.error_message = f"Max retries ({message.max_retries}) exceeded"
            message.completed_at = datetime.utcnow()
        else:
            # Increment retry count and set next retry time
            message.retry_count += 1
            # Set error message if provided
            if error_message:
                message.error_message = error_message
            # Exponential backoff: 1min, 2min, 4min, 8min, etc.
            delay = min(60 * (2 ** (message.retry_count - 1)), 3600)  # Max 1 hour
            message.next_retry_at = datetime.utcnow() + timedelta(seconds=delay)
            message.status = MessageStatus.READY.value
            message.processing_started_at = None
        
        self.session.commit()
        self.session.refresh(message)

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
        message = self.session.get(MessageQueue, message_id)
        if message is None:
            return None
        
        message.last_activity_at = datetime.utcnow()
        
        self.session.commit()
        self.session.refresh(message)
        
        return message
    
    def get_status(self, message_id: str) -> str | None:
        """Get the current status of a message.
        
        Args:
            message_id: The message ID to check.
            
        Returns:
            The status string or None if not found.
        """
        message = self.session.get(MessageQueue, message_id)
        if message is None:
            return None
        return message.status
    
    def is_empty(self, session_id: str) -> bool:
        """Check if the queue is empty for a session.
        
        Returns True if there are no ready, processing, or retry-ready messages.
        """
        now = datetime.utcnow()
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
        count = self.session.exec(stmt).one()
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
        stmt = select(MessageQueue)
        
        if status:
            stmt = stmt.where(MessageQueue.status == status)
        if session_id:
            stmt = stmt.where(MessageQueue.session_id == session_id)

        stmt = stmt.order_by(
            col(MessageQueue.priority).desc(),
            col(MessageQueue.enqueued_at).desc()
        ).offset(offset).limit(limit)
        
        return list(self.session.exec(stmt))

    def list_ready(self, limit: int = 100) -> list[MessageQueue]:
        """List all ready messages."""
        now = datetime.utcnow()
        stmt = (
            select(MessageQueue)
            .where(MessageQueue.status == MessageStatus.READY.value)
            .where(
                (MessageQueue.next_retry_at.is_(None))
                | (MessageQueue.next_retry_at <= now)
            )
            .order_by(
                col(MessageQueue.priority).desc(),
                col(MessageQueue.enqueued_at).asc()
            )
            .limit(limit)
        )
        return list(self.session.exec(stmt))

    def list_pending(self, session_id: str | None = None, limit: int = 100) -> list[MessageQueue]:
        """List pending (ready or processing) messages."""
        stmt = select(MessageQueue).where(
            (MessageQueue.status == MessageStatus.READY.value)
            | (MessageQueue.status == MessageStatus.PROCESSING.value)
        )
        
        if session_id:
            stmt = stmt.where(MessageQueue.session_id == session_id)

        stmt = stmt.order_by(
            col(MessageQueue.priority).desc(),
            col(MessageQueue.enqueued_at).asc()
        ).limit(limit)
        
        return list(self.session.exec(stmt))

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
        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        
        stmt = sql_delete(MessageQueue).where(
            MessageQueue.status.in_([
                MessageStatus.COMPLETED.value,
                MessageStatus.FAILED.value
            ])
        ).where(MessageQueue.completed_at < cutoff)
        
        result = self.session.exec(stmt)
        self.session.commit()
        
        return result.rowcount

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, message_id: str) -> dict[str, Any]:
        """Delete a message from the queue."""
        message = self.session.get(MessageQueue, message_id)
        if message is None:
            return {"deleted": False, "message_id": message_id, "error": "Not found"}

        self.session.delete(message)
        self.session.commit()

        return {
            "deleted": True,
            "message_id": message_id,
        }

    def delete_by_session(self, session_id: str) -> int:
        """Delete all messages for a session."""
        stmt = sql_delete(MessageQueue).where(MessageQueue.session_id == session_id)
        result = self.session.exec(stmt)
        self.session.commit()
        
        return result.rowcount

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    def count_by_status(self) -> dict[str, int]:
        """Get count of messages by status."""
        counts = {}
        for status in MessageStatus:
            stmt = select(func.count()).select_from(MessageQueue).where(
                MessageQueue.status == status.value
            )
            counts[status.value] = self.session.exec(stmt).one()
        return counts

    def count_pending(self) -> int:
        """Get count of pending (ready + processing) messages."""
        stmt = select(func.count()).select_from(MessageQueue).where(
            (MessageQueue.status == MessageStatus.READY.value)
            | (MessageQueue.status == MessageStatus.PROCESSING.value)
        )
        return self.session.exec(stmt).one()
