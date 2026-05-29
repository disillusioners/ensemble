"""MCP (Model Context Protocol) client infrastructure."""

from daemon.mcp.config import (
    McpStdioConfig,
    McpSseConfig,
    McpStreamableHttpConfig,
    McpServerConfig,
    McpConfigValidationError,
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
from daemon.mcp.kb_server import (
    create_kb_mcp_server,
    get_kb_mcp_http_app,
    get_kb_mcp_session_manager,
    get_kb_mcp_sse_app,
    set_kb_mcp_manager,
)

__all__ = [
    "McpStdioConfig",
    "McpSseConfig",
    "McpStreamableHttpConfig",
    "McpServerConfig",
    "McpConfigValidationError",
    "validate_mcp_server_config",
    "McpConnectionManager",
    "get_mcp_connection_manager",
    "mcp_tool_name",
    "is_mcp_tool",
    "adapt_mcp_tools",
    "create_kb_mcp_server",
    "get_kb_mcp_http_app",
    "get_kb_mcp_session_manager",
    "get_kb_mcp_sse_app",
    "set_kb_mcp_manager",
]
