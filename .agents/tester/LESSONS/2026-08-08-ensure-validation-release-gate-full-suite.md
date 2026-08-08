# LESSON: ensure.md Release Gate full-suite failures (pre-existing, not from lifecycle hooks)

**Date:** 2026-08-08
**Feature:** Instance Lifecycle Hooks (ensure.md validation)
**Severity:** 🟡 Important (blocks Release Gate but pre-existing)
**Found by:** Full suite worker (instance bcac87a9)

## Summary

Full non-integration suite: **55/60 packs PASS, 5 FAIL**. All 5 failures are pre-existing issues unrelated to the Instance Lifecycle Hooks feature.

## Pre-existing Failures (5 packs)

### 1-3. Migration SQLite incompatibility (3 packs: c2_pg_manager, c2_core_regression, shared_context_regression)
- Migration `20260714_000001_widen_job_queue_type_constraint.sql` uses PG-only `ALTER TABLE ... DROP CONSTRAINT`
- Fails on SQLite even though the comment claims SQLite 3.35+ supports it
- **Fix:** Rewrite as SQLite-compatible table rebuild (not quick-fix eligible)

### 4. Agent registry fixture leak (core_unit_test)
- `tests/test_agents_api.py` leaks 33 real agents instead of 1 fixture agent
- Fixture patches `BASE_DIR` but endpoint reads `registry.list_all_grouped()` global state
- **Fix:** Redesign fixture to isolate registry state (not quick-fix eligible)

### 5. Self-deadlock fix test breakage (child_parent_lifecycle_regression_test)
- `test_process_message_blocked_by_cross_system_guard` broken by commit `338a72b0`
- Guard correctly excludes the candidate's own row; test needs a sibling RUNNING task
- **Fix:** Test architecture change (needs sibling task seeding, > 20 lines)
- **Note:** This same failure appeared in the concurrency pack run

## Quick Fixes Applied (commits 665c6215 + fdfb19ca)
- 7 pack scripts repointed to existing test files after legacy test deletion
- 2 stub classes updated with `_deferred_watchover_terminate` attribute
- 2 test logic fixes (hard_delete filter, opencode resume text)
- 1 environment-aware skip (port 8079 detection)
- 1 test infra fix (PYTHONPATH in mock_job_queue pack)

## Impact on Instance Lifecycle Hooks
None. None of the 5 failing packs are related to the lifecycle hooks feature. The feature's own test files all pass.

## ensure.md Improvement Notices
1. ⚠️ 7 pack scripts reference deleted test files — pack paths need updating
2. ⚠️ Migration uses PG-only syntax — needs SQLite-compatible rewrite
