# Phase 1: Foundation — MCP Client Module

## Objective
Install MCP dependencies and create the core MCP client infrastructure: config schema for server types, and a connection manager for lifecycle management. Tool discovery logic lives in `McpService` (Phase 2), not here.

## Coupling
- **Depends on**: None (this is the foundation phase)
- **Coupling type**: —
- **Shared files with other phases**: `daemon/mcp/` (new module, other phases import from here)
- **Shared APIs/interfaces**: `McpConnectionManager`, config models
- **Why this coupling**: Foundation module — all other phases depend on the interfaces defined here.

## Context
- The `McpServer` model already exists at `daemon/repositories/mcp_server/models.py` with a schemaless `config: dict[str, Any]` JSON field.
- No MCP client code exists anywhere in the codebase.
- All built-in tools use `@tool` decorator from `langchain_core.tools`, producing `StructuredTool` instances.
- The MCP config field currently has no schema — tests show it accepts any JSON dict.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Install MCP dependencies** | Add `mcp>=1.0.0` and `langchain-mcp-adapters>=0.1.0` to `pyproject.toml` dependencies. Run `uv sync` to install. | `pyproject.toml` |
| 2 | **Define MCP server config schema** | Create Pydantic models for the `config` JSON field. Support: (a) `McpStdioConfig` with `transport="stdio"`, `command`, `args`, `env`; (b) `McpSseConfig` with `transport="sse"`, `url`; (c) `McpStreamableHttpConfig` with `transport="streamable-http"`, `url`. Use discriminated union on `transport` field. Include `McpServerConfig` as the union type. | `daemon/mcp/config.py` (new) |
| 3 | **Create MCP module `__init__.py`** | Define public API: `get_mcp_connection_manager()`, `McpServerConfig`, `McpStdioConfig`, `McpSseConfig`. Re-export from submodules. | `daemon/mcp/__init__.py` (new) |
| 4 | **Implement `McpConnectionManager`** | Class that manages per-instance MCP client sessions. Key methods: `async connect_instance(instance_id, servers, per_server_timeout=5.0)` — opens connections to all active servers in parallel via `asyncio.gather()` with per-server timeout; `get_session(instance_id, server_name)` — returns cached session; `async close_instance(instance_id)` — closes all connections for an instance; `async close_all()` — cleanup on shutdown. Store connections as `dict[str, dict[str, ClientSession]]` (instance_id → server_name → session). Use **lazy-initialized** `asyncio.Lock` (not in `__init__`) for thread safety. Log warnings for failed connections but don't raise. | `daemon/mcp/connection_manager.py` (new) |
| 5 | **Add connection manager singleton** | Module-level singleton pattern for `McpConnectionManager` with `get_mcp_connection_manager()` factory. | `daemon/mcp/connection_manager.py` |
| 6 | **Add config validation to CRUD API** | In the MCP server CRUD router/service, validate incoming `config` dict against `McpServerConfig` Pydantic model at create and update time. Reject invalid configs with clear error messages before they're stored in the DB. | `daemon/routers/mcp_servers.py` (modified) |

## Key Files

### New Files
| File | Purpose |
|------|---------|
| `daemon/mcp/__init__.py` | Module public API, re-exports |
| `daemon/mcp/config.py` | Pydantic config models for MCP server configs |
| `daemon/mcp/connection_manager.py` | Per-instance connection lifecycle management |

### Modified Files
| File | Change |
|------|--------|
| `pyproject.toml` | Add `mcp` and `langchain-mcp-adapters` to dependencies |
| `daemon/routers/mcp_servers.py` | Validate config JSON against Pydantic schema at CRUD time |

## Detailed Design

### Config Schema (`daemon/mcp/config.py`)

```python
from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, Field


class McpStdioConfig(BaseModel):
    """Configuration for stdio-based MCP server (subprocess)."""
    transport: Literal["stdio"] = "stdio"
    command: str = Field(description="Command to run (e.g., 'npx', 'python')")
    args: list[str] = Field(default_factory=list, description="Command arguments")
    env: dict[str, str] | None = Field(default=None, description="Environment variables")


class McpSseConfig(BaseModel):
    """Configuration for SSE-based MCP server (HTTP Server-Sent Events)."""
    transport: Literal["sse"] = "sse"
    url: str = Field(description="SSE endpoint URL (e.g., 'http://localhost:8080/sse')")


class McpStreamableHttpConfig(BaseModel):
    """Configuration for streamable HTTP MCP server."""
    transport: Literal["streamable-http"] = "streamable-http"
    url: str = Field(description="HTTP endpoint URL")


McpServerConfig = Annotated[
    Union[McpStdioConfig, McpSseConfig, McpStreamableHttpConfig],
    Field(discriminator="transport")
]
```

