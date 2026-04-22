# Architecture Decisions

## AD-1: Facade Pattern for Manager Decomposition
**Decision**: Keep `InstanceManager` as a facade that delegates to new service classes, rather than replacing it with a registry of services.

**Rationale**: 
- 20+ test files and 6+ production files import from `daemon.manager`
- 5 module-level functions and 4 inner classes must remain importable from `daemon.manager`
- Changing all consumers would be a massive change with high risk
- Facade preserves the public API while allowing internal restructuring
- Services can be tested independently while facade maintains integration

**Alternative considered**: Replace `InstanceManager` with direct service imports
**Rejected because**: Too many call sites to update, high risk of breaking tests and module-level function consumers

---

## AD-2: Package-style Models Split with Re-exports
**Decision**: Convert `daemon/models.py` to `daemon/models/` package with `__init__.py` re-exporting everything.

**Rationale**:
- Preserves all existing `from daemon.models import X` imports
- New code can use more specific imports (`from daemon.models.instance import InstanceCreate`)
- Zero changes needed in consumers during the split

**Alternative considered**: Just add comments/regions in the existing file
**Rejected because**: Doesn't actually improve discoverability or reduce file size

---

## AD-3: app.state for Global Service References (Coexist with live_hub)
**Decision**: Migrate module-level globals in `api.py` to FastAPI's `app.state` pattern, adding to the existing `app.state.live_hub` pattern already in use.

**Rationale**:
- `app.state.live_hub` is already active (lines 341, 370–371, 972)
- Standard FastAPI pattern for application-scoped state
- Enables proper dependency injection via `Request.app.state`
- More testable (can create app instances with different state)

**Correct globals to migrate** (from `api.py` lines 166–174):
- `manager`, `start_time`, `credential_manager`, `job_queue_service`, `job_processor`, `job_queue_mgmt_service`, `retry_scheduler`, `dispatch_event_bus`

**NOT globals** (they are InstanceManager attributes or don't exist):
- `source_dispatcher` → accessed via `manager.get_source_registry()`
- `scheduler_service` → InstanceManager attribute
- `mapping_service` → InstanceManager attribute
- `prompt_cache` → InstanceManager attribute
- `config` → passed to InstanceManager

**Alternative considered**: Keep globals but organize them better
**Rejected because**: Doesn't solve the fundamental issue; inconsistent with existing `app.state.live_hub` pattern

---

## AD-4: Incremental Router Extraction
**Decision**: Extract routers from `api.py` one at a time, verifying tests after each extraction.

**Rationale**:
- Lower risk than a single big-bang extraction
- Each extraction is independently verifiable
- If one extraction causes issues, it's easy to pinpoint
- Allows for incremental testing

**Alternative considered**: Extract all routers in one commit
**Rejected because**: Higher risk, harder to debug if tests break

---

## AD-5: APPEND to Existing utils.py (Not Replace)
**Decision**: Append new utility functions to the existing `daemon/utils.py` (204 lines), not create a new file.

**Rationale**:
- `daemon/utils.py` already exists with 5 functions: `parse_think_tags`, `_extract_timestamp`, `serialize_message`, `get_next_sequence`, `compute_message_id`
- These are all utility functions; new helpers belong in the same module
- Creating a separate file would be over-engineering

**Alternative considered**: Create `daemon/helpers.py` for new utilities
**Rejected because**: Unnecessary file proliferation; utilities belong together

---

## AD-6: Relocate `validate_agent_id` to utils.py
**Decision**: Move `validate_agent_id` from `daemon/api.py` (lines 100–127) to `daemon/utils.py`, with re-export in `api.py`.

**Rationale**:
- `validate_agent_id` is a pure validation function — NOT a route handler
- It's imported by `daemon/routers/jobs.py` (line 166) — cross-module dependency on api.py
- It's imported by `tests/test_spawn_instance_instructive_errors.py` (line 14)
- Moving to utils eliminates the circular dependency risk when api.py is split into routers
- Re-export in `api.py` preserves backward compatibility during transition

**Alternative considered**: Keep in api.py and fix imports after router split
**Rejected because**: Creates ordering dependency; Phase 3 (router split) would break Phase 5 (jobs router)

---

## AD-7: Keep Module-Level Functions in manager.py
**Decision**: Keep `_build_message_content`, `extract_project_keywords`, `format_project_context`, `_get_message_event_type`, `_compute_message_content_hash` in `manager.py` alongside the facade.

**Rationale**:
- These are imported by tests: `tests/unit/test_vision.py`, `daemon/tests/test_project_context_injection.py`
- Moving them to utils.py would require updating all import sites
- They're tightly coupled to InstanceManager internals (access manager state)
- Keeping them in manager.py is lower risk and maintains backward compatibility
- `find_near_instance` and `_edit_distance` are the exception — they're pure utility functions and can move to `utils.py`

**Alternative considered**: Move all module-level functions to utils.py
**Rejected because**: Some functions access InstanceManager internals; moving creates coupling between utils and manager

---

## AD-8: Sequential Phase Execution (No Parallelization)
**Decision**: Execute all 6 phases sequentially in the order: 1 → 2 → 3 → 5 → 4 → 6.

**Rationale**:
- Phase 3 splits `api.py` — Phase 5 imports from `api.py` (even though `validate_agent_id` is relocated in Phase 1, the file itself changes)
- Phase 1 and Phase 5 both modify `job_queue_service.py` — cannot overlap
- Phase 1 and Phase 4 both modify `manager.py` — cannot overlap
- Sequential execution eliminates all file contention risks
- Total time increase is minimal (~1 hour) compared to the risk of parallel conflicts

**Alternative considered**: Parallel execution of Phases 2, 3, and 5
**Rejected because**: Phase 5 depends on Phase 3 completing (api.py stability); Phase 5 depends on Phase 1 completing (same file); too many hidden dependencies

---

## AD-9: Constants in Single Module
**Decision**: All named constants in a single `daemon/constants.py` with categorized sections.

**Rationale**:
- Constants are small, primitive values
- Single file is easy to search and browse
- Categorized sections provide organization without file proliferation
- Total size will be ~80–100 lines — well within manageable range

**Alternative considered**: `daemon/constants/` package with `limits.py`, `timeouts.py`, etc.
**Rejected because**: Over-fragmentation for a small amount of data

---

## AD-10: Lock Release Dedup with Dual Fallback Mode
**Decision**: Extract a single `_release_job_lock()` helper that accepts a `fallback_mode` parameter to handle the two different fallback code paths.

**Rationale**:
- The two lock release blocks at lines 603–614 and 836–843 have the same structure but different fallback calls
- Pattern A uses `release_by_instance(job.instance_id)` (checks `if job.instance_id`)
- Pattern B uses `release(job.project_id, job_id)` (no instance check)
- A single helper with a mode flag is cleaner than two nearly-identical methods
- The `fallback_mode` parameter makes the behavioral difference explicit

**Alternative considered**: Two separate methods
**Rejected because**: 90% code duplication; the mode flag is cleaner
