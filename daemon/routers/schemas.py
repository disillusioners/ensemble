"""Pydantic schemas for Task Queue API."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class TaskCreateRequest(BaseModel):
    """Request body for creating a new task."""
    
    agent_dir: str = Field(..., description="Path to the agent directory")
    message: str = Field(..., description="Task message/content")
    project_id: Optional[str] = Field(default=None, description="Optional project ID for task serialization")
    priority: int = Field(default=5, ge=1, le=10, description="Task priority (1-10, default 5)")
    source: str = Field(default="api", description="Source of the task")
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


class TaskResponse(BaseModel):
    """Response for a single task."""
    
    task_id: str = Field(..., description="Unique task identifier")
    status: str = Field(..., description="Task status (pending, processing, completed, failed, cancelled)")
    priority: int = Field(..., description="Task priority (1-10)")
    agent_dir: str = Field(..., description="Path to the agent directory")
    project_id: Optional[str] = Field(default=None, description="Project ID if task is serialized")
    session_id: Optional[str] = Field(default=None, description="Session ID if task is processing/processed")
    created_at: str = Field(..., description="Task creation timestamp")
    started_at: Optional[str] = Field(default=None, description="Task start timestamp")
    completed_at: Optional[str] = Field(default=None, description="Task completion timestamp")
    result_summary: Optional[str] = Field(default=None, description="Summary of task result")
    error_message: Optional[str] = Field(default=None, description="Error message if task failed")
    position: Optional[int] = Field(default=None, description="Queue position if task is pending")
    message: Optional[str] = Field(default=None, description="Status message")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": "task-uuid",
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
                "message": "Task completed successfully"
            }
        }
    }


class TaskListResponse(BaseModel):
    """Response for listing tasks."""
    
    tasks: list[TaskResponse] = Field(default_factory=list, description="List of tasks")
    total: int = Field(..., description="Total number of tasks matching the query")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "tasks": [
                    {
                        "task_id": "task-uuid-1",
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


class TaskValidationError(BaseModel):
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


class TaskNotFoundResponse(BaseModel):
    """Not found error response."""
    
    error: str = Field(default="Task not found", description="Error type")
    task_id: str = Field(..., description="The task ID that was not found")
    
    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "Task not found",
                "task_id": "invalid-uuid"
            }
        }
    }
