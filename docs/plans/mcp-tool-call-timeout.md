# MCP Tool Call Timeout

**Date**: 2026-06-11
**Status**: Draft
**Impact**: All transports (STDIO, SSE, Streamable HTTP)

## Problem

MCP tool invocations (`session.call_tool()`) have **no timeout**. When an MCP server hangs
or becomes unresponsive during tool execution, the calling instance is blocked indefinitely:

- LangGraph's `ToolNode` awaits the tool coroutine with no deadline.
- `langchain_mcp_adapters` calls `session.call_tool()` directly (see
  `langchain_mcp_adapters/tools.py` line ~394) with no timeout wrapper.
- The graph recursion step never completes, preventing the agent from continuing or
  producing a response.
- The instance appears "stuck" to the user with no feedback.

By contrast, the connection/initialization path already has timeouts:
- Connection establishment: 30s (STDIO), 15s (SSE/Streamable HTTP) — `connection_manager.py:27,103`
- Session initialization: 10s per attempt × 3 retries — `warmup_pool.py:200`
- Health check ping: 5s — `warmup_pool.py:340`

**The actual tool execution is the gap.**

## Proposed Solution

Wrap every MCP tool invocation in `asyncio.timeout()` with a configurable default of **120 seconds**.

### Design

```
                        ┌─────────────────────┐
                        │   ToolNode (LangGraph)  │
                        └─────────┬───────────┘
                                  │ invokes tool coroutine
                                  ▼
                    ┌─────────────────────────────┐
                    │  StructuredTool.coroutine     │
                    │  (= call_tool from adapter)   │
                    └─────────┬───────────────────┘
                              │
           ┌──────────────────┼──────────────────────┐
           │                  │                      │
           ▼                  ▼                      ▼
   ┌───────────────┐  ┌──────────────┐   ┌───────────────────┐
   │ Session-based │  │ Per-call     │   │ asyncio.TimeoutError│
   │ call_tool()   │  │ timeout wrap │   │ → ToolException    │
   │ (no timeout)  │  │ (NEW)        │   │ → graceful error   │
   └───────────────┘  └──────────────┘   └───────────────────┘
```

**Key principle**: Apply the timeout at the adapter layer, not at the session/transport
layer. This keeps the implementation independent of MCP SDK internals and works for all
transport types uniformly.

### Approach: Wrap in `adapt_mcp_tools`

The simplest and most central injection point is `daemon/mcp/tool_adapter.py::adapt_mcp_tools()`.
This function already processes every MCP tool before it reaches the graph. We wrap each
tool's coroutine with a timeout.

**Why here and not elsewhere:**

| Alternative | Why not |
|-------------|---------|
| Patch `langchain_mcp_adapters` | Upstream dependency, can't modify |
| Custom `ToolNode` subclass | Fragile, invasive, LangGraph upgrade risk |
| Per-session timeout in `ManagedClientSession` | `call_tool` is inherited from SDK, hard to override cleanly |
| `asyncio.timeout` in graph.py around `ToolNode` | Times out ALL tools, not just MCP; builtin tools may need different limits |

## Implementation

### 1. Add config field — `daemon/config.py`

Add `tool_call_timeout` to `McpPoolConfig` (reuse existing MCP config section):

```python
class McpPoolConfig(BaseSettings):
    # ... existing fields ...
    tool_call_timeout: int = Field(
        default=120,
        ge=10,
        description="Timeout in seconds for individual MCP tool call executions. "
                    "Applies to all transport types (STDIO, SSE, Streamable HTTP).",
    )
```

Config file usage (`config.yaml`):
```yaml
mcp_pool:
  tool_call_timeout: 120
```

Env var: `MCP_POOL_TOOL_CALL_TIMEOUT=120`

### 2. Create timeout wrapper — `daemon/mcp/tool_adapter.py`

Add a function that wraps a `BaseTool`'s coroutine with `asyncio.timeout`:

