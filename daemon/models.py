from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionStatus(str, Enum):
    """Status of a daemon session."""

    idle = "idle"
    running = "running"
    waiting = "waiting"
    error = "error"
    terminated = "terminated"


class ErrorCodes(str, Enum):
    """Error codes for API responses."""

    INVALID_REQUEST = "INVALID_REQUEST"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_TERMINATED = "SESSION_TERMINATED"
    RATE_LIMITED = "RATE_LIMITED"
    MAX_SESSIONS_EXCEEDED = "MAX_SESSIONS_EXCEEDED"
    LLM_ERROR = "LLM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


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
                "details": {"field": "agent_dir", "reason": "required field"}
            }
        }
    )


class SessionCreate(BaseModel):
    """Request for spawning a new session."""

    agent_dir: str = Field(..., description="Directory containing the agent implementation")
    session_id: str | None = Field(default=None, description="Optional session ID (auto-generated if omitted)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agent_dir": "/path/to/agent",
                "session_id": "session-123"
            }
        }
    )


class SessionInfo(BaseModel):
    """Response for session information."""

    session_id: str = Field(..., description="Unique session identifier")
    agent_dir: str = Field(..., description="Directory containing the agent implementation")
    status: SessionStatus = Field(..., description="Current session status")
    parent_id: str | None = Field(default=None, description="Parent session ID if this is a child session")
    children: list[str] = Field(default_factory=list, description="List of child session IDs")
    created_at: datetime = Field(..., description="Session creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "session-123",
                "agent_dir": "/path/to/agent",
                "status": "running",
                "parent_id": None,
                "children": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:01:00Z"
            }
        }
    )


class MessageCreate(BaseModel):
    """Request for sending a message to a session."""

    content: str = Field(..., description="Message content to send to the agent")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "Hello, agent!"
            }
        }
    )


class MessageResponse(BaseModel):
    """Response after sending a message."""

    message_id: str = Field(..., description="Unique message identifier")
    role: str = Field(..., description="Message role (always 'assistant')")
    content: str | None = Field(default=None, description="Message content")
    thinking: str | None = Field(default=None, description="Thinking/reasoning content from the agent")
    tool_calls: list[dict[str, Any]] | None = Field(default=None, description="Tool calls made by the agent")
    created_at: datetime = Field(..., description="Message creation timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message_id": "msg-456",
                "role": "assistant",
                "content": "Hello! How can I help you?",
                "thinking": None,
                "tool_calls": None,
                "created_at": "2024-01-01T00:00:00Z"
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


class SessionListResponse(BaseModel):
    """Response for listing sessions."""

    sessions: list[SessionInfo] = Field(..., description="List of active sessions")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sessions": [
                    {
                        "session_id": "session-123",
                        "agent_dir": "/path/to/agent",
                        "status": "running",
                        "parent_id": None,
                        "children": [],
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:01:00Z"
                    }
                ]
            }
        }
    )


class AgentInfo(BaseModel):
    """Information about an available agent."""

    id: str = Field(..., description="Unique agent identifier")
    name: str = Field(..., description="Display name of the agent")
    description: str = Field(..., description="Description of what the agent does")
    icon: str = Field(default="🤖", description="Emoji icon for the agent")
    color: str = Field(default="accent-blue", description="Color theme for the agent")
    version: str | None = Field(default=None, description="Agent version")
    agent_dir: str = Field(..., description="Path to the agent directory")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "coder",
                "name": "Coder",
                "description": "Specializes in code generation and debugging",
                "icon": "💻",
                "color": "accent-cyan",
                "version": "1.0.0",
                "agent_dir": "./agents/coder"
            }
        }
    )


class AgentListResponse(BaseModel):
    """Response for listing available agents."""

    agents: list[AgentInfo] = Field(..., description="List of available agents")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agents": [
                    {
                        "id": "coder",
                        "name": "Coder",
                        "description": "Specializes in code generation and debugging",
                        "icon": "💻",
                        "color": "accent-cyan",
                        "version": "1.0.0",
                        "agent_dir": "./agents/coder"
                    }
                ]
            }
        }
    )
