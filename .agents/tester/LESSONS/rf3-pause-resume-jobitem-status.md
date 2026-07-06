# RF3 Pause/Resume JobItem Status Issue

**Date**: 2026-07-06
**Branch**: `feature/job-as-front-primitive-full`
**Severity**: Architecture-level (NOT quick-fixable)

## Problem

After the full Job-as-Front-Primitive cutover (Phases 1-5), the pause/resume E2E test (`test_pause_after_spawn_then_resume`) still fails. The POC symptom (JobItem mirror incorrectly `cancelled` after resume) is gone, but a **different** symptom replaced it:

**JobItem stays `paused` after resume — never transitions back to `processing`.**

## Root Cause

1. Phase 4 (Job-as-Queue-Proxy) deliberately **deleted** the job `PAUSED → PROCESSING` UPDATE from `_resume_cascade_db_sync` (see comment at `daemon/services/instance_lifecycle.py:2371-2377`)
2. The drift reconciler (`job_recovery_service.py:223-228`) flips job `PROCESSING → PAUSED` on pause
3. Nothing flips it back on resume
4. `_process_resume_finalize` calls `_finalize_job` whose guard is `WHERE status='processing'` → rowcount drops to 0 → finalize is a silent no-op
5. Test cleanup `terminate_instance` is what eventually sets status to `cancelled`

## Why Not Quick-Fixed

Restoring the transition would partially revert Phase 4's documented design choice. The Phase 6 test expectations (added in commit `f79ce558`) predate and contradict the Phase 4 decision.

## Impact

- **Instance-level pause/resume works fine** — the instance correctly transitions PAUSED → RUNNING
- **JobItem status is wrong** — stays `paused` instead of `processing` after resume
- **E2E Test 2 fails** at step 7b assertion

## Resolution Options

1. **Restore the transition** — Add `PAUSED → PROCESSING` UPDATE back in `_resume_cascade_db_sync` (reverts Phase 4 decision)
2. **Update test contract** — Accept that JobItem stays `paused` and check INSTANCE status instead (contradicts Phase 6 design intent)
3. **Make _finalize_job accept paused status** — Change guard to `WHERE status IN ('processing', 'paused')` (minimal change)

## Related

- E2E Test 4 (wave spawn) also fails — leader stuck in `waiting_children` when second child doesn't complete
- ensure.md Reqs 6 & 8 block testing completion
