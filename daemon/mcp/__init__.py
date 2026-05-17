"""MCP (Model Context Protocol) client infrastructure."""

from daemon.mcp.config import (
    McpStdioConfig,
    McpSseConfig,
    McpStreamableHttpConfig,
    McpServerConfig,
    validate_mcp_server_config,
)
from daemon.mcp.connection_manager import (
    McpConnectionManager,
    get_mcp_connection_manager,
)
from daemon.mcp.tool_adapter import (
    mcp_tool_name,
    is_mcp_tool,
    adapt_mcp_tools,
)

__all__ = [
    "McpStdioConfig",
    "McpSseConfig",
    "McpStreamableHttpConfig",
    "McpServerConfig",
    "validate_mcp_server_config",
    "McpConnectionManager",
    "get_mcp_connection_manager",
    "mcp_tool_name",
    "is_mcp_tool",
    "adapt_mcp_tools",
]
