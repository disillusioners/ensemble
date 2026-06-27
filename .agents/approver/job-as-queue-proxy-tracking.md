# Job as Queue Proxy — Approval Tracking

Current Plan: Job as Queue Proxy
Tracking File: job-as-queue-proxy-tracking.md
Iterations: 1

---

## Iteration 001 — 2026-06-27

**Status:** APPROVED (with notes)

### Verification Performed

| Target | Claim | Verified | Method |
|--------|-------|----------|--------|
| V1 | `_finalize_job_db_sync:2209-2212` derives job status from instance | ✅ Confirmed | Direct read |
| V2 | `JobStatus` is 7-value enum at models.py:21-37 | ✅ Confirmed | Direct read |
| V3 | Status-drift warning at work_resolver.py:692-712 | ✅ Confirmed | Direct read |
| V4 | Pause cascade dual-writes at instance_lifecycle.py:2138-2165 | ✅ Confirmed | Direct read |
| V5 | `count_active_jobs*` uses PENDING+PROCESSING (C2 fix valid) | ✅ Confirmed | Direct read |
| V6 | `_ACTIVE_JOB_IDS_SUBQUERY` uses status IN (pending,processing) (C3 fix valid) | ✅ Confirmed | Direct read |
| V7 | `maybe_retry` is in complete_job:1579/1657, NOT in _finalize_job_db_sync | ✅ Confirmed | Grep |
| V8 | No CONSTRAINT TRIGGER precedent in codebase | ✅ Confirmed | Grep |
| V9 | `_ensure_postgres_columns()` at manager.py:1653 is migration pattern | ✅ Confirmed | Grep |
| V10 | §6.1 instance.status write inventory is complete | ⚠️ Incomplete (non-blocking) | Council + direct read |

### Council Finding (V10)

The §6.1 inventory misses 6 write sites using `parent.status =` variable alias instead of `instance.status =`:
- 3 terminal: `error_reporting.py:287`, `child_reports.py:842`, `child_reports.py:1606` (COMPLETED)
- 3 non-terminal: `error_reporting.py:317`, `child_reports.py:875`, `child_reports.py:1628` (WAITING_CHILDREN)

All 6 are in `bus is None` dead-code branches behind A8/A9 hard-error RuntimeError guards — unreachable in production. The structural guarantee holds for all reachable paths. However, the plan's claim of "COMPLETE inventory" and "grep-verifiable" boundary is technically inaccurate; a Phase 4 grep will find these `parent.status` sites outside the boundary, producing false alarms.

### Notes (non-blocking)
- `update_status()` (repository.py:603) is effectively dead code for status writes — it delegates to `update()` which raises ValueError on status kwarg. Plan lists it as a write site; it isn't one.
- These are documentation accuracy issues, not architectural flaws. The plan's core design is sound.
