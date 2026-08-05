# G7 Unique Index AttributeError — Coverage Gap

**Date:** 2026-08-04
**Bug:** `_ensure_blueprint_g7_unique_index` in `daemon/manager.py` used `project.id` instead of `project.project_id`, causing `AttributeError` at startup.
**Root cause:** `Project` model uses `project_id` as primary key (no `id` attribute). The iteration `for project in projects:` was untested at the manager level.

## Coverage Gap

Existing unit tests for `auto_dedup_cores` in `tests/unit/test_blueprint_repository.py` pass hardcoded `project_id` strings directly to the repository method. They never exercise the manager-level iteration that:

1. Calls `project_repo.list_projects(limit=10000)` → returns `list[Project]`
2. Iterates `for project in projects:` 
3. Passes `project.project_id` to `auto_dedup_cores()`

This manager-level path was the actual crash site. The bug slipped through because tests stopped at the repository boundary.

## Fix Applied

**Production:** `project.id` → `project.project_id` at 2 locations (lines 4379, 4384).

**Test:** New smoke test pack `tests/packs/g7_unique_index_smoke_test.py` binds the real unbound `_ensure_blueprint_g7_unique_index` function to a mocked `InstanceManager`, using `MagicMock(spec=["project_id"])` fakes that correctly lack an `.id` attribute — so any reintroduction of `project.id` would immediately fail the test.

## Lesson

When a function iterates ORM objects and passes a specific attribute, test that iteration path directly — not just the called function with hardcoded values. The repository-level test was necessary but insufficient; it validated the callee but not the caller's attribute access.

## Pattern to Remember

`MagicMock(spec=["project_id"])` is an effective guard for ORM attribute bugs: it raises `AttributeError` on `.id` access (mirroring the real model), making the test fail-fast if the wrong attribute is used.
