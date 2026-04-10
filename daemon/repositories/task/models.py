"""Task-related database models (tables).

This module contains the SQLModel table definitions for Task entities
used by the worker pool.
"""

from __future__ import annotations

import enum
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Index
from sqlmodel import SQLModel, Field


class TaskType(str, enum.Enum):
    """Task type enum."""
    PROCESS_MESSAGE = "process_message"
    SEND_REPORT = "send_report"
    CLEANUP = "cleanup"


class TaskStatus(str, enum.Enum):
    """Task status enum."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(SQLModel, table=True):
    """SQLModel Task table for worker pool tasks."""
    __tablename__ = "task"
    __table_args__ = (
        Index("idx_task_status_created", "status", "created_at"),
    )

    # Primary key (INTEGER PRIMARY KEY AUTOINCREMENT for SQLite)
    id: Optional[int] = Field(default=None, primary_key=True)

    # Task identification
    task_type: str = Field(default=TaskType.PROCESS_MESSAGE.value)
    instance_id: str = Field(index=True)
    message_id: Optional[str] = Field(default=None, index=True)

    # Status
    status: str = Field(default=TaskStatus.PENDING.value, index=True)

    # Worker assignment
    worker_id: Optional[str] = Field(default=None, index=True)

    # Retry tracking
    retry_count: int = Field(default=0)
    next_retry_at: Optional[str] = Field(default=None)

    # Cancellation
    cancel_requested: bool = Field(default=False)
    cancel_requested_at: Optional[str] = Field(default=None)

    # Retry guard (atomic flag to prevent double-retry)
    retry_scheduled: bool = Field(default=False)

    # Result storage (TEXT column storing JSON)
    result: Optional[str] = Field(default=None)

    # Error storage
    error: Optional[str] = Field(default=None)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        result_data = None
        if self.result:
            try:
                result_data = json.loads(self.result)
            except json.JSONDecodeError:
                result_data = self.result

        return {
            "id": self.id,
            "task_type": self.task_type,
            "instance_id": self.instance_id,
            "message_id": self.message_id,
            "status": self.status,
            "worker_id": self.worker_id,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
            "cancel_requested": self.cancel_requested,
            "cancel_requested_at": self.cancel_requested_at,
            "retry_scheduled": self.retry_scheduled,
            "result": result_data,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
