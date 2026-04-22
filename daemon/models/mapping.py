from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InstanceMappingCreate(BaseModel):
    """Request for creating an instance mapping."""

    external_user_id: str = Field(..., description="External user ID (e.g., Telegram chat_id)", min_length=1, max_length=256)
    agent_id: str = Field(..., description="Agent ID (e.g., 'coder')")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "external_user_id": "123456789",
                "agent_id": "coder",
                "metadata": {"username": "john_doe"}
            }
        }
    )


class InstanceMappingInfo(BaseModel):
    """Response for instance mapping information."""

    mapping_id: str = Field(..., description="Unique mapping identifier")
    source_id: str = Field(..., description="Source this mapping belongs to")
    external_user_id: str = Field(..., description="External user ID")
    agent_instance_id: str = Field(..., description="Agent instance handling this user")
    agent_id: str = Field(..., description="Agent ID (e.g., 'coder')")
    agent_dir: str = Field(..., description="Path to the agent directory")
    metadata: dict[str, Any] | None = Field(default=None, description="Additional metadata")
    last_message_at: datetime | None = Field(default=None, description="Last message timestamp")
    created_at: datetime = Field(..., description="Mapping creation timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "mapping_id": "telegram-main:123456789",
                "source_id": "telegram-main",
                "external_user_id": "123456789",
                "agent_instance_id": "instance-abc",
                "agent_id": "coder",
                "agent_dir": "./agents/coder",
                "metadata": {"username": "john_doe"},
                "last_message_at": "2024-01-01T00:01:00Z",
                "created_at": "2024-01-01T00:00:00Z"
            }
        }
    )


class InstanceMappingListResponse(BaseModel):
    """Response for listing instance mappings."""

    mappings: list[InstanceMappingInfo] = Field(..., description="List of instance mappings")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "mappings": [
                    {
                        "mapping_id": "telegram-main:123456789",
                        "source_id": "telegram-main",
                        "external_user_id": "123456789",
                        "agent_instance_id": "instance-abc",
                        "agent_dir": "./agents/coder",
                        "metadata": None,
                        "last_message_at": "2024-01-01T00:01:00Z",
                        "created_at": "2024-01-01T00:00:00Z"
                    }
                ]
            }
        }
    )


__all__ = ["InstanceMappingCreate", "InstanceMappingInfo", "InstanceMappingListResponse"]
