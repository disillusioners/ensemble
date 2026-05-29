# Phase 1: MCP KB Server Module

## Objective
Create the `daemon/mcp/kb_server.py` module containing a `FastMCP` server instance with `ensemble_kb_explore` and `ensemble_kb_experience` tools. These tools wrap the existing knowledge base infrastructure for external consumption by reusing the same module-level helper functions from `daemon/tools/knowledge_tools.py`.

## Coupling
- **Depends on**: None
- **Coupling type**: — (root phase)
- **Shared files with other phases**: `daemon/mcp/kb_server.py` (created here, used by Phase 2)
- **Shared APIs/interfaces**: `setup_kb_mcp_server(manager)` — called during app lifespan
- **Why**: This is the foundational module that Phase 2 wires into the app

## Context
- This is a greenfield module
- Key reference: `daemon/tools/knowledge_tools.py` — the existing agent-internal implementation
- Key reference: `daemon/utils.py:490-583` — `invoke_agent_and_wait()` utility
- The MCP tools do NOT use the LangChain `@tool` decorator or the `create_knowledge_tools()` factory
- The MCP tools DO reuse the module-level helper functions from `knowledge_tools.py` — these are explicit-parameter functions with no closure coupling

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/mcp/kb_server.py` skeleton | Module with imports, logger, module-level `_manager` variable, and setter function `set_kb_mcp_manager(manager: InstanceManager)` | `daemon/mcp/kb_server.py` (new) |
| 2 | Define `ensemble_kb_explore` tool with full post-processing | `FastMCP` tool that: (a) validates `_manager` is set, (b) validates `project_id` is provided, (c) validates `mode` is one of `local|global|hybrid|naive`, (d) checks `is_rag_enabled()`, (e) calls `invoke_agent_and_wait()`, (f) **post-processes**: parse `_parse_should_update_kb(result)`, conditionally fire-and-forget `_enqueue_kb_update_job()`, strip KB heading from result, return cleaned result | `daemon/mcp/kb_server.py` (new) |
| 3 | Define `ensemble_kb_experience` tool using `_enqueue_experience_job()` | `FastMCP` tool that: (a) validates `_manager` is set, (b) validates `project_id` is provided, (c) checks `is_rag_enabled()`, (d) calls `_enqueue_experience_job(manager, text, project_id, source_instance_id)` via `asyncio.ensure_future()`, (e) returns confirmation. **Does NOT use `invoke_agent_and_wait()`** — avoids semaphore consumption and worker thread overhead. | `daemon/mcp/kb_server.py` (new) |
| 4 | Create `create_kb_mcp_server()` factory with eager session manager init | Function that creates the `FastMCP` instance, registers both tools, **eagerly calls `mcp.streamable_http_app()`** to initialize the session manager (preventing RuntimeError on later access), and returns the `FastMCP` instance. | `daemon/mcp/kb_server.py` (new) |
| 5 | Create dual-transport mount helpers | Two functions: `get_kb_mcp_sse_app()` and `get_kb_mcp_http_app()` that return Starlette sub-apps for SSE and StreamableHTTP respectively. `get_kb_mcp_session_manager()` returns the already-initialized session manager. | `daemon/mcp/kb_server.py` (new) |
| 6 | Export from `daemon/mcp/__init__.py` | Add exports: `create_kb_mcp_server`, `set_kb_mcp_manager`, `get_kb_mcp_sse_app`, `get_kb_mcp_http_app`, `get_kb_mcp_session_manager` | `daemon/mcp/__init__.py` |
| 7 | Write unit tests | Test tool logic with mocked `InstanceManager`: (a) test explore returns result with KB heading stripped, (b) test explore triggers KB update when flag is true, (c) test experience enqueues via `_enqueue_experience_job` not `invoke_agent_and_wait`, (d) test error when project_id missing, (e) test error when mode is invalid, (f) test error when RAG disabled, (g) test error when manager not initialized | `tests/unit/test_mcp_kb_server.py` (new) |

## Detailed Design

### Module Structure (`daemon/mcp/kb_server.py`)

```python
"""MCP server exposing Knowledge Base tools for external agent systems.

Provides ensemble_kb_explore and ensemble_kb_experience tools via
SSE and StreamableHTTP transports.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from daemon.rag.config import is_rag_enabled
from daemon.tools.knowledge_tools import (
    _enqueue_experience_job,
    _enqueue_kb_update_job,
    _generate_experience_idempotency_key,
    _generate_idempotency_key,
    _parse_should_update_kb,
    _SHOULD_UPDATE_KB_PATTERN,
)
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

# Module-level manager reference (set during app lifespan)
_manager: "InstanceManager | None" = None

# System parent ID for MCP-spawned agent instances
_MCP_SYSTEM_PARENT_ID = "mcp-kb-server"

# Valid explore modes
_VALID_MODES = ("local", "global", "hybrid", "naive")

# Module-level references (created once by create_kb_mcp_server)
_mcp_server: FastMCP | None = None
_http_app = None  # Eagerly created streamable_http_app


def set_kb_mcp_manager(manager: "InstanceManager") -> None:
    """Set the InstanceManager reference. Called during app lifespan startup."""
    global _manager
    _manager = manager


def create_kb_mcp_server() -> FastMCP:
    """Create and return the FastMCP server for KB tools.
    
    Called once during app startup. Eagerly initializes the StreamableHTTP
    session manager by calling streamable_http_app().
    """
    global _mcp_server, _http_app
    
    mcp = FastMCP(
        name="ensemble-kb",
        instructions=(
            "Knowledge Base tools for the agents-ensemble system. "
            "Use ensemble_kb_explore to search the knowledge base, "
            "and ensemble_kb_experience to record new knowledge."
        ),
        stateless_http=True,
        json_response=True,
    )

    # --- Register tools ---

    @mcp.tool()
    async def ensemble_kb_explore(
        query: str,
        project_id: str,
        mode: str = "hybrid",
    ) -> str:
        """..."""  # See Task 2 details below

    @mcp.tool()
    async def ensemble_kb_experience(
        text: str,
        project_id: str,
    ) -> str:
        """..."""  # See Task 3 details below

    # Eagerly initialize StreamableHTTP session manager
    # (prevents RuntimeError when get_kb_mcp_session_manager() is called later)
    _http_app = mcp.streamable_http_app()

    _mcp_server = mcp
    return mcp


def get_kb_mcp_sse_app(mount_path: str = "/api/mcp/kb/sse"):
    """Get Starlette sub-app for SSE transport."""
    if _mcp_server is None:
        raise RuntimeError("KB MCP server not created. Call create_kb_mcp_server() first.")
    return _mcp_server.sse_app(mount_path)


def get_kb_mcp_http_app():
    """Get Starlette sub-app for StreamableHTTP transport (already created eagerly)."""
    if _http_app is None:
        raise RuntimeError("KB MCP server not created. Call create_kb_mcp_server() first.")
    return _http_app


def get_kb_mcp_session_manager():
    """Get the StreamableHTTP session manager (initialized during create_kb_mcp_server)."""
    if _mcp_server is None:
        raise RuntimeError("KB MCP server not created. Call create_kb_mcp_server() first.")
    return _mcp_server.session_manager
```

### Tool Implementation Details

#### `ensemble_kb_explore` — with full post-processing (Fixes #2 and #5)

```python
@mcp.tool()
async def ensemble_kb_explore(
    query: str,
    project_id: str,
    mode: str = "hybrid",
) -> str:
    """Search the agents-ensemble knowledge base.

    Args:
        query: The question or topic to search for.
        project_id: Required. The project ID to search within.
        mode: Search mode - "local", "global", "hybrid", or "naive". Defaults to "hybrid".

    Returns:
        Search results from the knowledge base.
    """
    if _manager is None:
        return "Error: KB MCP server not initialized. Please try again later."

    if not project_id:
        return "Error: project_id is required. Provide the project ID to search within."

    # Fix #5: Validate mode parameter
    if mode not in _VALID_MODES:
        return f"Error: Invalid mode '{mode}'. Must be one of: {', '.join(_VALID_MODES)}."

    if not is_rag_enabled():
        return "Error: Knowledge base (RAG) is not enabled. Configure RAG to use this tool."

    try:
        # Spawn explorer agent and wait for result
        result = await invoke_agent_and_wait(
            manager=_manager,
            agent_id="explorer",
            message=query,
            project_id=project_id,
            parent_id=_MCP_SYSTEM_PARENT_ID,
            instance_name=f"mcp-explore-{project_id[:8]}",
            timeout=300.0,
        )

        if result is None:
            return "Explorer agent timed out or failed. Try a simpler query."

        # Fix #2: Post-processing — parse KB update flag from explorer response
        should_update_kb = _parse_should_update_kb(result)

        if should_update_kb:
            try:
                # Fire-and-forget: enqueue kb-importer job if explorer found new knowledge
                asyncio.ensure_future(_enqueue_kb_update_job(
                    manager=_manager,
                    query=query,
                    explorer_response=result,
                    project_id=project_id,
                    source_instance_id=_MCP_SYSTEM_PARENT_ID,
                ))
            except RuntimeError as e:
                # No running event loop — log but don't fail the response
                logger.warning("Failed to schedule kb-importer job (no event loop): %s", e)
            except Exception as e:
                logger.warning("Failed to schedule kb-importer job: %s", e)

        # Strip the "## Need Update KB: ..." heading from the response
        result = _SHOULD_UPDATE_KB_PATTERN.sub("", result).strip()

        return result

    except Exception as e:
        logger.error(f"ensemble_kb_explore failed: {e}", exc_info=True)
        return f"Error: {e}"
```

#### `ensemble_kb_experience` — using `_enqueue_experience_job()` (Fix #1)

```python
@mcp.tool()
async def ensemble_kb_experience(
    text: str,
    project_id: str,
) -> str:
    """Record new knowledge into the agents-ensemble knowledge base.

    Args:
        text: The knowledge text to record (facts, findings, patterns, etc.).
        project_id: Required. The project ID to record knowledge under.

    Returns:
        Confirmation message.
    """
    if _manager is None:
        return "Error: KB MCP server not initialized. Please try again later."

    if not project_id:
        return "Error: project_id is required. Provide the project ID to record knowledge under."

    if not is_rag_enabled():
        return "Error: Knowledge base (RAG) is not enabled. Configure RAG to use this tool."

    try:
        # Fire-and-forget: enqueue experiencer job via JobQueueService
        # This does NOT use invoke_agent_and_wait() — avoids semaphore consumption
        # and worker thread overhead for a fire-and-forget operation.
        asyncio.ensure_future(_enqueue_experience_job(
            manager=_manager,
            text=text,
            project_id=project_id,
            source_instance_id=_MCP_SYSTEM_PARENT_ID,
        ))
        return "Knowledge recording started."

    except RuntimeError as e:
        # No running event loop
        logger.warning("Failed to schedule experiencer job (no event loop): %s", e)
        return "Error: Failed to schedule knowledge recording. Please try again."
    except Exception as e:
        logger.error(f"ensemble_kb_experience failed: {e}", exc_info=True)
        return f"Error: {e}"
```

### Important Design Notes

1. **Fix #1 — No `invoke_agent_and_wait()` for experience**: The `_enqueue_experience_job()` function enqueues directly to the job queue via `JobQueueService`. It accesses `manager._job_queue_service` with `getattr(manager, "_job_queue_service", None)` and resolves the `system_kb_fifo_queue` (with `system_fifo_queue` fallback). No semaphore, no worker thread, no agent spawn. This matches the internal implementation exactly.

2. **Fix #2 — Full post-processing in explore**: The MCP explore tool does three post-processing steps matching the internal tool: (a) parse `_parse_should_update_kb(result)` from explorer response, (b) if true, fire-and-forget `_enqueue_kb_update_job()` for the kb-importer agent, (c) strip the `## Need Update KB: ...` heading from the response before returning.

3. **Fix #3 — Eager session manager init**: `create_kb_mcp_server()` calls `mcp.streamable_http_app()` before returning, which initializes the session manager. `get_kb_mcp_session_manager()` can then safely access `_mcp_server.session_manager` without RuntimeError.

4. **Fix #5 — Mode validation**: The `mode` parameter is validated against `("local", "global", "hybrid", "naive")` before proceeding. Returns clear error for invalid values.

5. **SYSTEM_PARENT_ID**: MCP-spawned agent instances use `"mcp-kb-server"` as `parent_id` / `source_instance_id`. This distinguishes them from agent-spawned instances in logs and job metadata.

6. **Shared helpers import**: The module imports `_enqueue_experience_job`, `_enqueue_kb_update_job`, `_parse_should_update_kb`, `_SHOULD_UPDATE_KB_PATTERN`, `_generate_idempotency_key`, and `_generate_experience_idempotency_key` from `daemon.tools.knowledge_tools`. These are module-level functions with explicit parameters — no closure coupling. If they are currently private (underscore-prefixed), they may need to be made importable or the import path adjusted. **Alternative**: If the underscore functions cannot be imported, extract them into a shared utility module (e.g., `daemon/tools/knowledge_helpers.py`) and import from both `knowledge_tools.py` and `kb_server.py`.

7. **No LangChain dependency**: The MCP tools are plain async functions decorated with `@mcp.tool()`, NOT LangChain `@tool`.

## Key Files
- `daemon/mcp/kb_server.py` — **NEW** — Core MCP server module
- `daemon/mcp/__init__.py` — Modified to add exports
- `daemon/tools/knowledge_tools.py` — Reference + import shared helpers (potentially refactor underscore functions to shared module)
- `daemon/utils.py` — Reference only (uses `invoke_agent_and_wait`)
- `daemon/rag/config.py` — Reference only (uses `is_rag_enabled`)

## Constraints
- Must NOT modify existing `daemon/tools/knowledge_tools.py` agent-internal tool behavior (refactoring shared helpers into a separate module is acceptable if needed for import)
- `project_id` is mandatory (no auto-detection — external callers have no instance context)
- Must work with both SSE and StreamableHTTP transports from the same FastMCP instance
- `stateless_http=True` for StreamableHTTP (no session state between requests)
- `ensemble_kb_experience` must NOT use `invoke_agent_and_wait()` — must use `_enqueue_experience_job()`
- `ensemble_kb_explore` must include full post-processing (KB update check + heading strip)
- `mode` parameter must be validated

## Deliverables
- [ ] `daemon/mcp/kb_server.py` with `FastMCP` server, both tools, mount helpers
- [ ] `daemon/mcp/__init__.py` updated with exports
- [ ] `tests/unit/test_mcp_kb_server.py` with unit tests covering all fixes
- [ ] All tests passing
