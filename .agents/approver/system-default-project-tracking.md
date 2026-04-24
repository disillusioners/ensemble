# Plan Improvement Tracking: System Default Project

## Iteration 001 — REJECTED

**Date**: 2026-04-25
**Verdict**: REJECTED

### Blocking Issues

1. **Retry path normalization gap** — `retry_job()` at `job_queue_service.py:539` passes the original job's `project_id` directly to `enqueue()` with no normalization. The REST retry endpoint (`jobs_management.py`) also delegates without normalization. Phase 2 normalizes at HTTP/tool boundaries only — internal callers like retry bypass this. Retrying an orphan job creates a new orphan job. Perpetuating the exact bug the plan aims to fix.
   - Expected: All code paths that create jobs normalize `project_id` before persistence
   - Found: Phase 2 normalizes at 2 of 4+ entry points (HTTP POST, tool interface); misses internal retry paths and service-to-service calls

2. **Missing orphan job migration prerequisite** — Phase 3 task 3.1 removes the C5 fallback (the ONLY code that currently processes orphan jobs with `project_id=NULL`). No task exists to backfill existing `project_id=NULL` rows before removing C5. Risk #1 says "document as manual step" but provides no SQL, no script, no verification. Existing orphans become permanently stuck with no processing path.
   - Expected: Explicit migration step that backfills `project_id=NULL` rows to system project ID before C5 removal
   - Found: Vague "documented manual step" with no concrete action, no prerequisite task before 3.1

### Notes (Non-blocking)
- Phase 3 task 3.2 `wait_for_job()` fallback with `project_id=None` is acceptable — addressed by plan, polling fallback is safe
- Phase 3 line reference for `job_queue_service.py` lines 299-313 is slightly inaccurate (describes behavior, not an explicit `None + None` branch) — cosmetic only
- Plan's approach to keeping `JobQueueService.enqueue()` `None` branch as defense-in-depth (task 3.3) is good


## Iteration 002 — APPROVED

**Date**: 2026-04-24
**Verdict**: APPROVED

### Previous Blocking Issues — Status

1. **Retry path normalization gap** → **RESOLVED**. Plan revised: `enqueue()` is now the canonical chokepoint (Phase 2 task 2.2). `retry_job()` calls `self.enqueue()` and is covered. Explicit test added (task 2.9).
2. **Missing orphan job migration** → **RESOLVED**. Phase 3 task 3.0 added: explicit SQL migration backfilling NULL rows with verification. Task 3.10 adds migration verification test.

### Evaluation Findings

**Code path verification**: 15/16 references confirmed exact match. One partial match (api.py line ~131 — `manager._project_repository` assignment is internal to manager, not in api.py). Non-blocking — Phase 1 task 1.4 correctly describes the placement as "after repos are set" which is accurate.

**Chokepoint verification**: `enqueue()` confirmed as single creation path. `JobRetryEngine.maybe_retry()` does in-place status change (not creation) — not a gap.

**Migration system note**: Plan references `op.execute` (Alembic API) in task 3.0, but project uses custom raw SQL migrations. Plan provides fallback option (hardcode deterministic UUID) which is valid for raw SQL.

### Notes (Non-blocking)
- Task 3.0 should use raw SQL with hardcoded UUID or subquery instead of `op.execute`
- `JobRetryEngine.maybe_retry()` in-place retry is safe post-migration (preserves existing non-NULL project_id)
