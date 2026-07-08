# System Jobs Cleanup — Testing Lessons

## Date: 2026-07-07

### Feature Overview
POST /api/jobs/cleanup endpoint cancels all non-terminal jobs across ALL projects.
- Two-stage: batch_cancel_queued() + per-row cancel_job() for active
- **Critical invariant**: Must exclude `job_type='message'` JobItems (mirror rows)
- Response: `{cancelled_queued, cancelled_active, total_processed}`

### Key Findings

#### 1. Message-Type JobItem Exclusion Works Correctly
- E2E test confirmed: 24 pre-existing message-type JobItems in `__system_default__` were untouched
- These are agent-to-agent mirror rows from Job-as-Front-Primitive pattern
- Both `batch_cancel_queued()` and `find_active_jobs()` filter `job_type != 'message'`
- Source field (`internal_agent:*`) is NOT reliable; `job_type` is the correct filter

#### 2. Pre-Existing Test Drift Found
- `test_phase5_jobs_router.py` had 2 stale assertions from Phase 4/7a refactor
- `test_complete_job_uses_release_job_lock`: Asserted old `release_by_instance=False` pattern
- `test_jobs_module_exports_terminal_statuses`: Expected `set`, actual is `frozenset`
- Fixed in commit `5e3907c7`, unrelated to cleanup feature

#### 3. Frontend Coverage Gap
- `system-cleanup-confirm-dialog.component.spec.ts` is MISSING
- Component exists but has no tests
- Page-level tests mock MatDialog, so dialog flow is untested
- Non-blocking but should be added

#### 4. Endpoint is Idempotent
- Second cleanup call on clean system returns 0/0/0
- Terminal jobs correctly skipped
- No errors on empty state

#### 5. Frontend Method Naming
- Actual method name is `cleanupAllJobs()` (not `cleanupSystemJobs()`)
- Naming is consistent across service, component, and spec files
