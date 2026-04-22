# Phase 4: Manager Decomposition

## Objective
Break the `InstanceManager` god class (2985 lines, 49 methods, 5 module-level functions, 2 callback handler classes, 2 dataclasses) into focused service classes while keeping `InstanceManager` as a thin facade that delegates to them. Also preserve all module-level functions and inner classes with proper re-exports.

## Coupling
- **Depends on**: Phase 1 (magic numbers already replaced in manager.py) + Phase 2 (model import paths updated)
- **Coupling type**: loose (Phase 1's magic number changes are already applied; Phase 2's model path changes are stable)
- **Shared files with other phases**: Only `daemon/manager.py` and new service files
- **Shared APIs/interfaces**: `InstanceManager` public interface + all module-level functions must remain importable
- **Why this coupling**: Phase 1 already replaced magic numbers — Phase 4 can restructure without merge conflicts. Phase 2's model package is stable.

## Pre-flight Validation
```bash
git tag refactor-pre-phase4

# Record all imports from daemon.manager
grep -rn "from daemon.manager import" daemon/ tests/ --include="*.py" | sort > /tmp/manager-imports-baseline.txt

# Record all module-level function imports
grep -rn "from daemon.manager import.*_build_message_content\|extract_project_keywords\|format_project_context\|_get_message_event_type\|_compute_message_content_hash" daemon/ tests/ --include="*.py"

# Verify magic numbers already replaced (Phase 1)
grep -c "from daemon.constants import" daemon/manager.py
```

## Rollback Procedure
```bash
# Restore manager.py and remove new service files
git checkout refactor-pre-phase4 -- daemon/manager.py
rm -f daemon/services/instance_lifecycle.py daemon/services/instance_messaging.py \
      daemon/services/child_reports.py daemon/services/error_reporting.py \
      daemon/services/cancellation.py daemon/services/title_generation.py \
      daemon/services/event_publisher.py
# Re-run tests
```

## Context
- Phase 1 completed: magic numbers in `manager.py` already replaced with constants
- Phase 2 completed: model imports use `daemon.models.*` paths
- `InstanceManager` has 49 methods, but `manager.py` also contains:
  - 5 **module-level functions** imported by tests and other code
  - 2 **callback handler classes** used internally
  - 2 **dataclasses** used as return types
- 20+ test files import from `manager.py`

## Complete Inventory of Non-Method Entities in `manager.py`

### Module-Level Functions (MUST preserve with re-exports)
| Function | Lines | Imported By |
|----------|-------|-------------|
| `_build_message_content` | 80–95 | `tests/unit/test_vision.py` |
| `extract_project_keywords` | 113–143 | `daemon/tests/test_project_context_injection.py` |
| `format_project_context` | 146–165 | `daemon/tests/test_project_context_injection.py` |
| `_get_message_event_type` | 255–270 | Used internally (check consumers) |
| `_compute_message_content_hash` | 273–293 | Used internally (check consumers) |

### Inner Classes / Dataclasses (MUST preserve)
| Class | Lines | Used By |
|-------|-------|---------|
| `ActivityCallbackHandler` | 168–217 | `InstanceManager.__init__` (instantiated for graph callbacks) |
| `CancellationCallbackHandler` | 220–252 | `InstanceManager.__init__` (instantiated for graph callbacks) |
| `MessageResult` | 296–302 | `InstanceManager.send_message` return type; imported by tests |
| `AsyncMessageResult` | 305–310 | `InstanceManager.enqueue_message` return type; imported by tests |

## Responsibility Group Breakdown

