"""TaskQueue-related database models (tables).

This module contains the SQLModel table definitions for the TaskQueue entity
and related Pydantic models for in-memory tracking.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy import Column, Index
from sqlalchemy.types import JSON
from sqlmodel import SQLModel, Field


class TaskStatus(str, enum.Enum):
    """Task queue status enum."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def is_valid(cls, status: str) -> bool:
        """Check if a status value is valid."""
        return status in cls._value2member_map_


class TaskQueueItem(SQLModel, table=True):
    """Task queue item - persisted for crash recovery.
    
    Tasks are serialized per-project to ensure only one task runs
    per project at a time.
    """
    __tablename__ = "task_queue_items"
    __table_args__ = (
        Index("idx_task_queue_status", "status"),
        Index("idx_task_queue_session", "session_id"),
        Index("idx_task_queue_project", "project_id"),
    )

    # Primary identification
    task_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True
    )

    # Task content
    agent_dir: str
    message: str
    source: str = Field(default="api")  # "api", "telegram", "scheduler", "webhook"

    # Project queuing (None = skip queue, execute immediately)
    project_id: Optional[str] = Field(default=None)

    # Scheduling
    priority: int = Field(default=5, ge=1, le=10)  # 1=lowest, 10=highest
    status: str = Field(default=TaskStatus.PENDING.value)

    # Timing
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Result (filled on completion)
    session_id: Optional[str] = Field(default=None)
    error_message: Optional[str] = None
    result_summary: Optional[str] = None

    # Metadata (avoiding SQLAlchemy's reserved 'metadata' attribute)
    task_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON)
    )

    # Cancellation
    cancelled_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "task_id": self.task_id,
            "agent_dir": self.agent_dir,
            "message": self.message,
            "source": self.source,
            "project_id": self.project_id,
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "session_id": self.session_id,
            "error_message": self.error_message,
            "result_summary": self.result_summary,
            "metadata": dict(self.task_metadata) if self.task_metadata else {},
            "cancelled_at": self.cancelled_at,
        }


class TaskLockInfo(BaseModel):
    """In-memory lock tracking for active tasks.
    
    Tracks which session is currently processing a task for a project.
    This is not persisted - only used during runtime.
    """
    task_id: str
    project_id: str
    session_id: str
    locked_at: datetime
