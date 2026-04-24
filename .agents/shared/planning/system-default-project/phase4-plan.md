# Phase 4: API & Visibility

## Objective

Make the system project discoverable but semi-hidden in the API. The system project must be accessible (e.g., an operator can look it up by ID), but it does not appear in default project listings and is flagged as system-reserved in all responses.

---

## Coupling

- **Depends on**: Phase 1 (System Project & Queue Bootstrap)
- **Coupling type**: independent (of Phases 2 and 3)
- **Shared files with other phases**: `daemon/routers/schemas.py` (also touched by Phase 2)
- **Shared APIs/interfaces**: None (purely API presentation)
- **Why this coupling**: Phase 4 only reads `SYSTEM_DEFAULT_PROJECT_NAME` from constants — a plain string that exists from Phase 1's first commit. It can run in parallel with Phases 2 and 3. The only file overlap is `schemas.py` (Phase 2 adds a validator, Phase 4 adds a field) — merge these if both phases run concurrently.

## Context

### Previous Phase Completed

Phase 1 delivered:
- System project `__system_default__` created at startup
- `SYSTEM_DEFAULT_PROJECT_NAME` constant available

### Current State

- `ProjectResponse` (schemas.py) has no `is_system` field.
- `GET /projects` (projects.py:186–203) calls `repo.list_projects()` with no filtering.
- `GET /projects/{project_id}` returns the project without any system flag.
- The system project (`__system_default__`) is already created by Phase 1, so it will appear in listings unless explicitly filtered.

### Target State

- `ProjectResponse` includes `is_system: bool = False` — derived from `project.name == SYSTEM_DEFAULT_PROJECT_NAME`.
- `GET /projects` accepts `exclude_system: bool = False` query param. When `True`, filters out the system project.
- `GET /projects/{project_id}` populates `is_system` but does not block access (operators can always look it up by ID).

---

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 4.1 | Add `is_system` field to `ProjectResponse` schema | Add `is_system: bool = Field(default=False, description="Whether this is a system-reserved project")`. Add to model config example. | `daemon/routers/schemas.py` |
| 4.2 | Add `is_system` to `ProjectListResponse` example | Update the example project dict in `ProjectListResponse` to include `"is_system": False`. | `daemon/routers/schemas.py` |
| 4.3 | Populate `is_system` in `_project_to_response()` | Import `SYSTEM_DEFAULT_PROJECT_NAME` from `daemon.constants`. Set `is_system=(project.name == SYSTEM_DEFAULT_PROJECT_NAME)`. | `daemon/routers/projects.py` |
| 4.4 | Add `exclude_system` query param to `GET /projects` | Add `exclude_system: bool = False` to `list_projects()` signature. Filter: `if exclude_system and p.name == SYSTEM_DEFAULT_PROJECT_NAME: continue`. | `daemon/routers/projects.py` |
| 4.5 | Add `exclude_system` to `list_projects_trailing` | Same param and logic as 4.4. | `daemon/routers/projects.py` |
| 4.6 | Add API tests for `GET /projects?exclude_system=true` | Test: default listing includes system project; `exclude_system=true` excludes it; `exclude_system=false` includes it. | `tests/api/test_projects.py` (new) |
| 4.7 | Add API tests for `ProjectResponse.is_system` | Test: system project returns `is_system=True`; regular project returns `is_system=False`. | `tests/api/test_projects.py` |
| 4.8 | Run full test suite | All tests pass. | `pytest tests/ -v` |

---

## Key Files

- `daemon/routers/schemas.py` — Add `is_system` field to `ProjectResponse`
- `daemon/routers/projects.py` — Add `exclude_system` param to list endpoints; populate `is_system` in `_project_to_response()`
- `tests/api/test_projects.py` — **New file** — API tests for visibility controls

---

## Constraints

1. **Do not block access by ID.** Even though the system project is filtered from list results, `GET /projects/{project_id}` must still return it if the caller knows the ID. This allows operators and internal tools to inspect it.
2. **Backward-compatible default.** `exclude_system=False` by default — existing API consumers see no change in behavior.
3. **Name-based detection.** `is_system` is derived by comparing `project.name` to `SYSTEM_DEFAULT_PROJECT_NAME`. Do not add a new column to the `Project` model; doing so would require a schema migration. Name-based detection is sufficient and avoids DB schema changes.
4. **No changes to `ProjectCreateRequest`.** The system project is created programmatically at startup, not via the API. There is no need to accept `is_system` on project creation, and doing so would be a security concern.
5. **`pause-queue` and `set-queue-status` endpoints.** These operate on existing projects. They should continue to work on the system project (operators may legitimately want to pause the system queues). No changes needed.

---

## Deliverables

- [ ] `is_system: bool` field added to `ProjectResponse` schema
- [ ] `is_system` populated in `_project_to_response()` using name comparison
- [ ] `exclude_system: bool = False` query param on `GET /projects` and its trailing-slash variant
- [ ] System project filtered out when `exclude_system=True`
- [ ] API tests for visibility controls
- [ ] All tests pass (`pytest tests/ -v`)
