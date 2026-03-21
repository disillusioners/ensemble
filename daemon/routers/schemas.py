"""Pydantic schemas for Job Queue API."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class JobCreateRequest(BaseModel):
    """Request body for creating a new job."""
    
    agent_dir: str = Field(..., description="Path to the agent directory")
    message: str = Field(..., description="Job message/content")
    project_id: Optional[str] = Field(default=None, description="Optional project ID for job serialization")
    priority: int = Field(default=5, ge=1, le=10, description="Job priority (1-10, default 5)")
    source: str = Field(default="api", description="Source of the job")
    metadata: Optional[dict[str, Any]] = Field(default=None, description="Optional metadata dictionary")
    
    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        if not 1 <= v <= 10:
            raise ValueError("Priority must be between 1 and 10")
        return v
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "agent_dir": "/agents/coder",
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
    status: str = Field(..., description="Job status (pending, processing, completed, failed, cancelled)")
    priority: int = Field(..., description="Job priority (1-10)")
    agent_dir: str = Field(..., description="Path to the agent directory")
    project_id: Optional[str] = Field(default=None, description="Project ID if job is serialized")
    session_id: Optional[str] = Field(default=None, description="Session ID if job is processing/processed")
    created_at: str = Field(..., description="Job creation timestamp")
    started_at: Optional[str] = Field(default=None, description="Job start timestamp")
    completed_at: Optional[str] = Field(default=None, description="Job completion timestamp")
    result_summary: Optional[str] = Field(default=None, description="Summary of job result")
    error_message: Optional[str] = Field(default=None, description="Error message if job failed")
    position: Optional[int] = Field(default=None, description="Queue position if job is pending")
    message: Optional[str] = Field(default=None, description="Status message")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "job-uuid",
                "status": "completed",
                "priority": 7,
                "agent_dir": "/agents/coder",
                "project_id": "project-uuid",
                "session_id": "session-uuid",
                "created_at": "2025-03-15T10:00:00Z",
                "started_at": "2025-03-15T10:00:01Z",
                "completed_at": "2025-03-15T10:05:00Z",
                "result_summary": "Fixed login bug - added token refresh logic",
                "error_message": None,
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
                        "agent_dir": "/agents/coder",
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


# Backward compatibility aliases
TaskCreateRequest = JobCreateRequest
TaskResponse = JobResponse
TaskListResponse = JobListResponse
TaskValidationError = JobValidationError
TaskNotFoundResponse = JobNotFoundResponse
