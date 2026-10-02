"""Schedule models for the daemon API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from daemon.models.source import SourceStatus


class SchedulerInstanceMode(str, Enum):
    """Instance mode for scheduler executions."""

    NEW_INSTANCE = "new_instance"
    REUSE_INSTANCE = "reuse_instance"


class ScheduleExecutionInfo(BaseModel):
    """Response for schedule execution information."""

    execution_id: str = Field(..., description="Unique execution identifier")
    schedule_id: str = Field(..., description="Schedule that triggered this execution")
    triggered_at: datetime = Field(..., description="When the execution was triggered")
    instance_id: str | None = Field(default=None, description="Instance that was triggered")
    status: str = Field(..., description="Execution status (triggered, completed, failed)")
    error_message: str | None = Field(default=None, description="Error message if failed")
    completed_at: datetime | None = Field(default=None, description="When execution completed")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "execution_id": "exec-123",
                "schedule_id": "morning-briefing",
                "triggered_at": "2024-01-01T08:00:00Z",
                "instance_id": "instance-456",
                "status": "completed",
                "error_message": None,
                "completed_at": "2024-01-01T08:00:05Z"
            }
        }
    )


class ScheduleExecutionListResponse(BaseModel):
    """Response for listing schedule executions."""

    executions: list[ScheduleExecutionInfo] = Field(..., description="List of executions")
    total: int = Field(..., description="Total number of executions")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "executions": [],
                "total": 0
            }
        }
    )


class ScheduleTriggerResponse(BaseModel):
    """Response for manually triggering a schedule."""

    execution_id: str = Field(..., description="ID of the triggered execution")
    schedule_id: str = Field(..., description="Schedule that was triggered")
    message: str = Field(..., description="Status message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "execution_id": "exec-789",
                "schedule_id": "morning-briefing",
                "message": "Schedule triggered successfully"
            }
        }
    )


class ScheduleInfo(BaseModel):
    """Response for schedule information (matches frontend Schedule interface)."""

    id: str = Field(..., description="Unique schedule identifier (maps to source_id)")
    name: str = Field(..., description="Display name for the schedule")
    config: dict[str, Any] = Field(..., description="Schedule configuration")
    status: SourceStatus = Field(..., description="Current schedule status")
    created_at: datetime = Field(..., description="Schedule creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")
    last_run_at: datetime | None = Field(default=None, description="Last execution timestamp")
    next_run_at: datetime | None = Field(default=None, description="Next scheduled execution")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "scheduler-123",
                "name": "Morning Briefing",
                "config": {
                    "type": "cron",
                    "schedule": "0 9 * * *",
                    "agent": "./agents/leader",
                    "message": "Daily briefing",
                    "timezone": "UTC"
                },
                "status": "running",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T09:00:00Z",
                "last_run_at": "2024-01-01T09:00:00Z",
                "next_run_at": "2024-01-02T09:00:00Z"
            }
        }
    )


class ScheduleUpdate(BaseModel):
    """Request for updating a schedule."""

    name: str | None = Field(default=None, description="Display name for the schedule", min_length=1, max_length=128)
    config: dict[str, Any] | None = Field(default=None, description="Schedule configuration (partial updates)")
    instance_mode: str | None = Field(
        default=None,
        description="Instance mode: 'new_instance' (default) creates new instance per execution, 'reuse_instance' reuses existing instance. Note: For one_time schedules, instance_mode is always forced to 'new_instance'."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Schedule Name",
                "config": {"interval_seconds": 600},
                "instance_mode": "new_instance"
            }
        }
    )


class ScheduleListResponse(BaseModel):
    """Response for listing schedules (matches frontend ScheduleListResponse)."""

    schedules: list[ScheduleInfo] = Field(..., description="List of configured schedules")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "schedules": [
                    {
                        "id": "scheduler-123",
                        "name": "Morning Briefing",
                        "config": {"type": "cron", "schedule": "0 9 * * *", "agent": "./agents/leader"},
                        "status": "running",
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T09:00:00Z"
                    }
                ]
            }
        }
    )


# ==================== Phase 3: REST surface ====================
# Canonical create/response models live in daemon.services.scheduling_service
# (ScheduleCreatePayload / ScheduleCreateResponse / ScheduleDetail /
# ScheduleCancelResponse). The models below are the THIN REST surface only —
# they never redefine a canonical model; routers map REST ↔ canonical via
# _schedule_create_to_payload (daemon/routers/schedules.py).


class ScheduleCreate(BaseModel):
    """REST request body for POST /schedules.

    REST-conventional field names (`source_id` / `agent_id` /
    `local_time` + `run_at`) differ from the canonical
    ``ScheduleCreatePayload`` (`label` / `agent` / `when`) — the router
    maps at its boundary (ADR-013 F1 rewrite). NO `enabled` / `autostart`
    fields: schedules are always created enabled+autostart; lifecycle
    flows through update / pause / cancel (POST /schedules/{id}/stop|start,
    DELETE /schedules/{id}).
    """

    source_id: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9_-]+$",
        min_length=1,
        max_length=64,
        description="URL-stable id; used as label for create (uniqueness)",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Human-readable display name; defaults to source_id",
    )
    agent_id: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    priority: int = Field(default=5, ge=1, le=10)
    instance_mode: Literal["new_instance", "reuse_instance"] = "new_instance"
    recurrence: Literal["once", "daily", "weekly", "cron"]
    local_time: str | None = Field(
        default=None,
        description="HH:MM for daily/weekly; full ISO for once (alternative to run_at)",
    )
    timezone: str | None = Field(
        default=None,
        description="IANA name; service resolves default chain",
    )
    cron_expression: str | None = Field(default=None)
    weekday: int | None = Field(
        default=None,
        ge=0,
        le=6,
        description="0=Sun..6=Sat (matches phase-2 / cron DOW)",
    )
    run_at: datetime | None = Field(
        default=None,
        description="tz-aware preferred; naive → treated as local per resolved tz",
    )

    @model_validator(mode="after")
    def _validate_recurrence_requirements(self):
        if self.recurrence == "cron" and not self.cron_expression:
            raise ValueError("cron_expression required when recurrence == 'cron'")
        if self.recurrence == "weekly" and self.weekday is None:
            raise ValueError("weekday required when recurrence == 'weekly'")
        if self.recurrence == "once" and not (self.run_at or self.local_time):
            raise ValueError("either run_at or local_time required when recurrence == 'once'")
        if self.recurrence in {"daily", "weekly"} and not self.local_time:
            raise ValueError("local_time required when recurrence in {'daily', 'weekly'}")
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_id": "morning-briefing",
                "name": "Morning Briefing",
                "agent_id": "ari",
                "message": "Give me a morning briefing",
                "project_id": "default",
                "recurrence": "daily",
                "local_time": "06:00",
                "timezone": "America/New_York",
            }
        }
    )


class ScheduleCreateRestResponse(BaseModel):
    """Wraps phase-2 ScheduleCreateResponse; adds REST-conventional field names."""

    id: str  # = phase-2 source_id
    source_id: str
    label: str
    status: str
    next_run_at_local: str | None
    next_run_at_utc: str | None
    tz_warning: str = ""


class ScheduleDetailRestResponse(BaseModel):
    """Wraps phase-2 ScheduleDetail; adds 'id' field for REST URL consistency."""

    id: str  # = source_id (= label for REST creates)
    source_id: str
    label: str
    status: str
    recurrence: str | None
    local_time: str | None
    timezone: str | None
    cron_expression: str | None
    weekday: int | None
    next_run_at_local: str | None
    next_run_at_utc: str | None
    last_run_at: str | None
    agent_id: str | None  # = phase-2 agent
    project_id: str | None
    instance_mode: str | None
    cancelled_at: str | None
    tz_warning: str = ""


class ScheduleCancelRestResponse(BaseModel):
    """Wraps phase-2 ScheduleCancelResponse; adds last_execution_id echo (§5.3) + message."""

    id: str
    source_id: str
    status: str
    cancelled_at: str
    last_execution_id: str | None  # §5.3 echo — cancel the in-flight JobItem with this
    message: str  # e.g. "Schedule {id} cancelled (history retained)"


__all__ = [
    "SchedulerInstanceMode",
    "ScheduleExecutionInfo",
    "ScheduleExecutionListResponse",
    "ScheduleTriggerResponse",
    "ScheduleInfo",
    "ScheduleUpdate",
    "ScheduleListResponse",
    "ScheduleCreate",
    "ScheduleCreateRestResponse",
    "ScheduleDetailRestResponse",
    "ScheduleCancelRestResponse",
]
