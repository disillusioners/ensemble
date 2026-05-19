# Phase 1: Context7 Server Definition

## Objective
Create the `Context7ServerDefinition` class in `daemon/mcp/builtin_servers/context7.py`, following the exact same pattern as `WebFetchServerDefinition`.

## Coupling
- **Depends on**: None (only depends on `base.py` which is stable)
- **Coupling type**: root phase
- **Shared files with other phases**: `daemon/mcp/builtin_servers/context7.py` (created here, imported by Phase 2)
- **Shared APIs/interfaces**: `BuiltinServerDefinition` ABC
- **Why this coupling**: Phase 2 imports and registers the class created here

## Context
- The `BuiltinServerDefinition` ABC requires: `name`, `display_name`, `description`, `schema_version`, `get_config_schema()`
- Optional overrides: `get_base_config()`, `build_config()`, `parse_config()`
- WebFetch (`daemon/mcp/builtin_servers/webfetch.py`) is the reference implementation
- Context7 takes **no CLI arguments or environment variables** — it's purely `npx -y @upstreamapi/context7-mcp`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `context7.py` file | New file in `daemon/mcp/builtin_servers/` | `daemon/mcp/builtin_servers/context7.py` |
| 2 | Implement `Context7ServerDefinition` class | Extend `BuiltinServerDefinition` with all required properties | `daemon/mcp/builtin_servers/context7.py` |
| 3 | Implement `get_base_config()` | Return stdio transport config with `npx` command and `-y @upstreamapi/context7-mcp` args | `daemon/mcp/builtin_servers/context7.py` |
| 4 | Implement `get_config_schema()` | Return empty list `[]` — no configurable fields | `daemon/mcp/builtin_servers/context7.py` |
| 5 | Implement `build_config()` override | Not needed — parent class handles empty schema correctly. Verify this. | `daemon/mcp/builtin_servers/context7.py` |
| 6 | Add module docstring | Document what Context7 does and why it's built-in | `daemon/mcp/builtin_servers/context7.py` |

## Key Files
- `daemon/mcp/builtin_servers/context7.py` — **NEW** — Context7 server definition
- `daemon/mcp/builtin_servers/base.py` — Base class (read-only reference)
- `daemon/mcp/builtin_servers/webfetch.py` — Reference implementation pattern

## Implementation Reference

> **Note**: No need to override `build_config()` or `parse_config()` — the parent `BuiltinServerDefinition` handles empty schema correctly. `build_config({})` returns the base config, and `parse_config(config)` returns `{}` for servers with no schema fields. Both are tested in Phase 3.

```python
# daemon/mcp/builtin_servers/context7.py
"""Context7 built-in MCP server definition.

Provides @upstreamapi/context7-mcp for fetching up-to-date, version-specific
documentation for libraries and frameworks. Resolves library names and fetches
real docs on demand — solving stale/hallucinated API knowledge in LLMs.
"""

from __future__ import annotations

from typing import Any

from daemon.mcp.builtin_servers.base import BuiltinServerDefinition


class Context7ServerDefinition(BuiltinServerDefinition):
    """Built-in MCP server definition for Context7 documentation lookup."""

    @property
    def name(self) -> str:
        return "context7"

    @property
    def display_name(self) -> str:
        return "Context7"

    @property
    def description(self) -> str:
        return (
            "Fetch up-to-date, version-specific documentation for libraries and frameworks. "
            "Resolves library names and fetches real docs on demand — "
            "solving stale/hallucinated API knowledge in LLMs."
        )

    @property
    def schema_version(self) -> str:
        return "1"

    def get_base_config(self) -> dict[str, Any]:
        """Return base configuration for Context7 MCP server."""
        return {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstreamapi/context7-mcp"],
        }

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return empty schema — Context7 has no user-configurable options."""
        return []
```

## Constraints
- Follow the exact naming and property pattern from `WebFetchServerDefinition`
- No user-configurable fields (empty schema)
- Base config uses `npx` (not `uvx` — this is an npm package, not a Python package)
- Schema version starts at `"1"`

## Deliverables
- [ ] `daemon/mcp/builtin_servers/context7.py` exists with `Context7ServerDefinition` class
- [ ] Class passes type checks (all abstract methods implemented)
- [ ] `build_config({})` returns correct stdio config dict
- [ ] `get_config_schema()` returns empty list
- [ ] `name` property returns `"context7"`