| # | Group | Methods | Target Location | Est. Lines |
|---|-------|---------|-----------------|------------|
| 1 | **Lifecycle** | `spawn_instance` (634–807), `terminate_instance` (2513–2594), `clear_all_instances` (2768–2778), `get_instance` (2634–2660), `_restore_instance` (2662–2718), `list_instances` (2720–2732), `get_instance_info` (2734–2749) | `daemon/services/instance_lifecycle.py` | ~500 |
| 2 | **Messaging** | `send_message` (809–925), `enqueue_message` (927–1051), `_process_message_with_tracking` (1053–1431), `get_messages` (2751–2766), `get_queue_stats` (2331–2342) | `daemon/services/instance_messaging.py` | ~600 |
| 3 | **Child/Parent Reports** | `_summarize_instance` (1467–1551), `_get_instance_report_prefix` (1433–1465), `_should_send_completion_report` (1553–1621), `_create_completion_report` (1623–1683), `_update_parent_on_child_complete` (1685–1783), `_create_completion_events` (1785–1837), `_process_child_completion_and_notify_parent` (1839–1976), `_get_last_assistant_message` (2209–2238) | `daemon/services/child_reports.py` | ~400 |
| 4 | **Error Reporting** | `_send_error_report` (1978–2207), `_on_stale_task_permanent_failure` (518–537) | `daemon/services/error_reporting.py` | ~150 |
| 5 | **Cancellation** | `cancel` (2492–2502), `cancel_instance_requests` (2504–2511), `get_active_requests` (2971–2980), `is_shutting_down` (2982–2984), `_cancel_all_active_requests` (2940–2948), `_wait_for_inflight` (2950–2969) | `daemon/services/cancellation.py` | ~100 |
| 6 | **Compaction** | `_maybe_compact_context` (2367–2454), `_get_system_prompt_tokens` (2344–2365) | (already in `daemon/compaction.py` — just delegate) | ~0 |
| 7 | **Title Generation** | `_generate_and_broadcast_title` (2241–2329) | `daemon/services/title_generation.py` | ~100 |
| 8 | **SSE/Streaming** | `_publish_instance_lifecycle_event` (2596–2632) | `daemon/services/event_publisher.py` | ~40 |
| 9 | **Fuzzy Matching** | `find_near_instance` (2852–2882), `_edit_distance` (2822–2850) | `daemon/utils.py` (append) | ~50 |
| 10 | **Shutdown** | `shutdown` (2894–2938), `cleanup` (2884–2892) | Stays in `InstanceManager` (orchestration) | ~100 |

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Keep** module-level functions in `manager.py` | `_build_message_content`, `extract_project_keywords`, `format_project_context`, `_get_message_event_type`, `_compute_message_content_hash` stay in `manager.py` alongside the facade. Tests import them from here. | `daemon/manager.py` |
| 2 | **Keep** inner classes in `manager.py` | `ActivityCallbackHandler`, `CancellationCallbackHandler`, `MessageResult`, `AsyncMessageResult` stay in `manager.py`. They are referenced by `InstanceManager.__init__` and by tests. | `daemon/manager.py` |
| 3 | Create `daemon/services/instance_lifecycle.py` | Extract Lifecycle group (7 methods). Create `InstanceLifecycleService` class. | `daemon/services/instance_lifecycle.py` (new) |
| 4 | Create `daemon/services/instance_messaging.py` | Extract Messaging group (5 methods). Create `InstanceMessagingService` class. | `daemon/services/instance_messaging.py` (new) |
| 5 | Create `daemon/services/child_reports.py` | Extract Child/Parent Reports group (8 methods). Create `ChildReportsService` class. | `daemon/services/child_reports.py` (new) |
| 6 | Create `daemon/services/error_reporting.py` | Extract Error Reporting group (2 methods). Create `ErrorReportingService` class. | `daemon/services/error_reporting.py` (new) |
| 7 | Create `daemon/services/cancellation.py` | Extract Cancellation group (6 methods). Create `CancellationService` class. | `daemon/services/cancellation.py` (new) |
| 8 | Create `daemon/services/title_generation.py` | Extract `_generate_and_broadcast_title`. Create `TitleGenerationService` class. | `daemon/services/title_generation.py` (new) |
| 9 | Create `daemon/services/event_publisher.py` | Extract `_publish_instance_lifecycle_event`. Create `EventPublisherService` class. | `daemon/services/event_publisher.py` (new) |
| 10 | **Append** fuzzy matching to `daemon/utils.py` | Move `_edit_distance` and `find_near_instance` to `daemon/utils.py`. Add re-export in `manager.py` for backward compatibility. | `daemon/utils.py` (existing), `daemon/manager.py` |
| 11 | Refactor `InstanceManager` to facade | Keep class but delegate all service-group methods to new service classes. | `daemon/manager.py` |
| 12 | Wire services in `__init__` | Initialize all new services in `InstanceManager.__init__()`, passing required dependencies. Inner classes (`ActivityCallbackHandler`, `CancellationCallbackHandler`) remain created here. | `daemon/manager.py` |
| 13 | Handle cross-service dependencies | Services that need to call other services receive the `InstanceManager` facade reference. | All new service files |
| 14 | Verify all imports still work | `from daemon.manager import InstanceManager, extract_project_keywords, format_project_context, _build_message_content, MessageResult, AsyncMessageResult` must all work. | — |

## Key Files
- `daemon/manager.py` — Becomes facade (~400 lines) + module-level functions (~100 lines) + inner classes (~100 lines) ≈ ~600 total
- `daemon/services/instance_lifecycle.py` (new) — ~500 lines
- `daemon/services/instance_messaging.py` (new) — ~600 lines
- `daemon/services/child_reports.py` (new) — ~400 lines
- `daemon/services/error_reporting.py` (new) — ~150 lines
- `daemon/services/cancellation.py` (new) — ~100 lines
- `daemon/services/title_generation.py` (new) — ~100 lines
- `daemon/services/event_publisher.py` (new) — ~40 lines
- `daemon/utils.py` (existing) — Append fuzzy matching functions

