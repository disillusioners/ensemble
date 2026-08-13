"""Plane tool category.

Plane tools are created dynamically at runtime by ``create_lazy_mcp_tools``
with the ``plane_`` prefix override (built-in MCP server). This module
provides category metadata for the help tool and category resolution.
"""

CATEGORY_NAME = "Plane"
CATEGORY_DOC = """\
Tools from the built-in Plane MCP server (project management).

**Dynamic Discovery**:
- Tools are loaded from the Plane MCP server at runtime
- Tool names are prefixed with `plane_` (e.g., `plane_list_issues`)
- Requires PLANE_MCP_URL, PLANE_MCP_API_KEY, PLANE_MCP_WORKSPACE_SLUG env vars
"""
