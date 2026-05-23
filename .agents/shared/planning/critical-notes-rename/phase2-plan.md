# Phase 2: API & Service Layer — Router Schemas, API Endpoints, Manager Formatting

## Objective
Update the API surface: response schemas, router endpoint logic, and the `format_project_context()` manager method that renders critical notes into agent system prompts.

## Coupling
- **Depends on**: Phase 1 (Core Layer)
- **Coupling type**: tight
- **Shared files with other phases**: 
  - `daemon/routers/projects.py` (Phase 4 tests against)
  - `daemon/routers/schemas.py` (Phase 4 tests against)
  - `daemon/manager.py` (Phase 4 tests against)
- **Why this coupling**: Phase 2 imports `CriticalNotes`, `Project.critical_notes` from Phase 1's models. Same Python runtime namespace.

## Context
Phase 1 has renamed all core types. This phase updates the consumers of those types in the API/service layer.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update response schema | In `schemas.py`: rename `critical_experience: list[dict] | None` → `critical_notes: list[dict] | None`. Check for any other schema references. | `daemon/routers/schemas.py` |
| 2 | Update `_project_to_response()` | In `projects.py` line ~100: update the field mapping from `critical_experience` → `critical_notes`. Ensure the response dict uses the new key. | `daemon/routers/projects.py` |
| 3 | Update `format_project_context()` | In `manager.py` lines 207-277: change "### ⚡ Critical Experience" → "### ⚡ Critical Notes". Update any `critical_experience` field access to `critical_notes`. Update all comment/docstring references. | `daemon/manager.py` |
| 4 | Check for API endpoint parameters | Search for any API endpoints that accept `critical_experience` as a query param or request body field. Rename to `critical_notes`. | `daemon/routers/projects.py` |
| 5 | Update repository layer | Check `daemon/repositories/project/` for any `critical_experience` references in repository methods, SQL queries, or data access patterns. Rename to `critical_notes`. | `daemon/repositories/project/` |

## Key Files
- `daemon/routers/schemas.py` — Response/request schemas
- `daemon/routers/projects.py` — API endpoint handlers
- `daemon/manager.py` — `format_project_context()` rendering
- `daemon/repositories/project/` — Repository layer (if references exist)

## Constraints
- API response JSON key changes from `critical_experience` to `critical_notes` — this is a breaking API change. Acceptable per requirements.
- The rendered section in agent prompts changes heading from "Critical Experience" to "Critical Notes" — all agents will see the new heading.
- Keep the same rendering logic (markdown formatting, bullet structure), just change names.

## Deliverables
- [ ] Schema field renamed in `schemas.py`
- [ ] `_project_to_response()` uses new field name
- [ ] `format_project_context()` renders "### ⚡ Critical Notes"
- [ ] All `critical_experience` references removed from API/service files
