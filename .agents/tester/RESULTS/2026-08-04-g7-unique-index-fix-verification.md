# G7 Unique Index Fix Verification

**Date:** 2026-08-04
**Worker Instances:** b32a8c15 (code-verify), 2d572c66 (startup-test), 46ee2bc2 (commit)
**Bug:** `AttributeError: 'Project' object has no attribute 'id'` in `_ensure_blueprint_g7_unique_index` at daemon startup
**Fix:** `project.id` → `project.project_id` (2 locations in `daemon/manager.py`)

## Summary

| Check | Result |
|-------|--------|
| Code verification (fix correct & complete) | ✅ PASS |
| Startup smoke test (no crash on startup path) | ✅ PASS (3/3, 1.16s) |
| Edge cases (empty project list, no blueprints) | ✅ PASS |
| Other `project.id` bugs in same file | ✅ NONE FOUND |
| **Overall** | **✅ RESOLVED** |

## Code Verification Evidence

**Function:** `_ensure_blueprint_g7_unique_index` in `daemon/manager.py:4318`

Both replacements confirmed:
- Line 4379: `auto_dedup_cores(project.project_id)` ✅
- Line 4384: `logger.warning("G7 auto-dedup failed for project %s: %s", project.project_id, exc)` ✅

- `project.id` references remaining in entire 6573-line `daemon/manager.py`: **0**
- `Project` model (`daemon/repositories/project/models.py:194`): PK is `project_id: str`, **no `id` attribute**
- `auto_dedup_cores(self, project_id: str) -> int` (blueprint/repository.py:51): correctly typed, returns 0 when ≤1 core exists

**Call chain:** `InstanceManager.__init__` (line 794) → `_ensure_blueprint_g7_unique_index()`, after both `_project_repository` (line 749) and `_blueprint_repo` (line 762) are constructed.

## Startup Smoke Test

**Pack:** `tests/packs/g7_unique_index_smoke_test.sh` + `.py` (NEW — first pack in `tests/packs/`)

Exercises the real `_ensure_blueprint_g7_unique_index` with mocked `InstanceManager`, binding the actual unbound function to a fake `self`. Uses `MagicMock(spec=["project_id"])` fakes that mirror the real Project shape (no `.id` attribute — accessing it raises `AttributeError`, so the test would fail if the bug were reintroduced).

| Scenario | Result |
|----------|--------|
| Non-empty project list → `auto_dedup_cores` called per project with correct `project_id` | ✅ PASS |
| Empty project list → graceful no-op, zero dedup calls | ✅ PASS |
| `_blueprint_repo is None` → defensive path skipped, no crash | ✅ PASS |

```
=== Test Pack: G7 Unique Index Startup Smoke ===
RESULT: PASS
Tests run: 3 | Passed: 3 | Failed: 0
Actual runtime: 1.16s
```

## Coverage Gap Found

Existing G7 tests in `tests/unit/test_blueprint_repository.py` exercise repo-level `auto_dedup_cores` with hardcoded project_id strings — they never iterate a `Project` list and never trigger the `for project in projects: project.project_id` path. The bug existed specifically on that iteration line. The new smoke test pack fills this gap.

## Code Changes
- `tests/packs/g7_unique_index_smoke_test.sh` — NEW (12 lines, outer wrapper)
- `tests/packs/g7_unique_index_smoke_test.py` — NEW (145 lines, test implementation)
- Production code: no changes (fix already applied)
- Commit: `5ace4a448c8d0c70323d8c80010cbdbc7cafa788` ("test: add G7 unique index startup smoke test (3/3 PASS)")
