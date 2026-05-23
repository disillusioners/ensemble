# Critical Notes Table Refactoring — Quick Fixes (2026-05-26)

## Commit: 39b7a35

### Fix 1: Migration SQL Syntax
- **File**: `daemon/migrations/versions/20260524_000001_create_critical_notes_table.sql`
- **Issue**: `DROP COLUMN IF EXISTS` not supported by SQLite 3.51.3
- **Fix**: Replaced with comment (SQLite doesn't require explicit column removal — columns are ignored when not in use)

### Fix 2: Test Parameter Update
- **File**: `tests/test_project_history_functions.py`
- **Issue**: Test set `critical_notes` as project attribute, but `format_project_context()` now reads from separate parameter (refactoring moved to dedicated table)
- **Fix**: Updated test to pass `critical_notes` as function parameter

## Pattern Learned
After moving data from a JSON column to a dedicated table, tests that accessed data through the old model (as object attributes) need to be updated to pass data through the new API (function parameters, repository calls, etc.)
