from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCodes(str, Enum):
    """Error codes for API responses."""

    INVALID_REQUEST = "INVALID_REQUEST"
    INSTANCE_NOT_FOUND = "INSTANCE_NOT_FOUND"
    INSTANCE_TERMINATED = "INSTANCE_TERMINATED"
    RATE_LIMITED = "RATE_LIMITED"
    LLM_ERROR = "LLM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SOURCE_ALREADY_EXISTS = "SOURCE_ALREADY_EXISTS"
    MCP_SERVER_NOT_FOUND = "MCP_SERVER_NOT_FOUND"
    MCP_SERVER_ALREADY_EXISTS = "MCP_SERVER_ALREADY_EXISTS"
    SOURCE_TYPE_NOT_SUPPORTED = "SOURCE_TYPE_NOT_SUPPORTED"
    SCHEDULER_ENABLE_NOT_ALLOWED = "SCHEDULER_ENABLE_NOT_ALLOWED"
    SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED = "SCHEDULER_SOURCE_UPDATE_NOT_ALLOWED"
    MAPPING_NOT_FOUND = "MAPPING_NOT_FOUND"
    MAPPING_ALREADY_EXISTS = "MAPPING_ALREADY_EXISTS"
    TODO_NOT_FOUND = "TODO_NOT_FOUND"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    BUILTIN_SERVER_PROTECTED = "BUILTIN_SERVER_PROTECTED"
    # Phase 1 / WS-1.5 — slash-command subsystem. Additive on top of
    # the existing {code, message} envelope (mirrors O13, 2026-08-31);
    # FE toasts on ``code`` and ``details.available`` later feeds
    # slash autocomplete without a contract change.
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"


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
    current_database: str | None = Field(
        default=None,
        description="Active database backend ('sqlite' or 'postgres'). None until lifespan wires up ensemble_config.",
    )
    postgres_env_available: bool | None = Field(
        default=None,
        description="Whether POSTGRES_HOST and POSTGRES_DB env vars are set. None until lifespan wires up ensemble_config.",
    )
    migration_available: bool | None = Field(
        default=None,
        description="Whether a SQLite→PostgreSQL migration can currently start. None until the migration worker is wired up in lifespan.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "uptime_seconds": 3600.0,
                "version": "1.0.0",
                "current_database": "sqlite",
                "postgres_env_available": False,
                "migration_available": False,
            }
        }
    )


class LivezResponse(BaseModel):
    """Liveness probe response (GET /livez).

    Mounted at the app root (NOT under /api) so supervisor/launcher
    probes hit http://host:PORT/livez directly. Pure event-loop
    answer: if the handler runs, the process is alive. No database
    access, no component checks — liveness never depends on
    infrastructure (ADR-002/003: restart on liveness failure only,
    never on readiness failure).
    """

    status: str = Field(..., description="Always 'alive' while the event loop answers")
    uptime_seconds: float = Field(..., description="Process uptime in seconds")
    version: str = Field(..., description="Daemon version")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "alive",
                "uptime_seconds": 3600.0,
                "version": "1.0.0",
            }
        }
    )


class ReadyzResponse(BaseModel):
    """Readiness probe response (GET /readyz).

    Served from a background-refreshed cached composite — the handler
    performs zero database access per request (ADR-003). HTTP 200 when
    every component is up, 503 + Retry-After when any component is
    degraded. ``draining`` is a reserved Phase-4 field (drain
    controller); it is always false in Phase 1.
    """

    status: str = Field(..., description="'ready' when all components pass, 'degraded' otherwise")
    components: dict[str, bool] = Field(
        ...,
        description="Per-component booleans: database, queue_freshness, services.",
    )
    detail: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Diagnostics: reasons (list of degraded reasons), "
            "queue_max_age_seconds (None when no RUNNING tasks), "
            "checked_at (ISO timestamp of the last composite refresh)."
        ),
    )
    draining: bool = Field(
        default=False,
        description="Reserved Phase-4 drain-controller flag. Always false in Phase 1.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ready",
                "components": {
                    "database": True,
                    "queue_freshness": True,
                    "services": True,
                },
                "detail": {
                    "reasons": [],
                    "queue_max_age_seconds": 12.3,
                    "checked_at": "2026-08-16T01:00:00+00:00",
                },
                "draining": False,
            }
        }
    )


__all__ = ["ErrorCodes", "ErrorResponse", "DeleteResponse", "HealthResponse", "LivezResponse", "ReadyzResponse"]
