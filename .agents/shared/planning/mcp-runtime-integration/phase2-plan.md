# Phase 2: Service Layer — MCP Service

## Objective
Create an `McpService` class that consolidates all MCP business logic: connecting to servers, discovering tools, converting them to LangChain format, caching per-instance, and cleanup. Wire it into `Manager.__init__()`. Update the tool registry to support MCP category filtering.

## Coupling
- **Depends on**: Phase 1 (imports `daemon/mcp/` module)
- **Coupling type**: **loose** — imports from Phase 1 but doesn't modify Phase 1 files. Phase 1 interfaces are stable.
- **Shared files with other phases**: None directly. Phase 3 will import `McpService`.
- **Shared APIs/interfaces**: `McpService` class with `preload_mcp_tools()`, `get_mcp_tools()`, `close_connections()`
- **Why this coupling**: Standard layered architecture — service depends on foundation, not the other way around.

## Context
- Services follow a consistent pattern (see `daemon/services/`): receive `manager` facade, use `TYPE_CHECKING` imports, have Google docstrings.
- Manager initializes services in `__init__()` (lines 470-530 of `daemon/manager.py`).
- The `_mcp_server_repository` already exists on the Manager.
- `McpConnectionManager` singleton is available from Phase 1.
- Tool discovery and LangChain conversion logic lives **here** in McpService, not in a separate `tool_loader.py`. This avoids dead code and keeps all MCP business logic in one place.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Create `McpService` class** | New file `daemon/services/mcp_service.py`. Follows existing service pattern: receives `manager` facade, uses `TYPE_CHECKING` imports. **Consolidates all MCP logic** — no separate `tool_loader.py`. Methods: (a) `async preload_mcp_tools(instance_id)` — connects to all active MCP servers in parallel, discovers tools, converts to LangChain `BaseTool` with `mcp_` prefix, stores in cache; (b) `get_mcp_tools(instance_id) -> list[BaseTool]` — sync method, reads from cache; (c) `async close_connections(instance_id)` — pop cache first, then close connections; (d) `async close_all_connections()` — shutdown cleanup. | `daemon/services/mcp_service.py` (new) |
| 2 | **Wire `McpService` into Manager** | Add `McpService` initialization in `Manager.__init__()` after lifecycle service (line ~521). Import with `TYPE_CHECKING`. Store as `self._mcp_service`. | `daemon/manager.py` (modified) |
| 3 | **Add `mcp` category to tool registry** | Add `"mcp": "daemon.tools.mcp_tools"` entry to `CATEGORY_MODULES` in `_tool_registry.py`. This ensures MCP tools show up in tool help listings. | `daemon/tools/_tool_registry.py` (modified) |
| 4 | **Add MCP tool category metadata** | Create `daemon/tools/mcp_tools.py` stub module with `CATEGORY_NAME = "MCP"` and `CATEGORY_DOC`. This module doesn't define individual tools (they're dynamic) but provides the category metadata for the help system. | `daemon/tools/mcp_tools.py` (new) |
| 5 | **Fix tool filter for dynamic MCP tools** | Modify `resolve_tool_filter()` in `daemon/tools/instance.py` to handle the `"mcp"` category specially. When `"mcp"` is encountered in allow/deny, match all tools with `mcp_` name prefix instead of looking up static `_tool_metadata`. This ensures `tools.deny: ["mcp"]` and `tools.allow: ["mcp"]` work correctly with dynamic tools. | `daemon/tools/instance.py` (modified) |

## Key Files

### New Files
| File | Purpose |
|------|---------|
| `daemon/services/mcp_service.py` | MCP service — all business logic in one place |
| `daemon/tools/mcp_tools.py` | Category metadata stub for MCP tools |

### Modified Files
| File | Change |
|------|--------|
| `daemon/manager.py` | Add `McpService` initialization (after line 521) |
| `daemon/tools/_tool_registry.py` | Add `"mcp"` entry to `CATEGORY_MODULES` dict |
| `daemon/tools/instance.py` | Fix `resolve_tool_filter()` for MCP prefix-based category expansion |

## Detailed Design

### McpService (`daemon/services/mcp_service.py`)

**No separate `tool_loader.py`** — all tool discovery and conversion lives here. This avoids the confusion of split responsibilities between a loader module and a service.