## Constraints
- `InstanceManager`'s **public method signatures must not change** — all 49 methods must remain callable
- **Module-level functions** MUST remain importable from `daemon.manager` — tests depend on this
- **Inner classes** (`ActivityCallbackHandler`, `CancellationCallbackHandler`, `MessageResult`, `AsyncMessageResult`) MUST remain importable from `daemon.manager`
- The **order of side effects** must be preserved (e.g., `terminate_instance` calls cancellation, then cleanup, then notification)
- **No behavioral changes** in error handling, logging, or state transitions
- All services receive dependencies via **constructor injection**

## Detailed Implementation Notes

### `manager.py` Post-Refactor Structure (~600 lines)
```python
"""InstanceManager and related module-level utilities."""

# ── Module-Level Functions (preserved for backward compat) ──

def _build_message_content(message: str, images: list[str] | None) -> str | list:
    ...  # lines 80-95 (unchanged)

def extract_project_keywords(message: str) -> list[str]:
    ...  # lines 113-143 (unchanged)

def format_project_context(project) -> str:
    ...  # lines 146-165 (unchanged)

def _get_message_event_type(msg: dict) -> str:
    ...  # lines 255-270 (unchanged)

def _compute_message_content_hash(msg: dict) -> str:
    ...  # lines 273-293 (unchanged)

# ── Inner Classes (preserved for backward compat) ──

class ActivityCallbackHandler(BaseCallbackHandler):
    ...  # lines 168-217 (unchanged)

class CancellationCallbackHandler(BaseCallbackHandler):
    ...  # lines 220-252 (unchanged)

@dataclass
class MessageResult:
    ...  # lines 296-302 (unchanged)

@dataclass
class AsyncMessageResult:
    ...  # lines 305-310 (unchanged)

# ── Re-exports from utils ──
from daemon.utils import find_near_instance as find_near_instance  # noqa: F401

# ── InstanceManager (facade) ──

class InstanceManager:
    """Facade that delegates to focused services."""
    
    def __init__(self, config: Config):
        # ... existing init for internal state ...
        
        # Initialize services
        self._lifecycle = InstanceLifecycleService(...)
        self._messaging = InstanceMessagingService(...)
        self._child_reports = ChildReportsService(...)
        self._cancellation = CancellationService(...)
        self._title_gen = TitleGenerationService(...)
        self._events = EventPublisherService(...)
        self._error_reporting = ErrorReportingService(...)
    
    # All 49 methods delegate to services...
    async def spawn_instance(self, ...): return await self._lifecycle.spawn_instance(...)
    async def send_message(self, ...): return await self._messaging.send_message(...)
    # ... etc for all public + private methods
```

### Handling Cross-Service Dependencies
Some services need to call methods from other groups:
- **Messaging** needs **Compaction** (compact context during processing)
- **Lifecycle** needs **Cancellation** (cancel on terminate)
- **Messaging** needs **Child Reports** (report completion)
- **Messaging** needs **Error Reporting** (report errors)

**Strategy**: Pass the `InstanceManager` facade itself to services that need cross-service calls.
```python
class InstanceMessagingService:
    def __init__(self, manager: "InstanceManager", ...):
        self._manager = manager  # for cross-service calls
```

### Fuzzy Matching Relocation
```python
# In daemon/utils.py (append after Phase 1 content):
def _edit_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    ...

def find_near_instance(instance_id: str, max_distance: int = 2) -> str | None:
    """Find a near-matching instance ID within edit distance."""
    ...

# In daemon/manager.py — re-export for backward compat:
from daemon.utils import find_near_instance as find_near_instance  # noqa: F401
# (tests may import this from daemon.manager)
```

### Incremental Extraction Order
1. **Leaf services** (no cross-deps): `EventPublisher`, `TitleGeneration`, `FuzzyMatching`
2. **Mid-level**: `Cancellation`, `ErrorReporting`, `ChildReports`
3. **Complex**: `InstanceMessaging` (depends on many others)
4. **Final**: `InstanceLifecycle` (depends on Cancellation)
5. **Wire all** in `InstanceManager.__init__`

After each extraction: run full test suite.

## Deliverables
- [ ] 7 new service files created in `daemon/services/`
- [ ] `InstanceManager` refactored to facade with all 49 methods delegating
- [ ] All 5 module-level functions preserved in `manager.py` (importable from `daemon.manager`)
- [ ] All 4 inner classes preserved in `manager.py` (importable from `daemon.manager`)
- [ ] Fuzzy matching moved to `daemon/utils.py` with re-export in `manager.py`
- [ ] All public method signatures preserved
- [ ] All cross-service dependencies properly wired
- [ ] No circular imports
- [ ] Full test suite passes
