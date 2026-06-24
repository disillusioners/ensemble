# Lesson: schedule_retry Status Guard Excluded 'cancelled' (Latent Regression)

**Date:** 2026-06-24
**Feature:** report-lane decoupling (regression sweep)
**Tags:** regression, schedule_retry, orphan-recovery, task/repository.py

## Bug

`daemon/repositories/task/repository.py` `schedule_retry()` had a WHERE-clause status guard:
```sql
AND status IN ('running', 'failed')
```
This was added in phase 3 (commit 17551447) to prevent clobbering concurrent terminal-state writes. It accidentally excluded `'cancelled'`, breaking the phase-5 orphan-recovery path (commit 0cf80785).

## Impact

A CANCELLED task with `retry_scheduled=False` (the "orphaned" state that `find_orphaned_cancelled_tasks()` + `recover_on_startup()` exist to repair) could NOT be recovered — `schedule_retry` returned None for it. 3 tests in `test_stale_recovery_v2.py` failed.

## Detection

Surfaced during the report-lane decoupling regression sweep (re-running related test suites after the hot-path `claim_pending_task` change). The bug was latent — introduced weeks earlier by the phase-3 status-guard tightening, but no orphan-recovery test had been re-run against the new code.

## Fix

Added `'cancelled'` to the eligible status set (14 lines net). The `retry_scheduled=false` and `retry_count < max_retries` guards still prevent duplicate retry creation; `completed` remains excluded.

**Commit:** `290eafbd` — `fix: restore orphan-retry path in schedule_retry status guard`

## Lesson

When tightening a status guard (`AND status IN (...)`), always audit ALL the states the method is expected to handle, especially for recovery/repair paths that operate on unusual states (cancelled, orphaned, stale). A regression sweep that re-runs sibling test suites is essential when changing hot-path repository code.
