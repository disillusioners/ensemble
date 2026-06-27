"""Task-related database models (tables).

This module contains the SQLModel table definitions for Task entities
used by the worker pool.
"""

from __future__ import annotations

import enum
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Column, Index, Integer, text
from sqlmodel import SQLModel, Field


class TaskType(str, enum.Enum):
    """Task type enum.

    ``PROCESS_REPORT`` (Phase 1, 2026-06-24): the report lane — child
    completion reports ride alongside user messages on the same
    ``task`` table and the same ``ProcessMessageProcessor`` delivery
    pipeline, but are admitted under a separate type so the cross-
    system job-coordination guard in ``claim_pending_task`` does not
    apply to them. Reports have no ``JobItem`` to collide with, so the
    original job guard is irrelevant for them — only the per-instance
    serialization guard (one RUNNING task per instance) applies.
    """
    PROCESS_MESSAGE = "process_message"
    PROCESS_REPORT = "process_report"
    SEND_REPORT = "send_report"
    CLEANUP = "cleanup"


class TaskStatus(str, enum.Enum):
    """Task status enum.

    Phase 1 (2026-06-25) pause/resume redesign: ``PAUSED`` is added as
    a first-class task state so that pausing an instance can transition
    its in-flight task out of ``RUNNING`` instead of relying on the
    prior workaround of keeping the row running while the instance is
    paused. Terminal states (``COMPLETED`` / ``FAILED`` / ``CANCELLED``)
    remain unchanged.
    """
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Module-level Column kept as a reference for use in Task.__mapper_args__.
# SQLAlchemy's mapper_coercions only accepts a Column expression (or a
# string key) for version_id_col — it rejects the Pydantic-FieldInfo-
# wrapped attribute that SQLModel exposes as `Task.version`. We can't
# reference __table__.c.version at class definition time either, so we
# define the Column once and reuse it as the sa_column= value (which
# deduplicates it into the Table) and as the version_id_col target.
_task_version_col = Column("version", Integer, nullable=False, server_default="0")

# Defer-queue marker column (Phase 3 Part B1, 2026-06-27). The Pydantic
# ``Field(default=False)`` on the Python side does NOT propagate to a
# SQL DEFAULT clause — SQLModel emits ``is_deferred BOOLEAN NOT NULL``
# with no default, which would break every existing raw-SQL ``INSERT
# INTO task`` that omits the column (e.g. the retry path in
# ``TaskRepository.schedule_retry`` and the test helper
# ``_create_task_with_status``). We declare the column manually with
# ``server_default=text("false")`` so fresh databases created by
# ``SQLModel.metadata.create_all()`` emit the same schema as the
# PostgreSQL migration in ``_ensure_postgres_columns()`` and the
# SQLite migration in
# ``daemon/migrations/versions/20260627_000003_task_is_deferred.sql``.
# Indexes the column because the defer-queue idle gate filters on it
# every claim cycle.
_task_is_deferred_col = Column(
    "is_deferred", Boolean, nullable=False, server_default=text("false"), index=True
)


class Task(SQLModel, table=True):
    """SQLModel Task table for worker pool tasks."""
    __tablename__ = "task"
    __table_args__ = (
        Index("idx_task_status_created", "status", "created_at"),
    )

    # Primary key (INTEGER PRIMARY KEY AUTOINCREMENT for SQLite)
    id: int | None = Field(default=None, primary_key=True)

    # Stable cross-system work identifier (UUID4 string). Lets the virtual
    # job resolver correlate a Task row with a corresponding JobItem row
    # (or a logical work unit that spans both) without depending on the
    # integer primary key. Unique + indexed for O(1) lookup. See
    # feature/virtual-job-management-surface.
    work_id: str = Field(default_factory=lambda: str(uuid.uuid4()), index=True, unique=True, nullable=False)

    # Task identification
    task_type: str = Field(default=TaskType.PROCESS_MESSAGE.value)
    instance_id: str = Field(index=True)
    message_id: str | None = Field(default=None, index=True)

    # Status
    status: str = Field(default=TaskStatus.PENDING.value, index=True)

    # Worker assignment
    worker_id: str | None = Field(default=None, index=True)

    # Retry tracking
    retry_count: int = Field(default=0)
    next_retry_at: str | None = Field(default=None)

    # Cancellation
    cancel_requested: bool = Field(default=False)
    cancel_requested_at: str | None = Field(default=None)

    # Retry guard (atomic flag to prevent double-retry)
    retry_scheduled: bool = Field(default=False)

    # Defer queue marker (Phase 3 Part B1, 2026-06-27,
    # feature/virtual-job-management-surface). When True the task
    # belongs to the defer-queue lane: the worker pool's idle gate
    # only claims a deferred task once every non-defer queue is
    # empty, so orchestrators can stage work behind "real" traffic
    # without competing for claim slots. Defaults to False so every
    # existing task created before this column was added is treated
    # as a regular (non-defer) task — backfill is a no-op. The
    # ``sa_column=_task_is_deferred_col`` declaration provides the
    # ``server_default=text("false")`` that Pydantic's
    # ``Field(default=False)`` does NOT emit to SQL on its own — see
    # the column definition above for the rationale.
    is_deferred: bool = Field(default=False, sa_column=_task_is_deferred_col)

    # Result storage (TEXT column storing JSON)
    result: str | None = Field(default=None)

    # Error storage
    error: str | None = Field(default=None)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)

    # Liveness signal. Updated periodically by the worker's heartbeat
    # thread (see TaskHeartbeat in worker_pool.py). The recovery service
    # uses this instead of started_at to decide whether a RUNNING task
    # is genuinely alive or its worker has crashed. Distinguishing
    # live long-running tasks (e.g. a 30-min exploration with several
    # bash tool calls) from crashed ones was impossible with started_at
    # alone, because both look the same in the DB.
    last_heartbeat_at: datetime | None = Field(default=None, index=True)

    # Optimistic locking version. SQLAlchemy's version_id_col makes every
    # ORM-flushed UPDATE / DELETE on this row append `AND version = :expected`
    # to the WHERE clause and increment the version on success, raising
    # StaleDataError on a concurrent modification. The atomic raw-SQL
    # status guards in TaskRepository (complete_task, fail_task,
    # cancel_task, claim_pending_task, update_heartbeat) bypass the ORM
    # session and therefore do NOT interact with this column — those
    # paths are already protected by the status WHERE clause. The
    # version_id_col adds defense-in-depth for any remaining ORM-based
    # commit path (e.g. delete, delete_by_instance) so a stale in-memory
    # Task instance cannot silently overwrite a row that was concurrently
    # mutated.
    version: int = Field(default=0, sa_column=_task_version_col)

    # SQLAlchemy ORM configuration: declare the version column as the
    # mapper's version_id_col so the unit-of-work machinery auto-emits
    # `AND version = :expected_version` on UPDATE/DELETE.
    __mapper_args__ = {"version_id_col": _task_version_col}

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
            "work_id": self.work_id,
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
            "is_deferred": self.is_deferred,
            "result": result_data,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
        }
