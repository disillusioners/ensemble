# Job Soft Delete — Phases 4 & 5 (FE Implementation)

## Summary
Implemented frontend service/model layer (Phase 4) and UI components (Phase 5) for job soft delete feature on branch `feature/job-soft-delete`.

## Phase 4: Service & Model Layer (commit `4421c02`)
- Added `deleted_at?: string | null` to `Job` interface (optional because new field, backward compatible)
- Added `include_deleted?: boolean` to `JobFilters`
- Added `isJobDeleted(job: Job): boolean` helper
- Added `softDeleteJob()` — `DELETE /api/jobs/{id}`, returns `Observable<Job>`, updates local signal
- Added `restoreJob()` — `POST /api/jobs/{id}/restore`, returns `Observable<Job>`, updates local signal
- Updated `listJobs()` to pass `include_deleted=true` when filter is set

## Phase 5: UI Components (commit `ae2b4f6`)
- JobCardComponent: `delete`/`restore` outputs, `isDeleted`/`canDelete`/`canRestore` computed values
- Delete button (warn color) shown for terminal status jobs; Restore button shown for deleted jobs
- Cancel/Retry buttons hidden when job is deleted
- Deleted jobs: 0.6 opacity, strikethrough message, "Deleted" badge
- JobsComponent: `showDeleted` signal, "Show Deleted" checkbox in filter bar
- Delete handler with undo snackbar (calls restoreJob on undo)
- Restore handler with full reload on success

## Key Patterns
- `softDeleteJob` and `restoreJob` return `Observable<Job>` (not void) — BE echoes updated job
- This matches `retryJob` pattern, not `cancelJob` pattern (which returns void)
- The undo snackbar pattern: show snackbar with action, subscribe to `.onAction()` for undo
- When "Show Deleted" is off, deleted jobs are removed from local list; when on, they're updated in-place
