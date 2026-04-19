# Architecture Decisions: Job Soft Delete

## Decision 1: Soft Delete via `deleted_at` column (not status change)

**Context**: We could either add a new `deleted` status or a separate `deleted_at` timestamp column.

**Decision**: Use `deleted_at` nullable timestamp column.

**Rationale**:
- Adding a `deleted` status would break the existing state machine (transitions to/from `deleted`)
- A deleted job is orthogonal to its execution status — a completed job can be deleted, a failed job can be deleted
- `deleted_at` is a well-established pattern (Laravel, Django, Rails all use this)
- Allows querying "when was this deleted?" which a boolean or status doesn't

## Decision 2: `DELETE /{job_id}` handles both cancel and soft-delete

**Context**: The existing `DELETE` endpoint cancels active jobs. We need a soft-delete for terminal jobs.

**Decision**: `DELETE /{job_id}` is overloaded: cancels PENDING/PROCESSING jobs, soft-deletes terminal jobs. Also add `POST /{job_id}/cancel` for explicit cancel.

**Rationale**:
- RESTful semantics: DELETE removes from view, which is what soft-delete does
- Backward compatible: existing API users calling DELETE on active jobs still get cancel behavior
- New `POST /{job_id}/cancel` provides an explicit cancel endpoint for clarity

## Decision 3: Repository-level exclusion, not service-level

**Context**: We could filter deleted jobs at the service layer or the repository layer.

**Decision**: Filter at the repository layer — every execution-path query includes `WHERE deleted_at IS NULL`.

**Rationale**:
- Repository is the single source of truth for data access
- Service layer might forget to filter, leading to deleted jobs being executed
- Repository is tested in isolation, making it easier to verify exclusion
- Defense in depth: even if a new service method is added, the repository ensures safety

## Decision 4: Keep `get()` and `atomic_transition()` unfiltered

**Context**: Should `get(job_id)` also exclude deleted jobs?

**Decision**: No. `get()` returns any job including deleted ones. Only list/pending/processing queries exclude deleted.

**Rationale**:
- API needs to return deleted job details (when `include_deleted=true` matches a deleted job)
- `atomic_transition()` needs to work on deleted jobs for potential restore
- The execution paths (`list_pending_*`, `find_processing_jobs`, etc.) are the ones that must exclude deleted
- `get()` is used for targeted lookups where the caller explicitly knows which job they want

## Decision 5: `list()` default excludes deleted, opt-in includes them

**Context**: Should the default list behavior include or exclude deleted jobs?

**Decision**: Default excludes deleted (`include_deleted=False`). Must explicitly opt in.

**Rationale**:
- Safer default — FE won't show deleted jobs unless user explicitly checks the box
- Matches user expectation: "deleted" things disappear unless you ask to see them
- API consumers (webhooks, scripts) won't accidentally process deleted jobs
