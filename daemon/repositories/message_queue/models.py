"""MessageQueue-related database models (tables).

This module contains the SQLModel table definitions for the MessageQueue entity.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


class MessageType(str, enum.Enum):
    """Message type enum."""
    HUMAN = "human"        # User input via API
    AGENT = "agent"        # Agent-generated message
    SYSTEM = "system"      # System-generated message
    COMPLETION_REPORT = "completion_report"  # Child completion report
    ERROR_REPORT = "error_report"  # Error report


class MessageStatus(str, enum.Enum):
    """Message queue status enum."""
    PENDING = "pending"     # NEW: Added for consistency with task model
    READY = "ready"
    PROCESSING = "processing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class MessageQueue(SQLModel, table=True):
    """SQLModel MessageQueue table - internal ORM representation."""
    __tablename__ = "message_queue"

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    instance_id: str = Field(index=True)
    content: str
    type: str = Field(default=MessageType.AGENT.value)  # human/agent/system/completion_report/error_report
    source: Optional[str] = Field(default=None)  # Nullable source
    root_source: Optional[str] = Field(default=None)  # Root cause source
    status: str = Field(default=MessageStatus.READY.value, index=True)
    priority: int = Field(default=1)
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=5)
    error_message: Optional[str] = None
    last_error: Optional[str] = None  # Last error message
    
    # Use 'message_metadata' to avoid conflict with SQLAlchemy's reserved 'metadata'
    message_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON)
    )
    
    enqueued_at: datetime = Field(default_factory=datetime.utcnow)
    processing_started_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    
    # FK to task table
    processing_task_id: Optional[str] = Field(default=None, index=True)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "message_id": self.message_id,
            "instance_id": self.instance_id,
            "content": self.content,
            "type": self.type,
            "source": self.source,
            "root_source": self.root_source,
            "status": self.status,
            "priority": self.priority,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error_message": self.error_message,
            "last_error": self.last_error,
            "processing_task_id": self.processing_task_id,
            "metadata": dict(self.message_metadata) if self.message_metadata else {},
            "enqueued_at": self.enqueued_at.isoformat() if self.enqueued_at else None,
            "processing_started_at": self.processing_started_at.isoformat() if self.processing_started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
        }
