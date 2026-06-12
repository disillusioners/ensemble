# Phase 1: Schema Cache + Lazy Tools Core

## Objective

Build the schema cache (tool schemas without sessions), a lazy tool factory that creates `StructuredTool` objects with deferred session resolution via `McpSessionProvider`, and a public API on `McpWarmupPool` for schema extraction. Wire these into the preload flow so `preload_mcp_tools()` becomes a fast, connection-free operation.

## Coupling

- **Depends on**: None
- **Coupling type**: —
- **Shared files with other phases**: `daemon/mcp/tool_adapter.py` (factory), `daemon/services/mcp_service.py` (schema cache + `McpSessionProvider`)
- **Shared APIs/interfaces**: `McpSessionProvider.get_session(server_name)` — the single interface between lazy coroutines and session resolution
- **Why this coupling**: Phase 2 implements the pool-aware resolution logic inside `McpSessionProvider`; Phase 1 defines the interface and builds the lazy coroutines that depend on it

## Context

### Current Flow (to be replaced)
```
preload_mcp_tools(instance_id)
  → pool.acquire() or conn_mgr.connect_instance()  ← BLOCKS 13s
  → load_mcp_tools(session)                         ← session bound into closure
  → adapt_mcp_tools(server, tools, timeout)          ← wraps with timeout
  → _tools_cache[instance_id] = tools
```

### New Flow
```
preload_mcp_tools(instance_id)
  → get_schemas_for_server(server)                    ← from cache, NO connection
  → create_lazy_mcp_tools(server, schemas, provider)  ← deferred coroutine
  → _tools_cache[instance_id] = lazy_tools
```

## Tasks

### Task 1: Add persistent schema cache to `McpService`

**File:** `daemon/services/mcp_service.py`

Add a class-level schema cache that stores tool schemas per server, independent of instances:

```python
class McpService:
    def __init__(self, manager):
        ...
        self._tools_cache: dict[str, list[BaseTool]] = {}        # per-instance (existing)
        self._schema_cache: dict[str, list[McpToolSchema]] = {}  # per-server (NEW)
        self._schema_cache_lock = asyncio.Lock()
```

**Schema representation:** Store the minimal info needed to create `StructuredTool`:
```python
@dataclass
class McpToolSchema:
    """MCP tool schema without session binding."""
    name: str                           # original MCP tool name
    description: str
    input_schema: dict[str, Any]        # JSON Schema for args
    server_name: str                    # which MCP server owns this
```

**Key method — `get_schemas_for_server()`:**
```python
async def get_schemas_for_server(self, server: McpServer) -> list[McpToolSchema]:
    """Get tool schemas for a server. Uses cache or discovers via warmup pool."""
    cache_key = server.name
    
    if cache_key in self._schema_cache:
        return self._schema_cache[cache_key]
    
    # For pooled servers: get from warmup pool's public API
    if self._warmup_pool and server.is_builtin:
        pool = self._warmup_pool
        cached_schemas = pool.get_cached_tool_schemas(server.name)
        if cached_schemas is not None:
            self._schema_cache[cache_key] = cached_schemas
            return cached_schemas
    
    # For cold servers: one-time connection to discover schemas (cached thereafter)
    schemas = await self._discover_schemas_cold(server)
    self._schema_cache[cache_key] = schemas
    return schemas
```

**Helper — `_discover_schemas_cold()`:**
```python
async def _discover_schemas_cold(self, server: McpServer) -> list[McpToolSchema]:
    """One-time schema discovery for non-pooled servers."""
    conn_mgr = get_mcp_connection_manager()
    try:
        # Create a temporary connection just for discovery
        await conn_mgr.connect_instance("_schema_discovery", [server], per_server_timeout=15.0)
        session = conn_mgr.get_session("_schema_discovery", server.name)
        if session:
            mcp_tools = await session.list_tools()
            schemas = [
                McpToolSchema(
                    name=t.name,
                    description=t.description or "",
                    input_schema=t.inputSchema,
                    server_name=server.name,
                )
                for t in mcp_tools.tools
            ]
            return schemas
    except Exception as e:
        logger.warning(f"Schema discovery failed for {server.name}: {e}")
        return []
    finally:
        await conn_mgr.close_instance("_schema_discovery")
```

**Note on `_discover_schemas_cold` timeout:** `connect_instance` receives `per_server_timeout=15.0`, but internally STDIO transport uses its own default of 30s (`STDIO_DEFAULT_TIMEOUT`) unless the server config specifies a `timeout` field. The 15s parameter applies to SSE/HTTP. This is existing behavior — no change needed.

