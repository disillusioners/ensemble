"""Plane built-in MCP server definition.

Plane (https://plane.so) is a project management tool. This definition
connects to a remote Plane MCP server over streamable-http transport
and exposes its tools with the native ``plane_`` prefix (rather than
``mcp_plane_``) so they feel like first-class tools to the
project-manager agent.

Configuration is read from environment variables at call time so the
daemon can boot without the vars set and Plane can be added later
without restarting with a different env:

- ``PLANE_MCP_URL`` — server endpoint URL (e.g.
  ``https://mcp.example/plane/http/api-key/mcp``). When unset,
  ``is_available()`` returns ``False`` and the daemon silently skips
  Plane at bootstrap.
- ``PLANE_MCP_API_KEY`` — bearer token sent as
  ``Authorization: Bearer <key>``.
- ``PLANE_MCP_WORKSPACE_SLUG`` — workspace identifier sent as the
  ``x-workspace-slug`` header.

Disable via ``MCP_DISABLE_BUILT_IN_PLANE=true`` (standard builtin
disable convention, handled by the registry bootstrap path).
"""

from __future__ import annotations

import os
from typing import Any

from daemon.mcp.builtin_servers.base import BuiltinServerDefinition


class PlaneServerDefinition(BuiltinServerDefinition):
    """Built-in MCP server definition for Plane (project management)."""

    @property
    def name(self) -> str:
        return "plane"

    @property
    def display_name(self) -> str:
        return "Plane"

    @property
    def description(self) -> str:
        return (
            "Project management tools for Plane (issues, projects, "
            "cycles, modules, etc.). Tools are exposed with the "
            "native 'plane_' prefix."
        )

    @property
    def schema_version(self) -> str:
        return "1"

    @property
    def tool_name_prefix(self) -> str:
        """Override the tool name prefix to drop the standard ``mcp_``.

        With this override, Plane's tools are exposed as
        ``plane_list_issues``, ``plane_create_issue``, etc. instead
        of ``mcp_plane_list_issues`` — the ``is_mcp_tool()`` check
        no longer matches them, so they bypass any
        ``tools.deny: ["mcp"]`` filter and feel native to the
        project-manager agent.

        The dispatch to the MCP server is unaffected: the lazy
        coroutine in ``create_lazy_mcp_tools`` closes over the
        ORIGINAL ``original_tool_name`` (e.g. ``list_issues``) and
        calls ``session.call_tool(original_tool_name, kwargs)`` —
        only the EXPOSED ``StructuredTool.name`` changes.
        """
        return "plane"

    @classmethod
    def is_available(cls) -> bool:
        """Plane is available only when ``PLANE_MCP_URL`` is set.

        The other env vars (``PLANE_MCP_API_KEY``,
        ``PLANE_MCP_WORKSPACE_SLUG``) are read inside
        ``get_base_config`` and their absence will surface as a
        runtime error when an agent actually tries to use Plane —
        a missing URL alone should not silently create a broken
        DB record.
        """
        url = os.environ.get("PLANE_MCP_URL", "").strip()
        return bool(url)

    def get_base_config(self) -> dict[str, Any]:
        """Return base configuration for the Plane streamable-http MCP.

        Reads all three env vars lazily at call time so the daemon
        can be imported even when the vars are absent. Bootstrap
        layers call ``is_available()`` first and skip when the URL
        is missing.
        """
        url = os.environ.get("PLANE_MCP_URL", "").strip()
        api_key = os.environ.get("PLANE_MCP_API_KEY", "")
        workspace_slug = os.environ.get("PLANE_MCP_WORKSPACE_SLUG", "")
        return {
            "transport": "streamable-http",
            "url": url,
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "x-workspace-slug": workspace_slug,
            },
        }

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the configuration schema for the Plane server.

        Plane is env-driven (no user-configurable fields surfaced in
        the admin UI), so the schema is empty — matching the
        context7 pattern.
        """
        return []
