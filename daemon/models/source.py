from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    slack = "slack"


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
    autostart: bool = Field(default=True, description="Whether to auto-start the source when the service starts (delayed by 1 minute)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_id": "telegram-main",
                "source_type": "telegram",
                "name": "Customer Support Bot",
                "config": {
                    "polling_enabled": True,
                    "polling_timeout": 30,
                    "default_agent": "developer"
                },
                "credentials": {
                    "bot_token": "123456:ABC-DEF"
                },
                "enabled": True,
                "autostart": True
            }
        }
    )


class SourceUpdate(BaseModel):
    """Request for updating a message source."""

    name: str | None = Field(default=None, description="Display name for the source", min_length=1, max_length=128)
    config: dict[str, Any] | None = Field(default=None, description="Source-specific configuration")
    credentials: dict[str, Any] | None = Field(default=None, description="Credentials (bot tokens, API keys)")
    enabled: bool | None = Field(default=None, description="Whether the source is enabled")
    autostart: bool | None = Field(default=None, description="Whether to auto-start the source when the service starts (delayed by 1 minute)")

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
    autostart: bool = Field(..., description="Whether to auto-start the source when the service starts (delayed by 1 minute)")
    status: SourceStatus = Field(..., description="Current adapter status")
    error_message: str | None = Field(default=None, description="Error message if status is 'error'")
    created_at: datetime = Field(..., description="Source creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")
    has_credentials: bool = Field(default=False, description="Whether this source has credentials configured")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source_id": "telegram-main",
                "source_type": "telegram",
                "name": "Customer Support Bot",
                "config": {"polling_enabled": True, "default_agent": "developer"},
                "enabled": True,
                "autostart": True,
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


__all__ = [
    "SourceStatus",
    "SourceType",
    "SourceCreate",
    "SourceUpdate",
    "SourceInfo",
    "SourceListResponse",
    "SourceTestRequest",
    "SourceTestResponse",
    "SourceActionResponse",
]