**Cache invalidation — add to `McpService`:**
```python
def invalidate_schema_cache(self, server_name: str | None = None) -> None:
    """Invalidate schema cache. If server_name is None, clear all."""
    if server_name:
        self._schema_cache.pop(server_name, None)
    else:
        self._schema_cache.clear()
```

**Concrete wiring into MCP server CRUD** in `daemon/routers/mcp_servers.py`:

```python
# At the top of the router file, after imports:
from daemon.services.mcp_service import McpService  # or access via manager

# In create endpoint:
@router.post("/", response_model=McpServerInfo)
async def create_mcp_server(...):
    result = await mcp_server_repository.create(...)
    # Invalidate schema cache so new server's tools are discovered on next preload
    if manager._mcp_service:
        manager._mcp_service.invalidate_schema_cache(result.name)
    return result

# In update endpoint:
@router.put("/{server_id}", response_model=McpServerInfo)
async def update_mcp_server(...):
    result = await mcp_server_repository.update(...)
    if manager._mcp_service:
        manager._mcp_service.invalidate_schema_cache(result.name)
    return result

# In delete endpoint:
@router.delete("/{server_id}", response_model=McpServerDeleteResponse)
async def delete_mcp_server(...):
    server = await mcp_server_repository.get(server_id)
    result = await mcp_server_repository.delete(server_id)
    if manager._mcp_service and server:
        manager._mcp_service.invalidate_schema_cache(server.name)
    return result

# In toggle active endpoint (if exists):
@router.patch("/{server_id}/toggle")
async def toggle_mcp_server(...):
    result = await mcp_server_repository.toggle_active(server_id)
    if manager._mcp_service:
        manager._mcp_service.invalidate_schema_cache(result.name)
    return result
```

---

### Task 2: Add public schema extraction to `McpWarmupPool`

**File:** `daemon/mcp/warmup_pool.py`

Add a public method that extracts tool schemas from the internal `_tool_discovery_cache` without exposing the private attribute:

```python
def get_cached_tool_schemas(self, server_name: str) -> list[McpToolSchema] | None:
    """Extract tool schemas from the discovery cache for a pooled server.
    
    Returns None if the server has no cached tools (not warmed up yet).
    The returned schemas have the original MCP tool names (mcp_ prefix stripped).
    """
    if server_name not in self._tool_discovery_cache:
        return None
    
    cached_tools = self._tool_discovery_cache[server_name]
    prefix = f"mcp_{_slugify(server_name)}_"
    
    schemas = []
    for tool in cached_tools:
        original_name = tool.name
        if original_name.startswith(prefix):
            original_name = original_name[len(prefix):]
        schemas.append(McpToolSchema(
            name=original_name,
            description=tool.description or "",
            input_schema=tool.args_schema.schema() if tool.args_schema else {},
            server_name=server_name,
        ))
    return schemas
```

**Note:** This requires importing `McpToolSchema` (from `daemon.services.mcp_service` or a shared location). To avoid circular imports, consider placing `McpToolSchema` in a shared location like `daemon/mcp/models.py` or importing it inside the method.

---

### Task 3: Define `McpSessionProvider` and create lazy tool factory

**File:** `daemon/mcp/tool_adapter.py`

This is the core task. We define a protocol for session resolution and use it in the lazy tool factory.

#### 3a: Session provider interface

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class McpSessionProvider(Protocol):
    """Protocol for lazy MCP session resolution."""
    
    async def get_session(self, server_name: str) -> Any:
        """Get or create session for this instance+server.
        
        Must be safe to call concurrently from multiple coroutines.
        Implementations must use double-check locking for thread safety.
        """
        ...
