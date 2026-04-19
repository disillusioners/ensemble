# Phase 4: FE — Service & Model Layer

## Objective
Update the frontend Job model to include `deleted_at`, add `softDelete()` method to `JobService`, and update the `JobFilters` type to support `include_deleted`.

## Coupling
- **Depends on**: Phase 3 (API contract must be stable)
- **Coupling type**: loose — only depends on the API response shape, not BE implementation
- **Shared files with other phases**: `frontend/src/app/models/job.model.ts`, `frontend/src/app/services/job.service.ts`
- **Shared APIs/interfaces**: `Job` interface, `JobFilters` interface, `JobService` methods
- **Why this coupling**: UI components in Phase 5 use these model/service definitions.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `deleted_at` to `Job` interface | Add `deleted_at?: string \| null` field | `frontend/src/app/models/job.model.ts` |
| 2 | Add `include_deleted` to `JobFilters` | Add `include_deleted?: boolean` to the filters interface | `frontend/src/app/models/job.model.ts` |
| 3 | Update `listJobs()` in `JobService` | Add `include_deleted` to `HttpParams` when filter is true | `frontend/src/app/services/job.service.ts` |
| 4 | Add `softDeleteJob()` to `JobService` | New method: `DELETE /api/jobs/{id}`. On success, update local jobs signal (mark as deleted or remove). | `frontend/src/app/services/job.service.ts` |
| 5 | Add `restoreJob()` to `JobService` | New method: `POST /api/jobs/{id}/restore`. On success, update local jobs signal. | `frontend/src/app/services/job.service.ts` |
| 6 | Add `isDeleted` helper to model | Helper function `isJobDeleted(job: Job): boolean` for template use | `frontend/src/app/models/job.model.ts` |

## Key Files
- `frontend/src/app/models/job.model.ts` — Job interface, JobFilters interface, helper functions
- `frontend/src/app/services/job.service.ts` — JobService (listJobs, softDeleteJob, restoreJob)

## Constraints
- Angular standalone components with signals
- Material UI components
- HTTP via `HttpClient` with `/api/jobs` base path

## Detailed Implementation Reference

### Updated `Job` interface
```typescript
export interface Job {
  // ... existing fields ...
  deleted_at?: string | null;  // NEW: soft-delete timestamp
}
```

### Updated `JobFilters`
```typescript
export interface JobFilters {
  status?: JobStatus[];
  source?: JobSource;
  agent_id?: string;
  project_id?: string;
  queue_id?: string;
  include_deleted?: boolean;  // NEW
}
```

### `softDeleteJob` method
```typescript
softDeleteJob(jobId: string): Observable<Job> {
  return this.http.delete<Job>(`${this.API_BASE}/${encodeURIComponent(jobId)}`).pipe(
    tap((updatedJob) => {
      this.jobs.update((jobs) =>
        jobs.map((job) =>
          job.job_id === jobId ? { ...job, deleted_at: updatedJob.deleted_at } : job
        )
      );
    }),
    catchError((err) => {
      this.error.set(err.message || 'Failed to delete job');
      throw err;
    })
  );
}
```

### Updated `listJobs` (include_deleted support)
```typescript
listJobs(filters?: JobFilters): Observable<Job[]> {
  let params = new HttpParams();
  if (filters) {
    // ... existing filters ...
    if (filters.include_deleted) params = params.set('include_deleted', 'true');
  }
  // ...
}
```

## Deliverables
- [ ] `Job` interface has `deleted_at` field
- [ ] `JobFilters` has `include_deleted` field
- [ ] `JobService.softDeleteJob()` implemented
- [ ] `JobService.restoreJob()` implemented
- [ ] `listJobs()` passes `include_deleted` query param
