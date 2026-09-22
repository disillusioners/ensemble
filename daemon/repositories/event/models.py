"""Event database models for SSE event persistence."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

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
    INSTANCE_LIFECYCLE = "instance_lifecycle"
    ERROR = "error"
    MESSAGE_COMPLETED = "message_completed"
    # v0.13.9 fix (fix/job-completed-result-arm, 2026-09-22):
    # JobItem mirror columns were dropped in Phase 5 Batch 2 (commit
    # 41633433); the existing ``instance_lifecycle`` event was the only
    # terminal marker for JobItem-backed work, but the consumer-side
    # ``result_summary`` derivation never made it through to that event's
    # data dict — pre-fix observers had to fall back to a second
    # ``_get_last_assistant_message_raw`` seam to recover the content.
    # Add a NEW EventKind so the observer can stamp a dedicated,
    # ``job_id``-addressed event row carrying ``result_summary`` (the
    # production extraction seam, NOT a publisher mock). The new
    # kind is additive — older readers ignore unknown kinds (the
    # SSE parser surfaces ``kind`` as a string, not as a typed enum).
    JOB_COMPLETED = "job_completed"


class Event(SQLModel, table=True):
    """SQLModel Event table for SSE event persistence."""
    __tablename__ = "event"
    __table_args__ = (
        Index("idx_event_instance_created", "instance_id", "created_at"),
    )

    # Primary key (INTEGER PRIMARY KEY AUTOINCREMENT for SQLite)
    id: int | None = Field(default=None, primary_key=True)

    # Instance reference
    instance_id: str = Field(index=True)

    # Message reference (for correlating events with messages)
    message_id: str | None = Field(default=None, index=True)

    # Event type
    kind: str = Field(default=EventKind.MESSAGE_RECEIVED.value)

    # Event data (TEXT column storing JSON)
    data: str | None = Field(default=None)

    # Timestamp
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "instance_id": self.instance_id,
            "message_id": self.message_id,
            "kind": self.kind,
            "data": self.data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
