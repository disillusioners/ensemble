# Working Notes

## Exploration Findings (2025-04-23)

### Critical Discoveries from Reviewer Feedback

#### C1: daemon/utils.py Already Exists
- **Path**: `daemon/utils.py` (204 lines)
- **Contains**: `parse_think_tags` (12-32), `_extract_timestamp` (36-55), `serialize_message` (58-164), `get_next_sequence` (171-183), `compute_message_id` (187-204)
- **Action**: ALL phases must APPEND to this file, not create it

#### C2: app.state Already Partially In Use
- **Lines**: 341 (`app.state.live_hub = manager._live_hub`), 370-371 (shutdown check), 972 (SSE access)
- **Action**: Phase 3 must coexist with existing pattern; add new attributes alongside `live_hub`

#### C3: Phase 5 NOT Independent of Phase 3
- **Evidence**: `daemon/routers/jobs.py:166` → `from daemon.api import validate_agent_id`
- **Also**: `tests/test_spawn_instance_instructive_errors.py:14` → `from daemon.api import validate_agent_id`
- **Resolution**: Phase 1 relocates `validate_agent_id` to utils.py; Phase 5 runs after Phase 3

#### C5: Test Import Break Points
- `tests/test_spawn_instance_instructive_errors.py:14` → `from daemon.api import validate_agent_id`
- `tests/unit/test_vision.py:705,742` → `from daemon.api import send_message` (endpoint handler)
- `daemon/routers/jobs.py:166` → `from daemon.api import validate_agent_id` (inline import)
- **Resolution**: Phase 1 moves `validate_agent_id`; Phase 3 handles `send_message` re-export

#### C6: Module-Level Functions in manager.py
| Function | Lines | Imported By |
|----------|-------|-------------|
| `_build_message_content` | 80-95 | `tests/unit/test_vision.py` |
| `extract_project_keywords` | 113-143 | `daemon/tests/test_project_context_injection.py` |
| `format_project_context` | 146-165 | `daemon/tests/test_project_context_injection.py` |
| `_get_message_event_type` | 255-270 | Internal use |
| `_compute_message_content_hash` | 273-293 | Internal use |
- **Resolution**: Keep in `manager.py` alongside facade (AD-7)

#### C7: Correct Globals in api.py (lines 166-174)
```python
manager: InstanceManager = None
start_time: float = None
credential_manager = CredentialManager()
job_queue_service: JobQueueService = None
job_processor: JobProcessor = None
job_queue_mgmt_service: JobQueueMgmtService = None
retry_scheduler = None
dispatch_event_bus: DispatchEventBus = None
```
**NOT globals**: source_dispatcher, scheduler_service, mapping_service, prompt_cache, config

### File Inventory (Updated)
- `daemon/api.py`: 2114 lines, 33 endpoints, 8 groups, 8 globals
- `daemon/manager.py`: 2985 lines, 49 methods, 5 module-level functions, 2 callback classes, 2 dataclasses
- `daemon/models.py`: 737 lines, 35 models across 8+ concern groups
- `daemon/routers/jobs.py`: 891 lines, 8 endpoints in 3 sub-groups, imports from api.py
- `daemon/services/job_queue_service.py`: 1144 lines, duplicated lock release (603-614 vs 836-843)
- `daemon/utils.py`: **204 lines existing** — 5 functions
- `daemon/compaction.py`: 948 lines (not in scope)

### Method Count Clarification
- `InstanceManager` has **49 methods** (not 52 or 69)
- Plus **5 module-level functions** (not methods)
- Plus **4 inner classes/dataclasses** (ActivityCallbackHandler, CancellationCallbackHandler, MessageResult, AsyncMessageResult)
- Total callable entities: 58

### Duplication Inventory
- **datetime parsing**: 32 occurrences (25 in api.py, 7 elsewhere)
- **HTTPException 503**: 4 occurrences across routers
- **Service dependency boilerplate**: 4+ routers
- **Lock release**: 2 blocks with **subtle differences** (see Phase 5 notes)
- **Optional[T]**: 326+ occurrences across daemon/
- **Union[]**: 1 occurrence (tools/bash.py line 41)

### Inner Classes in manager.py
| Class | Lines | Type |
|-------|-------|------|
| `ActivityCallbackHandler` | 168-217 | BaseCallbackHandler subclass |
| `CancellationCallbackHandler` | 220-252 | BaseCallbackHandler subclass |
| `MessageResult` | 296-302 | @dataclass |
| `AsyncMessageResult` | 305-310 | @dataclass |

### Test Import Map
```
tests/test_spawn_instance_instructive_errors.py:14  →  from daemon.api import validate_agent_id
tests/unit/test_vision.py:705,742                    →  from daemon.api import send_message
tests/unit/test_vision.py                            →  from daemon.manager import _build_message_content
daemon/tests/test_project_context_injection.py        →  from daemon.manager import extract_project_keywords, format_project_context
daemon/routers/jobs.py:166                            →  from daemon.api import validate_agent_id (inline)
```

### Dependency Graph
```
constants.py ← (used by everything)
utils.py ← (existing + appended; used by routers, services, manager)
  ├── validates: validate_agent_id (from Phase 1)
  ├── fuzzy matching (from Phase 4)
models/ ← (used by routers, manager, services)
  ├── routers/ ← (used by api.py)
  ├── services/ ← (used by manager.py, routers)
  └── manager.py ← (used by api.py, tools, sources)
```

### Required Phase Order
```
Phase 1 (Constants, Utilities, relocate validate_agent_id)
  → Phase 2 (Models Split)
    → Phase 3 (API Router Extraction — correct globals, handle validate_agent_id re-export)
      → Phase 5 (Jobs Router — safe, validate_agent_id already relocated, api.py stable)
        → Phase 4 (Manager Decomposition — most complex, all deps resolved)
          → Phase 6 (Final Polish — type annotations)
```
