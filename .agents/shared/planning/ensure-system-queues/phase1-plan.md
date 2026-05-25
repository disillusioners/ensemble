# Phase 1: Project Delete Cleanup

## Objective

Add a `DELETE /api/projects/{project_id}` endpoint that safely removes a project and ALL related data: instances, job queues, job items, job locks, dead letter items, plus the existing cleanup (tags, shortnames, metadata).

## Coupling

- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: None
- **Shared APIs/interfaces**: None
- **Why this coupling**: Phase 1 touches project router/repo; Phases 2-3 touch queue router/frontend

## Context

- The project repository already has a `delete()` method at `daemon/repositories/project/repository.py:659-685` but it only cleans up tags, shortnames, and metadata
- 5 tables have `project_id` columns without CASCADE DELETE: instances, job_queues, job_queue_items, job_locks, dead_letter_items
- No DELETE endpoint exists in `daemon/routers/projects.py` (710 lines, no DELETE route)
- Active instances with running jobs must be handled safely

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Add bulk-delete methods to repositories** | Add `delete_by_project(project_id)` methods to: JobQueueRepository, JobRepository (for job_queue_items), JobLockRepository, DeadLetterRepository, InstanceRepository | `daemon/repositories/job_queue/queue_repository.py`, `daemon/repositories/job_queue/repository.py`, `daemon/repositories/job_queue/lock_repository.py`, `daemon/repositories/job_queue/dead_letter_repository.py`, `daemon/repositories/instance/repository.py` |
| 2 | **Enhance project repo delete()** | Update `repository.delete()` to call the new bulk-delete methods in correct order: locks → dead_letters → job_items → job_queues → instances → tags/shortnames/metadata → project | `daemon/repositories/project/repository.py:659-685` |
| 3 | **Add safety checks** | Before deletion: check for active (non-idle) instances; refuse delete with 409 if any running. Optionally add a `force` query param to override. Pause project queue first to prevent new job submissions during delete. | `daemon/repositories/project/repository.py`, `daemon/routers/projects.py` |
| 4 | **Add DELETE endpoint to router** | Add `DELETE /{project_id}` route in projects router. Wire to the enhanced `repository.delete()`. Return summary of deleted counts. | `daemon/routers/projects.py` |
| 5 | **Add DELETE to frontend API service** | Add `deleteProject(projectId)` method to the frontend API service. | `frontend/src/app/services/api.service.ts` |
| 6 | **Test the deletion flow** | Verify: project deleted → all 10 tables cleaned → no orphans. Test with active instances (should refuse). Test with force flag. | Test files or manual testing |

## Key Files

- `daemon/repositories/project/repository.py:659-685` — Current delete method to enhance
- `daemon/routers/projects.py` — Add DELETE endpoint (no DELETE route currently)
- `daemon/repositories/job_queue/queue_repository.py` — Add bulk delete for queues
- `daemon/repositories/job_queue/repository.py` — Add bulk delete for job items
- `daemon/repositories/job_queue/lock_repository.py` — Add bulk delete for locks
- `daemon/repositories/job_queue/dead_letter_repository.py` — Add bulk delete for dead letters
- `daemon/repositories/instance/repository.py` — Add bulk delete for instances
- `frontend/src/app/services/api.service.ts` — Add deleteProject method

## Deletion Order (Critical)

The order matters due to foreign key relationships between job tables:

```
1. Pause project queue (prevent new submissions)
2. Delete job_locks (references job_queue_items)
3. Delete dead_letter_items (orphaned from jobs)
4. Delete job_queue_items (references job_queues via queue_id FK)
5. Delete job_queues (top-level queue records)
6. Delete instances (references project via project_id, no FK)
7. Delete project_tags, project_shortnames, project_metadata_records
8. Delete project record
```

All in a single DB transaction for atomicity.

## Constraints

- Must be atomic — all deletes in one transaction, or none
- Must check for active instances before deleting (safety)
- The `force` query param should only bypass active instance check, not skip cleanup
- Return a summary of what was deleted for debugging/auditing

## Deliverables

- [ ] `delete_by_project()` methods on 5 repositories
- [ ] Enhanced `project.repository.delete()` with full cascade cleanup
- [ ] `DELETE /{project_id}` endpoint in projects router
- [ ] Safety check for active instances
- [ ] Frontend `deleteProject()` API method
