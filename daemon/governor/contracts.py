# Contract 1: spawn_councilor tool signature
from pydantic import BaseModel, Field
from typing import Literal


class SpawnCouncilorInput(BaseModel):
    """Contract for spawn_councilor tool input."""
    councilor_agent_id: str = Field(..., min_length=1, description="Agent ID of the councilor (e.g., 'developer')")
    model: str = Field(..., min_length=1, description="REQUIRED LLM model to use for this councilor")
    instance_name: str | None = Field(None, description="Optional short name for the instance")
    initial_message: str = Field(..., min_length=1, description="The request/message to forward to this councilor")
    version_tag: str | None = Field(None, description="Optional agent version tag")


# Contract 2: spawn_councilor return type
class SpawnCouncilorResult(BaseModel):
    instance_id: str
    councilor_agent_id: str
    model: str
    canonical_model: str = Field(..., description="Canonical form of the model name (after dedup)")
    status: Literal["SPAWNED", "FAILED"]


# Contract 3: clear_councilor_errors tool signature
class ClearCouncilorErrorsResult(BaseModel):
    cleared: bool
    previous_error: str | None


# Contract 4: append_allowed_models appender signature
class AllowedModelsBlock(BaseModel):
    """The injected <allowed_models> block."""
    models: list[str]
    mode: Literal["restricted", "unrestricted"]
    status: Literal["ok", "error"]
    error_message: str | None = None