# Phase 3: RAG Tools & Knowledge Tools

## Objective

Create the tool layer that connects agents to LightRAG. This includes 15 RAG tools (wrapping LightRAG API endpoints) in a new `rag` tool category assigned only to explorer/experiencer agents, plus 2 knowledge tools (`explore()` and `experience()`) in a new `knowledge` category available to ALL agents. Also register new tool categories in the registry and wire tools into the instance tool creation flow.

## Coupling

- **Depends on**: Phase 1 (CompletionRegistry + `invoke_agent_and_wait()`), Phase 2 (AsyncLightRAGClient)
- **Coupling type**: tight (both) — directly imports from both phases
- **Shared files with other phases**:
  - `daemon/tools/rag_tools.py` — used by Phase 4 (explorer) and Phase 5 (experiencer) via tool filtering
  - `daemon/tools/knowledge_tools.py` — used by ALL agents via tool assignment
  - `daemon/tools/_tool_registry.py` — modified to add categories
  - `daemon/tools/instance.py` — modified to wire in new tools
- **Why this coupling**: RAG tools directly import `AsyncLightRAGClient`; knowledge tools directly call `invoke_agent_and_wait()`

## Context

### Tool Registration Pattern

From the codebase:
- Tools use `@register_tool_category("category")` + `@tool` decorators
- `CATEGORY_MODULES` dict in `_tool_registry.py` maps category → module path
- `create_instance_tools()` in `instance.py` builds tool list per agent
- Tool filtering via `meta.json` `allow/deny` controls what each agent gets
- Context (manager, instance_id) passed via closure pattern in `create_*_tools()` factory functions

### Key Architecture Decision

**RAG tools** (rag_insert_text, rag_query, etc.) are LOW-LEVEL wrappers around LightRAG API. Only assigned to explorer/experiencer agents.

**Knowledge tools** (explore, experience) are HIGH-LEVEL tools that spawn agents. Assigned to ALL agents.

This separation means:
- Regular agents never see RAG API details — they just call `explore()`/`experience()`
- Explorer/experiencer agents get both their RAG tools AND can use other tools (filesystem, etc.)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create RAG tools module | 15 tools wrapping LightRAG API endpoints with `rag` category | `daemon/tools/rag_tools.py` |
| 2 | Create knowledge tools module | explore() and experience() tools with `knowledge` category | `daemon/tools/knowledge_tools.py` |
| 3 | Register new tool categories | Add `rag` and `knowledge` to CATEGORY_MODULES | `daemon/tools/_tool_registry.py` |
| 4 | Wire tools into instance creation | Add RAG and knowledge tools to `create_instance_tools()` | `daemon/tools/instance.py` |
| 5 | Write unit tests | Test tool wrappers with mocked client, test knowledge tools with mocked invoke | `tests/unit/tools/test_rag_tools.py`, `tests/unit/tools/test_knowledge_tools.py` |

### Task 3.1: Create RAG Tools Module

**File**: `daemon/tools/rag_tools.py` (NEW)

**Design**: Factory function `create_rag_tools(manager, instance_id)` that creates tool instances with closure access to manager context.

**Category**: `rag`
**Tools** (15):

| Tool Name | LightRAG Endpoint | Purpose |
|-----------|-------------------|---------|
| `rag_insert_text` | POST /documents/text | Insert text document |
| `rag_insert_texts` | POST /documents/texts | Bulk insert texts |
| `rag_query` | POST /query | LLM-powered query |
| `rag_query_data` | POST /query/data | Structured data query (no LLM) |
| `rag_search_labels` | GET /graph/label/search | Search graph labels |
| `rag_get_graph` | GET /graphs | Get subgraph |
| `rag_create_entity` | POST /graph/entity/create | Create entity |
| `rag_create_relation` | POST /graph/relation/create | Create relation |
| `rag_update_entity` | POST /graph/entity/update | Update entity |
| `rag_merge_entities` | POST /graph/entity/merge | Merge entities |
| `rag_delete_entity` | DELETE /documents/delete_entity | Delete entity |
| `rag_delete_relation` | DELETE /documents/delete_relation | Delete relation |
| `rag_delete_docs` | DELETE /documents/delete_document | Delete documents |
| `rag_list_docs` | POST /documents/paginated | List documents (paginated) |
| `rag_track_status` | GET /documents/track_status/{id} | Track insertion status |

**Implementation Pattern**:

```python
CATEGORY_NAME = "RAG"
CATEGORY_DOC = """RAG knowledge management tools for interacting with LightRAG.
These tools allow querying, inserting, and managing knowledge in the RAG system."""

# Module-level shared client
_rag_client: AsyncLightRAGClient | None = None


def _get_rag_client() -> AsyncLightRAGClient:
    global _rag_client
    if _rag_client is None:
        _rag_client = AsyncLightRAGClient()
    return _rag_client


def create_rag_tools(manager, current_instance_id: str) -> list:
    """Create RAG tools with context injection."""
    
    @register_tool_category("rag")
    @tool
    async def rag_insert_text(text: str, description: str = "") -> str:
        """Insert text into the RAG knowledge base.
        
        Args:
            text: The text content to insert.
            description: Optional description of the text.
            
        Returns:
            Track ID for monitoring insertion status.
        """
        client = _get_rag_client()
        if not client.is_available:
            return "Error: RAG is not configured. Set LIGHTRAG_HOST environment variable."
        try:
            result = await client.insert_text(text=text, description=description)
            return f"Text inserted successfully. Track ID: {result.track_id}"
        except RAGError as e:
            return f"RAG error: {e}"
    
    @register_tool_category("rag")
    @tool
    async def rag_query(query: str, mode: str = "hybrid") -> str:
        """Query the RAG knowledge base using LLM.
        
        Args:
            query: The question or search query.
            mode: Query mode - "local", "global", "hybrid", or "naive".
            
        Returns:
            The query response from the knowledge base.
        """
        client = _get_rag_client()
        if not client.is_available:
            return "Error: RAG is not configured."
        try:
            result = await client.query(query=query, mode=mode)
            return result.response
        except RAGError as e:
            return f"RAG error: {e}"
    
    # ... all other RAG tools following same pattern
    
    return [
        rag_insert_text,
        rag_insert_texts,
        rag_query,
        rag_query_data,
        rag_search_labels,
        rag_get_graph,
        rag_create_entity,
        rag_create_relation,
        rag_update_entity,
        rag_merge_entities,
        rag_delete_entity,
        rag_delete_relation,
        rag_delete_docs,
        rag_list_docs,
        rag_track_status,
    ]
```

**Key Design Decisions**:
- All tools return **strings** (consistent with existing tool pattern)
- Errors caught and returned as user-friendly strings (not raised)
- `is_available` check first — graceful degradation
- Shared `AsyncLightRAGClient` instance (lazy-initialized singleton)
- Project context from instance's project_id (auto-injected by knowledge tools layer)

### Task 3.2: Create Knowledge Tools Module

**File**: `daemon/tools/knowledge_tools.py` (NEW)

**Category**: `knowledge`
**Tools** (2):

| Tool Name | Purpose | Blocking? |
|-----------|---------|-----------|
| `explore` | Query project knowledge (spawns explorer, WAITS) | Yes — synchronous |
| `experience` | Record knowledge (spawns experiencer, returns immediately) | No — fire-and-forget |

```python
CATEGORY_NAME = "Knowledge"
CATEGORY_DOC = """Knowledge management tools for exploring and recording project knowledge.

explore() queries the project knowledge base using the Explorer agent.
experience() records new knowledge using the Experiencer agent.
"""


def create_knowledge_tools(manager, current_instance_id: str) -> list:
    """Create knowledge tools available to ALL agents."""
    
    def _get_project_id() -> str | None:
        """Auto-inject project_id from instance context."""
        instance = manager.get_instance(current_instance_id)
        return instance.project_id if instance else None
    
    @register_tool_category("knowledge")
    @tool
    async def explore(query: str, mode: str = "hybrid", project_id: str | None = None) -> str:
        """Explore project knowledge using the Explorer agent.

        Sends a query to the Explorer agent, which searches the RAG knowledge base
        and optionally browses project files to find relevant information.

        Args:
            query: The question or topic to explore.
            mode: Query mode - "local", "global", "hybrid", or "naive". Defaults to "hybrid".
            project_id: Optional project ID. Auto-detected from context if not provided.
            
        Returns:
            The explorer agent's response with relevant knowledge.
        """
        pid = project_id or _get_project_id()
        
        # Build message for explorer agent
        explorer_message = f"Query (mode={mode}): {query}"
        if pid:
            explorer_message += f"\nProject: {pid}"
        
        result = await invoke_agent_and_wait(
            manager=manager,
            agent_id="explorer",
            message=explorer_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=f"explore-{query[:30]}",
            timeout=300.0,
        )
        
        if result is None:
            return "Explorer agent timed out or failed. Try a simpler query."
        return result
    
    @register_tool_category("knowledge")
    @tool
    async def experience(text: str, project_id: str | None = None) -> str:
        """Record new knowledge using the Experiencer agent.

        Analyzes the text, extracts entities and relationships,
        and inserts them into the RAG knowledge base. Runs in background.

        Args:
            text: The knowledge text to record (facts, findings, patterns, etc.)
            project_id: Optional project ID. Auto-detected from context if not provided.
            
        Returns:
            Confirmation that knowledge recording has started.
        """
        pid = project_id or _get_project_id()
        
        # Build message for experiencer agent
        experiencer_message = f"Process and record the following knowledge:\n\n{text}"
        if pid:
            experiencer_message += f"\nProject: {pid}"
        
        # Fire-and-forget: spawn instance and enqueue message
        import asyncio
        instance_id = manager.spawn_instance(
            agent_id="experiencer",
            parent_id=current_instance_id,
            project_id=pid,
            instance_name=f"experience-{text[:30]}",
        )
        await manager.enqueue_message(
            instance_id=instance_id,
            message=experiencer_message,
            source=f"experience:{current_instance_id}",
        )
        
        return f"Knowledge recording started. Instance: {instance_id[:8]}..."
    
    return [explore, experience]
```

