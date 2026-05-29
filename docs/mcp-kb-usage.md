# MCP KB Tools Server — Developer Guide

Connect external agent systems to the Ensemble Knowledge Base via MCP (Model Context Protocol).

## 1. Quick Start

### Connection

```python
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession

MCP_URL = "http://localhost:8079/api/mcp/kb/mcp"

async with streamable_http_client(MCP_URL) as (read_stream, write_stream, _):
    async with ClientSession(read_stream, write_stream) as session:
        await session.initialize()
        # Ready to use tools
```

### Complete Minimal Example

```python
import asyncio
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession

async def main():
    MCP_URL = "http://localhost:8079/api/mcp/kb/mcp"
    PROJECT_ID = "your-project-uuid"

    async with streamable_http_client(MCP_URL) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

            # Explore the knowledge base
            result = await session.call_tool(
                "ensemble_kb_explore",
                {"query": "How does authentication work?", "project_id": PROJECT_ID}
            )
            print(result.content[0].text)

            # Record new knowledge
            result = await session.call_tool(
                "ensemble_kb_experience",
                {"text": "Auth requires API key in Authorization header", "project_id": PROJECT_ID}
            )
            print(result.content[0].text)  # "Knowledge recording started."

asyncio.run(main())
```

## 2. Tool Reference

### ensemble_kb_explore

Search the knowledge base for relevant information.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | — | Search query or question |
| `project_id` | string | Yes | — | Project UUID to search within |
| `mode` | string | No | "hybrid" | Search mode (see below) |

**Returns:** Search results as string.

### ensemble_kb_experience

Record new knowledge into the knowledge base (fire-and-forget).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | string | Yes | — | Knowledge to record (facts, findings, patterns) |
| `project_id` | string | Yes | — | Project UUID to record under |

**Returns:** `"Knowledge recording started."` or error message.

## 3. Transport Options

| Transport | Endpoint | Best For |
|-----------|----------|----------|
| **StreamableHTTP** | `/api/mcp/kb/mcp` | Recommended — modern, stateless, async-friendly |
| **SSE** | `/api/mcp/kb/sse/sse` | Alternative transport for clients that only support SSE |

Use StreamableHTTP unless you have a specific requirement for SSE.

```python
# StreamableHTTP (recommended)
async with streamable_http_client("http://localhost:8079/api/mcp/kb/mcp") as (...):
    ...

# SSE fallback
from mcp.client.sse import sse_client
async with sse_client("http://localhost:8079/api/mcp/kb/sse/sse") as (...):
    ...
```

## 4. Prerequisites

- **Ensemble daemon running** on port 8079 (default dev port)
- **RAG enabled** — `is_rag_enabled()` must return true (check server logs on startup)
- **Valid `project_id`** — UUID of an existing project (obtain via `/api/projects`)
- **FastMCP server name:** `ensemble-kb`

## 5. Query Modes

| Mode | When to Use |
|------|-------------|
| `local` | Find specific relevant chunks — best for precise lookups |
| `global` | Understand overall themes — best for summaries and broad topics |
| `hybrid` | Combines local + global (default, recommended for most cases) |
| `naive` | Simple keyword search — fastest but least intelligent |

```python
# Example: Global mode for topic overview
result = await session.call_tool(
    "ensemble_kb_explore",
    {"query": "What are the main patterns in this codebase?", "project_id": PROJECT_ID, "mode": "global"}
)

# Example: Local mode for specific facts
result = await session.call_tool(
    "ensemble_kb_explore",
    {"query": "What is the retry policy?", "project_id": PROJECT_ID, "mode": "local"}
)
```

## 6. Error Handling

Tools return **error strings** (not exceptions). Always check the response:

```python
result = await session.call_tool("ensemble_kb_explore", {...})
text = result.content[0].text

if text.startswith("Error:"):
    print(f"Failed: {text}")
else:
    print(f"Success: {text}")
```

### Common Errors

| Error Message | Cause | Fix |
|---------------|-------|-----|
| `Error: KB MCP server not initialized.` | Server still starting up | Wait and retry |
| `Error: project_id is required.` | Missing project_id parameter | Provide valid UUID |
| `Error: Knowledge base (RAG) is not enabled.` | RAG not configured | Enable RAG in config |
| `Error: Invalid mode 'xxx'.` | Invalid mode value | Use: local, global, hybrid, naive |
| `Explorer agent timed out or failed.` | Query too complex | Simplify query |
| `Error: An internal error occurred...` | Unexpected server error | Check server logs |
| `Connection refused` | Server not running | Start daemon with `./dev.sh` |

## 7. Integration Tips

### LangChain Agents

```python
from langchain.tools import Tool
from your_mcp_client import get_mcp_session

def explore_tool(query: str, project_id: str) -> str:
    # Call MCP ensemble_kb_explore
    ...

tools = [
    Tool(
        name="kb_explore",
        func=lambda x: explore_tool(**json.loads(x)),
        description="Search the knowledge base"
    )
]
```

### Custom Agent Frameworks

```python
async def agent_loop(agent, session):
    # 1. Decide next action using LLM
    decision = await agent.decide()

    # 2. If decision involves KB lookup
    if decision.tool == "kb_explore":
        result = await session.call_tool(
            "ensemble_kb_explore",
            {"query": decision.query, "project_id": PROJECT_ID}
        )
        agent.observe(result.content[0].text)

    # 3. If decision involves recording knowledge
    elif decision.tool == "kb_experience":
        result = await session.call_tool(
            "ensemble_kb_experience",
            {"text": decision.knowledge, "project_id": PROJECT_ID}
        )
        agent.observe(result.content[0].text)
```

### Key Points

- **Keep sessions short-lived** — StreamableHTTP is stateless; create/fetch/destroy
- **Handle errors gracefully** — Tools return strings, not exceptions
- **Use `project_id` consistently** — All operations scope to a project
- **Experience is async** — Fire-and-forget; don't wait for confirmation

## Running Tests

```bash
# Start dev server
./dev.sh

# In another terminal, run E2E tests
RUN_E2E_TESTS=1 E2E_PROJECT_ID=<your-uuid> pytest tests/e2e/test_mcp_kb_e2e.py -v
```
