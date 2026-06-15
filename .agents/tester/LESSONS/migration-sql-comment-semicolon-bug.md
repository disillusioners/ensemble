# Quick Fix: Migration SQL Comment Semicolon Bug

**Date:** 2026-06-15  
**Commit:** `4a8a4dc`  
**Session:** `infra-regression`  
**Severity:** Critical (60 test failures)

## Problem
The infra migration file `daemon/migrations/versions/20260616_000001_create_infra_asset_storage_tables.sql` had a semicolon (`;`) inside a SQL comment on line 90: `-- ... reconstructable; the FK ...`

The migration runner splits SQL on `;` without SQL-aware parsing, so this semicolon inside a comment broke the `CREATE TABLE infra_asset_history` statement into two invalid pieces.

## Impact
- 60 test failures across `test_manager.py`, `test_progressive_dispatch.py`, `test_spawn_limit_edge_cases.py`, and other tests that depend on the migration succeeding
- These failures were NOT pre-existing — they were caused by the Phase 1.5 migration file (commit `353c236`)

## Fix
Changed the comment from `reconstructable; the FK` to `reconstructable. The FK` (1 line, single file)

## Lesson Learned
**Never use semicolons inside SQL comments when the migration runner splits on `;`.** The migration runner does naive string splitting, not SQL-aware parsing. This is a gotcha for all future migration files.

## Prevention
- Migration review checklist should include: "grep for `;` inside `--` comment lines"
- Consider updating the migration runner to use SQL-aware statement splitting in the future