### Task 3.3: Register New Tool Categories

**File**: `daemon/tools/_tool_registry.py` (MODIFIED)

Add to `CATEGORY_MODULES`:
```python
CATEGORY_MODULES = {
    # ... existing entries ...
    "rag": "daemon.tools.rag_tools",
    "knowledge": "daemon.tools.knowledge_tools",
}
```

### Task 3.4: Wire Tools into Instance Creation

**File**: `daemon/tools/instance.py` (MODIFIED)

In `create_instance_tools()`, add RAG and knowledge tools:

```python
from .rag_tools import create_rag_tools
from .knowledge_tools import create_knowledge_tools

def create_instance_tools(manager, current_instance_id, agent_id=""):
    tools = [
        # ... existing base tools ...
    ]
    
    # RAG tools (only if agent allows "rag" category)
    rag_tools = create_rag_tools(manager, current_instance_id)
    tools.extend(rag_tools)
    
    # Knowledge tools (available to all agents)
    knowledge_tools_list = create_knowledge_tools(manager, current_instance_id)
    tools.extend(knowledge_tools_list)
    
    # ... rest of existing logic (mother tools, help tool) ...
    
    # Tool filtering from meta.json will handle actual availability
    tools = _apply_tool_filter(tools, agent_id)
    
    return tools
```

**Important**: Both tool sets are added BEFORE `_apply_tool_filter()`. The filtering step (based on agent's `meta.json` `allow/deny`) handles actual availability:
- Explorer agent: `allow: ["rag", "filesystem", "help"]` → gets RAG tools
- Experiencer agent: `allow: ["rag", "help"]` → gets RAG tools
- All other agents: `allow: [..., "knowledge"]` → gets explore/experience tools

### Task 3.5: Write Unit Tests

**File**: `tests/unit/tools/test_rag_tools.py` (NEW)

Test cases:
1. `test_rag_tools_created` — factory returns 15 tools
2. `test_rag_insert_text_success` — mock client, verify output
3. `test_rag_insert_text_not_configured` — graceful error message
4. `test_rag_query_success` — mock client, verify mode passed
5. `test_rag_query_error` — verify error string returned

**File**: `tests/unit/tools/test_knowledge_tools.py` (NEW)

Test cases:
1. `test_knowledge_tools_created` — factory returns 2 tools
2. `test_explore_calls_invoke_and_wait` — mock invoke_agent_and_wait
3. `test_explore_timeout_returns_error_message` — verify graceful degradation
4. `test_experience_spawns_and_returns_immediately` — verify fire-and-forget
5. `test_experience_auto_injects_project_id` — verify context injection

## Key Files

- `daemon/tools/rag_tools.py` — **NEW**: 15 RAG tools (rag category)
- `daemon/tools/knowledge_tools.py` — **NEW**: explore/experience (knowledge category)
- `daemon/tools/_tool_registry.py` — **MODIFIED**: Add rag, knowledge to CATEGORY_MODULES
- `daemon/tools/instance.py` — **MODIFIED**: Wire in create_rag_tools(), create_knowledge_tools()
- `tests/unit/tools/test_rag_tools.py` — **NEW**: RAG tool tests
- `tests/unit/tools/test_knowledge_tools.py` — **NEW**: Knowledge tool tests

## Constraints

1. **All tools return strings** — consistent with existing tool pattern
2. **Error as string, not exception** — tools catch errors and return user-friendly messages
3. **Graceful degradation** — if LightRAG not configured, tools return error string, don't crash
4. **Tool filtering** — RAG tools only available to agents with "rag" in allow list
5. **Knowledge tools for all** — "knowledge" category added to all agents' default tools
6. **explore() blocking** — uses invoke_agent_and_wait with 300s timeout
7. **experience() non-blocking** — uses spawn + enqueue, returns immediately
8. **project_id auto-injection** — same pattern as existing tools (from instance context)

## Deliverables

- [ ] `daemon/tools/rag_tools.py` — 15 RAG tools with factory function
- [ ] `daemon/tools/knowledge_tools.py` — explore() and experience() tools
- [ ] `daemon/tools/_tool_registry.py` — rag and knowledge categories registered
- [ ] `daemon/tools/instance.py` — tools wired into create_instance_tools()
- [ ] `tests/unit/tools/test_rag_tools.py` — RAG tool tests passing
- [ ] `tests/unit/tools/test_knowledge_tools.py` — Knowledge tool tests passing
- [ ] Existing tests still pass

## Verification

```bash
# Verify tool registration
python -c "from daemon.tools.rag_tools import create_rag_tools; print('OK')"
python -c "from daemon.tools.knowledge_tools import create_knowledge_tools; print('OK')"

# Run tests
pytest tests/unit/tools/test_rag_tools.py -v
pytest tests/unit/tools/test_knowledge_tools.py -v

# Verify categories registered
python -c "from daemon.tools._tool_registry import CATEGORY_MODULES; print(CATEGORY_MODULES.get('rag'), CATEGORY_MODULES.get('knowledge'))"
```
