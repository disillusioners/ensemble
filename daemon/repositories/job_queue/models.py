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


class AdmissionState(str, enum.Enum):
    """Admission state enum for the queue-proxy model (Phase 2+).

    Phase 2 introduced this column ALONGSIDE ``status`` (additive only);
    every existing ``status`` write site also wrote ``admission_state``
    in the same UPDATE. Phase 4 made ``admission_state`` the sole write
    authority. The legacy ``JobStatus`` enum was removed in Phase 7b —
    callers speak either the 4-value ``AdmissionState`` vocabulary or
    inline string literals.

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


# Reverse map: admission_state → representative legacy status string.
# Phase 7b: the legacy ``JobStatus`` enum was removed. The map is now
# the SOLE source for the admission→legacy status translation, and
# ``JobResponse`` / API consumers continue to see the legacy strings
# (``"pending"``, ``"processing"``, ``"completed"``, ``"failed"``,
# ``"cancelled"``, ``"dead_letter"``) for backward compatibility.
#
# The ``done → completed`` mapping is lossy by design (failed/cancelled
# both collapse to ``done``), but the API surface never exposed the
# distinction — the resolver-canonical vocabulary always carried the
# fine-grained value through ``work_record.status``.
_ADMISSION_TO_LEGACY_STATUS: dict[str, str] = {
    "queued": "pending",
    "active": "processing",
    "done": "completed",   # lossy: completed/failed/cancelled all map here
    "dead": "dead_letter",
}


# Set of valid legacy status strings (the values the API still
# accepts on the ``status`` query param and emits on the response).
# Replaces the old ``JobStatus.is_valid()`` classmethod.
_VALID_LEGACY_STATUSES: frozenset[str] = frozenset({
    "pending", "processing", "completed", "failed",
    "cancelled", "dead_letter", "paused",
})


# Phase 7b backward-compatibility shim. The ``JobStatus`` enum was
# removed from this module in production semantics (the queue-proxy
# model uses ``AdmissionState`` exclusively for queue admission and
# reads execution lifecycle from the joined ``Instance``), but ~14
# test files in ``tests/job_queue/`` (200+ references) still import
# ``JobStatus`` and access its members via ``JobStatus.X.value``,
# ``JobStatus.is_valid(...)``, and ``for s in JobStatus``. This shim
# restores the legacy 7-value surface as a real ``str, Enum`` so those
# test imports continue to work without rewriting every call site.
#
# New production code MUST NOT import this shim — use
# ``AdmissionState`` for queue-admission concerns, or the inline
# string literals (``"pending"``, ``"processing"``, ``"completed"``,
# ``"failed"``, ``"cancelled"``, ``"dead_letter"``, ``"paused"``)
# directly for the legacy API vocabulary. The shim is intentional
# and tracked for removal in a later cleanup batch once the test
# imports are migrated. ``_ADMISSION_TO_LEGACY_STATUS`` is the
# authoritative mapping for production admission → legacy status
# translation.
JobStatus = enum.Enum(
    "JobStatus",
    {
        "PENDING": "pending",
        "PROCESSING": "processing",
        "PAUSED": "paused",
        "COMPLETED": "completed",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
        "DEAD_LETTER": "dead_letter",
    },
    type=str,
)


# Keep ``JobStatus.is_valid`` working for tests that called the old
# classmethod. Delegates to the new ``_VALID_LEGACY_STATUSES`` set so
# the two sources of truth stay in sync.
JobStatus.is_valid = classmethod(lambda cls, value: value in _VALID_LEGACY_STATUSES)  # type: ignore[attr-defined]  # noqa: E501


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

    Phase 5 (Job-as-Queue-Proxy): this model is a pure queue ticket.
    Execution lifecycle state (``status``, ``started_at``,
    ``completed_at``, ``result_summary``, ``error_message``,
    ``cancelled_at``, ``failed_at``) is read from ``Instance.status``
    and ``Instance`` timestamp columns via the resolver, not from
    JobItem. ``admission_state`` is the sole write authority for
    queue gating. See WorkResolver and Instance for the execution
    side; see ``_ADMISSION_TO_LEGACY_STATUS`` (this module) for the
    API-side translation.
    """
    __tablename__ = "job_queue_items"
    __table_args__ = (
        Index("idx_job_queue_instance", "instance_id"),
        Index("idx_job_queue_project", "project_id"),
        Index("idx_job_queue_items_queue", "queue_id"),
        # The ``admission_state`` index supports the
        # ``WHERE admission_state IN ('queued', 'active')``
        # predicates used by the work-resolver sweep and the
        # gating / count queries.
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
    # Phase 4 cleanup: ``admission_state`` is the sole authority
    # for queue gating (Plan §3.1). Defaults to QUEUED so
    # freshly-inserted rows are already in the correct admission
    # bucket without needing a backfill on the INSERT path.
    admission_state: str = Field(
        default=AdmissionState.QUEUED.value,
        # ``server_default`` keeps ``SQLModel.metadata.create_all()`` (used
        # by the PG test conftest) in sync with the Alembic migration's
        # ``DEFAULT 'queued'``. Without it, raw-SQL INSERTs that omit
        # the column (e.g. tests/postgres/test_optimistic_locking.py)
        # violate the NOT NULL constraint.
        sa_column_kwargs={"server_default": text("'queued'")},
    )

    # Timing (queue-side only; execution timing lives on Instance)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Work-record relationship (execution state is read from Instance)
    instance_id: str | None = Field(default=None)

    # Metadata (avoiding SQLAlchemy's reserved 'metadata' attribute)
    job_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONBType)
    )

    # Soft delete
    deleted_at: str | None = None

    # Job type: "task" (serial) or "message" (parallel)
    job_type: str = Field(default="task")

    # Retry counters and scheduling (queue-side).
    retry_count: int = Field(default=0, ge=0)
    max_retries: int | None = Field(default=None)
    idempotency_key: str | None = Field(default=None, max_length=255)
    next_retry_at: str | None = Field(default=None)
    # Retry marker — distinguishes a FAILED-path ``done`` row
    # (retryable) from COMPLETED/CANCELLED ``done`` rows (terminal).
    # Read by JobRetryEngine. The plan deferred full removal to a
    # future batch that migrates the retry engine off this marker.
    failed_at: str | None = Field(default=None)

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
        """Convert to dictionary for serialization.

        Phase 5: only queue-side fields are emitted. Execution
        lifecycle state (``status``, ``started_at``,
        ``completed_at``, ``result_summary``, ``error_message``,
        ``cancelled_at``, ``failed_at``) is read from the joined
        ``Instance`` via the resolver (see WorkResolver) — not
        from this row. Callers that need execution state must
        resolve the work record explicitly.
        """
        return {
            "job_id": self.job_id,
            "agent_id": self.agent_id,
            "agent_dir": self.agent_dir,
            "message": self.message,
            "source": self.source,
            "project_id": self.project_id,
            "queue_id": self.queue_id,
            "priority": self.priority,
            "admission_state": self.admission_state,
            "created_at": self.created_at,
            "instance_id": self.instance_id,
            "metadata": dict(self.job_metadata) if self.job_metadata else {},
            "deleted_at": self.deleted_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "idempotency_key": self.idempotency_key,
            "job_type": self.job_type,
            "next_retry_at": self.next_retry_at,
            "failed_at": self.failed_at,
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