**Design notes**:
- Default `transport` to `"stdio"` in the `McpStdioConfig` — most common case for local MCP servers
- Discriminated union ensures clean validation with Pydantic v2
- The existing `config: dict[str, Any]` field in DB model validates against this schema at CRUD time, not at DB level

### Connection Manager (`daemon/mcp/connection_manager.py`)

```python
class McpConnectionManager:
    """Manages MCP client connections per instance."""

    def __init__(self):
        self._connections: dict[str, dict[str, ClientSession]] = {}
        self._lock: asyncio.Lock | None = None  # Lazy-initialized

    def _get_lock(self) -> asyncio.Lock:
        """Get or create the asyncio lock (lazy init for event loop safety)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def connect_instance(
        self, instance_id: str, servers: list[McpServer], *, per_server_timeout: float = 5.0
    ) -> None:
        """Open MCP connections for all active servers in parallel. Non-fatal on errors."""
        async with self._get_lock():
            if instance_id not in self._connections:
                self._connections[instance_id] = {}

        async def _connect_one(server):
            try:
                async with asyncio.timeout(per_server_timeout):
                    session = await self._create_session(server)
                    async with self._get_lock():
                        self._connections[instance_id][server.name] = session
            except Exception as e:
                logger.warning(
                    f"Failed to connect to MCP server '{server.name}' "
                    f"for instance {instance_id[:8]}: {e}"
                )

        await asyncio.gather(*[_connect_one(s) for s in servers])

    def get_session(self, instance_id: str, server_name: str) -> ClientSession | None:
        """Get an existing session. Returns None if not connected."""
        return self._connections.get(instance_id, {}).get(server_name)

    async def close_instance(self, instance_id: str) -> None:
        """Close all MCP connections for an instance."""
        async with self._get_lock():
            sessions = self._connections.pop(instance_id, {})

        for server_name, session in sessions.items():
            try:
                await session.close()
            except Exception as e:
                logger.warning(f"Error closing session for '{server_name}': {e}")

    async def close_all(self) -> None:
        """Close all connections (shutdown cleanup)."""
        async with self._get_lock():
            all_sessions = self._connections.copy()
            self._connections.clear()

        for instance_id, sessions in all_sessions.items():
            for server_name, session in sessions.items():
                try:
                    await session.close()
                except Exception as e:
                    logger.warning(
                        f"Error closing session '{server_name}' for "
                        f"instance {instance_id[:8]}: {e}"
                    )
```

**Key design points**:
- **Lazy `asyncio.Lock`**: Not created in `__init__()` because `asyncio.Lock()` requires a running event loop. Created on first async use via `_get_lock()`.
- **Parallel connections**: `asyncio.gather()` connects to all servers concurrently with per-server timeout (default 5s). One slow server doesn't block others.
- **Non-fatal**: Failed connections are logged and skipped. Other servers' connections are still available.
- **`get_session()` is sync**: No `async` needed — just dict lookup. Used by tool invocation code.
- **`close_instance()` pops first**: Removes from tracking dict before closing, so a crash during close doesn't leave orphaned entries.

### Config Validation at CRUD Time

In the MCP server CRUD router, validate config before storing:

```python
from daemon.mcp.config import McpServerConfig
from pydantic import ValidationError

def _validate_mcp_config(config: dict) -> None:
    """Validate MCP server config against schema. Raises HTTPException on invalid."""
    try:
        McpServerConfig.model_validate(config)  # Pydantic v2
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid MCP server config: {e}"
        )
```

Call this in both `create_mcp_server` and `update_mcp_server` endpoints.

## Constraints
- All MCP operations must be non-blocking to the instance lifecycle
- Failed MCP servers must not prevent instance creation
- Connection manager must handle concurrent access safely
- Config parsing errors must be logged clearly with the server name
- `asyncio.Lock` must be lazy-initialized (not in `__init__`)

## Deliverables
- [ ] `pyproject.toml` updated with MCP dependencies, `uv sync` succeeds
- [ ] `daemon/mcp/__init__.py` created with public API
- [ ] `daemon/mcp/config.py` with Pydantic config models (validated with test cases)
- [ ] `daemon/mcp/connection_manager.py` with lifecycle management, lazy lock, parallel connections
- [ ] CRUD API validates config JSON against schema
- [ ] Each new file has module-level docstring