```

#### 3b: Lazy tool factory

```python
def create_lazy_mcp_tools(
    server_name: str,
    schemas: list[dict],  # list of McpToolSchema-as-dict
    session_provider: McpSessionProvider,
    shared_session_cache: dict[str, Any],
    shared_session_lock: asyncio.Lock,
    tool_call_timeout: int = 120,
) -> list[BaseTool]:
    """Create lazy MCP tools that defer connection until first call.
    
    Args:
        server_name: MCP server name
        schemas: Tool schemas (list of dicts with name, description, input_schema)
        session_provider: McpSessionProvider for lazy session resolution
        shared_session_cache: Dict shared across ALL tools for this instance+server
        shared_session_lock: Lock shared across ALL tools for this instance+server
        tool_call_timeout: Per-call timeout (0 = disabled)
    """
    if not schemas:
        return []
    
    slugified_server = _slugify(server_name)
    prefix = f"mcp_{slugified_server}_"
    description_suffix = f"[MCP:{server_name}]"
    
    lazy_tools: list[BaseTool] = []
    
    for schema in schemas:
        tool_name = schema["name"]
        adapted_name = f"{prefix}{tool_name}"
        description = f"{schema['description']} {description_suffix}"
        
        # Build lazy coroutine — shares session_cache and lock with sibling tools
        coroutine = _build_lazy_coroutine(
            server_name=server_name,
            original_tool_name=tool_name,
            session_provider=session_provider,
            shared_session_cache=shared_session_cache,
            shared_session_lock=shared_session_lock,
            timeout_seconds=tool_call_timeout if tool_call_timeout > 0 else None,
        )
        
        # Create StructuredTool directly from schema
        tool = StructuredTool(
            name=adapted_name,
            description=description,
            args_schema=schema.get("input_schema", {}),
            coroutine=coroutine,
            response_format="content_and_artifact",
        )
        lazy_tools.append(tool)
    
    return lazy_tools
```

#### 3c: Lazy coroutine — shared session cache (C1 fix)

```python
def _build_lazy_coroutine(
    server_name: str,
    original_tool_name: str,
    session_provider: McpSessionProvider,
    shared_session_cache: dict[str, Any],
    shared_session_lock: asyncio.Lock,
    timeout_seconds: float | None,
) -> Callable:
    """Build a coroutine that lazily creates MCP session on first call.
    
    CRITICAL: shared_session_cache and shared_session_lock are provided by the
    caller (McpService.preload_mcp_tools) and shared across ALL tools for the
    same instance+server. This ensures N tools → 1 connection, not N connections.
    
    Concurrency guard: double-check-locking pattern (W7).
    - Fast path: check cache without lock (no contention for subsequent calls)
    - Slow path: acquire lock, re-check cache, create session if still missing
    """
    
    async def _get_session() -> Any:
        """Get or create session using double-check locking."""
        # Fast path — no lock needed for reads after session is established
        if server_name in shared_session_cache:
            return shared_session_cache[server_name]
        
        async with shared_session_lock:
            # Double-check after acquiring lock (W7: concurrency guard)
            if server_name in shared_session_cache:
                return shared_session_cache[server_name]
            
            session = await session_provider.get_session(server_name)
            shared_session_cache[server_name] = session
            return session
    
    async def _lazy_coroutine(**kwargs):
        """Lazy MCP tool coroutine — connects on first call."""
        try:
            session = await _get_session()
            
            # Strip InjectedToolArg if present (LangGraph injects this)
            kwargs.pop("runtime", None)
            
            # Call tool via session
            if timeout_seconds is not None:
                async with asyncio.timeout(timeout_seconds):
                    result = await session.call_tool(original_tool_name, kwargs)
            else:
                result = await session.call_tool(original_tool_name, kwargs)
            
            # Convert MCP result to LangChain format
            return _convert_call_tool_result(result)
            
        except asyncio.TimeoutError:
            raise ToolException(
                f"Tool '{original_tool_name}' on server '{server_name}' "
                f"timed out after {timeout_seconds}s. The MCP server may be unresponsive."
            )
        except ToolException:
            raise  # re-raise our own errors
        except Exception as e:
            raise ToolException(
                f"MCP tool call failed for '{original_tool_name}' on '{server_name}': {e}"
            )
    
    return _lazy_coroutine
```

---

### Task 4: Import-based result conversion (W2 fix)

**File:** `daemon/mcp/tool_adapter.py`

Import `_convert_call_tool_result` from `langchain_mcp_adapters` rather than vendoring. The library version handles AudioContent, ResourceLink, EmbeddedResource, and structuredContent — our vendored version was missing these.

```python
# Import from library with fallback
try:
    from langchain_mcp_adapters.tools import _convert_call_tool_result
except ImportError:
    # Fallback: minimal conversion if library structure changes
    def _convert_call_tool_result(result):
        """Minimal fallback for MCP result conversion."""
        if hasattr(result, 'content'):
            content = []
            for item in result.content:
                if hasattr(item, 'text'):
                    content.append({"type": "text", "text": item.text})
                else:
                    content.append(str(item))
            if getattr(result, 'isError', False):
                from langchain_core.tools import ToolException
                raise ToolException(str(content))
            return content, None
        return [str(result)], None
