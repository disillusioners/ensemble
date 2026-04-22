"""Schedule models for the daemon API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


__all__ = [
    "SchedulerInstanceMode",
    "ScheduleExecutionInfo",
    "ScheduleExecutionListResponse",
    "ScheduleTriggerResponse",
    "ScheduleInfo",
    "ScheduleUpdate",
    "ScheduleListResponse",
]
