# Dead-Letter Job Retry Feature Implementation

## Date: 2026-04-18

## Feature Summary
Extended the jobs system to support retry/replay of DEAD_LETTER status jobs with full UI support.

## Key Architecture Decisions

### Backend Retry Patterns
- **FAILED retry**: Creates a NEW JobItem via enqueue(), original stays FAILED
- **DEAD_LETTER retry**: Reuses EXISTING JobItem via `DeadLetterService.replay_from_dlq()`, resets to PENDING, deletes DLQ entry atomically
- Both handled by same endpoint `POST /api/jobs/{job_id}/retry`

### Critical Review Findings
1. **Response schema must include DLQ fields** — `dlq_reason`, `retry_count`, `moved_to_dlq_at` must be in `JobResponse` for frontend to display DLQ info. Added to `daemon/routers/schemas.py`.
2. **Frontend-backend response shape alignment** — DLQ list returns paginated `{items, total}`, not a plain array. Frontend must extract `.items`.
3. **TERMINAL_STATUSES** must include DEAD_LETTER or SSE streams hang indefinitely for dead_letter jobs.
4. **Type mismatches** — Backend DLQ replay returns `{job_id, status, message}`, not a full Job object.

### Frontend Patterns
- JobStatus type extended with `'dead_letter'`
- Purple color `#7C3AED` for dead_letter (distinct from red FAILED)
- `report_problem` icon for dead_letter status
- `canRetry` computed extended for both `failed` and `dead_letter`
- statusLabel must handle snake_case: `.replace(/_/g, ' ')` for display

## Files Modified
- `daemon/routers/jobs.py` — retry endpoint, TERMINAL_STATUSES, _job_to_response
- `daemon/routers/schemas.py` — JobResponse with DLQ fields
- `daemon/routers/dlq.py` — bulk replay-all endpoint
- `frontend/src/app/models/job.model.ts` — types, interfaces
- `frontend/src/app/services/job.service.ts` — DLQ API methods
- `frontend/src/app/components/job-card/` — canRetry for dead_letter
- `frontend/src/app/pages/jobs/` — Dead Letter filter, Retry All button
- `frontend/src/app/components/job-detail-drawer/` — DLQ info section

## Commit: 61c498e
