# Phase 5: Dependency Injection Cleanup (Optional / Deferrable)

## Objective
Reduce coupling in `SchedulerAdapter` by extracting Protocol-based interfaces for repositories and services, improving testability and following SOLID principles. This phase is P4 priority and can be deferred.

## Coupling
- **Depends on**: Phase 4 (all changes complete, tests exist)
- **Coupling type**: loose
- **Shared files with other phases**: `daemon/sources/adapters/scheduler.py`, `daemon/sources/registry.py`
- **Shared APIs**: Repository and service constructor types
- **Why**: Type-system only change — no behavioral impact, tests already exist to verify

## Context
**Issue addressed**: #14 (tight coupling — 4+ direct dependencies)

**Current state**:
- `SchedulerAdapter.__init__` depends on concrete types: `JobQueueService`, `SourceRepository`, `SQLModelInstanceRepository`
- These are imported under `TYPE_CHECKING` but used as type hints for concrete dependencies
- Makes unit testing harder — must mock concrete classes rather than implement protocols
- 5 dependencies total: `job_queue_service`, `source_repo`, `instance_repo`, `execution_callback`, `on_complete_callback`

**Target state**:
- Define Protocol classes for each dependency
- Constructor accepts Protocol types
- Same runtime behavior, better type safety and testability

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 5.1 | Create `SourceRepositoryProtocol` | Define Protocol with methods used by scheduler: `increment_scheduler_run_counter()`, `record_execution_start()`, `record_execution_complete()`, `get_latest_execution()`, `update_source_config()`. | `daemon/sources/adapters/scheduler.py` (or new protocols file) |
| 5.2 | Create `InstanceRepositoryProtocol` | Define Protocol with methods: `get()`, `get_instance_mapping()`. | `daemon/sources/adapters/scheduler.py` |
| 5.3 | Create `JobQueueServiceProtocol` | Define Protocol with method: `enqueue()`. | `daemon/sources/adapters/scheduler.py` |
| 5.4 | Update constructor type hints | Replace concrete types with Protocol types in `__init__`. Remove `TYPE_CHECKING` imports for concrete classes. Runtime behavior unchanged — Python doesn't enforce Protocol at runtime. | `daemon/sources/adapters/scheduler.py:44-52` |
| 5.5 | Update registry integration | Ensure `daemon/sources/registry.py` (`_create_adapter_from_config`, lines 195-344) still passes concrete implementations. No behavioral change needed — Protocol is structural typing. | `daemon/sources/registry.py` |
| 5.6 | Verify all tests pass | Existing tests should pass unchanged — Protocol types are compatible with existing mocks. | All test files |

## Key Files
- `daemon/sources/adapters/scheduler.py` — Protocol definitions and updated type hints
- `daemon/sources/registry.py` — Adapter instantiation via `_create_adapter_from_config()` (verify compatibility)

## Constraints
- Must maintain backward compatibility — Protocol is structural, no inheritance needed
- All existing tests must pass unchanged
- No changes to public API behavior
- Consider placing Protocol definitions in a separate file if they're reusable

## Deliverables
- [ ] `SourceRepositoryProtocol` defined
- [ ] `InstanceRepositoryProtocol` defined
- [ ] `JobQueueServiceProtocol` defined
- [ ] Constructor type hints use Protocols
- [ ] All tests pass
- [ ] Registry integration verified

## Note
This phase is **optional and deferrable**. It addresses P4 priority (maintainability) with no user-facing impact. Schedule it after Phases 1–4 are stable and deployed.
