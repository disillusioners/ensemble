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
from sqlalchemy import CheckConstraint, Column, Index, Integer, Text, UniqueConstraint, text
from sqlmodel import SQLModel, Field

from daemon.repositories.infra.types import JSONBType


class JobStatus(str, enum.Enum):
    """Job queue status enum."""
    PENDING = "pending"
    PROCESSING = "processing"
    # PAUSED is the first-class pause state added in Phase 1 of the
    # pause/resume redesign. Non-terminal: a paused job can be resumed
    # back to PROCESSING. See feature/pause-resume-redesign.
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"

    @classmethod
    def is_valid(cls, status: str) -> bool:
        """Check if a status value is valid."""
        return status in cls._value2member_map_


class AdmissionState(str, enum.Enum):
    """Admission state enum for the queue-proxy model (Phase 2+).

    Replaces JobStatus for queue/admission concerns. Execution lifecycle
    state is read from Instance.status via WorkResolver.

    Phase 2 introduces this column ALONGSIDE ``status`` (additive only);
    every existing ``status`` write site also writes ``admission_state``
    in the same UPDATE. Nothing reads ``admission_state`` until later
    phases. The column is dropped in Phase 5 along with ``status``.

    The four values map onto the execution-lifecycle vocabulary:
      - ``QUEUED`` — in queue, awaiting dequeue (was: PENDING)
      - ``ACTIVE`` — dequeued, lock held, instance spawned
        (was: PROCESSING / PAUSED — pause is an Instance concern, so
        a paused job stays ``active`` in admission)
      - ``DONE`` — terminal, no retry pending
        (was: COMPLETED / FAILED / CANCELLED)
      - ``DEAD`` — dead-lettered (was: DEAD_LETTER)
    """
    QUEUED = "queued"
    ACTIVE = "active"
    DONE = "done"
    DEAD = "dead"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Check if an admission_state value is valid."""
        return value in cls._value2member_map_


class Decision(str, enum.Enum):
    """Required decision for terminal admission transitions (Phase 4).

    The single terminal-write boundary ``JobQueueService._finalize_terminal``
    takes a REQUIRED ``Decision`` value so a new finalize path that forgets
    retry/DLQ handling fails at instantiation, not in production. The enum
    is closed and non-defaulted — there is no neutral member.

    Each value maps onto a single admission transition::

        NO_RETRY     active → done     (success, fail-no-retry, cancel)
        RETRY        active → queued   (retry_count+1, next_retry_at set)
        DEAD_LETTER  active → dead     (move_to_dlq)

    Phase 4 §3.2 (Plan §6.1 — C1 audit inventory): every terminal-write
    caller funnels through ``_finalize_terminal`` with one of these values.
    The structural guarantee converts the §8.2 retry-without-instance audit
    from a checklist into a grep.
    """
    NO_RETRY = "no_retry"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


# Mapping from JobStatus values (string) to AdmissionState values.
# Defined at module level so service-layer raw SQL UPDATEs can import
# it without circular-dependency risk (the helper depends only on the
# string values of JobStatus, not the enum itself).
_STATUS_TO_ADMISSION: dict[str, str] = {
    JobStatus.PENDING.value: AdmissionState.QUEUED.value,
    JobStatus.PROCESSING.value: AdmissionState.ACTIVE.value,
    # Paused jobs stay ``active`` in admission — pause is an Instance
    # concern (Instance.status == PAUSED) and the lock is still held.
    # Plan §8.1 makes this explicit.
    JobStatus.PAUSED.value: AdmissionState.ACTIVE.value,
    JobStatus.COMPLETED.value: AdmissionState.DONE.value,
    JobStatus.FAILED.value: AdmissionState.DONE.value,
    JobStatus.CANCELLED.value: AdmissionState.DONE.value,
    JobStatus.DEAD_LETTER.value: AdmissionState.DEAD.value,
}


def status_to_admission(status: str) -> str:
    """Map a JobStatus value to its corresponding AdmissionState value.

    Every existing ``status`` write site calls this helper so the
    ``admission_state`` column moves in lockstep with ``status`` in
    the SAME UPDATE statement (Phase 2 dual-write contract).

    Args:
        status: A ``JobStatus`` enum value string (e.g. ``"processing"``).

    Returns:
        The corresponding ``AdmissionState`` value string. Unknown
        statuses default to ``QUEUED`` — the safest fall-through
        (matches the column default and the model field default).
    """
    return _STATUS_TO_ADMISSION.get(status, AdmissionState.QUEUED.value)


class QueueType(str, enum.Enum):
    """Queue type enum."""
    FIFO = "fifo"
    PARALLEL = "parallel"
    DEFER = "defer"


# Module-level Column kept as a reference for use in JobItem.__mapper_args__.
# SQLAlchemy's mapper_coercions only accepts a Column expression (or a
# string key) for version_id_col — it rejects the Pydantic-FieldInfo-
# wrapped attribute that SQLModel exposes as `JobItem.version`. We can't
# reference __table__.c.version at class definition time either, so we
# define the Column once and reuse it as the sa_column= value (which
# deduplicates it into the Table) and as the version_id_col target.
_job_item_version_col = Column("version", Integer, nullable=False, server_default="0")


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
        # Phase 2 of feature/job-as-queue-proxy. The ``admission_state``
        # column co-moves with ``status`` — every site that writes
        # ``status`` also writes ``admission_state`` in the same UPDATE
        # via :func:`status_to_admission`. Nothing reads this column yet;
        # it becomes the queue-side status in Phase 4 and ``status`` is
        # dropped in Phase 5. The index supports the future ``WHERE
        # admission_state IN ('queued', 'active')`` predicates used by
        # the work-resolver sweep.
        Index("idx_job_queue_admission_state", "admission_state"),
        # M6 fix: partial UNIQUE index on ``idempotency_key`` so that
        # ``create_or_get_by_idempotency_key`` can use
        # ``INSERT ... ON CONFLICT DO NOTHING`` to atomically claim the
        # key. The ``WHERE idempotency_key IS NOT NULL`` predicate lets
        # multiple rows with NULL keys coexist (NULL ≠ NULL in unique
        # index semantics), which preserves the old "no key, no
        # constraint" behavior. The ``AND deleted_at IS NULL`` predicate
        # extends the index to also exclude soft-deleted rows — this
        # matches ``find_by_idempotency_key`` (which already filters on
        # ``deleted_at IS NULL``) and allows the soft-delete → recreate
        # pattern where a caller deletes a job then submits a fresh
        # enqueue with the same key. The same index is also created in
        # the ``20260420_000001_add_job_system_improvements`` migration
        # (without the ``deleted_at`` clause), so the
        # ``20260619_120000_fix_idempotency_index_include_deleted_at``
        # migration drops and recreates the index with the refined
        # predicate. ``create_all`` will create the correct index on
        # fresh databases.
        Index(
            "idx_job_idempotency",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
            postgresql_where=text("idempotency_key IS NOT NULL AND deleted_at IS NULL"),
        ),
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
    # Phase 2: admission_state co-moves with status (see
    # :func:`status_to_admission`). Defaults to QUEUED so freshly-
    # inserted rows are already in the correct admission bucket
    # without needing a backfill on the INSERT path.
    admission_state: str = Field(
        default=AdmissionState.QUEUED.value,
        # ``server_default`` keeps ``SQLModel.metadata.create_all()`` (used
        # by the PG test conftest) in sync with the Alembic migration's
        # ``DEFAULT 'queued'``. Without it, raw-SQL INSERTs that omit
        # the column (e.g. tests/postgres/test_optimistic_locking.py)
        # violate the NOT NULL constraint. Phase 5 will drop both the
        # column and this default.
        sa_column_kwargs={"server_default": text("'queued'")},
    )

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
        sa_column=Column("metadata", JSONBType)
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
    failed_at: str | None = Field(default=None)
    next_retry_at: str | None = Field(default=None)

    # Optimistic locking version. SQLAlchemy's version_id_col makes every
    # ORM-flushed UPDATE / DELETE on this row append `AND version = :expected`
    # to the WHERE clause and increment the version on success, raising
    # StaleDataError on a concurrent modification. The atomic status
    # transitions in JobRepository.atomic_transition (used by
    # complete_job, fail_job, cancel_job, terminate_job, start_job) issue
    # a Core UPDATE that already enforces status via WHERE status = :from
    # — with version_id_col configured, SQLAlchemy additionally appends
    # `AND version = :expected` to the WHERE, providing defense-in-depth
    # for that path. The version_id_col also protects any remaining
    # ORM-based commit path (e.g. update, soft_delete) from silently
    # overwriting a row that was concurrently mutated.
    version: int = Field(default=0, sa_column=_job_item_version_col)

    # SQLAlchemy ORM configuration: declare the version column as the
    # mapper's version_id_col so the unit-of-work machinery auto-emits
    # `AND version = :expected_version` on UPDATE/DELETE.
    __mapper_args__ = {"version_id_col": _job_item_version_col}

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
            "admission_state": self.admission_state,
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
    """Persistent lock tracking for active jobs.

    The (project_id, queue_id, lock_slot) UNIQUE constraint is the
    cross-process atomicity primitive that makes
    ``JobLockManager.acquire_queue_lock`` safe when two daemons race.
    Each acquire tries slot 0, then 1, ... up to concurrency_limit-1
    via ``INSERT OR IGNORE`` (SQLite) / ``INSERT ... ON CONFLICT DO
    NOTHING`` (PostgreSQL). Because at most one row can exist per
    (project_id, queue_id, lock_slot) tuple, at most
    concurrency_limit rows can exist per queue, and two processes
    racing for the same slot cannot both succeed. See migration
    ``20260619_000001_add_lock_slot_to_job_locks.sql`` and the
    ``ExecutionLeaseRepository.try_acquire`` pattern for the same
    idea generalised from "1 lease keyed by PK" to "N slots keyed
    by (project_id, queue_id, lock_slot)".

    NOTE: ``lock_slot`` is not user-meaningful — it exists purely to
    give the DB a uniqueness key that lets atomic INSERTs enforce the
    per-queue capacity invariant. Callers should not branch on its
    value; the only relevant query is "does a row exist for this
    (project_id, queue_id, lock_slot)?".
    """
    __tablename__ = "job_locks"
    __table_args__ = (
        # C5: enforces cross-process atomicity of acquire_queue_lock.
        UniqueConstraint(
            "project_id", "queue_id", "lock_slot",
            name="uq_job_locks_slot",
        ),
        Index("idx_job_locks_project_id", "project_id"),
        Index("idx_job_locks_queue_id", "queue_id"),
        Index("idx_job_locks_instance_id", "instance_id"),
    )

    lock_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    project_id: str = Field(index=True)
    queue_id: str = Field(index=True)
    job_id: str = Field(index=True)
    instance_id: str | None = Field(default=None, index=True)
    # C5: integer slot key. Acquire tries slots 0..limit-1 atomically;
    # release is by (project_id, queue_id, job_id) so slot value is
    # unused at release time.
    lock_slot: int = Field(default=0)
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
        sa_column=Column("metadata", JSONBType, nullable=True)
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
