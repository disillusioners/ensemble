# Phase 2: Agent Tool

## Objective

Create a new `shared_context_metadata` agent tool that supports batch CRUD operations (set, delete, list) on context-key-scoped metadata. Register it in the tool registry and make it available to the leader agent.

## Coupling

- **Depends on**: Phase 1 (Storage Layer)
- **Coupling type**: tight — imports `SharedContextMetadataRepository` from Phase 1
- **Shared files with other phases**: 
  - `daemon/tools/_tool_registry.py` — adds category to `CATEGORY_MODULES`
  - `daemon/tools/instance.py` — wires factory into `create_instance_tools()`
  - `agents/leader/meta.json` — adds `"context_metadata"` to `tools.allow`
- **Shared APIs/interfaces**: Uses `manager.get_shared_context_repository()` to access the repository
- **Why this coupling**: Tool needs the repository class from Phase 1 to perform DB operations

## Context

- **Previous phase completed**: `SharedContextMetadataRepository` with `batch_upsert()`, `batch_delete()`, `list_records()`, `get_all_as_dict()` methods
- **Tool pattern**: Follows `@register_tool_category()` + `@tool` + `_full_doc_` pattern (see `chart_tools.py`, `context_tools.py` as reference)
- **Factory pattern**: `create_<name>_tools(manager, current_instance_id) -> list` — manager is injected for repository access
- **Key decision**: Tool uses `current_instance_id` to resolve `context_key` via `instance_repository.get_tree_root_id()` — the agent does NOT need to pass context_key manually

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create tool module | `daemon/tools/shared_context_tools.py` with `create_shared_context_tools(manager, current_instance_id)` factory. Single tool `shared_context_metadata` accepting `operations` JSON. | `daemon/tools/shared_context_tools.py` |
| 2 | Register category | Add `"context_metadata": "daemon.tools.shared_context_tools"` to `CATEGORY_MODULES` dict | `daemon/tools/_tool_registry.py:184` |
| 3 | Wire into create_instance_tools | Import factory, call `create_shared_context_tools(manager, current_instance_id)`, extend tools list (near line 1038, alongside `context_tool_list`) | `daemon/tools/instance.py` |
| 4 | Add to leader's tools.allow | Add `"context_metadata"` to the `tools.allow` array in leader's meta.json | `agents/leader/meta.json` |
| 5 | (Optional) Add to other spawning agents | If planner/developer/etc. should also set metadata, add to their meta.json. Default: leader only. | `agents/*/meta.json` |

## Key Files

### New File: `daemon/tools/shared_context_tools.py`

```python
"""Shared context metadata tools for batch CRUD on context-key-scoped KV pairs."""
import json
import logging
import asyncio
from typing import TYPE_CHECKING
from langchain_core.tools import tool
from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Context Metadata"
CATEGORY_DOC = """\
Context metadata tools for batch CRUD on key-value metadata scoped by context_key.
Metadata is automatically injected into ALL team members' system prompts.
"""


def create_shared_context_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create shared context metadata tools with injected manager reference."""

    @register_tool_category("context_metadata")
    @tool
    async def shared_context_metadata(operations: str) -> str:
        """Batch CRUD on shared context metadata KV pairs. Use tool_help("shared_context_metadata") for details."""
        try:
            ops = json.loads(operations) if isinstance(operations, str) else operations
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON in operations: {e}"

        if not isinstance(ops, list) or len(ops) == 0:
            return "Error: operations must be a non-empty JSON array."

        # Resolve context_key from current instance
        instance_repo = manager.get_instance_repository()
        root_id = instance_repo.get_tree_root_id(current_instance_id)
        if root_id is None:
            root_id = current_instance_id  # Fallback: this IS the root
        context_key = root_id

        repo = manager.get_shared_context_repository()
        results = []

        for op in ops:
            op_type = op.get("op")
            if op_type == "set":
                key = op.get("key")
                value = op.get("value")
                if not key:
                    results.append({"op": "set", "key": key, "error": "key is required"})
                    continue
                repo.upsert_record(context_key, key, value)
                results.append({"op": "set", "key": key, "status": "ok"})

            elif op_type == "delete":
                key = op.get("key")
                if not key:
                    results.append({"op": "delete", "key": key, "error": "key is required"})
                    continue
                deleted = repo.delete_record(context_key, key)
                results.append({"op": "delete", "key": key, "deleted": deleted})

            elif op_type == "list":
                records = repo.list_records(context_key)
                kv = {r.meta_key: r.meta_value for r in records}
                results.append({"op": "list", "count": len(kv), "data": kv})

            else:
                results.append({"op": op_type, "error": f"unknown op type: {op_type}"})

        return json.dumps({"context_key": context_key, "results": results}, indent=2)

    shared_context_metadata._full_doc_ = """\
Batch CRUD on shared context metadata KV pairs.

Metadata is scoped by context_key (the instance tree root ID) and automatically
injected into ALL team members' system prompts — not just explorer agents.

Args:
    operations: A JSON array of operation objects. Each object has:
        - {"op": "set", "key": "<string>", "value": <any JSON value>}
          Creates or updates a key-value pair.
        - {"op": "delete", "key": "<string>"}
          Deletes a key-value pair. Returns deleted: true/false.
        - {"op": "list"}
          Lists all current KV pairs for this context_key.

        All operations in the array are executed in order (batch).

Returns:
    JSON string with context_key and per-operation results array.

Example:
    operations = '[
        {"op": "set", "key": "project_change_scope", "value": "BIG"},
        {"op": "set", "key": "decision", "value": "use OAuth2"},
        {"op": "list"}
    ]'

Notes:
    - The context_key is automatically resolved from your instance tree root.
    - You do NOT need to pass context_key — it's derived automatically.
    - Set values can be any JSON type (string, number, boolean, object, array).
    - Metadata appears in all team members' prompts as "# Shared Context > ## Metadata KV".
"""

    return [shared_context_metadata]
```

