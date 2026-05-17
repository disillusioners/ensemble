"""Pydantic configuration models for MCP server connections."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Union

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class McpStdioConfig(BaseModel):
    """Configuration for STDIO transport MCP servers."""

    transport: str = Field(default="stdio", description="Transport type")
    command: str = Field(description="Command to execute for the MCP server")
    args: list[str] = Field(default_factory=list, description="Command-line arguments")
    env: dict[str, str] | None = Field(default=None, description="Environment variables")


class McpSseConfig(BaseModel):
    """Configuration for SSE (Server-Sent Events) transport MCP servers."""

    transport: str = Field(default="sse", description="Transport type")
    url: str = Field(description="URL endpoint for the SSE MCP server")
    headers: dict[str, str] | None = Field(default=None, description="HTTP headers for the connection")


class McpStreamableHttpConfig(BaseModel):
    """Configuration for Streamable HTTP transport MCP servers."""

    transport: str = Field(default="streamable-http", description="Transport type")
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
        ValueError: If transport type is unknown or validation fails
    """
    transport = config.get("transport")

    if transport == "stdio":
        return McpStdioConfig(**config)
    elif transport == "sse":
        return McpSseConfig(**config)
    elif transport == "streamable-http":
        return McpStreamableHttpConfig(**config)
    else:
        logger.warning(f"Unknown MCP transport type: {transport}")
        raise ValueError(f"Unknown MCP transport type: {transport}")