```python
"""MCP service — manages MCP tool lifecycle for agent instances.

This service consolidates all MCP business logic:
- Connects to MCP servers and discovers tools (async)
- Converts MCP tools to LangChain BaseTool format with mcp_ prefix
- Caches discovered tools per instance for sync retrieval
- Cleans up MCP connections on instance termination
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from ..manager import Manager

logger = logging.getLogger(__name__)


class McpService:
    """Service for managing MCP tool integration with agent instances."""

    def __init__(self, manager: Manager) -> None:
        self._manager = manager
        self._tools_cache: dict[str, list[BaseTool]] = {}

    async def preload_mcp_tools(self, instance_id: str) -> None:
        """Connect to MCP servers and discover tools for an instance.

        Called from async context BEFORE sync spawn_instance/get_instance.
        Results are cached for sync retrieval by get_mcp_tools().

        Non-fatal: logs errors and caches empty list on failure.
        """
        try:
            servers = self._manager._mcp_server_repository.list_mcp_servers(
                is_active=True
            )

            if not servers:
                logger.debug(f"No active MCP servers for instance {instance_id[:8]}")
                self._tools_cache[instance_id] = []
                return

            # Connect to all servers (parallel, per-server timeout)
            from ..mcp import get_mcp_connection_manager
            conn_mgr = get_mcp_connection_manager()
            await conn_mgr.connect_instance(instance_id, servers)

            # Discover tools from all connected servers (parallel)
            results = await asyncio.gather(
                *[
                    self._discover_server_tools(instance_id, server)
                    for server in servers
                ],
                return_exceptions=True,
            )

            tools = []
            for server, result in zip(servers, results):
                if isinstance(result, Exception):
                    logger.warning(
                        f"Failed to discover tools from MCP server "
                        f"'{server.name}': {result}"
                    )
                else:
                    tools.extend(result)

            logger.info(
                f"Discovered {len(tools)} MCP tools from {len(servers)} "
                f"server(s) for instance {instance_id[:8]}"
            )
            self._tools_cache[instance_id] = tools

        except Exception as e:
            logger.error(
                f"Failed to preload MCP tools for instance "
                f"{instance_id[:8]}: {e}"
            )
            self._tools_cache[instance_id] = []

    def get_mcp_tools(self, instance_id: str) -> list[BaseTool]:
        """Get cached MCP tools for an instance (sync).

        Returns:
            List of MCP tools. Empty list if not preloaded or on error.
        """
        return self._tools_cache.get(instance_id, [])

    async def _discover_server_tools(self, instance_id: str, server) -> list:
        """Discover tools from a single MCP server and convert to LangChain tools.

        Uses langchain-mcp-adapters for tool conversion.
        Namespaces tool names as mcp_{server_name}_{tool_name}.
        """
        from ..mcp import get_mcp_connection_manager
        from ..mcp.config import McpServerConfig

        conn_mgr = get_mcp_connection_manager()
        session = conn_mgr.get_session(instance_id, server.name)
        if session is None:
            return []

        # Parse config for transport-specific parameters
        config = McpServerConfig.model_validate(server.config)

        # Use langchain-mcp-adapters to list and convert tools
        from langchain_mcp_adapters.tools import load_mcp_tools
        mcp_tools = await load_mcp_tools(session)

        # Namespace tool names
        slug = _slugify(server.name)
        langchain_tools = []
        for tool in mcp_tools:
            original_name = tool.name
            tool.name = f"mcp_{slug}_{original_name}"
            tool.description = f"[MCP:{server.name}] {tool.description}"
            langchain_tools.append(tool)

        return langchain_tools

    async def close_connections(self, instance_id: str) -> None:
        """Close all MCP connections for an instance.

        Pops cache FIRST, then closes connections.
        If close fails, cache is already removed — no orphan.
        """
        self._tools_cache.pop(instance_id, None)
        try:
            from ..mcp import get_mcp_connection_manager

            conn_mgr = get_mcp_connection_manager()
            await conn_mgr.close_instance(instance_id)
            logger.debug(
                f"Closed MCP connections for instance {instance_id[:8]}"
            )
        except Exception as e:
            logger.warning(
                f"Error closing MCP connections for "
                f"instance {instance_id[:8]}: {e}"
            )

    async def close_all_connections(self) -> None:
        """Close all MCP connections (shutdown cleanup)."""
        self._tools_cache.clear()
        try:
            from ..mcp import get_mcp_connection_manager

            conn_mgr = get_mcp_connection_manager()
            await conn_mgr.close_all()
        except Exception as e:
            logger.warning(f"Error closing all MCP connections: {e}")


def _slugify(name: str) -> str:
    """Convert server name to tool-name-safe slug."""
    return name.lower().replace("-", "_").replace(" ", "_")
```

