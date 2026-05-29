# Phase 2: Integration & Wiring — Mount MCP KB Server into FastAPI App

## Objective
Wire the MCP KB server into the existing FastAPI application: create the FastMCP instance during lifespan startup, set the manager reference, mount both transport sub-apps (SSE + StreamableHTTP), and add the StreamableHTTP session manager to the app lifespan. **Critical**: mount calls must be placed BEFORE the catch-all SPA route.

## Coupling
- **Depends on**: Phase 1 (MCP KB Server Module)
- **Coupling type**: tight — Phase 2 imports exact module and functions created in Phase 1
- **Shared files with other phases**: `daemon/mcp/kb_server.py` (imported), `daemon/api.py` (modified)
- **Shared APIs/interfaces**: `create_kb_mcp_server()`, `set_kb_mcp_manager()`, `get_kb_mcp_session_manager()`, `get_kb_mcp_sse_app()`, `get_kb_mcp_http_app()`
- **Why tight**: Phase 2 directly calls Phase 1's exported functions and mounts the returned ASGI apps

## Context
- Phase 1 created the `daemon/mcp/kb_server.py` module with all the MCP server logic
- The existing FastAPI app in `daemon/api.py` uses a `@asynccontextmanager` lifespan (lines 83-362)
- Services are initialized in lifespan startup, stored in `app.state`, and injected via setter functions
- The StreamableHTTP session manager requires `session_manager.run()` in the lifespan context
- The `create_kb_mcp_server()` factory eagerly initializes the session manager by calling `mcp.streamable_http_app()` internally (Fix #3)
- **No existing `app.mount()` calls in the codebase** — this is the first mount usage
- **Catch-all SPA route** at `daemon/api.py:520` (`@app.get("/{path:path}")`) must come AFTER mount calls (Fix #4)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add MCP KB imports to `daemon/api.py` | Import `create_kb_mcp_server`, `set_kb_mcp_manager`, `get_kb_mcp_session_manager`, `get_kb_mcp_sse_app`, `get_kb_mcp_http_app` from `daemon.mcp` | `daemon/api.py` |
| 2 | Initialize MCP KB server in lifespan startup | After `InstanceManager` is created and services (especially `_job_queue_service`) are initialized: (a) call `create_kb_mcp_server()` to create the FastMCP instance (which eagerly inits session manager), (b) call `set_kb_mcp_manager(manager)` to inject the manager reference | `daemon/api.py` (lifespan startup section) |
| 3 | Add StreamableHTTP session manager to lifespan context | Nest `session_mgr.run()` within the lifespan context, wrapping the `yield`. The session manager is already initialized by `create_kb_mcp_server()`. | `daemon/api.py` (lifespan context) |
| 4 | Mount sub-apps BEFORE catch-all SPA route | Use `app.mount()` to mount: (a) SSE at `/api/mcp/kb/sse`, (b) StreamableHTTP at `/api/mcp/kb`. These calls MUST be placed BEFORE the catch-all `@app.get("/{path:path}")` at line 520. The mount calls go between `app.include_router(api_router)` (line 506) and the SPA routes (lines 509-540). | `daemon/api.py` |
| 5 | Write integration test | Test that MCP endpoints are accessible: (a) StreamableHTTP `/api/mcp/kb/mcp` responds to initialize request, (b) SSE `/api/mcp/kb/sse/sse` responds to GET, (c) tool listing via MCP protocol works | `tests/unit/test_mcp_kb_integration.py` (new) |

## Detailed Implementation Notes

### Changes to `daemon/api.py`

#### Import Section (near existing MCP imports)
```python
# Add near other MCP imports
from daemon.mcp import (
    create_kb_mcp_server,
    get_kb_mcp_http_app,
    get_kb_mcp_kb_session_manager,
    get_kb_mcp_sse_app,
    set_kb_mcp_manager,
)
from starlette.routing import Mount
```

#### Lifespan Startup (after manager + job_queue_service are initialized)
```python
# Initialize KB MCP server (eagerly inits StreamableHTTP session manager)
kb_mcp = create_kb_mcp_server()
set_kb_mcp_manager(manager)
```

**Placement**: After the manager is created AND `_job_queue_service` is initialized on the manager. The `_enqueue_experience_job()` function accesses `manager._job_queue_service`, so the service must be available. Based on exploration, this is around line 120-180 in the lifespan.

#### Lifespan Context (wrapping the existing yield)
```python
@asynccontextmanager
async def lifespan(app):
    # ... existing startup code ...
    
    # Initialize KB MCP server
    kb_mcp = create_kb_mcp_server()
    set_kb_mcp_manager(manager)
    
    # Start StreamableHTTP session manager within lifespan
    session_mgr = get_kb_mcp_session_manager()
    
    async with session_mgr.run():
        yield  # App is running
    
    # ... existing shutdown code ...
```

**Note**: If the existing lifespan already uses `AsyncExitStack` or nested context managers, add `session_mgr.run()` to the same stack. If it's a simple `try/finally`, wrap with `async with`.

#### Mounting the Sub-Apps — CRITICAL ORDERING (Fix #4)

```python
    # --- API routes ---
    app.include_router(api_router)  # line 506 — all /api/* routes
    
    # --- MCP KB server mounts (MUST be before catch-all SPA route) ---
    app.mount("/api/mcp/kb/sse", get_kb_mcp_sse_app("/api/mcp/kb/sse"))
    app.mount("/api/mcp/kb", get_kb_mcp_http_app())
    
    # --- UI serving endpoints (production) ---
    @app.get("/")
    async def serve_ui_root(): ...
    
    @app.get("/{path:path}")  # catch-all SPA route — MUST be last
    async def serve_ui_assets(path: str): ...
```

**Why this ordering matters**: Starlette matches routes in registration order. A `@app.get("/{path:path}")` catch-all would match `/api/mcp/kb/...` paths if registered before the mounts. Since `app.mount()` creates a separate routing scope, it's checked alongside routes — but only if registered before the catch-all. Placing mounts between `include_router` and the SPA routes ensures they're checked first.

**Note**: The SPA catch-all already skips `api` and `ws` prefixed paths explicitly, so there's a double safety net. But correct ordering is still required for the mount to work properly at the ASGI level.

### Integration Test Design

```python
# tests/unit/test_mcp_kb_integration.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from starlette.testclient import TestClient


class TestKbMcpIntegration:
    """Integration tests for MCP KB server mounting and endpoint accessibility."""

    @pytest.fixture
    def mock_manager(self):
        manager = MagicMock()
        manager._instance_repository = MagicMock()
        manager._job_queue_service = MagicMock()
        return manager

    @pytest.fixture
    def app(self, mock_manager):
        """Create FastAPI app with MCP KB server mounted (matching api.py pattern)."""
        from daemon.mcp.kb_server import (
            create_kb_mcp_server,
            set_kb_mcp_manager,
            get_kb_mcp_http_app,
            get_kb_mcp_sse_app,
        )
        from starlette.routing import Mount
        
        set_kb_mcp_manager(mock_manager)
        create_kb_mcp_server()
        
        app = FastAPI()
        
        # Mount MCP KB transports (same order as production api.py)
        app.mount("/api/mcp/kb/sse", get_kb_mcp_sse_app("/api/mcp/kb/sse"))
        app.mount("/api/mcp/kb", get_kb_mcp_http_app())
        
        return app

    def test_streamable_http_endpoint_exists(self, app):
        """Verify StreamableHTTP endpoint responds (not 404)."""
        client = TestClient(app)
        response = client.post("/api/mcp/kb/mcp", json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            }
        })
        assert response.status_code != 404

    def test_sse_endpoint_exists(self, app):
        """Verify SSE endpoint responds (not 404)."""
        client = TestClient(app)
        response = client.get("/api/mcp/kb/sse/sse")
        assert response.status_code != 404

    def test_tools_listable(self, app):
        """Verify MCP tools are discoverable via tools/list."""
        client = TestClient(app)
        # After initialize, send tools/list to verify both tools exist
        response = client.post("/api/mcp/kb/mcp", json={
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 2,
        })
        # Note: May need initialize first depending on MCP protocol state
        assert response.status_code != 404
```

## Key Files
- `daemon/api.py` — Modified: add imports, lifespan changes, mount sub-apps (ordering critical)
- `daemon/mcp/__init__.py` — Already modified in Phase 1 (verify exports are correct)
- `tests/unit/test_mcp_kb_integration.py` — **NEW** — Integration tests

## Constraints
- Must NOT break existing API endpoints or agent functionality
- `app.mount()` calls MUST be placed before the catch-all `@app.get("/{path:path}")` SPA route
- SSE `mount_path` parameter must match the actual mount point for correct URL construction
- More specific paths (`/api/mcp/kb/sse`) should be mounted before less specific (`/api/mcp/kb`) 
- The StreamableHTTP session manager lifecycle must be properly nested within the app lifespan
- `create_kb_mcp_server()` must be called AFTER `manager._job_queue_service` is initialized

## Deliverables
- [ ] `daemon/api.py` modified with MCP KB server initialization and mounting
- [ ] Mount calls placed before catch-all SPA route
- [ ] Both transports accessible at their designated paths
- [ ] Integration test passing
- [ ] Existing tests still passing (regression check)
