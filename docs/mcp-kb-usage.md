# MCP KB Tools Server — Developer Guide

Connect external agent systems to the Ensemble Knowledge Base via MCP (Model Context Protocol).

## 1. OpenCode Integration

### Configuration

OpenCode uses **JSON** config (not YAML). Add the MCP server to your config file.

**Config file locations** (pick one):
- **User-level:** `~/.config/opencode/opencode.json` — applies to all projects
- **Project-level:** `.opencode.json` in your project root — project-specific

**Minimal config — StreamableHTTP (recommended):**
```json
{
  "mcp": {
    "ensemble-kb": {
      "type": "remote",
      "url": "http://localhost:8079/api/mcp/kb/mcp",
      "enabled": true
    }
  }
}
```

**SSE transport (alternative):**
```json
{
  "mcp": {
    "ensemble-kb": {
      "type": "sse",
      "url": "http://localhost:8079/api/mcp/kb/sse/sse",
      "enabled": true
    }
  }
}
```

> **Note:** If your config already has other fields (model, provider, etc.), just add the `"mcp"` key alongside them. Don't remove existing settings.

**Full example with typical OpenCode settings:**
```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-sonnet-4-20250514",
  "mcp": {
    "ensemble-kb": {
      "type": "remote",
      "url": "http://localhost:8079/api/mcp/kb/mcp",
      "enabled": true
    }
  }
}
```

### Usage

Once configured, the agent automatically has access to four tools:
- `ensemble_kb_explore` — search the knowledge base
- `ensemble_kb_experience` — record new knowledge
- `ensemble_kb_list_projects` — list all available projects
- `ensemble_kb_search_projects` — fuzzy search projects by name

**Using `project_name` (recommended):**

After config, use the project's name or shortname instead of its UUID:

```
# Agent searches the knowledge base by project name
ensemble_kb_explore(query="How does authentication work?", project_name="agents-ensemble")

# Or use a shortname
ensemble_kb_explore(query="database schema", project_name="ens")
```

**Using `project_id` (UUID):**

```
ensemble_kb_explore(query="How does authentication work?", project_id="83da04de-a410-4fb5-9e92-251a99d28a52")
```

**Discovering projects:**

Not sure which projects are available? List them first:

```
ensemble_kb_list_projects()
ensemble_kb_search_projects(query="ensemble")
```

### Prerequisites

- Ensemble daemon running on port 8079
- A valid project identifier (name, shortname, or UUID — discover via `list_projects`)

## 2. Quick Start (Python SDK)

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
    PROJECT_NAME = "my-project"  # Use name, shortname, or UUID

    async with streamable_http_client(MCP_URL) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

            # Discover available projects
            result = await session.call_tool("ensemble_kb_list_projects", {})
            print(result.content[0].text)

            # Explore the knowledge base
            result = await session.call_tool(
                "ensemble_kb_explore",
                {"query": "How does authentication work?", "project_name": PROJECT_NAME}
            )
            print(result.content[0].text)

            # Record new knowledge
            result = await session.call_tool(
                "ensemble_kb_experience",
                {"text": "Auth requires API key in Authorization header", "project_name": PROJECT_NAME}
            )
            print(result.content[0].text)  # "Knowledge recording started."

asyncio.run(main())
```

## 3. Tool Reference

### ensemble_kb_explore

Search the knowledge base for relevant information.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | — | Search query or question |
| `project_id` | string | No* | — | Project UUID |
| `project_name` | string | No* | — | Project name or shortname |
| `project_path` | string | No* | — | Project main directory path |
| `mode` | string | No | "hybrid" | Search mode (see below) |

*At least one project identifier required.

**Returns:** Search results as string.

### ensemble_kb_experience

Record new knowledge into the knowledge base (fire-and-forget).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | string | Yes | — | Knowledge to record (facts, findings, patterns) |
| `project_id` | string | No* | — | Project UUID |
| `project_name` | string | No* | — | Project name or shortname |
| `project_path` | string | No* | — | Project main directory path |

*At least one project identifier required.

**Returns:** `"Knowledge recording started."` or error message.

### ensemble_kb_list_projects

List all available projects in the knowledge base.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `limit` | int | No | 50 | Maximum projects to return |
| `offset` | int | No | 0 | Number of projects to skip |
| `status` | string | No | None | Filter by status (e.g. "active") |

**Returns:** JSON array of projects with `id`, `name`, `shortnames`, `main_directory`, `status`, `tags`.

### ensemble_kb_search_projects

Search projects by query (fuzzy matching on name/shortname).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | — | Search query |
| `limit` | int | No | 20 | Maximum results |

**Returns:** JSON array of matching projects.

### Project Resolution

The explore and experience tools can identify a project in three ways:

1. **project_id** — Exact UUID match. If not found, fuzzy matching suggests similar IDs.
2. **project_name** — Matches against project name or any shortname. Fuzzy matching tolerates typos.
3. **project_path** — Matches against the project's main directory. Handles partial paths.

#### Examples

**By name (recommended for external tools):**
```python
result = await session.call_tool("ensemble_kb_explore", {
    "query": "architecture",
    "project_name": "agents-ensemble"
})
```

**By shortname:**
```python
result = await session.call_tool("ensemble_kb_explore", {
    "query": "database", 
    "project_name": "ens"  # shortname for agents-ensemble
})
```

**By path:**
```python
result = await session.call_tool("ensemble_kb_experience", {
    "text": "Uses SQLite with SQLAlchemy ORM",
    "project_path": "/Users/me/projects/agents-ensemble"
})
```

**Fuzzy matching — "Did you mean?":**
If a project name has a typo, the server suggests the closest match:
```
Error: Project 'agents-ensamble' not found. Did you mean 'agents-ensemble' (ens, ensemble) (83da04de-...)?
```

#### Discovery workflow

1. `ensemble_kb_list_projects` — See all available projects
2. `ensemble_kb_search_projects` — Search by keyword  
3. Use the returned name/id/path with explore/experience

## 4. Transport Options

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

## 5. Prerequisites

- **Ensemble daemon running** on port 8079 (default dev port)
- **RAG enabled** — `is_rag_enabled()` must return true (check server logs on startup)
- **At least one project identifier** — `project_id`, `project_name`, or `project_path`. Use `list_projects` to discover available projects.
- **FastMCP server name:** `ensemble-kb`

## 6. Query Modes

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

## 7. Error Handling

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

## 8. Integration Tips

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
- **Use any project identifier** — `project_name`, `project_id`, or `project_path` all work
- **Experience is async** — Fire-and-forget; don't wait for confirmation

## 9. Running Tests

```bash
# Start dev server
./dev.sh

# In another terminal, run E2E tests
RUN_E2E_TESTS=1 E2E_PROJECT_ID=<your-uuid> pytest tests/e2e/test_mcp_kb_e2e.py -v
```
