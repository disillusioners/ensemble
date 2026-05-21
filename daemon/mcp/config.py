"""Pydantic configuration models for MCP server connections."""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


class McpConfigValidationError(ValueError):
    """Raised when MCP server configuration is invalid."""

    pass


def _is_restricted_ip(ip_str: str, allow_loopback: bool) -> bool:
    """
    Check if an IP address is restricted (private, loopback, link-local, reserved).

    Args:
        ip_str: IP address string to check
        allow_loopback: Whether to allow loopback addresses (for local dev)

    Returns:
        True if the IP is restricted, False otherwise
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Not a valid IP address
        return False

    # Check loopback (127.x.x.x, ::1)
    if ip.is_loopback:
        return not allow_loopback

    # Check private networks (10.x.x.x, 172.16-31.x.x, 192.168.x.x)
    if ip.is_private:
        return not allow_loopback

    # Check link-local (169.254.x.x, fe80::)
    if ip.is_link_local:
        return True

    # Check reserved
    if ip.is_reserved:
        return True

    return False


def _validate_url_not_ssrf(url: str) -> str:
    """
    Validate that a URL does not point to a restricted/internal address.

    This prevents SSRF attacks by blocking connections to:
    - Loopback addresses (127.x.x.x, ::1) unless MCP_ALLOW_LOOPBACK=true
    - Private networks (10.x.x.x, 172.16-31.x.x, 192.168.x.x) unless MCP_ALLOW_LOOPBACK=true
    - Link-local addresses (169.254.x.x, fe80::)
    - Reserved IP addresses

    DNS hostnames are resolved and the resolved IP is checked.

    Args:
        url: URL string to validate

    Returns:
        The original URL string if validation passes

    Raises:
        McpConfigValidationError: If the URL resolves to a restricted address
    """
    # Parse the URL to extract hostname
    try:
        from urllib.parse import urlparse
    except ImportError:
        from urlparse import urlparse  # type: ignore

    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        # No hostname (e.g., relative URL) - let it pass, connection will fail anyway
        return url

    # Allow loopback only when env var is set (for local dev)
    allow_loopback = os.environ.get("MCP_ALLOW_LOOPBACK", "false").lower() == "true"

    try:
        # Resolve hostname to IP addresses
        # getaddrinfo returns a list of (family, type, proto, canonname, sockaddr)
        addr_info = socket.getaddrinfo(hostname, None)

        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]

            if _is_restricted_ip(ip_str, allow_loopback):
                raise McpConfigValidationError(
                    f"URL resolves to a restricted address: {ip_str}. "
                    f"This may indicate an SSRF attempt. "
                    f"Set MCP_ALLOW_LOOPBACK=true to allow loopback for local development."
                )
    except socket.gaierror:
        # Cannot resolve hostname - let the connection attempt handle this
        # (it will fail with a proper error message)
        pass

    return url


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

    @field_validator("url", mode="after")
    @classmethod
    def validate_url_no_ssrf(cls, url: str) -> str:
        """Validate URL does not point to internal/restricted addresses."""
        return _validate_url_not_ssrf(url)


class McpStreamableHttpConfig(BaseModel):
    """Configuration for Streamable HTTP transport MCP servers."""

    transport: Literal["streamable-http"] = Field(default="streamable-http", description="Transport type")
    url: str = Field(description="URL endpoint for the Streamable HTTP MCP server")
    headers: dict[str, str] | None = Field(default=None, description="HTTP headers for the connection")

    @field_validator("url", mode="after")
    @classmethod
    def validate_url_no_ssrf(cls, url: str) -> str:
        """Validate URL does not point to internal/restricted addresses."""
        return _validate_url_not_ssrf(url)


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
