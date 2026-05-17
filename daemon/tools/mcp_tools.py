"""MCP (Model Context Protocol) tool category.

MCP tools are discovered dynamically at runtime from configured MCP servers.
Unlike other tool categories, individual tools are not defined in this module.
"""

CATEGORY_NAME = "MCP"
CATEGORY_DOC = """\
Tools from external MCP (Model Context Protocol) servers.

**Dynamic Discovery**:
- Tools are loaded from configured MCP servers at runtime
- Available tools depend on the MCP server configuration
- Tool names and schemas vary by server implementation

**Usage**:
- Use `list_mcp_tools()` to see available MCP tools
- Use `call_mcp_tool(name, arguments)` to invoke a specific tool
"""
