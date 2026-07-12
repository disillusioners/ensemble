"""Pydantic schemas for Router APIs."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ==================== Job Queue Schemas ====================


class JobCreateRequest(BaseModel):
    """Request body for creating a new job."""
    
    agent_id: str = Field(..., description="Agent ID (e.g., 'developer')")
    message: str = Field(..., description="Job message/content")
    project_id: str | None = Field(default=None, description="Optional project ID for job serialization")
    queue_id: str | None = Field(default=None, description="Optional queue ID to assign job to a specific queue")
    priority: int = Field(default=5, ge=1, le=10, description="Job priority (1-10, default 5)")
    source: str = Field(default="api", description="Source of the job")
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
                "position": None,
                "message": "Job completed successfully"
            }
        }
    }


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
                "pending_jobs": 5
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
    queue_type: str = Field(default="fifo", description="Queue type: 'fifo', 'parallel', or 'defer'")
    concurrency_limit: int = Field(default=1, ge=1, le=20, description="Max concurrent jobs")
    description: str | None = Field(default=None, max_length=500, description="Queue description")
    
    @field_validator("queue_type")
    @classmethod
    def validate_queue_type(cls, v: str) -> str:
        if v not in ("fifo", "parallel", "defer"):
            raise ValueError("queue_type must be 'fifo', 'parallel', or 'defer'")
        return v
    
    @field_validator("queue_name")
    @classmethod
    def validate_queue_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Queue name cannot be empty or whitespace-only")
        reserved = ("system_fifo_queue", "system_parallel_queue", "system_kb_fifo_queue", "system_defer_queue")
        if v.lower() in reserved:
            raise ValueError(f"'{v}' is a reserved queue name")
        return v
    
    @model_validator(mode="after")
    def validate_queue_concurrency(self) -> "JobQueueCreateRequest":
        if self.queue_type == "fifo" and self.concurrency_limit != 1:
            raise ValueError("FIFO queues must have concurrency_limit=1")
        if self.queue_type == "defer" and self.concurrency_limit != 1:
            raise ValueError("Defer queues must have concurrency_limit=1")
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
    queue_type: str | None = Field(default=None, description="Queue type: 'fifo', 'parallel', or 'defer'")
    concurrency_limit: int | None = Field(default=None, ge=1, le=20, description="New concurrency limit")
    is_paused: bool | None = Field(default=None, description="Pause/unpause the queue")
    description: str | None = Field(default=None, max_length=500, description="New description")
    
    @field_validator("queue_type")
    @classmethod
    def validate_queue_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ("fifo", "parallel", "defer"):
            raise ValueError("queue_type must be 'fifo', 'parallel', or 'defer'")
        return v
    
    @field_validator("queue_name")
    @classmethod
    def validate_queue_name(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Queue name cannot be empty or whitespace-only")
            reserved = ("system_fifo_queue", "system_parallel_queue", "system_kb_fifo_queue", "system_defer_queue")
            if v.lower() in reserved:
                raise ValueError(f"'{v}' is a reserved queue name")
        return v
    
    @model_validator(mode="after")
    def validate_queue_concurrency(self) -> "JobQueueUpdateRequest":
        # Only validate when BOTH queue_type AND concurrency_limit are explicitly provided
        if self.queue_type is not None and self.concurrency_limit is not None:
            if self.queue_type == "fifo" and self.concurrency_limit != 1:
                raise ValueError("FIFO queues must have concurrency_limit=1")
            if self.queue_type == "defer" and self.concurrency_limit != 1:
                raise ValueError("Defer queues must have concurrency_limit=1")
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
                "created_queues": ["system_kb_fifo_queue", "system_defer_queue"],
                "total_system_queues": 4
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