```

**Why import instead of vendor:**
- The library version is 113 lines handling AudioContent, ResourceLink, EmbeddedResource, structuredContent
- Our vendored version was 31 lines — missing all edge cases
- `try/except ImportError` protects against library restructuring
- If the private function moves, the fallback provides basic functionality with a clear signal to update

---

### Task 5: Rewrite `preload_mcp_tools()` to use schema cache + `McpSessionProvider`

**File:** `daemon/services/mcp_service.py`

Replace the current blocking preload with a fast, connection-free version that creates shared session caches:

```python
async def preload_mcp_tools(self, instance_id: str) -> None:
    """Preload MCP tool schemas (lazy — no connections established)."""
    async with await self._get_preload_lock(instance_id):
        # Check if already loaded
        if instance_id in self._tools_cache:
            return
        
        # List active servers
        servers = self._manager._mcp_server_repository.list_servers(is_active=True)
        if not servers:
            self._tools_cache[instance_id] = []
            return
        
        all_tools: list[BaseTool] = []
        tool_call_timeout = self._get_tool_call_timeout()
        
        # Per-instance session caches (shared across tools for same server)
        # Key: server_name → {"cache": dict, "lock": asyncio.Lock}
        instance_session_state: dict[str, dict] = {}
        
        for server in servers:
            # Get schemas (from cache — fast, no connections)
            schemas = await self.get_schemas_for_server(server)
            if not schemas:
                continue
            
            # Create shared session cache + lock for this instance+server
            # All tools for this server share the same dict and lock
            instance_session_state[server.name] = {
                "cache": {},
                "lock": asyncio.Lock(),
            }
            
            # Create session provider for this instance
            session_provider = self._create_session_provider(instance_id)
            
            # Create lazy tools
            schema_dicts = [
                {
                    "name": s.name,
                    "description": s.description,
                    "input_schema": s.input_schema,
                }
                for s in schemas
            ]
            
            lazy_tools = create_lazy_mcp_tools(
                server_name=server.name,
                schemas=schema_dicts,
                session_provider=session_provider,
                shared_session_cache=instance_session_state[server.name]["cache"],
                shared_session_lock=instance_session_state[server.name]["lock"],
                tool_call_timeout=tool_call_timeout,
            )
            all_tools.extend(lazy_tools)
        
        self._tools_cache[instance_id] = all_tools
        # Store session state for cleanup access
        self._session_caches[instance_id] = instance_session_state
        
        logger.info(
            f"Lazy-loaded {len(all_tools)} MCP tool schemas for instance {instance_id[:8]}"
        )
```

**New state on McpService:**
```python
self._session_caches: dict[str, dict[str, dict]] = {}  # instance_id → {server_name → {"cache": dict, "lock": Lock}}
```

**Helper:**
```python
def _create_session_provider(self, instance_id: str) -> McpSessionProvider:
    """Create a session provider bound to this instance."""
    return _McpSessionProviderImpl(self, instance_id)

def _get_server_by_name(self, server_name: str) -> McpServer | None:
    """Look up server config by name."""
    servers = self._manager._mcp_server_repository.list_servers(is_active=True)
    for s in servers:
        if s.name == server_name:
            return s
    return None

def _get_tool_call_timeout(self) -> int:
    """Get configured tool call timeout."""
    if hasattr(self._manager, 'config') and hasattr(self._manager.config, 'mcp_pool'):
        return self._manager.config.mcp_pool.tool_call_timeout
    return 120
```

---

### Task 6: Implement `_McpSessionProviderImpl` — pool-aware session resolution

**File:** `daemon/services/mcp_service.py`

This class lives on `McpService` and implements the `McpSessionProvider` protocol. It tries the warmup pool first (for pooled servers), then falls back to `connect_instance()`:

```python
class _McpSessionProviderImpl:
    """Pool-aware lazy session resolver bound to a specific instance.
    
    Implements the McpSessionProvider protocol.
    """
    
    def __init__(self, mcp_service: McpService, instance_id: str):
        self._service = mcp_service
        self._instance_id = instance_id
    
    async def get_session(self, server_name: str) -> Any:
        """Get or create session for this instance+server.
        
        Resolution order:
        1. Check connection_manager for existing session (fast path)
        2. For pooled servers, try warmup pool acquire
        3. Fall back to connect_instance (cold start via transport)
        """
        conn_mgr = get_mcp_connection_manager()
        
        # 1. Fast path: session already exists in connection manager
        existing = conn_mgr.get_session(self._instance_id, server_name)
        if existing:
            return existing
        
        # 2. For pooled servers, try acquiring from pool
        if self._service._warmup_pool:
            pool = self._service._warmup_pool
            if server_name in pool._configs:
                try:
                    conn = await pool.acquire(server_name)
                    if conn:
                        await conn_mgr.transfer_session(
                            self._instance_id, server_name,
                            conn.session, conn.stream_cm,
                        )
                        return conn.session
                except Exception as e:
                    logger.warning(
                        f"Pool acquire failed for {server_name}: {e}, "
                        f"falling back to cold start"
                    )
        
        # 3. Cold start: connect_instance handles full transport flow
        #    (_create_session → _open_and_track_session → store in _connections)
        server = self._service._get_server_by_name(server_name)
        if server is None:
            raise ToolException(
                f"MCP server '{server_name}' not found. It may have been removed."
            )
        
        await conn_mgr.connect_instance(
            self._instance_id,
            [server],
            per_server_timeout=15.0,
        )
        session = conn_mgr.get_session(self._instance_id, server_name)
        if session is None:
            raise ToolException(
                f"Failed to connect to MCP server '{server_name}'. "
                f"The server may be unavailable."
            )
        return session
