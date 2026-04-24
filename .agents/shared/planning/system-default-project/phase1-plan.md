# Phase 1: System Project & Queue Bootstrap

## Objective

Create the `__system_default__` project and its two system queues at daemon startup, making them available for all subsequent job routing. The project and queues are created idempotently (re-running on every startup is safe).

---

## Coupling

- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/constants.py` (provides `SYSTEM_DEFAULT_PROJECT_NAME` and `SYSTEM_DEFAULT_PROJECT_ID` used by all other phases)
- **Shared APIs/interfaces**: `ensure_system_default_project()` → returns `project_id` used by Phase 2 normalization
- **Why this coupling**: Phase 2's normalization helpers read `SYSTEM_DEFAULT_PROJECT_ID` that this phase populates at startup. Phase 4 reads `SYSTEM_DEFAULT_PROJECT_NAME` from constants. All phases depend on the system project existing.

## Context

### Current State

- `constants.py` has no system-default-project constant.
- `SQLModelProjectRepository` has `create()` and `get_by_name()` but no `ensure()` or idempotent-get-or-create helper.
- `JobQueueMgmtService.auto_provision_system_queues()` already creates system queues idempotently per project (it checks `get_by_name` first).
- `api.py` lifespan (lines 253–260) already loops over existing projects and calls `auto_provision_system_queues`. The system project does not exist yet, so it is skipped.

### Target State

- `SYSTEM_DEFAULT_PROJECT_NAME = "__system_default__"` defined in `constants.py`.
- `SYSTEM_DEFAULT_PROJECT_ID: str | None = None` module variable in `constants.py`.
- `ensure_system_default_project()` function that gets-or-creates the system project, returning its `project_id`.
- Startup slot in `api.py` lifespan that calls `ensure_system_default_project()` **before** `auto_provision_system_queues` loop, then passes the resulting `project_id` to `auto_provision_system_queues()` for the system project.
- In-memory module-level variable holding the system project ID so other modules (normalization layer) can access it without a DB round-trip.

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1.1 | Add `SYSTEM_DEFAULT_PROJECT_NAME` constant | `SYSTEM_DEFAULT_PROJECT_NAME = "__system_default__"` — plain string only, no imports from project/queue code to avoid circular deps | `daemon/constants.py` |
| 1.2 | Add `SYSTEM_DEFAULT_PROJECT_ID` runtime slot | `SYSTEM_DEFAULT_PROJECT_ID: str \| None = None` — module variable, written at startup, read by normalization layer | `daemon/constants.py` |
| 1.3 | Add `ensure_system_default_project()` to repository | Idempotent get-or-create. Uses `get_by_name()` first; if not found, calls `create()` with a fixed UUID derived from name (stable ID across restarts). Mark `is_system=True` via `project_metadata`. | `daemon/repositories/project/repository.py` |
| 1.4 | Update `api.py` lifespan — add system project bootstrap slot | Slot is after `manager._project_repository` is set (line ~131) but **before** `auto_provision_system_queues` loop (lines 253–260). Call `ensure_system_default_project()`, store result in `SYSTEM_DEFAULT_PROJECT_ID`. Then include system project in the auto-provision loop. | `daemon/api.py` |
| 1.5 | Add unit tests for idempotency | Test: first call creates, second call returns existing; returns same `project_id` on repeated calls; queues are provisioned | `tests/unit/test_system_project_bootstrap.py` |

---

## Key Files

- `daemon/constants.py` — Add two constants (`SYSTEM_DEFAULT_PROJECT_NAME`, `SYSTEM_DEFAULT_PROJECT_ID`)
- `daemon/repositories/project/repository.py` — Add `ensure_system_default_project()` method
- `daemon/api.py` — Add bootstrap slot in lifespan function

---

## Constraints

1. **No circular imports.** `constants.py` must not import from `repositories/` or `services/`. The system project ID is stored as a plain string at startup, not derived at import time.
2. **Stable project ID.** The system project's `project_id` must be deterministic (derived from its name via UUID5 or similar) so that `ensure_system_default_project()` returns the same ID on every startup without needing a separate lookup table.
3. **Idempotency.** Both project creation and queue creation must be safe to call multiple times. If the project already exists, `ensure_system_default_project()` must not error and must return the existing ID.
4. **Startup ordering.** The system project bootstrap must run **before** `JobQueueService`, `JobProcessor`, `manager.start_sources()`, and the existing `auto_provision_system_queues` loop — otherwise jobs submitted during that narrow startup window could hit the old `None`-project path.
5. **No schema changes.** Do not modify `Project` model in `daemon/repositories/project/models.py`.

---

## Deliverables

- [ ] `SYSTEM_DEFAULT_PROJECT_NAME` and `SYSTEM_DEFAULT_PROJECT_ID` in `daemon/constants.py`
- [ ] `ensure_system_default_project()` method on `SQLModelProjectRepository`
- [ ] `api.py` lifespan updated with bootstrap slot before services are wired
- [ ] System project ID stored in `SYSTEM_DEFAULT_PROJECT_ID` at startup
- [ ] Unit tests covering idempotent creation
- [ ] All existing tests still pass (`pytest tests/ -v`)
