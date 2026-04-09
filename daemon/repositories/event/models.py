"""Event database models for SSE event persistence."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Index
from sqlmodel import SQLModel, Field


class EventKind(str, enum.Enum):
    """Event kind enum."""
    MESSAGE_RECEIVED = "message_received"
    PROCESSING_STARTED = "processing_started"
    PROCESSING_COMPLETED = "processing_completed"
    PROCESSING_FAILED = "processing_failed"
    CHILD_COMPLETED = "child_completed"
    CHILD_FAILED = "child_failed"
    INSTANCE_COMPLETED = "instance_completed"
    ERROR = "error"


class Event(SQLModel, table=True):
    """SQLModel Event table for SSE event persistence."""
    __tablename__ = "event"
    __table_args__ = (
        Index("idx_event_instance_created", "instance_id", "created_at"),
    )

    # Primary key (INTEGER PRIMARY KEY AUTOINCREMENT for SQLite)
    id: Optional[int] = Field(default=None, primary_key=True)

    # Instance reference
    instance_id: str = Field(index=True)

    # Event type
    kind: str = Field(default=EventKind.MESSAGE_RECEIVED.value)

    # Event data (TEXT column storing JSON)
    data: Optional[str] = Field(default=None)

    # Timestamp
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "instance_id": self.instance_id,
            "kind": self.kind,
            "data": self.data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