```python
import asyncio
import logging
from langchain_core.tools import ToolException

logger = logging.getLogger(__name__)

DEFAULT_TOOL_CALL_TIMEOUT = 120  # seconds


def _wrap_with_timeout(tool: BaseTool, timeout_seconds: float) -> BaseTool:
    """Wrap a tool's coroutine with an asyncio timeout.

    Returns a new tool whose coroutine is the original coroutine
    guarded by asyncio.timeout(). On TimeoutError, raises
    ToolException so LangGraph's ToolNode can handle it gracefully.
    """
    original_coroutine = tool.coroutine

    async def _timed_coroutine(**kwargs):
        try:
            async with asyncio.timeout(timeout_seconds):
                return await original_coroutine(**kwargs)
        except asyncio.TimeoutError:
            tool_name = getattr(tool, "name", "<unknown>")
            logger.error(
                f"MCP tool '{tool_name}' timed out after {timeout_seconds}s"
            )
            raise ToolException(
                f"Tool '{tool_name}' timed out after {timeout_seconds}s. "
                f"The MCP server may be unresponsive."
            )

    timed_tool = tool.copy()
    timed_tool.coroutine = _timed_coroutine
    return timed_tool
```

### 3. Apply wrapper in `adapt_mcp_tools` — `daemon/mcp/tool_adapter.py`

```python
def adapt_mcp_tools(
    server_name: str,
    tools: list[BaseTool],
    tool_call_timeout: float = DEFAULT_TOOL_CALL_TIMEOUT,
) -> list[BaseTool]:
    # ... existing name/description adaptation ...
    for tool in tools:
        adapted_tool = tool.copy()
        adapted_tool.name = new_name
        adapted_tool.description = new_description
        adapted_tools.append(adapted_tool)

    # Wrap with timeout (applies to all adapted tools)
    if tool_call_timeout > 0:
        adapted_tools = [
            _wrap_with_timeout(t, tool_call_timeout) for t in adapted_tools
        ]

    return adapted_tools
```

### 4. Thread config through — `daemon/services/mcp_service.py`

Pass the config value from `McpPoolConfig.tool_call_timeout` to `adapt_mcp_tools`:

- `_discover_server_tools()`: Read `tool_call_timeout` from the manager's config and
  pass it to `adapt_mcp_tools()`.
- Warmup pool path (`warmup_pool.py::_create_pooled_connection`): Same — read config
  and pass to `adapt_mcp_tools()`.

### 5. Files to modify

| File | Change |
|------|--------|
| `daemon/config.py` | Add `tool_call_timeout` to `McpPoolConfig` |
| `daemon/mcp/tool_adapter.py` | Add `_wrap_with_timeout()`, update `adapt_mcp_tools()` signature |
| `daemon/services/mcp_service.py` | Pass `tool_call_timeout` from config to `adapt_mcp_tools()` |
| `daemon/mcp/warmup_pool.py` | Pass `tool_call_timeout` from config to `adapt_mcp_tools()` |

### 6. Tests

| Test | File | What it verifies |
|------|------|-----------------|
| Unit: timeout fires | `tests/unit/test_mcp_tool_timeout.py` (new) | Mock a tool coroutine that sleeps > timeout; assert `ToolException` raised |
| Unit: success under timeout | same | Mock a fast coroutine; assert result passes through |
| Unit: config passthrough | same | Verify `adapt_mcp_tools` receives and applies the config value |
| Unit: zero/disabled timeout | same | `tool_call_timeout=0` skips wrapping (no timeout) |
| Unit: config field validation | same | `tool_call_timeout < 10` raises validation error |
| Integration: end-to-end in ToolNode | same | Adapted MCP tool in a LangGraph `ToolNode` times out gracefully, returns `ToolMessage` with error |

### 7. Error behavior

When a tool times out:

1. `asyncio.TimeoutError` is caught inside the wrapper.
2. A `ToolException` is raised with a descriptive message.
3. LangGraph's `ToolNode` catches `ToolException` and returns a `ToolMessage` with
   `status="error"` containing the timeout message.
4. The agent node receives the error `ToolMessage` and can retry, use a different
   approach, or inform the user.
5. The graph continues — no indefinite hang.

## Considerations

- **120s default**: Most MCP tool calls (file reads, searches, API calls) complete in
  under 30s. 120s provides headroom for slow external services (e.g., `zread` fetching
  large repositories) while still preventing indefinite hangs.
- **Per-server override**: Future extension could allow per-server timeout via
  `McpPoolConfig.servers` dict. Not included in initial implementation to keep scope
  minimal.
- **Streaming tools**: If an MCP tool streams partial results, `asyncio.timeout`
  measures wall-clock time from call to return. A streaming tool that continuously
  produces output won't timeout as long as the total execution stays under the limit.
- **Connection vs execution timeout**: Connection timeout (existing) and tool call
  timeout (this change) are independent. A slow connection that succeeds within its
  timeout still gets a tool call timeout for subsequent operations.
