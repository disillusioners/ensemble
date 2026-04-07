# Phase 1: Job Queue Completion Callback

**Date**: 2026-04-07
**Commit**: dfc9b97 — `feat(job-queue): implement job completion callback mechanism`

## What was implemented
- Job completion callback mechanism wiring instance lifecycle into job queue
- Jobs now transition PROCESSING → COMPLETED/FAILED when instances finish

## Key files modified
- `daemon/services/job_queue_service.py` — Added `get_job_by_instance()`, `get_job_by_instance_sync()`, `complete_job_sync()`, `result_summary` param on `complete_job()`
- `daemon/manager.py` — Added `_complete_job_for_instance()` helper, wired into `_process_queue()` success/failure paths and `terminate_instance()`
- `daemon/services/job_processor.py` — Removed premature `trigger_next_job()` from `_process_next_job()`

## Important notes
- `_fail_job()` already releases locks — plan mentioned it was a bug but it was already fixed
- `terminate_instance()` is sync — uses `_sync` variants of service methods
- `complete_job()` catches `ValueError` for already-completed jobs (idempotency)
- The `trigger_next_job()` is called by the helper after completion, NOT during job processing

## Opencode daemon issue
- Sessions consistently returned empty `parts` after ~10 successful operations
- The daemon was still processing — commits and edits went through, just no response text
- Workaround: check git status/diff directly to verify changes when daemon returns empty