### Manager Registration (`daemon/manager.py`)

Add after lifecycle service (line ~521):

```python
# MCP service (depends on mcp_server_repository via manager)
self._mcp_service = McpService(manager=self)
```

### Tool Registry Entry (`daemon/tools/_tool_registry.py`)

Add to `CATEGORY_MODULES` dict:

```python
"mcp": "daemon.tools.mcp_tools",
```

### MCP Tools Stub (`daemon/tools/mcp_tools.py`)

```python
"""MCP dynamic tools — loaded at runtime from MCP servers.

This module provides category metadata only. Actual MCP tools are
discovered and injected dynamically by McpService during instance spawn.
"""
CATEGORY_NAME = "MCP"
CATEGORY_DOC = """\
Dynamic tools loaded from MCP (Model Context Protocol) servers.

These tools are discovered at runtime from configured MCP servers.
Tool names follow the pattern: mcp_{server_name}_{tool_name}
"""
```

### Fix Tool Filter for Dynamic MCP Tools (`daemon/tools/instance.py`)

The current `resolve_tool_filter()` (lines 44-101) expands categories by looking up `tool_categories` dict (from `list_tools_by_category()`, which reads `_tool_metadata`). Dynamic MCP tools are not in `_tool_metadata` at expansion time.

**Problem**: 
- `tools.deny: ["mcp"]` → `tool_categories["mcp"]` is empty → nothing denied → all MCP tools pass through ❌
- `tools.allow: ["mcp"]` → `tool_categories["mcp"]` is empty → only those names allowed → all MCP tools filtered out ❌

**Fix**: After the category expansion step, add prefix-based expansion for `"mcp"` category:

```python
def resolve_tool_filter(
    allow: list[str] | None, 
    deny: list[str] | None,
    tool_categories: dict[str, list[str]] | None = None,
    all_tool_names: set[str] | None = None,   # ← NEW parameter
) -> set[str] | None:
```

In the expansion logic (after line 89), when `"mcp"` is encountered in allow/deny and `all_tool_names` is provided:

```python
    # After normal category expansion:
    # Expand "mcp" category using prefix matching (dynamic tools)
    if all_tool_names and "mcp" in tool_categories and not tool_categories["mcp"]:
        mcp_tools = {name for name in all_tool_names if name.startswith("mcp_")}
        if mcp_tools:
            tool_categories["mcp"] = list(mcp_tools)
```

The caller `_apply_tool_filter()` passes `all_tool_names`:

```python
    all_tool_names = {getattr(t, 'name', None) for t in tools} - {None}
    allowed_tools = resolve_tool_filter(
        allow=agent_meta.tools.allow,
        deny=agent_meta.tools.deny,
        all_tool_names=all_tool_names,
    )
```

This ensures:
- `tools.deny: ["mcp"]` → all `mcp_*` tools denied ✅
- `tools.allow: ["*"]` → MCP tools included ✅
- `tools.allow: ["mcp"]` → only `mcp_*` tools allowed ✅
- `tools.allow: ["bash"]` → MCP tools excluded ✅

## Constraints
- Follow existing service patterns exactly (TYPE_CHECKING, docstrings, manager facade)
- `get_mcp_tools()` must be sync-safe (called from sync `create_instance_tools()`)
- All errors logged, never raised to caller
- Category entry in registry must not break existing tool filtering logic
- No separate `tool_loader.py` — all logic in `McpService` to avoid dead code
- `close_connections()` must pop cache BEFORE closing connections (prevent orphaning on failure)

## Deliverables
- [ ] `daemon/services/mcp_service.py` with consolidated MCP logic
- [ ] `daemon/manager.py` updated with `McpService` initialization
- [ ] `daemon/tools/_tool_registry.py` updated with `mcp` category
- [ ] `daemon/tools/mcp_tools.py` created with category metadata
- [ ] `daemon/tools/instance.py` updated with MCP-aware `resolve_tool_filter()`
