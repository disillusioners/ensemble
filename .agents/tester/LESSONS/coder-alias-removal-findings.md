# Lessons: coder→developer Alias Removal

## Date: 2026-07-10
## Branch: feature/coder-alias-removal

## Key Finding: Alias Removal is Clean
The `coder→developer` alias removal from `AGENT_ID_ALIASES` in `daemon/registry.py` is fully functional with **zero regressions**. All 17 validation assertions pass, all 10 affected test files pass (excluding pre-existing failures), and the full regression suite shows no new failures.

## Wanderer→Coder Delegation Restored
- Wanderer's `team_members` now contains `["coder"]` (was empty before)
- Wanderer's `tools.allow` now includes `"instance"` (for spawning coder instances)
- `_check_team_membership("wanderer", "coder")` returns `None` (authorized)
- Wanderer's soul.md documents coder delegation for complex coding tasks
- Read-only discipline maintained (no write_file, edit_file, inner_soul)

## `_check_team_membership` Signature Gotcha
The brief showed `_check_team_membership(registry, "wanderer", "coder")` but the real signature is `_check_team_membership(caller_agent_id, requested_agent_id)` — no registry parameter. This is in `daemon/tools/instance.py:237`.

## Pre-existing Failures Found (Not Caused by Alias Removal)

### 1. Duplicate Migration Version (5 failures)
- **File**: `tests/unit/test_coder_developer_migration.py`
- **Root cause**: Two migration files share version `20260628_000002`:
  - `daemon/migrations/versions/20260628_000002_drop_admission_legacy.sql`
  - `daemon/migrations/versions/20260628_000002_drop_job_queue_legacy_columns.sql`
- **Impact**: `UNIQUE constraint failed: schema_migrations.version` when test bulk-inserts migrations
- **Introduced by**: Phase 5 Batch 2 (commit `41633433`)
- **Fix needed**: Rename or delete one of the duplicate migration files (architectural decision)

### 2. Missing TODO_NOT_FOUND in test (1 failure, FIXED)
- **File**: `tests/test_models.py:330`
- **Root cause**: `ErrorCodes` enum gained `TODO_NOT_FOUND` but test's `expected_codes` wasn't updated
- **Fix**: Added `"TODO_NOT_FOUND"` to expected list (commit `cc270882`)

### 3. Stale Mock for Phase 5 enqueue_message_job Cutover (2 failures, FIXED)
- **File**: `tests/test_api.py:62-69, 810-815, 858`
- **Root cause**: Phase 5 changed `enqueue_message` → `enqueue_message_job` but test mock wasn't updated
- **Fix**: Added `manager.enqueue_message_job` mock (commit `039e1c0e`)

## Test Hangs (Pre-existing)
- `tests/opencode/test_tools.py` hangs on both this branch AND base commit — pre-existing test pollution issue
- Some tests in full suite hang due to event loop issues — all pre-existing

## Quick Fixes Applied
1. `tests/test_models.py` — +1 line, added TODO_NOT_FOUND (commit cc270882)
2. `tests/test_api.py` — +7/-3 lines, updated mock for enqueue_message_job (commit 039e1c0e)

Both fixes are pre-existing issues exposed by running the test files, not caused by alias removal.
