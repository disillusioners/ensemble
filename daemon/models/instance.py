from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Re-export the canonical InstanceStatus enum from the repositories layer.
# This avoids a duplicate definition and ensures a single source of truth
# (see Phase 5 of the CorrelationManager migration).
from daemon.repositories.instance.models import InstanceStatus  # re-export for backward compat


class InstanceCreate(BaseModel):
    """Request for spawning a new instance."""

    agent_id: str = Field(..., description="Agent ID (e.g., 'developer')")
    instance_id: str | None = Field(default=None, description="Optional instance ID")
    project_id: str | None = Field(default=None, description="Optional project ID for associating instance with a project")
    version_tag: str | None = Field(default=None, description="Optional agent version tag (None = base). Selects a tagged variant of the agent.")

    @model_validator(mode='after')
    def validate_agent(self):
        if not self.agent_id:
            raise ValueError('agent_id is required')
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agent_id": "developer",
                "instance_id": "instance-123"
            }
        }
    )


class InstanceInfo(BaseModel):
    """Response for instance information."""

    instance_id: str = Field(..., description="Unique instance identifier")
    agent_id: str | None = Field(default=None, description="Agent ID (e.g., 'developer')")
    project_id: str | None = Field(default=None, description="Optional project ID for associating instance with a project")
    agent_dir: str = Field(..., description="Path to the agent directory (derived from agent_id)")
    status: InstanceStatus = Field(..., description="Current instance status")
    title: str | None = Field(default=None, description="Auto-generated instance title from first message")
    initiative_message: str | None = Field(
        default=None,
        description=(
            "The first real user message sent to the instance (captured on IDLE -> RUNNING "
            "transition). Provides search-friendly recall of what the user originally asked, "
            "independent of the auto-generated title."
        ),
    )
    parent_id: str | None = Field(default=None, description="Parent instance ID if this is a child instance")
    children: list[str] | None = Field(
        default=None,
        description=(
            "API response field populated from the permanent parent_id record "
            "via list_child_ids_permanent() — NOT a DB column on the instances "
            "table. Includes completed / terminated children so they remain "
            "nested under their parent in the instance tree UI (the "
            "instance_hierarchy working set deletes rows on completion, which "
            "would orphan finished children)."
        ),
    )
    mcp_tool_names: list[str] | None = Field(default=None, description="List of MCP tool names available to this instance")
    created_at: datetime = Field(..., description="Instance creation timestamp")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")
    pending_count: int | None = Field(default=None, description="Count of incomplete message jobs (READY + PROCESSING + RETRYING)")
    pinned: bool | None = Field(default=None, description="Whether this instance is pinned in the UI (UI-only preference)")
    color_tag: str | None = Field(default=None, description="UI color tag (e.g., 'red', 'blue', '#ff0000') for this instance (UI-only preference)")
    icon_tag: str | None = Field(default=None, description="UI icon tag for this instance (UI-only preference)")
    pinned_at: datetime | None = Field(default=None, description="When this instance was pinned (UI-only preference)")
    agent_tag: str | None = Field(default=None, description="Agent version tag (None = base). Selects a tagged variant of the agent.")
    model: str | None = Field(
        default=None,
        description=(
            "The LLM model the instance is using. Populated from "
            "``instance_metadata.model_override`` for load-balanced and "
            "override-sourced instances. ``None`` for instances that "
            "fall through to ``metadata.llm_model`` or the global default "
            "(the API caller can resolve those by inspecting the agent's "
            "meta.json or the daemon config)."
        ),
    )
    watchover_enabled: bool = Field(
        default=False,
        description=(
            "Whether watchover (security monitoring) is active for this "
            "instance. Sourced from instance_metadata.watchover_enabled."
        ),
    )
    watchover_context: str | None = Field(
        default=None,
        description=(
            "The watchover context string (compaction summary + requirement) "
            "used by the watcher agent. Sourced from "
            "instance_metadata.watchover_context. Null when watchover is off."
        ),
    )
    watchover_denial_count: int = Field(
        default=0,
        description=(
            "Current watchover denial count for this turn. Reset to 0 at "
            "the start of each turn. The list/get endpoints always return "
            "0 (graph state is not fetched per-instance for performance); "
            "the frontend tracks the real-time count via SSE denial "
            "events. This field is a best-effort default."
        ),
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "instance_id": "instance-123",
                "agent_id": "developer",
                "agent_dir": "./agents/developer",
                "status": "running",
                "title": "Help with Python debugging",
                "parent_id": None,
                "mcp_tool_names": ["webfetch", "context7_fetch_docs"],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:01:00Z",
                "watchover_enabled": False,
                "watchover_context": None,
                "watchover_denial_count": 0,
            }
        }
    )


class InstanceListResponse(BaseModel):
    """Response for listing instances."""

    instances: list[InstanceInfo] = Field(..., description="List of active instances")
    total: int = Field(..., description="Total number of instances available")
    limit: int = Field(..., description="Maximum number of instances returned")
    offset: int = Field(..., description="Number of instances skipped")
    has_more: bool = Field(..., description="Whether more instances are available")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "instances": [
                    {
                        "instance_id": "instance-123",
                        "agent_dir": "/path/to/agent",
                        "status": "running",
                        "parent_id": None,
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


class ResumeRequest(BaseModel):
    """Request body for resuming an instance with optional message."""

    message: str | None = Field(
        default=None,
        description="Optional message to send when resuming (defaults to 'resume')"
    )


__all__ = ["InstanceStatus", "InstanceCreate", "InstanceInfo", "InstanceListResponse", "ResumeRequest"]
