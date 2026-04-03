# Phase 5: Tools Layer — Instance Tools & All Tool References

## Objective
Rename `daemon/tools/session.py` → `daemon/tools/instance.py` and update all tool definitions and references across the tools layer. Tools are what agents use to interact with the system (spawn, message, terminate, list, get_info).

## Context
- **Phase 1-3 completed**: Models, repository, core daemon (including InstanceManager) fully renamed
- Tools layer imports InstanceManager from daemon.manager and provides agent-callable functions
- This phase can run in parallel with Phase 4 (Sources) since they both only depend on Phase 3

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename file** `daemon/tools/session.py` → `daemon/tools/instance.py` | Use `git mv daemon/tools/session.py daemon/tools/instance.py`. | File rename |
| 2 | **Update daemon/tools/instance.py** | Rename function `create_session_tools`→`create_instance_tools`. Rename tool functions: `spawn_session`→`spawn_instance`, `terminate_session`→`terminate_instance`, `list_sessions`→`list_instances`, `get_session_info`→`get_instance_info`. Rename input types: `SpawnSessionInput`→`SpawnInstanceInput`. Update all `session_id`→`instance_id` params. Update type hints: `SessionManager`→`InstanceManager`. Update docstrings. | `daemon/tools/instance.py` (~212 lines) |
| 3 | **Update daemon/tools/__init__.py** | Change import: `from .session import create_session_tools` → `from .instance import create_instance_tools`. Update `__all__` if present. | `daemon/tools/__init__.py` |
| 4 | **Update daemon/tools/_tool_registry.py** | Rename category: `"session"` → `"instance"` in tool category definitions. | `daemon/tools/_tool_registry.py` (~156 lines) |
| 5 | **Update daemon/tools/inner_soul.py** | Rename type hints: `SessionManager`→`InstanceManager`, `session_id`→`instance_id` params. ~12 occurrences. | `daemon/tools/inner_soul.py` |
| 6 | **Update daemon/tools/agent_mother.py** | Rename `SessionManager`→`InstanceManager` import/type hints, `current_session_id`→`current_instance_id`. ~4 occurrences. | `daemon/tools/agent_mother.py` |
| 7 | **Update daemon/tools/project.py** | Rename function `project_get_by_session`→`project_get_by_instance`, `current_session_id`→`current_instance_id`, `creator_session_id`→`creator_instance_id`. ~16 occurrences. | `daemon/tools/project.py` |
| 8 | **Update daemon/tools/help.py** | Update any category references from `"session"` → `"instance"`. | `daemon/tools/help.py` |

## Key Files
- `daemon/tools/session.py` → `daemon/tools/instance.py` (file rename + content)
- `daemon/tools/__init__.py` — package exports
- `daemon/tools/_tool_registry.py` — tool registry categories
- `daemon/tools/inner_soul.py` — self-modification tool
- `daemon/tools/agent_mother.py` — agent spawning tool
- `daemon/tools/project.py` — project management tool (~670 lines)
- `daemon/tools/help.py` — help tool

## Detailed Rename Map for instance.py

### Function & Type Renames
| Old | New |
|-----|-----|
| `create_session_tools()` | `create_instance_tools()` |
| `SpawnSessionInput` | `SpawnInstanceInput` |
| `spawn_session` (tool name) | `spawn_instance` |
| `send_message` (no change) | `send_message` |
| `terminate_session` (tool name) | `terminate_instance` |
| `list_sessions` (tool name) | `list_instances` |
| `get_session_info` (tool name) | `get_instance_info` |

### Parameter Renames
| Old | New |
|-----|-----|
| `session_id` | `instance_id` |
| `SessionManager` type hint | `InstanceManager` |

## Constraints
- `send_message` tool name stays the same — it's a generic concept, not session-specific
- Don't change the tool's LangChain `@tool` decorator patterns, just the names within
- Tool descriptions/docstrings should be updated to say "instance" instead of "session"

## Verification
```bash
# 1. Old file removed
ls daemon/tools/session.py  # should fail
ls daemon/tools/instance.py  # should exist

# 2. No old names in tools layer
grep -rn "create_session_tools\|SpawnSessionInput\|spawn_session\|terminate_session\|list_sessions\|get_session_info\|SessionManager\|project_get_by_session\|current_session_id" daemon/tools/

# 3. New names present
grep -c "create_instance_tools\|SpawnInstanceInput\|spawn_instance\|terminate_instance\|list_instances\|get_instance_info\|InstanceManager\|project_get_by_instance\|current_instance_id" daemon/tools/instance.py
```

## Deliverables
- [ ] File renamed: `session.py` → `instance.py`
- [ ] `create_instance_tools()` function with all tool renames
- [ ] `__init__.py` exports updated
- [ ] `_tool_registry.py` category updated
- [ ] All other tool files updated (inner_soul, agent_mother, project, help)
- [ ] Grep shows 0 old session names in tools layer
