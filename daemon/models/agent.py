from pydantic import BaseModel, ConfigDict, Field


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


__all__ = ["AgentInfo", "AgentListResponse", "AgentCreate"]
