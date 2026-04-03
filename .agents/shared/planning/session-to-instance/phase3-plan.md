# Phase 3a: Core Daemon — Manager, Graph, Persistence, Config, Request Registry

## Objective
Rename all session references in the core daemon files: the manager (largest file at ~2100 lines), graph builder, persistence layer, config, and request registry. These are the primary orchestrators that consume the repository layer.

## Context
- **Phase 1 completed**: All models renamed
- **Phase 2 completed**: Repository layer fully renamed, `daemon.repositories` exports new names
- This phase updates the primary consumers of the repository layer
- Phases 3b, 3c, 4, and 5 all depend on this phase completing first

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename daemon/manager.py** (~2100 lines — LARGEST FILE) | Systematic rename of: Class `SessionManager`→`InstanceManager`, dict `self.sessions`→`self.instances`, all methods: `spawn_session`→`spawn_instance`, `terminate_session`→`terminate_instance`, `list_sessions`→`list_instances`, `get_session_info`→`get_instance_info`, `_restore_session`→`_restore_instance`, `_summarize_session`→`_summarize_instance`, `_generate_session_title`→`_generate_instance_title`, `clear_all_sessions`→`clear_all_instances`, `enqueue_message`→`enqueue_message` (keep name, update session_id param→instance_id). All params: `session_id`→`instance_id`, `session_metadata`→`instance_metadata`. Imports: `SQLModelSessionRepository`→`SQLModelInstanceRepository`, `Session`→`Instance`, `build_session_graph`→`build_instance_graph`, `create_session_tools`→`create_instance_tools`. All log strings and completion reports. | `daemon/manager.py` |
| 2 | **Rename daemon/graph.py** | Rename function `build_session_graph`→`build_instance_graph`. Update any internal `session_id` references (used as `thread_id` in LangGraph). | `daemon/graph.py` |
| 3 | **Rename daemon/persistence.py** | Rename `get_session_messages`→`get_instance_messages`. Update `session_id` parameters → `instance_id`. | `daemon/persistence.py` (~138 lines) |
| 4 | **Rename daemon/config.py** | Rename config keys: `max_sessions`→`max_instances`, `max_children_per_session`→`max_children_per_instance`, `session_timeout_minutes`→`instance_timeout_minutes`. Update access patterns in code. | `daemon/config.py` (~193 lines) |
| 5 | **Rename daemon/request_registry.py** | Rename fields: `session_id`→`instance_id`, `_by_session`→`_by_instance`. Rename methods: `get_active_for_session`→`get_active_for_instance`, `cancel_by_session`→`cancel_by_instance`. Rename error: `SESSION_TERMINATED`→`INSTANCE_TERMINATED`. | `daemon/request_registry.py` (~131 lines) |

## Key Files
- `daemon/manager.py` — ~2100 lines, the core orchestrator. **Most changes in this phase.**
- `daemon/graph.py` — Graph builder function
- `daemon/persistence.py` — Checkpoint/message storage
- `daemon/config.py` — Configuration loading
- `daemon/request_registry.py` — Request tracking

## Exclusions — DO NOT Rename
- `db_session` — SQLAlchemy session parameter
- Any LangGraph internal concepts that happen to use "session"
- The `opencode_skill` session concept

## Detailed Rename Map for manager.py

### Imports (top of file)
```python
# OLD
from .repositories import SQLModelSessionRepository, Session, SessionStatus
from .graph import build_session_graph
from .tools import create_session_tools

# NEW
from .repositories import SQLModelInstanceRepository, Instance, InstanceStatus
from .graph import build_instance_graph
from .tools import create_instance_tools
```

### Class & State
```python
# OLD: class SessionManager
# NEW: class InstanceManager

# OLD: self.sessions: dict[str, Session] = {}
# NEW: self.instances: dict[str, Instance] = {}
```

### Method Signatures (key methods)
| Old Name | New Name |
|----------|----------|
| `spawn_session()` | `spawn_instance()` |
| `terminate_session()` | `terminate_instance()` |
| `list_sessions()` | `list_instances()` |
| `get_session_info()` | `get_instance_info()` |
| `_restore_session()` | `_restore_instance()` |
| `_summarize_session()` | `_summarize_instance()` |
| `_generate_session_title()` | `_generate_instance_title()` |
| `clear_all_sessions()` | `clear_all_instances()` |
| `enqueue_message()` | `enqueue_message()` (keep name, update param) |

### Parameters
| Old | New |
|-----|-----|
| `session_id` | `instance_id` |
| `session_metadata` | `instance_metadata` |
| `current_session_id` | `current_instance_id` |

### Internal References
- `self.sessions[session_id]` → `self.instances[instance_id]`
- `self._repo.create_session()` → `self._repo.create_instance()`
- `self._repo.get_session()` → `self._repo.get_instance()`
- Log strings: `"session"` → `"instance"` in f-strings and logger calls

## Constraints
- **manager.py is 2100 lines** — use systematic find-and-replace, don't try to read every line
- After this phase, Phases 3b, 3c, 4, 5, and 6 imports will still be broken — that's expected
- Accept that the codebase won't fully compile until Phase 6

## Verification
```bash
# 1. No old class/method names in core daemon files
grep -rn "SessionManager\|build_session_graph\|get_session_messages\|spawn_session\|terminate_session\|list_sessions\|get_session_info\|max_sessions\|session_timeout" daemon/manager.py daemon/graph.py daemon/persistence.py daemon/config.py daemon/request_registry.py

# 2. New names present
grep -c "InstanceManager\|build_instance_graph\|get_instance_messages\|spawn_instance\|terminate_instance\|list_instances\|get_instance_info\|max_instances\|instance_timeout" daemon/manager.py daemon/graph.py daemon/persistence.py daemon/config.py daemon/request_registry.py

# 3. Exclusions preserved (these should STILL exist)
grep -rn "db_session" daemon/manager.py daemon/persistence.py
```

## Deliverables
- [ ] `daemon/manager.py` — InstanceManager class, all methods/params renamed
- [ ] `daemon/graph.py` — build_instance_graph function
- [ ] `daemon/persistence.py` — get_instance_messages, instance_id params
- [ ] `daemon/config.py` — max_instances, max_children_per_instance, instance_timeout_minutes
- [ ] `daemon/request_registry.py` — instance_id, _by_instance, get_active_for_instance
- [ ] Grep verification shows 0 old session names in these files (excluding exclusions)