```

**Why `connect_instance` instead of a custom lazy method (C2 fix):**
`connect_instance()` already handles the full flow for all 3 transports:
- `_create_session()` → transport dispatch (stdio subprocess / SSE HTTP / StreamableHTTP)
- `_open_and_track_session()` → `ManagedClientSession.start()` + `initialize()` + store in `_connections`
- Error handling and cleanup on failure

Creating a custom `_create_and_store_session` method would duplicate this logic and risk skipping `_create_session()` (the actual transport creation). Instead, we pass a single-element `[server]` list to `connect_instance()` — it handles everything correctly.

The double-check-locking in `_build_lazy_coroutine._get_session()` prevents duplicate calls: if two tools call concurrently, the first acquires the lock and creates the session; the second finds it cached after acquiring the lock.

---

### Task 7: Wire schema cache invalidation into MCP server CRUD routes

**File:** `daemon/routers/mcp_servers.py`

See Task 1 for the concrete wiring pattern. In summary:
- `create_mcp_server`: `invalidate_schema_cache(result.name)` after successful create
- `update_mcp_server`: `invalidate_schema_cache(result.name)` after successful update
- `delete_mcp_server`: `invalidate_schema_cache(server.name)` after successful delete
- `toggle_mcp_server` (if exists): `invalidate_schema_cache(result.name)` after toggle

Access `mcp_service` via `request.app.state.manager._mcp_service` (or however the manager is injected in this router).

## Key Files

- `daemon/mcp/tool_adapter.py` — New `McpSessionProvider` protocol, `create_lazy_mcp_tools()`, `_build_lazy_coroutine()`, imported `_convert_call_tool_result`
- `daemon/services/mcp_service.py` — New `_schema_cache`, `McpToolSchema`, `get_schemas_for_server()`, `_McpSessionProviderImpl`, rewritten `preload_mcp_tools()`
- `daemon/mcp/warmup_pool.py` — New `get_cached_tool_schemas()` public method
- `daemon/routers/mcp_servers.py` — Schema cache invalidation hooks in CRUD handlers
- `daemon/manager.py` — Unchanged (preload is already async-compatible)

## Constraints

- `args_schema` in `StructuredTool` expects a Pydantic model or JSON Schema dict — verify both work with LangGraph
- Tool names must still follow `mcp_{slugified_server}_{original_name}` pattern (tested by existing tests)
- `response_format="content_and_artifact"` must match what `ToolNode` expects
- Existing `_load_mcp_tools()` in `instance.py` reads from `_tools_cache` — unchanged
- `create_help_tool()` needs `mcp_tool_names` — still extracted from cached tools, unchanged
- `McpToolSchema` must be importable from both `mcp_service.py` and `warmup_pool.py` without circular imports — place in shared location if needed

## Deliverables

- [ ] `McpToolSchema` dataclass (shared location to avoid circular imports)
- [ ] `_schema_cache` + `get_schemas_for_server()` + `_discover_schemas_cold()` + `invalidate_schema_cache()`
- [ ] `get_cached_tool_schemas()` public method on `McpWarmupPool`
- [ ] `McpSessionProvider` protocol in `tool_adapter.py`
- [ ] `create_lazy_mcp_tools()` + `_build_lazy_coroutine()` with shared session cache/lock
- [ ] Imported `_convert_call_tool_result` with ImportError fallback
- [ ] `_McpSessionProviderImpl` with pool-first, connect_instance-fallback resolution
- [ ] Rewritten `preload_mcp_tools()` creating shared session state per instance+server
- [ ] Cache invalidation wired into MCP server CRUD routes
