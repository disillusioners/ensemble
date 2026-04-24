# Quick Fixes: system_default_project Feature Testing (2026-04-24)

## Fix 1: Missing SYSTEM_DEFAULT_PROJECT_ID fixture
- **File**: `tests/test_job_queue_tools.py`
- **Root Cause**: Tests called `normalize_project_id()` but `SYSTEM_DEFAULT_PROJECT_ID` was None (not initialized by conftest)
- **Fix**: Added autouse fixture that sets `constants.SYSTEM_DEFAULT_PROJECT_ID` and `project_normalizer.SYSTEM_DEFAULT_PROJECT_ID` to test value before each test
- **Commit**: `9ca599f`
- **Lesson**: When adding new constants that are used by normalizers/services, existing test files that exercise those paths need fixtures to set the constants

## Fix 2: Integration test pytest path and schema column
- **File**: `test/packs/integration_test.sh`, `tests/integration/test_migration.py`
- **Root Cause**: Wrong pytest command (should be `python -m pytest`), wrong column name in test (`project_metadata` → `metadata`)
- **Commit**: `9f57afb`

## Fix 3: Migration SQL bugs (Critical)
- **File**: `daemon/migrations/versions/20260424_000001_backfill_null_project_ids.sql`
- **Root Cause**: Multiple issues in the migration script:
  1. Wrong column name: `project_metadata` → `metadata` (projects table)
  2. Missing NOT NULL column: `job_queue_paused` in projects INSERT
  3. Wrong column order: `is_paused` before `is_system` in job_queues INSERT
  4. Missing column: `default_max_retries` in job_queues INSERT
  5. `strftime()` with commas caused statement splitting issues
  6. Hardcoded queue_id that might not match existing system FIFO queue
- **Fix**: Corrected all column names/order, added missing columns, replaced strftime with datetime, used COALESCE with subquery for dynamic queue_id
- **Commit**: `63853c7`
- **Lesson**: Migration scripts that INSERT into tables with many columns should cross-reference the actual schema carefully. Always test migrations against real DB schema.
