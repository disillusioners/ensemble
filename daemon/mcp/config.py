"""Pydantic configuration models for MCP server connections."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class McpConfigValidationError(ValueError):
    """Raised when MCP server configuration is invalid."""

    pass


class McpStdioConfig(BaseModel):
    """Configuration for STDIO transport MCP servers."""

    transport: Literal["stdio"] = Field(default="stdio", description="Transport type")
    command: str = Field(description="Command to execute for the MCP server")
    args: list[str] = Field(default_factory=list, description="Command-line arguments")
    env: dict[str, str] | None = Field(default=None, description="Environment variables")
    timeout: float | None = Field(
        default=None,
        description="Connection timeout in seconds. Defaults to 30s for STDIO (supports cold starts). "
        "Set lower for fast servers or higher for slow network/package resolution.",
    )


class McpSseConfig(BaseModel):
    """Configuration for SSE (Server-Sent Events) transport MCP servers."""

    transport: Literal["sse"] = Field(default="sse", description="Transport type")
    url: str = Field(description="URL endpoint for the SSE MCP server")
    headers: dict[str, str] | None = Field(default=None, description="HTTP headers for the connection")


class McpStreamableHttpConfig(BaseModel):
    """Configuration for Streamable HTTP transport MCP servers."""

    transport: Literal["streamable-http"] = Field(default="streamable-http", description="Transport type")
    url: str = Field(description="URL endpoint for the Streamable HTTP MCP server")
    headers: dict[str, str] | None = Field(default=None, description="HTTP headers for the connection")


McpServerConfig = Annotated[
    Union[McpStdioConfig, McpSseConfig, McpStreamableHttpConfig],
    Field(discriminator="transport"),
]


def validate_mcp_server_config(config: dict[str, Any]) -> McpStdioConfig | McpSseConfig | McpStreamableHttpConfig:
    """
    Validate and parse an MCP server configuration dictionary.

    Args:
        config: Raw configuration dictionary with 'transport' key

    Returns:
        Validated config model for the appropriate transport type

    Raises:
        McpConfigValidationError: If validation fails
    """
    # Try individual models first (better error messages)
    for model_cls in (McpStdioConfig, McpSseConfig, McpStreamableHttpConfig):
        try:
            return model_cls.model_validate(config)
        except ValidationError:
            continue

    # Fallback to discriminated union for best error message
    try:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(McpServerConfig)
        return adapter.validate_python(config)
    except ValidationError as e:
        raise McpConfigValidationError(f"Invalid MCP server config: {e}") from e
    except Exception as e:
        raise McpConfigValidationError(f"Invalid MCP server config: {e}") from e
