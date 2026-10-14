# Tool System Architecture — agents-ensemble

**Explored:** 2025-04-25
**Purpose:** Complete understanding of tool system mechanics for adding new tools.

---

## Tool Registration Pattern

**Two-level decorator system** in `daemon/tools/_tool_registry.py`:

1. **`@register_tool_category(category)`** — marks a tool as belonging to a category
2. **`@register_tool(tool_name, category, short_doc, full_doc)`** — registers metadata (optional, for tool_help)
3. **`@tool`** (from `langchain_core.tools`) — LangChain's native tool decorator (required)

### CATEGORY_MODULES Registry (9 categories)

```python
CATEGORY_MODULES = {
    "bash": "daemon.tools.bash",
    "filesystem": "daemon.tools.filesystem",
    "time": "daemon.tools.time",
    "instance": "daemon.tools.instance",
    "self": ["daemon.tools.inner_soul", "daemon.tools.access_memory"],
    "project": "daemon.tools.project",
    "job": "daemon.tools.job_queue",
    "help": "daemon.tools.help",
    "mother": "daemon.tools.agent_mother",
}
```

## How to Add a New Tool

1. Create tool file in `daemon/tools/` (e.g., `daemon/tools/my_tool.py`)
2. Use decorator pattern: `@register_tool_category("category")` + `@tool`
3. For complex inputs, use Pydantic `BaseModel` with `args_schema=`
4. For dependency injection, use factory pattern: `def create_xxx_tools(manager, ...) -> list`
5. Register category in `CATEGORY_MODULES` dict
6. Import and add tools in `daemon/tools/instance.py:create_instance_tools()`
7. Tool filtering is automatic via agent's `meta.json` tools.allow/deny

## Tool Assignment to Agents

Agents define tool access in `meta.json`:
```json
{
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help"],
    "deny": ["write_file", "edit_file"]
  }
}
```

`allow`/`deny` can reference category names OR individual tool names.
Resolution: `resolve_tool_filter()` expands categories → individual tool names.
Filtering: `_apply_tool_filter()` filters the full tool list.

## 58 Existing Tools by Category

| Category | Count | Tools |
|----------|-------|-------|
| bash | 1 | bash |
| filesystem | 6 | list_directory, read_file, write_file, glob_files, grep_files, edit_file |
| time | 1 | time |
| instance | 5 | spawn_instance, send_message, terminate_instance, list_instances, get_instance_info |
| project | 21 | project_create, project_get, project_list, project_search, + 17 more |
| self | 2 | inner_soul, access_memory |
| job | 16 | job_create, job_get, job_list, job_cancel, job_retry, dlq_list, dlq_replay, watch_job, + more |
| help | 1 | tool_help |
| mother | 5 | agent_list, agent_create, agent_read, agent_modify, agent_delete |

## Memory System (No RAG)

- **File-based**: Markdown files in `agents/{agent-id}/memories/`
- **Retrieval**: Timestamp-sorted filenames only (no vector search)
- **access_memory tool**: Read specific memory file by name
- **inner_soul tool**: Semantic classification (regex-based) of experiences → writes to appropriate files
- **No vector DB, no embeddings, no semantic search**

## Event System

Two event buses:
1. **EventBus** (`daemon/services/event_bus.py`): SSE delivery, DB persistence, checkpoint events
2. **DispatchEventBus** (`daemon/services/dispatch_event_bus.py`): Job dispatch signaling, in-process only

Events are stored in SQLite `event` table. Agents cannot directly subscribe to events.

## Agent Definition Files

```
agents/{agent-id}/
├── meta.json       # Config (id, name, tools, innate_skills, version)
├── soul.md         # Identity/personality
├── rule.md         # Hard constraints (MUST/MUST NOT)
├── workflow.md     # Methodology
├── memory.md       # Knowledge (max 500 words)
├── memories/       # Timestamped memory files
├── tools_note.md   # Agent-specific tool docs
├── knowledge.md    # Domain knowledge
├── growth.md       # Learning rules
└── user.md         # User preferences
```

## Instance Lifecycle (Spawning)

1. Registry resolves agent_id → AgentMetadata (path, tools config)
2. Loader composes system prompt from agent files + skills + dynamic tools doc
3. `create_instance_tools()` creates ALL tools, then filters by agent's meta.json
4. `build_instance_graph()` binds LLM to filtered tools → LangGraph state machine
5. Instance stored in memory + persisted to DB

## Tool Execution Flow

```
LLM → AIMessage.tool_calls → should_continue() → "tools" route
→ ToolNode executes each tool_call → ToolMessage → back to agent node
```

## Workdir Pattern

Tools like filesystem/bash are wrapped with `_make_workdir_aware()` to auto-populate workdir parameter. This ensures tools operate in the correct project directory.
