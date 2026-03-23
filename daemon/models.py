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
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_ALREADY_EXISTS = "SOURCE_ALREADY_EXISTS"
    SOURCE_TYPE_NOT_SUPPORTED = "SOURCE_TYPE_NOT_SUPPORTED"
    MAPPING_NOT_FOUND = "MAPPING_NOT_FOUND"
    MAPPING_ALREADY_EXISTS = "MAPPING_ALREADY_EXISTS"


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
    title: str | None = Field(default=None, description="Auto-generated session title from first message")
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
                "title": "Help with Python debugging",
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
    thinking: str | None = Field(default=None, description="Thinking from metadata (reasoning_content, etc.)")
    thinking_extracted: str | None = Field(default=None, description="Thinking extracted from <think/> tags in content")
    tool_calls: list[dict[str, Any]] | None = Field(default=None, description="Tool calls made by the agent")
    created_at: datetime = Field(..., description="Message creation timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message_id": "msg-456",
                "role": "assistant",
                "content": "Hello! How can I help you?",
                "thinking": None,
                "thinking_extracted": None,
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
    total: int = Field(..., description="Total number of sessions available")
    limit: int = Field(..., description="Maximum number of sessions returned")
    offset: int = Field(..., description="Number of sessions skipped")
    has_more: bool = Field(..., description="Whether more sessions are available")

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
                ],
                "total": 150,
                "limit": 100,
                "offset": 0,
                "has_more": True
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
    system: bool = Field(default=False, description="Whether this is a system agent")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "coder",
                "name": "Coder",
                "description": "Specializes in code generation and debugging",
                "icon": "💻",
                "color": "accent-cyan",
                "version": "1.0.0",
                "agent_dir": "./agents/coder",
                "system": False
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


class AgentCreate(BaseModel):
    """Request for creating a new agent."""
    id: str = Field(..., description="Unique agent identifier (directory name)")
    name: str = Field(..., description="Display name of the agent")
    description: str = Field(default="", description="Description of what the agent does")
    icon: str = Field(default="🤖", description="Emoji icon for the agent")
    color: str = Field(default="accent-blue", description="Color theme for the agent")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "my-agent",
                "name": "My Agent",
                "description": "A custom agent",
                "icon": "🚀",
                "color": "accent-emerald"
            }
        }
    )


# ==================== Source Models ====================


class SourceStatus(str, Enum):
    """Status of a message source adapter."""

    stopped = "stopped"
    starting = "starting"
    running = "running"
    error = "error"


class SourceType(str, Enum):
    """Supported message source types."""

    telegram = "telegram"
    webhook = "webhook"
    whatsapp = "whatsapp"
    discord = "discord"
    scheduler = "scheduler"


class SourceCreate(BaseModel):
    """Request for creating a new message source."""

    source_id: str = Field(
        ..., 
        description="Unique source identifier (alphanumeric, hyphens, underscores only)", 
        min_length=1, 
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$"
    )
    source_type: SourceType = Field(..., description="Type of message source")
    name: str = Field(..., description="Display name for the source", min_length=1, max_length=128)
    config: dict[str, Any] = Field(default_factory=dict, description="Source-specific configuration")
    credentials: dict[str, Any] = Field(default_factory=dict, description="Credentials (bot tokens, API keys)")
    enabled: bool = Field(default=True, description="Whether the source is enabled")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_id": "telegram-main",
                "source_type": "telegram",
                "name": "Customer Support Bot",
                "config": {
                    "polling_enabled": True,
                    "polling_timeout": 30,
                    "default_agent": "coder"
                },
                "credentials": {
                    "bot_token": "123456:ABC-DEF"
                },
                "enabled": True
            }
        }
    )


class SourceUpdate(BaseModel):
    """Request for updating a message source."""

    name: str | None = Field(default=None, description="Display name for the source", min_length=1, max_length=128)
    config: dict[str, Any] | None = Field(default=None, description="Source-specific configuration")
    credentials: dict[str, Any] | None = Field(default=None, description="Credentials (bot tokens, API keys)")
    enabled: bool | None = Field(default=None, description="Whether the source is enabled")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Bot Name",
                "config": {"polling_enabled": False},
                "enabled": True
            }
        }
    )


class SourceInfo(BaseModel):
    """Response for source information."""

    source_id: str = Field(..., description="Unique source identifier")
    source_type: SourceType = Field(..., description="Type of message source")
    name: str = Field(..., description="Display name for the source")
    config: dict[str, Any] = Field(..., description="Source-specific configuration")
    enabled: bool = Field(..., description="Whether the source is enabled")
    status: SourceStatus = Field(..., description="Current adapter status")
    error_message: str | None = Field(default=None, description="Error message if status is 'error'")
    created_at: datetime = Field(..., description="Source creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_id": "telegram-main",
                "source_type": "telegram",
                "name": "Customer Support Bot",
                "config": {"polling_enabled": True, "default_agent": "coder"},
                "enabled": True,
                "status": "running",
                "error_message": None,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:01:00Z"
            }
        }
    )


