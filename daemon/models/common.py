from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCodes(str, Enum):
    """Error codes for API responses."""

    INVALID_REQUEST = "INVALID_REQUEST"
    INSTANCE_NOT_FOUND = "INSTANCE_NOT_FOUND"
    INSTANCE_TERMINATED = "INSTANCE_TERMINATED"
    RATE_LIMITED = "RATE_LIMITED"
    MAX_INSTANCES_EXCEEDED = "MAX_INSTANCES_EXCEEDED"
    LLM_ERROR = "LLM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_ALREADY_EXISTS = "SOURCE_ALREADY_EXISTS"
    SOURCE_TYPE_NOT_SUPPORTED = "SOURCE_TYPE_NOT_SUPPORTED"
    SCHEDULER_ENABLE_NOT_ALLOWED = "SCHEDULER_ENABLE_NOT_ALLOWED"
    SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED = "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED"
    MAPPING_NOT_FOUND = "MAPPING_NOT_FOUND"
    MAPPING_ALREADY_EXISTS = "MAPPING_ALREADY_EXISTS"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class ErrorResponse(BaseModel):
    """Error response schema."""

    code: ErrorCodes = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    details: dict[str, Any] | None = Field(default=None, description="Additional error details")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "INVALID_REQUEST",
                "message": "The request body is invalid",
                "details": {"field": "agent_id", "reason": "required field"}
            }
        }
    )


class DeleteResponse(BaseModel):
    """Generic delete response."""

    deleted: bool = Field(..., description="Whether the resource was deleted")
    message: str = Field(..., description="Status message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "deleted": True,
                "message": "Resource deleted successfully"
            }
        }
    )


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Service status (always 'healthy')")
    uptime_seconds: float = Field(..., description="Service uptime in seconds")
    version: str = Field(..., description="Service version")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "uptime_seconds": 3600.0,
                "version": "1.0.0"
            }
        }
    )


__all__ = ["ErrorCodes", "ErrorResponse", "DeleteResponse", "HealthResponse"]
