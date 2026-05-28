"""JobQueue-related database models (tables).

This module contains the SQLModel table definitions for the JobQueue entity
and related Pydantic models for in-memory tracking.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import CheckConstraint, Column, Index, UniqueConstraint
from sqlalchemy.types import JSON, Text
from sqlmodel import SQLModel, Field


class JobStatus(str, enum.Enum):
    """Job queue status enum."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"

    @classmethod
    def is_valid(cls, status: str) -> bool:
        """Check if a status value is valid."""
        return status in cls._value2member_map_


class QueueType(str, enum.Enum):
    """Queue type enum."""
    FIFO = "fifo"
    PARALLEL = "parallel"
    DEFER = "defer"


class JobQueue(SQLModel, table=True):
    """Named job queue for per-project job isolation."""
    __tablename__ = "job_queues"
    __table_args__ = (
        CheckConstraint("queue_type IN ('fifo', 'parallel', 'defer')", name="ck_job_queues_queue_type"),
        CheckConstraint("queue_type != 'defer' OR concurrency_limit = 1", name="ck_job_queues_defer_concurrency"),
        Index("idx_job_queues_project", "project_id"),
        UniqueConstraint("project_id", "queue_name_lower", name="uq_job_queues_project_name"),
    )

    # Primary identification
    queue_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True
    )
    
    # Queue identity
    project_id: str  # NOT NULL, FK target (no foreign_key= param)
    queue_name: str = Field(default="default", max_length=100)
    queue_name_lower: str = Field(default="default", max_length=100)  # For case-insensitive uniqueness
    queue_type: str = Field(default=QueueType.FIFO.value)  # "fifo", "parallel", or "defer"
    
    # Queue configuration
    concurrency_limit: int = Field(default=1, ge=1, le=20)
    is_system: bool = Field(default=False)
    is_paused: bool = Field(default=False)
    description: str | None = None
    default_max_retries: int | None = Field(default=None)
    
    # Timestamps
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="after")
    def enforce_defer_concurrency_limit(self) -> "JobQueue":
        """Defer queues must have concurrency_limit=1."""
        if self.queue_type == QueueType.DEFER.value and self.concurrency_limit != 1:
            raise ValueError("Defer queues must have concurrency_limit=1")
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "queue_id": self.queue_id,
            "project_id": self.project_id,
            "queue_name": self.queue_name,
            "queue_name_lower": self.queue_name_lower,
            "queue_type": self.queue_type,
            "concurrency_limit": self.concurrency_limit,
            "is_system": self.is_system,
            "is_paused": self.is_paused,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobItem(SQLModel, table=True):
    """Job queue item - persisted for crash recovery.
    
    Jobs are serialized per-project to ensure only one job runs
    per project at a time.
    """
    __tablename__ = "job_queue_items"
    __table_args__ = (
        Index("idx_job_queue_status", "status"),
        Index("idx_job_queue_instance", "instance_id"),
        Index("idx_job_queue_project", "project_id"),
        Index("idx_job_queue_items_queue", "queue_id"),
        Index("idx_job_queue_items_project_status_deleted", "project_id", "status", "deleted_at"),
        Index("idx_job_queue_items_status_type_instance", "status", "job_type", "instance_id"),
    )

    # Primary identification
    job_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True
    )

    # Job content
    agent_id: str
    agent_dir: str
    message: str
    source: str = Field(default="api")  # "api", "telegram", "scheduler", "webhook"

    # Project queuing (None = skip queue, execute immediately)
    project_id: str | None = Field(default=None)
    queue_id: str | None = Field(default=None, foreign_key="job_queues.queue_id")

    # Scheduling
    priority: int = Field(default=5, ge=1, le=10)  # 1=lowest, 10=highest
    status: str = Field(default=JobStatus.PENDING.value)

    # Timing
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None

    # Result (filled on completion)
    instance_id: str | None = Field(default=None)
    error_message: str | None = None
    result_summary: str | None = None

    # Metadata (avoiding SQLAlchemy's reserved 'metadata' attribute)
    job_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON)
    )

    # Cancellation
    cancelled_at: str | None = None

    # Soft delete
    deleted_at: str | None = None

    # Job type: "task" (serial) or "message" (parallel)
    job_type: str = Field(default="task")

    # Retry handling
    retry_count: int = Field(default=0, ge=0)
    max_retries: int | None = Field(default=None)
    idempotency_key: str | None = Field(default=None, max_length=255)
    failed_at: str | None = None
    next_retry_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "agent_dir": self.agent_dir,
            "message": self.message,
            "source": self.source,
            "project_id": self.project_id,
            "queue_id": self.queue_id,
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "instance_id": self.instance_id,
            "error_message": self.error_message,
            "result_summary": self.result_summary,
            "metadata": dict(self.job_metadata) if self.job_metadata else {},
            "cancelled_at": self.cancelled_at,
            "deleted_at": self.deleted_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "idempotency_key": self.idempotency_key,
            "job_type": self.job_type,
            "failed_at": self.failed_at,
            "next_retry_at": self.next_retry_at,
        }


class JobLockInfo(BaseModel):
    """In-memory lock tracking for active jobs.
    
    Tracks which instance is currently processing a job for a queue.
    This is not persisted - only used during runtime.
    """
    job_id: str
    project_id: str
    queue_id: str
    instance_id: str
    locked_at: datetime


class JobLock(SQLModel, table=True):
    """Persistent lock tracking for active jobs."""
    __tablename__ = "job_locks"

    lock_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    project_id: str = Field(index=True)
    queue_id: str = Field(index=True)
    job_id: str = Field(index=True)
    instance_id: str | None = Field(default=None, index=True)
    acquired_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DeadLetterItem(SQLModel, table=True):
    """Dead letter queue item for failed jobs that exceeded retry limits.
    
    Jobs that fail after exhausting their retry attempts are moved here for
    later inspection, manual replay, or cleanup.
    """
    __tablename__ = "dead_letter_items"
    __table_args__ = (
        Index("idx_dead_letter_job_id", "job_id", unique=True),
        Index("idx_dead_letter_project", "project_id"),
        Index("idx_dead_letter_queue", "queue_id"),
        Index("idx_dead_letter_moved_at", "moved_to_dlq_at"),
    )

    # Primary identification
    dlq_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    
    # Original job reference
    job_id: str
    
    # Job content (duplicated for quick access without joining)
    agent_id: str
    agent_dir: str
    message: str
    source: str
    
    # Queue routing
    project_id: str
    queue_id: str
    priority: int = Field(default=5)
    
    # Error details
    error_message: str
    retry_count: int = Field(default=0)
    failed_at: str
    
    # DLQ metadata
    moved_to_dlq_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str  # "MAX_RETRIES", "MANUAL", "CIRCUIT_BREAKER", etc.
    
    # Optional metadata storage
    metadata_json: dict[str, Any | None] = Field(
        default=None,
        sa_column=Column("metadata", JSON, nullable=True)
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "dlq_id": self.dlq_id,
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "agent_dir": self.agent_dir,
            "message": self.message,
            "source": self.source,
            "project_id": self.project_id,
            "queue_id": self.queue_id,
            "priority": self.priority,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "failed_at": self.failed_at,
            "moved_to_dlq_at": self.moved_to_dlq_at,
            "reason": self.reason,
            "metadata": dict(self.metadata_json) if self.metadata_json else {},
        }