class SourceListResponse(BaseModel):
    """Response for listing message sources."""

    sources: list[SourceInfo] = Field(..., description="List of configured sources")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sources": [
                    {
                        "source_id": "telegram-main",
                        "source_type": "telegram",
                        "name": "Customer Support Bot",
                        "config": {"polling_enabled": True},
                        "enabled": True,
                        "status": "running",
                        "error_message": None,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:01:00Z"
                    }
                ]
            }
        }
    )


class SourceTestRequest(BaseModel):
    """Request for testing a source configuration."""

    source_type: SourceType = Field(..., description="Type of message source")
    config: dict[str, Any] = Field(default_factory=dict, description="Source-specific configuration")
    credentials: dict[str, Any] = Field(default_factory=dict, description="Credentials to test")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_type": "telegram",
                "config": {"polling_enabled": True},
                "credentials": {"bot_token": "123456:ABC-DEF"}
            }
        }
    )


class SourceTestResponse(BaseModel):
    """Response for testing a source configuration."""

    success: bool = Field(..., description="Whether the connection test succeeded")
    message: str = Field(..., description="Human-readable result or error message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "message": "Connected to @my_bot (My Bot)"
            }
        }
    )


class ScheduleExecutionInfo(BaseModel):
    """Response for schedule execution information."""

    execution_id: str = Field(..., description="Unique execution identifier")
    schedule_id: str = Field(..., description="Schedule that triggered this execution")
    triggered_at: datetime = Field(..., description="When the execution was triggered")
    session_id: str | None = Field(default=None, description="Session that was triggered")
    status: str = Field(..., description="Execution status (triggered, completed, failed)")
    error_message: str | None = Field(default=None, description="Error message if failed")
    completed_at: datetime | None = Field(default=None, description="When execution completed")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "execution_id": "exec-123",
                "schedule_id": "morning-briefing",
                "triggered_at": "2024-01-01T08:00:00Z",
                "session_id": "session-456",
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
    status: SourceStatus | None = Field(default=None, description="Schedule status (running, stopped, paused)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Schedule Name",
                "config": {"interval_seconds": 600},
                "status": "running"
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


class SourceActionResponse(BaseModel):
    """Response for source actions (start/stop)."""

    source_id: str = Field(..., description="Source identifier")
    status: SourceStatus = Field(..., description="Current status after action")
    message: str = Field(..., description="Status message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_id": "telegram-main",
                "status": "running",
                "message": "Source started successfully"
            }
        }
    )


# ==================== Session Mapping Models ====================


class SessionMappingCreate(BaseModel):
    """Request for creating a session mapping."""

    external_user_id: str = Field(..., description="External user ID (e.g., Telegram chat_id)", min_length=1, max_length=256)
    agent_dir: str = Field(..., description="Agent directory to use for this user")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "external_user_id": "123456789",
                "agent_dir": "./agents/coder",
                "metadata": {"username": "john_doe"}
            }
        }
    )


class SessionMappingInfo(BaseModel):
    """Response for session mapping information."""

    mapping_id: str = Field(..., description="Unique mapping identifier")
    source_id: str = Field(..., description="Source this mapping belongs to")
    external_user_id: str = Field(..., description="External user ID")
    agent_session_id: str = Field(..., description="Agent session handling this user")
    agent_dir: str = Field(..., description="Agent directory used")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")
    last_message_at: datetime | None = Field(default=None, description="Last message timestamp")
    created_at: datetime = Field(..., description="Mapping creation timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "mapping_id": "telegram-main:123456789",
                "source_id": "telegram-main",
                "external_user_id": "123456789",
                "agent_session_id": "session-abc",
                "agent_dir": "./agents/coder",
                "metadata": {"username": "john_doe"},
                "last_message_at": "2024-01-01T00:01:00Z",
                "created_at": "2024-01-01T00:00:00Z"
            }
        }
    )


class SessionMappingListResponse(BaseModel):
    """Response for listing session mappings."""

    mappings: list[SessionMappingInfo] = Field(..., description="List of session mappings")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "mappings": [
                    {
                        "mapping_id": "telegram-main:123456789",
                        "source_id": "telegram-main",
                        "external_user_id": "123456789",
                        "agent_session_id": "session-abc",
                        "agent_dir": "./agents/coder",
                        "metadata": None,
                        "last_message_at": "2024-01-01T00:01:00Z",
                        "created_at": "2024-01-01T00:00:00Z"
                    }
                ]
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
