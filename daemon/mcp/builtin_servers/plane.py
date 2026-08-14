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

Availability is gated entirely by ``PLANE_MCP_URL`` + ``PLANE_MCP_API_KEY``:
when both are set the server registers; when either is absent the daemon
silently skips it (no DB record, no connection). There is no separate
disable toggle — absence of the required env vars IS the disable mechanism.

Resilience (Phase 4)
--------------------
Plane opts into the hybrid resilience layer via ``resilience_config``:

- **Retry** — transient failures (5xx, timeouts, connection resets) get
  retried with exponential backoff (1s/2s/4s + jitter, max 3 attempts).
  Auth and tool errors propagate immediately.
- **Result caching** — read tools (``list_*``, ``get_*``, ``search_*``)
  cache results for 5 min (avoids hammering Plane on repeated lookups).
- **Circuit breaker** — 5 consecutive failures opens the circuit for 60s
  before the next call probes recovery (5s timeout on the probe).
- **Graceful degradation** — when the circuit is OPEN or all retries
  fail, the agent receives a structured JSON fallback instead of a
  hard ``ToolException``. McpAuthError is the exception (raises so the
  operator notices the bad key).
- **Write invalidation** — write tools (``create_*``, ``update_*``,
  ``delete_*``, ...) invalidate the entire server cache on success.

Tunable via env vars (overrides the defaults below):

- ``PLANE_RETRY_MAX_ATTEMPTS`` (default 3)
- ``PLANE_RETRY_BASE_DELAY`` (default 1.0)
- ``PLANE_CACHE_TTL_SECONDS`` (default 300)
- ``PLANE_CIRCUIT_FAILURE_THRESHOLD`` (default 5)
- ``PLANE_CIRCUIT_RECOVERY_TIMEOUT`` (default 60)
- ``PLANE_PROBE_TIMEOUT`` (default 5)
"""

from __future__ import annotations

import json
import os
from typing import Any

from daemon.mcp.builtin_servers.base import BuiltinServerDefinition


# Default Plane fallback message (returned as a JSON string to the agent
# when the circuit is OPEN or all retries are exhausted). Kept as a
# module-level constant so tests can assert against the canonical
# payload without rebuilding it from env vars.
_DEFAULT_PLANE_FALLBACK: str = json.dumps(
    {
        "status": "unavailable",
        "source": "plane",
        "message": (
            "Plane MCP is currently unreachable. Using local project "
            "history only."
        ),
    }
)


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

    @property
    def read_only_tools(self) -> bool:
        """CR-3: Plane is exposed read-only to the agent.

        The project-manager agent's ``meta.json`` declares
        ``tools.allow: ["plane"]`` and ``tools.deny`` entries for
        specific Plane write verbs, but a deny-list approach is
        brittle: a future Plane MCP server release can add new
        write verbs (e.g. ``plane_export_data``) that aren't yet on
        the deny list. Declaring ``read_only_tools = True`` at
        definition time drops the entire write set from the tool
        list the agent sees, so the LLM can never call them — the
        deny list becomes belt-and-suspenders, not the primary
        enforcement.

        Pattern-matching is delegated to the resilience layer's
        ``is_read_tool(tool_name, resilience_config)`` so the same
        ``read_tool_patterns`` / ``write_tool_patterns`` tuple that
        drives caching also drives filtering — no chance for the
        two classifiers to disagree on what counts as a write.

        Per-agent opt-out (Approach B): an agent's ``meta.json`` may
        declare ``mcp_full_access: ["plane"]`` to receive the FULL
        tool surface for Plane — the read-only filter is skipped at
        ``McpService._get_read_only_tools`` time, and write verbs
        are exposed to the agent as if this declaration returned
        ``False``. The opt-out is meta-side; this property remains
        ``True`` as the global fail-closed default for every agent
        that does NOT list ``plane`` in ``mcp_full_access``. Unknown
        or typo entries (``mcp_full_access: ["pane"]``) are validated
        in ``AgentRegistry.validate_tool_configs`` and produce a
        WARNING; the strip stays applied so a typo fails closed.
        """
        return True

    @property
    def resilience_config(self):  # type: ignore[override]
        """Plane-specific resilience tuning.

        See module docstring for the behavior summary. All values
        are env-overridable so operators can tighten/loosen
        behavior without code changes.

        Function-local imports: keep ``daemon.mcp.resilience`` out of
        the module-level import graph so the builtin registry can be
        loaded without pulling in the resilience machinery (which
        itself imports ``daemon.sources.circuit_breaker``).
        """
        # Function-local import: keeps the coupling narrow and avoids
        # loading the resilience module for builtins that don't opt in.
        from daemon.mcp.resilience import ResilienceConfig, RetryPolicy

        return ResilienceConfig(
            retry_policy=RetryPolicy(
                max_attempts=int(
                    os.environ.get("PLANE_RETRY_MAX_ATTEMPTS", "3")
                ),
                base_delay=float(
                    os.environ.get("PLANE_RETRY_BASE_DELAY", "1.0")
                ),
            ),
            cache_ttl=float(
                os.environ.get("PLANE_CACHE_TTL_SECONDS", "300")
            ),
            circuit_failure_threshold=int(
                os.environ.get("PLANE_CIRCUIT_FAILURE_THRESHOLD", "5")
            ),
            circuit_recovery_timeout=float(
                os.environ.get("PLANE_CIRCUIT_RECOVERY_TIMEOUT", "60")
            ),
            probe_timeout=float(
                os.environ.get("PLANE_PROBE_TIMEOUT", "5")
            ),
            # Always use the canonical fallback — we deliberately don't
            # expose a per-server override here, since the message
            # identifies the source ("plane") which is meaningless if
            # overridable.
            fallback_message=_DEFAULT_PLANE_FALLBACK,
            # 5 min — avoids hitting Plane API on every tool call when
            # the agent polls the same view repeatedly within a turn.
            stale_threshold=300.0,
            read_tool_patterns=(
                "list_",
                "get_",
                "search_",
            ),
            # ``set_`` and ``edit_`` are Plane-specific verb variants
            # (e.g. ``plane_set_issue_priority``). ``assign_`` covers
            # the assignee-update tools.
            write_tool_patterns=(
                "create_",
                "update_",
                "delete_",
                "add_",
                "remove_",
                "set_",
                "edit_",
                "assign_",
            ),
        )

    @classmethod
    def is_available(cls) -> bool:
        """Plane is available only when BOTH URL and API key are set.

        The workspace slug is read inside ``get_base_config`` and its
        absence surfaces as a runtime header error — but a missing URL
        or API key means the server cannot function at all, so we refuse
        to register it (no DB record, no tool discovery).

        There is intentionally NO disable toggle for this server:
        absence of the required env vars IS the disable mechanism.
        """
        url = os.environ.get("PLANE_MCP_URL", "").strip()
        api_key = os.environ.get("PLANE_MCP_API_KEY", "").strip()
        return bool(url) and bool(api_key)

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