### Modified Files

#### `daemon/tools/_tool_registry.py` — `CATEGORY_MODULES` (~line 184)

Add entry:
```python
"context_metadata": "daemon.tools.shared_context_tools",
```

#### `daemon/tools/instance.py`

**Import** (near line 93-123, with other tool imports):
```python
from .shared_context_tools import create_shared_context_tools
```

**Wire into `create_instance_tools()`** (near line 1038, after `context_tool_list`):
```python
# Shared context metadata tools (batch CRUD, injected into all agents' prompts)
shared_context_metadata_tools = create_shared_context_tools(manager, current_instance_id)
tools.extend(shared_context_metadata_tools)
```

#### `agents/leader/meta.json`

Add `"context_metadata"` to `tools.allow`:
```json
{
  "tools": {
    "allow": ["instance", "self", "project", "help", "knowledge", "mcp", "critical_notes", "project_history", "context_metadata"]
  }
}
```

## Tool Design Rationale

### Why a single tool with `operations` array (not separate set/delete/list tools)?

1. **Batch efficiency**: Leader can set multiple KV pairs in one call (e.g., `project_change_scope` + `decision` + `priority`)
2. **Atomic mental model**: All operations in one call are conceptually one "metadata update"
3. **Fewer tool registrations**: One tool vs three — simpler for LLM to discover and use
4. **Matches user requirement**: "The tool supports batch create/update/delete of multiple KV pairs in one call"

### Why auto-resolve context_key (not pass as parameter)?

1. **Consistency**: `context_key` is already injected into system prompt via `append_context_key()`. The tool should use the same resolution.
2. **Safety**: Prevents agent from accidentally writing to a different context_key's metadata.
3. **Simplicity**: Agent doesn't need to extract context_key from its own system prompt.

### Why `operations` as a JSON string (not typed args)?

LangChain `@tool` decorator works best with simple parameter types. A JSON string parameter gives maximum flexibility for batch operations while keeping the tool signature clean. The tool parses the JSON internally. This matches the pattern used by `todo_graph_add_subtask` which accepts JSON-encoded arrays.

## Constraints

- Follow `@register_tool_category` + `@tool` + `_full_doc_` pattern exactly.
- Tool must be `async def` returning `str`.
- Errors return string `"Error: ..."` — never raise exceptions (keeps LLM loop intact).
- Tool category key in `CATEGORY_MODULES` must match the `@register_tool_category()` argument.
- Factory must accept `(manager, current_instance_id)` signature for parity.
- Leader's `tools.allow` must explicitly include `"context_metadata"` (not auto-granted via innate skills).

## Deliverables

- [ ] `daemon/tools/shared_context_tools.py` created with `create_shared_context_tools()` factory
- [ ] `shared_context_metadata` tool with batch set/delete/list operations
- [ ] `_full_doc_` attached with full documentation
- [ ] Category registered in `CATEGORY_MODULES` in `_tool_registry.py`
- [ ] Factory wired into `create_instance_tools()` in `instance.py`
- [ ] `"context_metadata"` added to leader's `tools.allow` in `meta.json`
- [ ] Tool visible to leader agent via `tool_help("shared_context_metadata")`
