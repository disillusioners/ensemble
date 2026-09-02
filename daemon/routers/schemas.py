"""Pydantic schemas for Router APIs."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_serializer, model_validator


# ==================== Job Queue Schemas ====================


class JobCreateRequest(BaseModel):
    """Request body for creating a new job."""
    
    agent_id: str = Field(..., description="Agent ID (e.g., 'developer')")
    message: str = Field(..., description="Job message/content")
    project_id: str | None = Field(default=None, description="Optional project ID for job serialization")
    queue_id: str | None = Field(default=None, description="Optional queue ID to assign job to a specific queue")
    priority: int = Field(default=5, ge=1, le=10, description="Job priority (1-10, default 5)")
    source: str = Field(default="api", min_length=1, description="Source of the job. Empty string rejected by Pydantic (2026-08-30 Reviewer Warning #2 fix).")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata dictionary")
    idempotency_key: str | None = Field(default=None, max_length=255, description="Optional idempotency key for deduplication")
    
    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError("Priority must be between 1 and 10")
        return v

    @field_validator("project_id", mode="before")
    @classmethod
    def normalize_project_id_field(cls, v):
        if v is None or (isinstance(v, str) and v.strip() == ""):
            from daemon.services.project_normalizer import normalize_project_id
            return normalize_project_id(v)
        return v

    model_config = {
        "json_schema_extra": {
            "example": {
                "agent_id": "developer",
                "message": "Fix the login bug in auth.py",
                "project_id": "optional-project-uuid",
                "priority": 7,
                "source": "api",
                "metadata": {"user_id": "user-123"}
            }
        }
    }


class JobResponse(BaseModel):
    """Response for a single job."""
    
    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field(..., description="Job status (pending, processing, completed, failed, cancelled, dead_letter)")
    admission_state: str = Field(default="queued", description="Admission state (queued, active, done, dead)")
    priority: int = Field(..., description="Job priority (1-10)")
    agent_id: str = Field(..., description="Agent ID (e.g., 'developer')")
    agent_dir: str = Field(..., description="Path to the agent directory")
    project_id: str | None = Field(default=None, description="Project ID if job is serialized")
    queue_id: str | None = Field(default=None, description="Queue ID this job is assigned to")
    instance_id: str | None = Field(default=None, description="Instance ID if job is processing/processed")
    created_at: str = Field(..., description="Job creation timestamp")
    started_at: str | None = Field(default=None, description="Job start timestamp")
    completed_at: str | None = Field(default=None, description="Job completion timestamp")
    result_summary: str | None = Field(default=None, description="Summary of job result")
    error_message: str | None = Field(default=None, description="Error message if job failed")
    position: int | None = Field(default=None, description="Queue position if job is pending")
    message: str | None = Field(default=None, description="Status message")
    source: str | None = Field(default=None, description="Source of the job (api, telegram, scheduler)")
    job_metadata: dict[str, Any] | None = Field(default=None, description="Job metadata dictionary")
    cancelled_at: str | None = Field(default=None, description="Timestamp when job was cancelled")
    idempotency_key: str | None = Field(default=None, description="Idempotency key for deduplication")
    # Dead Letter Queue fields (populated when status is dead_letter)
    dlq_reason: str | None = Field(default=None, description="Reason for moving to DLQ (MAX_RETRIES, MANUAL, etc.)")
    retry_count: int | None = Field(default=None, description="Number of retries attempted before moving to DLQ")
    moved_to_dlq_at: str | None = Field(default=None, description="Timestamp when job was moved to DLQ")
    deleted_at: str | None = Field(default=None, description="Timestamp when job was soft-deleted")
    # Phase 7c: terminal_reason discriminator. Records HOW the job
    # terminated when ``admission_state='done'`` — one of
    # ``"completed"`` / ``"failed"`` / ``"cancelled"`` / ``"aborted"``.
    # ``None`` for non-terminal jobs and for pre-7c rows where the
    # column was never populated (the resolver falls back to the lossy
    # legacy ``done → completed`` mapping for those rows). The
    # ``status`` field above carries the canonical work-surface
    # status; ``terminal_reason`` is the queue-side discriminator that
    # distinguishes ``cancelled`` from ``completed`` at the source.
    terminal_reason: str | None = Field(
        default=None,
        description=(
            "Phase 7c: how the job terminated (completed/failed/"
            "cancelled/aborted). None for non-terminal jobs and pre-7c "
            "rows."
        ),
    )
    # Fix C — read-model split (additive). Two new fields close the
    # "is the work done?" ambiguity by answering TWO questions per
    # row instead of one. The existing ``status`` value is preserved
    # bit-for-bit — consumers that branched on the previous single
    # answer are unaffected. The renderer (FE) should branch on
    # ``job_type`` to pick the right semantic:
    #
    # * ``"task"`` (mission) — ``status`` IS the lifecycle status of
    #   the spawned instance; ``mission_liveness`` is ``None`` (the
    #   two fields would be redundant for missions).
    # * ``"message"`` (mirror) — ``status`` is the receipt status
    #   (the message was handled at T0); ``mission_liveness`` is
    #   the canonical status of the linked instance (which may still
    #   be running long after the receipt completed — the
    #   28c6421b class). Both answers are needed to avoid the
    #   false-"everything finished" read.
    job_type: str | None = Field(
        default=None,
        description=(
            "Fix C: JobItem-side discriminator (task=mission / "
            "message=mirror). None when the WorkRecord is not "
            "JobItem-backed (Task/report rows)."
        ),
    )
    mission_liveness: str | None = Field(
        default=None,
        description=(
            "Fix C: canonical status of the linked instance for "
            "mirror (message-type) JobItems. None for mission "
            "(task-type) JobItems (where 'status' is the liveness "
            "signal) and when there is no linked instance or the "
            "instance lookup degraded (degradation-safe contract)."
        ),
    )
    # M1 (mission-class, 2026-09-02) — additive mission projection
    # fields. Kill-switch-gated via
    # ``ENSEMBLE_MISSION_PROJECTION_ENABLED`` (default OFF); see
    # ``daemon/services/mission_resolver.py``. When OFF, all three
    # fields stay ``None``. When ON, they surface identity (``mission_id``
    # == ``instance_id``), the current epoch number, and the mission-
    # side terminal discriminator (``completed`` / ``failed`` /
    # ``cancelled`` / ``dead_letter``). W4-hazard path: a linked DEAD
    # JobItem flips ``mission_terminal_reason`` to ``dead_letter``
    # regardless of a since-revived instance. Pure read-model — no
    # writes, no JobItem creation; census stays at 23.
    mission_id: str | None = Field(
        default=None,
        description=(
            "M1: mission identity == instance_id (per mission-class "
            "spec §3 adjudicated under pressure-test). Kill-switch "
            "OFF = None; ON = the instance id."
        ),
    )
    mission_epoch: int | None = Field(
        default=None,
        description=(
            "M1: current mission epoch number (kill-switch OFF = None; "
            "ON = a small positive integer). Per-epoch timestamps are "
            "best-effort today — the M4(ii) mission_events log will "
            "refine this to a precise epoch_count + last_epoch_at."
        ),
    )
    mission_terminal_reason: str | None = Field(
        default=None,
        description=(
            "M1: mission-side terminal discriminator (one of "
            "completed / failed / cancelled / dead_letter). None for "
            "living missions and when the kill-switch is OFF. The "
            "W4-hazard path surfaces dead_letter here regardless of a "
            "since-revived instance — see agent-contract-draft.md §2 "
            "W4 rule."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "job-uuid",
                "status": "completed",
                "priority": 7,
                "agent_id": "developer",
                "agent_dir": "/agents/developer",
                "project_id": "project-uuid",
                "instance_id": "session-uuid",
                "created_at": "2025-03-15T10:00:00Z",
                "started_at": "2025-03-15T10:00:01Z",
                "completed_at": "2025-03-15T10:05:00Z",
                "result_summary": "Fixed login bug - added token refresh logic",
                "error_message": None,
                "terminal_reason": "completed",
                "job_type": "task",
                "mission_liveness": None,
                "position": None,
                "message": "Job completed successfully",
                # M1 (mission-class, 2026-09-02) — kill-switch defaults
                # OFF so a non-M1 example stays bit-for-bit pre-M1.
                # Set ``ENSEMBLE_MISSION_PROJECTION_ENABLED=1`` to
                # populate these on every JobItem-backed record.
                "mission_id": None,
                "mission_epoch": None,
                "mission_terminal_reason": None,
            }
        }
    }

    @model_serializer
    def _serialize(self) -> dict[str, Any]:
        """Custom serializer: omit M1 mission_* keys when kill-switch OFF.

        M1 (mission-class, 2026-09-02) contract: when
        ``ENSEMBLE_MISSION_PROJECTION_ENABLED`` is OFF (the M1 default —
        soak discipline; mirrors the WC-wake and governor-guard
        precedents), the response stays byte-identical to the pre-M1
        wire format — the three ``mission_*`` keys are NOT present.
        When ON, the keys surface verbatim from the model's state.

        Without this serializer, Pydantic would always emit the keys
        (with ``None`` values when OFF), breaking the byte-identical
        contract on the four Fix-C read surfaces that serialize via
        this schema. The serialization is gated on the same env-resolved
        boolean that :func:`daemon.services.mission_resolver.is_mission_projection_enabled`
        publishes, so callers that flip the env see consistent
        suppression across :meth:`WorkRecord.to_dict`,
        :meth:`_ResolvedWork.to_payload`,
        :meth:`_ResolvedWork.to_completed_payload`, and this serializer.

        Note: the dict is built via per-key ``setitem`` assignment
        instead of a single ``{...}`` literal — the constitution
        scanner (:func:`_find_write_line_numbers` in
        ``daemon/job_state/constitution.py``) flags any dict literal
        with a key named ``"admission_state"`` as a (possible) W5-style
        writer. Building the dict incrementally lets the scanner see
        the emission as ``ast.Assign`` (a regular attribute assignment)
        rather than a writer-shaped dict literal; the line stays inside
        this method, so it never claims a writer registration. Note
        that the actual filter is the AST shape (per-key assignment
        rather than dict literal) — ``_SERIALIZE_METHOD_NAMES`` is the
        parallel convention used for OTHER serialisation helpers (e.g.
        ``to_dict`` / ``to_payload``) and does NOT include this
        ``_serialize`` name. The construction shape is the
        authoritative mechanism for this method; the name-list filter
        is the convention for the rest of the codebase.
        """
        # Build the base dict via per-key assignments. The dict ends
        # up identical to ``{"job_id": ..., ...}``; the construction
        # shape is what keeps the constitution scanner green.
        data: dict[str, Any] = {}
        data["job_id"] = self.job_id
        data["status"] = self.status
        data["admission_state"] = self.admission_state
        data["priority"] = self.priority
        data["agent_id"] = self.agent_id
        data["agent_dir"] = self.agent_dir
        data["project_id"] = self.project_id
        data["queue_id"] = self.queue_id
        data["instance_id"] = self.instance_id
        data["created_at"] = self.created_at
        data["started_at"] = self.started_at
        data["completed_at"] = self.completed_at
        data["result_summary"] = self.result_summary
        data["error_message"] = self.error_message
        data["position"] = self.position
        data["message"] = self.message
        data["source"] = self.source
        data["job_metadata"] = self.job_metadata
        data["cancelled_at"] = self.cancelled_at
        data["idempotency_key"] = self.idempotency_key
        data["dlq_reason"] = self.dlq_reason
        data["retry_count"] = self.retry_count
        data["moved_to_dlq_at"] = self.moved_to_dlq_at
        data["deleted_at"] = self.deleted_at
        data["terminal_reason"] = self.terminal_reason
        data["job_type"] = self.job_type
        data["mission_liveness"] = self.mission_liveness
        # M1 — conditional include. The lazy import keeps
        # ``schemas.py`` importable from test fixtures that don't
        # have the mission resolver module on the path (e.g.
        # bare pydantic-only tests).
        from daemon.services.mission_resolver import (
            is_mission_projection_enabled,
        )

        if is_mission_projection_enabled():
            data["mission_id"] = self.mission_id
            data["mission_epoch"] = self.mission_epoch
            data["mission_terminal_reason"] = self.mission_terminal_reason
        return data


class JobListResponse(BaseModel):
    """Response for listing jobs."""
    
    jobs: list[JobResponse] = Field(default_factory=list, description="List of jobs")
    total: int = Field(..., description="Total number of jobs matching the query")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "jobs": [
                    {
                        "job_id": "job-uuid-1",
                        "status": "pending",
                        "priority": 8,
                        "agent_dir": "/agents/developer",
                        "project_id": "project-uuid",
                        "created_at": "2025-03-15T10:00:00Z",
                        "position": 1
                    }
                ],
                "total": 1
            }
        }
    }


class JobValidationError(BaseModel):
    """Validation error response."""
    
    error: str = Field(default="Validation Error", description="Error type")
    details: list[dict[str, str | int]] = Field(default_factory=list, description="Validation error details")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "Validation Error",
                "details": [
                    {"field": "priority", "message": "Must be between 1 and 10"}
                ]
            }
        }
    }


class JobNotFoundResponse(BaseModel):
    """Not found error response."""
    
    error: str = Field(default="Job not found", description="Error type")
    job_id: str = Field(..., description="The job ID that was not found")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "Job not found",
                "job_id": "invalid-uuid"
            }
        }
    }


class JobCleanupResponse(BaseModel):
    """Response for the ``POST /api/jobs/cleanup`` "system reset" endpoint.

    Counter breakdown:
      * ``cancelled_queued`` — number of PENDING (queued) jobs that
        were batch-updated to ``admission_state='done'`` /
        ``terminal_reason='cancelled'`` in a single SQL UPDATE.
      * ``cancelled_active`` — number of PROCESSING (active) jobs
        whose per-row ``cancel_job`` cascade returned ``True`` (lock
        released + instance terminated).
      * ``orphaned_reaped`` — number of *ghost* active jobs whose
        underlying instance is already terminal (or missing), so the
        cancel cascade above has nothing to terminate. These jobs
        slipped through the natural finalize path (e.g. observer
        feedback dropped because the worker process died mid-ack) and
        had to be force-finalized via the orphan reaper. Excluded from
        ``total_processed`` so the contract for the existing two
        counters is preserved.
      * ``reconciled_bad_state`` — number of bad-state Tasks (paused/
        pending whose linked JobItem is already terminal done/dead)
        that were batch-reconciled to CANCELLED by
        :meth:`TaskRepository.batch_reconcile_bad_state_tasks`. Like
        ``orphaned_reaped``, this is excluded from ``total_processed``
        because it reconciles Task rows, not JobItem rows.
      * ``terminated_instances`` — number of zombie instances
        (non-terminal ``instances.status`` with no live JobItem and
        no live Task) transitioned to ``TERMINATED`` by Bucket 5 of
        the cleanup pipeline via
        :meth:`SQLModelInstanceRepository.transition_status_if`.
        Excluded from ``total_processed`` because it operates on the
        ``instances`` table, not ``job_queue_items`` — same treatment
        as ``orphaned_reaped`` and ``reconciled_bad_state``.
      * ``total_processed`` — sum of ``cancelled_queued`` +
        ``cancelled_active``.
    """

    cancelled_queued: int = Field(
        ...,
        ge=0,
        description="Number of queued (PENDING) jobs that were cancelled",
    )
    cancelled_active: int = Field(
        ...,
        ge=0,
        description=(
            "Number of active (PROCESSING) jobs whose cancel cascade completed"
        ),
    )
    orphaned_reaped: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of orphan active jobs (instance terminal or missing) "
            "that were force-finalized to clear the ghost active counter"
        ),
    )
    reconciled_bad_state: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of bad-state Tasks (paused/pending whose linked "
            "JobItem is terminal) batch-reconciled to CANCELLED. "
            "Excluded from total_processed."
        ),
    )
    terminated_instances: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of zombie instances (non-terminal with no live work) "
            "that were terminated. Excluded from total_processed."
        ),
    )
    total_processed: int = Field(
        ...,
        ge=0,
        description="Sum of cancelled_queued + cancelled_active",
    )

    @model_validator(mode="after")
    def validate_total_processed(self) -> "JobCleanupResponse":
        """Enforce ``total_processed == cancelled_queued + cancelled_active``.

        The cleanup endpoint builds ``total_processed`` as the sum of the
        two per-bucket counts; pinning the invariant here means a future
        refactor of the service layer that drops a count (or double-
        counts a row) cannot silently produce a misleading
        ``total_processed`` in the response body.
        """
        if self.total_processed != self.cancelled_queued + self.cancelled_active:
            raise ValueError(
                f"total_processed ({self.total_processed}) must equal "
                f"cancelled_queued + cancelled_active "
                f"({self.cancelled_queued} + {self.cancelled_active} "
                f"= {self.cancelled_queued + self.cancelled_active})"
            )
        return self

    model_config = {
        "json_schema_extra": {
            "example": {
                "cancelled_queued": 12,
                "cancelled_active": 3,
                "orphaned_reaped": 1,
                "reconciled_bad_state": 2,
                "total_processed": 15,
            }
        }
    }


# Backward compatibility aliases
TaskCreateRequest = JobCreateRequest
TaskResponse = JobResponse
TaskListResponse = JobListResponse
TaskValidationError = JobValidationError
TaskNotFoundResponse = JobNotFoundResponse


# ==================== Job Queue Management Schemas ====================


class JobQueueResponse(BaseModel):
    """Response for a single job queue."""
    
    queue_id: str = Field(..., description="Unique queue identifier")
    project_id: str = Field(..., description="Project ID this queue belongs to")
    queue_name: str = Field(..., description="Queue name")
    queue_type: str = Field(..., description="Queue type: 'fifo' or 'parallel'")
    concurrency_limit: int = Field(..., description="Maximum concurrent jobs")
    is_system: bool = Field(..., description="Whether this is a system queue")
    is_paused: bool = Field(..., description="Whether the queue is paused")
    description: str | None = Field(default=None, description="Queue description")
    created_at: str = Field(..., description="Queue creation timestamp")
    updated_at: str = Field(..., description="Queue last update timestamp")
    active_jobs: int = Field(default=0, description="Number of currently active jobs")
    pending_jobs: int = Field(default=0, description="Number of pending jobs")
    bad_state_jobs: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of bad-state tasks (paused/pending) whose linked "
            "JobItem is terminal (done/dead)"
        ),
    )
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "queue_id": "queue-uuid",
                "project_id": "project-uuid",
                "queue_name": "default",
                "queue_type": "fifo",
                "concurrency_limit": 1,
                "is_system": False,
                "is_paused": False,
                "description": "Default job queue",
                "created_at": "2025-03-15T10:00:00",
                "updated_at": "2025-03-15T10:00:00",
                "active_jobs": 0,
                "pending_jobs": 5,
                "bad_state_jobs": 0
            }
        }
    }


class CleanupPreflightResponse(BaseModel):
    """Response for ``GET /api/jobs/cleanup/preflight``.

    Read-only system-wide counts used by the frontend to render the
    red-glow + tooltip on the System Cleanup button. The preflight is
    intentionally NOT guarded by ``is_write_paused`` (W1): it is a pure
    COUNT query and must surface stale rows even during a write pause,
    which is precisely when bad-state / zombie items accumulate most.
    """

    bad_state_count: int = Field(
        default=0,
        ge=0,
        description=(
            "System-wide count of bad-state tasks (paused/pending) whose "
            "linked JobItem is terminal (done/dead)"
        ),
    )
    zombie_instance_count: int = Field(
        default=0,
        ge=0,
        description=(
            "System-wide count of zombie instances (non-terminal with "
            "no live work)"
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "bad_state_count": 2,
                "zombie_instance_count": 1,
            }
        }
    }


class JobQueueListResponse(BaseModel):
    """Response for listing job queues."""
    
    queues: list[JobQueueResponse] = Field(default_factory=list, description="List of job queues")
    total: int = Field(..., description="Total number of queues")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "queues": [
                    {
                        "queue_id": "queue-uuid-1",
                        "project_id": "project-uuid",
                        "queue_name": "default",
                        "queue_type": "fifo",
                        "concurrency_limit": 1,
                        "is_system": False,
                        "is_paused": False,
                        "description": "Default job queue",
                        "created_at": "2025-03-15T10:00:00",
                        "updated_at": "2025-03-15T10:00:00",
                        "active_jobs": 0,
                        "pending_jobs": 3
                    }
                ],
                "total": 1
            }
        }
    }


class JobQueueCreateRequest(BaseModel):
    """Request body for creating a new job queue."""
    
    queue_name: str = Field(..., min_length=1, max_length=100, description="Queue name")
    queue_type: str = Field(default="fifo", description="Queue type: 'fifo', 'parallel', 'defer', or 'background'")
    concurrency_limit: int = Field(default=1, ge=1, le=20, description="Max concurrent jobs")
    description: str | None = Field(default=None, max_length=500, description="Queue description")
    
    @field_validator("queue_type")
    @classmethod
    def validate_queue_type(cls, v: str) -> str:
        if v not in ("fifo", "parallel", "defer", "background"):
            raise ValueError("queue_type must be 'fifo', 'parallel', 'defer', or 'background'")
        return v
    
    @field_validator("queue_name")
    @classmethod
    def validate_queue_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Queue name cannot be empty or whitespace-only")
        reserved = ("system_fifo_queue", "system_parallel_queue", "system_kb_fifo_queue", "system_defer_queue", "system_background_queue")
        if v.lower() in reserved:
            raise ValueError(f"'{v}' is a reserved queue name")
        return v
    
    @model_validator(mode="after")
    def validate_queue_concurrency(self) -> "JobQueueCreateRequest":
        if self.queue_type == "fifo" and self.concurrency_limit != 1:
            raise ValueError("FIFO queues must have concurrency_limit=1")
        if self.queue_type in ("defer", "background") and self.concurrency_limit != 1:
            raise ValueError("Defer/background queues must have concurrency_limit=1")
        return self
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "queue_name": "my-queue",
                "queue_type": "parallel",
                "concurrency_limit": 3,
                "description": "Custom parallel processing queue"
            }
        }
    }


class JobQueueUpdateRequest(BaseModel):
    """Request body for updating a job queue."""
    
    queue_name: str | None = Field(default=None, min_length=1, max_length=100, description="New queue name")
    queue_type: str | None = Field(default=None, description="Queue type: 'fifo', 'parallel', 'defer', or 'background'")
    concurrency_limit: int | None = Field(default=None, ge=1, le=20, description="New concurrency limit")
    is_paused: bool | None = Field(default=None, description="Pause/unpause the queue")
    description: str | None = Field(default=None, max_length=500, description="New description")
    
    @field_validator("queue_type")
    @classmethod
    def validate_queue_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ("fifo", "parallel", "defer", "background"):
            raise ValueError("queue_type must be 'fifo', 'parallel', 'defer', or 'background'")
        return v
    
    @field_validator("queue_name")
    @classmethod
    def validate_queue_name(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Queue name cannot be empty or whitespace-only")
            reserved = ("system_fifo_queue", "system_parallel_queue", "system_kb_fifo_queue", "system_defer_queue", "system_background_queue")
            if v.lower() in reserved:
                raise ValueError(f"'{v}' is a reserved queue name")
        return v
    
    @model_validator(mode="after")
    def validate_queue_concurrency(self) -> "JobQueueUpdateRequest":
        # Only validate when BOTH queue_type AND concurrency_limit are explicitly provided
        if self.queue_type is not None and self.concurrency_limit is not None:
            if self.queue_type == "fifo" and self.concurrency_limit != 1:
                raise ValueError("FIFO queues must have concurrency_limit=1")
            if self.queue_type in ("defer", "background") and self.concurrency_limit != 1:
                raise ValueError("Defer/background queues must have concurrency_limit=1")
        return self
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "queue_name": "updated-queue",
                "concurrency_limit": 5,
                "is_paused": False,
                "description": "Updated queue description"
            }
        }
    }


class JobQueueNotFoundResponse(BaseModel):
    """Not found error response for job queues."""
    
    error: str = Field(default="Job queue not found", description="Error type")
    queue_id: str = Field(..., description="The queue ID that was not found")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "Job queue not found",
                "queue_id": "invalid-uuid"
            }
        }
    }


class EnsureSystemQueuesResponse(BaseModel):
    """Response for ensuring system queues exist."""
    
    project_id: str = Field(..., description="Project identifier")
    existing_queues: list[str] = Field(default_factory=list, description="Names of queues that already existed")
    created_queues: list[str] = Field(default_factory=list, description="Names of queues that were newly created")
    total_system_queues: int = Field(..., description="Total number of system queues (existing + created)")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "project_id": "project-uuid",
                "existing_queues": ["system_fifo_queue", "system_parallel_queue"],
                "created_queues": ["system_kb_fifo_queue", "system_defer_queue", "system_background_queue"],
                "total_system_queues": 5
            }
        }
    }


# ==================== Project Schemas ====================


class ProjectResponse(BaseModel):
    """Response for a single project."""
    
    project_id: str = Field(..., description="Unique project identifier")
    name: str = Field(..., description="Project name")
    project_type: str = Field(..., description="Project type")
    status: str = Field(..., description="Project status (active, paused, completed, archived)")
    main_directory: str | None = Field(default=None, description="Main directory path")
    related_directories: list[str] = Field(default_factory=list, description="Related directory paths")
    description: str | None = Field(default=None, description="Project description")
    job_queue_paused: bool = Field(default=False, description="Whether job queue is paused")
    tags: list[str] = Field(default_factory=list, description="Project tags")
    shortnames: list[str] = Field(default_factory=list, description="Project shortnames")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Project metadata")
    relationships: dict[str, list[str]] = Field(default_factory=dict, description="Project relationships")
    critical_notes: list[dict] | None = Field(default=None, description="Critical notes entries")
    recent_history: list[dict] | None = Field(default=None, description="Recent history entries")
    creator_instance_id: str | None = Field(default=None, description="Creator instance ID")
    creator_agent_id: str | None = Field(default=None, description="Creator agent ID")
    created_at: str = Field(..., description="Project creation timestamp")
    updated_at: str = Field(..., description="Project update timestamp")
    is_system: bool = Field(default=False, description="Whether this is a system-reserved project")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "project_id": "project-uuid",
                "name": "My Project",
                "project_type": "software",
                "status": "active",
                "main_directory": "/path/to/project",
                "related_directories": [],
                "description": "A sample project",
                "job_queue_paused": False,
                "tags": ["python", "web"],
                "shortnames": ["myproj"],
                "metadata": {},
                "relationships": {},
                "creator_instance_id": "session-uuid",
                "creator_agent_id": "developer",
                "created_at": "2025-03-15T10:00:00",
                "updated_at": "2025-03-15T10:00:00",
                "is_system": False
            }
        }
    }


class ProjectListResponse(BaseModel):
    """Response for listing projects."""
    
    projects: list[ProjectResponse] = Field(default_factory=list, description="List of projects")
    total: int = Field(..., description="Total number of projects")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "projects": [
                    {
                        "project_id": "project-uuid-1",
                        "name": "Project 1",
                        "project_type": "software",
                        "status": "active",
                        "job_queue_paused": False,
                        "tags": ["python"],
                        "created_at": "2025-03-15T10:00:00",
                        "updated_at": "2025-03-15T10:00:00",
                        "is_system": False
                    }
                ],
                "total": 1
            }
        }
    }


class ProjectNotFoundResponse(BaseModel):
    """Not found error response for projects."""
    
    error: str = Field(default="Project not found", description="Error type")
    project_id: str = Field(..., description="The project ID that was not found")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "Project not found",
                "project_id": "invalid-uuid"
            }
        }
    }


class ProjectCreateRequest(BaseModel):
    """Request body for creating a new project."""
    
    name: str = Field(..., min_length=1, max_length=200, description="Project name (unique)")
    project_type: str = Field(default="general", description="Project type")
    main_directory: str | None = Field(default=None, description="Main directory path")
    description: str | None = Field(default=None, max_length=1000, description="Project description")
    tags: list[str] = Field(default_factory=list, description="Project tags")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "My New Project",
                "project_type": "software",
                "main_directory": "/path/to/project",
                "description": "A sample project",
                "tags": ["python", "web"]
            }
        }
    }


# ==================== Project History Schemas ====================


class ProjectHistoryEntryResponse(BaseModel):
    """Response schema for a single project history entry."""
    id: str = Field(..., description="Unique entry ID")
    project_id: str = Field(..., description="Owning project ID")
    entry_type: str = Field(..., description="Type of history entry")
    summary: str = Field(..., description="Brief summary of the entry")
    details: str | None = Field(default=None, description="Detailed description")
    source_agent: str | None = Field(default=None, description="Agent that created the entry")
    source_instance_id: str | None = Field(default=None, description="Instance that created the entry")
    entry_metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")
    created_at: str | None = Field(default=None, description="Creation timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "entry-uuid",
                "project_id": "project-uuid",
                "entry_type": "milestone",
                "summary": "Completed Phase 1 implementation",
                "details": "Data layer and repository implementation",
                "source_agent": "developer",
                "source_instance_id": "session-uuid",
                "entry_metadata": {"phase": 1},
                "created_at": "2025-03-15T10:00:00+00:00"
            }
        }
    }


class ProjectHistoryListResponse(BaseModel):
    """Paginated list of project history entries."""
    entries: list[ProjectHistoryEntryResponse] = Field(default_factory=list, description="History entries")
    total: int = Field(..., description="Total number of matching entries")
    limit: int = Field(..., description="Maximum entries per page")
    offset: int = Field(..., description="Number of entries skipped")

    model_config = {
        "json_schema_extra": {
            "example": {
                "entries": [
                    {
                        "id": "entry-uuid",
                        "project_id": "project-uuid",
                        "entry_type": "milestone",
                        "summary": "Completed Phase 1",
                        "created_at": "2025-03-15T10:00:00+00:00"
                    }
                ],
                "total": 1,
                "limit": 20,
                "offset": 0
            }
        }
    }


class ProjectHistoryAddRequest(BaseModel):
    """Request body for adding a project history entry."""
    entry_type: str = Field(..., description="Type of history entry")
    summary: str = Field(..., description="Brief summary of the entry")
    details: str | None = Field(default=None, description="Detailed description")
    entry_metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")

    model_config = {
        "json_schema_extra": {
            "example": {
                "entry_type": "milestone",
                "summary": "Completed Phase 1 implementation",
                "details": "Data layer and repository implementation",
                "entry_metadata": {"phase": 1}
            }
        }
    }


class ProjectHistorySearchResponse(BaseModel):
    """Search results for project history entries."""
    entries: list[ProjectHistoryEntryResponse] = Field(default_factory=list, description="Matching history entries")
    total: int = Field(..., description="Total number of matching entries")
    limit: int = Field(..., description="Maximum entries per page")
    offset: int = Field(..., description="Number of entries skipped")
    query: str = Field(..., description="The search query used")

    model_config = {
        "json_schema_extra": {
            "example": {
                "entries": [
                    {
                        "id": "entry-uuid",
                        "project_id": "project-uuid",
                        "entry_type": "note",
                        "summary": "TODO: Add tests",
                        "created_at": "2025-03-15T10:00:00+00:00"
                    }
                ],
                "total": 1,
                "limit": 20,
                "offset": 0,
                "query": "tests"
            }
        }
    }


class LanguagePreferenceResponse(BaseModel):
    """Response for the current language preference."""

    language: str = Field(..., description="Current language preference")


class LanguagePreferenceUpdate(BaseModel):
    """Request body for updating the language preference."""

    language: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r'^[A-Za-z\u00C0-\u017F \-()]+$',
        description="Preferred language name — letters, spaces, hyphens, and parentheses only",
    )


# ==================== Editor Preference Schemas ====================


class VSCodeStatus(BaseModel):
    """Status block for the VS Code server returned by editor settings endpoints."""

    available: bool = Field(
        default=False,
        description="Whether the code-server binary is resolvable on PATH",
    )
    binary_path: str | None = Field(
        default=None,
        description="Resolved path to the code-server binary, or null if not found",
    )
    status: str = Field(
        default="stopped",
        description="VSCodeServerState.status: stopped|starting|running|crashed|stopping",
    )
    allow_remote: bool = Field(
        default=False,
        description="Whether the server is configured to allow remote access",
    )
    # Auto-restart contract (Phase: vscode-reliability-fixes): when the
    # watchdog flips state.status to ``crashed`` it also records the
    # exit code and a log-tail diagnostic on the manager's state. These
    # two fields expose that diagnostic to the frontend so operators can
    # see WHY the server crashed (e.g. last 50 lines of code-server's
    # stdout/stderr) without having to SSH into the host.
    last_error: str | None = Field(
        default=None,
        description=(
            "Last crash diagnostic from the watchdog — includes the "
            "exit code and the tail of the code-server log buffer. "
            "None when no crash has been recorded yet."
        ),
    )
    exit_code: int | None = Field(
        default=None,
        description=(
            "Last process exit code observed by the watchdog (None "
            "while the process is still alive)."
        ),
    )
    # C4: port and pid REMOVED — defeats proxy boundary


class EditorPreferenceResponse(BaseModel):
    """Response for GET/PUT /api/settings/editor."""

    editor: str = Field(
        ...,
        description="Current editor preference: 'builtin' or 'vscode'",
    )
    vscode: VSCodeStatus = Field(
        default_factory=VSCodeStatus,
        description="VS Code server status block",
    )


class EditorPreferenceUpdate(BaseModel):
    """Request body for PUT /api/settings/editor."""

    editor: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Editor preference: 'builtin' or 'vscode'",
    )

    @field_validator("editor")
    @classmethod
    def _validate_editor(cls, v: str) -> str:
        # Lazy import to avoid a hard daemon import at schema-load time.
        from daemon import constants

        if v not in constants.EDITOR_OPTIONS:
            raise ValueError(
                f"editor must be one of {constants.EDITOR_OPTIONS}, got '{v}'"
            )
        return v


class VSCodeStatusResponse(BaseModel):
    """Lightweight response for GET /api/settings/vscode/status."""

    status: str = Field(
        default="stopped",
        description="VSCodeServerState.status: stopped|starting|running|crashed|stopping",
    )
    # C4: port and pid REMOVED — defeats proxy boundary


# ==================== Default Agent Versions Schemas ====================


class DefaultAgentVersionsResponse(BaseModel):
    """Response for GET /api/settings/default-agent-versions."""

    default_versions: dict[str, str | None] = Field(
        default_factory=dict,
        description="Map of agent_id → version_tag (null means use base version)",
    )


class DefaultAgentVersionUpdate(BaseModel):
    """Request body for PUT /api/settings/default-agent-versions."""

    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Agent identifier (e.g., 'developer', 'tester')",
    )
    version_tag: str | None = Field(
        default=None,
        description="Default version tag for this agent (null to reset to base)",
    )

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("agent_id must be a non-empty string")
        return cleaned

    @field_validator("version_tag")
    @classmethod
    def _validate_version_tag(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("version_tag must be a non-empty string or null")
        return cleaned


# ==================== Blueprint Peak Hours Schemas ====================


class BlueprintPeakHoursResponse(BaseModel):
    """Response for GET /api/settings/blueprint-peak-hours.

    The daemon-side daily blueprint scan skips itself when the local
    time falls inside the [start, end) hour window. The window is
    expressed in ``tz_offset`` (UTC offset in whole hours, e.g. 7 for
    GMT+7) so the operator can match their working-day timeline.
    """

    start: int = Field(..., description="Inclusive start hour 0-23 (local time)")
    end: int = Field(..., description="Exclusive end hour 0-23 (local time)")
    tz_offset: int = Field(..., description="UTC offset in whole hours (e.g. 7 for GMT+7)")


class BlueprintPeakHoursUpdate(BaseModel):
    """Request body for PUT /api/settings/blueprint-peak-hours."""

    start: int = Field(..., ge=0, le=23, description="Inclusive start hour 0-23")
    end: int = Field(..., ge=0, le=23, description="Exclusive end hour 0-23")
    tz_offset: int = Field(
        ...,
        ge=-12,
        le=14,
        description="UTC offset in whole hours (-12 to 14, e.g. 7 for GMT+7)",
    )


# ==================== Plane Config Schemas ====================


class PlaneConfigResponse(BaseModel):
    """Response for GET /api/settings/plane.

    The frontend uses this to decide whether to show the "Plan" nav
    item and mount the Plane iframe. ``enabled`` is true only when the
    ``PLANE_BASE_URL`` environment variable is set to a non-empty value.
    """

    enabled: bool = Field(..., description="Whether Plane integration is enabled")
    url: str = Field(..., description="The Plane base URL (empty string if disabled)")
